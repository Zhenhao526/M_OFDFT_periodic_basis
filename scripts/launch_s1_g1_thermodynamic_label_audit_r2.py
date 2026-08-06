#!/usr/bin/env python3
"""Launch and attest the detached G1 thermodynamic-label R2 supervisor.

The supervisor is intentionally outside the SSH/PTY process group.  It waits
for a separately committed detachment attestation before it creates a GO token
and invokes the formal runner.  A state directory is single-use: this launcher
never restarts a stopped or failed scientific attempt.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn


DEFAULT_STATE_DIRECTORY = Path(
    "/home/shenwei01/.local/state/m_ofdft/g1_thermodynamic_label_audit_r2_20260806"
)
DEFAULT_CONFIG = Path("config/S1_g1_thermodynamic_label_audit_r2.json")
DEFAULT_MANIFEST = Path("config/S1_g1_thermodynamic_label_audit_r2_manifest.tsv")
DEFAULT_RUNNER = Path("scripts/run_s1_g1_thermodynamic_label_audit_r2.sh")
DEFAULT_ATTESTATION = Path(
    "orchestration/s1/g1_thermodynamic_label_audit_r2_20260806/detachment.json"
)
DEFAULT_COMPLETION = Path(
    "orchestration/s1/g1_thermodynamic_label_audit_r2_20260806/supervisor_completion.json"
)
DEFAULT_ANALYSIS_SUMMARY = Path(
    "analysis/s1/g1_thermodynamic_label_audit_r2_20260806/summary.json"
)
PROTOCOL_REVISION = "S1-G1-THERMODYNAMIC-LABEL-AUDIT-R2"
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
    "M_OFDFT_G1_R2_SUPERVISOR_STATE_DIRECTORY",
    "M_OFDFT_G1_R2_SUPERVISOR_PID",
    "M_OFDFT_G1_R2_SUPERVISOR_START_TIME_TICKS",
    "M_OFDFT_G1_R2_BOOT_ID",
    "M_OFDFT_G1_R2_LAUNCH_SHA256",
    "M_OFDFT_G1_R2_GO_SHA256",
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


def _require_detached_process_record(
    observed: dict[str, object],
    registered: dict[str, object],
    pid: int,
    state_directory: Path,
) -> None:
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


def _registered_files(
    project_root: Path, config_relative: Path, manifest_relative: Path, runner_relative: Path
) -> dict[str, object]:
    config = project_root / config_relative
    manifest = project_root / manifest_relative
    runner = project_root / runner_relative
    parsed = _read_object(config)
    if parsed.get("protocol_revision") != PROTOCOL_REVISION:
        raise ValueError("configuration protocol revision differs")
    if parsed.get("status") != "preregistered":
        raise ValueError("configuration is not preregistered")
    return {
        "config_path": config_relative.as_posix(),
        "config_sha256": _sha256(config),
        "manifest_path": manifest_relative.as_posix(),
        "manifest_sha256": _sha256(manifest),
        "runner_path": runner_relative.as_posix(),
        "runner_sha256": _sha256(runner),
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
    validator = project_root / "scripts/validate_s1_g1_thermodynamic_label_audit_r2.py"
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
    return execution


def _runner_command(
    bash_tool: Path,
    project_root: Path,
    runner_relative: Path,
    manifest_relative: Path,
    config_relative: Path,
) -> list[str]:
    if not bash_tool.is_absolute():
        raise ValueError("registered runner Bash path is not absolute")
    return [
        str(bash_tool),
        str(project_root / runner_relative),
        str(project_root / manifest_relative),
        str(project_root / config_relative),
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
        "M_OFDFT_G1_R2_SUPERVISOR_STATE_DIRECTORY": str(state_directory),
        "M_OFDFT_G1_R2_SUPERVISOR_PID": str(supervisor_pid),
        "M_OFDFT_G1_R2_SUPERVISOR_START_TIME_TICKS": str(
            supervisor_start_time_ticks
        ),
        "M_OFDFT_G1_R2_BOOT_ID": boot_id,
        "M_OFDFT_G1_R2_LAUNCH_SHA256": launch_sha256,
        "M_OFDFT_G1_R2_GO_SHA256": go_sha256,
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
        raise SystemExit("another R2 supervisor owns the state lock") from error

    def receive_hup(_signum: int, _frame: object) -> None:
        _journal_append(state_directory, "sighup_received")

    signal.signal(signal.SIGHUP, receive_hup)
    files = _registered_files(
        project_root, arguments.config, arguments.manifest, arguments.runner
    )
    process = _proc_record(os.getpid())
    runner_command = _runner_command(
        bash_tool,
        project_root,
        arguments.runner,
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
        go = _read_object(go_path)
        if go.get("protocol_revision") != PROTOCOL_REVISION:
            raise ValueError("GO protocol revision differs")
        if go.get("launch_sha256") != _sha256(state_directory / "launch.json"):
            raise ValueError("GO launch hash differs")
        if go.get("boot_id") != _boot_id():
            raise ValueError("GO boot ID differs")
        if go.get("supervisor_start_time_ticks") != process["start_time_ticks"]:
            raise ValueError("GO supervisor identity differs")
        _require_clean(project_root)
        current_files = _registered_files(
            project_root, arguments.config, arguments.manifest, arguments.runner
        )
        if current_files != files:
            raise ValueError("registered files changed after launch")
        attestation_relative = Path(str(go["attestation_path"]))
        attestation_path = project_root / attestation_relative
        if go.get("attestation_sha256") != _sha256(attestation_path):
            raise ValueError("GO detachment attestation hash differs")
        subprocess.check_call(
            ["git", "-C", str(project_root), "ls-files", "--error-unmatch", "--", str(attestation_relative)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if _git(project_root, "diff", "--", str(attestation_relative)):
            raise ValueError("detachment attestation differs from HEAD")
    except Exception as error:
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
            git_head=_git(project_root, "rev-parse", "HEAD"),
        )
        child = subprocess.Popen(
            runner_command,
            cwd=project_root,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            env=_runner_environment(
                frozen_environment,
                state_directory,
                os.getpid(),
                process["start_time_ticks"],
                _boot_id(),
                _sha256(state_directory / "launch.json"),
                _sha256(go_path),
            ),
        )
        child_identity = _proc_record(child.pid)
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
                "go_sha256": _sha256(go_path),
                "journal_sha256": _sha256(state_directory / "journal.jsonl"),
                "git_head_after_runner": _git(project_root, "rev-parse", "HEAD"),
                "analysis_summary_path": DEFAULT_ANALYSIS_SUMMARY.as_posix(),
                "analysis_summary_sha256": analysis_summary_hash,
                "log_sha256": _sha256(state_directory / "supervisor.log"),
                "finished_utc": _utc(),
            },
        )
    except Exception as error:
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
    pid = int(process["pid"])
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
        "git_head": _git(project_root, "rev-parse", "HEAD"),
        "registered_files": registered,
        "created_utc": _utc(),
    }
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
    )
    launch_path = state_directory / "launch.json"
    go_path = state_directory / "go.json"
    terminal_path = state_directory / "terminal.json"
    log_path = state_directory / "supervisor.log"
    journal_path = state_directory / "journal.jsonl"
    launch = _read_object(launch_path)
    go_payload = _read_object(go_path)
    terminal = _read_object(terminal_path)
    launch_process = launch.get("process")
    if not isinstance(launch_process, dict):
        raise ValueError("completion launch process identity is missing")
    if launch.get("protocol_revision") != PROTOCOL_REVISION:
        raise ValueError("completion launch protocol differs")
    if launch.get("environment") != _environment_registration(config_at_entry):
        raise ValueError("completion launch frozen ambient-environment record differs")
    if go_payload.get("protocol_revision") != PROTOCOL_REVISION:
        raise ValueError("completion GO protocol differs")
    if terminal.get("protocol_revision") != PROTOCOL_REVISION:
        raise ValueError("completion terminal protocol differs")
    if Path(str(launch.get("state_directory", ""))).resolve() != state_directory:
        raise ValueError("completion launch state directory differs")
    if (
        go_payload.get("supervisor_pid") != launch_process.get("pid")
        or go_payload.get("supervisor_start_time_ticks")
        != launch_process.get("start_time_ticks")
        or go_payload.get("boot_id") != launch.get("boot_id")
    ):
        raise ValueError("completion GO supervisor identity differs")
    if terminal.get("status") != "accepted" or terminal.get("runner_return_code") != 0:
        raise ValueError("supervisor terminal record is not accepted")
    if go_payload.get("launch_sha256") != _sha256(launch_path):
        raise ValueError("completion GO launch hash differs")
    if terminal.get("launch_sha256") != _sha256(launch_path):
        raise ValueError("completion terminal launch hash differs")
    if terminal.get("go_sha256") != _sha256(go_path):
        raise ValueError("completion terminal GO hash differs")
    if terminal.get("journal_sha256") != _sha256(journal_path):
        raise ValueError("completion terminal journal hash differs")
    if terminal.get("log_sha256") != _sha256(log_path):
        raise ValueError("completion terminal log hash differs")
    analysis_relative = Path(str(terminal.get("analysis_summary_path", "")))
    if analysis_relative != DEFAULT_ANALYSIS_SUMMARY:
        raise ValueError("completion analysis summary path differs")
    analysis_path = project_root / analysis_relative
    if terminal.get("analysis_summary_sha256") != _sha256(analysis_path):
        raise ValueError("completion analysis summary hash differs")
    analysis = _read_object(analysis_path)
    if (
        analysis.get("protocol_revision") != PROTOCOL_REVISION
        or analysis.get("audit_status") != "accepted"
        or analysis.get("overall_protocol_status") != "pending_supervisor_completion"
        or analysis.get("g1_status") != "pending (1/6)"
        or analysis.get("authorized_scope") != "no_G1_advancement"
    ):
        raise ValueError("completion analysis is not an accepted R2 summary")
    if terminal.get("git_head_after_runner") != _git(project_root, "rev-parse", "HEAD"):
        raise ValueError("completion Git HEAD changed after the runner")
    events = _journal_events(state_directory)
    started = [event for event in events if event.get("event") == "runner_started"]
    finished = [event for event in events if event.get("event") == "runner_finished"]
    if len(started) != 1 or len(finished) != 1:
        raise ValueError("completion journal runner event count differs")
    if (
        finished[0].get("return_code") != 0
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
    config_payload = _read_object(config_path)
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
        or manifest_registration.get("sha256") != _sha256(manifest_path)
    ):
        raise ValueError("completion manifest registration differs")
    git_head = _git(project_root, "rev-parse", "HEAD")
    payload = {
        "schema_version": 1,
        "protocol_revision": PROTOCOL_REVISION,
        "status": "supervisor_completed",
        "created_utc": _utc(),
        "config_path": config_relative.as_posix(),
        "config_sha256": _sha256(config_path),
        "manifest_path": manifest_relative.as_posix(),
        "manifest_sha256": _sha256(manifest_path),
        "git_head_before_completion": git_head,
        "supervisor_state_directory": str(state_directory),
        "supervisor_launch_path": str(launch_path),
        "supervisor_launch_sha256": _sha256(launch_path),
        "supervisor_terminal_path": str(terminal_path),
        "supervisor_terminal_sha256": _sha256(terminal_path),
        "supervisor_journal_path": str(journal_path),
        "supervisor_journal_sha256": _sha256(journal_path),
        "supervisor_pid": int(launch_process["pid"]),
        "supervisor_start_time_ticks": int(launch_process["start_time_ticks"]),
        "boot_id": str(launch["boot_id"]),
        "runner_exit_code": int(terminal["runner_return_code"]),
        "analysis_path": analysis_relative.as_posix(),
        "analysis_sha256": str(terminal["analysis_summary_sha256"]),
        "analysis_audit_status": str(analysis["audit_status"]),
        "final_acceptance_policy": "committed_completion_then_validator_revalidation",
    }
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
