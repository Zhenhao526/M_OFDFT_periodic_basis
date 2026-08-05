#!/usr/bin/env python3
"""Enter an ephemeral user/mount namespace before launching the MPI audit."""

from __future__ import annotations

import csv
import json
import os
import signal
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from s1_runtime_relocation_elf import remaining_seconds, sha256, versioned_tool_identity


COUNTERPART_HEADER = (
    "recovery_path",
    "recovery_realpath",
    "recovery_relative_path",
    "recovery_sha256",
    "old_counterpart_path",
    "old_counterpart_realpath",
    "old_counterpart_sha256",
    "byte_equal",
    "verification_rule",
)
ABSOLUTE_WATCHDOG_SECONDS = 7260.0
UNSHARE_NAMESPACE_ARGUMENTS = (
    "--user",
    "--map-root-user",
    "--kill-child=KILL",
    "--mount",
    "--pid",
    "--fork",
    "--mount-proc",
    "--propagation",
    "private",
)


class AbsoluteWatchdogExpired(BaseException):
    """Uncatchable by broad operational-error handlers inside the audit."""


def _atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _tool_from_environment(
    prefix: str, label: str, deadline: float | None = None
) -> tuple[Path, dict]:
    path = Path(os.environ[f"M_OFDFT_{prefix}_TOOL"])
    expected = {
        "path": os.environ[f"M_OFDFT_{prefix}_PATH"],
        "realpath": os.environ[f"M_OFDFT_{prefix}_REALPATH"],
        "sha256": os.environ[f"M_OFDFT_{prefix}_SHA256"],
        "version_first_line": os.environ[f"M_OFDFT_{prefix}_VERSION_FIRST_LINE"],
        "version_output_sha256": os.environ[
            f"M_OFDFT_{prefix}_VERSION_OUTPUT_SHA256"
        ],
    }
    actual = versioned_tool_identity(path, label, deadline=deadline)
    for key, value in expected.items():
        if actual.get(key) != value:
            raise ValueError(f"{label} {key} differs from frozen identity")
    return path, actual


def _script_identity(
    environment_key: str,
    sha_key: str,
    label: str,
    deadline: float | None = None,
) -> Path:
    path = Path(os.environ[environment_key]).resolve(strict=True)
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"{label} is missing or a symbolic link: {path}")
    if sha256(path, deadline, f"{label} hashing") != os.environ[sha_key]:
        raise ValueError(f"{label} SHA-256 differs from frozen identity")
    return path


def _path_identity(path: Path) -> dict | None:
    try:
        value = path.lstat()
    except FileNotFoundError:
        return None
    return {
        "device": value.st_dev,
        "gid": value.st_gid,
        "inode": value.st_ino,
        "mode": value.st_mode,
        "mode_type": stat.S_IFMT(value.st_mode),
        "mtime_ns": value.st_mtime_ns,
        "size": value.st_size,
        "uid": value.st_uid,
        "is_symlink": path.is_symlink(),
        "realpath": str(path.resolve(strict=False)),
    }


def _host_old_runtime_identity() -> dict:
    old_root = Path(os.environ["M_OFDFT_OLD_ROOT"])
    old_prefix = Path(os.environ["M_OFDFT_OLD_PREFIX"])
    mount_lines = []
    for line in Path("/proc/self/mountinfo").read_text(
        encoding="utf-8", errors="replace"
    ).splitlines():
        fields = line.split()
        if len(fields) >= 5 and fields[4] == str(old_root):
            mount_lines.append(line)
    return {
        "host_uid": os.getuid(),
        "host_gid": os.getgid(),
        "old_root": str(old_root),
        "old_prefix": str(old_prefix),
        "old_root_exists": old_root.exists(),
        "old_prefix_exists": old_prefix.exists(),
        "old_root_lstat": _path_identity(old_root),
        "old_prefix_lstat": _path_identity(old_prefix),
        "old_root_mountinfo_lines": mount_lines,
    }


def _frozen_identity(prefix: str) -> dict:
    return {
        "path": os.environ[f"M_OFDFT_{prefix}_PATH"],
        "realpath": os.environ[f"M_OFDFT_{prefix}_REALPATH"],
        "sha256": os.environ[f"M_OFDFT_{prefix}_SHA256"],
    }


