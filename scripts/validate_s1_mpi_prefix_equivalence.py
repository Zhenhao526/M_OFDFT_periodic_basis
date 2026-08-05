#!/usr/bin/env python3
"""Strict byte/provenance validation for the S1-R8 six-point MPI replay."""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
from pathlib import Path

from analyze_s1_non_equilibrium import _archive_failures, _checksum_failures
from s1_mpi_prefix_equivalence_common import (
    CANONICAL_CONFIG_PATH,
    CANONICAL_MANIFEST_PATH,
    FIXED_PAIRS,
    PROTOCOL_REVISION,
    REQUIRED_SOURCE_FILES,
    TRANSIENT_MAPPING_PATTERNS,
    is_within,
    normalized_run_input,
    path_from_project,
    read_r8_manifest,
    read_tsv,
    reparse_run,
    require_tracked_at_head,
    sha256,
)
from mpi_prefix_audit_launcher import (
    classify_mapping,
    parse_execve_paths,
    parse_strace_lines,
)


TOP_LEVEL_KEYS = {
    "schema_version",
    "status",
    "protocol_revision",
    "generated_from_commit",
    "experiment_id_block",
    "rank_count",
    "source",
    "runtime",
    "runtime_audit",
    "acceptance",
    "manifest_path",
    "mappings",
}
RUNTIME_KEYS = {
    "recovery_root",
    "recovery_prefix",
    "old_prefix",
    "abacus_path",
    "abacus_sha256",
    "mpirun_path",
    "mpirun_sha256",
    "launcher_path",
    "launcher_sha256",
    "prefix_environment",
}
AUDIT_KEYS = {
    "launcher_count",
    "rank_count",
    "old_prefix_mapped_object_count_max",
    "unexpected_mapped_object_count_max",
    "file_trace_required",
    "old_prefix_successful_access_count_max",
    "allowed_failed_probe_path",
    "allowed_failed_probe_errno",
    "allowed_failed_probe_expected_count_per_run",
    "other_old_prefix_attempt_count_max",
    "clean_environment_required",
    "required_ld_library_path",
    "ld_preload_must_be_unset",
    "transient_mapping_patterns",
    "system_mapping_roots",
}
SOURCE_KEYS = {
    "r8_config_path",
    "r8_config_sha256",
    "r8_manifest_path",
    "r8_manifest_sha256",
    "r8_summary_path",
    "r8_summary_sha256",
    "r8_status",
    "r8_series_status",
    "r8_manifest_validation",
    "r8_algorithm_provenance",
}
R8_VALIDATION_KEYS = {
    "experiment_count",
    "first_experiment_id",
    "last_experiment_id",
    "config_sha256",
    "manifest_sha256",
    "preregistration_commit",
}
R8_ALGORITHM_KEYS = {
    "analysis_commit",
    "r8_analyzer_sha256",
    "eos_analyzer_sha256",
    "result_parser_sha256",
}
OBJECT_HEADER = (
    "pid",
    "role",
    "rank",
    "mapped_path",
    "loaded_realpath",
    "loaded_sha256",
    "classification",
)
FROZEN_IMPLEMENTATION_PATHS = (
    "scripts/generate_s1_mpi_prefix_equivalence.py",
    "scripts/validate_s1_mpi_prefix_equivalence.py",
    "scripts/run_s1_mpi_prefix_equivalence.sh",
    "scripts/mpi_prefix_audit_launcher.py",
    "scripts/analyze_s1_mpi_prefix_equivalence.py",
    "scripts/s1_mpi_prefix_equivalence_common.py",
    "scripts/run_s1_single.sh",
    "scripts/parse_s1_single.py",
    "scripts/analyze_s1_non_equilibrium.py",
    "scripts/analyze_s1_eos.py",
)


def _same_path(left: Path, right: Path) -> bool:
    return left.resolve(strict=False) == right.resolve(strict=False)


def _expect_keys(payload: dict, expected: set[str], label: str, errors: list[str]) -> None:
    actual = set(payload)
    if actual != expected:
        errors.append(
            f"{label} keys differ: missing={sorted(expected - actual)} "
            f"unexpected={sorted(actual - expected)}"
        )


def _check_hash(path: Path, expected: str, label: str, errors: list[str]) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", str(expected)):
        errors.append(f"{label}: invalid registered SHA-256")
    elif not path.is_file() or path.is_symlink():
        errors.append(f"{label}: missing or symbolic-link file {path}")
    elif sha256(path) != expected:
        errors.append(f"{label}: SHA-256 mismatch")


def _git_bytes(project_root: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(project_root), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"git {' '.join(arguments)} failed: {detail}")
    return completed.stdout


