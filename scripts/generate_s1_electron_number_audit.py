#!/usr/bin/env python3
"""Freeze the 90-point G1 electron-number audit and 30 OF output replays."""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from s1_electron_number_common import (
    AUDIT_IDS,
    CUBE_PRECISION,
    ENERGY_LIMIT_MEV_PER_ATOM,
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
    ordered_of_replay_sources,
    read_json,
    sha256,
    write_manifest,
)
from validate_s1_mpi_prefix_equivalence import validate as validate_runtime_relocation
from validate_s1_non_equilibrium_manifest import validate as validate_non_equilibrium


CONFIG_PATH = Path("config/S1_electron_number_audit.json")
MANIFEST_PATH = Path("config/S1_electron_number_audit_manifest.tsv")
INPUT_ROOT = Path("inputs/s1/electron_number_audit")
R7_MANIFEST = Path("config/S1_eos_run_manifest.tsv")
R8_MANIFEST = Path("config/S1_non_equilibrium_run_manifest.tsv")
RUNTIME_CONFIG = Path("config/S1_runtime_relocation_equivalence.json")
RUNTIME_MANIFEST = Path("config/S1_runtime_relocation_equivalence_manifest.tsv")
R8_CONFIG = Path("config/S1_non_equilibrium_convergence.json")
CORE_SUMMARY = Path("analysis/s1/core_eos_20260805/summary.json")
R8_SUMMARY = Path("analysis/s1/non_equilibrium_convergence_20260805/summary.json")
RUNTIME_SUMMARY = Path("analysis/s1/runtime_relocation_equivalence_20260805/summary.json")

IMPLEMENTATION_PATHS = (
    "docs/S1_G1_ELECTRON_NUMBER_AUDIT_PROTOCOL.md",
    "environment/activate.sh",
    "scripts/s1_electron_number_common.py",
    "scripts/generate_s1_electron_number_audit.py",
    "scripts/validate_s1_electron_number_audit.py",
    "scripts/analyze_s1_electron_number_audit.py",
    "scripts/run_s1_electron_number_audit.sh",
    "scripts/run_s1_single.sh",
    "scripts/parse_s1_single.py",
    "scripts/runtime_relocation_namespace_launcher.py",
    "scripts/runtime_relocation_namespace_payload.sh",
    "scripts/runtime_relocation_audit_launcher.py",
    "scripts/runtime_relocation_rank_wrapper.py",
    "scripts/write_s1_runtime_relocation_status.py",
    "scripts/validate_s1_mpi_prefix_equivalence.py",
    "scripts/s1_mpi_prefix_equivalence_common.py",
    "scripts/s1_runtime_relocation_elf.py",
    "scripts/s1_runtime_relocation_smoke.py",
    "scripts/analyze_s1_non_equilibrium.py",
    "scripts/analyze_s1_eos.py",
    "scripts/validate_s1_non_equilibrium_manifest.py",
    "scripts/generate_s1_non_equilibrium_manifest.py",
    "tests/unit/test_s1_electron_number_audit.py",
)
EXECUTABLE_IMPLEMENTATION_PATHS = {
    "scripts/run_s1_electron_number_audit.sh",
    "scripts/run_s1_single.sh",
    "scripts/runtime_relocation_namespace_payload.sh",
}


def _git(project_root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(project_root), *args], text=True
    ).strip()


def _introduction_commit(project_root: Path, path: Path) -> str:
    relative = str(path.relative_to(project_root))
    commits = _git(
        project_root, "log", "--format=%H", "--diff-filter=A", "--", relative
    ).splitlines()
    if len(commits) != 1:
        raise ValueError(f"expected one introduction commit for {relative}")
    return commits[0]


