#!/usr/bin/env python3
"""Reparse and assess the six S1-R8 runtime-relocation replay calculations."""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
from pathlib import Path

from analyze_s1_non_equilibrium import _fit_quality, _series_status, compare_series
from s1_mpi_prefix_equivalence_common import (
    CANONICAL_CONFIG_PATH,
    CANONICAL_MANIFEST_PATH,
    equivalence_tier,
    git_clean,
    json_safe,
    path_from_project,
    raw_observables,
    read_tsv,
    reparse_run,
    require_tracked_at_head,
    sha256,
)
from validate_s1_mpi_prefix_equivalence import validate, validate_replay_run


def _energy(metadata: dict, result: dict) -> float:
    if metadata["solver"] == "ksdft":
        return float(result["zero_temp_extrapolated_energy_ev_per_atom"])
    return float(result["energy_ev_per_atom"])


def replacement_conclusion(
    r8_config: dict,
    r8_summary: dict,
    core_summary: dict,
    material: str,
    series_id: str,
    replay_id: str,
    replay_energy_ev_per_atom: float,
    replay_pressure_gpa: float,
) -> dict:
    key = f"{material}/{series_id}"
    original = r8_summary["series"][key]
    points = copy.deepcopy(original["points"])
    matches = [point for point in points if round(float(point["volume_ratio"]), 12) == 1.0]
    if len(matches) != 1:
        raise ValueError(f"{key}: expected exactly one V/V0=1.0 refined point")
    matches[0]["experiment_id"] = replay_id
    matches[0]["energy_ev_per_atom"] = replay_energy_ev_per_atom
    matches[0]["pressure_gpa"] = replay_pressure_gpa

    spec = r8_config["materials"][material][series_id]
    baseline_key = f"{material}/{spec['baseline_series_id']}"
    baseline = core_summary["series"][baseline_key]["points"]
    reference_ratio = float(
        r8_config["acceptance"]["relative_energy_reference_volume_ratio"]
    )
    if spec["comparison_axis"] == "cutoff":
        energy_threshold = float(
            r8_config["acceptance"][
                "cutoff_max_relative_energy_difference_mev_per_atom"
            ]
        )
        pressure_threshold = float(
            r8_config["acceptance"]["cutoff_max_pressure_difference_gpa"]
        )
    else:
        energy_threshold = float(
            r8_config["acceptance"][
                "kmesh_max_relative_energy_difference_mev_per_atom"
            ]
        )
        pressure_threshold = None
    comparison = compare_series(
        baseline,
        points,
        reference_ratio,
        energy_threshold,
        pressure_threshold,
    )
    fit = None
    fit_failures: list[str] = []
    try:
        fit, fit_failures = _fit_quality(points)
    except ValueError as error:
        fit_failures = [f"bm3_fit_failed:{error}"]
    modified_status = _series_status([], fit_failures, comparison)
    original_status = str(original["status"])
    original_global_status = str(r8_summary["s1_r8_status"])
    modified_global_status = (
        modified_status
        if modified_status != "accepted"
        else (
            "accepted"
            if all(
                payload.get("status") == "accepted"
                for other_key, payload in r8_summary["series"].items()
                if other_key != key
            )
            else original_global_status
        )
    )
    return {
        "series_key": key,
        "original_series_status": original_status,
        "modified_series_status": modified_status,
        "original_r8_status": original_global_status,
        "modified_r8_status": modified_global_status,
        "conclusion_unchanged": (
            modified_status == original_status
            and modified_global_status == original_global_status
        ),
        "modified_comparison": comparison,
        "modified_fit": fit,
        "modified_fit_failures": fit_failures,
    }