def _write_counterpart_rows(path: Path, rows: list[dict]) -> None:
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=COUNTERPART_HEADER,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _verify_recovery_counterparts(
    audit_directory: Path, total_deadline: float
) -> dict:
    """Compare every captured recovery component with its old-root counterpart."""

    objects_path = audit_directory / "objects.tsv"
    if not objects_path.is_file() or objects_path.is_symlink():
        raise ValueError("runtime objects.tsv is missing or symbolic")
    recovery_root = Path(os.environ["M_OFDFT_RECOVERY_ROOT"]).resolve(strict=True)
    old_root = Path(os.environ["M_OFDFT_OLD_ROOT"]).resolve(strict=True)
    replay_identities = {
        "relocated_abacus_elf_gate": _frozen_identity("REPLAY_ABACUS"),
        "mpirun_identity_gate": _frozen_identity("REPLAY_MPIRUN"),
        "launcher_identity_gate": _frozen_identity("REPLAY_LAUNCHER"),
    }
    reference_identities = {
        "relocated_abacus_elf_gate": _frozen_identity("REFERENCE_ABACUS"),
        "mpirun_identity_gate": _frozen_identity("REFERENCE_MPIRUN"),
        "launcher_identity_gate": _frozen_identity("REFERENCE_LAUNCHER"),
    }
    exclusions = {
        str(Path(identity["realpath"]).resolve(strict=True)): rule
        for rule, identity in replay_identities.items()
    }
    failures: list[str] = []
    for rule, replay in replay_identities.items():
        if time.monotonic() >= total_deadline:
            raise TimeoutError("namespace deadline exceeded during identity comparison")
        reference = reference_identities[rule]
        replay_path = Path(replay["realpath"]).resolve(strict=True)
        reference_path = Path(reference["realpath"]).resolve(strict=True)
        if sha256(
            replay_path, total_deadline, f"{rule} replay hashing"
        ) != replay["sha256"]:
            failures.append(f"{rule}:replay_identity_changed")
        if sha256(
            reference_path, total_deadline, f"{rule} reference hashing"
        ) != reference["sha256"]:
            failures.append(f"{rule}:reference_identity_changed")
        if rule != "relocated_abacus_elf_gate" and (
            replay["sha256"] != reference["sha256"]
        ):
            failures.append(f"{rule}:registered_reference_replay_bytes_differ")

    with objects_path.open(newline="", encoding="utf-8") as handle:
        object_rows = list(csv.DictReader(handle, delimiter="\t"))
    recovery_objects: dict[str, dict[str, str]] = {}
    for row in object_rows:
        if row.get("classification") != "recovery_runtime":
            continue
        mapped_path = str(Path(os.path.abspath(row["mapped_path"])))
        realpath = str(Path(row["loaded_realpath"]).resolve(strict=True))
        registered_sha = row.get("loaded_sha256", "")
        value = {"realpath": realpath, "sha256": registered_sha}
        previous = recovery_objects.setdefault(mapped_path, value)
        if previous != value:
            failures.append(f"inconsistent_recovery_object:{mapped_path}")

    rows = []
    missing_count = 0
    mismatch_count = 0
    for recovery_value, registered in sorted(recovery_objects.items()):
        if time.monotonic() >= total_deadline:
            raise TimeoutError("namespace deadline exceeded during counterpart hashing")
        recovery_path = Path(recovery_value)
        recovery_realpath = Path(registered["realpath"])
        try:
            relative = recovery_path.relative_to(recovery_root)
        except ValueError:
            try:
                relative = recovery_realpath.relative_to(recovery_root)
            except ValueError:
                failures.append(f"recovery_object_outside_root:{recovery_path}")
                continue
        recovery_sha = sha256(
            recovery_realpath,
            total_deadline,
            f"recovery counterpart hashing: {recovery_realpath}",
        )
        if recovery_sha != registered["sha256"]:
            failures.append(f"recovery_object_hash_changed:{recovery_realpath}")
        rule = exclusions.get(str(recovery_realpath))
        if rule is not None:
            reference = reference_identities[rule]
            old_path = Path(reference["path"])
            old_realpath = Path(reference["realpath"]).resolve(strict=True)
            old_sha = sha256(
                old_realpath,
                total_deadline,
                f"registered counterpart hashing: {old_realpath}",
            )
            byte_equal = recovery_sha == old_sha
            if rule != "relocated_abacus_elf_gate" and not byte_equal:
                mismatch_count += 1
        else:
            rule = "relative_path_counterpart_byte_equality"
            old_path = old_root / relative
            try:
                old_realpath = old_path.resolve(strict=True)
            except FileNotFoundError:
                old_realpath = old_path.resolve(strict=False)
                old_sha = ""
                byte_equal = False
                missing_count += 1
            else:
                if not old_realpath.is_file():
                    old_sha = ""
                    byte_equal = False
                    missing_count += 1
                else:
                    old_sha = sha256(
                        old_realpath,
                        total_deadline,
                        f"old counterpart hashing: {old_realpath}",
                    )
                    byte_equal = recovery_sha == old_sha
                    if not byte_equal:
                        mismatch_count += 1
        rows.append(
            {
                "recovery_path": str(recovery_path),
                "recovery_realpath": str(recovery_realpath),
                "recovery_relative_path": relative.as_posix(),
                "recovery_sha256": recovery_sha,
                "old_counterpart_path": str(old_path),
                "old_counterpart_realpath": str(old_realpath),
                "old_counterpart_sha256": old_sha,
                "byte_equal": "true" if byte_equal else "false",
                "verification_rule": rule,
            }
        )
    if missing_count:
        failures.append(f"mapped_component_counterpart_missing_count:{missing_count}")
    if mismatch_count:
        failures.append(f"mapped_component_byte_mismatch_count:{mismatch_count}")
    _write_counterpart_rows(audit_directory / "counterparts.tsv", rows)
    payload = {
        "schema_version": 1,
        "status": "accepted" if not failures else "rejected",
        "failure_reasons": failures,
        "captured_recovery_component_count": len(rows),
        "counterpart_missing_count": missing_count,
        "counterpart_byte_mismatch_count": mismatch_count,
        "exclusion_rules": {
            rule: {
                "reference": reference_identities[rule],
                "replay": replay_identities[rule],
                "byte_equality_required": rule != "relocated_abacus_elf_gate",
            }
            for rule in sorted(replay_identities)
        },
    }
    _atomic_json(audit_directory / "counterpart_audit.json", payload)
    return payload


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