def accepted_summary_registration(
    project_root: Path, relative: Path, status_key: str
) -> dict[str, object]:
    path = project_root / relative
    payload = read_json(path)
    if payload.get(status_key) != "accepted":
        raise ValueError(f"upstream summary is not accepted: {relative}")
    metric_expectations = {
        "core_eos_status": {
            "expected_calculations": 42,
            "selected_calculations": 42,
            "failures": [],
        },
        "s1_r8_status": {
            "expected_calculations": 42,
            "selected_calculations": 42,
            "accepted_comparisons": 6,
            "failures": [],
        },
        "six_point_status": {
            "expected_points": 6,
            "analyzed_points": 6,
            "scientific_gate_passed_count": 6,
            "runtime_audit_accepted_count": 6,
            "storage_closure_tier_count": 6,
            "r8_conclusion_unchanged_count": 6,
            "failures": [],
        },
    }[status_key]
    if any(payload.get(key) != value for key, value in metric_expectations.items()):
        raise ValueError(f"upstream summary acceptance metrics differ: {relative}")
    _git(project_root, "ls-files", "--error-unmatch", "--", str(relative))
    head_blob = subprocess.check_output(
        ["git", "-C", str(project_root), "cat-file", "blob", f"HEAD:{relative}"]
    )
    if head_blob != path.read_bytes():
        raise ValueError(f"upstream summary differs from HEAD: {relative}")
    provenance = payload.get("analysis_provenance")
    if not isinstance(provenance, dict):
        raise ValueError(f"upstream summary lacks analysis provenance: {relative}")
    analyzer_commit = provenance.get("analyzer_code_commit", provenance.get("analyzer_commit"))
    if not isinstance(analyzer_commit, str) or len(analyzer_commit) != 40:
        raise ValueError(f"upstream summary lacks analyzer commit: {relative}")
    return {
        "path": str(relative),
        "sha256": sha256(path),
        "status_key": status_key,
        "status": "accepted",
        "acceptance_metrics": metric_expectations,
        "analyzer_commit": analyzer_commit,
        "last_change_commit": _git(
            project_root, "log", "-n", "1", "--format=%H", "--", str(relative)
        ),
    }


