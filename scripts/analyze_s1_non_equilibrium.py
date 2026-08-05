#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import subprocess
from pathlib import Path

from analyze_s1_eos import fit_bm3
from validate_s1_non_equilibrium_manifest import read_manifest, sha256, validate


def _project_path(project_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else project_root / path


def _energy(metadata: dict, result: dict) -> float | None:
    if not result.get("converged"):
        return None
    if metadata["solver"] == "ksdft":
        value = result.get("zero_temp_extrapolated_energy_ev_per_atom")
    else:
        value = result.get("energy_ev_per_atom")
    if value is None or not math.isfinite(float(value)):
        return None
    return float(value)


def compare_series(
    baseline: list[dict],
    refined: list[dict],
    reference_ratio: float,
    energy_threshold_mev: float,
    pressure_threshold_gpa: float | None,
) -> dict:
    baseline_by_ratio = {
        round(float(point["volume_ratio"]), 12): point for point in baseline
    }
    refined_by_ratio = {
        round(float(point["volume_ratio"]), 12): point for point in refined
    }
    reference_ratio = round(float(reference_ratio), 12)
    if (
        set(baseline_by_ratio) != set(refined_by_ratio)
        or reference_ratio not in baseline_by_ratio
    ):
        return {
            "status": "indeterminate",
            "failure_reason": "mismatched_ratios_or_missing_reference_volume",
        }
    baseline_reference = baseline_by_ratio[reference_ratio]["energy_ev_per_atom"]
    refined_reference = refined_by_ratio[reference_ratio]["energy_ev_per_atom"]
    rows = []
    for ratio in sorted(baseline_by_ratio):
        base = baseline_by_ratio[ratio]
        dense = refined_by_ratio[ratio]
        base_relative = (base["energy_ev_per_atom"] - baseline_reference) * 1000.0
        dense_relative = (dense["energy_ev_per_atom"] - refined_reference) * 1000.0
        pressure_difference = dense["pressure_gpa"] - base["pressure_gpa"]
        rows.append(
            {
                "volume_ratio": ratio,
                "baseline_experiment_id": base["experiment_id"],
                "refined_experiment_id": dense["experiment_id"],
                "baseline_relative_energy_mev_per_atom": base_relative,
                "refined_relative_energy_mev_per_atom": dense_relative,
                "relative_energy_difference_mev_per_atom": dense_relative - base_relative,
                "baseline_pressure_gpa": base["pressure_gpa"],
                "refined_pressure_gpa": dense["pressure_gpa"],
                "pressure_difference_gpa": pressure_difference,
            }
        )
    max_energy = max(abs(row["relative_energy_difference_mev_per_atom"]) for row in rows)
    max_pressure = max(abs(row["pressure_difference_gpa"]) for row in rows)
    energy_passed = max_energy < energy_threshold_mev
    pressure_passed = (
        True if pressure_threshold_gpa is None else max_pressure < pressure_threshold_gpa
    )
    return {
        "status": "accepted" if energy_passed and pressure_passed else "rejected",
        "max_relative_energy_difference_mev_per_atom": max_energy,
        "max_pressure_difference_gpa": max_pressure,
        "relative_energy_passed": energy_passed,
        "pressure_passed": pressure_passed,
        "pressure_acceptance_role": (
            "diagnostic_only" if pressure_threshold_gpa is None else "hard_gate"
        ),
        "rows": rows,
        "thresholds": {
            "max_relative_energy_difference_mev_per_atom": energy_threshold_mev,
            "max_pressure_difference_gpa": pressure_threshold_gpa,
            "comparison": "strict_less_than",
        },
    }


def _fit_quality(points: list[dict], max_residual_mev: float = 1.0) -> tuple[dict, list[str]]:
    points = sorted(points, key=lambda point: point["volume_per_atom_angstrom3"])
    fit = fit_bm3(
        [point["volume_per_atom_angstrom3"] for point in points],
        [point["energy_ev_per_atom"] for point in points],
    )
    failures = []
    volumes = [point["volume_per_atom_angstrom3"] for point in points]
    if not volumes[0] < fit["v0_angstrom3_per_atom"] < volumes[-1]:
        failures.append("fitted_v0_outside_sample_range")
    if fit["b0_gpa"] <= 0.0:
        failures.append("nonpositive_bulk_modulus")
    if fit["max_abs_residual_mev_per_atom"] >= max_residual_mev:
        failures.append("fit_residual_threshold_failed")
    energies = [point["energy_ev_per_atom"] for point in points]
    minimum_index = energies.index(min(energies))
    fit["sampled_shape_diagnostic"] = {
        "acceptance_role": "diagnostic_only",
        "discrete_minimum_at_sampled_endpoint": minimum_index in (0, len(points) - 1),
        "discrete_minimum_volume_ratio": points[minimum_index]["volume_ratio"],
    }
    return fit, failures


def _baseline_points(core_summary: dict, material: str, series_id: str) -> list[dict]:
    payload = core_summary["series"][f"{material}/{series_id}"]
    if payload["status"] != "accepted":
        raise ValueError(f"frozen baseline series is not accepted: {material}/{series_id}")
    return payload["points"]


def _read_refined_point(project_root: Path, row: dict[str, str]) -> tuple[dict | None, list[str]]:
    run_directory = project_root / "runs" / row["experiment_id"]
    failures = []
    required = (
        "input_metadata.json",
        "experiment_metadata.json",
        "result.json",
        "INPUT_SHA256SUMS",
    )
    if any(not (run_directory / name).is_file() for name in required):
        return None, ["missing_run_artifacts"]
    metadata = json.loads((run_directory / "input_metadata.json").read_text(encoding="utf-8"))
    experiment = json.loads(
        (run_directory / "experiment_metadata.json").read_text(encoding="utf-8")
    )
    result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))
    if sha256(run_directory / "input_metadata.json") != row["input_metadata_sha256"]:
        failures.append("run_input_metadata_sha256_mismatch")
    if experiment.get("experiment_id") != row["experiment_id"]:
        failures.append("experiment_id_mismatch")
    if metadata.get("series_id") != row["series_id"]:
        failures.append("series_id_mismatch")
    if metadata.get("baseline_experiment_id") != row["reference_experiment_id"]:
        failures.append("reference_experiment_id_mismatch")
    if not result.get("converged"):
        failures.append("run_not_converged")
    energy = _energy(metadata, result)
    if energy is None:
        failures.append("missing_registered_energy_observable")
    return (
        {
            "abacus_sha256": experiment.get("abacus_sha256"),
            "code_commit": experiment.get("code_commit"),
            "converged": bool(result.get("converged")),
            "ecutrho_ry": metadata.get("ecutrho_ry"),
            "ecutwfc_ry": metadata.get("ecutwfc_ry"),
            "energy_ev_per_atom": energy,
            "energy_observable": metadata.get("energy_observable"),
            "experiment_id": row["experiment_id"],
            "kmesh": metadata.get("kmesh"),
            "material": row["material"],
            "pressure_gpa": result.get("pressure_gpa"),
            "series_id": row["series_id"],
            "smearing_sigma_ry": metadata.get("smearing_sigma_ry"),
            "solver": metadata.get("solver"),
            "stru_sha256": metadata.get("stru_sha256"),
            "volume_per_atom_angstrom3": metadata.get("volume_per_atom_angstrom3"),
            "volume_ratio": round(float(metadata.get("volume_ratio")), 12),
        },
        failures,
    )


