#!/usr/bin/env python3
"""Deterministic rank/map and file-access audit for runtime relocation."""

from __future__ import annotations

import ast
import collections
import csv
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from s1_mpi_prefix_equivalence_common import (
    REGISTERED_DEVICE_MAPPING_PATTERNS,
    SYSTEM_MAPPING_EXACT_PATHS,
    SYSTEM_MAPPING_ROOTS,
    TRANSIENT_MAPPING_PATTERNS,
    registered_old_prefix_failed_probes,
)
from s1_runtime_relocation_elf import (
    file_identity,
    remaining_seconds,
    sha256,
    versioned_tool_identity,
)


QUOTED_STRING = re.compile(r'"(?:\\.|[^"\\])*"')
RESULT_PATTERN = re.compile(
    r"\)\s+=\s+(-?\d+|0x[0-9a-fA-F]+)(?:\s+([A-Z][A-Z0-9]+))?"
)
SYSCALL_PATTERN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\(")
ABSOLUTE_WATCHDOG_SECONDS = 7200.0


class AbsoluteWatchdogExpired(BaseException):
    """Uncatchable by broad operational-error handlers inside the audit."""


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def _lexically_within(path: Path, root: Path) -> bool:
    try:
        Path(os.path.abspath(path)).relative_to(Path(os.path.abspath(root)))
    except ValueError:
        return False
    return True


def _decode_quoted(value: str) -> str:
    try:
        decoded = ast.literal_eval(value)
    except (SyntaxError, ValueError):
        return value[1:-1]
    return decoded if isinstance(decoded, str) else value[1:-1]


def _openat_flags(line: str) -> str | None:
    if not line.startswith("openat("):
        return None
    match = QUOTED_STRING.search(line)
    if match is None:
        return None
    suffix = line[match.end() :].lstrip()
    if not suffix.startswith(","):
        return None
    value = suffix[1:].lstrip().split(",", 1)[0].split(")", 1)[0].strip()
    return value or None


def parse_strace_records(
    records: list[dict],
    old_prefix: Path,
    policies: tuple[dict, ...],
    expected_ranks: int,
) -> dict:
    """Classify every old-prefix event by exact syscall/flags/errno/role/count."""

    old_prefix = Path(os.path.abspath(old_prefix))
    events = []
    successful = 0
    exec_successful = 0
    unknown_failed = 0
    registered_failed = 0
    for record in records:
        line = str(record.get("line", ""))
        paths = []
        for quoted in QUOTED_STRING.findall(line):
            value = _decode_quoted(quoted)
            if not value.startswith("/"):
                continue
            path = Path(os.path.abspath(value))
            if _lexically_within(path, old_prefix):
                paths.append(str(path))
        if not paths:
            continue
        syscall_match = SYSCALL_PATTERN.match(line)
        syscall = syscall_match.group(1) if syscall_match else None
        flags = _openat_flags(line)
        result_match = RESULT_PATTERN.search(line)
        result_value = result_match.group(1) if result_match else None
        errno = result_match.group(2) if result_match else None
        is_success = result_value is not None and result_value != "-1"
        role = record.get("role", "unknown")
        rank = record.get("rank")
        matched = []
        if not is_success and len(set(paths)) == 1:
            for policy in policies:
                rank_matches = policy["rank"] == rank or (
                    policy["rank"] == "each" and isinstance(rank, int)
                )
                if (
                    paths[0] == policy["path"]
                    and syscall == policy["syscall"]
                    and flags == policy["flags"]
                    and errno == policy["errno"]
                    and role == policy["role"]
                    and rank_matches
                ):
                    matched.append(policy["probe_id"])
        if len(matched) == 1:
            registered_failed += 1
        elif not is_success:
            unknown_failed += 1
        if is_success:
            successful += 1
            if syscall == "execve":
                exec_successful += 1
        events.append(
            {
                "pid": record.get("pid"),
                "role": role,
                "rank": rank,
                "paths": paths,
                "syscall": syscall,
                "flags": flags,
                "result": result_value,
                "errno": errno,
                "successful": is_success,
                "registered_probe_id": matched[0] if len(matched) == 1 else None,
                "line": line,
            }
        )

    counts: dict[str, dict] = {}
    mismatch_count = 0
    for policy in policies:
        matching = [
            event
            for event in events
            if event["registered_probe_id"] == policy["probe_id"]
        ]
        if policy["rank"] == "each":
            by_rank = {
                str(rank): sum(event["rank"] == rank for event in matching)
                for rank in range(expected_ranks)
            }
            expected = int(policy["expected_count_per_rank"])
            mismatches = {
                rank: value for rank, value in by_rank.items() if value != expected
            }
            mismatch_count += len(mismatches)
            counts[policy["probe_id"]] = {
                "total": len(matching),
                "by_rank": by_rank,
                "expected_count_per_rank": expected,
                "mismatches": mismatches,
            }
        else:
            expected = int(policy["expected_count"])
            mismatch = len(matching) != expected
            mismatch_count += int(mismatch)
            counts[policy["probe_id"]] = {
                "total": len(matching),
                "expected_count": expected,
                "mismatch": mismatch,
            }
    return {
        "old_prefix_access_attempt_count": len(events),
        "old_prefix_successful_access_count": successful,
        "old_prefix_exec_success_count": exec_successful,
        "registered_old_prefix_failed_probe_count": registered_failed,
        "unknown_old_prefix_failed_probe_count": unknown_failed,
        "registered_probe_count_mismatch_count": mismatch_count,
        "registered_probe_counts": counts,
        "old_prefix_access_events": events,
    }


def parse_execve_records(records: list[dict]) -> list[dict]:
    values = []
    for record in records:
        line = str(record.get("line", ""))
        if not line.startswith("execve("):
            continue
        quoted = QUOTED_STRING.findall(line)
        if not quoted:
            continue
        value = _decode_quoted(quoted[0])
        if not value.startswith("/"):
            continue
        result_match = RESULT_PATTERN.search(line)
        result = result_match.group(1) if result_match else None
        values.append(
            {
                "pid": record.get("pid"),
                "role": record.get("role", "unknown"),
                "rank": record.get("rank"),
                "path": str(Path(value).resolve(strict=False)),
                "result": result,
                "errno": result_match.group(2) if result_match else None,
                "successful": result == "0",
            }
        )
    return values


def _direct_children(pid: int, proc_root: Path = Path("/proc")) -> set[int]:
    task_directory = proc_root / str(pid) / "task"
    try:
        tasks = list(task_directory.iterdir())
    except (FileNotFoundError, PermissionError, OSError):
        return set()
    children: set[int] = set()
    for task in tasks:
        if not task.name.isdigit():
            continue
        try:
            values = (task / "children").read_text(encoding="ascii").split()
        except (FileNotFoundError, PermissionError, OSError):
            continue
        for value in values:
            try:
                children.add(int(value))
            except ValueError:
                continue
    return children


def _descendants(root_pid: int, proc_root: Path = Path("/proc")) -> set[int]:
    selected: set[int] = set()
    pending = [root_pid]
    while pending:
        pid = pending.pop()
        if pid in selected:
            continue
        selected.add(pid)
        pending.extend(_direct_children(pid, proc_root) - selected)
    return selected


def _executable(pid: int) -> Path | None:
    try:
        return Path(os.readlink(Path("/proc") / str(pid) / "exe")).resolve(
            strict=False
        )
    except (FileNotFoundError, PermissionError, OSError):
        return None


def _mapped_paths(pid: int) -> set[str]:
    paths: set[str] = set()
    try:
        lines = (Path("/proc") / str(pid) / "maps").read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()
    except (FileNotFoundError, PermissionError):
        return paths
    for line in lines:
        fields = line.split(maxsplit=5)
        if len(fields) != 6:
            continue
        value = fields[5]
        if value.endswith(" (deleted)"):
            value = value[: -len(" (deleted)")]
        if value.startswith("/"):
            paths.add(value)
    return paths


def classify_mapping(
    original: Path,
    realpath: Path,
    old_prefix: Path,
    recovery_root: Path,
    system_roots: tuple[str, ...] = SYSTEM_MAPPING_ROOTS,
    system_exact_paths: tuple[str, ...] = SYSTEM_MAPPING_EXACT_PATHS,
    device_patterns: tuple[str, ...] = REGISTERED_DEVICE_MAPPING_PATTERNS,
    transient_patterns: tuple[str, ...] = TRANSIENT_MAPPING_PATTERNS,
) -> str:
    if (
        _lexically_within(original, old_prefix)
        or _lexically_within(realpath, old_prefix)
        or _within(original, old_prefix)
        or _within(realpath, old_prefix)
    ):
        return "old_prefix"
    if (
        _lexically_within(original, recovery_root)
        or _lexically_within(realpath, recovery_root)
        or _within(original, recovery_root)
        or _within(realpath, recovery_root)
    ):
        return "recovery_runtime"
    if any(re.fullmatch(pattern, str(original)) for pattern in transient_patterns):
        return "transient_system"
    if str(original) in system_exact_paths or str(realpath) in system_exact_paths:
        return "system"
    if any(re.fullmatch(pattern, str(original)) for pattern in device_patterns):
        return "registered_device"
    if any(
        _lexically_within(original, Path(root))
        or _lexically_within(realpath, Path(root))
        or _within(original, Path(root))
        or _within(realpath, Path(root))
        for root in system_roots
    ):
        return "system"
    return "unexpected"


def _capture_target(
    pid: int,
    role: str,
    rank: int | None,
    expected_executable: Path,
    old_prefix: Path,
    recovery_root: Path,
    processes: dict[int, dict],
    objects: dict[tuple[int, str], dict],
) -> bool:
    executable = _executable(pid)
    if executable is None or executable != expected_executable:
        return False
    record = processes.setdefault(
        pid,
        {
            "pid": pid,
            "role": role,
            "rank": rank,
            "executable_realpath": str(executable),
            "executable_sha256": None,
            "mapped_object_count": 0,
            "initial_map_capture_observed": False,
        },
    )
    if record["role"] != role or record["rank"] != rank:
        raise ValueError(f"PID {pid} changed runtime role/rank")
    mapped = _mapped_paths(pid)
    if mapped:
        record["initial_map_capture_observed"] = True
    for original_value in mapped:
        key = (pid, original_value)
        if key in objects:
            continue
        original = Path(original_value)
        realpath = original.resolve(strict=False)
        objects[key] = {
            "pid": pid,
            "role": role,
            "rank": rank,
            "mapped_path": original_value,
            "loaded_realpath": str(realpath),
            "loaded_sha256": None,
            "classification": classify_mapping(
                original, realpath, old_prefix, recovery_root
            ),
        }
    record["mapped_object_count"] = sum(object_pid == pid for object_pid, _ in objects)
    return True


def _sha256(
    path: Path, cache: dict[str, str | None], deadline: float | None = None
) -> str | None:
    key = str(path)
    if key in cache:
        return cache[key]
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            cache[key] = None
            return None
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                remaining_seconds(deadline, f"mapped object hashing: {path}")
                chunk = handle.read(1024 * 1024)
                remaining_seconds(deadline, f"mapped object hashing: {path}")
                if not chunk:
                    break
                digest.update(chunk)
        remaining_seconds(deadline, f"mapped object hashing: {path}")
        value: str | None = digest.hexdigest()
    except (FileNotFoundError, IsADirectoryError, PermissionError, OSError):
        value = None
    cache[key] = value
    return value


def _write_objects(path: Path, rows: list[dict]) -> None:
    header = (
        "pid",
        "role",
        "rank",
        "mapped_path",
        "loaded_realpath",
        "loaded_sha256",
        "classification",
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=header, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({key: "" if row[key] is None else row[key] for key in header})


def _atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _process_group_members(process_group: int) -> list[int]:
    members = []
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        try:
            text = (path / "stat").read_text(encoding="ascii")
            tail = text[text.rfind(")") + 2 :].split()
            if len(tail) > 2 and int(tail[2]) == process_group:
                members.append(int(path.name))
        except (FileNotFoundError, PermissionError, OSError, ValueError):
            continue
    return sorted(members)


def _proc_start_time_ticks(pid: int, proc_root: Path = Path("/proc")) -> int | None:
    try:
        text = (proc_root / str(pid) / "stat").read_text(encoding="ascii")
        fields = text[text.rfind(")") + 2 :].split()
        return int(fields[19])
    except (FileNotFoundError, PermissionError, OSError, ValueError, IndexError):
        return None


def _trace_pids(directory: Path) -> set[int]:
    values: set[int] = set()
    try:
        paths = list(directory.glob("trace.*"))
    except OSError:
        return values
    for path in paths:
        try:
            values.add(int(path.name.rsplit(".", 1)[1]))
        except (IndexError, ValueError):
            continue
    return values


def _register_known_pids(
    known: dict[int, dict], pids: set[int] | list[int], source: str
) -> None:
    for pid in sorted(set(pids)):
        if pid <= 0:
            continue
        record = known.setdefault(
            pid,
            {
                "pid": pid,
                "sources": set(),
                "observed_start_time_ticks": _proc_start_time_ticks(pid),
            },
        )
        record["sources"].add(source)
        if record["observed_start_time_ticks"] is None:
            record["observed_start_time_ticks"] = _proc_start_time_ticks(pid)


def _prove_known_pids_gone(
    known: dict[int, dict], deadline: float, process_group: int | None
) -> dict:
    """Record terminal evidence for every PID observed by independent channels."""

    proof_deadline = min(deadline, time.monotonic() + 5.0)
    while True:
        remaining = []
        for pid, record in known.items():
            current = _proc_start_time_ticks(pid)
            original = record.get("observed_start_time_ticks")
            if current is not None and (original is None or current == original):
                remaining.append(pid)
        group_members = _process_group_members(process_group) if process_group else []
        _register_known_pids(known, group_members, "terminal_process_group_scan")
        if not remaining and not group_members:
            break
        if time.monotonic() >= proof_deadline:
            break
        time.sleep(0.05)
    rows = []
    all_gone = True
    for pid, record in sorted(known.items()):
        current = _proc_start_time_ticks(pid)
        original = record.get("observed_start_time_ticks")
        if current is None:
            terminal = "gone"
        elif original is not None and current != original:
            terminal = "pid_reused_original_gone"
        else:
            terminal = "still_present_or_identity_unproven"
            all_gone = False
        rows.append(
            {
                "pid": pid,
                "sources": sorted(record["sources"]),
                "observed_start_time_ticks": original,
                "terminal_start_time_ticks": current,
                "terminal_state": terminal,
            }
        )
    final_group_members = _process_group_members(process_group) if process_group else []
    if final_group_members:
        all_gone = False
    return {
        "known_pid_count": len(rows),
        "known_pids": rows,
        "process_group": process_group,
        "process_group_members_after": final_group_members,
        "all_known_pids_gone": all_gone and bool(rows),
    }


def _terminate_process_group(
    process: subprocess.Popen, grace_seconds: float = 10
) -> dict:
    """Bound cleanup of strace, mpirun, launcher, and ranks as one session."""

    before = _process_group_members(process.pid)
    tracees = sorted(_descendants(process.pid))
    if process.poll() is not None and not before and not tracees:
        return {
            "members_before_cleanup": before,
            "members_after_cleanup": [],
            "tracee_pids_before_cleanup": tracees,
            "tracee_pids_after_cleanup": [],
            "all_group_members_gone": True,
        }
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    for pid in reversed(tracees):
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        process.wait(timeout=grace_seconds)
        after = _process_group_members(process.pid)
        remaining_tracees = [pid for pid in tracees if Path("/proc", str(pid)).exists()]
        return {
            "members_before_cleanup": before,
            "members_after_cleanup": after,
            "tracee_pids_before_cleanup": tracees,
            "tracee_pids_after_cleanup": remaining_tracees,
            "all_group_members_gone": not after and not remaining_tracees,
        }
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    for pid in reversed(tracees):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        pass
    deadline = time.monotonic() + 5
    after = _process_group_members(process.pid)
    remaining_tracees = [pid for pid in tracees if Path("/proc", str(pid)).exists()]
    while (after or remaining_tracees) and time.monotonic() < deadline:
        time.sleep(0.05)
        after = _process_group_members(process.pid)
        remaining_tracees = [pid for pid in tracees if Path("/proc", str(pid)).exists()]
    return {
        "members_before_cleanup": before,
        "members_after_cleanup": after,
        "tracee_pids_before_cleanup": tracees,
        "tracee_pids_after_cleanup": remaining_tracees,
        "all_group_members_gone": not after and not remaining_tracees,
    }


def _trace_records(
    directory: Path, launcher_pid: int | None, rank_pids: dict[int, int]
) -> list[dict]:
    pid_roles = {
        pid: ("rank", rank) for rank, pid in rank_pids.items()
    }
    if launcher_pid is not None:
        pid_roles[launcher_pid] = ("launcher", None)
    records = []
    for path in sorted(directory.glob("trace.*")):
        try:
            pid = int(path.name.rsplit(".", 1)[1])
        except (IndexError, ValueError):
            pid = -1
        role, rank = pid_roles.get(pid, ("support", None))
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            records.append(
                {"pid": pid, "role": role, "rank": rank, "line": line}
            )
    return records


def _wait_for_ready(
    process: subprocess.Popen,
    handshake: Path,
    expected_ranks: int,
    expected_launcher: Path,
    timeout_seconds: float,
) -> tuple[dict[int, int], int | None, list[str]]:
    deadline = time.monotonic() + timeout_seconds
    launcher_pids: set[int] = set()
    failures: list[str] = []
    ready_directory = handshake / "ready"
    while time.monotonic() < deadline:
        for pid in _descendants(process.pid):
            if _executable(pid) == expected_launcher:
                launcher_pids.add(pid)
        ready_paths = sorted(ready_directory.glob("rank-*.json"))
        if len(ready_paths) >= expected_ranks:
            break
        if process.poll() is not None:
            failures.append("launcher_exited_before_rank_handshake")
            break
        time.sleep(0.01)
    rank_pids: dict[int, int] = {}
    for path in sorted(ready_directory.glob("rank-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rank = int(payload["rank"])
            pid = int(payload["pid"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            failures.append(f"invalid_rank_handshake:{path.name}:{error}")
            continue
        if rank in rank_pids:
            failures.append(f"duplicate_rank_handshake:{rank}")
        rank_pids[rank] = pid
    if sorted(rank_pids) != list(range(expected_ranks)):
        failures.append(f"rank_handshake_set:{sorted(rank_pids)}")
    if len(set(rank_pids.values())) != len(rank_pids):
        failures.append("rank_handshake_duplicate_pid")
    if len(launcher_pids) != 1:
        failures.append(f"launcher_handshake_count:{len(launcher_pids)}")
    launcher_pid = next(iter(launcher_pids)) if len(launcher_pids) == 1 else None
    return rank_pids, launcher_pid, failures


def _release_and_capture_rank(
    rank: int,
    pid: int,
    handshake: Path,
    expected_abacus: Path,
    old_prefix: Path,
    recovery_root: Path,
    processes: dict[int, dict],
    objects: dict[tuple[int, str], dict],
    timeout_seconds: float,
) -> str | None:
    release = handshake / "release" / f"rank-{rank}"
    with release.open("x", encoding="ascii") as handle:
        handle.write("release\n")
        handle.flush()
        os.fsync(handle.fileno())
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        executable = _executable(pid)
        if executable == expected_abacus:
            try:
                os.kill(pid, signal.SIGSTOP)
                stop_deadline = time.monotonic() + 2
                while time.monotonic() < stop_deadline:
                    status = (Path("/proc") / str(pid) / "status").read_text(
                        encoding="utf-8", errors="replace"
                    )
                    state = next(
                        (line for line in status.splitlines() if line.startswith("State:")),
                        "",
                    )
                    if "T" in state or "t" in state:
                        break
                    time.sleep(0.001)
                if not _capture_target(
                    pid,
                    "rank",
                    rank,
                    expected_abacus,
                    old_prefix,
                    recovery_root,
                    processes,
                    objects,
                ):
                    return f"rank_{rank}_initial_map_capture_failed"
            except (FileNotFoundError, ProcessLookupError, PermissionError, OSError) as error:
                return f"rank_{rank}_capture_error:{error}"
            finally:
                try:
                    os.kill(pid, signal.SIGCONT)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
            return None
        if executable is None:
            return f"rank_{rank}_exited_before_abacus_exec"
        time.sleep(0.001)
    return f"rank_{rank}_abacus_exec_timeout"


def _required_environment() -> tuple[str, ...]:
    return (
        "M_OFDFT_REAL_MPIRUN",
        "M_OFDFT_EXPECTED_MPIRUN_SHA256",
        "M_OFDFT_EXPECTED_LAUNCHER",
        "M_OFDFT_EXPECTED_LAUNCHER_SHA256",
        "M_OFDFT_EXPECTED_ABACUS",
        "M_OFDFT_EXPECTED_ABACUS_SHA256",
        "M_OFDFT_RANK_WRAPPER",
        "M_OFDFT_RANK_WRAPPER_SHA256",
        "M_OFDFT_PYTHON_TOOL",
        "M_OFDFT_PYTHON_SHA256",
        "M_OFDFT_STRACE_TOOL",
        "M_OFDFT_STRACE_PATH",
        "M_OFDFT_STRACE_REALPATH",
        "M_OFDFT_STRACE_SHA256",
        "M_OFDFT_STRACE_VERSION_FIRST_LINE",
        "M_OFDFT_STRACE_VERSION_OUTPUT_SHA256",
        "M_OFDFT_MPI_AUDIT_DIR",
        "M_OFDFT_RECOVERY_ROOT",
        "M_OFDFT_RECOVERY_PREFIX",
        "M_OFDFT_OLD_PREFIX",
        "M_OFDFT_MPI_AUDIT_EXPECTED_RANKS",
    )


def _main_impl(
    started: float,
    started_monotonic: float,
    started_at_utc: str,
    total_deadline: float,
    watchdog_state: dict[str, object],
) -> int:
    audit_directory = Path(os.environ.get("M_OFDFT_MPI_AUDIT_DIR", "."))
    audit_directory.mkdir(parents=True, exist_ok=True)
    objects_path = audit_directory / "objects.tsv"
    audit_path = audit_directory / "audit.json"
    strace_directory = audit_directory / "strace"
    handshake = audit_directory / "rank_handshake"
    failures: list[str] = []
    processes: dict[int, dict] = {}
    objects: dict[tuple[int, str], dict] = {}
    launcher_pid: int | None = None
    rank_pids: dict[int, int] = {}
    exit_code = 97
    strace_before: dict = {}
    strace_after: dict = {}
    real_command: list[str] = []
    strace_command: list[str] = []
    process: subprocess.Popen | None = None
    timeout_triggered = False
    cleanup_evidence = {
        "members_before_cleanup": [],
        "members_after_cleanup": [],
        "tracee_pids_before_cleanup": [],
        "tracee_pids_after_cleanup": [],
        "all_group_members_gone": False,
    }
    known_pids: dict[int, dict] = {}
    watchdog_state["known_pids"] = known_pids
    watchdog_state["strace_directory"] = strace_directory
    terminal_process_evidence = {
        "known_pid_count": 0,
        "known_pids": [],
        "process_group": None,
        "process_group_members_after": [],
        "all_known_pids_gone": False,
    }
    missing = [key for key in _required_environment() if not os.environ.get(key)]
    if missing:
        failures.append(f"missing_environment:{','.join(missing)}")
    try:
        if failures:
            raise ValueError(failures[-1])
        if not sys.argv[1:]:
            raise ValueError("runtime audit launcher requires mpirun arguments")
        for path in (objects_path, audit_path, strace_directory, handshake):
            if path.exists() or path.is_symlink():
                raise ValueError(f"refusing to overwrite audit evidence: {path}")
        strace_directory.mkdir()
        (handshake / "ready").mkdir(parents=True)
        (handshake / "release").mkdir()
        (handshake / "failure").mkdir()

        old_prefix = Path(os.environ["M_OFDFT_OLD_PREFIX"])
        recovery_root = Path(os.environ["M_OFDFT_RECOVERY_ROOT"]).resolve(strict=False)
        recovery_prefix = Path(os.environ["M_OFDFT_RECOVERY_PREFIX"]).resolve(
            strict=False
        )
        if old_prefix.exists() or old_prefix.is_symlink():
            raise ValueError("old prefix is visible inside the isolation namespace")
        expected_ranks = int(os.environ["M_OFDFT_MPI_AUDIT_EXPECTED_RANKS"])
        real_mpirun = Path(os.environ["M_OFDFT_REAL_MPIRUN"]).resolve(strict=True)
        expected_launcher = Path(os.environ["M_OFDFT_EXPECTED_LAUNCHER"]).resolve(
            strict=True
        )
        expected_abacus = Path(os.environ["M_OFDFT_EXPECTED_ABACUS"]).resolve(
            strict=True
        )
        rank_wrapper = Path(os.environ["M_OFDFT_RANK_WRAPPER"]).resolve(strict=True)
        python_tool = Path(os.environ["M_OFDFT_PYTHON_TOOL"]).resolve(strict=True)
        if sha256(
            real_mpirun, total_deadline, "mpirun preflight hashing"
        ) != os.environ["M_OFDFT_EXPECTED_MPIRUN_SHA256"]:
            raise ValueError("mpirun SHA-256 changed before launch")
        if sha256(
            expected_launcher, total_deadline, "launcher preflight hashing"
        ) != os.environ["M_OFDFT_EXPECTED_LAUNCHER_SHA256"]:
            raise ValueError("launcher SHA-256 changed before launch")
        if sha256(
            expected_abacus, total_deadline, "ABACUS preflight hashing"
        ) != os.environ["M_OFDFT_EXPECTED_ABACUS_SHA256"]:
            raise ValueError("ABACUS SHA-256 changed before launch")
        if sha256(
            rank_wrapper, total_deadline, "rank wrapper preflight hashing"
        ) != os.environ["M_OFDFT_RANK_WRAPPER_SHA256"]:
            raise ValueError("rank wrapper SHA-256 changed before launch")
        if sha256(
            python_tool, total_deadline, "Python preflight hashing"
        ) != os.environ["M_OFDFT_PYTHON_SHA256"]:
            raise ValueError("Python SHA-256 changed before launch")
        strace_before = versioned_tool_identity(
            Path(os.environ["M_OFDFT_STRACE_TOOL"]),
            "strace",
            deadline=total_deadline,
        )
        expected_strace = {
            "path": os.environ["M_OFDFT_STRACE_PATH"],
            "realpath": os.environ["M_OFDFT_STRACE_REALPATH"],
            "sha256": os.environ["M_OFDFT_STRACE_SHA256"],
            "version_first_line": os.environ["M_OFDFT_STRACE_VERSION_FIRST_LINE"],
            "version_output_sha256": os.environ[
                "M_OFDFT_STRACE_VERSION_OUTPUT_SHA256"
            ],
        }
        for key, expected in expected_strace.items():
            if strace_before.get(key) != expected:
                raise ValueError(f"strace {key} differs before launch")
        if Path(sys.argv[-1]).resolve(strict=True) != expected_abacus:
            raise ValueError("last mpirun argument must be the frozen replay ABACUS")
        prefix_environment = {
            key: os.environ.get(key)
            for key in ("OPAL_PREFIX", "PRTE_PREFIX", "PMIX_PREFIX", "UCX_MODULE_DIR")
        }
        if any(value != str(recovery_prefix) for value in prefix_environment.values()):
            raise ValueError("OPAL/PRTE/PMIX/UCX module prefixes must equal recovery prefix")
        if os.environ.get("LD_LIBRARY_PATH") != str(recovery_prefix / "lib"):
            raise ValueError("LD_LIBRARY_PATH is not the exact recovery lib directory")
        if os.environ.get("LD_PRELOAD") not in (None, ""):
            raise ValueError("LD_PRELOAD must be unset")
        required_runtime_environment = {
            "PATH": f"{recovery_prefix}/bin:/usr/bin:/bin",
            "LD_LIBRARY_PATH": str(recovery_prefix / "lib"),
            "LD_PRELOAD": None,
            "CMAKE_PREFIX_PATH": str(recovery_prefix),
            "MKLROOT": str(recovery_prefix),
            "OMP_NUM_THREADS": "1",
            "HOME": str(audit_directory.parent / "runtime_home"),
        }
        actual_runtime_environment = {
            key: os.environ.get(key) for key in required_runtime_environment
        }
        if actual_runtime_environment != required_runtime_environment:
            raise ValueError("final controlled runtime environment differs from registration")
        home = Path(required_runtime_environment["HOME"])
        marker = home / "CONTROLLED_HOME.txt"
        if (
            not home.is_dir()
            or home.is_symlink()
            or sorted(path.name for path in home.iterdir()) != [marker.name]
            or marker.read_text(encoding="utf-8")
            != "Controlled empty HOME for S1 runtime-relocation replay.\n"
        ):
            raise ValueError("controlled HOME is missing, altered, or contains extra entries")

        transformed_arguments = [
            *sys.argv[1:-1],
            str(python_tool),
            str(rank_wrapper),
            str(expected_abacus),
        ]
        os.environ["M_OFDFT_RANK_HANDSHAKE_DIR"] = str(handshake)
        real_command = [str(real_mpirun), "--allow-run-as-root", *transformed_arguments]
        strace_command = [
            strace_before["path"],
            "-ff",
            "-qq",
            "--kill-on-exit",
            "-s",
            "4096",
            "-e",
            "trace=file,process",
            "-o",
            str(strace_directory / "trace"),
            *real_command,
        ]
        process = subprocess.Popen(
            strace_command,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        watchdog_state["process"] = process
        _register_known_pids(known_pids, [process.pid], "strace_root")
        rank_pids, launcher_pid, handshake_failures = _wait_for_ready(
            process,
            handshake,
            expected_ranks,
            expected_launcher,
            max(0.001, min(120, total_deadline - time.monotonic())),
        )
        failures.extend(handshake_failures)
        _register_known_pids(known_pids, set(rank_pids.values()), "rank_handshake")
        if launcher_pid is not None:
            _register_known_pids(known_pids, [launcher_pid], "launcher_discovery")
        if launcher_pid is not None:
            if not _capture_target(
                launcher_pid,
                "launcher",
                None,
                expected_launcher,
                old_prefix,
                recovery_root,
                processes,
                objects,
            ):
                failures.append("launcher_initial_map_capture_failed")
        if not handshake_failures:
            for rank in range(expected_ranks):
                failure = _release_and_capture_rank(
                    rank,
                    rank_pids[rank],
                    handshake,
                    expected_abacus,
                    old_prefix,
                    recovery_root,
                    processes,
                    objects,
                    max(0.001, min(30, total_deadline - time.monotonic())),
                )
                if failure:
                    failures.append(failure)
                    break
        if failures:
            (handshake / "abort").write_text("abort\n", encoding="ascii")
            for rank in range(expected_ranks):
                release = handshake / "release" / f"rank-{rank}"
                if not release.exists():
                    release.write_text("abort\n", encoding="ascii")
        while process.poll() is None:
            if time.monotonic() >= total_deadline:
                timeout_triggered = True
                failures.append("runtime_audit_wall_timeout:7200")
                (handshake / "abort").write_text("abort\n", encoding="ascii")
                cleanup_evidence = _terminate_process_group(process)
                break
            _register_known_pids(
                known_pids, _descendants(process.pid), "descendant_scan"
            )
            _register_known_pids(
                known_pids,
                _process_group_members(process.pid),
                "process_group_scan",
            )
            _register_known_pids(
                known_pids, _trace_pids(strace_directory), "strace_trace_file"
            )
            if launcher_pid is not None:
                _capture_target(
                    launcher_pid,
                    "launcher",
                    None,
                    expected_launcher,
                    old_prefix,
                    recovery_root,
                    processes,
                    objects,
                )
            for rank, pid in rank_pids.items():
                _capture_target(
                    pid,
                    "rank",
                    rank,
                    expected_abacus,
                    old_prefix,
                    recovery_root,
                    processes,
                    objects,
                )
            time.sleep(0.1)
        if process.poll() is None:
            cleanup_evidence = _terminate_process_group(process)
        exit_code = int(process.returncode if process.returncode is not None else 97)
        if exit_code != 0:
            failures.append(f"launcher_exit_code:{exit_code}")
        strace_after = versioned_tool_identity(
            Path(os.environ["M_OFDFT_STRACE_TOOL"]),
            "strace",
            deadline=total_deadline,
        )
        if strace_after != strace_before:
            failures.append("strace_identity_changed_during_run")
    except (
        KeyError,
        FileExistsError,
        FileNotFoundError,
        OSError,
        TimeoutError,
        ValueError,
    ) as error:
        if isinstance(error, TimeoutError):
            timeout_triggered = True
        failures.append(f"audit_launcher_exception:{error}")
        if process is not None:
            cleanup_evidence = _terminate_process_group(process)
            if process.returncode is not None:
                exit_code = int(process.returncode)

    hash_cache: dict[str, str | None] = {}
    for record in processes.values():
        if time.monotonic() >= total_deadline:
            timeout_triggered = True
            failures.append("runtime_audit_total_deadline_exceeded_during_hashing")
            break
        try:
            record["executable_sha256"] = _sha256(
                Path(record["executable_realpath"]), hash_cache, total_deadline
            )
        except TimeoutError as error:
            timeout_triggered = True
            failures.append(f"runtime_audit_hash_deadline:{error}")
            break
        if time.monotonic() >= total_deadline:
            timeout_triggered = True
            failures.append("runtime_audit_total_deadline_exceeded_after_hashing")
            break
    for record in objects.values():
        if time.monotonic() >= total_deadline:
            timeout_triggered = True
            failures.append("runtime_audit_total_deadline_exceeded_during_hashing")
            break
        if record["classification"] != "transient_system":
            try:
                record["loaded_sha256"] = _sha256(
                    Path(record["loaded_realpath"]), hash_cache, total_deadline
                )
            except TimeoutError as error:
                timeout_triggered = True
                failures.append(f"runtime_audit_hash_deadline:{error}")
                break
            if time.monotonic() >= total_deadline:
                timeout_triggered = True
                failures.append("runtime_audit_total_deadline_exceeded_after_hashing")
                break
    object_rows = sorted(objects.values(), key=lambda row: (row["pid"], row["mapped_path"]))
    process_rows = sorted(processes.values(), key=lambda row: row["pid"])
    _write_objects(objects_path, object_rows)
    if process is not None:
        residual_members = _process_group_members(process.pid)
        if residual_members:
            cleanup_evidence = _terminate_process_group(process)
            failures.append("runtime_audit_process_group_residual")
        elif not cleanup_evidence["all_group_members_gone"]:
            cleanup_evidence = _terminate_process_group(process)
        _register_known_pids(known_pids, _trace_pids(strace_directory), "strace_trace_file")
        _register_known_pids(
            known_pids, _process_group_members(process.pid), "terminal_process_group_scan"
        )
        terminal_process_evidence = _prove_known_pids_gone(
            known_pids, total_deadline, process.pid
        )
    if not cleanup_evidence["all_group_members_gone"]:
        failures.append("runtime_audit_process_group_cleanup_incomplete")
    if not terminal_process_evidence["all_known_pids_gone"]:
        failures.append("runtime_audit_known_pid_terminal_proof_incomplete")

    trace_records = _trace_records(strace_directory, launcher_pid, rank_pids)
    old_prefix_value = Path(os.environ.get("M_OFDFT_OLD_PREFIX", "/invalid-old-prefix"))
    expected_ranks_value = int(os.environ.get("M_OFDFT_MPI_AUDIT_EXPECTED_RANKS", "4"))
    policies = registered_old_prefix_failed_probes(old_prefix_value)
    access = parse_strace_records(
        trace_records, old_prefix_value, policies, expected_ranks_value
    )
    execve_records = parse_execve_records(trace_records)
    old_mappings = sum(row["classification"] == "old_prefix" for row in object_rows)
    unexpected_mappings = sum(row["classification"] == "unexpected" for row in object_rows)
    transient_mappings = sum(
        row["classification"] == "transient_system" for row in object_rows
    )
    registered_device_mappings = sum(
        row["classification"] == "registered_device" for row in object_rows
    )
    unhashed_regular = sum(
        row["classification"] != "transient_system"
        and Path(row["loaded_realpath"]).is_file()
        and not row.get("loaded_sha256")
        for row in object_rows
    )
    target_count_ok = (
        len([row for row in process_rows if row["role"] == "launcher"]) == 1
        and sorted(
            int(row["rank"]) for row in process_rows if row["role"] == "rank"
        )
        == list(range(expected_ranks_value))
    )
    missing_maps = sum(
        not row.get("initial_map_capture_observed") or row.get("mapped_object_count", 0) <= 0
        for row in process_rows
    )
    if not target_count_ok:
        failures.append("target_process_set_incomplete")
    if missing_maps:
        failures.append(f"target_process_missing_maps_count:{missing_maps}")
    if old_mappings:
        failures.append(f"old_prefix_mapped_object_count:{old_mappings}")
    if unexpected_mappings:
        failures.append(f"unexpected_mapped_object_count:{unexpected_mappings}")
    if unhashed_regular:
        failures.append(f"unhashed_regular_mapped_object_count:{unhashed_regular}")
    for key in (
        "old_prefix_successful_access_count",
        "old_prefix_exec_success_count",
        "unknown_old_prefix_failed_probe_count",
        "registered_probe_count_mismatch_count",
    ):
        if access[key]:
            failures.append(f"{key}:{access[key]}")
    expected_mpirun = str(Path(os.environ.get("M_OFDFT_REAL_MPIRUN", "/invalid")).resolve())
    expected_launcher_value = str(
        Path(os.environ.get("M_OFDFT_EXPECTED_LAUNCHER", "/invalid")).resolve()
    )
    expected_abacus_value = str(
        Path(os.environ.get("M_OFDFT_EXPECTED_ABACUS", "/invalid")).resolve()
    )
    expected_python_value = str(
        Path(os.environ.get("M_OFDFT_PYTHON_TOOL", "/invalid")).resolve()
    )
    successful_exec_paths = [
        record["path"] for record in execve_records if record["successful"]
    ]
    expected_exec_multiset = collections.Counter(
        {
            expected_mpirun: 1,
            expected_launcher_value: 1,
            expected_python_value: expected_ranks_value,
            expected_abacus_value: expected_ranks_value,
        }
    )
    observed_exec_multiset = collections.Counter(successful_exec_paths)
    if observed_exec_multiset != expected_exec_multiset:
        failures.append(
            "successful_exec_multiset_mismatch:"
            + json.dumps(
                {
                    "expected": dict(sorted(expected_exec_multiset.items())),
                    "observed": dict(sorted(observed_exec_multiset.items())),
                },
                sort_keys=True,
            )
        )
    ambiguous_exec_result_count = sum(
        record["result"] not in ("0", "-1") for record in execve_records
    )
    if ambiguous_exec_result_count:
        failures.append(
            f"ambiguous_exec_result_count:{ambiguous_exec_result_count}"
        )
    handshake_terminal_state = {
        "ready_files": sorted(
            path.name for path in (handshake / "ready").glob("*") if path.is_file()
        ),
        "release_files": sorted(
            path.name for path in (handshake / "release").glob("*") if path.is_file()
        ),
        "failure_files": sorted(
            path.name for path in (handshake / "failure").glob("*") if path.is_file()
        ),
        "abort_exists": (handshake / "abort").exists(),
    }

    ended_monotonic = time.monotonic()
    ended = time.time()
    if ended_monotonic > total_deadline:
        timeout_triggered = True
        failures.append("runtime_audit_absolute_deadline_exceeded_before_summary")

    payload = {
        "schema_version": 2,
        "protocol": "runtime_relocation_equivalence",
        "status": "accepted" if not failures else "rejected",
        "failure_reasons": failures,
        "command": real_command,
        "strace_command": strace_command,
        "launcher_exit_code": exit_code,
        "started_at_utc": started_at_utc,
        "ended_at_utc": datetime.now(timezone.utc).isoformat(),
        "started_epoch_seconds": started,
        "ended_epoch_seconds": ended,
        "elapsed_seconds": ended_monotonic - started_monotonic,
        "runtime_wall_timeout_seconds": 7200,
        "absolute_deadline_watchdog_seconds": 7200,
        "timeout_triggered": timeout_triggered,
        "process_group_cleanup": cleanup_evidence,
        "terminal_process_evidence": terminal_process_evidence,
        "old_prefix": str(old_prefix_value),
        "recovery_root": os.environ.get("M_OFDFT_RECOVERY_ROOT"),
        "recovery_prefix": os.environ.get("M_OFDFT_RECOVERY_PREFIX"),
        "prefix_environment": {
            key: os.environ.get(key)
            for key in ("OPAL_PREFIX", "PRTE_PREFIX", "PMIX_PREFIX", "UCX_MODULE_DIR")
        },
        "ld_library_path": os.environ.get("LD_LIBRARY_PATH"),
        "ld_preload": os.environ.get("LD_PRELOAD"),
        "runtime_environment": {
            key: os.environ.get(key)
            for key in (
                "PATH",
                "LD_LIBRARY_PATH",
                "LD_PRELOAD",
                "CMAKE_PREFIX_PATH",
                "MKLROOT",
                "HOME",
                "OMP_NUM_THREADS",
            )
        },
        "strace_identity_before": strace_before,
        "strace_identity_after": strace_after,
        "launcher_pid": launcher_pid,
        "rank_pids": {str(rank): pid for rank, pid in sorted(rank_pids.items())},
        "rank_handshake_status": (
            "accepted"
            if sorted(rank_pids) == list(range(expected_ranks_value))
            else "rejected"
        ),
        "rank_handshake_terminal_state": handshake_terminal_state,
        "processes": process_rows,
        "mapped_object_count": len(object_rows),
        "old_prefix_mapped_object_count": old_mappings,
        "unexpected_mapped_object_count": unexpected_mappings,
        "transient_system_mapped_object_count": transient_mappings,
        "registered_device_mapped_object_count": registered_device_mappings,
        "unhashed_regular_mapped_object_count": unhashed_regular,
        "system_mapping_roots": list(SYSTEM_MAPPING_ROOTS),
        "system_mapping_exact_paths": list(SYSTEM_MAPPING_EXACT_PATHS),
        "registered_device_mapping_patterns": list(REGISTERED_DEVICE_MAPPING_PATTERNS),
        "transient_mapping_patterns": list(TRANSIENT_MAPPING_PATTERNS),
        "registered_old_prefix_failed_probes": list(policies),
        "observed_execve_records": execve_records,
        "successful_exec_multiset": dict(sorted(observed_exec_multiset.items())),
        "ambiguous_exec_result_count": ambiguous_exec_result_count,
        **access,
    }
    _atomic_json(audit_path, payload)
    if time.monotonic() > total_deadline and payload["status"] == "accepted":
        payload["status"] = "rejected"
        payload["timeout_triggered"] = True
        payload["failure_reasons"].append(
            "runtime_audit_absolute_deadline_exceeded_during_summary_write"
        )
        payload["ended_epoch_seconds"] = time.time()
        payload["ended_at_utc"] = datetime.now(timezone.utc).isoformat()
        payload["elapsed_seconds"] = time.monotonic() - started_monotonic
        _atomic_json(audit_path, payload)
    if exit_code != 0:
        return exit_code
    return 0 if not failures else 96


def _deadline_alarm(_signum, _frame) -> None:
    raise AbsoluteWatchdogExpired("runtime audit absolute watchdog expired")


def main() -> int:
    """Enforce a process-wide deadline, including preflight and postflight work."""

    started_monotonic = time.monotonic()
    started = time.time()
    started_at_utc = datetime.now(timezone.utc).isoformat()
    total_deadline = started_monotonic + ABSOLUTE_WATCHDOG_SECONDS
    watchdog_state: dict[str, object] = {}
    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _deadline_alarm)
    try:
        remaining = total_deadline - time.monotonic()
        if remaining <= 0:
            raise AbsoluteWatchdogExpired(
                "runtime audit deadline elapsed before watchdog activation"
            )
        signal.setitimer(signal.ITIMER_REAL, remaining)
        return _main_impl(
            started,
            started_monotonic,
            started_at_utc,
            total_deadline,
            watchdog_state,
        )
    except AbsoluteWatchdogExpired as error:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        cleanup_evidence = {
            "members_before_cleanup": [],
            "members_after_cleanup": [],
            "tracee_pids_before_cleanup": [],
            "tracee_pids_after_cleanup": [],
            "all_group_members_gone": False,
        }
        terminal_process_evidence = {
            "known_pid_count": 0,
            "known_pids": [],
            "process_group": None,
            "process_group_members_after": [],
            "all_known_pids_gone": False,
        }
        cleanup_failures: list[str] = []
        process = watchdog_state.get("process")
        if process is not None:
            try:
                known_pids = watchdog_state.get("known_pids")
                if not isinstance(known_pids, dict):
                    known_pids = {}
                _register_known_pids(known_pids, [process.pid], "watchdog_root")
                _register_known_pids(
                    known_pids, _descendants(process.pid), "watchdog_descendant_scan"
                )
                cleanup_evidence = _terminate_process_group(process)
                _register_known_pids(
                    known_pids,
                    cleanup_evidence["tracee_pids_before_cleanup"],
                    "watchdog_cleanup_scan",
                )
                strace_directory = watchdog_state.get("strace_directory")
                if isinstance(strace_directory, Path):
                    _register_known_pids(
                        known_pids,
                        _trace_pids(strace_directory),
                        "watchdog_strace_trace_file",
                    )
                terminal_process_evidence = _prove_known_pids_gone(
                    known_pids, time.monotonic() + 5.0, process.pid
                )
            except Exception as cleanup_error:
                cleanup_failures.append(f"watchdog_cleanup_failed:{cleanup_error}")
        audit_directory = Path(os.environ.get("M_OFDFT_MPI_AUDIT_DIR", "."))
        audit_directory.mkdir(parents=True, exist_ok=True)
        ended_monotonic = time.monotonic()
        ended = time.time()
        _atomic_json(
            audit_directory / "audit.json",
            {
                "schema_version": 2,
                "protocol": "runtime_relocation_equivalence",
                "status": "rejected",
                "failure_reasons": [
                    f"runtime_audit_absolute_watchdog:{error}",
                    *cleanup_failures,
                ],
                "launcher_exit_code": 124,
                "runtime_wall_timeout_seconds": 7200,
                "absolute_deadline_watchdog_seconds": 7200,
                "started_at_utc": started_at_utc,
                "ended_at_utc": datetime.now(timezone.utc).isoformat(),
                "started_epoch_seconds": started,
                "ended_epoch_seconds": ended,
                "elapsed_seconds": ended_monotonic - started_monotonic,
                "timeout_triggered": True,
                "process_group_cleanup": cleanup_evidence,
                "terminal_process_evidence": terminal_process_evidence,
            },
        )
        return 124
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)


if __name__ == "__main__":
    raise SystemExit(main())
