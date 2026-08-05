#!/usr/bin/env python3
"""Freeze the six S1-R8 MPI-prefix replay points after references exist."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from analyze_s1_non_equilibrium import _archive_failures
from s1_mpi_prefix_equivalence_common import (
    CANONICAL_CONFIG_PATH,
    CANONICAL_MANIFEST_PATH,
    DEFAULT_R8_SUMMARY_PATH,
    FIXED_PAIRS,
    PROTOCOL_REVISION,
    R8_CONFIG_PATH,
    R8_MANIFEST_PATH,
    REQUIRED_SOURCE_FILES,
    TRANSIENT_MAPPING_PATTERNS,
    atomic_write,
    git_clean,
    is_within,
    path_from_project,
    read_r8_manifest,
    relative_or_absolute,
    render_tsv,
    reparse_run,
    require_tracked_at_head,
    sha256,
)
from validate_s1_non_equilibrium_manifest import validate as validate_r8_manifest


def _resolved_executable(path: Path, label: str) -> Path:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except FileNotFoundError as error:
        raise ValueError(f"missing {label}: {path}") from error
    if not resolved.is_file() or not resolved.stat().st_mode & 0o111:
        raise ValueError(f"{label} is not an executable regular file: {resolved}")
    with resolved.open("rb") as handle:
        elf_magic = handle.read(4)
    if elf_magic != b"\x7fELF":
        raise ValueError(
            f"{label} must resolve directly to its Linux ELF executable for /proc auditing: "
            f"{resolved}"
        )
    return resolved


def _head(project_root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(project_root), "rev-parse", "HEAD"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError(completed.stderr.strip() or "cannot resolve repository HEAD")
    return completed.stdout.strip()


def _git_blob(project_root: Path, commit: str, relative_path: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(project_root), "cat-file", "blob", f"{commit}:{relative_path}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"cannot read {relative_path} at R8 analysis commit: {detail}")
    return completed.stdout


def validate_r8_summary_provenance(
    project_root: Path,
    r8_config_path: Path,
    r8_manifest_path: Path,
    r8_summary: dict,
    r8_manifest_validation: dict,
) -> dict:
    expected_counts = {
        "expected_calculations": 42,
        "selected_calculations": 42,
        "accepted_comparisons": 6,
    }
    for key, expected in expected_counts.items():
        if r8_summary.get(key) != expected:
            raise ValueError(f"S1-R8 summary {key} must equal {expected}")
    provenance = r8_summary.get("analysis_provenance")
    if not isinstance(provenance, dict):
        raise ValueError("S1-R8 summary lacks analysis_provenance")
    commit = str(provenance.get("analyzer_code_commit", ""))
    if not commit or not all(character in "0123456789abcdef" for character in commit):
        raise ValueError("S1-R8 summary has an invalid analyzer commit")
    registered_files = {
        "r8_analyzer_sha256": "scripts/analyze_s1_non_equilibrium.py",
        "eos_analyzer_sha256": "scripts/analyze_s1_eos.py",
        "result_parser_sha256": "scripts/parse_s1_single.py",
    }
    payload = {"analysis_commit": commit}
    for key, relative in registered_files.items():
        blob_digest = hashlib.sha256(_git_blob(project_root, commit, relative)).hexdigest()
        current_path = project_root / relative
        if sha256(current_path) != blob_digest:
            raise ValueError(f"current {relative} differs from R8 analysis commit {commit}")
        payload[key] = blob_digest
    expected_provenance = {
        "analyzer_script_sha256": payload["r8_analyzer_sha256"],
        "result_parser_script_sha256": payload["result_parser_sha256"],
        "config_sha256": sha256(r8_config_path),
        "manifest_sha256": sha256(r8_manifest_path),
        "preregistration_commit": r8_manifest_validation["preregistration_commit"],
    }
    r8_config = json.loads(r8_config_path.read_text(encoding="utf-8"))
    core = r8_config["core_reference"]
    core_summary = path_from_project(project_root, core["summary_path"])
    if sha256(core_summary) != core["summary_sha256"]:
        raise ValueError("S1-R8 core summary differs from its registered SHA-256")
    expected_provenance["core_summary_sha256"] = core["summary_sha256"]
    for key, expected in expected_provenance.items():
        if provenance.get(key) != expected:
            raise ValueError(f"S1-R8 analysis provenance {key} mismatch")
    return payload


def build_frozen_payload(
    project_root: Path,
    recovery_prefix: Path,
    old_prefix: Path,
    abacus: Path,
    mpirun: Path,
    launcher: Path | None,
    r8_config_path: Path,
    r8_manifest_path: Path,
    r8_summary_path: Path,
    output_manifest_path: Path,
    *,
    require_clean_worktree: bool = True,
) -> tuple[dict, list[dict[str, object]], list[Path]]:
    project_root = project_root.resolve()
    if require_clean_worktree and not git_clean(project_root):
        raise ValueError("refusing to freeze MPI replay from a dirty worktree")

    recovery_prefix = recovery_prefix.expanduser()
    if not recovery_prefix.is_absolute():
        raise ValueError("recovery prefix must be an absolute path")
    recovery_prefix = recovery_prefix.resolve(strict=True)
    if not recovery_prefix.is_dir():
        raise ValueError(f"recovery prefix is not a directory: {recovery_prefix}")
    recovery_root = recovery_prefix.parent.resolve()
    old_prefix = old_prefix.expanduser()
    if not old_prefix.is_absolute():
        raise ValueError("old prefix must be an absolute path")
    old_prefix = old_prefix.resolve(strict=False)
    if old_prefix == recovery_prefix:
        raise ValueError("old prefix must be an absolute path different from recovery prefix")
    for value, label in ((abacus, "ABACUS"), (mpirun, "mpirun")):
        if not value.expanduser().is_absolute():
            raise ValueError(f"{label} path must be absolute")
    if launcher is not None and not launcher.expanduser().is_absolute():
        raise ValueError("final MPI launcher path must be absolute")
    abacus = _resolved_executable(abacus, "ABACUS")
    mpirun = _resolved_executable(mpirun, "mpirun")
    launcher = _resolved_executable(
        launcher if launcher is not None else recovery_prefix / "bin" / "prterun",
        "final MPI launcher",
    )
    if not is_within(mpirun, recovery_prefix):
        raise ValueError("mpirun must resolve inside the recovery prefix")
    if not is_within(launcher, recovery_prefix):
        raise ValueError("final MPI launcher must resolve inside the recovery prefix")
    if not is_within(abacus, recovery_root):
        raise ValueError("ABACUS must resolve inside the recovery runtime root")

    for path, label in (
        (r8_config_path, "S1-R8 config"),
        (r8_manifest_path, "S1-R8 manifest"),
        (r8_summary_path, "accepted S1-R8 summary"),
    ):
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"missing or symbolic-link {label}: {path}")

    try:
        r8_manifest_validation = validate_r8_manifest(
            project_root, r8_config_path, r8_manifest_path
        )
    except ValueError as error:
        raise ValueError(f"S1-R8 preregistered manifest validation failed: {error}") from error

    r8_summary = json.loads(r8_summary_path.read_text(encoding="utf-8"))
    if r8_summary.get("s1_r8_status") != "accepted":
        raise ValueError("S1-R8 summary must be accepted before MPI replay can be frozen")
    r8_series = r8_summary.get("series", {})
    expected_series = {f"{material}/{series}" for _, _, material, series in FIXED_PAIRS}
    if not expected_series.issubset(r8_series):
        missing = sorted(expected_series - set(r8_series))
        raise ValueError(f"S1-R8 summary is missing registered series: {missing}")
    if any(r8_series[key].get("status") != "accepted" for key in expected_series):
        raise ValueError("all six S1-R8 source series must be accepted")
    r8_algorithm_provenance = validate_r8_summary_provenance(
        project_root,
        r8_config_path,
        r8_manifest_path,
        r8_summary,
        r8_manifest_validation,
    )

    r8_rows = read_r8_manifest(r8_manifest_path)
    tracked_paths = [r8_config_path, r8_manifest_path, r8_summary_path]
    staged_rows: list[dict[str, object]] = []
    mappings = []
    for replay_id, reference_id, material, series_id in FIXED_PAIRS:
        source_row = r8_rows.get(reference_id)
        if source_row is None:
            raise ValueError(f"S1-R8 manifest is missing reference {reference_id}")
        if source_row.get("material") != material or source_row.get("series_id") != series_id:
            raise ValueError(f"{reference_id}: material/series differs from fixed mapping")
        if round(float(source_row.get("volume_ratio", "nan")), 12) != 1.0:
            raise ValueError(f"{reference_id}: fixed MPI replay source is not V/V0=1.0")

        input_directory_value = str(source_row["input_directory"])
        input_directory = path_from_project(project_root, input_directory_value)
        source_paths = {name: input_directory / name for name in REQUIRED_SOURCE_FILES}
        for name, path in source_paths.items():
            if not path.is_file() or path.is_symlink():
                raise ValueError(f"{reference_id}: missing or symbolic-link source {name}")
        metadata = json.loads(source_paths["metadata.json"].read_text(encoding="utf-8"))
        if sha256(source_paths["metadata.json"]) != source_row.get(
            "input_metadata_sha256"
        ):
            raise ValueError(f"{reference_id}: R8 row input_metadata_sha256 mismatch")
        if metadata.get("material") != material or metadata.get("series_id") != series_id:
            raise ValueError(f"{reference_id}: source metadata does not match fixed mapping")
        pseudopotential = metadata.get("pseudopotential")
        if not isinstance(pseudopotential, str) or Path(pseudopotential).name != pseudopotential:
            raise ValueError(f"{reference_id}: invalid pseudopotential basename")
        pseudo_path = project_root / "assets" / "pseudo" / pseudopotential
        if not pseudo_path.is_file() or pseudo_path.is_symlink():
            raise ValueError(f"{reference_id}: missing or symbolic-link pseudopotential")
        if sha256(pseudo_path) != metadata.get("pseudopotential_sha256"):
            raise ValueError(f"{reference_id}: pseudopotential hash differs from metadata")

        reference_run = project_root / "runs" / reference_id
        try:
            reference_metadata, reference_log, reference_result = reparse_run(reference_run)
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError(f"{reference_id}: reference result is incomplete: {error}") from error
        if not reference_result.get("converged"):
            raise ValueError(f"{reference_id}: reference result did not converge")
        if (reference_run / "input_metadata.json").read_bytes() != source_paths[
            "metadata.json"
        ].read_bytes():
            raise ValueError(f"{reference_id}: run metadata differs byte-for-byte from source")
        reference_experiment_metadata_path = reference_run / "experiment_metadata.json"
        reference_result_path = reference_run / "result.json"
        if not reference_experiment_metadata_path.is_file():
            raise ValueError(f"{reference_id}: missing experiment_metadata.json")
        reference_experiment_metadata = json.loads(
            reference_experiment_metadata_path.read_text(encoding="utf-8")
        )
        archive_failures = _archive_failures(
            project_root,
            source_row,
            reference_run,
            reference_metadata,
            reference_result,
        )
        if archive_failures:
            raise ValueError(
                f"{reference_id}: reference run archive differs from frozen input: "
                + ",".join(archive_failures)
            )
        if reference_experiment_metadata.get("mpi_ranks") != 4:
            raise ValueError(f"{reference_id}: reference run did not use four MPI ranks")
        if reference_experiment_metadata.get("abacus_sha256") != sha256(abacus):
            raise ValueError(
                f"{reference_id}: reference ABACUS bytes differ from replay ABACUS"
            )

        tracked_paths.extend(
            [
                *source_paths.values(),
                pseudo_path,
                reference_run / "input_metadata.json",
                reference_result_path,
                reference_log,
                reference_experiment_metadata_path,
            ]
        )
        staged_rows.append(
            {
                "replay_experiment_id": replay_id,
                "reference_experiment_id": reference_id,
                "input_directory": input_directory_value,
                "material": material,
                "series_id": series_id,
                "solver": str(metadata["solver"]),
                "input_sha256": sha256(source_paths["INPUT"]),
                "stru_sha256": sha256(source_paths["STRU"]),
                "kpt_sha256": sha256(source_paths["KPT"]),
                "metadata_sha256": sha256(source_paths["metadata.json"]),
                "pseudopotential": pseudopotential,
                "pseudopotential_sha256": sha256(pseudo_path),
                "reference_result_path": relative_or_absolute(
                    project_root, reference_result_path
                ),
                "reference_result_sha256": sha256(reference_result_path),
                "reference_log_path": relative_or_absolute(project_root, reference_log),
                "reference_log_sha256": sha256(reference_log),
                "reference_experiment_metadata_path": relative_or_absolute(
                    project_root, reference_experiment_metadata_path
                ),
                "reference_experiment_metadata_sha256": sha256(
                    reference_experiment_metadata_path
                ),
            }
        )
        mappings.append(
            {
                "replay_experiment_id": replay_id,
                "reference_experiment_id": reference_id,
                "material": material,
                "series_id": series_id,
                "input_directory": input_directory_value,
            }
        )

    tracked_failures = require_tracked_at_head(project_root, tracked_paths)
    if tracked_failures:
        raise ValueError(
            "reference/source artifacts are not immutable at HEAD:\n- "
            + "\n- ".join(tracked_failures)
        )

    prefix_environment = {
        "OPAL_PREFIX": str(recovery_prefix),
        "PRTE_PREFIX": str(recovery_prefix),
        "PMIX_PREFIX": str(recovery_prefix),
    }
    config = {
        "schema_version": 1,
        "status": "mpi_prefix_equivalence_frozen",
        "protocol_revision": PROTOCOL_REVISION,
        "generated_from_commit": _head(project_root),
        "experiment_id_block": {
            "date": "20260805",
            "start_sequence": 113,
            "end_sequence": 118,
        },
        "rank_count": 4,
        "source": {
            "r8_config_path": relative_or_absolute(project_root, r8_config_path),
            "r8_config_sha256": sha256(r8_config_path),
            "r8_manifest_path": relative_or_absolute(project_root, r8_manifest_path),
            "r8_manifest_sha256": sha256(r8_manifest_path),
            "r8_summary_path": relative_or_absolute(project_root, r8_summary_path),
            "r8_summary_sha256": sha256(r8_summary_path),
            "r8_status": "accepted",
            "r8_series_status": {
                key: str(r8_series[key]["status"]) for key in sorted(expected_series)
            },
            "r8_manifest_validation": r8_manifest_validation,
            "r8_algorithm_provenance": r8_algorithm_provenance,
        },
        "runtime": {
            "recovery_root": str(recovery_root),
            "recovery_prefix": str(recovery_prefix),
            "old_prefix": str(old_prefix),
            "abacus_path": str(abacus),
            "abacus_sha256": sha256(abacus),
            "mpirun_path": str(mpirun),
            "mpirun_sha256": sha256(mpirun),
            "launcher_path": str(launcher),
            "launcher_sha256": sha256(launcher),
            "prefix_environment": prefix_environment,
        },
        "runtime_audit": {
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
        },
        "acceptance": {
            "max_absolute_energy_difference_mev_per_atom": 0.1,
            "max_absolute_pressure_difference_gpa": 0.02,
            "threshold_comparison": "strict_less_than",
            "six_point_closure_tiers": [
                "storage_exact",
                "storage_resolution_equal",
            ],
            "scientific_tolerance_only_action": "expand_to_registered_eos_endpoints",
            "r8_v100_replacement_must_preserve_conclusion": True,
        },
        "manifest_path": relative_or_absolute(project_root, output_manifest_path),
        "mappings": mappings,
    }
    return config, staged_rows, tracked_paths


def generate(
    project_root: Path,
    recovery_prefix: Path,
    old_prefix: Path,
    abacus: Path,
    mpirun: Path,
    config_path: Path,
    manifest_path: Path,
    r8_config_path: Path,
    r8_manifest_path: Path,
    r8_summary_path: Path,
    *,
    launcher: Path | None = None,
    require_clean_worktree: bool = True,
) -> dict:
    for output in (config_path, manifest_path):
        if output.exists() or output.is_symlink():
            raise ValueError(f"refusing to overwrite frozen output: {output}")
    config, rows, _ = build_frozen_payload(
        project_root,
        recovery_prefix,
        old_prefix,
        abacus,
        mpirun,
        launcher,
        r8_config_path,
        r8_manifest_path,
        r8_summary_path,
        manifest_path,
        require_clean_worktree=require_clean_worktree,
    )
    config_text = json.dumps(config, indent=2, sort_keys=True) + "\n"
    config_digest = hashlib.sha256(config_text.encode("utf-8")).hexdigest()
    for row in rows:
        row["config_sha256"] = config_digest
    manifest_text = render_tsv(rows)
    atomic_write(config_path, config_text)
    atomic_write(manifest_path, manifest_text)
    return {
        "config_path": str(config_path),
        "config_sha256": config_digest,
        "manifest_path": str(manifest_path),
        "manifest_sha256": sha256(manifest_path),
        "experiment_count": len(rows),
        "first_experiment_id": rows[0]["replay_experiment_id"],
        "last_experiment_id": rows[-1]["replay_experiment_id"],
    }


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Freeze S1-R8 MPI-prefix replay only after all reference artifacts exist."
    )
    parser.add_argument("--recovery-prefix", type=Path, required=True)
    parser.add_argument("--old-prefix", type=Path, required=True)
    parser.add_argument("--abacus", type=Path, required=True)
    parser.add_argument("--mpirun", type=Path, required=True)
    parser.add_argument(
        "--launcher",
        type=Path,
        help="final launcher ELF; defaults to <recovery-prefix>/bin/prterun",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=project_root / CANONICAL_CONFIG_PATH,
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=project_root / CANONICAL_MANIFEST_PATH,
    )
    parser.add_argument(
        "--r8-config", type=Path, default=project_root / R8_CONFIG_PATH
    )
    parser.add_argument(
        "--r8-manifest", type=Path, default=project_root / R8_MANIFEST_PATH
    )
    parser.add_argument(
        "--r8-summary", type=Path, default=project_root / DEFAULT_R8_SUMMARY_PATH
    )
    args = parser.parse_args()
    payload = generate(
        project_root,
        args.recovery_prefix,
        args.old_prefix,
        args.abacus,
        args.mpirun,
        args.config.resolve(),
        args.manifest.resolve(),
        args.r8_config.resolve(),
        args.r8_manifest.resolve(),
        args.r8_summary.resolve(),
        launcher=args.launcher,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
