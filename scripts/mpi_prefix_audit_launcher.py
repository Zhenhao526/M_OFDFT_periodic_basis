#!/usr/bin/env python3
"""mpirun-compatible launcher that audits /proc maps and strace file events."""

from __future__ import annotations

import ast
import csv
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

from s1_mpi_prefix_equivalence_common import TRANSIENT_MAPPING_PATTERNS


QUOTED_STRING = re.compile(r'"(?:\\.|[^"\\])*"')
RESULT_PATTERN = re.compile(r"\)\s+=\s+(-?\d+|0x[0-9a-fA-F]+)(?:\s+([A-Z][A-Z0-9]+))?")
RANK_ENV_KEYS = ("OMPI_COMM_WORLD_RANK", "PMIX_RANK", "PMI_RANK")


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


def parse_strace_lines(
    lines: list[str], old_prefix: Path, allowed_probe: Path, allowed_errno: str
) -> dict:
    old_prefix = old_prefix.resolve(strict=False)
    allowed_probe = allowed_probe.resolve(strict=False)
    attempts = []
    successful = 0
    allowed = 0
    other = 0
    for source_line in lines:
        paths = []
        for quoted in QUOTED_STRING.findall(source_line):
            value = _decode_quoted(quoted)
            if not value.startswith("/"):
                continue
            path = Path(value).resolve(strict=False)
            if _within(path, old_prefix):
                paths.append(path)
        if not paths:
            continue
        result_match = RESULT_PATTERN.search(source_line)
        result_value = result_match.group(1) if result_match else None
        errno = result_match.group(2) if result_match else None
        is_success = result_value is not None and result_value != "-1"
        is_allowed = (
            not is_success
            and result_value == "-1"
            and errno == allowed_errno
            and all(path == allowed_probe for path in paths)
        )
        if is_success:
            successful += 1
        if is_allowed:
            allowed += 1
        else:
            other += 1
        attempts.append(
            {
                "paths": [str(path) for path in paths],
                "result": result_value,
                "errno": errno,
                "successful": is_success,
                "allowed_failed_probe": is_allowed,
                "line": source_line.rstrip("\n"),
            }
        )
    return {
        "old_prefix_access_attempt_count": len(attempts),
        "old_prefix_successful_access_count": successful,
        "allowed_failed_probe_count": allowed,
        "other_old_prefix_attempt_count": other,
        "old_prefix_access_events": attempts,
    }


def parse_execve_paths(lines: list[str]) -> list[str]:
    paths = []
    for line in lines:
        start = line.find("execve(")
        if start < 0:
            continue
        quoted = QUOTED_STRING.findall(line[start:])
        if not quoted:
            continue
        value = _decode_quoted(quoted[0])
        if value.startswith("/"):
            paths.append(str(Path(value).resolve(strict=False)))
    return paths


def _direct_children(pid: int, proc_root: Path = Path("/proc")) -> set[int]:
    """Read children created by every thread without scanning the whole node."""

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


def _environment(pid: int) -> dict[str, str]:
    try:
        raw = (Path("/proc") / str(pid) / "environ").read_bytes()
    except (FileNotFoundError, PermissionError):
        return {}
    values: dict[str, str] = {}
    for field in raw.split(b"\0"):
        if b"=" not in field:
            continue
        key, value = field.split(b"=", 1)
        values[key.decode(errors="replace")] = value.decode(errors="replace")
    return values


def _rank(environment: dict[str, str]) -> int | None:
    for key in RANK_ENV_KEYS:
        value = environment.get(key)
        if value is not None:
            try:
                return int(value)
            except ValueError:
                return None
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


def _sha256(path: Path, cache: dict[str, str | None]) -> str | None:
    key = str(path)
    if key in cache:
        return cache[key]
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            cache[key] = None
            return None
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        value: str | None = digest.hexdigest()
    except (FileNotFoundError, IsADirectoryError, PermissionError, OSError):
        value = None
    cache[key] = value
    return value


