#!/usr/bin/env python3
"""Run the dedicated managed 074 runtime-relocation smoke.

This entry never writes runs/074 or runs/113--118.  Its only output root is
analysis/s1/runtime_relocation_smoke_20260805/.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from generate_s1_mpi_prefix_equivalence import build_frozen_payload
from s1_mpi_prefix_equivalence_common import (
    DEFAULT_R8_SUMMARY_PATH,
    FIXED_PAIRS,
    R8_CONFIG_PATH,
    R8_MANIFEST_PATH,
    RUNTIME_SMOKE_ID,
    RUNTIME_SMOKE_REFERENCE_ID,
    RUNTIME_SMOKE_RUN_DIRECTORY,
    RUNTIME_SMOKE_ROOT,
    git_clean,
)
from s1_runtime_relocation_smoke import (
    finalize_smoke,
    validate_smoke,
    validate_smoke_precommit,
)
from write_s1_runtime_relocation_status import write_status


def _atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _preserve_precommit_validation_failure(
    smoke_root: Path,
    run_directory: Path,
    error: Exception,
) -> None:
    """Convert an accepted execution/rejected validation into an archivable attempt."""

    summary_path = smoke_root / "summary.json"
    manifest_path = smoke_root / "evidence_manifest.tsv"
    candidate_summary = run_directory / "precommit_candidate_summary.json"
    candidate_manifest = run_directory / "precommit_candidate_evidence_manifest.tsv"
    if (
        not summary_path.is_file()
        or not manifest_path.is_file()
        or candidate_summary.exists()
        or candidate_manifest.exists()
    ):
        raise ValueError("precommit failure candidate files are incomplete or reused")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("status") != "accepted":
        raise ValueError("precommit failure candidate summary was not accepted")
    metadata = json.loads(
        (run_directory / "experiment_metadata.json").read_text(encoding="utf-8")
    )
    run_status = json.loads(
        (run_directory / "run_status.json").read_text(encoding="utf-8")
    )
    workflow_exit = int(run_status.get("workflow_exit_code", 97))
    invocation_exit = int(run_status.get("invocation_exit_code", 97))
    parser_exit = int(run_status.get("parser_exit_code", 97))
    write_status(
        run_directory,
        experiment_id=RUNTIME_SMOKE_ID,
        code_commit=metadata["code_commit"],
        workflow_exit=workflow_exit,
        invocation_exit=invocation_exit,
        parser_exit=parser_exit,
        core_validation_exit=97,
        setup_completed=run_status.get("setup_completed") is True,
        failure_stage="managed_smoke_precommit_validation",
    )
    failure_status_path = run_directory / "failure.json"
    failure_status = json.loads(failure_status_path.read_text(encoding="utf-8"))
    failure_status["failure_stage"] = "managed_smoke_precommit_validation"
    failure_status["precommit_validation_error_type"] = type(error).__name__
    failure_status["precommit_validation_error"] = str(error)
    _atomic_json(failure_status_path, failure_status)
    _atomic_json(
        run_directory / "precommit_validation_failure.json",
        {
            "schema_version": 1,
            "status": "rejected",
            "failure_stage": "managed_smoke_precommit_validation",
            "error_type": type(error).__name__,
            "error": str(error),
            "code_commit": metadata["code_commit"],
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "candidate_summary_status": "accepted",
            "candidate_summary_path": "run/precommit_candidate_summary.json",
            "candidate_evidence_manifest_path": (
                "run/precommit_candidate_evidence_manifest.tsv"
            ),
            "evidence_manifest_scope": (
                "pre_validation_run_tree_before_failure_annotation"
            ),
            "retry_requires_committed_archive": True,
        },
    )
    os.replace(summary_path, candidate_summary)
    os.replace(manifest_path, candidate_manifest)


def _environment(config: dict, run_directory: Path) -> dict[str, str]:
    runtime = config["runtime"]
    replay = runtime["replay"]
    reference = runtime["reference"]
    tools = runtime["tools"]
    wrappers = runtime["wrappers"]
    prefix = runtime["recovery_prefix"]
    values = {
        "HOME": str(run_directory / "runtime_home"),
        "USER": os.environ.get("USER", "shenwei01"),
        "LOGNAME": os.environ.get("LOGNAME", os.environ.get("USER", "shenwei01")),
        "PATH": "/usr/bin:/bin",
        "LC_ALL": "C",
        "TZ": "UTC",
        "TMPDIR": "/tmp",
        "OMP_NUM_THREADS": "1",
        "CUDA_CACHE_DISABLE": "1",
        "M_OFDFT_RUNTIME_RELOCATION_MODE": "1",
        "M_OFDFT_RUNTIME_RELOCATION_SMOKE_MODE": "1",
        "M_OFDFT_RUN_DIRECTORY_OVERRIDE": str(run_directory),
        "M_OFDFT_RUNTIME": runtime["recovery_root"],
        "M_OFDFT_PREFIX": prefix,
        "OPAL_PREFIX": prefix,
        "PRTE_PREFIX": prefix,
        "PMIX_PREFIX": prefix,
        "UCX_MODULE_DIR": prefix,
        "M_OFDFT_ABACUS": replay["abacus"]["path"],
        "M_OFDFT_NPROCS": str(config["rank_count"]),
        "M_OFDFT_MPIRUN": tools["python"]["path"],
        "M_OFDFT_MPIRUN_SCRIPT": wrappers["namespace_launcher"]["path"],
        "M_OFDFT_PROVENANCE_MPIRUN": replay["mpirun"]["path"],
        "M_OFDFT_PYTHON_TOOL": tools["python"]["path"],
        "M_OFDFT_PYTHON_SHA256": tools["python"]["sha256"],
        "M_OFDFT_REAL_MPIRUN": replay["mpirun"]["path"],
        "M_OFDFT_EXPECTED_MPIRUN_SHA256": replay["mpirun"]["sha256"],
        "M_OFDFT_EXPECTED_LAUNCHER": replay["launcher"]["path"],
        "M_OFDFT_EXPECTED_LAUNCHER_SHA256": replay["launcher"]["sha256"],
        "M_OFDFT_EXPECTED_ABACUS": replay["abacus"]["path"],
        "M_OFDFT_EXPECTED_ABACUS_SHA256": replay["abacus"]["sha256"],
        "M_OFDFT_MPI_AUDIT_DIR": str(run_directory / "mpi_runtime_audit"),
        "M_OFDFT_RECOVERY_ROOT": runtime["recovery_root"],
        "M_OFDFT_RECOVERY_PREFIX": prefix,
        "M_OFDFT_OLD_ROOT": runtime["old_root"],
        "M_OFDFT_OLD_PREFIX": runtime["old_prefix"],
        "M_OFDFT_MPI_AUDIT_EXPECTED_RANKS": str(config["rank_count"]),
        "M_OFDFT_NAMESPACE_PAYLOAD": wrappers["namespace_payload"]["path"],
        "M_OFDFT_NAMESPACE_PAYLOAD_SHA256": wrappers["namespace_payload"]["sha256"],
        "M_OFDFT_AUDIT_LAUNCHER": wrappers["audit_launcher"]["path"],
        "M_OFDFT_AUDIT_LAUNCHER_SHA256": wrappers["audit_launcher"]["sha256"],
        "M_OFDFT_RANK_WRAPPER": wrappers["rank_wrapper"]["path"],
        "M_OFDFT_RANK_WRAPPER_SHA256": wrappers["rank_wrapper"]["sha256"],
        "M_OFDFT_MOUNT_TOOL": tools["mount"]["path"],
        "M_OFDFT_HOST_UID": str(runtime["namespace"]["host_uid"]),
        "M_OFDFT_HOST_GID": str(runtime["namespace"]["host_gid"]),
    }
    for prefix_name, name in (
        ("STRACE", "strace"),
        ("PYTHON", "python"),
        ("UNSHARE", "unshare"),
        ("MOUNT", "mount"),
        ("BASH", "bash"),
    ):
        tool = tools[name]
        values[f"M_OFDFT_{prefix_name}_PATH"] = tool["path"]
        values[f"M_OFDFT_{prefix_name}_REALPATH"] = tool["realpath"]
        values[f"M_OFDFT_{prefix_name}_SHA256"] = tool["sha256"]
        values[f"M_OFDFT_{prefix_name}_VERSION_FIRST_LINE"] = tool[
            "version_first_line"
        ]
        values[f"M_OFDFT_{prefix_name}_VERSION_OUTPUT_SHA256"] = tool[
            "version_output_sha256"
        ]
    values["M_OFDFT_STRACE_TOOL"] = tools["strace"]["path"]
    values["M_OFDFT_UNSHARE_TOOL"] = tools["unshare"]["path"]
    values["M_OFDFT_BASH_TOOL"] = tools["bash"]["path"]
    for role in ("ABACUS", "MPIRUN", "LAUNCHER"):
        for scope, identities in (("REPLAY", replay), ("REFERENCE", reference)):
            identity = identities[role.lower()]
            for field in ("path", "realpath", "sha256"):
                values[f"M_OFDFT_{scope}_{role}_{field.upper()}"] = identity[field]
    return values


def _archive_existing_failed_smoke(project_root: Path, smoke_root: Path) -> None:
    summary_path = smoke_root / "summary.json"
    if summary_path.is_file():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError("existing smoke summary is invalid; preserve and inspect it") from error
        if summary.get("status") == "accepted":
            raise ValueError("an accepted managed 074 smoke already exists")
    replay_status_path = smoke_root / "run" / "replay_status.json"
    failure_path = smoke_root / "run" / "failure.json"
    try:
        replay = json.loads(replay_status_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as error:
        raise ValueError("existing smoke root lacks a machine-readable rejected attempt") from error
    if replay.get("status") != "rejected" or not failure_path.is_file():
        raise ValueError("existing smoke root is neither accepted nor an archivable failure")
    status = subprocess.run(
        ["git", "-C", str(project_root), "status", "--porcelain"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if status.returncode != 0 or status.stdout.strip():
        raise ValueError(
            "failed smoke must first be committed as a complete evidence tree before retry"
        )
    relative_status = replay_status_path.relative_to(project_root).as_posix()
    failure_commit = subprocess.check_output(
        [
            "git",
            "-C",
            str(project_root),
            "log",
            "-1",
            "--no-renames",
            "--diff-filter=A",
            "--format=%H",
            "--",
            relative_status,
        ],
        text=True,
    ).strip()
    head = subprocess.check_output(
        ["git", "-C", str(project_root), "rev-parse", "HEAD"], text=True
    ).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", failure_commit) or failure_commit != head:
        raise ValueError("failed smoke evidence must be the current HEAD before archive/retry")
    archive = (
        project_root
        / "failed_runs/runtime_relocation_smoke"
        / f"attempt-{failure_commit[:12]}"
    )
    if archive.exists() or archive.is_symlink():
        raise ValueError(f"refusing to overwrite failed smoke archive: {archive}")
    archive.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "-C", str(project_root), "mv", str(smoke_root), str(archive)],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(project_root),
            "commit",
            "-m",
            f"archive failed runtime-relocation smoke {failure_commit[:12]}",
        ],
        check=True,
    )


def _only_smoke_worktree_changes(project_root: Path) -> bool:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(project_root),
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
            ".",
            f":(exclude){RUNTIME_SMOKE_ROOT.as_posix()}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError(completed.stderr.strip() or "cannot inspect smoke worktree")
    return not completed.stdout.strip()


def _summary_exists_at_head(project_root: Path) -> bool:
    relative = RUNTIME_SMOKE_ROOT.joinpath("summary.json").as_posix()
    completed = subprocess.run(
        ["git", "-C", str(project_root), "cat-file", "-e", f"HEAD:{relative}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode == 0


def _pending_commit_result(validation: dict) -> dict:
    payload = dict(validation)
    payload.pop("tracked_paths", None)
    return {
        "status": "pending_commit",
        "message": "managed 074 smoke evidence passed precommit validation; commit the complete canonical smoke root",
        "next_command": (
            "git add analysis/s1/runtime_relocation_smoke_20260805 && "
            "git commit -m 'record managed 074 runtime-relocation smoke'"
        ),
        "validation": payload,
    }


def run_smoke(project_root: Path, config: dict, row: dict[str, str]) -> dict:
    smoke_root = project_root / RUNTIME_SMOKE_ROOT
    run_directory = project_root / RUNTIME_SMOKE_RUN_DIRECTORY
    if smoke_root.exists() or smoke_root.is_symlink():
        summary_path = smoke_root / "summary.json"
        try:
            existing_summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            existing_summary = None
        if isinstance(existing_summary, dict) and existing_summary.get("status") == "accepted":
            if not _only_smoke_worktree_changes(project_root):
                raise ValueError("accepted smoke may be resumed only with no unrelated worktree changes")
            if _summary_exists_at_head(project_root):
                validation = validate_smoke(
                    project_root, config, row, summary_path, require_committed=True
                )
                validation.pop("tracked_paths", None)
                return {
                    "status": "accepted_committed",
                    "message": "managed 074 smoke is already committed and fully validated",
                    "validation": validation,
                }
            try:
                validation = validate_smoke_precommit(
                    project_root, config, row, summary_path
                )
            except Exception as error:
                _preserve_precommit_validation_failure(
                    smoke_root, run_directory, error
                )
                raise ValueError(
                    "managed smoke precommit validation failed; rejection evidence preserved"
                ) from error
            return _pending_commit_result(validation)
        _archive_existing_failed_smoke(project_root, smoke_root)
    elif not git_clean(project_root):
        raise ValueError("managed smoke must start from a clean worktree")
    source_directory = project_root / row["input_directory"]
    completed = subprocess.run(
        [
            str(project_root / "scripts" / "run_s1_single.sh"),
            RUNTIME_SMOKE_ID,
            str(source_directory),
        ],
        cwd=project_root,
        env=_environment(config, run_directory),
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if not run_directory.is_dir():
        raise ValueError("managed smoke entry did not create its canonical run directory")
    try:
        existing = json.loads((run_directory / "run_status.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        existing = {}
    metadata = json.loads(
        (run_directory / "experiment_metadata.json").read_text(encoding="utf-8")
    )
    invocation_exit = existing.get("invocation_exit_code", completed.returncode)
    parser_exit = existing.get("parser_exit_code", 97)
    write_status(
        run_directory,
        experiment_id=RUNTIME_SMOKE_ID,
        code_commit=metadata["code_commit"],
        workflow_exit=completed.returncode,
        invocation_exit=invocation_exit,
        parser_exit=parser_exit,
        core_validation_exit=0 if completed.returncode == 0 else 97,
        setup_completed=existing.get("setup_completed") is True,
        failure_stage=existing.get("failure_stage"),
    )
    if completed.returncode != 0:
        raise ValueError(
            f"managed 074 smoke rejected with workflow exit {completed.returncode}"
        )
    summary_path = smoke_root / "summary.json"
    finalize_smoke(project_root, config, row, summary_path)
    try:
        validation = validate_smoke_precommit(project_root, config, row, summary_path)
    except Exception as error:
        _preserve_precommit_validation_failure(smoke_root, run_directory, error)
        raise ValueError(
            "managed smoke precommit validation failed; rejection evidence preserved"
        ) from error
    return _pending_commit_result(validation)


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description=(
            "Run the managed S1-R8 074 runtime-relocation smoke without touching "
            "reference 074 or formal replay IDs 113--118."
        )
    )
    parser.add_argument("--recovery-prefix", type=Path, required=True)
    parser.add_argument("--old-prefix", type=Path, required=True)
    parser.add_argument("--abacus", type=Path, required=True)
    parser.add_argument("--mpirun", type=Path, required=True)
    parser.add_argument("--launcher", type=Path)
    parser.add_argument("--reference-mpirun", type=Path)
    parser.add_argument("--reference-launcher", type=Path)
    parser.add_argument("--readelf", type=Path, default=Path("/usr/bin/readelf"))
    parser.add_argument("--chrpath", type=Path, default=Path("/usr/bin/chrpath"))
    parser.add_argument("--strace", type=Path, default=Path("/usr/bin/strace"))
    parser.add_argument("--unshare", type=Path, default=Path("/usr/bin/unshare"))
    parser.add_argument("--mount", type=Path, default=Path("/usr/bin/mount"))
    parser.add_argument("--bash", type=Path, default=Path("/bin/bash"))
    parser.add_argument("--python", type=Path, default=Path("/usr/bin/python3"))
    parser.add_argument("--r8-config", type=Path, default=project_root / R8_CONFIG_PATH)
    parser.add_argument("--r8-manifest", type=Path, default=project_root / R8_MANIFEST_PATH)
    parser.add_argument(
        "--r8-summary", type=Path, default=project_root / DEFAULT_R8_SUMMARY_PATH
    )
    args = parser.parse_args()
    draft_manifest = project_root / RUNTIME_SMOKE_ROOT / "draft-unused-manifest.tsv"
    config, rows, _ = build_frozen_payload(
        project_root,
        args.recovery_prefix,
        args.old_prefix,
        args.abacus,
        args.mpirun,
        args.launcher,
        args.r8_config.resolve(),
        args.r8_manifest.resolve(),
        args.r8_summary.resolve(),
        draft_manifest,
        readelf=args.readelf,
        chrpath=args.chrpath,
        strace=args.strace,
        unshare=args.unshare,
        mount=args.mount,
        bash=args.bash,
        python=args.python,
        reference_mpirun=args.reference_mpirun,
        reference_launcher=args.reference_launcher,
        require_clean_worktree=False,
    )
    row = next(
        value
        for value in rows
        if value["reference_experiment_id"] == RUNTIME_SMOKE_REFERENCE_ID
    )
    summary = run_smoke(project_root, config, row)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
