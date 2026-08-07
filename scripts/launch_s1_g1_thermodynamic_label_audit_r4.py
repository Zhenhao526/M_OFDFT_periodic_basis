#!/usr/bin/env python3
"""Launch and attest the detached G1 thermodynamic-label R4 supervisor.

The supervisor is intentionally outside the SSH/PTY process group.  It waits
for a separately committed detachment attestation before it creates a GO token
and invokes the formal runner.  A state directory is single-use: this launcher
never restarts a stopped or failed scientific attempt.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
import re
import resource
import signal
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn


DEFAULT_STATE_DIRECTORY = Path(
    "/home/shenwei01/.local/state/m_ofdft/g1_thermodynamic_label_audit_r4_20260807"
)
DEFAULT_CONFIG = Path("config/S1_g1_thermodynamic_label_audit_r4.json")
DEFAULT_MANIFEST = Path("config/S1_g1_thermodynamic_label_audit_r4_manifest.tsv")
DEFAULT_RUNNER = Path("scripts/run_s1_g1_thermodynamic_label_audit_r4.sh")
DEFAULT_ATTESTATION = Path(
    "orchestration/s1/g1_thermodynamic_label_audit_r4_20260807/detachment.json"
)
DEFAULT_COMPLETION = Path(
    "orchestration/s1/g1_thermodynamic_label_audit_r4_20260807/supervisor_completion.json"
)
DEFAULT_ANALYSIS_SUMMARY = Path(
    "analysis/s1/g1_thermodynamic_label_audit_r4_20260807/summary.json"
)
PROTOCOL_REVISION = "S1-G1-THERMODYNAMIC-LABEL-AUDIT-R4"
FROZEN_AMBIENT_ENVIRONMENT_KEYS = (
    "HOME",
    "LC_ALL",
    "LOGNAME",
    "PATH",
    "PYTHONHASHSEED",
    "PYTHONIOENCODING",
    "PYTHONNOUSERSITE",
    "PYTHONUTF8",
    "TZ",
    "USER",
)
FROZEN_AMBIENT_ENVIRONMENT_VALUES = {
    "HOME": "/home/shenwei01",
    "LC_ALL": "C",
    "LOGNAME": "shenwei01",
    "PATH": "/usr/bin:/bin",
    "PYTHONHASHSEED": "0",
    "PYTHONIOENCODING": "UTF-8",
    "PYTHONNOUSERSITE": "1",
    "PYTHONUTF8": "1",
    "TZ": "UTC",
    "USER": "shenwei01",
}
RUNNER_BINDING_ENVIRONMENT_KEYS = (
    "M_OFDFT_G1_R4_SUPERVISOR_STATE_DIRECTORY",
    "M_OFDFT_G1_R4_SUPERVISOR_PID",
    "M_OFDFT_G1_R4_SUPERVISOR_START_TIME_TICKS",
    "M_OFDFT_G1_R4_BOOT_ID",
    "M_OFDFT_G1_R4_LAUNCH_SHA256",
    "M_OFDFT_G1_R4_GO_SHA256",
)
SEALED_EXECUTION_INPUT_MODE = "linux_memfd_sealed_v1"
SEALED_EXECUTION_INPUT_FDS = {
    "runner": 200,
    "manifest": 201,
    "config": 202,
}
SEALED_EXECUTION_INPUT_PROC_PATHS = {
    name: f"/proc/self/fd/{descriptor}"
    for name, descriptor in SEALED_EXECUTION_INPUT_FDS.items()
}
SEALED_EXECUTION_INPUT_SEAL_NAMES = (
    "F_SEAL_SEAL",
    "F_SEAL_SHRINK",
    "F_SEAL_GROW",
    "F_SEAL_WRITE",
)
SEALED_EXECUTION_INPUT_SEAL_MASK = 15
GO_PAYLOAD_KEYS = (
    "schema_version",
    "protocol_revision",
    "status",
    "launch_sha256",
    "boot_id",
    "supervisor_pid",
    "supervisor_start_time_ticks",
    "attestation_path",
    "attestation_sha256",
    "git_head",
    "registered_files",
    "sealed_execution_inputs_sha256",
    "created_utc",
)
ACCEPTED_TERMINAL_KEYS = (
    "schema_version",
    "protocol_revision",
    "status",
    "runner_return_code",
    "runner_pid",
    "runner_start_time_ticks",
    "launch_sha256",
    "go_sha256",
    "journal_sha256",
    "git_head_after_runner",
    "analysis_summary_path",
    "analysis_summary_sha256",
    "log_sha256",
    "finished_utc",
)
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_UTC = re.compile(
    r"20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,9})?Z\Z"
)
_BOOT_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z"
)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(payload: object) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def _sha256(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"not a regular file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_exclusive(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.tmp-{os.getpid()}-{time.time_ns()}"
    linked = False
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            data = _canonical(payload)
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short write while publishing supervisor evidence")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.link(temporary, path)
        linked = True
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        if linked:
            path.unlink(missing_ok=True)
        raise
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _read_object(path: Path) -> dict[str, object]:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"not a regular JSON file: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return value


def _stable_stat_fields(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_uid,
        value.st_gid,
        value.st_rdev,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _read_stable_regular_bytes_with_sha256(path: Path) -> tuple[bytes, str]:
    """Bind raw bytes and SHA-256 to one stable regular-file read."""

    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise ValueError("stable regular-file read requires O_NOFOLLOW")
    flags = os.O_RDONLY | nofollow
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ValueError(f"cannot open stable regular file: {path}") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"stable path is not a regular file: {path}")
        blocks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            blocks.append(block)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    raw = b"".join(blocks)
    if _stable_stat_fields(before) != _stable_stat_fields(after) or len(raw) != before.st_size:
        raise ValueError(f"stable file changed while being read: {path}")
    return raw, hashlib.sha256(raw).hexdigest()


def _read_stable_object_with_sha256(
    path: Path,
) -> tuple[bytes, dict[str, object], str]:
    """Bind raw bytes, parsed JSON, and SHA-256 to one stable regular-file read."""

    raw, digest = _read_stable_regular_bytes_with_sha256(path)
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return raw, value, digest


def _require_stable_go_path_unchanged(
    path: Path, validated_raw: bytes, validated_sha256: str
) -> None:
    current_raw, _, current_sha256 = _read_stable_object_with_sha256(path)
    if current_sha256 != validated_sha256 or current_raw != validated_raw:
        raise ValueError("GO path changed after validation")


def _git(project_root: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(project_root), *arguments], text=True
    ).strip()


def _project_root(path: Path) -> Path:
    root = path.resolve()
    if Path(_git(root, "rev-parse", "--show-toplevel")).resolve() != root:
        raise ValueError("project root differs from Git top level")
    return root


def _require_clean(project_root: Path) -> None:
    if _git(project_root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("formal detached launch requires a clean worktree")


def _proc_record(pid: int) -> dict[str, object]:
    stat_path = Path(f"/proc/{pid}/stat")
    raw = stat_path.read_text(encoding="ascii").strip()
    close = raw.rfind(")")
    if close < 0:
        raise ValueError(f"malformed process stat for PID {pid}")
    fields = raw[close + 2 :].split()  # field 3 onward
    if len(fields) < 20:
        raise ValueError(f"short process stat for PID {pid}")
    fd_root = Path(f"/proc/{pid}/fd")
    return {
        "pid": pid,
        "ppid": int(fields[1]),
        "process_group_id": int(fields[2]),
        "session_id": int(fields[3]),
        "tty_nr": int(fields[4]),
        "start_time_ticks": int(fields[19]),
        "stdin": os.readlink(fd_root / "0"),
        "stdout": os.readlink(fd_root / "1"),
        "stderr": os.readlink(fd_root / "2"),
    }


def _positive_integer(value: object) -> bool:
    return type(value) is int and value > 0


def _require_detached_process_record(
    observed: dict[str, object],
    registered: dict[str, object],
    pid: int,
    state_directory: Path,
) -> None:
    for field in ("pid", "ppid", "process_group_id", "session_id", "start_time_ticks"):
        if not _positive_integer(registered.get(field)):
            raise ValueError(f"registered supervisor {field} is not a positive integer")
    if type(registered.get("tty_nr")) is not int:
        raise ValueError("registered supervisor tty_nr is not an integer")
    if registered.get("pid") != pid:
        raise ValueError("registered supervisor PID differs")
    if observed.get("start_time_ticks") != registered.get("start_time_ticks"):
        raise ValueError("supervisor PID was reused")
    if observed.get("session_id") != pid or observed.get("process_group_id") != pid:
        raise ValueError("supervisor is not its own detached session/process group")
    if observed.get("tty_nr") != 0:
        raise ValueError("supervisor still has a controlling terminal")
    if observed.get("stdin") != "/dev/null":
        raise ValueError("supervisor stdin is not /dev/null")
    expected_log = str(state_directory / "supervisor.log")
    if observed.get("stdout") != expected_log:
        raise ValueError("supervisor stdout is not the exact persistent log path")
    if observed.get("stderr") != expected_log:
        raise ValueError("supervisor stderr is not the exact persistent log path")


def _boot_id() -> str:
    return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()


def _umask() -> str:
    value = os.umask(0)
    os.umask(value)
    return f"{value:04o}"


def _canonical_values(values: dict[str, str]) -> bytes:
    return (
        json.dumps(values, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _ambient_environment_contract(config: dict[str, object]) -> dict[str, object]:
    execution = config.get("execution")
    if not isinstance(execution, dict):
        raise ValueError("configuration execution registration is missing")
    observed = execution.get("ambient_environment")
    expected = {
        "keys_exact": list(FROZEN_AMBIENT_ENVIRONMENT_KEYS),
        "values_exact": dict(FROZEN_AMBIENT_ENVIRONMENT_VALUES),
        "canonical_values_sha256": hashlib.sha256(
            _canonical_values(FROZEN_AMBIENT_ENVIRONMENT_VALUES)
        ).hexdigest(),
        "mutating_launcher_exact_match_required": True,
        "supervisor_umask_exact": "0022",
        "python_no_user_site_required": True,
        "validator_subprocess_explicit_environment_required": True,
        "supervisor_subprocess_explicit_environment_required": True,
        "runner_additional_binding_keys_exact": list(
            RUNNER_BINDING_ENVIRONMENT_KEYS
        ),
        "runner_registered_bash_required": True,
    }
    if observed != expected:
        raise ValueError("configuration frozen ambient-environment contract differs")
    return expected


def _sealed_execution_input_contract(config: dict[str, object]) -> dict[str, object]:
    execution = config.get("execution")
    if not isinstance(execution, dict):
        raise ValueError("configuration execution registration is missing")
    observed = execution.get("sealed_execution_inputs")
    expected = {
        "mode": SEALED_EXECUTION_INPUT_MODE,
        "fixed_fds_exact": dict(SEALED_EXECUTION_INPUT_FDS),
        "proc_paths_exact": dict(SEALED_EXECUTION_INPUT_PROC_PATHS),
        "seal_mask_exact": SEALED_EXECUTION_INPUT_SEAL_MASK,
        "seal_names_exact": list(SEALED_EXECUTION_INPUT_SEAL_NAMES),
        "popen_pass_fds_exact": list(SEALED_EXECUTION_INPUT_FDS.values()),
        "registered_bash_executes_runner_fd": True,
        "scientific_config_manifest_from_sealed_fds_required": True,
        "canonical_paths_provenance_only": True,
    }
    if observed != expected:
        raise ValueError("configuration sealed-execution-input contract differs")
    return expected


def _kernel_seal_mask() -> int:
    if not hasattr(fcntl, "F_ADD_SEALS") or not hasattr(fcntl, "F_GET_SEALS"):
        raise ValueError("Linux file-seal operations are unavailable")
    mask = 0
    for name in SEALED_EXECUTION_INPUT_SEAL_NAMES:
        value = getattr(fcntl, name, None)
        if type(value) is not int:
            raise ValueError(f"Linux file-seal constant is unavailable: {name}")
        mask |= value
    if mask != SEALED_EXECUTION_INPUT_SEAL_MASK:
        raise ValueError("Linux file-seal mask differs from frozen registration")
    return mask


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short write while freezing registered input")
        view = view[written:]


def _read_all(descriptor: int) -> bytes:
    size = os.fstat(descriptor).st_size
    blocks: list[bytes] = []
    offset = 0
    while offset < size:
        block = os.pread(descriptor, min(1024 * 1024, size - offset), offset)
        if not block:
            break
        blocks.append(block)
        offset += len(block)
    payload = b"".join(blocks)
    if len(payload) != size:
        raise ValueError("short read from sealed execution input")
    return payload


def _close_descriptors(descriptors: tuple[int, ...]) -> None:
    for descriptor in descriptors:
        try:
            os.close(descriptor)
        except OSError as error:
            if error.errno != errno.EBADF:
                raise


def _freeze_registered_inputs(
    project_root: Path,
    config_relative: Path,
    manifest_relative: Path,
    runner_relative: Path,
) -> tuple[dict[str, object], dict[str, object], tuple[int, ...]]:
    """Stable-read registered inputs once, copy them to sealed fixed memfds."""

    if sys.platform != "linux" or not hasattr(os, "memfd_create"):
        raise ValueError("sealed execution inputs require Linux memfd_create")
    allow_sealing = getattr(os, "MFD_ALLOW_SEALING", None)
    cloexec = getattr(os, "MFD_CLOEXEC", None)
    if type(allow_sealing) is not int or type(cloexec) is not int:
        raise ValueError("Linux memfd sealing flags are unavailable")
    seal_mask = _kernel_seal_mask()
    fixed_fds = tuple(SEALED_EXECUTION_INPUT_FDS.values())
    soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft_limit <= max(fixed_fds):
        raise ValueError("RLIMIT_NOFILE is too low for sealed execution fixed FDs")
    proc_fd_root = Path("/proc/self/fd")
    if not proc_fd_root.is_dir() or not os.access(proc_fd_root, os.R_OK | os.X_OK):
        raise ValueError("sealed execution inputs require accessible /proc/self/fd")
    for descriptor in fixed_fds:
        try:
            os.fstat(descriptor)
        except OSError as error:
            if error.errno != errno.EBADF:
                raise
        else:
            raise ValueError(f"sealed execution fixed FD is already open: {descriptor}")

    paths = {
        "runner": project_root / runner_relative,
        "manifest": project_root / manifest_relative,
        "config": project_root / config_relative,
    }
    payloads: dict[str, bytes] = {}
    digests: dict[str, str] = {}
    for name, path in paths.items():
        payloads[name], digests[name] = _read_stable_regular_bytes_with_sha256(path)
    try:
        config = json.loads(payloads["config"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("frozen configuration is not valid UTF-8 JSON") from error
    if not isinstance(config, dict):
        raise ValueError("frozen configuration JSON root is not an object")
    if config.get("protocol_revision") != PROTOCOL_REVISION:
        raise ValueError("frozen configuration protocol revision differs")
    if config.get("status") != "preregistered":
        raise ValueError("frozen configuration is not preregistered")
    _sealed_execution_input_contract(config)

    created_fixed: list[int] = []
    try:
        for name, fixed_fd in SEALED_EXECUTION_INPUT_FDS.items():
            temporary_fd = os.memfd_create(
                f"m_ofdft_g1_r4_{name}", allow_sealing | cloexec
            )
            transferred_to_fixed = False
            try:
                _write_all(temporary_fd, payloads[name])
                os.fsync(temporary_fd)
                fcntl.fcntl(temporary_fd, fcntl.F_ADD_SEALS, seal_mask)
                if fcntl.fcntl(temporary_fd, fcntl.F_GET_SEALS) != seal_mask:
                    raise ValueError(f"sealed execution {name} seal mask differs")
                if temporary_fd != fixed_fd:
                    os.dup2(temporary_fd, fixed_fd, inheritable=False)
                transferred_to_fixed = True
            finally:
                if temporary_fd != fixed_fd or not transferred_to_fixed:
                    os.close(temporary_fd)
            created_fixed.append(fixed_fd)
            if fcntl.fcntl(fixed_fd, fcntl.F_GET_SEALS) != seal_mask:
                raise ValueError(f"sealed execution {name} fixed-FD seals differ")
            if hashlib.sha256(_read_all(fixed_fd)).hexdigest() != digests[name]:
                raise ValueError(f"sealed execution {name} fixed-FD bytes differ")
    except Exception:
        for descriptor in created_fixed:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise

    registered_files: dict[str, object] = {
        "config_path": config_relative.as_posix(),
        "config_sha256": digests["config"],
        "manifest_path": manifest_relative.as_posix(),
        "manifest_sha256": digests["manifest"],
        "runner_path": runner_relative.as_posix(),
        "runner_sha256": digests["runner"],
    }
    inputs: dict[str, object] = {}
    for name in SEALED_EXECUTION_INPUT_FDS:
        inputs[name] = {
            "fd": SEALED_EXECUTION_INPUT_FDS[name],
            "proc_path": SEALED_EXECUTION_INPUT_PROC_PATHS[name],
            "canonical_path": str(paths[name]),
            "sha256": digests[name],
        }
    record: dict[str, object] = {
        "mode": SEALED_EXECUTION_INPUT_MODE,
        "seal_mask": seal_mask,
        "seal_names": list(SEALED_EXECUTION_INPUT_SEAL_NAMES),
        "pass_fds": list(fixed_fds),
        "inputs": inputs,
    }
    return registered_files, record, fixed_fds


def _sealed_execution_inputs_sha256(record: dict[str, object]) -> str:
    return hashlib.sha256(_canonical(record)).hexdigest()


def _require_exact_sealed_execution_inputs(
    project_root: Path,
    config_relative: Path,
    manifest_relative: Path,
    runner_relative: Path,
    registered_files: dict[str, object],
    record: object,
) -> dict[str, object]:
    if not isinstance(record, dict):
        raise ValueError("launch sealed-execution-input record is missing")
    expected_paths = {
        "runner": project_root / runner_relative,
        "manifest": project_root / manifest_relative,
        "config": project_root / config_relative,
    }
    expected_digests = {
        "runner": registered_files.get("runner_sha256"),
        "manifest": registered_files.get("manifest_sha256"),
        "config": registered_files.get("config_sha256"),
    }
    expected_inputs = {
        name: {
            "fd": SEALED_EXECUTION_INPUT_FDS[name],
            "proc_path": SEALED_EXECUTION_INPUT_PROC_PATHS[name],
            "canonical_path": str(expected_paths[name]),
            "sha256": expected_digests[name],
        }
        for name in SEALED_EXECUTION_INPUT_FDS
    }
    expected: dict[str, object] = {
        "mode": SEALED_EXECUTION_INPUT_MODE,
        "seal_mask": SEALED_EXECUTION_INPUT_SEAL_MASK,
        "seal_names": list(SEALED_EXECUTION_INPUT_SEAL_NAMES),
        "pass_fds": list(SEALED_EXECUTION_INPUT_FDS.values()),
        "inputs": expected_inputs,
    }
    if record != expected:
        raise ValueError("launch sealed-execution-input record differs")
    return record


def _require_live_sealed_execution_inputs(
    pid: int, record: dict[str, object]
) -> None:
    seal_mask = _kernel_seal_mask()
    inputs = record.get("inputs")
    if not isinstance(inputs, dict):
        raise ValueError("live sealed-execution-input table is missing")
    for name, fixed_fd in SEALED_EXECUTION_INPUT_FDS.items():
        item = inputs.get(name)
        if not isinstance(item, dict):
            raise ValueError(f"live sealed execution {name} record is missing")
        path = Path(f"/proc/{pid}/fd/{fixed_fd}")
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        except OSError as error:
            raise ValueError(f"cannot open live sealed execution {name} FD") from error
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError(f"live sealed execution {name} FD is not regular")
            if fcntl.fcntl(descriptor, fcntl.F_GET_SEALS) != seal_mask:
                raise ValueError(f"live sealed execution {name} seals differ")
            digest = hashlib.sha256(_read_all(descriptor)).hexdigest()
        finally:
            os.close(descriptor)
        if digest != item.get("sha256"):
            raise ValueError(f"live sealed execution {name} SHA-256 differs")


def _attestation_introduction_commit(
    project_root: Path, attestation_relative: Path
) -> str:
    commits = [
        line
        for line in _git(
            project_root,
            "log",
            "--no-renames",
            "--format=%H",
            "--diff-filter=A",
            "--",
            attestation_relative.as_posix(),
        ).splitlines()
        if line
    ]
    if len(commits) != 1 or not _HEX40.fullmatch(commits[0]):
        raise ValueError("detachment attestation must have one introduction commit")
    return commits[0]


def _environment_registration(config: dict[str, object]) -> dict[str, object]:
    contract = _ambient_environment_contract(config)
    return {
        "keys_exact": contract["keys_exact"],
        "values_exact": contract["values_exact"],
        "canonical_values_sha256": contract["canonical_values_sha256"],
    }


def _registered_tool(config: dict[str, object], name: str) -> Path:
    runtime = config.get("runtime")
    if not isinstance(runtime, dict):
        raise ValueError("configuration runtime table is missing")
    tools = runtime.get("tools")
    if not isinstance(tools, dict) or not isinstance(tools.get(name), dict):
        raise ValueError(f"configuration {name} tool registration is missing")
    registration = tools[name]
    assert isinstance(registration, dict)
    path_value = registration.get("path")
    realpath_value = registration.get("realpath")
    digest = registration.get("sha256")
    if not isinstance(path_value, str) or not Path(path_value).is_absolute():
        raise ValueError(f"registered {name} path is not absolute")
    if not isinstance(realpath_value, str) or not Path(realpath_value).is_absolute():
        raise ValueError(f"registered {name} realpath is not absolute")
    path = Path(path_value)
    realpath = Path(realpath_value)
    if not path.is_file() or path.resolve() != realpath:
        raise ValueError(f"registered {name} path/realpath differs")
    if not realpath.is_file() or realpath.is_symlink() or _sha256(realpath) != digest:
        raise ValueError(f"registered {name} realpath/SHA-256 differs")
    return path


def _require_mutating_process_context(
    project_root: Path, config_relative: Path
) -> tuple[dict[str, object], dict[str, str], Path, Path]:
    config = _read_object(project_root / config_relative)
    _ambient_environment_contract(config)
    frozen = dict(FROZEN_AMBIENT_ENVIRONMENT_VALUES)
    observed = dict(os.environ)
    if observed != frozen:
        missing = sorted(set(frozen) - set(observed))
        extra = sorted(set(observed) - set(frozen))
        mismatched = sorted(
            key for key in set(observed) & set(frozen) if observed[key] != frozen[key]
        )
        raise ValueError(
            "mutating launcher ambient environment differs from frozen registration: "
            f"missing={missing}, extra={extra}, mismatched={mismatched}"
        )
    expected_umask = str(
        _ambient_environment_contract(config)["supervisor_umask_exact"]
    )
    if _umask() != expected_umask:
        raise ValueError(
            "mutating launcher umask differs from frozen registration: "
            f"expected={expected_umask}"
        )
    python_tool = _registered_tool(config, "python")
    bash_tool = _registered_tool(config, "bash")
    runtime = config["runtime"]
    assert isinstance(runtime, dict)
    tools = runtime["tools"]
    assert isinstance(tools, dict)
    python_registration = tools["python"]
    assert isinstance(python_registration, dict)
    current = Path(sys.executable)
    if (
        not current.is_absolute()
        or current.resolve() != python_tool.resolve()
        or _sha256(current.resolve()) != python_registration.get("sha256")
    ):
        raise ValueError("current launcher interpreter differs from registered Python")
    if not sys.flags.no_user_site:
        raise ValueError("mutating launcher requires Python user-site isolation")
    return config, frozen, python_tool, bash_tool


def _journal_append(state_directory: Path, event: str, **fields: object) -> None:
    payload = {"event": event, "pid": os.getpid(), "utc": _utc(), **fields}
    descriptor = os.open(
        state_directory / "journal.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
    )
    try:
        os.write(descriptor, json.dumps(payload, sort_keys=True).encode() + b"\n")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _journal_events(state_directory: Path) -> list[dict[str, object]]:
    path = state_directory / "journal.jsonl"
    if not path.is_file() or path.is_symlink():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("supervisor journal row is not an object")
        rows.append(value)
    return rows


def _require_accepted_terminal_and_journal(
    state_directory: Path,
    launch: dict[str, object],
    go_payload: dict[str, object],
    terminal: dict[str, object],
) -> tuple[list[dict[str, object]], bytes, str]:
    if set(terminal) != set(ACCEPTED_TERMINAL_KEYS):
        raise ValueError("supervisor accepted terminal key set differs")
    if (
        type(terminal.get("schema_version")) is not int
        or terminal.get("schema_version") != 1
        or terminal.get("protocol_revision") != PROTOCOL_REVISION
        or terminal.get("status") != "accepted"
        or type(terminal.get("runner_return_code")) is not int
        or terminal.get("runner_return_code") != 0
        or not _positive_integer(terminal.get("runner_pid"))
        or not _positive_integer(terminal.get("runner_start_time_ticks"))
        or not isinstance(terminal.get("finished_utc"), str)
        or not _UTC.fullmatch(terminal["finished_utc"])
    ):
        raise ValueError("supervisor accepted terminal schema or identity differs")

    launch_process = launch.get("process")
    if not isinstance(launch_process, dict) or not _positive_integer(
        launch_process.get("pid")
    ):
        raise ValueError("supervisor launch process identity is invalid")
    launch_pid = launch_process["pid"]
    journal_path = state_directory / "journal.jsonl"
    journal_bytes, journal_sha256 = _read_stable_regular_bytes_with_sha256(
        journal_path
    )
    if not journal_bytes or not journal_bytes.endswith(b"\n"):
        raise ValueError("supervisor journal is empty or not newline terminated")
    events: list[dict[str, object]] = []
    for line in journal_bytes.decode("utf-8").splitlines():
        event = json.loads(line)
        if not isinstance(event, dict):
            raise ValueError("supervisor journal row is not an object")
        events.append(event)
    event_keys = {
        "waiting_for_go": {"event", "pid", "utc"},
        "waiting_heartbeat": {"event", "pid", "utc"},
        "sighup_received": {"event", "pid", "utc"},
        "go_accepted": {"event", "pid", "utc", "git_head", "go_sha256"},
        "runner_started": {
            "event",
            "pid",
            "utc",
            "child_pid",
            "child_start_time_ticks",
        },
        "runner_finished": {"event", "pid", "utc", "return_code"},
    }
    for index, event in enumerate(events, 1):
        name = event.get("event")
        if not isinstance(name, str) or name not in event_keys:
            raise ValueError(f"supervisor journal event {index} name differs")
        if set(event) != event_keys[name]:
            raise ValueError(f"supervisor journal event {index} key set differs")
        utc = event.get("utc")
        if (
            not _positive_integer(event.get("pid"))
            or event.get("pid") != launch_pid
            or not isinstance(utc, str)
            or not _UTC.fullmatch(utc)
        ):
            raise ValueError(f"supervisor journal event {index} identity differs")
        if name == "runner_started" and (
            not _positive_integer(event.get("child_pid"))
            or not _positive_integer(event.get("child_start_time_ticks"))
        ):
            raise ValueError("supervisor journal runner-start identity differs")
        if name == "runner_finished" and type(event.get("return_code")) is not int:
            raise ValueError("supervisor journal runner-finish code type differs")

    names = [event["event"] for event in events]
    accepted = [index for index, name in enumerate(names) if name == "go_accepted"]
    started = [index for index, name in enumerate(names) if name == "runner_started"]
    finished = [index for index, name in enumerate(names) if name == "runner_finished"]
    if (
        not names
        or names[0] != "waiting_for_go"
        or names.count("waiting_for_go") != 1
        or len(accepted) != 1
        or len(started) != 1
        or len(finished) != 1
        or not accepted[0] < started[0] < finished[0]
        or finished[0] != len(events) - 1
        or any(
            name in {"waiting_heartbeat", "sighup_received"}
            for name in names[accepted[0] + 1 :]
        )
    ):
        raise ValueError("supervisor accepted journal event sequence differs")
    go_event = events[accepted[0]]
    started_event = events[started[0]]
    finished_event = events[finished[0]]
    if (
        go_event.get("git_head") != go_payload.get("git_head")
        or go_event.get("go_sha256") != _sha256(state_directory / "go.json")
        or started_event.get("child_pid") != terminal.get("runner_pid")
        or started_event.get("child_start_time_ticks")
        != terminal.get("runner_start_time_ticks")
        or type(finished_event.get("return_code")) is not int
        or finished_event.get("return_code") != 0
    ):
        raise ValueError("supervisor terminal and journal result differ")
    return events, journal_bytes, journal_sha256


def _registered_files(
    project_root: Path, config_relative: Path, manifest_relative: Path, runner_relative: Path
) -> dict[str, object]:
    config = project_root / config_relative
    manifest = project_root / manifest_relative
    runner = project_root / runner_relative
    _, parsed, config_sha256 = _read_stable_object_with_sha256(config)
    _, manifest_sha256 = _read_stable_regular_bytes_with_sha256(manifest)
    _, runner_sha256 = _read_stable_regular_bytes_with_sha256(runner)
    if parsed.get("protocol_revision") != PROTOCOL_REVISION:
        raise ValueError("configuration protocol revision differs")
    if parsed.get("status") != "preregistered":
        raise ValueError("configuration is not preregistered")
    _sealed_execution_input_contract(parsed)
    return {
        "config_path": config_relative.as_posix(),
        "config_sha256": config_sha256,
        "manifest_path": manifest_relative.as_posix(),
        "manifest_sha256": manifest_sha256,
        "runner_path": runner_relative.as_posix(),
        "runner_sha256": runner_sha256,
    }


def _run_validator(
    project_root: Path,
    config_relative: Path,
    manifest_relative: Path,
    *flags: str,
) -> None:
    _, frozen_environment, python_tool, _ = _require_mutating_process_context(
        project_root, config_relative
    )
    validator = project_root / "scripts/validate_s1_g1_thermodynamic_label_audit_r4.py"
    subprocess.check_call(
        [
            str(python_tool),
            "-s",
            str(validator),
            str(project_root / manifest_relative),
            "--config",
            str(project_root / config_relative),
            *flags,
        ],
        cwd=project_root,
        stdout=subprocess.DEVNULL,
        env=frozen_environment,
    )


def _validate_registration(
    project_root: Path, config_relative: Path, manifest_relative: Path
) -> None:
    _run_validator(
        project_root,
        config_relative,
        manifest_relative,
        "--require-committed",
    )


def _execution_registration(
    project_root: Path, config_relative: Path
) -> dict[str, object]:
    config = _read_object(project_root / config_relative)
    execution = config.get("execution")
    if not isinstance(execution, dict):
        raise ValueError("configuration execution registration is missing")
    return execution


def _require_registered_launch_paths(
    project_root: Path,
    config_relative: Path,
    manifest_relative: Path,
    runner_relative: Path,
    state_directory: Path,
) -> dict[str, object]:
    expected = {
        "config": (project_root / DEFAULT_CONFIG).resolve(),
        "manifest": (project_root / DEFAULT_MANIFEST).resolve(),
        "runner": (project_root / DEFAULT_RUNNER).resolve(),
    }
    observed = {
        "config": (project_root / config_relative).resolve(),
        "manifest": (project_root / manifest_relative).resolve(),
        "runner": (project_root / runner_relative).resolve(),
    }
    if observed != expected:
        raise ValueError("formal launch paths differ from frozen registration")
    execution = _execution_registration(project_root, config_relative)
    registered_state = Path(
        str(execution.get("supervisor_state_directory", ""))
    ).resolve()
    if registered_state != state_directory.resolve():
        raise ValueError("supervisor state directory differs from frozen registration")
    config_payload = _read_object(project_root / config_relative)
    _sealed_execution_input_contract(config_payload)
    return execution


def _require_exact_go_payload(
    project_root: Path,
    state_directory: Path,
    config_relative: Path,
    manifest_relative: Path,
    runner_relative: Path,
    launch: dict[str, object],
    go_payload: dict[str, object],
    *,
    head_policy: str,
    require_live_detachment: bool,
) -> None:
    """Fail closed unless a GO token binds the exact live/frozen launch state."""

    if set(go_payload) != set(GO_PAYLOAD_KEYS):
        raise ValueError("GO key set differs")
    if type(go_payload.get("schema_version")) is not int or go_payload.get(
        "schema_version"
    ) != 1:
        raise ValueError("GO schema version differs")
    if go_payload.get("protocol_revision") != PROTOCOL_REVISION:
        raise ValueError("GO protocol revision differs")
    if go_payload.get("status") != "go":
        raise ValueError("GO status differs")
    created_utc = go_payload.get("created_utc")
    if not isinstance(created_utc, str) or not _UTC.fullmatch(created_utc):
        raise ValueError("GO UTC timestamp differs")
    try:
        parsed_utc = datetime.fromisoformat(created_utc[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError("GO UTC timestamp is invalid") from error
    if parsed_utc.utcoffset() is None or parsed_utc.utcoffset().total_seconds() != 0:
        raise ValueError("GO timestamp is not UTC")

    if (
        launch.get("protocol_revision") != PROTOCOL_REVISION
        or launch.get("status") != "waiting_for_detachment_attestation"
    ):
        raise ValueError("GO launch protocol/status differs")
    launch_path = state_directory / "launch.json"
    if go_payload.get("launch_sha256") != _sha256(launch_path):
        raise ValueError("GO launch hash differs")
    launch_process = launch.get("process")
    if not isinstance(launch_process, dict):
        raise ValueError("GO launch process identity is missing")
    pid = launch_process.get("pid")
    start_time_ticks = launch_process.get("start_time_ticks")
    if (
        type(pid) is not int
        or pid <= 0
        or type(start_time_ticks) is not int
        or start_time_ticks <= 0
        or go_payload.get("supervisor_pid") != pid
        or type(go_payload.get("supervisor_pid")) is not int
        or go_payload.get("supervisor_start_time_ticks") != start_time_ticks
        or type(go_payload.get("supervisor_start_time_ticks")) is not int
    ):
        raise ValueError("GO supervisor identity differs")
    go_boot_id = go_payload.get("boot_id")
    if (
        not isinstance(go_boot_id, str)
        or not _BOOT_ID.fullmatch(go_boot_id)
        or go_boot_id != launch.get("boot_id")
    ):
        raise ValueError("GO boot ID differs from launch")
    if require_live_detachment:
        if go_boot_id != _boot_id():
            raise ValueError("GO boot ID differs from current boot")
        current_process = _proc_record(pid)
        _require_detached_process_record(
            current_process, launch_process, pid, state_directory
        )

    registered = launch.get("registered_files")
    if not isinstance(registered, dict):
        raise ValueError("GO launch registered-file table is missing")
    current_registered = _registered_files(
        project_root, config_relative, manifest_relative, runner_relative
    )
    if registered != current_registered:
        raise ValueError("GO launch registered files differ from current files")
    if not isinstance(go_payload.get("registered_files"), dict) or go_payload.get(
        "registered_files"
    ) != registered:
        raise ValueError("GO registered files differ from launch")
    sealed_record = _require_exact_sealed_execution_inputs(
        project_root,
        config_relative,
        manifest_relative,
        runner_relative,
        registered,
        launch.get("sealed_execution_inputs"),
    )
    if go_payload.get(
        "sealed_execution_inputs_sha256"
    ) != _sealed_execution_inputs_sha256(sealed_record):
        raise ValueError("GO sealed-execution-input record hash differs")
    if require_live_detachment:
        _require_live_sealed_execution_inputs(pid, sealed_record)

    execution = _execution_registration(project_root, config_relative)
    attestation_registered = execution.get("detachment_attestation_path")
    if (
        not isinstance(attestation_registered, str)
        or not attestation_registered
        or Path(attestation_registered).is_absolute()
        or ".." in Path(attestation_registered).parts
    ):
        raise ValueError("GO frozen attestation path is invalid")
    if go_payload.get("attestation_path") != attestation_registered:
        raise ValueError("GO attestation path differs from frozen registration")
    attestation_path = project_root / attestation_registered
    if go_payload.get("attestation_sha256") != _sha256(attestation_path):
        raise ValueError("GO detachment attestation hash differs")

    git_head = go_payload.get("git_head")
    if not isinstance(git_head, str) or not _HEX40.fullmatch(git_head):
        raise ValueError("GO Git HEAD is invalid")
    introduction_commit = _attestation_introduction_commit(
        project_root, Path(attestation_registered)
    )
    if git_head != introduction_commit:
        raise ValueError(
            "GO Git HEAD differs from detachment-attestation introduction commit"
        )
    current_head = _git(project_root, "rev-parse", "HEAD")
    if head_policy == "current":
        if git_head != current_head:
            raise ValueError("GO Git HEAD differs from current HEAD")
    elif head_policy == "ancestor":
        if subprocess.run(
            [
                "git",
                "-C",
                str(project_root),
                "merge-base",
                "--is-ancestor",
                git_head,
                current_head,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode != 0:
            raise ValueError("GO Git HEAD is not an ancestor of current HEAD")
    else:
        raise ValueError("unknown GO Git HEAD policy")


def _validate_go_before_runner(
    project_root: Path,
    state_directory: Path,
    config_relative: Path,
    manifest_relative: Path,
    runner_relative: Path,
    launch: dict[str, object],
    go_path: Path,
) -> tuple[dict[str, object], str]:
    validated_raw, go_payload, validated_sha256 = (
        _read_stable_object_with_sha256(go_path)
    )
    _require_exact_go_payload(
        project_root,
        state_directory,
        config_relative,
        manifest_relative,
        runner_relative,
        launch,
        go_payload,
        head_policy="current",
        require_live_detachment=True,
    )
    _require_clean(project_root)
    _run_validator(
        project_root,
        config_relative,
        manifest_relative,
        "--require-committed",
        "--check-detachment-attestation",
    )
    _require_clean(project_root)
    _require_exact_go_payload(
        project_root,
        state_directory,
        config_relative,
        manifest_relative,
        runner_relative,
        launch,
        go_payload,
        head_policy="current",
        require_live_detachment=True,
    )
    _require_stable_go_path_unchanged(go_path, validated_raw, validated_sha256)
    return go_payload, validated_sha256


def _runner_command(
    bash_tool: Path,
    project_root: Path,
    manifest_relative: Path,
    config_relative: Path,
) -> list[str]:
    if not bash_tool.is_absolute():
        raise ValueError("registered runner Bash path is not absolute")
    return [
        str(bash_tool),
        SEALED_EXECUTION_INPUT_PROC_PATHS["runner"],
        str(project_root),
        str(project_root / manifest_relative),
        str(project_root / config_relative),
        SEALED_EXECUTION_INPUT_PROC_PATHS["manifest"],
        SEALED_EXECUTION_INPUT_PROC_PATHS["config"],
    ]


def _runner_environment(
    frozen_environment: dict[str, str],
    state_directory: Path,
    supervisor_pid: int,
    supervisor_start_time_ticks: object,
    boot_id: str,
    launch_sha256: str,
    go_sha256: str,
) -> dict[str, str]:
    bindings = {
        "M_OFDFT_G1_R4_SUPERVISOR_STATE_DIRECTORY": str(state_directory),
        "M_OFDFT_G1_R4_SUPERVISOR_PID": str(supervisor_pid),
        "M_OFDFT_G1_R4_SUPERVISOR_START_TIME_TICKS": str(
            supervisor_start_time_ticks
        ),
        "M_OFDFT_G1_R4_BOOT_ID": boot_id,
        "M_OFDFT_G1_R4_LAUNCH_SHA256": launch_sha256,
        "M_OFDFT_G1_R4_GO_SHA256": go_sha256,
    }
    if set(frozen_environment) != set(FROZEN_AMBIENT_ENVIRONMENT_KEYS):
        raise ValueError("runner frozen ambient environment key set differs")
    if set(bindings) != set(RUNNER_BINDING_ENVIRONMENT_KEYS):
        raise ValueError("runner supervisor-binding environment key set differs")
    return {**frozen_environment, **bindings}


def _supervise(arguments: argparse.Namespace) -> NoReturn:
    candidate_root = arguments.project_root.resolve()
    config_payload, frozen_environment, _, bash_tool = (
        _require_mutating_process_context(candidate_root, arguments.config)
    )
    project_root = _project_root(candidate_root)
    state_directory = arguments.state_directory.resolve()
    _require_registered_launch_paths(
        project_root,
        arguments.config,
        arguments.manifest,
        arguments.runner,
        state_directory,
    )
    lock_descriptor = os.open(state_directory / "supervisor.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise SystemExit("another R4 supervisor owns the state lock") from error

    def receive_hup(_signum: int, _frame: object) -> None:
        _journal_append(state_directory, "sighup_received")

    signal.signal(signal.SIGHUP, receive_hup)
    files, sealed_execution_inputs, sealed_input_fds = _freeze_registered_inputs(
        project_root, arguments.config, arguments.manifest, arguments.runner
    )
    process = _proc_record(os.getpid())
    runner_command = _runner_command(
        bash_tool,
        project_root,
        arguments.manifest,
        arguments.config,
    )
    launch = {
        "schema_version": 1,
        "protocol_revision": PROTOCOL_REVISION,
        "status": "waiting_for_detachment_attestation",
        "launch_method": "python_subprocess_start_new_session",
        "restart_policy": "never",
        "project_root": str(project_root),
        "hostname": os.uname().nodename,
        "working_directory": str(Path.cwd().resolve()),
        "umask": _umask(),
        "environment": _environment_registration(config_payload),
        "state_directory": str(state_directory),
        "lock_path": str(state_directory / "supervisor.lock"),
        "log_path": str(state_directory / "supervisor.log"),
        "boot_id": _boot_id(),
        "process": process,
        "git_head_at_launch": _git(project_root, "rev-parse", "HEAD"),
        "registered_files": files,
        "sealed_execution_inputs": sealed_execution_inputs,
        "launcher": {
            "path": str(Path(__file__).resolve()),
            "sha256": _sha256(Path(__file__).resolve()),
            "python_path": sys.executable,
            "python_realpath": str(Path(sys.executable).resolve()),
            "python_sha256": _sha256(Path(sys.executable).resolve()),
        },
        "runner_argv": runner_command,
        "started_utc": _utc(),
    }
    _write_exclusive(state_directory / "launch.json", launch)
    _journal_append(state_directory, "waiting_for_go")

    go_path = state_directory / "go.json"
    last_heartbeat = time.monotonic()
    while not go_path.exists():
        time.sleep(0.25)
        if time.monotonic() - last_heartbeat >= 30.0:
            _journal_append(state_directory, "waiting_heartbeat")
            last_heartbeat = time.monotonic()
    try:
        validated_go, validated_go_sha256 = _validate_go_before_runner(
            project_root,
            state_directory,
            arguments.config,
            arguments.manifest,
            arguments.runner,
            launch,
            go_path,
        )
    except Exception as error:
        _close_descriptors(sealed_input_fds)
        sealed_input_fds = ()
        _journal_append(state_directory, "go_rejected", reason=str(error))
        _write_exclusive(
            state_directory / "terminal.json",
            {
                "schema_version": 1,
                "protocol_revision": PROTOCOL_REVISION,
                "status": "go_rejected",
                "reason": str(error),
                "finished_utc": _utc(),
            },
        )
        raise SystemExit(98)

    child: subprocess.Popen[bytes] | None = None
    child_identity: dict[str, object] | None = None
    try:
        _journal_append(
            state_directory,
            "go_accepted",
            git_head=validated_go["git_head"],
            go_sha256=validated_go_sha256,
        )
        child = subprocess.Popen(
            runner_command,
            cwd=project_root,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            pass_fds=sealed_input_fds,
            env=_runner_environment(
                frozen_environment,
                state_directory,
                os.getpid(),
                process["start_time_ticks"],
                _boot_id(),
                _sha256(state_directory / "launch.json"),
                validated_go_sha256,
            ),
        )
        child_identity = _proc_record(child.pid)
        _close_descriptors(sealed_input_fds)
        sealed_input_fds = ()
        _journal_append(
            state_directory,
            "runner_started",
            child_pid=child.pid,
            child_start_time_ticks=child_identity["start_time_ticks"],
        )
        return_code = child.wait()
        _journal_append(state_directory, "runner_finished", return_code=return_code)
        analysis_summary = project_root / DEFAULT_ANALYSIS_SUMMARY
        analysis_summary_hash = (
            _sha256(analysis_summary)
            if analysis_summary.is_file() and not analysis_summary.is_symlink()
            else None
        )
        _write_exclusive(
            state_directory / "terminal.json",
            {
                "schema_version": 1,
                "protocol_revision": PROTOCOL_REVISION,
                "status": "accepted" if return_code == 0 else "stopped",
                "runner_return_code": return_code,
                "runner_pid": child.pid,
                "runner_start_time_ticks": child_identity["start_time_ticks"],
                "launch_sha256": _sha256(state_directory / "launch.json"),
                "go_sha256": validated_go_sha256,
                "journal_sha256": _sha256(state_directory / "journal.jsonl"),
                "git_head_after_runner": _git(project_root, "rev-parse", "HEAD"),
                "analysis_summary_path": DEFAULT_ANALYSIS_SUMMARY.as_posix(),
                "analysis_summary_sha256": analysis_summary_hash,
                "log_sha256": _sha256(state_directory / "supervisor.log"),
                "finished_utc": _utc(),
            },
        )
    except Exception as error:
        _close_descriptors(sealed_input_fds)
        sealed_input_fds = ()
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        try:
            _journal_append(
                state_directory,
                "supervisor_error",
                reason=f"{type(error).__name__}: {error}",
            )
        except Exception:
            pass
        terminal_path = state_directory / "terminal.json"
        if not terminal_path.exists():
            _write_exclusive(
                terminal_path,
                {
                    "schema_version": 1,
                    "protocol_revision": PROTOCOL_REVISION,
                    "status": "supervisor_error",
                    "reason": f"{type(error).__name__}: {error}",
                    "runner_pid": child.pid if child is not None else None,
                    "runner_start_time_ticks": (
                        child_identity.get("start_time_ticks")
                        if child_identity is not None
                        else None
                    ),
                    "runner_return_code": child.poll() if child is not None else None,
                    "finished_utc": _utc(),
                },
            )
        raise
    raise SystemExit(return_code)


def start(arguments: argparse.Namespace) -> int:
    candidate_root = arguments.project_root.resolve()
    _, frozen_environment, python_tool, _ = _require_mutating_process_context(
        candidate_root, arguments.config
    )
    project_root = _project_root(candidate_root)
    _require_clean(project_root)
    _validate_registration(project_root, arguments.config, arguments.manifest)
    state_directory = arguments.state_directory.resolve()
    _require_registered_launch_paths(
        project_root,
        arguments.config,
        arguments.manifest,
        arguments.runner,
        state_directory,
    )
    if state_directory == project_root or project_root in state_directory.parents:
        raise ValueError("supervisor state directory must be outside the repository")
    try:
        state_directory.mkdir(parents=True, mode=0o700)
    except FileExistsError as error:
        raise ValueError("single-use supervisor state directory already exists") from error
    log_path = state_directory / "supervisor.log"
    log_handle = log_path.open("xb")
    command = [
        str(python_tool),
        "-s",
        str(Path(__file__).resolve()),
        "_supervise",
        "--project-root",
        str(project_root),
        "--state-directory",
        str(state_directory),
        "--config",
        str(arguments.config),
        "--manifest",
        str(arguments.manifest),
        "--runner",
        str(arguments.runner),
    ]
    child = subprocess.Popen(
        command,
        cwd=project_root,
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
        env=frozen_environment,
    )
    log_handle.close()
    launch_path = state_directory / "launch.json"
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if launch_path.is_file():
            launch = _read_object(launch_path)
            print(_canonical({"status": "started_waiting_for_attestation", "launch": launch}).decode(), end="")
            return 0
        if child.poll() is not None:
            diagnostic = log_path.read_text(encoding="utf-8", errors="replace")
            raise RuntimeError(
                f"detached supervisor exited early with {child.returncode}: {diagnostic}"
            )
        time.sleep(0.1)
    raise TimeoutError("detached supervisor did not publish launch evidence")


def verify(arguments: argparse.Namespace) -> int:
    candidate_root = arguments.project_root.resolve()
    config_payload, _, _, _ = _require_mutating_process_context(
        candidate_root, arguments.config
    )
    project_root = _project_root(candidate_root)
    _require_clean(project_root)
    state_directory = arguments.state_directory.resolve()
    execution = _require_registered_launch_paths(
        project_root,
        arguments.config,
        DEFAULT_MANIFEST,
        DEFAULT_RUNNER,
        state_directory,
    )
    launch_path = state_directory / "launch.json"
    launch = _read_object(launch_path)
    if launch.get("protocol_revision") != PROTOCOL_REVISION:
        raise ValueError("launch protocol revision differs")
    if launch.get("environment") != _environment_registration(config_payload):
        raise ValueError("launch frozen ambient-environment record differs")
    if launch.get("boot_id") != _boot_id():
        raise ValueError("supervisor belongs to another boot")
    registered = launch.get("registered_files")
    if not isinstance(registered, dict):
        raise ValueError("launch registered-file table is missing")
    process = launch.get("process")
    if not isinstance(process, dict):
        raise ValueError("launch process identity is missing")
    if not _positive_integer(process.get("pid")):
        raise ValueError("launch supervisor PID is invalid")
    pid = process["pid"]
    before = _proc_record(pid)
    _require_detached_process_record(before, process, pid, state_directory)
    hup_before = sum(row.get("event") == "sighup_received" for row in _journal_events(state_directory))
    os.kill(pid, signal.SIGHUP)
    deadline = time.monotonic() + 5.0
    hup_after = hup_before
    while time.monotonic() < deadline:
        hup_after = sum(row.get("event") == "sighup_received" for row in _journal_events(state_directory))
        if hup_after > hup_before:
            break
        time.sleep(0.05)
    after = _proc_record(pid)
    if hup_after != hup_before + 1:
        raise ValueError("supervisor did not attest exactly one HUP probe")
    _require_detached_process_record(after, process, pid, state_directory)
    stable_fields = {
        "pid",
        "process_group_id",
        "session_id",
        "tty_nr",
        "start_time_ticks",
        "stdin",
        "stdout",
        "stderr",
    }
    if any(before.get(field) != after.get(field) for field in stable_fields):
        raise ValueError("supervisor stable process fields changed during HUP probe")
    current_files = _registered_files(
        project_root,
        Path(str(registered["config_path"])),
        Path(str(registered["manifest_path"])),
        Path(str(registered["runner_path"])),
    )
    if current_files != registered:
        raise ValueError("registered files changed before detachment attestation")
    sealed_record = _require_exact_sealed_execution_inputs(
        project_root,
        arguments.config,
        DEFAULT_MANIFEST,
        DEFAULT_RUNNER,
        registered,
        launch.get("sealed_execution_inputs"),
    )
    _require_live_sealed_execution_inputs(pid, sealed_record)
    output = (project_root / arguments.output).resolve()
    try:
        output.relative_to(project_root)
    except ValueError as error:
        raise ValueError("attestation output must be inside the repository") from error
    registered_output = (
        project_root / str(execution.get("detachment_attestation_path", ""))
    ).resolve()
    if output != registered_output:
        raise ValueError("attestation output differs from frozen registration")
    payload = {
        "schema_version": 1,
        "protocol_revision": PROTOCOL_REVISION,
        "status": "accepted",
        "launch_path": str(launch_path),
        "launch_sha256": _sha256(launch_path),
        "boot_id": _boot_id(),
        "supervisor_process_before_hup": before,
        "supervisor_process_after_hup": after,
        "hup_event_count_before": hup_before,
        "hup_event_count_after": hup_after,
        "registered_files": registered,
        "git_head": _git(project_root, "rev-parse", "HEAD"),
        "attested_utc": _utc(),
    }
    _write_exclusive(output, payload)
    print(_canonical({"status": "detachment_accepted", "attestation": str(output)}).decode(), end="")
    return 0


def go(arguments: argparse.Namespace) -> int:
    candidate_root = arguments.project_root.resolve()
    config_payload, _, _, _ = _require_mutating_process_context(
        candidate_root, arguments.config
    )
    project_root = _project_root(candidate_root)
    _require_clean(project_root)
    state_directory = arguments.state_directory.resolve()
    execution = _require_registered_launch_paths(
        project_root,
        arguments.config,
        DEFAULT_MANIFEST,
        DEFAULT_RUNNER,
        state_directory,
    )
    _run_validator(
        project_root,
        arguments.config,
        DEFAULT_MANIFEST,
        "--require-committed",
        "--check-detachment-attestation",
    )
    launch_path = state_directory / "launch.json"
    launch = _read_object(launch_path)
    if launch.get("environment") != _environment_registration(config_payload):
        raise ValueError("GO launch frozen ambient-environment record differs")
    process = launch.get("process")
    if not isinstance(process, dict):
        raise ValueError("launch process identity is missing")
    pid = int(process["pid"])
    current = _proc_record(pid)
    _require_detached_process_record(current, process, pid, state_directory)
    attestation_relative = arguments.attestation
    attestation = project_root / attestation_relative
    if attestation.resolve() != (
        project_root / str(execution.get("detachment_attestation_path", ""))
    ).resolve():
        raise ValueError("GO attestation path differs from frozen registration")
    evidence = _read_object(attestation)
    if evidence.get("status") != "accepted":
        raise ValueError("detachment attestation is not accepted")
    if evidence.get("launch_sha256") != _sha256(launch_path):
        raise ValueError("detachment attestation launch hash differs")
    if evidence.get("boot_id") != _boot_id():
        raise ValueError("detachment attestation boot ID differs")
    subprocess.check_call(
        ["git", "-C", str(project_root), "ls-files", "--error-unmatch", "--", str(attestation_relative)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    registered = launch["registered_files"]
    assert isinstance(registered, dict)
    sealed_record = _require_exact_sealed_execution_inputs(
        project_root,
        arguments.config,
        DEFAULT_MANIFEST,
        DEFAULT_RUNNER,
        registered,
        launch.get("sealed_execution_inputs"),
    )
    _require_live_sealed_execution_inputs(pid, sealed_record)
    introduction_commit = _attestation_introduction_commit(
        project_root, attestation_relative
    )
    payload = {
        "schema_version": 1,
        "protocol_revision": PROTOCOL_REVISION,
        "status": "go",
        "launch_sha256": _sha256(launch_path),
        "boot_id": _boot_id(),
        "supervisor_pid": pid,
        "supervisor_start_time_ticks": current["start_time_ticks"],
        "attestation_path": attestation_relative.as_posix(),
        "attestation_sha256": _sha256(attestation),
        "git_head": introduction_commit,
        "registered_files": registered,
        "sealed_execution_inputs_sha256": _sealed_execution_inputs_sha256(
            sealed_record
        ),
        "created_utc": _utc(),
    }
    _require_exact_go_payload(
        project_root,
        state_directory,
        arguments.config,
        DEFAULT_MANIFEST,
        DEFAULT_RUNNER,
        launch,
        payload,
        head_policy="current",
        require_live_detachment=True,
    )
    _write_exclusive(state_directory / "go.json", payload)
    print(_canonical({"status": "go_created", "go": payload}).decode(), end="")
    return 0


def finalize(arguments: argparse.Namespace) -> int:
    """Import the immutable terminal supervisor receipt into Git scope."""

    candidate_root = arguments.project_root.resolve()
    config_at_entry, _, _, _ = _require_mutating_process_context(
        candidate_root, arguments.config
    )
    project_root = _project_root(candidate_root)
    _require_clean(project_root)
    state_directory = arguments.state_directory.resolve()
    _require_registered_launch_paths(
        project_root,
        arguments.config,
        arguments.manifest,
        DEFAULT_RUNNER,
        state_directory,
    )
    _run_validator(
        project_root,
        arguments.config,
        arguments.manifest,
        "--require-committed",
        "--require-all-runs",
        "--check-analysis-summary",
        "--check-detachment-attestation-record",
    )
    _require_clean(project_root)
    launch_path = state_directory / "launch.json"
    go_path = state_directory / "go.json"
    terminal_path = state_directory / "terminal.json"
    log_path = state_directory / "supervisor.log"
    journal_path = state_directory / "journal.jsonl"
    launch_raw, launch, launch_sha256 = _read_stable_object_with_sha256(
        launch_path
    )
    go_raw, go_payload, go_sha256 = _read_stable_object_with_sha256(go_path)
    terminal_raw, terminal, terminal_sha256 = _read_stable_object_with_sha256(
        terminal_path
    )
    _require_exact_go_payload(
        project_root,
        state_directory,
        arguments.config,
        arguments.manifest,
        DEFAULT_RUNNER,
        launch,
        go_payload,
        head_policy="ancestor",
        require_live_detachment=False,
    )
    events, journal_raw, journal_sha256 = _require_accepted_terminal_and_journal(
        state_directory, launch, go_payload, terminal
    )
    launch_process = launch.get("process")
    if not isinstance(launch_process, dict):
        raise ValueError("completion launch process identity is missing")
    if launch.get("protocol_revision") != PROTOCOL_REVISION:
        raise ValueError("completion launch protocol differs")
    if launch.get("environment") != _environment_registration(config_at_entry):
        raise ValueError("completion launch frozen ambient-environment record differs")
    if terminal.get("protocol_revision") != PROTOCOL_REVISION:
        raise ValueError("completion terminal protocol differs")
    if Path(str(launch.get("state_directory", ""))).resolve() != state_directory:
        raise ValueError("completion launch state directory differs")
    if (
        terminal.get("status") != "accepted"
        or type(terminal.get("runner_return_code")) is not int
        or terminal.get("runner_return_code") != 0
    ):
        raise ValueError("supervisor terminal record is not accepted")
    if not _positive_integer(terminal.get("runner_pid")) or not _positive_integer(
        terminal.get("runner_start_time_ticks")
    ):
        raise ValueError("supervisor terminal runner identity is invalid")
    if not _positive_integer(launch_process.get("pid")) or not _positive_integer(
        launch_process.get("start_time_ticks")
    ):
        raise ValueError("completion launch process identity is invalid")
    if terminal.get("launch_sha256") != launch_sha256:
        raise ValueError("completion terminal launch hash differs")
    if terminal.get("go_sha256") != go_sha256:
        raise ValueError("completion terminal GO hash differs")
    if terminal.get("journal_sha256") != journal_sha256:
        raise ValueError("completion terminal journal hash differs")
    log_raw, log_sha256 = _read_stable_regular_bytes_with_sha256(log_path)
    if terminal.get("log_sha256") != log_sha256:
        raise ValueError("completion terminal log hash differs")
    analysis_relative = Path(str(terminal.get("analysis_summary_path", "")))
    if analysis_relative != DEFAULT_ANALYSIS_SUMMARY:
        raise ValueError("completion analysis summary path differs")
    analysis_path = project_root / analysis_relative
    analysis_raw, analysis, analysis_sha256 = _read_stable_object_with_sha256(
        analysis_path
    )
    if terminal.get("analysis_summary_sha256") != analysis_sha256:
        raise ValueError("completion analysis summary hash differs")
    if (
        analysis.get("protocol_revision") != PROTOCOL_REVISION
        or analysis.get("audit_status") != "accepted"
        or analysis.get("overall_protocol_status") != "pending_supervisor_completion"
        or analysis.get("g1_status") != "pending (1/6)"
        or analysis.get("authorized_scope") != "no_G1_advancement"
    ):
        raise ValueError("completion analysis is not an accepted R4 summary")
    git_head = _git(project_root, "rev-parse", "HEAD")
    if terminal.get("git_head_after_runner") != git_head:
        raise ValueError("completion Git HEAD changed after the runner")
    started = [event for event in events if event.get("event") == "runner_started"]
    finished = [event for event in events if event.get("event") == "runner_finished"]
    if len(started) != 1 or len(finished) != 1:
        raise ValueError("completion journal runner event count differs")
    if (
        type(finished[0].get("return_code")) is not int
        or finished[0].get("return_code") != 0
        or not _positive_integer(started[0].get("child_pid"))
        or not _positive_integer(started[0].get("child_start_time_ticks"))
        or started[0].get("child_pid") != terminal.get("runner_pid")
        or started[0].get("child_start_time_ticks")
        != terminal.get("runner_start_time_ticks")
    ):
        raise ValueError("completion journal/terminal runner identity differs")
    output = (project_root / arguments.output).resolve()
    try:
        output.relative_to(project_root)
    except ValueError as error:
        raise ValueError("completion output must be inside the repository") from error
    config_path = (project_root / arguments.config).resolve()
    manifest_path = (project_root / arguments.manifest).resolve()
    try:
        config_relative = config_path.relative_to(project_root)
        manifest_relative = manifest_path.relative_to(project_root)
    except ValueError as error:
        raise ValueError("completion config and manifest must be inside the repository") from error
    config_raw, config_payload, config_sha256 = _read_stable_object_with_sha256(
        config_path
    )
    manifest_raw, manifest_sha256 = _read_stable_regular_bytes_with_sha256(
        manifest_path
    )
    execution = config_payload.get("execution")
    if not isinstance(execution, dict):
        raise ValueError("completion execution registration is missing")
    registered_state = Path(str(execution.get("supervisor_state_directory", ""))).resolve()
    registered_output = (
        project_root / str(execution.get("supervisor_completion_path", ""))
    ).resolve()
    if registered_state != state_directory:
        raise ValueError("completion supervisor state directory differs from registration")
    if registered_output != output:
        raise ValueError("completion output path differs from registration")
    if launch.get("registered_files") != _registered_files(
        project_root, arguments.config, arguments.manifest, DEFAULT_RUNNER
    ):
        raise ValueError("completion registered files differ from launch")
    manifest_registration = config_payload.get("manifest")
    if (
        not isinstance(manifest_registration, dict)
        or manifest_registration.get("path") != manifest_relative.as_posix()
        or manifest_registration.get("sha256") != manifest_sha256
    ):
        raise ValueError("completion manifest registration differs")
    payload = {
        "schema_version": 1,
        "protocol_revision": PROTOCOL_REVISION,
        "status": "supervisor_completed",
        "created_utc": _utc(),
        "config_path": config_relative.as_posix(),
        "config_sha256": config_sha256,
        "manifest_path": manifest_relative.as_posix(),
        "manifest_sha256": manifest_sha256,
        "git_head_before_completion": git_head,
        "supervisor_state_directory": str(state_directory),
        "supervisor_launch_path": str(launch_path),
        "supervisor_launch_sha256": launch_sha256,
        "supervisor_terminal_path": str(terminal_path),
        "supervisor_terminal_sha256": terminal_sha256,
        "supervisor_journal_path": str(journal_path),
        "supervisor_journal_sha256": journal_sha256,
        "supervisor_pid": launch_process["pid"],
        "supervisor_start_time_ticks": launch_process["start_time_ticks"],
        "boot_id": str(launch["boot_id"]),
        "runner_exit_code": terminal["runner_return_code"],
        "analysis_path": analysis_relative.as_posix(),
        "analysis_sha256": str(terminal["analysis_summary_sha256"]),
        "analysis_audit_status": str(analysis["audit_status"]),
        "final_acceptance_policy": "committed_completion_then_validator_revalidation",
    }
    frozen_inputs = (
        ("launch", launch_path, launch_raw),
        ("GO", go_path, go_raw),
        ("terminal", terminal_path, terminal_raw),
        ("journal", journal_path, journal_raw),
        ("log", log_path, log_raw),
        ("analysis", analysis_path, analysis_raw),
        ("config", config_path, config_raw),
        ("manifest", manifest_path, manifest_raw),
    )
    for label, path, expected_raw in frozen_inputs:
        observed_raw, _ = _read_stable_regular_bytes_with_sha256(path)
        if observed_raw != expected_raw:
            raise ValueError(
                f"completion {label} changed after stable validation"
            )
    _require_clean(project_root)
    if _git(project_root, "rev-parse", "HEAD") != git_head:
        raise ValueError("completion Git HEAD changed after stable validation")
    _write_exclusive(output, payload)
    print(
        _canonical(
            {"status": "supervisor_completion_accepted", "completion": str(output)}
        ).decode(),
        end="",
    )
    return 0


def status(arguments: argparse.Namespace) -> int:
    state_directory = arguments.state_directory.resolve()
    payload: dict[str, object] = {"state_directory": str(state_directory)}
    for name in ("launch.json", "go.json", "terminal.json"):
        path = state_directory / name
        payload[name] = _read_object(path) if path.is_file() else None
    launch = payload.get("launch.json")
    if isinstance(launch, dict) and isinstance(launch.get("process"), dict):
        try:
            observed = _proc_record(int(launch["process"]["pid"]))  # type: ignore[index]
            payload["supervisor_live"] = (
                observed["start_time_ticks"] == launch["process"]["start_time_ticks"]  # type: ignore[index]
            )
            payload["observed_process"] = observed
        except (FileNotFoundError, ProcessLookupError):
            payload["supervisor_live"] = False
    payload["journal"] = _journal_events(state_directory)
    print(_canonical(payload).decode(), end="")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("start", "_supervise"):
        child = subparsers.add_parser(name)
        child.add_argument("--project-root", type=Path, required=True)
        child.add_argument("--state-directory", type=Path, default=DEFAULT_STATE_DIRECTORY)
        child.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
        child.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
        child.add_argument("--runner", type=Path, default=DEFAULT_RUNNER)
        child.set_defaults(function=start if name == "start" else _supervise)
    child = subparsers.add_parser("verify")
    child.add_argument("--project-root", type=Path, required=True)
    child.add_argument("--state-directory", type=Path, default=DEFAULT_STATE_DIRECTORY)
    child.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    child.add_argument("--output", type=Path, default=DEFAULT_ATTESTATION)
    child.set_defaults(function=verify)
    child = subparsers.add_parser("go")
    child.add_argument("--project-root", type=Path, required=True)
    child.add_argument("--state-directory", type=Path, default=DEFAULT_STATE_DIRECTORY)
    child.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    child.add_argument("--attestation", type=Path, default=DEFAULT_ATTESTATION)
    child.set_defaults(function=go)
    child = subparsers.add_parser("status")
    child.add_argument("--state-directory", type=Path, default=DEFAULT_STATE_DIRECTORY)
    child.set_defaults(function=status)
    child = subparsers.add_parser("finalize")
    child.add_argument("--project-root", type=Path, required=True)
    child.add_argument("--state-directory", type=Path, default=DEFAULT_STATE_DIRECTORY)
    child.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    child.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    child.add_argument("--output", type=Path, default=DEFAULT_COMPLETION)
    child.set_defaults(function=finalize)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    result = arguments.function(arguments)
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())
