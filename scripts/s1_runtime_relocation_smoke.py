#!/usr/bin/env python3
"""Managed pre-registration smoke evidence for S1 runtime relocation."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import stat
import subprocess
from datetime import datetime
from pathlib import Path

from s1_mpi_prefix_equivalence_common import (
    RUNTIME_SMOKE_EVIDENCE_MANIFEST,
    RUNTIME_SMOKE_ID,
    RUNTIME_SMOKE_REFERENCE_ID,
    RUNTIME_SMOKE_RUN_DIRECTORY,
    RUNTIME_SMOKE_ROOT,
    equivalence_tier,
    normalized_run_input,
    path_from_project,
    raw_observables,
    reparse_run,
    sha256,
)


EVIDENCE_HEADER = ("relative_path", "git_mode", "size_bytes", "sha256")
SUMMARY_KEYS = {
    "schema_version",
    "status",
    "smoke_id",
    "reference_experiment_id",
    "run_directory",
    "evidence_manifest_path",
    "evidence_manifest_sha256",
    "evidence_file_count",
    "code_commit",
    "generated_at_utc",
    "runtime_registration_sha256",
    "implementation_closure",
    "runtime_identities",
    "status_gates",
    "scientific_equivalence",
}

SMOKE_IMPLEMENTATION_PATHS = (
    "environment/activate.sh",
    "scripts/generate_s1_mpi_prefix_equivalence.py",
    "scripts/validate_s1_mpi_prefix_equivalence.py",
    "scripts/s1_mpi_prefix_equivalence_common.py",
    "scripts/s1_runtime_relocation_elf.py",
    "scripts/s1_runtime_relocation_smoke.py",
    "scripts/run_s1_runtime_relocation_smoke.py",
    "scripts/run_s1_runtime_relocation_equivalence.sh",
    "scripts/run_s1_single.sh",
    "scripts/write_s1_runtime_relocation_status.py",
    "scripts/runtime_relocation_namespace_launcher.py",
    "scripts/runtime_relocation_namespace_payload.sh",
    "scripts/runtime_relocation_audit_launcher.py",
    "scripts/runtime_relocation_rank_wrapper.py",
    "scripts/parse_s1_single.py",
)


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def _atomic_json(path: Path, payload: dict) -> None:
    _atomic_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _regular_file_mode(path: Path) -> str:
    value = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(value.st_mode):
        raise ValueError(f"smoke evidence must contain regular files only: {path}")
    return "100755" if value.st_mode & 0o111 else "100644"


def _evidence_rows(run_directory: Path) -> list[dict[str, object]]:
    if not run_directory.is_dir() or run_directory.is_symlink():
        raise ValueError(f"missing managed smoke run directory: {run_directory}")
    rows = []
    for path in sorted(run_directory.rglob("*")):
        if path.is_dir() and not path.is_symlink():
            continue
        rows.append(
            {
                "relative_path": path.relative_to(run_directory).as_posix(),
                "git_mode": _regular_file_mode(path),
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    if not rows:
        raise ValueError("managed smoke evidence tree is empty")
    return rows


def write_evidence_manifest(run_directory: Path, manifest_path: Path) -> list[dict]:
    rows = _evidence_rows(run_directory)
    lines = ["\t".join(EVIDENCE_HEADER)]
    for row in rows:
        value = [str(row[key]) for key in EVIDENCE_HEADER]
        if any(any(character in item for character in "\t\r\n") for item in value):
            raise ValueError("smoke evidence manifest contains a control character")
        lines.append("\t".join(value))
    _atomic_text(manifest_path, "\n".join(lines) + "\n")
    return rows


def _read_evidence_manifest(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if tuple(reader.fieldnames or ()) != EVIDENCE_HEADER:
            raise ValueError("invalid smoke evidence manifest header")
        rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError("ragged smoke evidence manifest")
    return rows


def runtime_registration_sha256(config: dict) -> str:
    registered = {
        "rank_count": config["rank_count"],
        "runtime": config["runtime"],
        "runtime_audit": config["runtime_audit"],
    }
    encoded = json.dumps(registered, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _runtime_identities(config: dict) -> dict:
    runtime = config["runtime"]
    return {
        "reference_abacus": runtime["reference"]["abacus"],
        "replay_abacus": runtime["replay"]["abacus"],
        "reference_mpirun": runtime["reference"]["mpirun"],
        "replay_mpirun": runtime["replay"]["mpirun"],
        "reference_launcher": runtime["reference"]["launcher"],
        "replay_launcher": runtime["replay"]["launcher"],
        "tools": runtime["tools"],
        "wrappers": runtime["wrappers"],
        "elf_relocation": runtime["elf_relocation"],
    }


def _implementation_closure(project_root: Path, code_commit: str) -> list[dict]:
    if not re.fullmatch(r"[0-9a-f]{40}", str(code_commit)):
        raise ValueError("managed smoke code_commit is invalid")
    rows = []
    for relative in SMOKE_IMPLEMENTATION_PATHS:
        path = project_root / relative
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"managed smoke implementation file is missing: {relative}")
        completed = subprocess.run(
            ["git", "-C", str(project_root), "cat-file", "blob", f"{code_commit}:{relative}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode != 0:
            raise ValueError(f"cannot read managed smoke implementation at commit: {relative}")
        current = path.read_bytes()
        if current != completed.stdout:
            raise ValueError(
                f"current managed smoke implementation differs from code_commit: {relative}"
            )
        rows.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(current).hexdigest(),
                "size_bytes": len(current),
            }
        )
    return rows


def _validate_smoke_commit_chain(
    project_root: Path, code_commit: str, paths: tuple[Path, ...]
) -> str:
    additions = []
    for path in paths:
        relative = path.relative_to(project_root).as_posix()
        values = subprocess.check_output(
            [
                "git",
                "-C",
                str(project_root),
                "log",
                "--diff-filter=A",
                "--format=%H",
                "--",
                relative,
            ],
            text=True,
        ).splitlines()
        if len(values) != 1:
            raise ValueError(
                f"managed smoke artifact must have one introduction commit: {relative}"
            )
        additions.append(values[0])
        committed = subprocess.check_output(
            ["git", "-C", str(project_root), "cat-file", "blob", f"{values[0]}:{relative}"]
        )
        if committed != path.read_bytes():
            raise ValueError(f"managed smoke artifact differs from introduction: {relative}")
    if len(set(additions)) != 1:
        raise ValueError("managed smoke summary/manifest/run metadata were not committed together")
    smoke_commit = additions[0]
    parent = subprocess.check_output(
        ["git", "-C", str(project_root), "rev-parse", f"{smoke_commit}^"], text=True
    ).strip()
    if parent != code_commit:
        raise ValueError("managed smoke commit parent differs from executed code_commit")
    ancestor = subprocess.run(
        ["git", "-C", str(project_root), "merge-base", "--is-ancestor", smoke_commit, "HEAD"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if ancestor.returncode != 0:
        raise ValueError("managed smoke commit is not an ancestor of HEAD")
    return smoke_commit


def _scientific_equivalence(
    project_root: Path, run_directory: Path, row: dict[str, str]
) -> dict:
    reference_run = project_root / "runs" / RUNTIME_SMOKE_REFERENCE_ID
    reference_metadata, reference_log, reference_result = reparse_run(reference_run)
    smoke_metadata, smoke_log, smoke_result = reparse_run(run_directory)
    if not reference_result.get("converged") or not smoke_result.get("converged"):
        raise ValueError("reference/smoke result is not converged")
    reference_raw = raw_observables(
        reference_log.read_text(encoding="utf-8", errors="replace"),
        str(reference_metadata["solver"]),
        int(reference_metadata["atom_count"]),
    )
    smoke_raw = raw_observables(
        smoke_log.read_text(encoding="utf-8", errors="replace"),
        str(smoke_metadata["solver"]),
        int(smoke_metadata["atom_count"]),
    )
    comparison = equivalence_tier(reference_raw, smoke_raw)
    return {
        "status": "accepted" if comparison["scientific_tolerance_passed"] else "rejected",
        "tier": comparison["tier"],
        "delta_energy_mev_per_atom": str(comparison["delta_energy_mev_per_atom"]),
        "delta_pressure_gpa": str(comparison["delta_pressure_gpa"]),
        "energy_strictly_below_0_1_mev_per_atom": comparison[
            "energy_strictly_below_0_1_mev_per_atom"
        ],
        "pressure_strictly_below_0_02_gpa": comparison[
            "pressure_strictly_below_0_02_gpa"
        ],
        "reference_result_sha256": sha256(reference_run / "result.json"),
        "reference_log_sha256": sha256(reference_log),
        "smoke_result_sha256": sha256(run_directory / "result.json"),
        "smoke_log_sha256": sha256(smoke_log),
        "manifest_input_directory": row["input_directory"],
    }


def _status_gates(run_directory: Path) -> dict:
    paths = {
        "run_status": run_directory / "run_status.json",
        "replay_status": run_directory / "replay_status.json",
        "runtime_audit": run_directory / "mpi_runtime_audit" / "audit.json",
        "namespace_host": run_directory
        / "mpi_runtime_audit"
        / "namespace"
        / "host_status.json",
        "counterpart_audit": run_directory
        / "mpi_runtime_audit"
        / "counterpart_audit.json",
    }
    payloads = {}
    for name, path in paths.items():
        payload = json.loads(path.read_text(encoding="utf-8"))
        payloads[name] = {
            "path": path,
            "sha256": sha256(path),
            "status": payload.get("status"),
            "payload": payload,
        }
    run_status = payloads["run_status"]["payload"]
    replay_status = payloads["replay_status"]["payload"]
    if (
        any(value["status"] != "accepted" for value in payloads.values())
        or run_status.get("setup_completed") is not True
        or run_status.get("failure_stage") is not None
        or replay_status.get("core_validation_exit_code") != 0
        or replay_status.get("run_status") != run_status
        or (run_directory / "failure.json").exists()
    ):
        raise ValueError("managed 074 smoke status gates are not all accepted")
    return {
        name: {
            "path": str(value["path"]),
            "sha256": value["sha256"],
            "status": value["status"],
        }
        for name, value in payloads.items()
    }


def _validate_experiment_metadata(
    config: dict, run_directory: Path, metadata: dict
) -> None:
    runtime = config["runtime"]
    replay = runtime["replay"]
    tools = runtime["tools"]
    wrappers = runtime["wrappers"]
    audit = config["runtime_audit"]
    expected = {
        "experiment_id": RUNTIME_SMOKE_ID,
        "mpi_ranks": config["rank_count"],
        "abacus_path": replay["abacus"]["path"],
        "abacus_realpath": replay["abacus"]["realpath"],
        "abacus_sha256": replay["abacus"]["sha256"],
        "mpirun_path": replay["mpirun"]["path"],
        "mpirun_realpath": replay["mpirun"]["realpath"],
        "mpirun_sha256": replay["mpirun"]["sha256"],
        "mpirun_invocation_path": wrappers["namespace_launcher"]["path"],
        "mpirun_invocation_sha256": wrappers["namespace_launcher"]["sha256"],
        "mpirun_invocation_interpreter_path": tools["python"]["path"],
        "mpirun_invocation_interpreter_realpath": tools["python"]["realpath"],
        "mpirun_invocation_interpreter_sha256": tools["python"]["sha256"],
        "runtime_relocation_mode": True,
        "runtime_environment": {
            "PATH": audit["required_path"],
            "LD_LIBRARY_PATH": audit["required_ld_library_path"],
            "LD_PRELOAD": None,
            "CMAKE_PREFIX_PATH": audit["required_cmake_prefix_path"],
            "MKLROOT": audit["required_mklroot"],
            "HOME": str(run_directory / "runtime_home"),
            "OMP_NUM_THREADS": "1",
        },
        **runtime["prefix_environment"],
        "worktree_dirty": False,
    }
    mismatches = [key for key, value in expected.items() if metadata.get(key) != value]
    if mismatches:
        raise ValueError(
            "managed smoke experiment metadata identity mismatch: " + ",".join(mismatches)
        )


def build_summary(
    project_root: Path,
    config: dict,
    row: dict[str, str],
    run_directory: Path,
    evidence_manifest_path: Path,
) -> dict:
    expected_run = project_root / RUNTIME_SMOKE_RUN_DIRECTORY
    if run_directory.resolve() != expected_run.resolve():
        raise ValueError("smoke run directory is not the canonical managed path")
    metadata = json.loads(
        (run_directory / "experiment_metadata.json").read_text(encoding="utf-8")
    )
    if metadata.get("experiment_id") != RUNTIME_SMOKE_ID:
        raise ValueError("smoke experiment metadata has the wrong identifier")
    _validate_experiment_metadata(config, run_directory, metadata)
    scientific = _scientific_equivalence(project_root, run_directory, row)
    if scientific["status"] != "accepted":
        raise ValueError("managed 074 smoke failed scientific equivalence")
    gates = _status_gates(run_directory)
    rows = _read_evidence_manifest(evidence_manifest_path)
    return {
        "schema_version": 1,
        "status": "accepted",
        "smoke_id": RUNTIME_SMOKE_ID,
        "reference_experiment_id": RUNTIME_SMOKE_REFERENCE_ID,
        "run_directory": RUNTIME_SMOKE_RUN_DIRECTORY.as_posix(),
        "evidence_manifest_path": RUNTIME_SMOKE_EVIDENCE_MANIFEST.as_posix(),
        "evidence_manifest_sha256": sha256(evidence_manifest_path),
        "evidence_file_count": len(rows),
        "code_commit": metadata["code_commit"],
        "generated_at_utc": datetime.now().astimezone().isoformat(),
        "runtime_registration_sha256": runtime_registration_sha256(config),
        "implementation_closure": _implementation_closure(
            project_root, metadata["code_commit"]
        ),
        "runtime_identities": _runtime_identities(config),
        "status_gates": gates,
        "scientific_equivalence": scientific,
    }


def finalize_smoke(
    project_root: Path,
    config: dict,
    row: dict[str, str],
    summary_path: Path,
) -> dict:
    run_directory = project_root / RUNTIME_SMOKE_RUN_DIRECTORY
    manifest_path = project_root / RUNTIME_SMOKE_EVIDENCE_MANIFEST
    if summary_path.exists() or summary_path.is_symlink() or manifest_path.exists():
        raise ValueError("refusing to overwrite managed smoke summary/manifest")
    write_evidence_manifest(run_directory, manifest_path)
    summary = build_summary(project_root, config, row, run_directory, manifest_path)
    _atomic_json(summary_path, summary)
    return summary


def validate_smoke(
    project_root: Path,
    config: dict,
    row: dict[str, str],
    summary_path: Path,
) -> dict:
    project_root = project_root.resolve()
    expected_summary = project_root / RUNTIME_SMOKE_ROOT / "summary.json"
    if summary_path.resolve() != expected_summary.resolve():
        raise ValueError("smoke summary is not at the canonical managed path")
    if not summary_path.is_file() or summary_path.is_symlink():
        raise ValueError("managed smoke summary is missing or symbolic")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if set(summary) != SUMMARY_KEYS:
        raise ValueError("managed smoke summary fields differ")
    try:
        timestamp = datetime.fromisoformat(str(summary["generated_at_utc"]))
        if timestamp.tzinfo is None:
            raise ValueError("timezone missing")
    except ValueError as error:
        raise ValueError("managed smoke generated_at_utc is invalid") from error
    run_directory = project_root / RUNTIME_SMOKE_RUN_DIRECTORY
    manifest_path = project_root / RUNTIME_SMOKE_EVIDENCE_MANIFEST
    manifest_rows = _read_evidence_manifest(manifest_path)
    actual_rows = _evidence_rows(run_directory)
    normalized_manifest = [
        {
            "relative_path": row["relative_path"],
            "git_mode": row["git_mode"],
            "size_bytes": int(row["size_bytes"]),
            "sha256": row["sha256"],
        }
        for row in manifest_rows
    ]
    if normalized_manifest != actual_rows:
        raise ValueError("managed smoke evidence manifest differs from complete run tree")
    rebuilt = build_summary(project_root, config, row, run_directory, manifest_path)
    rebuilt["generated_at_utc"] = summary["generated_at_utc"]
    if rebuilt != summary:
        raise ValueError("managed smoke summary differs from reparsed evidence")
    if not re.fullmatch(r"[0-9a-f]{40}", str(summary["code_commit"])):
        raise ValueError("managed smoke code_commit is invalid")
    smoke_commit = _validate_smoke_commit_chain(
        project_root,
        summary["code_commit"],
        (
            summary_path,
            manifest_path,
            run_directory / "experiment_metadata.json",
        ),
    )

    # Re-run the detailed raw strace/maps/namespace validator against the
    # in-memory pre-registration contract.  This has no dependency on a formal
    # 113--118 config/manifest on disk.
    from validate_s1_mpi_prefix_equivalence import (
        _validate_runtime_relocation_audit_evidence,
    )

    audit = json.loads(
        (run_directory / "mpi_runtime_audit" / "audit.json").read_text(
            encoding="utf-8"
        )
    )
    errors: list[str] = []
    _validate_runtime_relocation_audit_evidence(
        run_directory,
        config["runtime"],
        config["runtime_audit"],
        audit,
        errors,
        "managed-smoke:",
    )
    if errors:
        raise ValueError("managed smoke runtime evidence failed:\n- " + "\n- ".join(errors))

    source = path_from_project(project_root, row["input_directory"])
    archived = {
        "INPUT": normalized_run_input((source / "INPUT").read_bytes()),
        "STRU": (source / "STRU").read_bytes(),
        "KPT": (source / "KPT").read_bytes(),
        "input_metadata.json": (source / "metadata.json").read_bytes(),
    }
    for name, expected in archived.items():
        if (run_directory / name).read_bytes() != expected:
            raise ValueError(f"managed smoke archived {name} differs from 074 source")
    tracked_paths = [summary_path, manifest_path, *[run_directory / row["relative_path"] for row in actual_rows]]
    return {
        "status": "accepted",
        "smoke_id": RUNTIME_SMOKE_ID,
        "reference_experiment_id": RUNTIME_SMOKE_REFERENCE_ID,
        "summary_path": RUNTIME_SMOKE_ROOT.joinpath("summary.json").as_posix(),
        "summary_sha256": sha256(summary_path),
        "run_directory": RUNTIME_SMOKE_RUN_DIRECTORY.as_posix(),
        "evidence_manifest_path": RUNTIME_SMOKE_EVIDENCE_MANIFEST.as_posix(),
        "evidence_manifest_sha256": sha256(manifest_path),
        "evidence_file_count": len(actual_rows),
        "code_commit": summary["code_commit"],
        "smoke_commit": smoke_commit,
        "runtime_registration_sha256": summary["runtime_registration_sha256"],
        "runtime_identities": summary["runtime_identities"],
        "status_gates": summary["status_gates"],
        "scientific_equivalence": summary["scientific_equivalence"],
        "tracked_paths": tracked_paths,
    }