def _accessible_host_pid_namespace_scan(
    namespace_inode: int | None, proc_root: Path = Path("/proc")
) -> dict:
    """Find readable host processes still attached to the namespace.

    A per-process ``PermissionError`` is deliberately non-fatal: the kernel PID 1
    lifecycle proof is authoritative, while this scan is an auxiliary negative
    check over the subset of host ``/proc`` entries that the invoking user may
    inspect.  Failure to enumerate ``/proc`` itself remains fatal.
    """

    members: list[dict] = []
    inaccessible: list[dict] = []
    fatal_errors: list[str] = []
    if (
        not isinstance(namespace_inode, int)
        or isinstance(namespace_inode, bool)
        or namespace_inode <= 0
    ):
        fatal_errors.append("invalid_or_missing_pid_namespace_inode")
        return {
            "schema_version": 1,
            "pid_namespace_inode": namespace_inode,
            "accessible_matching_members": members,
            "accessible_matching_member_count": 0,
            "inaccessible_pid_count": 0,
            "inaccessible_pid_samples": [],
            "fatal_errors": fatal_errors,
            "no_accessible_matching_members": False,
            "scan_passed": False,
        }
    try:
        proc_entries = list(proc_root.iterdir())
    except OSError as error:
        fatal_errors.append(f"cannot_list_host_proc:{error}")
        return {
            "schema_version": 1,
            "pid_namespace_inode": namespace_inode,
            "accessible_matching_members": members,
            "accessible_matching_member_count": 0,
            "inaccessible_pid_count": 0,
            "inaccessible_pid_samples": [],
            "fatal_errors": fatal_errors,
            "no_accessible_matching_members": False,
            "scan_passed": False,
        }
    for entry in proc_entries:
        if not entry.name.isdigit():
            continue
        namespace_path = entry / "ns" / "pid"
        try:
            inode = namespace_path.stat().st_ino
        except FileNotFoundError:
            continue
        except PermissionError as error:
            inaccessible.append(
                {"host_pid": int(entry.name), "error": str(error)}
            )
            continue
        except OSError as error:
            fatal_errors.append(
                f"cannot_stat_pid_namespace:{entry.name}:{error}"
            )
            continue
        if inode != namespace_inode:
            continue
        try:
            stat_text = (entry / "stat").read_text(encoding="ascii")
            start_time_ticks = int(stat_text[stat_text.rfind(")") + 2 :].split()[19])
        except (FileNotFoundError, PermissionError, OSError, ValueError, IndexError):
            start_time_ticks = None
        members.append(
            {
                "host_pid": int(entry.name),
                "pid_namespace_inode": inode,
                "start_time_ticks": start_time_ticks,
            }
        )
    members.sort(key=lambda row: row["host_pid"])
    inaccessible.sort(key=lambda row: row["host_pid"])
    no_accessible_matching_members = not members
    return {
        "schema_version": 1,
        "pid_namespace_inode": namespace_inode,
        "accessible_matching_members": members,
        "accessible_matching_member_count": len(members),
        "inaccessible_pid_count": len(inaccessible),
        "inaccessible_pid_samples": inaccessible[:20],
        "fatal_errors": fatal_errors,
        "no_accessible_matching_members": no_accessible_matching_members,
        "scan_passed": no_accessible_matching_members and not fatal_errors,
    }