def _preregistration_commit(
    project_root: Path, config_path: Path, manifest_path: Path
) -> str:
    relative_paths = []
    for path in (config_path, manifest_path):
        try:
            relative_paths.append(path.relative_to(project_root).as_posix())
        except ValueError as error:
            raise ValueError(f"formal frozen file is outside project: {path}") from error
    commits = []
    for relative in relative_paths:
        output = _git_bytes(
            project_root,
            "log",
            "--diff-filter=A",
            "--format=%H",
            "--",
            relative,
        ).decode("ascii")
        values = [line for line in output.splitlines() if line]
        if len(values) != 1:
            raise ValueError(
                f"expected exactly one preregistration addition commit for {relative}, "
                f"found {len(values)}"
            )
        commits.append(values[0])
    if len(set(commits)) != 1:
        raise ValueError("config and manifest were not added in one preregistration commit")
    commit = commits[0]
    for relative, path in zip(relative_paths, (config_path, manifest_path)):
        frozen = _git_bytes(project_root, "cat-file", "blob", f"{commit}:{relative}")
        if frozen != path.read_bytes():
            raise ValueError(
                f"{relative} differs byte-for-byte from preregistration commit {commit}"
            )
    return commit


def _run_commit_chain_failure(
    project_root: Path, experiment_id: str, code_commit: object
) -> str | None:
    if not isinstance(code_commit, str) or not re.fullmatch(r"[0-9a-f]{40,64}", code_commit):
        return f"{experiment_id}: invalid code_commit provenance"
    relative = f"runs/{experiment_id}/experiment_metadata.json"
    output = _git_bytes(
        project_root,
        "log",
        "--diff-filter=A",
        "--format=%H",
        "--",
        relative,
    ).decode("ascii")
    additions = [line for line in output.splitlines() if line]
    if len(additions) != 1:
        return f"{experiment_id}: expected one run addition commit, found {len(additions)}"
    parent = _git_bytes(project_root, "rev-parse", f"{additions[0]}^").decode("ascii").strip()
    if parent != code_commit:
        return (
            f"{experiment_id}: experiment code_commit {code_commit} is not the run "
            f"commit parent {parent}"
        )
    ancestor = subprocess.run(
        ["git", "-C", str(project_root), "merge-base", "--is-ancestor", code_commit, "HEAD"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if ancestor.returncode != 0:
        return f"{experiment_id}: code_commit is not an ancestor of HEAD"
    return None


def _validate_runtime_audit_evidence(
    run_directory: Path,
    runtime: dict,
    audit_spec: dict,
    audit: dict,
    errors: list[str],
    prefix: str,
) -> list[Path]:
    objects_path = run_directory / "mpi_runtime_audit" / "objects.tsv"
    trace_directory = run_directory / "mpi_runtime_audit" / "strace"
    evidence_paths = [objects_path]
    try:
        with objects_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            if tuple(reader.fieldnames or ()) != OBJECT_HEADER:
                errors.append(f"{prefix} invalid runtime object TSV header")
                object_rows = []
            else:
                object_rows = list(reader)
    except FileNotFoundError:
        errors.append(f"{prefix} missing runtime object TSV")
        object_rows = []

    processes = audit.get("processes")
    if not isinstance(processes, list):
        errors.append(f"{prefix} runtime audit processes must be a list")
        processes = []
    process_by_pid: dict[int, dict] = {}
    valid_processes = []
    for process in processes:
        if not isinstance(process, dict):
            errors.append(f"{prefix} runtime process record must be an object")
            continue
        valid_processes.append(process)
        try:
            pid = int(process["pid"])
            role = str(process["role"])
            rank = process.get("rank")
            rank = None if rank is None else int(rank)
            executable = Path(process["executable_realpath"])
            executable_hash = str(process["executable_sha256"])
        except (KeyError, TypeError, ValueError) as error:
            errors.append(f"{prefix} invalid runtime process record: {error}")
            continue
        if pid in process_by_pid:
            errors.append(f"{prefix} duplicate runtime process PID {pid}")
        process_by_pid[pid] = process
        if role not in {"launcher", "rank", "support"}:
            errors.append(f"{prefix} invalid runtime process role {role}")
        _check_hash(executable, executable_hash, f"{prefix} process {pid} executable", errors)
        if role == "launcher" and not _same_path(
            executable, Path(runtime["launcher_path"])
        ):
            errors.append(f"{prefix} launcher executable is not frozen final launcher")
        if role == "rank" and not _same_path(executable, Path(runtime["abacus_path"])):
            errors.append(f"{prefix} rank {rank} executable is not frozen ABACUS")
    launcher_rows = [row for row in valid_processes if row.get("role") == "launcher"]
    rank_rows = [row for row in valid_processes if row.get("role") == "rank"]
    try:
        rank_values = sorted(int(row["rank"]) for row in rank_rows)
    except (KeyError, TypeError, ValueError):
        rank_values = []
    if len(launcher_rows) != 1:
        errors.append(f"{prefix} raw process evidence does not contain one launcher")
    if len(rank_rows) != audit_spec["rank_count"] or rank_values != list(
        range(audit_spec["rank_count"])
    ):
        errors.append(f"{prefix} raw process evidence does not contain exactly four ranks")
    for process in [*launcher_rows, *rank_rows]:
        if int(process.get("mapped_object_count", 0)) <= 0:
            errors.append(
                f"{prefix} target process {process.get('pid')} has no captured maps"
            )

    seen_objects: set[tuple[int, str]] = set()
    recomputed_counts = {"old_prefix": 0, "unexpected": 0, "transient_system": 0}
    object_counts_by_pid: dict[int, int] = {}
    for row in object_rows:
        try:
            pid = int(row["pid"])
            mapped = Path(row["mapped_path"])
            loaded = Path(row["loaded_realpath"])
        except (KeyError, TypeError, ValueError) as error:
            errors.append(f"{prefix} invalid mapped-object record: {error}")
            continue
        key = (pid, str(mapped))
        if key in seen_objects:
            errors.append(f"{prefix} duplicate mapped-object record {key}")
        seen_objects.add(key)
        object_counts_by_pid[pid] = object_counts_by_pid.get(pid, 0) + 1
        process = process_by_pid.get(pid)
        if process is None:
            errors.append(f"{prefix} mapped object refers to unknown PID {pid}")
        else:
            expected_rank = "" if process.get("rank") is None else str(process.get("rank"))
            if row.get("role") != str(process.get("role")) or row.get("rank", "") != expected_rank:
                errors.append(f"{prefix} mapped object PID {pid} role/rank mismatch")
        expected_loaded = mapped.resolve(strict=False)
        if loaded != expected_loaded:
            errors.append(f"{prefix} mapped object realpath mismatch: {mapped}")
        classification = classify_mapping(
            mapped,
            loaded,
            Path(runtime["old_prefix"]),
            Path(runtime["recovery_root"]),
            [Path(value) for value in audit_spec["system_mapping_roots"]],
        )
        if row.get("classification") != classification:
            errors.append(f"{prefix} mapped object classification mismatch: {mapped}")
        if classification in recomputed_counts:
            recomputed_counts[classification] += 1
        if classification == "recovery_runtime" and not row.get("loaded_sha256"):
            errors.append(f"{prefix} recovery mapping lacks SHA-256: {loaded}")
        if loaded.is_file() and classification != "transient_system":
            registered_hash = row.get("loaded_sha256", "")
            if not re.fullmatch(r"[0-9a-f]{64}", registered_hash):
                errors.append(f"{prefix} mapped regular file lacks SHA-256: {loaded}")
            elif sha256(loaded) != registered_hash:
                errors.append(f"{prefix} mapped object SHA-256 mismatch: {loaded}")
    for pid, process in process_by_pid.items():
        if process.get("mapped_object_count") != object_counts_by_pid.get(pid, 0):
            errors.append(f"{prefix} process {pid} mapped-object count differs from TSV")
    for process in [*launcher_rows, *rank_rows]:
        executable = Path(str(process.get("executable_realpath", ""))).resolve(
            strict=False
        )
        if not any(
            row.get("pid") == str(process.get("pid"))
            and Path(row.get("loaded_realpath", "")).resolve(strict=False) == executable
            for row in object_rows
        ):
            errors.append(
                f"{prefix} target process {process.get('pid')} executable map was not captured"
            )
    if len(object_rows) != audit.get("mapped_object_count"):
        errors.append(f"{prefix} mapped object count differs from TSV")
    if recomputed_counts["old_prefix"] != audit.get("old_prefix_mapped_object_count"):
        errors.append(f"{prefix} old-prefix mapping count differs from TSV")
    if recomputed_counts["unexpected"] != audit.get("unexpected_mapped_object_count"):
        errors.append(f"{prefix} unexpected mapping count differs from TSV")
    if recomputed_counts["transient_system"] != audit.get(
        "transient_system_mapped_object_count"
    ):
        errors.append(f"{prefix} transient-system mapping count differs from TSV")

    traces = sorted(trace_directory.glob("trace*"))
    evidence_paths.extend(path for path in traces if path.is_file())
    trace_lines: list[str] = []
    for path in traces:
        trace_lines.extend(
            path.read_text(encoding="utf-8", errors="replace").splitlines()
        )
    reparsed = parse_strace_lines(
        trace_lines,
        Path(runtime["old_prefix"]),
        Path(audit_spec["allowed_failed_probe_path"]),
        audit_spec["allowed_failed_probe_errno"],
    )
    for key, value in reparsed.items():
        if audit.get(key) != value:
            errors.append(f"{prefix} runtime audit {key} differs from raw strace")
    execve_paths = parse_execve_paths(trace_lines)
    if audit.get("observed_execve_realpaths") != execve_paths:
        errors.append(f"{prefix} runtime audit execve paths differ from raw strace")
    invocation_observed = str(Path(runtime["mpirun_path"]).resolve()) in execve_paths
    launcher_observed = str(Path(runtime["launcher_path"]).resolve()) in execve_paths
    if audit.get("mpirun_invocation_execve_observed") != invocation_observed:
        errors.append(f"{prefix} mpirun invocation execve claim differs from raw strace")
    if audit.get("launcher_execve_observed") != launcher_observed:
        errors.append(f"{prefix} launcher execve claim differs from raw strace")
    command = audit.get("command")
    if not isinstance(command, list) or not command or not _same_path(
        Path(str(command[0])), Path(runtime["mpirun_path"])
    ):
        errors.append(f"{prefix} runtime command did not invoke frozen mpirun")
    return evidence_paths


def validate_replay_run(
    project_root: Path,
    config: dict,
    row: dict[str, str],
    *,
    require_committed: bool,
) -> list[str]:
    experiment_id = row["replay_experiment_id"]
    prefix = f"{experiment_id}:"
    errors: list[str] = []
    run_directory = project_root / "runs" / experiment_id
    source_directory = path_from_project(project_root, row["input_directory"])
    if not run_directory.is_dir() or run_directory.is_symlink():
        return [f"{prefix} missing replay run directory"]
    required = {
        "INPUT": run_directory / "INPUT",
        "STRU": run_directory / "STRU",
        "KPT": run_directory / "KPT",
        "input_metadata.json": run_directory / "input_metadata.json",
        "experiment_metadata.json": run_directory / "experiment_metadata.json",
        "result.json": run_directory / "result.json",
        "INPUT_SHA256SUMS": run_directory / "INPUT_SHA256SUMS",
        row["pseudopotential"]: run_directory / row["pseudopotential"],
        "mpi_runtime_audit/audit.json": run_directory
        / "mpi_runtime_audit"
        / "audit.json",
        "mpi_runtime_audit/objects.tsv": run_directory
        / "mpi_runtime_audit"
        / "objects.tsv",
    }
    for name, path in required.items():
        if not path.is_file() or path.is_symlink():
            errors.append(f"{prefix} missing or symbolic-link run artifact {name}")

    try:
        source_input = (source_directory / "INPUT").read_bytes()
        if required["INPUT"].read_bytes() != normalized_run_input(source_input):
            errors.append(f"{prefix} archived INPUT differs beyond pseudo_dir normalization")
        for name in ("STRU", "KPT"):
            if required[name].read_bytes() != (source_directory / name).read_bytes():
                errors.append(f"{prefix} archived {name} differs byte-for-byte from source")
        if required["input_metadata.json"].read_bytes() != (
            source_directory / "metadata.json"
        ).read_bytes():
            errors.append(f"{prefix} archived metadata differs byte-for-byte from source")
        if required[row["pseudopotential"]].read_bytes() != (
            project_root / "assets" / "pseudo" / row["pseudopotential"]
        ).read_bytes():
            errors.append(f"{prefix} archived pseudopotential differs from frozen asset")
    except (FileNotFoundError, ValueError) as error:
        errors.append(f"{prefix} replay input comparison failed: {error}")

    try:
        metadata, log_path, result = reparse_run(run_directory)
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        errors.append(f"{prefix} raw-log reparse failed: {error}")
        log_path = None
        result = {}
        metadata = {}
    if not result.get("converged"):
        errors.append(f"{prefix} replay did not converge")
    if metadata.get("solver") != row["solver"]:
        errors.append(f"{prefix} replay solver differs from manifest")
    errors.extend(
        f"{prefix} {failure}"
        for failure in _checksum_failures(run_directory, row["pseudopotential"])
    )

    runtime = config["runtime"]
    audit_launcher = project_root / "scripts" / "mpi_prefix_audit_launcher.py"
    try:
        experiment_metadata = json.loads(
            required["experiment_metadata.json"].read_text(encoding="utf-8")
        )
    except (FileNotFoundError, json.JSONDecodeError) as error:
        errors.append(f"{prefix} invalid experiment metadata: {error}")
        experiment_metadata = {}
    expected_metadata = {
        "experiment_id": experiment_id,
        "mpi_ranks": 4,
        "abacus_path": runtime["abacus_path"],
        "abacus_sha256": runtime["abacus_sha256"],
        "mpirun_path": runtime["mpirun_path"],
        "mpirun_sha256": runtime["mpirun_sha256"],
        "mpirun_invocation_path": str(audit_launcher),
        "mpirun_invocation_sha256": sha256(audit_launcher),
        **runtime["prefix_environment"],
        "worktree_dirty": False,
    }
    for key, expected in expected_metadata.items():
        if experiment_metadata.get(key) != expected:
            errors.append(f"{prefix} experiment metadata {key} mismatch")

    audit_path = required["mpi_runtime_audit/audit.json"]
    try:
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as error:
        errors.append(f"{prefix} invalid MPI runtime audit: {error}")
        audit = {}
    audit_spec = config["runtime_audit"]
    audit_expected = {
        "status": "accepted",
        "old_prefix": runtime["old_prefix"],
        "recovery_prefix": runtime["recovery_prefix"],
        "observed_launcher_count": audit_spec["launcher_count"],
        "observed_ranks": list(range(audit_spec["rank_count"])),
        "old_prefix_mapped_object_count": 0,
        "unexpected_mapped_object_count": 0,
        "expected_mpirun_path": runtime["mpirun_path"],
        "expected_mpirun_sha256": runtime["mpirun_sha256"],
        "expected_launcher_path": runtime["launcher_path"],
        "expected_launcher_sha256": runtime["launcher_sha256"],
        "expected_abacus_path": runtime["abacus_path"],
        "expected_abacus_sha256": runtime["abacus_sha256"],
        "launcher_executable_mismatch_count": 0,
        "rank_executable_mismatch_count": 0,
        "target_process_empty_maps_count": 0,
        "target_process_missing_executable_sha_count": 0,
        "target_executable_mapping_missing_count": 0,
        "mpirun_invocation_execve_observed": True,
        "launcher_execve_observed": True,
        "unhashed_regular_mapped_object_count": 0,
        "unverifiable_recovery_mapped_object_count": 0,
        "ld_library_path": audit_spec["required_ld_library_path"],
        "ld_preload": None,
        "file_trace_status": "completed",
        "old_prefix_access_attempt_count": audit_spec[
            "allowed_failed_probe_expected_count_per_run"
        ],
        "old_prefix_successful_access_count": 0,
        "allowed_failed_probe_count": audit_spec[
            "allowed_failed_probe_expected_count_per_run"
        ],
        "other_old_prefix_attempt_count": 0,
    }
    for key, expected in audit_expected.items():
        if audit.get(key) != expected:
            errors.append(f"{prefix} runtime audit {key} mismatch")
    if audit.get("allowed_failed_probe_path") != audit_spec["allowed_failed_probe_path"]:
        errors.append(f"{prefix} runtime audit failed-probe path mismatch")
    if audit.get("allowed_failed_probe_errno") != audit_spec["allowed_failed_probe_errno"]:
        errors.append(f"{prefix} runtime audit failed-probe errno mismatch")

    evidence_paths = _validate_runtime_audit_evidence(
        run_directory,
        runtime,
        audit_spec,
        audit,
        errors,
        prefix,
    )

    tracked = [path for path in required.values() if path.is_file()]
    if log_path is not None:
        tracked.append(log_path)
    trace_directory = run_directory / "mpi_runtime_audit" / "strace"
    if audit_spec["file_trace_required"]:
        traces = sorted(trace_directory.glob("trace*"))
        if not traces:
            errors.append(f"{prefix} required strace files are missing")
    tracked.extend(path for path in evidence_paths if path.is_file())
    if require_committed:
        errors.extend(
            f"{prefix} {failure}"
            for failure in require_tracked_at_head(project_root, tracked)
        )
        try:
            commit_failure = _run_commit_chain_failure(
                project_root, experiment_id, experiment_metadata.get("code_commit")
            )
        except ValueError as error:
            commit_failure = f"{experiment_id}: cannot validate run commit chain: {error}"
        if commit_failure:
            errors.append(commit_failure)
    return errors


def validate(
    project_root: Path,
    config_path: Path,
    manifest_path: Path,
    *,
    require_committed: bool = False,
    check_run_ids: tuple[str, ...] = (),
) -> dict:
    project_root = project_root.resolve()
    config_path = config_path.resolve()
    manifest_path = manifest_path.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    rows = read_tsv(manifest_path)
    errors: list[str] = []
    _expect_keys(config, TOP_LEVEL_KEYS, "config", errors)
    if config.get("schema_version") != 1:
        errors.append("schema_version must equal 1")
    if config.get("status") != "mpi_prefix_equivalence_frozen":
        errors.append("config is not a formally frozen MPI equivalence protocol")
    if config.get("protocol_revision") != PROTOCOL_REVISION:
        errors.append("protocol revision mismatch")
    if not re.fullmatch(r"[0-9a-f]{40}", str(config.get("generated_from_commit", ""))):
        errors.append("generated_from_commit must be a Git SHA-1")
    if config.get("rank_count") != 4:
        errors.append("rank_count must equal 4")
    if config.get("experiment_id_block") != {
        "date": "20260805",
        "start_sequence": 113,
        "end_sequence": 118,
    }:
        errors.append("experiment ID block is not exactly 113-118")

    runtime = config.get("runtime", {})
    audit_spec = config.get("runtime_audit", {})
    if isinstance(runtime, dict):
        _expect_keys(runtime, RUNTIME_KEYS, "runtime", errors)
    else:
        errors.append("runtime must be an object")
        runtime = {}
    if isinstance(audit_spec, dict):
        _expect_keys(audit_spec, AUDIT_KEYS, "runtime_audit", errors)
    else:
        errors.append("runtime_audit must be an object")
        audit_spec = {}

    recovery_prefix = Path(str(runtime.get("recovery_prefix", "")))
    recovery_root = Path(str(runtime.get("recovery_root", "")))
    old_prefix = Path(str(runtime.get("old_prefix", "")))
    if not recovery_prefix.is_absolute() or not recovery_root.is_absolute():
        errors.append("recovery root and prefix must be absolute")
    if not old_prefix.is_absolute() or _same_path(old_prefix, recovery_prefix):
        errors.append("old prefix must be a distinct absolute path")
    prefixes = runtime.get("prefix_environment")
    if prefixes != {
        "OPAL_PREFIX": str(recovery_prefix),
        "PRTE_PREFIX": str(recovery_prefix),
        "PMIX_PREFIX": str(recovery_prefix),
    }:
        errors.append("OPAL_PREFIX/PRTE_PREFIX/PMIX_PREFIX must all equal recovery_prefix")
    abacus = Path(str(runtime.get("abacus_path", "")))
    mpirun = Path(str(runtime.get("mpirun_path", "")))
    launcher = Path(str(runtime.get("launcher_path", "")))
    _check_hash(abacus, str(runtime.get("abacus_sha256", "")), "ABACUS", errors)
    _check_hash(mpirun, str(runtime.get("mpirun_sha256", "")), "mpirun", errors)
    _check_hash(
        launcher, str(runtime.get("launcher_sha256", "")), "final MPI launcher", errors
    )
    if not is_within(abacus, recovery_root):
        errors.append("ABACUS resolves outside recovery_root")
    if not is_within(mpirun, recovery_prefix):
        errors.append("mpirun resolves outside recovery_prefix")
    if not is_within(launcher, recovery_prefix):
        errors.append("final MPI launcher resolves outside recovery_prefix")
    expected_audit = {
        "launcher_count": 1,
        "rank_count": 4,
        "old_prefix_mapped_object_count_max": 0,
        "unexpected_mapped_object_count_max": 0,
        "file_trace_required": True,
        "old_prefix_successful_access_count_max": 0,
        "allowed_failed_probe_path": str(old_prefix / "classid"),
        "allowed_failed_probe_errno": "ENOENT",
        "allowed_failed_probe_expected_count_per_run": 2,
        "other_old_prefix_attempt_count_max": 0,
        "clean_environment_required": True,
        "required_ld_library_path": str(recovery_prefix / "lib"),
        "ld_preload_must_be_unset": True,
        "transient_mapping_patterns": list(TRANSIENT_MAPPING_PATTERNS),
        "system_mapping_roots": ["/usr", "/lib", "/lib64", "/dev", "/proc", "/sys"],
    }
    if audit_spec != expected_audit:
        errors.append("runtime_audit fields differ from the registered hard gates")

    acceptance = config.get("acceptance")
    expected_acceptance = {
        "max_absolute_energy_difference_mev_per_atom": 0.1,
        "max_absolute_pressure_difference_gpa": 0.02,
        "threshold_comparison": "strict_less_than",
        "six_point_closure_tiers": ["storage_exact", "storage_resolution_equal"],
        "scientific_tolerance_only_action": "expand_to_registered_eos_endpoints",
        "r8_v100_replacement_must_preserve_conclusion": True,
    }
    if acceptance != expected_acceptance:
        errors.append("acceptance fields differ from the registered strict protocol")

    source = config.get("source", {})
    if isinstance(source, dict):
        _expect_keys(source, SOURCE_KEYS, "source", errors)
    else:
        errors.append("source must be an object")
        source = {}
    for label in ("r8_config", "r8_manifest", "r8_summary"):
        path_key = f"{label}_path"
        hash_key = f"{label}_sha256"
        if path_key not in source or hash_key not in source:
            errors.append(f"source is missing {label} path/hash")
            continue
        _check_hash(
            path_from_project(project_root, source[path_key]),
            str(source[hash_key]),
            label,
            errors,
        )
    if source.get("r8_status") != "accepted":
        errors.append("registered S1-R8 status is not accepted")
    r8_manifest_validation = source.get("r8_manifest_validation")
    if not isinstance(r8_manifest_validation, dict):
        errors.append("source r8_manifest_validation must be an object")
    else:
        _expect_keys(
            r8_manifest_validation,
            R8_VALIDATION_KEYS,
            "source r8_manifest_validation",
            errors,
        )
        expected_r8_validation = {
            "experiment_count": 42,
            "first_experiment_id": "S1-20260805-071",
            "last_experiment_id": "S1-20260805-112",
            "config_sha256": source.get("r8_config_sha256"),
            "manifest_sha256": source.get("r8_manifest_sha256"),
        }
        for key, expected in expected_r8_validation.items():
            if r8_manifest_validation.get(key) != expected:
                errors.append(f"source R8 validation {key} mismatch")
        if not re.fullmatch(
            r"[0-9a-f]{40,64}",
            str(r8_manifest_validation.get("preregistration_commit", "")),
        ):
            errors.append("source R8 preregistration commit is invalid")
    r8_algorithm = source.get("r8_algorithm_provenance")
    if not isinstance(r8_algorithm, dict):
        errors.append("source r8_algorithm_provenance must be an object")
    else:
        _expect_keys(
            r8_algorithm,
            R8_ALGORITHM_KEYS,
            "source r8_algorithm_provenance",
            errors,
        )
        algorithm_files = {
            "r8_analyzer_sha256": project_root
            / "scripts"
            / "analyze_s1_non_equilibrium.py",
            "eos_analyzer_sha256": project_root / "scripts" / "analyze_s1_eos.py",
            "result_parser_sha256": project_root / "scripts" / "parse_s1_single.py",
        }
        for key, path in algorithm_files.items():
            _check_hash(path, str(r8_algorithm.get(key, "")), key, errors)
        if not re.fullmatch(
            r"[0-9a-f]{40,64}", str(r8_algorithm.get("analysis_commit", ""))
        ):
            errors.append("source R8 analysis commit is invalid")
    try:
        r8_summary_path = path_from_project(project_root, source["r8_summary_path"])
        r8_summary = json.loads(r8_summary_path.read_text(encoding="utf-8"))
        if r8_summary.get("s1_r8_status") != "accepted":
            errors.append("current frozen S1-R8 summary is not accepted")
        for key, expected in (
            ("expected_calculations", 42),
            ("selected_calculations", 42),
            ("accepted_comparisons", 6),
        ):
            if r8_summary.get(key) != expected:
                errors.append(f"current frozen S1-R8 summary {key} mismatch")
        r8_summary_provenance = r8_summary.get("analysis_provenance", {})
        if isinstance(r8_algorithm, dict):
            expected_algorithm_provenance = {
                "analyzer_code_commit": r8_algorithm.get("analysis_commit"),
                "analyzer_script_sha256": r8_algorithm.get("r8_analyzer_sha256"),
                "result_parser_script_sha256": r8_algorithm.get("result_parser_sha256"),
            }
            for key, expected in expected_algorithm_provenance.items():
                if r8_summary_provenance.get(key) != expected:
                    errors.append(f"S1-R8 summary algorithm provenance {key} mismatch")
        current_series_status = {
            key: value.get("status")
            for key, value in r8_summary.get("series", {}).items()
            if key in source.get("r8_series_status", {})
        }
        if current_series_status != source.get("r8_series_status"):
            errors.append("S1-R8 series conclusions differ from registration")
    except (KeyError, FileNotFoundError, json.JSONDecodeError) as error:
        errors.append(f"cannot read frozen S1-R8 summary: {error}")

    if not _same_path(
        path_from_project(project_root, str(config.get("manifest_path", ""))),
        manifest_path,
    ):
        errors.append("config manifest_path does not identify the supplied manifest")
    config_digest = sha256(config_path)
    if len(rows) != len(FIXED_PAIRS):
        errors.append(f"manifest must contain exactly {len(FIXED_PAIRS)} rows")
    if [row["replay_experiment_id"] for row in rows] != [pair[0] for pair in FIXED_PAIRS]:
        errors.append("manifest rows must be ordered exactly from S1-20260805-113 to 118")
    if config.get("mappings") != [
        {
            "replay_experiment_id": replay,
            "reference_experiment_id": reference,
            "material": material,
            "series_id": series,
            "input_directory": next(
                (
                    row["input_directory"]
                    for row in rows
                    if row["replay_experiment_id"] == replay
                ),
                None,
            ),
        }
        for replay, reference, material, series in FIXED_PAIRS
    ]:
        errors.append("config mappings differ from exact 113->074 ... 118->109 mapping")

    r8_manifest_path = path_from_project(project_root, str(source.get("r8_manifest_path", "")))
    try:
        r8_by_id = read_r8_manifest(r8_manifest_path)
    except (FileNotFoundError, KeyError, ValueError) as error:
        errors.append(f"cannot read S1-R8 manifest: {error}")
        r8_by_id = {}
    expected_by_replay = {pair[0]: pair for pair in FIXED_PAIRS}
    seen: set[str] = set()
    frozen_paths = [config_path, manifest_path]
    for index, row in enumerate(rows):
        replay_id = row["replay_experiment_id"]
        pair = expected_by_replay.get(replay_id)
        if pair is None:
            errors.append(f"row {index + 1}: replay ID is outside fixed mapping")
            continue
        if replay_id in seen:
            errors.append(f"duplicate replay ID: {replay_id}")
        seen.add(replay_id)
        expected_replay, reference_id, material, series_id = pair
        if row["reference_experiment_id"] != reference_id:
            errors.append(f"{replay_id}: reference mapping mismatch")
        if row["material"] != material or row["series_id"] != series_id:
            errors.append(f"{replay_id}: material/series mapping mismatch")
        if row["config_sha256"] != config_digest:
            errors.append(f"{replay_id}: config SHA-256 mismatch")
        r8_row = r8_by_id.get(reference_id)
        if r8_row is None:
            errors.append(f"{replay_id}: reference missing from S1-R8 manifest")
        else:
            if row["input_directory"] != r8_row.get("input_directory"):
                errors.append(f"{replay_id}: input_directory is not the exact R8 value")
            if r8_row.get("material") != material or r8_row.get("series_id") != series_id:
                errors.append(f"{replay_id}: R8 reference material/series mismatch")
            try:
                if round(float(r8_row.get("volume_ratio", "nan")), 12) != 1.0:
                    errors.append(f"{replay_id}: R8 reference is not V/V0=1.0")
            except ValueError:
                errors.append(f"{replay_id}: invalid R8 volume ratio")

        input_directory = path_from_project(project_root, row["input_directory"])
        registered_hashes = {
            "INPUT": row["input_sha256"],
            "STRU": row["stru_sha256"],
            "KPT": row["kpt_sha256"],
            "metadata.json": row["metadata_sha256"],
        }
        for name in REQUIRED_SOURCE_FILES:
            source_path = input_directory / name
            _check_hash(source_path, registered_hashes[name], f"{replay_id}:{name}", errors)
            frozen_paths.append(source_path)
        try:
            metadata = json.loads(
                (input_directory / "metadata.json").read_text(encoding="utf-8")
            )
            if metadata.get("solver") != row["solver"]:
                errors.append(f"{replay_id}: solver differs from source metadata")
            if metadata.get("pseudopotential") != row["pseudopotential"]:
                errors.append(f"{replay_id}: pseudopotential basename mismatch")
            if metadata.get("material") != material or metadata.get("series_id") != series_id:
                errors.append(f"{replay_id}: source metadata mapping mismatch")
        except (FileNotFoundError, json.JSONDecodeError) as error:
            errors.append(f"{replay_id}: invalid source metadata: {error}")
        pseudo_path = project_root / "assets" / "pseudo" / row["pseudopotential"]
        _check_hash(
            pseudo_path,
            row["pseudopotential_sha256"],
            f"{replay_id}:pseudopotential",
            errors,
        )
        frozen_paths.append(pseudo_path)
        for path_key, hash_key, label in (
            ("reference_result_path", "reference_result_sha256", "reference result"),
            ("reference_log_path", "reference_log_sha256", "reference log"),
            (
                "reference_experiment_metadata_path",
                "reference_experiment_metadata_sha256",
                "reference experiment metadata",
            ),
        ):
            path = path_from_project(project_root, row[path_key])
            _check_hash(path, row[hash_key], f"{replay_id}:{label}", errors)
            frozen_paths.append(path)
        reference_run = project_root / "runs" / reference_id
        canonical_reference_paths = {
            "reference_result_path": reference_run / "result.json",
            "reference_experiment_metadata_path": reference_run
            / "experiment_metadata.json",
        }
        for key, expected_path in canonical_reference_paths.items():
            if not _same_path(path_from_project(project_root, row[key]), expected_path):
                errors.append(f"{replay_id}: {key} is not the canonical reference artifact")
        try:
            reference_metadata, reference_log, reference_result = reparse_run(reference_run)
            if not reference_result.get("converged"):
                errors.append(f"{replay_id}: reference result did not converge")
            if not _same_path(
                reference_log,
                path_from_project(project_root, row["reference_log_path"]),
            ):
                errors.append(f"{replay_id}: registered reference log path mismatch")
            if (reference_run / "input_metadata.json").read_bytes() != (
                input_directory / "metadata.json"
            ).read_bytes():
                errors.append(f"{replay_id}: reference/source metadata bytes differ")
            if reference_metadata.get("solver") != row["solver"]:
                errors.append(f"{replay_id}: reference solver mismatch")
            reference_experiment_metadata = json.loads(
                (reference_run / "experiment_metadata.json").read_text(encoding="utf-8")
            )
            if r8_row is not None:
                reference_archive_failures = _archive_failures(
                    project_root,
                    r8_row,
                    reference_run,
                    reference_metadata,
                    reference_result,
                )
                errors.extend(
                    f"{replay_id}: reference archive {failure}"
                    for failure in reference_archive_failures
                )
            if reference_experiment_metadata.get("mpi_ranks") != 4:
                errors.append(f"{replay_id}: reference run did not use four MPI ranks")
            if reference_experiment_metadata.get("abacus_sha256") != runtime.get(
                "abacus_sha256"
            ):
                errors.append(f"{replay_id}: reference ABACUS SHA-256 mismatch")
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            errors.append(f"{replay_id}: reference raw-log validation failed: {error}")

    if seen != set(expected_by_replay):
        errors.append(f"manifest replay IDs differ: {sorted(seen)}")
    preregistration_commit = None
    if require_committed:
        errors.extend(require_tracked_at_head(project_root, frozen_paths))
        try:
            preregistration_commit = _preregistration_commit(
                project_root, config_path, manifest_path
            )
            preregistration_parent = _git_bytes(
                project_root, "rev-parse", f"{preregistration_commit}^"
            ).decode("ascii").strip()
            if preregistration_parent != config.get("generated_from_commit"):
                errors.append(
                    "preregistration commit parent differs from generated_from_commit"
                )
            generated_commit = str(config.get("generated_from_commit"))
            for relative in FROZEN_IMPLEMENTATION_PATHS:
                frozen_blob = _git_bytes(
                    project_root, "cat-file", "blob", f"{generated_commit}:{relative}"
                )
                current_path = project_root / relative
                if not current_path.is_file() or current_path.read_bytes() != frozen_blob:
                    errors.append(
                        f"{relative} differs from frozen implementation at {generated_commit}"
                    )
        except ValueError as error:
            errors.append(str(error))
    selected_run_ids = check_run_ids or ()
    if selected_run_ids:
        rows_by_id = {row["replay_experiment_id"]: row for row in rows}
        for experiment_id in selected_run_ids:
            row = rows_by_id.get(experiment_id)
            if row is None:
                errors.append(f"requested run check is outside manifest: {experiment_id}")
                continue
            errors.extend(
                validate_replay_run(
                    project_root,
                    config,
                    row,
                    require_committed=require_committed,
                )
            )
    if errors:
        raise ValueError("S1 MPI-prefix equivalence validation failed:\n- " + "\n- ".join(errors))
    return {
        "protocol_revision": PROTOCOL_REVISION,
        "experiment_count": len(rows),
        "first_experiment_id": rows[0]["replay_experiment_id"],
        "last_experiment_id": rows[-1]["replay_experiment_id"],
        "config_sha256": config_digest,
        "manifest_sha256": sha256(manifest_path),
        "preregistration_commit": preregistration_commit,
        "checked_run_ids": list(selected_run_ids),
    }


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "manifest",
        nargs="?",
        type=Path,
        default=project_root / CANONICAL_MANIFEST_PATH,
    )
    parser.add_argument(
        "--config", type=Path, default=project_root / CANONICAL_CONFIG_PATH
    )
    parser.add_argument("--require-committed", action="store_true")
    parser.add_argument("--check-run", action="append", default=[])
    args = parser.parse_args()
    payload = validate(
        project_root,
        args.config.resolve(),
        args.manifest.resolve(),
        require_committed=args.require_committed,
        check_run_ids=tuple(args.check_run),
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
