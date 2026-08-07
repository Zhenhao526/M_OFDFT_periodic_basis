#!/usr/bin/env python3
"""G1-R4 runtime-audit shim with fail-closed SIGSTOP confirmation.

The immutable R1 launcher checked for ``T``/``t`` anywhere in the complete
``State:`` line. The key name itself contains a lower-case ``t``, so that test
did not prove that the rank had stopped before ``/proc/PID/maps`` was captured.
R4 patches only the process-local function and delegates to the R2 shim.
"""

from __future__ import annotations

import os
import re
import signal
import time
from pathlib import Path

import runtime_relocation_audit_launcher as _r1
import runtime_relocation_audit_launcher_g1_r2 as _r2


STOPPED_PROC_STATES = frozenset({"T", "t"})
TERMINAL_PROC_STATES = frozenset({"Z", "X", "x"})


def _parse_proc_state(status_text: str) -> str:
    """Return the exact one-letter Linux process state from proc status."""

    state_lines = [
        line for line in status_text.splitlines() if line.startswith("State:")
    ]
    if len(state_lines) != 1:
        raise ValueError("proc status must contain exactly one State field")
    parts = state_lines[0].split()
    if len(parts) < 2 or re.fullmatch(r"[A-Za-z]", parts[1]) is None:
        raise ValueError("proc State field does not contain one state code")
    return parts[1]


def _read_proc_state(pid: int, proc_root: Path = Path("/proc")) -> str:
    status = (proc_root / str(pid) / "status").read_text(
        encoding="utf-8", errors="replace"
    )
    return _parse_proc_state(status)


def _wait_for_proc_stop(
    pid: int,
    deadline: float,
    proc_root: Path = Path("/proc"),
) -> tuple[str | None, str | None]:
    """Wait until Linux reports a stopped state, or return a precise failure."""

    last_state = "unobserved"
    while time.monotonic() < deadline:
        try:
            state = _read_proc_state(pid, proc_root)
        except FileNotFoundError:
            return None, "proc_status_missing"
        except PermissionError:
            return None, "proc_status_permission_denied"
        except ValueError:
            return None, "proc_status_parse_error"
        except OSError as error:
            return None, f"proc_status_read_error_errno:{error.errno}"
        last_state = state
        if state in STOPPED_PROC_STATES:
            return state, None
        if state in TERMINAL_PROC_STATES:
            return None, f"terminal_state:{state}"
        time.sleep(0.001)
    return None, f"stop_confirmation_timeout:last_state:{last_state}"


def _post_capture_state_failure(pid: int) -> tuple[str | None, str | None]:
    try:
        state = _read_proc_state(pid)
    except FileNotFoundError:
        return None, "post_capture_proc_status_missing"
    except PermissionError:
        return None, "post_capture_proc_status_permission_denied"
    except ValueError:
        return None, "post_capture_proc_status_parse_error"
    except OSError as error:
        return None, f"post_capture_proc_status_read_error_errno:{error.errno}"
    if state not in STOPPED_PROC_STATES:
        return state, f"stop_lost_during_map_capture:state:{state}"
    return state, None


def _release_and_capture_rank_g1_r4(
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
    _r1._atomic_control_token(release, b"release\n")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        executable = _r1._executable(pid)
        if executable == expected_abacus:
            failure: str | None = None
            stop_sent = False
            pre_state: str | None = None
            post_state: str | None = None
            try:
                os.kill(pid, signal.SIGSTOP)
                stop_sent = True
                stop_deadline = min(deadline, time.monotonic() + 2.0)
                pre_state, failure = _wait_for_proc_stop(pid, stop_deadline)
                if failure is None:
                    captured = _r1._capture_target(
                        pid,
                        "rank",
                        rank,
                        expected_abacus,
                        old_prefix,
                        recovery_root,
                        processes,
                        objects,
                    )
                    if not captured:
                        failure = (
                            "initial_map_capture_failed_after_confirmed_stop:"
                            f"state:{pre_state}"
                        )
                if failure is None:
                    post_state, failure = _post_capture_state_failure(pid)
                if failure is None:
                    process = processes.get(pid)
                    if not isinstance(process, dict):
                        failure = "captured_process_record_missing"
                    else:
                        process["initial_map_capture_stop_confirmed"] = True
                        process["initial_map_capture_stop_state_before"] = pre_state
                        process["initial_map_capture_stop_state_after"] = post_state
            except FileNotFoundError:
                failure = "capture_proc_entry_missing"
            except ProcessLookupError:
                failure = "process_gone_during_capture"
            except PermissionError:
                failure = "capture_permission_denied"
            except OSError as error:
                failure = f"capture_os_error_errno:{error.errno}"
            finally:
                if stop_sent:
                    try:
                        os.kill(pid, signal.SIGCONT)
                    except ProcessLookupError:
                        if failure is None:
                            failure = "process_gone_before_sigcont"
                    except PermissionError:
                        if failure is None:
                            failure = "sigcont_permission_denied"
                    except OSError as error:
                        if failure is None:
                            failure = f"sigcont_failed_errno:{error.errno}"
            return None if failure is None else f"rank_{rank}_{failure}"
        if executable is None:
            return f"rank_{rank}_exited_before_abacus_exec"
        time.sleep(0.001)
    return f"rank_{rank}_abacus_exec_timeout"


def main() -> int:
    """Run the R2 KMP shim with the R4 stop-confirmed capture function."""

    original = _r1._release_and_capture_rank
    _r1._release_and_capture_rank = _release_and_capture_rank_g1_r4
    try:
        return _r2.main()
    finally:
        _r1._release_and_capture_rank = original


if __name__ == "__main__":
    raise SystemExit(main())