def _readme(payload: dict) -> str:
    lines = [
        "# S1-R8 non-equilibrium cutoff and k-mesh convergence",
        "",
        f"Status: `{payload['s1_r8_status']}`. G1 remains `{payload['g1_status']}`.",
        "",
        "All energy comparisons use the seven raw points after anchoring each curve at "
        "`V/V0=1.00`; BM3 fits are completeness/shape QA and do not replace the raw maximum.",
        "For KSDFT the parsed ABACUS `E_KS(sigma->0)` field is reported as an "
        "entropy-corrected estimator, not as an exact zero-temperature label.",
        "",
        "| Material | Series | Axis | Max anchored energy diff (meV/atom) | "
        "Max pressure diff (GPa) | Status |",
        "|---|---|---|---:|---:|---|",
    ]
    for key, series in sorted(payload["series"].items()):
        comparison = series.get("comparison", {})
        lines.append(
            "| {material} | `{series_id}` | {axis} | {energy:.9f} | {pressure:.9f} | "
            "`{status}` |".format(
                material=series["material"],
                series_id=series["series_id"],
                axis=series["comparison_axis"],
                energy=float(
                    comparison.get("max_relative_energy_difference_mev_per_atom", float("nan"))
                ),
                pressure=float(comparison.get("max_pressure_difference_gpa", float("nan"))),
                status=series["status"],
            )
        )
    lines.extend(
        [
            "",
            "## G1 items still pending",
            "",
            *(f"- `{item}`" for item in payload["g1_pending"]),
            "",
        ]
    )
    return "\n".join(lines)