def classify_mapping(
    original: Path,
    realpath: Path,
    old_prefix: Path,
    recovery_root: Path,
    system_roots: list[Path],
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
    if any(re.fullmatch(pattern, str(original)) for pattern in TRANSIENT_MAPPING_PATTERNS):
        return "transient_system"
    if any(
        _lexically_within(original, root)
        or _lexically_within(realpath, root)
        or _within(original, root)
        or _within(realpath, root)
        for root in system_roots
    ):
        return "system"
    return "unexpected"


def _capture_processes(
    root_pid: int,
    expected_launcher: Path,
    old_prefix: Path,
    recovery_root: Path,
    system_roots: list[Path],
    processes: dict[int, dict],
    objects: dict[tuple[int, str], dict],
) -> None:
    for pid in _descendants(root_pid):
        proc = Path("/proc") / str(pid)
        try:
            executable = Path(os.readlink(proc / "exe")).resolve(strict=False)
        except (FileNotFoundError, PermissionError, OSError):
            continue
        environment = _environment(pid)
        rank = _rank(environment)
        if executable == expected_launcher:
            role = "launcher"
        elif rank is not None:
            role = "rank"
        else:
            role = "support"
        record = processes.setdefault(
            pid,
            {
                "pid": pid,
                "role": role,
                "rank": rank,
                "executable_realpath": str(executable),
                "executable_sha256": None,
                "mapped_object_count": 0,
            },
        )
        if record["executable_realpath"] != str(executable):
            record["executable_realpath"] = str(executable)
            record["executable_sha256"] = None
        previous_identity = (record["role"], record["rank"])
        if role == "rank":
            record["role"] = role
            record["rank"] = rank
        elif role == "launcher" and record["role"] != "rank":
            record["role"] = role
        if previous_identity != (record["role"], record["rank"]):
            for (object_pid, _), object_row in objects.items():
                if object_pid == pid:
                    object_row["role"] = record["role"]
                    object_row["rank"] = record["rank"]
        for original_value in _mapped_paths(pid):
            key = (pid, original_value)
            if key in objects:
                continue
            original = Path(original_value)
            realpath = original.resolve(strict=False)
            objects[key] = {
                "pid": pid,
                "role": record["role"],
                "rank": record["rank"],
                "mapped_path": original_value,
                "loaded_realpath": str(realpath),
                "loaded_sha256": None,
                "classification": classify_mapping(
                    original, realpath, old_prefix, recovery_root, system_roots
                ),
            }
    counts: dict[int, int] = {}
    for pid, _ in objects:
        counts[pid] = counts.get(pid, 0) + 1
    for pid, count in counts.items():
        if pid in processes:
            processes[pid]["mapped_object_count"] = count


def _write_objects(path: Path, objects: list[dict]) -> None:
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
        writer = csv.DictWriter(handle, fieldnames=header, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for row in objects:
            writer.writerow({key: "" if row[key] is None else row[key] for key in header})


def main() -> int:
    if not sys.argv[1:]:
        print("mpi_prefix_audit_launcher.py requires mpirun arguments", file=sys.stderr)
        return 2
    required_environment = (
        "M_OFDFT_REAL_MPIRUN",
        "M_OFDFT_EXPECTED_MPIRUN_SHA256",
        "M_OFDFT_EXPECTED_LAUNCHER",
        "M_OFDFT_EXPECTED_LAUNCHER_SHA256",
        "M_OFDFT_EXPECTED_ABACUS",
        "M_OFDFT_EXPECTED_ABACUS_SHA256",
        "M_OFDFT_MPI_AUDIT_DIR",
        "M_OFDFT_RECOVERY_ROOT",
        "M_OFDFT_RECOVERY_PREFIX",
        "M_OFDFT_OLD_PREFIX",
        "M_OFDFT_MPI_AUDIT_EXPECTED_RANKS",
        "M_OFDFT_MPI_AUDIT_ALLOWED_PROBE_COUNT",
    )
    missing = [key for key in required_environment if not os.environ.get(key)]
    if missing:
        print(f"missing MPI audit environment: {','.join(missing)}", file=sys.stderr)
        return 2
    real_mpirun = Path(os.environ["M_OFDFT_REAL_MPIRUN"]).resolve(strict=True)
    expected_mpirun_sha256 = os.environ["M_OFDFT_EXPECTED_MPIRUN_SHA256"]
    expected_launcher = Path(os.environ["M_OFDFT_EXPECTED_LAUNCHER"]).resolve(
        strict=True
    )
    expected_launcher_sha256 = os.environ["M_OFDFT_EXPECTED_LAUNCHER_SHA256"]
    expected_abacus = Path(os.environ["M_OFDFT_EXPECTED_ABACUS"]).resolve(strict=True)
    expected_abacus_sha256 = os.environ["M_OFDFT_EXPECTED_ABACUS_SHA256"]
    audit_directory = Path(os.environ["M_OFDFT_MPI_AUDIT_DIR"])
    recovery_root = Path(os.environ["M_OFDFT_RECOVERY_ROOT"]).resolve(strict=False)
    recovery_prefix = Path(os.environ["M_OFDFT_RECOVERY_PREFIX"]).resolve(strict=False)
    old_prefix = Path(os.environ["M_OFDFT_OLD_PREFIX"]).resolve(strict=False)
    expected_ranks = int(os.environ["M_OFDFT_MPI_AUDIT_EXPECTED_RANKS"])
    expected_probe_count = int(os.environ["M_OFDFT_MPI_AUDIT_ALLOWED_PROBE_COUNT"])
    allowed_probe = old_prefix / "classid"
    allowed_errno = "ENOENT"
    system_roots = [
        Path(value)
        for value in os.environ.get(
            "M_OFDFT_MPI_AUDIT_SYSTEM_ROOTS", "/usr:/lib:/lib64:/dev:/proc:/sys"
        ).split(":")
        if value
    ]
    prefix_values = {
        key: os.environ.get(key)
        for key in ("OPAL_PREFIX", "PRTE_PREFIX", "PMIX_PREFIX")
    }
    ld_library_path = os.environ.get("LD_LIBRARY_PATH")
    ld_preload = os.environ.get("LD_PRELOAD")
    audit_directory.mkdir(parents=True, exist_ok=False)
    strace_directory = audit_directory / "strace"
    strace_directory.mkdir()
    strace_mode = os.environ.get("M_OFDFT_MPI_AUDIT_STRACE_MODE", "require")
    strace = shutil.which("strace")
    if strace_mode not in {"require", "auto", "off"}:
        print(f"invalid strace mode: {strace_mode}", file=sys.stderr)
        return 2
    if strace_mode == "require" and strace is None:
        payload = {
            "status": "rejected",
            "failure_reasons": ["required_strace_unavailable"],
            "file_trace_status": "unavailable",
            "old_prefix": str(old_prefix),
            "recovery_prefix": str(recovery_prefix),
        }
        (audit_directory / "audit.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        _write_objects(audit_directory / "objects.tsv", [])
        return 96

    command = [str(real_mpirun), *sys.argv[1:]]
    file_trace_status = "disabled"
    if strace and strace_mode != "off":
        command = [
            strace,
            "-ff",
            "-qq",
            "-s",
            "4096",
            "-e",
            "trace=file",
            "-o",
            str(strace_directory / "trace"),
            *command,
        ]
        file_trace_status = "completed"
    elif strace_mode == "auto":
        file_trace_status = "unavailable"

    processes: dict[int, dict] = {}
    objects: dict[tuple[int, str], dict] = {}
    started = time.time()
    process = subprocess.Popen(command)
    while process.poll() is None:
        _capture_processes(
            process.pid,
            expected_launcher,
            old_prefix,
            recovery_root,
            system_roots,
            processes,
            objects,
        )
        time.sleep(0.02)
    _capture_processes(
        process.pid,
        expected_launcher,
        old_prefix,
        recovery_root,
        system_roots,
        processes,
        objects,
    )
    exit_code = int(process.returncode)

    hash_cache: dict[str, str | None] = {}
    for record in processes.values():
        record["executable_sha256"] = _sha256(
            Path(record["executable_realpath"]), hash_cache
        )
    for record in objects.values():
        if record["classification"] != "transient_system":
            record["loaded_sha256"] = _sha256(
                Path(record["loaded_realpath"]), hash_cache
            )

    trace_lines: list[str] = []
    if file_trace_status == "completed":
        for trace_path in sorted(strace_directory.glob("trace*")):
            trace_lines.extend(
                trace_path.read_text(encoding="utf-8", errors="replace").splitlines()
            )
    access = parse_strace_lines(trace_lines, old_prefix, allowed_probe, allowed_errno)
    execve_paths = parse_execve_paths(trace_lines)
    mpirun_invocation_execve_observed = str(real_mpirun) in execve_paths
    launcher_execve_observed = str(expected_launcher) in execve_paths
    object_rows = sorted(objects.values(), key=lambda row: (row["pid"], row["mapped_path"]))
    process_rows = sorted(processes.values(), key=lambda row: row["pid"])
    launchers = [row for row in process_rows if row["role"] == "launcher"]
    rank_processes = [row for row in process_rows if row["role"] == "rank"]
    ranks = sorted(
        {
            int(row["rank"])
            for row in process_rows
            if row["role"] == "rank" and row["rank"] is not None
        }
    )
    old_mappings = sum(row["classification"] == "old_prefix" for row in object_rows)
    unexpected_mappings = sum(
        row["classification"] == "unexpected" for row in object_rows
    )
    transient_system_mappings = sum(
        row["classification"] == "transient_system" for row in object_rows
    )
    launcher_executable_mismatches = sum(
        Path(row["executable_realpath"]).resolve(strict=False) != expected_launcher
        or row["executable_sha256"] != expected_launcher_sha256
        for row in launchers
    )
    rank_executable_mismatches = sum(
        Path(row["executable_realpath"]).resolve(strict=False) != expected_abacus
        or row["executable_sha256"] != expected_abacus_sha256
        for row in rank_processes
    )
    target_process_empty_maps = sum(
        int(row.get("mapped_object_count", 0)) <= 0
        for row in [*launchers, *rank_processes]
    )
    target_process_missing_executable_sha = sum(
        not row.get("executable_sha256") for row in [*launchers, *rank_processes]
    )
    target_executable_mapping_missing = 0
    for process_row in [*launchers, *rank_processes]:
        expected_executable = Path(process_row["executable_realpath"]).resolve(strict=False)
        if not any(
            object_row["pid"] == process_row["pid"]
            and Path(object_row["loaded_realpath"]).resolve(strict=False)
            == expected_executable
            for object_row in object_rows
        ):
            target_executable_mapping_missing += 1
    unhashed_regular_mapped_objects = sum(
        row["classification"] != "transient_system"
        and Path(row["loaded_realpath"]).is_file()
        and not row.get("loaded_sha256")
        for row in object_rows
    )
    unverifiable_recovery_mapped_objects = sum(
        row["classification"] == "recovery_runtime" and not row.get("loaded_sha256")
        for row in object_rows
    )
    failures = []
    if exit_code != 0:
        failures.append(f"launcher_exit_code:{exit_code}")
    if len(launchers) != 1:
        failures.append(f"observed_launcher_count:{len(launchers)}")
    if len(rank_processes) != expected_ranks or ranks != list(range(expected_ranks)):
        failures.append(f"observed_ranks:{ranks}")
    if launcher_executable_mismatches:
        failures.append(
            f"launcher_executable_mismatch_count:{launcher_executable_mismatches}"
        )
    if not mpirun_invocation_execve_observed:
        failures.append("frozen_mpirun_invocation_execve_not_observed")
    if not launcher_execve_observed:
        failures.append("frozen_final_launcher_execve_not_observed")
    if rank_executable_mismatches:
        failures.append(f"rank_executable_mismatch_count:{rank_executable_mismatches}")
    if target_process_empty_maps:
        failures.append(f"target_process_empty_maps_count:{target_process_empty_maps}")
    if target_process_missing_executable_sha:
        failures.append(
            "target_process_missing_executable_sha_count:"
            f"{target_process_missing_executable_sha}"
        )
    if target_executable_mapping_missing:
        failures.append(
            "target_executable_mapping_missing_count:"
            f"{target_executable_mapping_missing}"
        )
    if unhashed_regular_mapped_objects:
        failures.append(
            f"unhashed_regular_mapped_object_count:{unhashed_regular_mapped_objects}"
        )
    if unverifiable_recovery_mapped_objects:
        failures.append(
            "unverifiable_recovery_mapped_object_count:"
            f"{unverifiable_recovery_mapped_objects}"
        )
    if old_mappings:
        failures.append(f"old_prefix_mapped_object_count:{old_mappings}")
    if unexpected_mappings:
        failures.append(f"unexpected_mapped_object_count:{unexpected_mappings}")
    if any(value != str(recovery_prefix) for value in prefix_values.values()):
        failures.append("mpi_component_prefix_environment_mismatch")
    if ld_library_path != str(recovery_prefix / "lib"):
        failures.append(f"ld_library_path_mismatch:{ld_library_path}")
    if ld_preload not in (None, ""):
        failures.append("ld_preload_must_be_unset")
    if file_trace_status != "completed":
        failures.append(f"file_trace_status:{file_trace_status}")
    if access["old_prefix_successful_access_count"]:
        failures.append(
            "old_prefix_successful_access_count:"
            f"{access['old_prefix_successful_access_count']}"
        )
    if access["other_old_prefix_attempt_count"]:
        failures.append(
            f"other_old_prefix_attempt_count:{access['other_old_prefix_attempt_count']}"
        )
    if access["allowed_failed_probe_count"] != expected_probe_count:
        failures.append(
            "allowed_failed_probe_count:"
            f"{access['allowed_failed_probe_count']}!=expected:{expected_probe_count}"
        )

    payload = {
        "schema_version": 1,
        "status": "accepted" if not failures else "rejected",
        "failure_reasons": failures,
        "command": [str(real_mpirun), *sys.argv[1:]],
        "launcher_exit_code": exit_code,
        "elapsed_seconds": time.time() - started,
        "old_prefix": str(old_prefix),
        "recovery_root": str(recovery_root),
        "recovery_prefix": str(recovery_prefix),
        "expected_mpirun_path": str(real_mpirun),
        "expected_mpirun_sha256": expected_mpirun_sha256,
        "expected_launcher_path": str(expected_launcher),
        "expected_launcher_sha256": expected_launcher_sha256,
        "expected_abacus_path": str(expected_abacus),
        "expected_abacus_sha256": expected_abacus_sha256,
        "prefix_environment": prefix_values,
        "ld_library_path": ld_library_path,
        "ld_preload": ld_preload,
        "observed_launcher_count": len(launchers),
        "observed_ranks": ranks,
        "observed_execve_realpaths": execve_paths,
        "mpirun_invocation_execve_observed": mpirun_invocation_execve_observed,
        "launcher_execve_observed": launcher_execve_observed,
        "processes": process_rows,
        "mapped_object_count": len(object_rows),
        "old_prefix_mapped_object_count": old_mappings,
        "unexpected_mapped_object_count": unexpected_mappings,
        "transient_system_mapped_object_count": transient_system_mappings,
        "launcher_executable_mismatch_count": launcher_executable_mismatches,
        "rank_executable_mismatch_count": rank_executable_mismatches,
        "target_process_empty_maps_count": target_process_empty_maps,
        "target_process_missing_executable_sha_count": (
            target_process_missing_executable_sha
        ),
        "target_executable_mapping_missing_count": target_executable_mapping_missing,
        "unhashed_regular_mapped_object_count": unhashed_regular_mapped_objects,
        "unverifiable_recovery_mapped_object_count": (
            unverifiable_recovery_mapped_objects
        ),
        "file_trace_status": file_trace_status,
        "strace_path": str(Path(strace).resolve()) if strace else None,
        "strace_sha256": _sha256(Path(strace).resolve(), hash_cache) if strace else None,
        "allowed_failed_probe_path": str(allowed_probe),
        "allowed_failed_probe_errno": allowed_errno,
        **access,
    }
    _write_objects(audit_directory / "objects.tsv", object_rows)
    (audit_directory / "audit.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if exit_code != 0:
        return exit_code
    return 0 if not failures else 96


if __name__ == "__main__":
    raise SystemExit(main())