def _read_namespace_evidence(path: Path, label: str) -> tuple[dict, list[str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError) as error:
        return {}, [f"{label}_unavailable:{error}"]
    if not isinstance(payload, dict):
        return {}, [f"{label}_not_json_object"]
    return payload, []


def _pid1_kernel_reap_proof(
    namespace_directory: Path,
    command: list[str],
    expected_unshare_argv: list[str],
    expected_payload_argv: list[str],
    process_wait_completed_normally: bool,
    process_wait_exit_code: int | None,
) -> dict:
    before, before_errors = _read_namespace_evidence(
        namespace_directory / "state.before_mount.json", "state_before_mount"
    )
    after, after_errors = _read_namespace_evidence(
        namespace_directory / "state.after_run.json", "state_after_run"
    )
    payload, payload_errors = _read_namespace_evidence(
        namespace_directory / "payload_status.json", "payload_status"
    )
    evidence_errors = [*before_errors, *after_errors, *payload_errors]
    expected_command = [*expected_unshare_argv, *expected_payload_argv]
    command_contract_satisfied = (
        bool(expected_unshare_argv)
        and expected_unshare_argv[1:] == list(UNSHARE_NAMESPACE_ARGUMENTS)
        and command == expected_command
    )
    namespace_init_pids = [
        before.get("namespace_init_pid"),
        after.get("namespace_init_pid"),
    ]
    pid1_is_one = all(
        isinstance(value, int)
        and not isinstance(value, bool)
        and value == 1
        for value in namespace_init_pids
    )
    namespace_inodes = [
        before.get("pid_namespace_inode"),
        after.get("pid_namespace_inode"),
        payload.get("pid_namespace_inode"),
    ]
    pid_namespace_inode_consistent = (
        all(
            isinstance(value, int)
            and not isinstance(value, bool)
            and value > 0
            for value in namespace_inodes
        )
        and len(set(namespace_inodes)) == 1
    )
    payload_accepted_and_exit_zero = (
        payload.get("status") == "accepted"
        and isinstance(payload.get("audit_launcher_exit_code"), int)
        and not isinstance(payload.get("audit_launcher_exit_code"), bool)
        and payload.get("audit_launcher_exit_code") == 0
    )
    wait_exit_zero = (
        process_wait_completed_normally is True
        and isinstance(process_wait_exit_code, int)
        and not isinstance(process_wait_exit_code, bool)
        and process_wait_exit_code == 0
    )
    all_namespace_members_reaped = (
        not evidence_errors
        and command_contract_satisfied
        and pid1_is_one
        and pid_namespace_inode_consistent
        and payload_accepted_and_exit_zero
        and wait_exit_zero
    )
    return {
        "schema_version": 1,
        "authority": "linux_pid_namespace_init_exit_and_unshare_parent_wait",
        "expected_unshare_argv": expected_unshare_argv,
        "expected_payload_argv": expected_payload_argv,
        "observed_command": command,
        "command_contract_satisfied": command_contract_satisfied,
        "process_wait_completed_normally": process_wait_completed_normally,
        "process_wait_exit_code": process_wait_exit_code,
        "process_wait_exit_zero": wait_exit_zero,
        "state_before_mount": {
            "namespace_init_pid": before.get("namespace_init_pid"),
            "pid_namespace_inode": before.get("pid_namespace_inode"),
        },
        "state_after_run": {
            "namespace_init_pid": after.get("namespace_init_pid"),
            "pid_namespace_inode": after.get("pid_namespace_inode"),
        },
        "payload_status": {
            "status": payload.get("status"),
            "audit_launcher_exit_code": payload.get("audit_launcher_exit_code"),
            "pid_namespace_inode": payload.get("pid_namespace_inode"),
        },
        "pid1_is_one": pid1_is_one,
        "pid_namespace_inode": (
            namespace_inodes[0]
            if isinstance(namespace_inodes[0], int)
            and not isinstance(namespace_inodes[0], bool)
            and namespace_inodes[0] > 0
            else None
        ),
        "pid_namespace_inode_consistent": pid_namespace_inode_consistent,
        "payload_accepted_and_exit_zero": payload_accepted_and_exit_zero,
        "evidence_errors": evidence_errors,
        "all_namespace_members_reaped": all_namespace_members_reaped,
    }


def _descendants(root_pid: int) -> list[int]:
    selected: set[int] = set()
    pending = [root_pid]
    while pending:
        pid = pending.pop()
        if pid in selected:
            continue
        selected.add(pid)
        task_root = Path("/proc") / str(pid) / "task"
        try:
            tasks = list(task_root.iterdir())
        except (FileNotFoundError, PermissionError, OSError):
            continue
        for task in tasks:
            try:
                values = (task / "children").read_text(encoding="ascii").split()
            except (FileNotFoundError, PermissionError, OSError):
                continue
            pending.extend(int(value) for value in values if value.isdigit())
    return sorted(selected)


def _terminate_group(process: subprocess.Popen) -> dict:
    before = _process_group_members(process.pid)
    tracees = _descendants(process.pid)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        remaining_tracees = [pid for pid in tracees if Path("/proc", str(pid)).exists()]
        if (
            process.poll() is not None
            and not _process_group_members(process.pid)
            and not remaining_tracees
        ):
            break
        try:
            os.killpg(process.pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        for pid in reversed(remaining_tracees):
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            continue
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


def _main_impl(
    started: float,
    started_monotonic: float,
    started_at_utc: str,
    total_deadline: float,
    watchdog_state: dict[str, object],
) -> int:
    audit_directory = Path(os.environ["M_OFDFT_MPI_AUDIT_DIR"])
    try:
        audit_directory.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        print(f"refusing to reuse runtime audit directory: {audit_directory}", file=sys.stderr)
        return 2
    namespace_directory = audit_directory / "namespace"
    namespace_directory.mkdir()
    failure_reasons: list[str] = []
    command: list[str] = []
    exit_code = 97
    before: dict[str, dict] = {}
    after: dict[str, dict] = {}
    host_old_runtime_before: dict = {}
    host_old_runtime_after: dict = {}
    counterpart_audit: dict = {}
    process: subprocess.Popen | None = None
    expected_unshare_argv: list[str] = []
    expected_payload_argv: list[str] = []
    process_wait_completed_normally = False
    process_wait_exit_code: int | None = None
    timeout_triggered = False
    cleanup_evidence = {
        "members_before_cleanup": [],
        "members_after_cleanup": [],
        "tracee_pids_before_cleanup": [],
        "tracee_pids_after_cleanup": [],
        "all_group_members_gone": False,
    }
    try:
        if os.getuid() == 0:
            raise ValueError("host namespace launcher must not run as root")
        if os.getuid() != int(os.environ["M_OFDFT_HOST_UID"]) or os.getgid() != int(
            os.environ["M_OFDFT_HOST_GID"]
        ):
            raise ValueError("host uid/gid differs from frozen namespace identity")
        host_old_runtime_before = _host_old_runtime_identity()
        if (
            not host_old_runtime_before["old_root_exists"]
            or not host_old_runtime_before["old_prefix_exists"]
            or host_old_runtime_before["old_root_mountinfo_lines"]
            or host_old_runtime_before["old_root_lstat"]["is_symlink"]
            or host_old_runtime_before["old_prefix_lstat"]["is_symlink"]
        ):
            raise ValueError("host old runtime preflight identity is unsafe")
        unshare_path, before["unshare"] = _tool_from_environment(
            "UNSHARE", "unshare", total_deadline
        )
        bash_path, before["bash"] = _tool_from_environment(
            "BASH", "bash", total_deadline
        )
        mount_path, before["mount"] = _tool_from_environment(
            "MOUNT", "mount", total_deadline
        )
        _, before["python"] = _tool_from_environment(
            "PYTHON", "python", total_deadline
        )
        payload = _script_identity(
            "M_OFDFT_NAMESPACE_PAYLOAD",
            "M_OFDFT_NAMESPACE_PAYLOAD_SHA256",
            "namespace payload",
            total_deadline,
        )
        audit_launcher = _script_identity(
            "M_OFDFT_AUDIT_LAUNCHER",
            "M_OFDFT_AUDIT_LAUNCHER_SHA256",
            "runtime audit launcher",
            total_deadline,
        )
        rank_wrapper = _script_identity(
            "M_OFDFT_RANK_WRAPPER",
            "M_OFDFT_RANK_WRAPPER_SHA256",
            "rank handshake wrapper",
            total_deadline,
        )
        if Path(os.environ["M_OFDFT_MOUNT_TOOL"]).resolve(strict=True) != mount_path.resolve(
            strict=True
        ):
            raise ValueError("namespace mount tool differs from frozen mount identity")
        expected_unshare_argv = [str(unshare_path), *UNSHARE_NAMESPACE_ARGUMENTS]
        expected_payload_argv = [str(bash_path), str(payload), *sys.argv[1:]]
        command = [*expected_unshare_argv, *expected_payload_argv]
        watchdog_state["command"] = command
        watchdog_state["expected_unshare_argv"] = expected_unshare_argv
        watchdog_state["expected_payload_argv"] = expected_payload_argv
        preflight = {
            "schema_version": 1,
            "status": "accepted",
            "tools": before,
            "namespace_payload_path": str(payload),
            "namespace_payload_sha256": sha256(
                payload, total_deadline, "namespace payload preflight hashing"
            ),
            "audit_launcher_path": str(audit_launcher),
            "audit_launcher_sha256": sha256(
                audit_launcher, total_deadline, "audit launcher preflight hashing"
            ),
            "rank_wrapper_path": str(rank_wrapper),
            "rank_wrapper_sha256": sha256(
                rank_wrapper, total_deadline, "rank wrapper preflight hashing"
            ),
            "command": command,
            "stdin": "/dev/null",
            "host_old_runtime_before": host_old_runtime_before,
        }
        _atomic_json(namespace_directory / "host_preflight.json", preflight)
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        watchdog_state["process"] = process
        try:
            exit_code = int(
                process.wait(timeout=max(0.001, total_deadline - time.monotonic()))
            )
        except subprocess.TimeoutExpired:
            timeout_triggered = True
            failure_reasons.append("namespace_total_wall_timeout:7260")
            cleanup_evidence = _terminate_group(process)
            exit_code = int(process.returncode if process.returncode is not None else 97)
        else:
            process_wait_completed_normally = True
            process_wait_exit_code = exit_code
            watchdog_state["process_wait_completed_normally"] = True
            watchdog_state["process_wait_exit_code"] = exit_code
            residual = _process_group_members(process.pid)
            if residual:
                cleanup_evidence = _terminate_group(process)
                failure_reasons.append("namespace_process_group_residual_after_exit")
            else:
                cleanup_evidence = _terminate_group(process)
        if exit_code == 0:
            counterpart_audit = _verify_recovery_counterparts(
                audit_directory, total_deadline
            )
            if counterpart_audit.get("status") != "accepted":
                failure_reasons.extend(counterpart_audit.get("failure_reasons", []))
    except (KeyError, FileNotFoundError, OSError, TimeoutError, ValueError) as error:
        if isinstance(error, TimeoutError):
            timeout_triggered = True
        failure_reasons.append(f"namespace_launch_failed:{error}")
        if process is not None:
            cleanup_evidence = _terminate_group(process)
    pid1_kernel_reap_proof = _pid1_kernel_reap_proof(
        namespace_directory,
        command,
        expected_unshare_argv,
        expected_payload_argv,
        process_wait_completed_normally,
        process_wait_exit_code,
    )
    accessible_host_pid_namespace_scan = _accessible_host_pid_namespace_scan(
        pid1_kernel_reap_proof["pid_namespace_inode"]
    )
    if not pid1_kernel_reap_proof["all_namespace_members_reaped"]:
        failure_reasons.append("pid1_kernel_reap_proof_failed")
    if accessible_host_pid_namespace_scan["fatal_errors"]:
        failure_reasons.append("accessible_host_pid_namespace_scan_fatal")
    if accessible_host_pid_namespace_scan["accessible_matching_members"]:
        failure_reasons.append("accessible_host_pid_namespace_member_detected")
    if not cleanup_evidence["all_group_members_gone"]:
        failure_reasons.append("namespace_process_group_cleanup_incomplete")
    for prefix, label in (
        ("UNSHARE", "unshare"),
        ("BASH", "bash"),
        ("MOUNT", "mount"),
        ("PYTHON", "python"),
    ):
        try:
            _, after[label] = _tool_from_environment(prefix, label, total_deadline)
            if label in before and after[label] != before[label]:
                failure_reasons.append(f"{label}_identity_changed_during_run")
        except (KeyError, FileNotFoundError, OSError, ValueError) as error:
            failure_reasons.append(f"{label}_postflight_failed:{error}")
    try:
        host_old_runtime_after = _host_old_runtime_identity()
        if host_old_runtime_after != host_old_runtime_before:
            failure_reasons.append("host_old_runtime_identity_changed_or_mount_propagated")
    except (KeyError, FileNotFoundError, OSError, ValueError) as error:
        failure_reasons.append(f"host_old_runtime_postflight_failed:{error}")
    if time.monotonic() >= total_deadline:
        timeout_triggered = True
        failure_reasons.append("namespace_total_deadline_exceeded_during_postflight")
    if exit_code != 0:
        failure_reasons.append(f"namespace_payload_exit_code:{exit_code}")
    ended_monotonic = time.monotonic()
    ended = time.time()
    if ended_monotonic > total_deadline:
        timeout_triggered = True
        failure_reasons.append("namespace_absolute_deadline_exceeded_before_summary")
    status = {
        "schema_version": 1,
        "status": "accepted" if not failure_reasons else "rejected",
        "failure_reasons": failure_reasons,
        "command": command,
        "stdin": "/dev/null",
        "namespace_payload_exit_code": exit_code,
        "total_wall_timeout_seconds": 7260,
        "absolute_deadline_watchdog_seconds": 7260,
        "started_at_utc": started_at_utc,
        "ended_at_utc": datetime.now(timezone.utc).isoformat(),
        "started_epoch_seconds": started,
        "ended_epoch_seconds": ended,
        "elapsed_seconds": ended_monotonic - started_monotonic,
        "timeout_triggered": timeout_triggered,
        "process_group_cleanup": cleanup_evidence,
        "pid1_kernel_reap_proof": pid1_kernel_reap_proof,
        "accessible_host_pid_namespace_scan": accessible_host_pid_namespace_scan,
        "tools_before": before,
        "tools_after": after,
        "host_old_runtime_before": host_old_runtime_before,
        "host_old_runtime_after": host_old_runtime_after,
        "counterpart_audit": counterpart_audit,
    }
    _atomic_json(namespace_directory / "host_status.json", status)
    if time.monotonic() > total_deadline and status["status"] == "accepted":
        status["status"] = "rejected"
        status["timeout_triggered"] = True
        status["failure_reasons"].append(
            "namespace_absolute_deadline_exceeded_during_summary_write"
        )
        status["ended_epoch_seconds"] = time.time()
        status["ended_at_utc"] = datetime.now(timezone.utc).isoformat()
        status["elapsed_seconds"] = time.monotonic() - started_monotonic
        _atomic_json(namespace_directory / "host_status.json", status)
    return 0 if not failure_reasons else (exit_code if exit_code else 97)


def _deadline_alarm(_signum, _frame) -> None:
    raise AbsoluteWatchdogExpired("namespace absolute watchdog expired")


def main() -> int:
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
                "namespace deadline elapsed before watchdog activation"
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
        cleanup_failures: list[str] = []
        process = watchdog_state.get("process")
        if process is not None:
            try:
                cleanup_evidence = _terminate_group(process)
            except Exception as cleanup_error:
                cleanup_failures.append(f"watchdog_cleanup_failed:{cleanup_error}")
        if not cleanup_evidence["all_group_members_gone"]:
            cleanup_failures.append("namespace_process_group_cleanup_incomplete")
        audit_directory = Path(os.environ.get("M_OFDFT_MPI_AUDIT_DIR", "."))
        namespace_directory = audit_directory / "namespace"
        namespace_directory.mkdir(parents=True, exist_ok=True)
        command = watchdog_state.get("command", [])
        expected_unshare_argv = watchdog_state.get("expected_unshare_argv", [])
        expected_payload_argv = watchdog_state.get("expected_payload_argv", [])
        if not isinstance(command, list):
            command = []
        if not isinstance(expected_unshare_argv, list):
            expected_unshare_argv = []
        if not isinstance(expected_payload_argv, list):
            expected_payload_argv = []
        pid1_kernel_reap_proof = _pid1_kernel_reap_proof(
            namespace_directory,
            command,
            expected_unshare_argv,
            expected_payload_argv,
            watchdog_state.get("process_wait_completed_normally") is True,
            (
                watchdog_state.get("process_wait_exit_code")
                if isinstance(watchdog_state.get("process_wait_exit_code"), int)
                and not isinstance(watchdog_state.get("process_wait_exit_code"), bool)
                else None
            ),
        )
        accessible_host_pid_namespace_scan = _accessible_host_pid_namespace_scan(
            pid1_kernel_reap_proof["pid_namespace_inode"]
        )
        if not pid1_kernel_reap_proof["all_namespace_members_reaped"]:
            cleanup_failures.append("pid1_kernel_reap_proof_failed")
        if accessible_host_pid_namespace_scan["fatal_errors"]:
            cleanup_failures.append("accessible_host_pid_namespace_scan_fatal")
        if accessible_host_pid_namespace_scan["accessible_matching_members"]:
            cleanup_failures.append(
                "accessible_host_pid_namespace_member_detected"
            )
        ended_monotonic = time.monotonic()
        ended = time.time()
        _atomic_json(
            namespace_directory / "host_status.json",
            {
                "schema_version": 1,
                "status": "rejected",
                "failure_reasons": [
                    f"namespace_absolute_watchdog:{error}",
                    *cleanup_failures,
                ],
                "namespace_payload_exit_code": 124,
                "total_wall_timeout_seconds": 7260,
                "absolute_deadline_watchdog_seconds": 7260,
                "started_at_utc": started_at_utc,
                "ended_at_utc": datetime.now(timezone.utc).isoformat(),
                "started_epoch_seconds": started,
                "ended_epoch_seconds": ended,
                "elapsed_seconds": ended_monotonic - started_monotonic,
                "timeout_triggered": True,
                "process_group_cleanup": cleanup_evidence,
                "pid1_kernel_reap_proof": pid1_kernel_reap_proof,
                "accessible_host_pid_namespace_scan": (
                    accessible_host_pid_namespace_scan
                ),
            },
        )
        return 124
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)


if __name__ == "__main__":
    raise SystemExit(main())