def revalidate_upstreams(project_root: Path) -> dict[str, object]:
    r8_validation = validate_non_equilibrium(
        project_root,
        project_root / R8_CONFIG,
        project_root / R8_MANIFEST,
    )
    runtime_validation = validate_runtime_relocation(
        project_root,
        project_root / RUNTIME_CONFIG,
        project_root / RUNTIME_MANIFEST,
        require_committed=True,
        check_run_ids=SUPPLEMENTAL_IDS,
    )
    return {
        "r8_committed_validator": r8_validation,
        "runtime_relocation_committed_validator": runtime_validation,
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


def _read_ids(path: Path, expected: tuple[str, ...]) -> None:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    observed = tuple(row["experiment_id"] for row in rows)
    if observed != expected:
        raise ValueError(f"frozen experiment IDs differ in {path}")


def _git_mode(project_root: Path, relative: str) -> str:
    rows = _git(project_root, "ls-files", "--stage", "--", relative).splitlines()
    if len(rows) != 1:
        raise ValueError(f"implementation path is not tracked exactly once: {relative}")
    mode = rows[0].split(maxsplit=1)[0]
    if mode not in {"100644", "100755"}:
        raise ValueError(f"implementation path has a non-regular Git mode: {relative}")
    return mode


def _supplemental_ids(runtime_config: dict) -> tuple[str, ...]:
    mappings = runtime_config.get("mappings")
    if not isinstance(mappings, list):
        raise ValueError("runtime config mappings are missing")
    observed = tuple(str(row.get("replay_experiment_id")) for row in mappings)
    if observed != SUPPLEMENTAL_IDS:
        raise ValueError("runtime replay IDs differ from S1-113--118")
    return observed


def _source_row(project_root: Path, experiment_id: str, scope: str) -> dict[str, object]:
    run = project_root / "runs" / experiment_id
    if not run.is_dir() or run.is_symlink():
        raise ValueError(f"missing source run: {experiment_id}")
    metadata = read_json(run / "input_metadata.json")
    result = read_json(run / "result.json")
    if result.get("converged") is not True:
        raise ValueError(f"source result is not converged: {experiment_id}")
    solver = str(metadata.get("solver"))
    if solver not in {"ksdft", "ofdft"}:
        raise ValueError(f"unexpected solver for {experiment_id}: {solver}")
    pseudo = str(metadata.get("pseudopotential"))
    if Path(pseudo).name != pseudo:
        raise ValueError(f"pseudopotential name is not a basename: {experiment_id}")
    pseudo_path = run / pseudo
    asset_path = project_root / "assets" / "pseudo" / pseudo
    if not pseudo_path.is_file() or pseudo_path.read_bytes() != asset_path.read_bytes():
        raise ValueError(f"pseudopotential differs from frozen asset: {experiment_id}")
    expected, derivation = expected_electrons(run)
    nominal = float(metadata.get("expected_electrons", -1))
    if abs(expected - nominal) > 1.0e-12:
        raise ValueError(f"independent expected electron count differs: {experiment_id}")
    log = find_single_log(run)
    restart_files = sorted(run.glob("OUT.*/*CHARGE-DENSITY.restart"))
    cube_files = sorted(run.glob("OUT.*/chg.cube"))
    if solver == "ksdft":
        density = find_single_density(run, "*CHARGE-DENSITY.restart")
        density_mode = "existing_reciprocal_restart"
        density_path = density.relative_to(project_root)
        density_digest = sha256(density)
        if cube_files:
            raise ValueError(f"unexpected pre-existing KS cube: {experiment_id}")
    else:
        if restart_files or cube_files:
            raise ValueError(f"successful OF source unexpectedly has density: {experiment_id}")
        density_mode = "high_precision_cube_replay"
        density_path = Path()
        density_digest = ""
    return {
        "source_experiment_id": experiment_id,
        "scope": scope,
        "material": str(metadata.get("material")),
        "series_id": str(metadata.get("series_id", metadata.get("scan_axis", ""))),
        "solver": solver,
        "volume_ratio": metadata.get("volume_ratio", ""),
        "expected_electrons": format(expected, ".17g"),
        "expected_derivation": derivation,
        "density_mode": density_mode,
        "density_path": str(density_path),
        "source_density_sha256": density_digest,
        "audit_experiment_id": "",
        "input_directory": "",
        "derived_suffix": "",
        "input_sha256": sha256(run / "INPUT"),
        "stru_sha256": sha256(run / "STRU"),
        "kpt_sha256": sha256(run / "KPT"),
        "metadata_sha256": sha256(run / "input_metadata.json"),
        "pseudopotential": pseudo,
        "pseudopotential_sha256": sha256(pseudo_path),
        "reference_experiment_id": experiment_id,
        "reference_result_path": str((run / "result.json").relative_to(project_root)),
        "reference_result_sha256": sha256(run / "result.json"),
        "reference_log_path": str(log.relative_to(project_root)),
        "reference_log_sha256": sha256(log),
    }


def prepare(project_root: Path) -> dict[str, object]:
    project_root = project_root.resolve()
    status = _git(project_root, "status", "--porcelain")
    if status:
        raise ValueError("generator requires a completely clean worktree")
    for relative in (CONFIG_PATH, MANIFEST_PATH, INPUT_ROOT):
        if (project_root / relative).exists():
            raise ValueError(f"refusing to overwrite frozen output: {relative}")

    _read_ids(project_root / R7_MANIFEST, tuple(PRIMARY_IDS[:42]))
    _read_ids(project_root / R8_MANIFEST, tuple(PRIMARY_IDS[42:]))
    runtime_config = read_json(project_root / RUNTIME_CONFIG)
    _supplemental_ids(runtime_config)
    upstream_validation = revalidate_upstreams(project_root)
    generated_from_commit = _git(project_root, "rev-parse", "HEAD")

    rows = [
        _source_row(
            project_root,
            experiment_id,
            "primary_baseline" if experiment_id in PRIMARY_IDS else "supplemental_runtime_replay",
        )
        for experiment_id in TARGET_IDS
    ]
    by_source = {str(row["source_experiment_id"]): row for row in rows}
    of_sources = list(
        ordered_of_replay_sources(
            {
                experiment_id: str(row["solver"])
                for experiment_id, row in by_source.items()
            }
        )
    )

    derived_payloads: list[tuple[Path, dict[str, bytes | str]]] = []
    for audit_id, source_id in zip(AUDIT_IDS, of_sources):
        row = by_source[source_id]
        source_run = project_root / "runs" / source_id
        suffix = f"g1_ne_{audit_id.rsplit('-', 1)[1]}_from_{source_id.rsplit('-', 1)[1]}"
        relative_input = INPUT_ROOT / audit_id
        input_bytes = derive_output_input((source_run / "INPUT").read_bytes(), suffix)
        source_metadata = read_json(source_run / "input_metadata.json")
        source_metadata["candidate_status"] = "electron_number_output_replay_preregistered"
        source_metadata["electron_number_audit"] = {
            "audit_experiment_id": audit_id,
            "cube_precision": CUBE_PRECISION,
            "only_scientific_input_change": "none_output_control_only",
            "output_control": f"out_chg 1 {CUBE_PRECISION}",
            "protocol_revision": PROTOCOL_REVISION,
            "source_experiment_id": source_id,
        }
        metadata_text = json.dumps(source_metadata, indent=2, sort_keys=True) + "\n"
        payload = {
            "INPUT": input_bytes,
            "STRU": (source_run / "STRU").read_bytes(),
            "KPT": (source_run / "KPT").read_bytes(),
            "metadata.json": metadata_text,
        }
        derived_payloads.append((relative_input, payload))
        row.update(
            {
                "audit_experiment_id": audit_id,
                "input_directory": str(relative_input),
                "derived_suffix": suffix,
                "density_path": f"runs/{audit_id}/OUT.{suffix}/chg.cube",
                "input_sha256": hashlib_sha256_bytes(input_bytes),
                "stru_sha256": hashlib_sha256_bytes(payload["STRU"]),
                "kpt_sha256": hashlib_sha256_bytes(payload["KPT"]),
                "metadata_sha256": hashlib_sha256_bytes(metadata_text.encode("utf-8")),
            }
        )

    implementation = {}
    implementation_git_modes = {}
    for relative in IMPLEMENTATION_PATHS:
        path = project_root / relative
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"missing implementation path: {relative}")
        implementation[relative] = sha256(path)
        implementation_git_modes[relative] = _git_mode(project_root, relative)
        if relative in EXECUTABLE_IMPLEMENTATION_PATHS and (
            implementation_git_modes[relative] != "100755"
            or not os.access(path, os.X_OK)
        ):
            raise ValueError(f"required implementation entry is not executable: {relative}")

    for relative_input, payload in derived_payloads:
        directory = project_root / relative_input
        directory.mkdir(parents=True, exist_ok=False)
        for name, value in payload.items():
            path = directory / name
            if isinstance(value, bytes):
                path.write_bytes(value)
            else:
                path.write_text(value, encoding="utf-8")

    manifest_path = project_root / MANIFEST_PATH
    write_manifest(manifest_path, rows)
    config = {
        "schema_version": 1,
        "protocol_revision": PROTOCOL_REVISION,
        "status": "preregistered_pending_execution",
        "rank_count": runtime_config["rank_count"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_from_commit": generated_from_commit,
        "manifest_path": str(MANIFEST_PATH),
        "manifest_sha256": sha256(manifest_path),
        "source": {
            "r7_manifest_path": str(R7_MANIFEST),
            "r7_manifest_sha256": sha256(project_root / R7_MANIFEST),
            "r8_manifest_path": str(R8_MANIFEST),
            "r8_manifest_sha256": sha256(project_root / R8_MANIFEST),
            "runtime_config_path": str(RUNTIME_CONFIG),
            "runtime_config_sha256": sha256(project_root / RUNTIME_CONFIG),
            "target_experiment_ids": list(TARGET_IDS),
            "upstream_validation": upstream_validation,
        },
        "scope": {
            "primary_baseline_ids": list(PRIMARY_IDS),
            "supplemental_runtime_replay_ids": list(SUPPLEMENTAL_IDS),
            "coverage_denominator": 90,
            "ks_existing_density_count": 60,
            "ofdft_output_replay_count": 30,
        },
        "experiment_id_block": {
            "first": AUDIT_IDS[0],
            "last": AUDIT_IDS[-1],
            "count": len(AUDIT_IDS),
        },
        "pilot": {
            "audit_experiment_ids": list(AUDIT_IDS[:2]),
            "source_experiment_ids": list(PILOT_SOURCE_IDS),
            "remaining_runs_forbidden_until_both_accepted": True,
        },
        "density": {
            "cube_output_control": [1, CUBE_PRECISION],
            "cube_density_unit": "electron_per_bohr3",
            "cube_volume_authority": "STRU",
            "cube_grid_dimension_authority": "raw_running_scf_log_equal_to_source",
            "cube_sum_algorithm": "exact_scaled_integer_decimal_sum_with_math_fsum_crosscheck",
            "certification_arithmetic": "exact_fraction_including_text_rounding_bound",
            "reciprocal_integral": "cell_volume_bohr3_times_sum_spin_real_rho_G0",
            "reciprocal_spin_count": 1,
            "expected_electron_authority": "STRU_atom_counts_times_local_pseudopotential_zion",
        },
        "acceptance": {
            "coverage_required": 90,
            "missing_allowed": 0,
            "failed_allowed": 0,
            "per_point_certified_relative_error_strictly_less_than": RELATIVE_ERROR_LIMIT,
            "replay_energy_delta_mev_per_atom_strictly_less_than": ENERGY_LIMIT_MEV_PER_ATOM,
            "replay_pressure_delta_gpa_strictly_less_than": PRESSURE_LIMIT_GPA,
        },
        "implementation": implementation,
        "implementation_git_modes": implementation_git_modes,
        "runtime": deepcopy(runtime_config["runtime"]),
        "runtime_audit": deepcopy(runtime_config["runtime_audit"]),
    }
    config_path = project_root / CONFIG_PATH
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "config_path": str(CONFIG_PATH),
        "config_sha256": sha256(config_path),
        "manifest_path": str(MANIFEST_PATH),
        "manifest_sha256": sha256(manifest_path),
        "target_count": len(rows),
        "ofdft_replay_count": len(of_sources),
        "generated_from_commit": generated_from_commit,
    }


def hashlib_sha256_bytes(value: bytes) -> str:
    import hashlib

    return hashlib.sha256(value).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args()
    payload = prepare(args.project_root)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