def analyze(
    project_root: Path, config_path: Path, manifest_path: Path, output_directory: Path
) -> dict:
    manifest_validation = validate(project_root, config_path, manifest_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    rows = read_manifest(manifest_path)
    reference = config["core_reference"]
    core_summary_path = _project_path(project_root, reference["summary_path"])
    core_summary = json.loads(core_summary_path.read_text(encoding="utf-8"))
    reference_ratio = float(config["acceptance"]["relative_energy_reference_volume_ratio"])
    grouped: dict[tuple[str, str], list[dict]] = {}
    runtime_failures: dict[tuple[str, str], list[str]] = {}
    input_code_commits = set()
    input_abacus_hashes = set()

    for row in rows:
        key = (row["material"], row["series_id"])
        point, failures = _read_refined_point(project_root, row)
        runtime_failures.setdefault(key, []).extend(
            f"{row['experiment_id']}:{failure}" for failure in failures
        )
        if point is not None:
            grouped.setdefault(key, []).append(point)
            input_code_commits.add(point["code_commit"])
            input_abacus_hashes.add(point["abacus_sha256"])

    series_payload = {}
    global_failures = []
    points_lines = [
        "material\tseries_id\tcomparison_axis\tvolume_ratio\tbaseline_experiment_id\t"
        "refined_experiment_id\tbaseline_relative_energy_mev_per_atom\t"
        "refined_relative_energy_mev_per_atom\trelative_energy_difference_mev_per_atom\t"
        "pressure_difference_gpa"
    ]
    comparison_lines = [
        "material\tseries_id\tcomparison_axis\tmax_relative_energy_difference_mev_per_atom\t"
        "max_pressure_difference_gpa\tstatus"
    ]

    for material in sorted(config["materials"]):
        for series_id in config["series_order"]:
            key = (material, series_id)
            spec = config["materials"][material][series_id]
            failures = list(runtime_failures.get(key, []))
            refined = grouped.get(key, [])
            expected_ratios = {
                round(float(value), 12) for value in config["volume_ratios"]
            }
            actual_ratios = {point["volume_ratio"] for point in refined if point["converged"]}
            if actual_ratios != expected_ratios:
                failures.append("incomplete_or_duplicate_seven_point_series")
            baseline = _baseline_points(core_summary, material, spec["baseline_series_id"])
            baseline_by_id = {point["experiment_id"]: point for point in baseline}
            for point in refined:
                reference_id = next(
                    row["reference_experiment_id"]
                    for row in rows
                    if row["experiment_id"] == point["experiment_id"]
                )
                base = baseline_by_id.get(reference_id)
                if base is None:
                    failures.append(f"{point['experiment_id']}:reference_not_in_baseline")
                    continue
                if point["abacus_sha256"] != base["abacus_sha256"]:
                    failures.append(f"{point['experiment_id']}:abacus_binary_mismatch")
                if point["stru_sha256"] != base["stru_sha256"]:
                    failures.append(f"{point['experiment_id']}:structure_mismatch")

            fit = None
            fit_failures = []
            comparison = {"status": "indeterminate", "failure_reason": "invalid_series"}
            if not failures:
                try:
                    fit, fit_failures = _fit_quality(refined)
                except ValueError as error:
                    fit_failures = [str(error)]
                failures.extend(fit_failures)
            if not failures:
                if spec["comparison_axis"] == "cutoff":
                    energy_threshold = float(
                        config["acceptance"][
                            "cutoff_max_relative_energy_difference_mev_per_atom"
                        ]
                    )
                    pressure_threshold = float(
                        config["acceptance"]["cutoff_max_pressure_difference_gpa"]
                    )
                else:
                    energy_threshold = float(
                        config["acceptance"][
                            "kmesh_max_relative_energy_difference_mev_per_atom"
                        ]
                    )
                    pressure_threshold = None
                comparison = compare_series(
                    baseline,
                    refined,
                    reference_ratio,
                    energy_threshold,
                    pressure_threshold,
                )
            status = "indeterminate" if failures else comparison["status"]
            if status != "accepted":
                global_failures.append(f"{material}/{series_id}:{status}")
            series_payload[f"{material}/{series_id}"] = {
                "material": material,
                "series_id": series_id,
                "comparison_axis": spec["comparison_axis"],
                "baseline_series_id": spec["baseline_series_id"],
                "status": status,
                "failures": failures,
                "fit": fit,
                "comparison": comparison,
                "points": refined,
            }
            if comparison.get("rows"):
                for point_row in comparison["rows"]:
                    points_lines.append(
                        "\t".join(
                            str(value)
                            for value in (
                                material,
                                series_id,
                                spec["comparison_axis"],
                                point_row["volume_ratio"],
                                point_row["baseline_experiment_id"],
                                point_row["refined_experiment_id"],
                                point_row["baseline_relative_energy_mev_per_atom"],
                                point_row["refined_relative_energy_mev_per_atom"],
                                point_row["relative_energy_difference_mev_per_atom"],
                                point_row["pressure_difference_gpa"],
                            )
                        )
                    )
                comparison_lines.append(
                    "\t".join(
                        str(value)
                        for value in (
                            material,
                            series_id,
                            spec["comparison_axis"],
                            comparison["max_relative_energy_difference_mev_per_atom"],
                            comparison["max_pressure_difference_gpa"],
                            status,
                        )
                    )
                )

    statuses = {series["status"] for series in series_payload.values()}
    if "indeterminate" in statuses:
        overall_status = "indeterminate"
    elif "rejected" in statuses:
        overall_status = "rejected"
    else:
        overall_status = "accepted"
    payload = {
        "analysis_provenance": {
            "analyzer_code_commit": subprocess.check_output(
                ["git", "-C", str(project_root), "rev-parse", "HEAD"], text=True
            ).strip(),
            "analyzer_script_sha256": sha256(Path(__file__).resolve()),
            "config_path": str(config_path.relative_to(project_root)),
            "config_sha256": sha256(config_path),
            "manifest_path": str(manifest_path.relative_to(project_root)),
            "manifest_sha256": sha256(manifest_path),
            "core_summary_path": str(core_summary_path.relative_to(project_root)),
            "core_summary_sha256": sha256(core_summary_path),
            "input_abacus_sha256_values": sorted(input_abacus_hashes),
            "input_code_commits": sorted(input_code_commits),
        },
        "manifest_validation": manifest_validation,
        "s1_r8_status": overall_status,
        "expected_calculations": 42,
        "selected_calculations": sum(len(points) for points in grouped.values()),
        "accepted_comparisons": sum(
            series["status"] == "accepted" for series in series_payload.values()
        ),
        "energy_observables": {
            "ofdft": "total_energy",
            "ksdft_machine_field": "zero_temp_extrapolated_energy_ev_per_atom",
            "ksdft_interpretation": "entropy_corrected_estimator_not_exact_zero_temperature_label",
        },
        "series": series_payload,
        "failures": global_failures,
        "g1_status": "pending",
        "g1_pending": [
            "integrated_electron_number_check_not_nominal_input_count",
            "third_smearing_or_ultradense_k_density_potential_derivative_label_audit",
            "independent_ofdft_cross_code_eos_pressure_check",
            "ks_nonlocal_to_ks_local_to_of_local_three_layer_validation",
            "small_displacement_strain_reference_density_and_energy_component_delivery",
            "ten_case_one_command_regeneration_failure_rate",
        ],
    }
    output_directory.mkdir(parents=True, exist_ok=True)
    (output_directory / "summary.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_directory / "points.tsv").write_text(
        "\n".join(points_lines) + "\n", encoding="utf-8"
    )
    (output_directory / "comparisons.tsv").write_text(
        "\n".join(comparison_lines) + "\n", encoding="utf-8"
    )
    (output_directory / "README.md").write_text(_readme(payload), encoding="utf-8")
    return payload


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("output_directory", type=Path)
    parser.add_argument(
        "--config",
        type=Path,
        default=project_root / "config" / "S1_non_equilibrium_convergence.json",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=project_root / "config" / "S1_non_equilibrium_run_manifest.tsv",
    )
    args = parser.parse_args()
    payload = analyze(
        project_root,
        args.config.resolve(),
        args.manifest.resolve(),
        args.output_directory.resolve(),
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["s1_r8_status"] == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
