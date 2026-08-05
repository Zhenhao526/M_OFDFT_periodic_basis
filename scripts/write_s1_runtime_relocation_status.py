#!/usr/bin/env python3
"""Atomically materialize coherent runtime-relocation execution status.

The single-point shell invokes this from its EXIT trap.  The outer replay
orchestrator invokes it again after core validation.  Consequently every
created attempt directory is machine-readable even when setup fails before an
MPI process exists, while the final replay status still records the outer
validator result.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) else None


def _atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def write_status(
    run: Path,
    *,
    experiment_id: str,
    code_commit: str,
    workflow_exit: int,
    invocation_exit: int,
    parser_exit: int,
    core_validation_exit: int,
    setup_completed: bool,
    failure_stage: str | None,
    runtime_relocation_mode: bool = True,
    run_only: bool = False,
) -> tuple[dict, dict | None]:
    run.mkdir(parents=True, exist_ok=True)
    metadata_path = run / "experiment_metadata.json"
    metadata = _read_json(metadata_path)
    if metadata is None:
        _atomic_json(
            metadata_path,
            {
                "schema_version": 1,
                "experiment_id": experiment_id,
                "code_commit": code_commit,
                "runtime_relocation_mode": runtime_relocation_mode,
                "setup_completed": False,
                "failure_stage": failure_stage or "metadata_not_created",
            },
        )
    result_path = run / "result.json"
    audit_path = run / "mpi_runtime_audit" / "audit.json"
    host_status_path = run / "mpi_runtime_audit" / "namespace" / "host_status.json"
    counterpart_path = run / "mpi_runtime_audit" / "counterpart_audit.json"
    result = _read_json(result_path)
    audit = _read_json(audit_path)
    host_status = _read_json(host_status_path)
    counterpart = _read_json(counterpart_path)
    launcher_exit = (
        int(audit["launcher_exit_code"])
        if isinstance(audit, dict) and isinstance(audit.get("launcher_exit_code"), int)
        else invocation_exit
    )
    accepted = (
        setup_completed
        and workflow_exit == 0
        and invocation_exit == 0
        and parser_exit == 0
        and isinstance(result, dict)
        and result.get("converged") is True
    )
    if runtime_relocation_mode:
        accepted = (
            accepted
            and isinstance(audit, dict)
            and audit.get("status") == "accepted"
            and isinstance(host_status, dict)
            and host_status.get("status") == "accepted"
            and isinstance(counterpart, dict)
            and counterpart.get("status") == "accepted"
        )
    run_status = {
        "schema_version": 2,
        "status": "accepted" if accepted else "rejected",
        "runtime_relocation_mode": runtime_relocation_mode,
        "setup_completed": setup_completed,
        "failure_stage": None if accepted else (failure_stage or "component_rejected"),
        "workflow_exit_code": workflow_exit,
        "invocation_exit_code": invocation_exit,
        "launcher_exit_code": launcher_exit,
        "parser_exit_code": parser_exit,
        "result_json_present": result_path.is_file(),
        "result_converged": result.get("converged") if isinstance(result, dict) else None,
        "runtime_audit_json_present": audit_path.is_file(),
        "runtime_audit_status": audit.get("status") if isinstance(audit, dict) else None,
        "namespace_host_status": (
            host_status.get("status") if isinstance(host_status, dict) else None
        ),
        "counterpart_audit_status": (
            counterpart.get("status") if isinstance(counterpart, dict) else None
        ),
    }
    _atomic_json(run / "run_status.json", run_status)
    if run_only:
        return run_status, None

    replay_accepted = accepted and core_validation_exit == 0
    replay_status = {
        "schema_version": 2,
        "status": "accepted" if replay_accepted else "rejected",
        "workflow_exit_code": workflow_exit,
        "invocation_exit_code": invocation_exit,
        "launcher_exit_code": launcher_exit,
        "parser_exit_code": parser_exit,
        "core_validation_exit_code": core_validation_exit,
        "run_status": run_status,
        "runtime_audit_status": audit.get("status") if isinstance(audit, dict) else None,
        "runtime_audit_failure_reasons": (
            audit.get("failure_reasons", []) if isinstance(audit, dict) else []
        ),
        "safe_retry_policy": "archive_committed_failure_then_retry_same_registered_id",
    }
    _atomic_json(run / "replay_status.json", replay_status)
    failure_path = run / "failure.json"
    if replay_accepted:
        if failure_path.exists() or failure_path.is_symlink():
            failure_path.unlink()
    else:
        _atomic_json(
            failure_path,
            {
                "schema_version": 2,
                "status": "failed_attempt_preserved",
                "workflow_exit_code": workflow_exit,
                "invocation_exit_code": invocation_exit,
                "launcher_exit_code": launcher_exit,
                "parser_exit_code": parser_exit,
                "core_validation_exit_code": core_validation_exit,
                "setup_completed": setup_completed,
                "failure_stage": run_status["failure_stage"],
                "runtime_audit_failure_reasons": replay_status[
                    "runtime_audit_failure_reasons"
                ],
                "retry_requires_committed_archive": True,
            },
        )
    return run_status, replay_status


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Write atomic run/replay/failure status for one managed attempt."
    )
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--code-commit", required=True)
    parser.add_argument("--workflow-exit", type=int, required=True)
    parser.add_argument("--invocation-exit", type=int, required=True)
    parser.add_argument("--parser-exit", type=int, required=True)
    parser.add_argument("--core-validation-exit", type=int, default=97)
    parser.add_argument("--setup-completed", choices=("true", "false"), required=True)
    parser.add_argument(
        "--runtime-relocation-mode", choices=("true", "false"), default="true"
    )
    parser.add_argument("--failure-stage")
    parser.add_argument("--run-only", action="store_true")
    args = parser.parse_args()
    write_status(
        args.run_directory,
        experiment_id=args.experiment_id,
        code_commit=args.code_commit,
        workflow_exit=args.workflow_exit,
        invocation_exit=args.invocation_exit,
        parser_exit=args.parser_exit,
        core_validation_exit=args.core_validation_exit,
        setup_completed=args.setup_completed == "true",
        failure_stage=args.failure_stage,
        runtime_relocation_mode=args.runtime_relocation_mode == "true",
        run_only=args.run_only,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
