#!/usr/bin/env python3
"""Validate the frozen S1 G1 electron-number audit and its OF replays."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from generate_s1_electron_number_audit import (
    CONFIG_PATH,
    EXECUTABLE_IMPLEMENTATION_PATHS,
    IMPLEMENTATION_PATHS,
    INPUT_ROOT,
    MANIFEST_PATH,
    R8_CONFIG,
    R7_MANIFEST,
    R8_MANIFEST,
    RUNTIME_CONFIG,
    RUNTIME_MANIFEST,
    CORE_SUMMARY,
    R8_SUMMARY,
    RUNTIME_SUMMARY,
    accepted_summary_registration,
)
from s1_electron_number_common import (
    AUDIT_IDS,
    CUBE_PRECISION,
    ENERGY_LIMIT_MEV_PER_ATOM,
    MANIFEST_FIELDS,
    PILOT_SOURCE_IDS,
    PRESSURE_LIMIT_GPA,
    PRIMARY_IDS,
    PROTOCOL_REVISION,
    RELATIVE_ERROR_LIMIT,
    SUPPLEMENTAL_IDS,
    TARGET_IDS,
    derive_output_input,
    expected_electrons,
    find_single_density,
    find_single_log,
    integrate_cube,
    integrate_reciprocal_restart,
    ordered_of_replay_sources,
    parse_charge_grid,
    parse_input_parameters,
    read_json,
    read_manifest,
    scientific_equivalence,
    sha256,
)
from validate_s1_mpi_prefix_equivalence import (
    _failed_archive_chain_failures,
    validate_failed_replay_run,
    validate_replay_run,
)


def _git(project_root: Path, *args: str, text: bool = True) -> str | bytes:
    output = subprocess.check_output(
        ["git", "-C", str(project_root), *args], text=text
    )
    return output.strip() if text else output


def _relative(project_root: Path, path: Path) -> str:
    return str(path.resolve().relative_to(project_root.resolve()))


def _tracked_failure(project_root: Path, path: Path) -> str | None:
    try:
        relative = _relative(project_root, path)
    except ValueError:
        return f"path is outside project root: {path}"
    result = subprocess.run(
        ["git", "-C", str(project_root), "ls-files", "--error-unmatch", "--", relative],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        return f"path is not tracked: {relative}"
    result = subprocess.run(
        ["git", "-C", str(project_root), "diff", "--quiet", "HEAD", "--", relative]
    )
    if result.returncode != 0:
        return f"path differs from HEAD: {relative}"
    return None


def _introduction_commit(project_root: Path, path: Path) -> str:
    relative = _relative(project_root, path)
    commits = str(
        _git(project_root, "log", "--format=%H", "--diff-filter=A", "--", relative)
    ).splitlines()
    if len(commits) != 1:
        raise ValueError(f"expected one introduction commit for {relative}")
    return commits[0]


def _latest_introduction_commit(project_root: Path, path: Path) -> str:
    relative = _relative(project_root, path)
    commits = str(
        _git(
            project_root,
            "log",
            "--no-renames",
            "--format=%H",
            "--diff-filter=A",
            "-n",
            "1",
            "--",
            relative,
        )
    ).splitlines()
    if len(commits) != 1:
        raise ValueError(f"expected a latest introduction commit for {relative}")
    return commits[0]


def _tree_entries(
    project_root: Path, commit: str, prefix: str
) -> dict[str, tuple[str, str, str]]:
    output = subprocess.check_output(
        ["git", "-C", str(project_root), "ls-tree", "-r", "-z", commit, "--", prefix]
    )
    entries: dict[str, tuple[str, str, str]] = {}
    for record in output.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, object_type, object_id = metadata.decode("ascii").split()
        path = raw_path.decode("utf-8")
        entries[path] = (mode, object_type, object_id)
    return entries


def _commit_changed_paths(project_root: Path, commit: str) -> set[str]:
    output = subprocess.check_output(
        [
            "git",
            "-C",
            str(project_root),
            "diff-tree",
            "--no-commit-id",
            "--no-renames",
            "--name-only",
            "-r",
            "-z",
            commit,
        ]
    )
    return {
        value.decode("utf-8") for value in output.split(b"\0") if value
    }


def _frozen_blob_failure(
    project_root: Path, path: Path, introduction: str
) -> str | None:
    relative = _relative(project_root, path)
    frozen_blob = _git(
        project_root,
        "cat-file",
        "blob",
        f"{introduction}:{relative}",
        text=False,
    )
    if path.read_bytes() != frozen_blob:
        return f"frozen path differs from preregistration blob: {relative}"
    entries = _tree_entries(project_root, introduction, relative)
    if entries.get(relative, (None, None, None))[:2] not in {
        ("100644", "blob"),
        ("100755", "blob"),
    }:
        return f"frozen path is not a regular blob in preregistration: {relative}"
    return None


def _complete_run_tree_failures(
    project_root: Path, run_directory: Path, introduction: str
) -> list[str]:
    prefix = _relative(project_root, run_directory)
    failures: list[str] = []
    if not run_directory.is_dir() or run_directory.is_symlink():
        return [f"{prefix}: run directory is missing or symbolic"]
    introduction_entries = _tree_entries(project_root, introduction, prefix)
    head_entries = _tree_entries(project_root, "HEAD", prefix)
    if not introduction_entries or introduction_entries != head_entries:
        failures.append(f"{prefix}: current Git tree differs from run introduction")
    changed_paths = _commit_changed_paths(project_root, introduction)
    parent = str(_git(project_root, "rev-parse", f"{introduction}^"))
    if _tree_entries(project_root, parent, prefix):
        failures.append(f"{prefix}: run prefix already existed in introduction parent")
    if changed_paths != set(introduction_entries):
        failures.append(f"{prefix}: run introduction commit scope is not exact")
    status = subprocess.check_output(
        [
            "git",
            "-C",
            str(project_root),
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--",
            prefix,
        ]
    )
    if status:
        failures.append(f"{prefix}: run worktree differs from HEAD")
    filesystem_paths: set[str] = set()
    for path in run_directory.rglob("*"):
        if path.is_symlink():
            failures.append(f"{prefix}: run tree contains a symbolic link: {path}")
        elif path.is_file():
            filesystem_paths.add(_relative(project_root, path))
        elif not path.is_dir():
            failures.append(f"{prefix}: run tree contains a special file: {path}")
    if filesystem_paths != set(head_entries):
        failures.append(f"{prefix}: filesystem file set differs from committed run tree")
    if any(mode == "120000" or object_type != "blob" for mode, object_type, _ in head_entries.values()):
        failures.append(f"{prefix}: committed run tree contains a non-regular blob")
    return failures


def _expected_of_sources(rows: list[dict[str, str]]) -> list[str]:
    return list(
        ordered_of_replay_sources(
            {
                row["source_experiment_id"]: row["solver"]
                for row in rows
            }
        )
    )


def _expected_derived_metadata(source: dict, audit_id: str, source_id: str) -> dict:
    payload = dict(source)
    payload["candidate_status"] = "electron_number_output_replay_preregistered"
    payload["electron_number_audit"] = {
        "audit_experiment_id": audit_id,
        "cube_precision": CUBE_PRECISION,
        "only_scientific_input_change": "none_output_control_only",
        "output_control": f"out_chg 1 {CUBE_PRECISION}",
        "protocol_revision": PROTOCOL_REVISION,
        "source_experiment_id": source_id,
    }
    return payload


def _validate_source_row(
    project_root: Path, row: dict[str, str], errors: list[str]
) -> dict[str, object] | None:
    source_id = row["source_experiment_id"]
    prefix = f"{source_id}:"
    source_run = project_root / "runs" / source_id
    try:
        metadata = read_json(source_run / "input_metadata.json")
        result = read_json(source_run / "result.json")
        log = find_single_log(source_run)
        expected, derivation = expected_electrons(source_run)
        if result.get("converged") is not True:
            errors.append(f"{prefix} source result is not converged")
        scalar_expectations = {
            "scope": (
                "primary_baseline"
                if source_id in PRIMARY_IDS
                else "supplemental_runtime_replay"
            ),
            "material": str(metadata.get("material")),
            "series_id": str(
                metadata.get("series_id", metadata.get("scan_axis", ""))
            ),
            "solver": str(metadata.get("solver")),
            "volume_ratio": str(metadata.get("volume_ratio", "")),
            "expected_electrons": format(expected, ".17g"),
            "pseudopotential": str(metadata.get("pseudopotential")),
            "reference_experiment_id": source_id,
            "reference_result_path": str(
                (source_run / "result.json").relative_to(project_root)
            ),
            "reference_result_sha256": sha256(source_run / "result.json"),
            "reference_log_path": str(log.relative_to(project_root)),
            "reference_log_sha256": sha256(log),
        }
        for key, expected_value in scalar_expectations.items():
            if row.get(key) != expected_value:
                errors.append(f"{prefix} manifest {key} mismatch")
        pseudo_name = str(metadata.get("pseudopotential"))
        if Path(pseudo_name).name != pseudo_name:
            errors.append(f"{prefix} pseudopotential name is not a basename")
            return None
        pseudo = source_run / pseudo_name
        if row["pseudopotential_sha256"] != sha256(pseudo):
            errors.append(f"{prefix} pseudopotential SHA-256 mismatch")
        asset_pseudo = project_root / "assets" / "pseudo" / pseudo_name
        if pseudo.read_bytes() != asset_pseudo.read_bytes():
            errors.append(f"{prefix} source pseudopotential differs from frozen asset")

        if row["solver"] == "ksdft":
            source_hashes = {
                "input_sha256": source_run / "INPUT",
                "stru_sha256": source_run / "STRU",
                "kpt_sha256": source_run / "KPT",
                "metadata_sha256": source_run / "input_metadata.json",
            }
            for key, path in source_hashes.items():
                if row[key] != sha256(path):
                    errors.append(f"{prefix} source {key} mismatch")
            density = find_single_density(source_run, "*CHARGE-DENSITY.restart")
            expected_path = str(density.relative_to(project_root))
            if row["density_mode"] != "existing_reciprocal_restart":
                errors.append(f"{prefix} density mode mismatch")
            if row["density_path"] != expected_path:
                errors.append(f"{prefix} density path mismatch")
            if row["source_density_sha256"] != sha256(density):
                errors.append(f"{prefix} density SHA-256 mismatch")
            if any(row[key] for key in ("audit_experiment_id", "input_directory", "derived_suffix")):
                errors.append(f"{prefix} KS row unexpectedly has OF replay fields")
            integration = integrate_reciprocal_restart(
                density, source_run / "STRU", expected
            )
            if not integration["accepted"]:
                errors.append(
                    f"{prefix} certified electron relative error is not strictly below the limit"
                )
        elif row["solver"] == "ofdft":
            audit_id = row["audit_experiment_id"]
            if audit_id not in AUDIT_IDS:
                errors.append(f"{prefix} audit experiment ID is outside the frozen block")
                return None
            expected_input_directory = str(INPUT_ROOT / audit_id)
            expected_suffix = (
                f"g1_ne_{audit_id.rsplit('-', 1)[1]}_from_{source_id.rsplit('-', 1)[1]}"
            )
            if row["input_directory"] != expected_input_directory:
                errors.append(f"{prefix} derived input directory is not canonical")
            if row["derived_suffix"] != expected_suffix:
                errors.append(f"{prefix} derived suffix is not canonical")
            derived = project_root / expected_input_directory
            suffix = expected_suffix
            expected_density = f"runs/{audit_id}/OUT.{suffix}/chg.cube"
            if row["density_mode"] != "high_precision_cube_replay":
                errors.append(f"{prefix} density mode mismatch")
            if row["density_path"] != expected_density or row["source_density_sha256"]:
                errors.append(f"{prefix} future cube registration mismatch")
            expected_input = derive_output_input((source_run / "INPUT").read_bytes(), suffix)
            if (derived / "INPUT").read_bytes() != expected_input:
                errors.append(f"{prefix} derived INPUT differs beyond suffix/out_chg")
            for name in ("STRU", "KPT"):
                if (derived / name).read_bytes() != (source_run / name).read_bytes():
                    errors.append(f"{prefix} derived {name} differs from source")
            derived_metadata = read_json(derived / "metadata.json")
            if derived_metadata != _expected_derived_metadata(metadata, audit_id, source_id):
                errors.append(f"{prefix} derived metadata mismatch")
            input_parameters = parse_input_parameters(derived / "INPUT")
            if input_parameters.get("suffix") != [suffix]:
                errors.append(f"{prefix} derived suffix is not frozen")
            if input_parameters.get("out_chg") != ["1", str(CUBE_PRECISION)]:
                errors.append(f"{prefix} high-precision out_chg is not frozen")
            if input_parameters.get("esolver_type") != ["ofdft"]:
                errors.append(f"{prefix} derived replay is not OFDFT")
            expected_hashes = {
                "input_sha256": sha256(derived / "INPUT"),
                "stru_sha256": sha256(derived / "STRU"),
                "kpt_sha256": sha256(derived / "KPT"),
                "metadata_sha256": sha256(derived / "metadata.json"),
            }
            for key, expected_hash in expected_hashes.items():
                if row[key] != expected_hash:
                    errors.append(f"{prefix} derived {key} mismatch")
            integration = None
        else:
            errors.append(f"{prefix} unsupported solver")
            return None
        return {
            "expected_electrons": expected,
            "expected_derivation": derivation,
            "integration": integration,
        }
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        errors.append(f"{prefix} source validation failed: {error}")
        return None


def replay_evidence(
    project_root: Path,
    config: dict,
    row: dict[str, str],
    *,
    require_committed: bool,
    require_replay_status: bool,
) -> tuple[dict[str, object], list[str]]:
    runtime_row = {**row, "replay_experiment_id": row["audit_experiment_id"]}
    errors = validate_replay_run(
        project_root,
        config,
        runtime_row,
        require_committed=require_committed,
        require_replay_status=require_replay_status,
    )
    source_id = row["source_experiment_id"]
    audit_id = row["audit_experiment_id"]
    source_run = project_root / "runs" / source_id
    audit_run = project_root / "runs" / audit_id
    payload: dict[str, object] = {}
    try:
        expected, derivation = expected_electrons(audit_run)
        if format(expected, ".17g") != row["expected_electrons"]:
            errors.append(f"{audit_id}: expected electron count differs from manifest")
        source_grid = parse_charge_grid(find_single_log(source_run))
        replay_grid = parse_charge_grid(find_single_log(audit_run))
        if replay_grid != source_grid:
            errors.append(f"{audit_id}: replay FFT grid differs from its source run")
        cube = project_root / row["density_path"]
        integration = integrate_cube(
            cube, audit_run / "STRU", expected, replay_grid
        )
        integration["density_path"] = row["density_path"]
        equivalence = scientific_equivalence(
            read_json(source_run / "result.json"), read_json(audit_run / "result.json")
        )
        if not integration["accepted"]:
            errors.append(
                f"{audit_id}: certified electron relative error is not strictly below the limit"
            )
        if not equivalence["accepted"]:
            errors.append(f"{audit_id}: source/replay scientific equivalence failed")
        payload = {
            "schema_version": 1,
            "protocol_revision": PROTOCOL_REVISION,
            "status": "accepted" if integration["accepted"] and equivalence["accepted"] else "rejected",
            "source_experiment_id": source_id,
            "audit_experiment_id": audit_id,
            "expected_electron_derivation": derivation,
            "integration": integration,
            "scientific_equivalence": equivalence,
            "provenance": {
                "config_path": str(CONFIG_PATH),
                "config_sha256": sha256(project_root / CONFIG_PATH),
                "manifest_path": str(MANIFEST_PATH),
                "manifest_sha256": sha256(project_root / MANIFEST_PATH),
                "preregistration_commit": _introduction_commit(
                    project_root, project_root / CONFIG_PATH
                ),
                "integrator_path": "scripts/s1_electron_number_common.py",
                "integrator_sha256": config["implementation"][
                    "scripts/s1_electron_number_common.py"
                ],
                "validator_path": "scripts/validate_s1_electron_number_audit.py",
                "validator_sha256": config["implementation"][
                    "scripts/validate_s1_electron_number_audit.py"
                ],
                "source_run_introduction_commit": _introduction_commit(
                    project_root, source_run / "input_metadata.json"
                ),
                "source_result_sha256": sha256(source_run / "result.json"),
                "replay_code_commit": read_json(
                    audit_run / "experiment_metadata.json"
                )["code_commit"],
            },
        }
        evidence_path = audit_run / "electron_number_audit.json"
        if evidence_path.exists():
            if not evidence_path.is_file() or evidence_path.is_symlink():
                errors.append(
                    f"{audit_id}: electron-number evidence is not a regular, non-symbolic file"
                )
            else:
                if read_json(evidence_path) != payload:
                    errors.append(
                        f"{audit_id}: committed electron-number evidence differs from recomputation"
                    )
                if require_committed:
                    failure = _tracked_failure(project_root, evidence_path)
                    if failure:
                        errors.append(f"{audit_id}: {failure}")
        elif require_committed or require_replay_status:
            errors.append(f"{audit_id}: missing electron-number evidence")
        if require_committed:
            failure = _tracked_failure(project_root, cube)
            if failure:
                errors.append(f"{audit_id}: {failure}")
            if evidence_path.is_file() and not evidence_path.is_symlink() and cube.is_file():
                try:
                    run_introduction = _latest_introduction_commit(
                        project_root, audit_run / "experiment_metadata.json"
                    )
                    errors.extend(
                        f"{audit_id}: {failure}"
                        for failure in _complete_run_tree_failures(
                            project_root, audit_run, run_introduction
                        )
                    )
                    if _latest_introduction_commit(project_root, cube) != run_introduction:
                        errors.append(f"{audit_id}: cube was not introduced with its run")
                    if _latest_introduction_commit(project_root, evidence_path) != run_introduction:
                        errors.append(
                            f"{audit_id}: electron-number evidence was not introduced with its run"
                        )
                except (subprocess.CalledProcessError, ValueError) as error:
                    errors.append(f"{audit_id}: run evidence commit validation failed: {error}")
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        errors.append(f"{audit_id}: electron-number replay validation failed: {error}")
    return payload, errors


def validate_registration(
    project_root: Path,
    config_path: Path,
    manifest_path: Path,
    *,
    require_committed: bool,
) -> tuple[dict, list[dict[str, str]], dict[str, dict[str, object]]]:
    project_root = project_root.resolve()
    config_path = config_path.resolve()
    manifest_path = manifest_path.resolve()
    config = read_json(config_path)
    rows = read_manifest(manifest_path)
    errors: list[str] = []
    canonical_config = (project_root / CONFIG_PATH).resolve()
    canonical_manifest = (project_root / MANIFEST_PATH).resolve()
    if config_path != canonical_config or manifest_path != canonical_manifest:
        errors.append("config and manifest must use their canonical project paths")
    expected_top_level_keys = {
        "schema_version",
        "protocol_revision",
        "status",
        "rank_count",
        "generated_at",
        "generated_from_commit",
        "manifest_path",
        "manifest_sha256",
        "source",
        "scope",
        "experiment_id_block",
        "pilot",
        "density",
        "acceptance",
        "implementation",
        "implementation_git_modes",
        "runtime",
        "runtime_audit",
    }
    if set(config) != expected_top_level_keys:
        errors.append("config top-level key set mismatch")
    if config.get("schema_version") != 1 or config.get("protocol_revision") != PROTOCOL_REVISION:
        errors.append("config schema/protocol mismatch")
    if config.get("status") != "preregistered_pending_execution":
        errors.append("config status is not preregistered_pending_execution")
    if config.get("rank_count") != 4:
        errors.append("config rank count mismatch")
    if config.get("manifest_path") != str(MANIFEST_PATH):
        errors.append("config manifest path mismatch")
    if config.get("manifest_sha256") != sha256(manifest_path):
        errors.append("config manifest SHA-256 mismatch")
    source = config.get("source", {})
    expected_upstream_validation = {
        "r8_committed_validator": {
            "experiment_count": 42,
            "first_experiment_id": PRIMARY_IDS[42],
            "last_experiment_id": PRIMARY_IDS[-1],
            "config_sha256": sha256(project_root / R8_CONFIG),
            "manifest_sha256": sha256(project_root / R8_MANIFEST),
            "preregistration_commit": _introduction_commit(
                project_root, project_root / R8_MANIFEST
            ),
        },
        "runtime_relocation_committed_validator": {
            "protocol_revision": read_json(project_root / RUNTIME_CONFIG)[
                "protocol_revision"
            ],
            "experiment_count": 6,
            "first_experiment_id": SUPPLEMENTAL_IDS[0],
            "last_experiment_id": SUPPLEMENTAL_IDS[-1],
            "config_sha256": sha256(project_root / RUNTIME_CONFIG),
            "manifest_sha256": sha256(project_root / RUNTIME_MANIFEST),
            "preregistration_commit": _introduction_commit(
                project_root, project_root / RUNTIME_CONFIG
            ),
            "checked_run_ids": list(SUPPLEMENTAL_IDS),
            "checked_core_ids": [],
            "checked_failure_ids": [],
        },
        "accepted_summaries": {
            "r7_core_eos": accepted_summary_registration(
                project_root, CORE_SUMMARY, "core_eos_status"
            ),
            "r8_non_equilibrium": accepted_summary_registration(
                project_root, R8_SUMMARY, "s1_r8_status"
            ),
            "runtime_relocation": accepted_summary_registration(
                project_root, RUNTIME_SUMMARY, "six_point_status"
            ),
        },
    }
    expected_source = {
        "r7_manifest_path": str(R7_MANIFEST),
        "r7_manifest_sha256": sha256(project_root / R7_MANIFEST),
        "r8_manifest_path": str(R8_MANIFEST),
        "r8_manifest_sha256": sha256(project_root / R8_MANIFEST),
        "runtime_config_path": str(RUNTIME_CONFIG),
        "runtime_config_sha256": sha256(project_root / RUNTIME_CONFIG),
        "target_experiment_ids": list(TARGET_IDS),
        "upstream_validation": expected_upstream_validation,
    }
    if source != expected_source:
        errors.append("config source registration mismatch")
    runtime_config = read_json(project_root / RUNTIME_CONFIG)
    if config.get("runtime") != runtime_config.get("runtime"):
        errors.append("copied runtime identity differs from accepted runtime config")
    if config.get("runtime_audit") != runtime_config.get("runtime_audit"):
        errors.append("copied runtime audit differs from accepted runtime config")
    expected_scope = {
        "primary_baseline_ids": list(PRIMARY_IDS),
        "supplemental_runtime_replay_ids": list(SUPPLEMENTAL_IDS),
        "coverage_denominator": 90,
        "ks_existing_density_count": 60,
        "ofdft_output_replay_count": 30,
    }
    if config.get("scope") != expected_scope:
        errors.append("config scope mismatch")
    if config.get("experiment_id_block") != {
        "first": AUDIT_IDS[0],
        "last": AUDIT_IDS[-1],
        "count": len(AUDIT_IDS),
    }:
        errors.append("config audit experiment ID block mismatch")
    expected_pilot = {
        "audit_experiment_ids": list(AUDIT_IDS[:2]),
        "source_experiment_ids": list(PILOT_SOURCE_IDS),
        "remaining_runs_forbidden_until_both_accepted": True,
    }
    if config.get("pilot") != expected_pilot:
        errors.append("config pilot gate mismatch")
    if config.get("density") != {
        "cube_output_control": [1, CUBE_PRECISION],
        "cube_density_unit": "electron_per_bohr3",
        "cube_volume_authority": "STRU",
        "cube_grid_dimension_authority": "raw_running_scf_log_equal_to_source",
        "cube_sum_algorithm": "exact_scaled_integer_decimal_sum_with_math_fsum_crosscheck",
        "certification_arithmetic": "exact_fraction_including_text_rounding_bound",
        "reciprocal_integral": "cell_volume_bohr3_times_sum_spin_real_rho_G0",
        "reciprocal_spin_count": 1,
        "expected_electron_authority": "STRU_atom_counts_times_local_pseudopotential_zion",
    }:
        errors.append("config density algorithm mismatch")
    if config.get("acceptance") != {
        "coverage_required": 90,
        "missing_allowed": 0,
        "failed_allowed": 0,
        "per_point_certified_relative_error_strictly_less_than": RELATIVE_ERROR_LIMIT,
        "replay_energy_delta_mev_per_atom_strictly_less_than": ENERGY_LIMIT_MEV_PER_ATOM,
        "replay_pressure_delta_gpa_strictly_less_than": PRESSURE_LIMIT_GPA,
    }:
        errors.append("config acceptance thresholds mismatch")
    if len(rows) != 90 or tuple(row["source_experiment_id"] for row in rows) != TARGET_IDS:
        errors.append("manifest must contain ordered S1-029--118 targets")
    ordered_of_rows = sorted(
        (row for row in rows if row["solver"] == "ofdft"),
        key=lambda row: row["audit_experiment_id"],
    )
    if tuple(row["audit_experiment_id"] for row in ordered_of_rows) != AUDIT_IDS:
        errors.append("OF audit IDs must be ordered S1-119--148")
    if [row["source_experiment_id"] for row in ordered_of_rows] != _expected_of_sources(rows):
        errors.append("OF audit source order differs from preregistered pilot/fanout order")
    if tuple(config.get("pilot", {}).get("source_experiment_ids", ())) != PILOT_SOURCE_IDS:
        errors.append("pilot source IDs mismatch")
    if tuple(config.get("pilot", {}).get("audit_experiment_ids", ())) != AUDIT_IDS[:2]:
        errors.append("pilot audit IDs mismatch")

    implementation = config.get("implementation", {})
    implementation_git_modes = config.get("implementation_git_modes", {})
    if set(implementation) != set(IMPLEMENTATION_PATHS):
        errors.append("implementation closure path set mismatch")
    if set(implementation_git_modes) != set(IMPLEMENTATION_PATHS):
        errors.append("implementation Git-mode path set mismatch")
    for relative in IMPLEMENTATION_PATHS:
        path = project_root / relative
        if not path.is_file() or path.is_symlink() or implementation.get(relative) != sha256(path):
            errors.append(f"implementation path differs: {relative}")
            continue
        entries = _tree_entries(project_root, "HEAD", relative)
        entry = entries.get(relative)
        if (
            entry is None
            or entry[1] != "blob"
            or implementation_git_modes.get(relative) != entry[0]
        ):
            errors.append(f"implementation Git mode/type differs: {relative}")
        if relative in EXECUTABLE_IMPLEMENTATION_PATHS and not os.access(path, os.X_OK):
            errors.append(f"required implementation entry is not executable: {relative}")

    details: dict[str, dict[str, object]] = {}
    for row in rows:
        detail = _validate_source_row(project_root, row, errors)
        if detail is not None:
            details[row["source_experiment_id"]] = detail

    if require_committed:
        expected_input_names = {"INPUT", "STRU", "KPT", "metadata.json"}
        frozen_paths = [config_path, manifest_path]
        for audit_id in AUDIT_IDS:
            directory = project_root / INPUT_ROOT / audit_id
            if not directory.is_dir() or directory.is_symlink():
                errors.append(f"missing or symbolic derived input directory: {directory}")
                continue
            observed_names = {path.name for path in directory.iterdir()}
            if observed_names != expected_input_names:
                errors.append(f"derived input file set mismatch: {directory}")
            frozen_paths.extend(directory / name for name in sorted(expected_input_names))
        for path in frozen_paths:
            if not path.is_file() or path.is_symlink():
                errors.append(f"frozen path is not a regular, non-symbolic file: {path}")
        for path in frozen_paths:
            failure = _tracked_failure(project_root, path)
            if failure:
                errors.append(failure)
        authority_paths: set[Path] = set()
        authority_paths.update(
            {
                project_root / R7_MANIFEST,
                project_root / R8_CONFIG,
                project_root / R8_MANIFEST,
                project_root / RUNTIME_CONFIG,
                project_root / RUNTIME_MANIFEST,
                project_root / CORE_SUMMARY,
                project_root / R8_SUMMARY,
                project_root / RUNTIME_SUMMARY,
            }
        )
        for row in rows:
            source_run = project_root / "runs" / row["source_experiment_id"]
            source_metadata = read_json(source_run / "input_metadata.json")
            pseudo_name = str(source_metadata.get("pseudopotential"))
            if Path(pseudo_name).name != pseudo_name:
                errors.append(
                    f"{row['source_experiment_id']}: authority pseudopotential is not a basename"
                )
                continue
            authority_paths.update(
                {
                    source_run / "INPUT",
                    source_run / "STRU",
                    source_run / "KPT",
                    source_run / "input_metadata.json",
                    source_run / "result.json",
                    source_run / pseudo_name,
                    project_root / "assets" / "pseudo" / pseudo_name,
                    find_single_log(source_run),
                }
            )
            if row["solver"] == "ksdft":
                authority_paths.add(
                    find_single_density(source_run, "*CHARGE-DENSITY.restart")
                )
        for path in sorted(authority_paths):
            if not path.is_file() or path.is_symlink():
                errors.append(f"authority path is not a regular, non-symbolic file: {path}")
                continue
            failure = _tracked_failure(project_root, path)
            if failure:
                errors.append(failure)
        for experiment_id in SUPPLEMENTAL_IDS:
            run_directory = project_root / "runs" / experiment_id
            try:
                introduction = _latest_introduction_commit(
                    project_root, run_directory / "experiment_metadata.json"
                )
                errors.extend(
                    _complete_run_tree_failures(
                        project_root, run_directory, introduction
                    )
                )
            except (subprocess.CalledProcessError, ValueError) as error:
                errors.append(
                    f"{experiment_id}: supplemental run tree validation failed: {error}"
                )
        try:
            introduction = _introduction_commit(project_root, config_path)
            expected_preregistration_paths = {
                _relative(project_root, path) for path in frozen_paths
            }
            if _commit_changed_paths(project_root, introduction) != expected_preregistration_paths:
                errors.append("preregistration commit scope differs from the frozen path set")
            for path in frozen_paths:
                if _introduction_commit(project_root, path) != introduction:
                    errors.append(f"frozen path was not introduced in preregistration: {path}")
                blob_failure = _frozen_blob_failure(
                    project_root, path, introduction
                )
                if blob_failure:
                    errors.append(blob_failure)
            parent = str(_git(project_root, "rev-parse", f"{introduction}^"))
            if parent != config.get("generated_from_commit"):
                errors.append("preregistration parent differs from generated_from_commit")
            for relative in IMPLEMENTATION_PATHS:
                failure = _tracked_failure(project_root, project_root / relative)
                if failure:
                    errors.append(f"implementation {failure}")
                blob = _git(
                    project_root,
                    "cat-file",
                    "blob",
                    f"{parent}:{relative}",
                    text=False,
                )
                if (project_root / relative).read_bytes() != blob:
                    errors.append(f"implementation differs from preregistration parent: {relative}")
                parent_entry = _tree_entries(project_root, parent, relative).get(relative)
                head_entry = _tree_entries(project_root, "HEAD", relative).get(relative)
                if (
                    parent_entry is None
                    or head_entry != parent_entry
                    or parent_entry[0] != implementation_git_modes.get(relative)
                    or parent_entry[1] != "blob"
                ):
                    errors.append(
                        f"implementation mode/type/blob differs from preregistration parent: {relative}"
                    )
        except (subprocess.CalledProcessError, ValueError) as error:
            errors.append(f"preregistration commit validation failed: {error}")
    if errors:
        raise ValueError("S1 electron-number registration validation failed:\n- " + "\n- ".join(errors))
    return config, rows, details


def _write_evidence(path: Path, payload: dict[str, object]) -> None:
    if path.exists():
        raise ValueError(f"refusing to overwrite electron-number evidence: {path}")
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", nargs="?", type=Path, default=project_root / MANIFEST_PATH)
    parser.add_argument("--config", type=Path, default=project_root / CONFIG_PATH)
    parser.add_argument("--require-committed", action="store_true")
    parser.add_argument("--check-run-core")
    parser.add_argument("--check-run")
    parser.add_argument("--check-failure-run")
    parser.add_argument("--check-failure-archives")
    parser.add_argument("--write-run-evidence")
    parser.add_argument("--require-all-runs", action="store_true")
    args = parser.parse_args()
    selected = [
        value
        for value in (
            args.check_run_core,
            args.check_run,
            args.check_failure_run,
            args.check_failure_archives,
            args.write_run_evidence,
        )
        if value
    ]
    if len(selected) > 1:
        parser.error("select at most one per-run mode")
    config, rows, details = validate_registration(
        project_root,
        args.config,
        args.manifest,
        require_committed=args.require_committed,
    )
    rows_by_audit = {
        row["audit_experiment_id"]: row for row in rows if row["audit_experiment_id"]
    }
    checked: list[str] = []
    if selected:
        audit_id = selected[0]
        row = rows_by_audit.get(audit_id)
        if row is None:
            raise ValueError(f"requested audit run is outside manifest: {audit_id}")
        if args.check_failure_archives:
            errors = _failed_archive_chain_failures(project_root, audit_id)
            if errors:
                raise ValueError(
                    "S1 electron-number failed-archive validation failed:\n- "
                    + "\n- ".join(errors)
                )
        elif args.check_failure_run:
            runtime_row = {**row, "replay_experiment_id": audit_id}
            errors = validate_failed_replay_run(
                project_root,
                config,
                runtime_row,
                require_committed=args.require_committed,
            )
            if errors:
                raise ValueError(
                    "S1 electron-number failed-run validation failed:\n- "
                    + "\n- ".join(errors)
                )
        else:
            payload, errors = replay_evidence(
                project_root,
                config,
                row,
                require_committed=args.require_committed,
                require_replay_status=args.check_run is not None,
            )
            if errors:
                raise ValueError(
                    "S1 electron-number run validation failed:\n- " + "\n- ".join(errors)
                )
            if args.write_run_evidence:
                _write_evidence(
                    project_root / "runs" / audit_id / "electron_number_audit.json",
                    payload,
                )
            elif not (
                project_root / "runs" / audit_id / "electron_number_audit.json"
            ).is_file():
                raise ValueError(f"missing electron-number evidence for {audit_id}")
        checked.append(audit_id)
    if args.require_all_runs:
        for audit_id in AUDIT_IDS:
            row = rows_by_audit[audit_id]
            _, errors = replay_evidence(
                project_root,
                config,
                row,
                require_committed=True,
                require_replay_status=True,
            )
            if errors:
                raise ValueError(
                    f"S1 electron-number all-run validation failed for {audit_id}:\n- "
                    + "\n- ".join(errors)
                )
            checked.append(audit_id)
    payload = {
        "protocol_revision": PROTOCOL_REVISION,
        "target_count": len(rows),
        "ks_prevalidated_count": sum(
            1
            for detail in details.values()
            if detail.get("integration") is not None
            and detail["integration"].get("accepted") is True
        ),
        "ofdft_replay_count": len(rows_by_audit),
        "config_sha256": sha256(args.config),
        "manifest_sha256": sha256(args.manifest),
        "checked_run_ids": checked,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