def _readme(payload: dict) -> str:
    lines = [
        "# S1-R8 runtime-relocation six-point equivalence replay",
        "",
        f"- Status: `{payload['six_point_status']}`",
        f"- Recommended action: `{payload['recommended_action']}`",
        f"- Scientific gates passed: {payload['scientific_gate_passed_count']}/6",
        f"- R8 replacement conclusions unchanged: {payload['r8_conclusion_unchanged_count']}/6",
        f"- Runtime audits accepted: {payload['runtime_audit_accepted_count']}/6",
        "",
        "The energy and pressure gates are strict: `|dE| < 0.1 meV/atom` and "
        "`|dP| < 0.02 GPa`. KS energy uses the logged `E_KS(sigma->0)` "
        "entropy-corrected estimator; OF uses `!FINAL_ETOT_IS`.",
        "",
        "Old-prefix accounting does not claim zero attempts. Inside the private mount "
        "namespace, exactly 22 ENOENT events are preregistered per point: 10 classid "
        "events (launcher plus four ranks), four rank ucx.conf probes, and eight rank "
        "opens of the hidden old prefix. Successful old access/exec, an unknown failed "
        "probe, an old mapping, or an unexpected mapping rejects the replay.",
        "",
        "## Points",
        "",
        "| replay | reference | tier | dE (meV/atom) | dP (GPa) | R8 unchanged |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for point in payload["points"]:
        lines.append(
            "| {replay_experiment_id} | {reference_experiment_id} | {tier} | "
            "{delta_energy_mev_per_atom} | {delta_pressure_gpa} | {unchanged} |".format(
                replay_experiment_id=point["replay_experiment_id"],
                reference_experiment_id=point["reference_experiment_id"],
                tier=point["equivalence"]["tier"],
                delta_energy_mev_per_atom=point["equivalence"][
                    "delta_energy_mev_per_atom"
                ],
                delta_pressure_gpa=point["equivalence"]["delta_pressure_gpa"],
                unchanged=point["r8_replacement"]["conclusion_unchanged"],
            )
        )
    if payload["failures"]:
        lines.extend(["", "## Failures", ""])
        lines.extend(f"- {failure}" for failure in payload["failures"])
    return "\n".join(lines) + "\n"


def analyze(
    project_root: Path,
    config_path: Path,
    manifest_path: Path,
    output_directory: Path,
    *,
    require_committed: bool = True,
) -> dict:
    project_root = project_root.resolve()
    if require_committed:
        if not git_clean(project_root):
            raise ValueError("refusing MPI-equivalence analysis from a dirty worktree")
        analysis_code = [
            Path(__file__).resolve(),
            project_root / "scripts" / "s1_mpi_prefix_equivalence_common.py",
            project_root / "scripts" / "s1_runtime_relocation_elf.py",
            project_root / "scripts" / "validate_s1_mpi_prefix_equivalence.py",
            project_root / "scripts" / "runtime_relocation_audit_launcher.py",
            project_root / "scripts" / "runtime_relocation_namespace_launcher.py",
            project_root / "scripts" / "runtime_relocation_rank_wrapper.py",
            project_root / "scripts" / "parse_s1_single.py",
            project_root / "scripts" / "analyze_s1_non_equilibrium.py",
            project_root / "scripts" / "analyze_s1_eos.py",
        ]
        code_failures = require_tracked_at_head(project_root, analysis_code)
        if code_failures:
            raise ValueError(
                "analysis implementation is not immutable at HEAD:\n- "
                + "\n- ".join(code_failures)
            )
    manifest_validation = validate(
        project_root,
        config_path,
        manifest_path,
        require_committed=require_committed,
    )
    config = json.loads(config_path.read_text(encoding="utf-8"))
    rows = read_tsv(manifest_path)
    source = config["source"]
    r8_config_path = path_from_project(project_root, source["r8_config_path"])
    r8_summary_path = path_from_project(project_root, source["r8_summary_path"])
    r8_config = json.loads(r8_config_path.read_text(encoding="utf-8"))
    r8_summary = json.loads(r8_summary_path.read_text(encoding="utf-8"))
    core_reference = r8_config["core_reference"]
    core_summary_path = path_from_project(project_root, core_reference["summary_path"])
    if sha256(core_summary_path) != core_reference["summary_sha256"]:
        raise ValueError("frozen S1 core summary SHA-256 mismatch")
    core_summary = json.loads(core_summary_path.read_text(encoding="utf-8"))

    points = []
    failures: list[str] = []
    for row in rows:
        replay_id = row["replay_experiment_id"]
        reference_id = row["reference_experiment_id"]
        run_failures = validate_replay_run(
            project_root,
            config,
            row,
            require_committed=require_committed,
        )
        failures.extend(run_failures)
        reference_run = project_root / "runs" / reference_id
        replay_run = project_root / "runs" / replay_id
        try:
            reference_metadata, reference_log, reference_result = reparse_run(reference_run)
            replay_metadata, replay_log, replay_result = reparse_run(replay_run)
            reference_raw = raw_observables(
                reference_log.read_text(encoding="utf-8", errors="replace"),
                str(reference_metadata["solver"]),
                int(reference_metadata["atom_count"]),
            )
            replay_raw = raw_observables(
                replay_log.read_text(encoding="utf-8", errors="replace"),
                str(replay_metadata["solver"]),
                int(replay_metadata["atom_count"]),
            )
            equivalence = equivalence_tier(reference_raw, replay_raw)
            replacement = replacement_conclusion(
                r8_config,
                r8_summary,
                core_summary,
                row["material"],
                row["series_id"],
                replay_id,
                _energy(replay_metadata, replay_result),
                float(replay_result["pressure_gpa"]),
            )
            audit = json.loads(
                (replay_run / "mpi_runtime_audit" / "audit.json").read_text(
                    encoding="utf-8"
                )
            )
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            failures.append(f"{replay_id}:analysis_failed:{error}")
            continue
        if not equivalence["scientific_tolerance_passed"]:
            failures.append(f"{replay_id}:strict_scientific_equivalence_failed")
        if not replacement["conclusion_unchanged"]:
            failures.append(f"{replay_id}:r8_conclusion_changed_after_v100_replacement")
        points.append(
            {
                "replay_experiment_id": replay_id,
                "reference_experiment_id": reference_id,
                "material": row["material"],
                "series_id": row["series_id"],
                "solver": row["solver"],
                "energy_observable": (
                    "zero_temp_extrapolated_energy_ev_per_atom"
                    if row["solver"] == "ksdft"
                    else "energy_ev_per_atom"
                ),
                "reference_raw": reference_raw,
                "replay_raw": replay_raw,
                "equivalence": equivalence,
                "runtime_audit_status": audit.get("status"),
                "runtime_audit": {
                    key: audit.get(key)
                    for key in (
                        "old_prefix_mapped_object_count",
                        "unexpected_mapped_object_count",
                        "transient_system_mapped_object_count",
                        "old_prefix_access_attempt_count",
                        "old_prefix_successful_access_count",
                        "old_prefix_exec_success_count",
                        "registered_old_prefix_failed_probe_count",
                        "unknown_old_prefix_failed_probe_count",
                        "registered_probe_count_mismatch_count",
                    )
                },
                "r8_replacement": replacement,
            }
        )

    complete = len(points) == len(rows) == 6
    scientific_count = sum(
        point["equivalence"]["scientific_tolerance_passed"] for point in points
    )
    conclusion_count = sum(
        point["r8_replacement"]["conclusion_unchanged"] for point in points
    )
    runtime_count = sum(point["runtime_audit_status"] == "accepted" for point in points)
    closure_tiers = set(
        config["acceptance"]["storage_equivalence_tiers_diagnostic_only"]
    )
    closure_count = sum(point["equivalence"]["tier"] in closure_tiers for point in points)
    scientific_only_count = sum(
        point["equivalence"]["tier"] == "scientific_tolerance_only" for point in points
    )
    hard_gate_passed = (
        complete
        and not failures
        and scientific_count == 6
        and conclusion_count == 6
        and runtime_count == 6
    )
    if hard_gate_passed:
        status = "accepted"
        action = "close_runtime_relocation_equivalence_and_keep_s1_r8_conclusion"
    else:
        status = "rejected"
        pure_audit_tokens = (
            "namespace_launch_failed",
            "namespace_payload_exit_code",
            "required_strace_unavailable",
            "strace_identity",
            "rank_handshake",
            "initial_map_capture",
        )
        pure_audit_failure = bool(failures) and all(
            any(token in failure for token in pure_audit_tokens) for failure in failures
        )
        action = (
            "fix_audit_operation_and_retry_only_six_registered_points"
            if pure_audit_failure
            else "rerun_full_42_point_s1_r8_matrix_under_recovery_prefix"
        )

    payload = {
        "schema_version": 1,
        "protocol_revision": config["protocol_revision"],
        "six_point_status": status,
        "recommended_action": action,
        "expected_points": 6,
        "analyzed_points": len(points),
        "scientific_gate_passed_count": scientific_count,
        "storage_closure_tier_count": closure_count,
        "scientific_tolerance_only_count": scientific_only_count,
        "r8_conclusion_unchanged_count": conclusion_count,
        "runtime_audit_accepted_count": runtime_count,
        "energy_observables": {
            "ofdft": "total_energy_from_FINAL_ETOT_IS",
            "ksdft_machine_field": "zero_temp_extrapolated_energy_ev_per_atom",
            "ksdft_interpretation": "entropy_corrected_estimator_not_exact_zero_temperature_label",
        },
        "thresholds": config["acceptance"],
        "old_prefix_access_interpretation": {
            "successful_access_required": 0,
            "successful_exec_required": 0,
            "unknown_failed_probe_required": 0,
            "registered_failed_probe_expected_count_per_point": config[
                "runtime_audit"
            ]["registered_old_prefix_failed_probe_count"],
            "registered_failed_probes": config["runtime_audit"][
                "registered_old_prefix_failed_probes"
            ],
            "claim_zero_attempts": False,
        },
        "decision_policy": {
            "full_42_rerun_triggers": config["acceptance"]["full_42_rerun_triggers"],
            "six_point_retry_after_fix_triggers": config["acceptance"][
                "six_point_retry_after_fix_triggers"
            ],
        },
        "manifest_validation": manifest_validation,
        "analysis_provenance": {
            "analyzer_commit": subprocess.check_output(
                ["git", "-C", str(project_root), "rev-parse", "HEAD"], text=True
            ).strip(),
            "analyzer_sha256": sha256(Path(__file__).resolve()),
            "config_path": str(config_path),
            "config_sha256": sha256(config_path),
            "manifest_path": str(manifest_path),
            "manifest_sha256": sha256(manifest_path),
            "r8_summary_path": str(r8_summary_path),
            "r8_summary_sha256": sha256(r8_summary_path),
            "core_summary_path": str(core_summary_path),
            "core_summary_sha256": sha256(core_summary_path),
        },
        "points": points,
        "failures": failures,
    }
    safe_payload = json_safe(payload)
    output_directory.mkdir(parents=True, exist_ok=True)
    (output_directory / "summary.json").write_text(
        json.dumps(safe_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    header = (
        "replay_experiment_id\treference_experiment_id\tmaterial\tseries_id\tsolver\t"
        "tier\tdelta_energy_mev_per_atom\tdelta_pressure_gpa\t"
        "scientific_tolerance_passed\truntime_audit_status\t"
        "old_prefix_access_attempt_count\told_prefix_successful_access_count\t"
        "r8_conclusion_unchanged"
    )
    lines = [header]
    for point in safe_payload["points"]:
        lines.append(
            "\t".join(
                str(value)
                for value in (
                    point["replay_experiment_id"],
                    point["reference_experiment_id"],
                    point["material"],
                    point["series_id"],
                    point["solver"],
                    point["equivalence"]["tier"],
                    point["equivalence"]["delta_energy_mev_per_atom"],
                    point["equivalence"]["delta_pressure_gpa"],
                    point["equivalence"]["scientific_tolerance_passed"],
                    point["runtime_audit_status"],
                    point["runtime_audit"]["old_prefix_access_attempt_count"],
                    point["runtime_audit"]["old_prefix_successful_access_count"],
                    point["r8_replacement"]["conclusion_unchanged"],
                )
            )
        )
    (output_directory / "points.tsv").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    (output_directory / "README.md").write_text(_readme(safe_payload), encoding="utf-8")
    return safe_payload


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("output_directory", type=Path)
    parser.add_argument(
        "--config", type=Path, default=project_root / CANONICAL_CONFIG_PATH
    )
    parser.add_argument(
        "--manifest", type=Path, default=project_root / CANONICAL_MANIFEST_PATH
    )
    args = parser.parse_args()
    payload = analyze(
        project_root,
        args.config.resolve(),
        args.manifest.resolve(),
        args.output_directory.resolve(),
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["six_point_status"] == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
