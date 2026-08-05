#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import subprocess
from collections import defaultdict
from pathlib import Path


EV_PER_ANGSTROM3_TO_GPA = 160.21766208
EXPECTED_SERIES = ("ofdft", "ksdft_standard", "ksdft_half")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bm3_energy(volume: float, e0: float, v0: float, b0: float, bp: float) -> float:
    x = (v0 / volume) ** (2.0 / 3.0) - 1.0
    return e0 + 9.0 * v0 * b0 / 16.0 * (2.0 * x * x + (bp - 4.0) * x**3)


def _linear_e0_b0(volumes: list[float], energies: list[float], v0: float, bp: float):
    z = []
    for volume in volumes:
        x = (v0 / volume) ** (2.0 / 3.0) - 1.0
        z.append(9.0 * v0 / 16.0 * (2.0 * x * x + (bp - 4.0) * x**3))
    z_mean = sum(z) / len(z)
    e_mean = sum(energies) / len(energies)
    denominator = sum((value - z_mean) ** 2 for value in z)
    if denominator <= 1e-24:
        return None
    b0 = sum((zv - z_mean) * (ev - e_mean) for zv, ev in zip(z, energies)) / denominator
    e0 = e_mean - b0 * z_mean
    residuals = [ev - (e0 + b0 * zv) for zv, ev in zip(z, energies)]
    return e0, b0, residuals


def fit_bm3(volumes: list[float], energies: list[float]) -> dict:
    if len(volumes) != len(energies) or len(volumes) < 4:
        raise ValueError("BM3 fit requires at least four volume/energy pairs")
    if len(set(volumes)) != len(volumes):
        raise ValueError("BM3 fit requires unique volumes")
    paired = sorted(zip(volumes, energies))
    volumes = [item[0] for item in paired]
    energies = [item[1] for item in paired]
    v_low, v_high = volumes[0], volumes[-1]
    bp_low, bp_high = 1.0, 8.0
    best = None
    v_step = (v_high - v_low) / 240.0
    bp_step = (bp_high - bp_low) / 140.0

    def evaluate(v0: float, bp: float):
        solved = _linear_e0_b0(volumes, energies, v0, bp)
        if solved is None:
            return None
        e0, b0, residuals = solved
        if not math.isfinite(b0) or b0 <= 0.0:
            return None
        sse = sum(value * value for value in residuals)
        return sse, v0, bp, e0, b0, residuals

    for vi in range(241):
        v0 = v_low + vi * v_step
        for bi in range(141):
            bp = bp_low + bi * bp_step
            candidate = evaluate(v0, bp)
            if candidate is not None and (best is None or candidate[:3] < best[:3]):
                best = candidate
    if best is None:
        raise ValueError("BM3 fit found no positive-curvature solution")

    # Follow the correlated V0/B0' valley instead of repeatedly narrowing an
    # axis-aligned box, which can exclude the true minimum after a coarse grid.
    v_step = (v_high - v_low) / 120.0
    bp_step = (bp_high - bp_low) / 70.0
    for _ in range(10000):
        candidate_best = best
        for delta_v, delta_bp in itertools.product(
            (-v_step, 0.0, v_step), (-bp_step, 0.0, bp_step)
        ):
            if delta_v == 0.0 and delta_bp == 0.0:
                continue
            v0 = best[1] + delta_v
            bp = best[2] + delta_bp
            if not (v_low <= v0 <= v_high and 0.1 <= bp <= 12.0):
                continue
            candidate = evaluate(v0, bp)
            if candidate is not None and candidate[:3] < candidate_best[:3]:
                candidate_best = candidate
        if candidate_best[0] < best[0]:
            best = candidate_best
            continue
        v_step /= 2.0
        bp_step /= 2.0
        if v_step < 1e-12 and bp_step < 1e-12:
            break

    sse, v0, bp, e0, b0, residuals = best
    return {
        "e0_ev_per_atom": e0,
        "v0_angstrom3_per_atom": v0,
        "b0_ev_per_angstrom3": b0,
        "b0_gpa": b0 * EV_PER_ANGSTROM3_TO_GPA,
        "b0_prime": bp,
        "rmse_mev_per_atom": math.sqrt(sse / len(residuals)) * 1000.0,
        "max_abs_residual_mev_per_atom": max(abs(value) for value in residuals) * 1000.0,
    }


def compare_sigma_series(
    standard: list[dict],
    half: list[dict],
    standard_fit: dict,
    half_fit: dict,
    energy_threshold_mev: float = 2.0,
    volume_threshold_percent: float = 0.2,
) -> dict:
    standard_by_ratio = {round(float(point["volume_ratio"]), 12): point for point in standard}
    half_by_ratio = {round(float(point["volume_ratio"]), 12): point for point in half}
    if set(standard_by_ratio) != set(half_by_ratio) or 1.0 not in standard_by_ratio:
        return {"status": "indeterminate", "failure_reason": "mismatched_ratios_or_missing_v100"}
    standard_reference = standard_by_ratio[1.0]["energy_ev_per_atom"]
    half_reference = half_by_ratio[1.0]["energy_ev_per_atom"]
    rows = []
    for ratio in sorted(standard_by_ratio):
        std_relative = (standard_by_ratio[ratio]["energy_ev_per_atom"] - standard_reference) * 1000.0
        half_relative = (half_by_ratio[ratio]["energy_ev_per_atom"] - half_reference) * 1000.0
        rows.append(
            {
                "volume_ratio": ratio,
                "standard_relative_energy_mev_per_atom": std_relative,
                "half_relative_energy_mev_per_atom": half_relative,
                "difference_mev_per_atom": half_relative - std_relative,
            }
        )
    max_difference = max(abs(row["difference_mev_per_atom"]) for row in rows)
    standard_v0 = standard_fit["v0_angstrom3_per_atom"]
    half_v0 = half_fit["v0_angstrom3_per_atom"]
    volume_difference = abs(half_v0 - standard_v0) / standard_v0 * 100.0
    energy_passed = max_difference < energy_threshold_mev
    volume_passed = volume_difference < volume_threshold_percent
    return {
        "status": "accepted" if energy_passed and volume_passed else "rejected",
        "max_relative_energy_difference_mev_per_atom": max_difference,
        "equilibrium_volume_difference_percent": volume_difference,
        "relative_energy_passed": energy_passed,
        "equilibrium_volume_passed": volume_passed,
        "rows": rows,
        "thresholds": {
            "max_relative_energy_difference_mev_per_atom": energy_threshold_mev,
            "max_equilibrium_volume_difference_percent": volume_threshold_percent,
        },
    }


def _energy_for_point(metadata: dict, result: dict) -> float:
    if metadata["solver"] == "ksdft":
        value = result.get("zero_temp_extrapolated_energy_ev_per_atom")
    else:
        value = result.get("energy_ev_per_atom")
    if value is None or not math.isfinite(float(value)):
        raise ValueError("missing finite pre-registered EOS energy observable")
    return float(value)


def _fit_quality(points: list[dict], fit: dict, max_residual_mev: float) -> tuple[bool, list[str]]:
    failures = []
    volumes = [point["volume_per_atom_angstrom3"] for point in points]
    if not volumes[0] < fit["v0_angstrom3_per_atom"] < volumes[-1]:
        failures.append("fitted_v0_outside_sample_range")
    if fit["b0_gpa"] <= 0.0:
        failures.append("nonpositive_bulk_modulus")
    if fit["max_abs_residual_mev_per_atom"] >= max_residual_mev:
        failures.append("fit_residual_threshold_failed")
    return not failures, failures


def _sampled_shape_diagnostic(points: list[dict]) -> dict:
    energies = [float(point["energy_ev_per_atom"]) for point in points]
    minimum_index = energies.index(min(energies))
    return {
        "acceptance_role": "diagnostic_only",
        "discrete_minimum_at_sampled_endpoint": minimum_index in (0, len(points) - 1),
        "discrete_minimum_volume_ratio": float(points[minimum_index]["volume_ratio"]),
        "reason": (
            "continuous_BM3_minimum_and_fit_quality_are_the_registered_acceptance_checks"
        ),
    }


def _pressure_diagnostic(points: list[dict]) -> dict:
    pressures = [float(point["pressure_gpa"]) for point in points]
    return {
        "kind": "finite_smearing_pressure",
        "minimum_gpa": min(pressures),
        "maximum_gpa": max(pressures),
        "crosses_zero_in_sampled_range": min(pressures) <= 0.0 <= max(pressures),
        "acceptance_role": "diagnostic_only",
    }


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("run_directories", nargs="*", type=Path)
    parser.add_argument(
        "--config", type=Path, default=project_root / "config" / "S1_baseline_protocol.json"
    )
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    config_digest = sha256(args.config)
    expected_ratios = [round(float(value), 12) for value in config["volume_ratios"]]
    attempts: dict[tuple[str, str, float], list[dict]] = defaultdict(list)
    global_failures = []

    for run_directory in args.run_directories:
        run_directory = run_directory.resolve()
        metadata = json.loads((run_directory / "input_metadata.json").read_text(encoding="utf-8"))
        result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))
        experiment = json.loads((run_directory / "experiment_metadata.json").read_text(encoding="utf-8"))
        if metadata.get("dataset_kind") != "eos":
            raise ValueError(f"{run_directory} is not an EOS run")
        ratio = round(float(metadata["volume_ratio"]), 12)
        attempts[(metadata["material"], metadata["series_id"], ratio)].append(
            {
                "abacus_sha256": experiment["abacus_sha256"],
                "code_commit": experiment["code_commit"],
                "config_sha256": metadata["config_sha256"],
                "converged": bool(result["converged"]),
                "ecutrho_ry": metadata["ecutrho_ry"],
                "ecutwfc_ry": metadata["ecutwfc_ry"],
                "energy_ev_per_atom": _energy_for_point(metadata, result),
                "energy_observable": metadata["energy_observable"],
                "experiment_id": run_directory.name,
                "failure_reason": result.get("failure_reason"),
                "free_energy_ev_per_atom": result.get("free_energy_ev_per_atom"),
                "kmesh": metadata["kmesh"],
                "nominal_electron_relative_error": result[
                    "electron_count_nominal_relative_error"
                ],
                "pressure_gpa": result["pressure_gpa"],
                "pseudopotential_sha256": metadata["pseudopotential_sha256"],
                "series_id": metadata["series_id"],
                "smearing_sigma_ry": metadata["smearing_sigma_ry"],
                "solver": metadata["solver"],
                "structure": metadata["structure"],
                "structure_id": metadata["structure_id"],
                "stru_sha256": metadata["stru_sha256"],
                "volume_per_atom_angstrom3": metadata["volume_per_atom_angstrom3"],
                "volume_ratio": ratio,
                "xc": metadata["xc"],
            }
        )

    selected = {}
    failed_attempts = 0
    for key, values in attempts.items():
        values.sort(key=lambda item: item["experiment_id"])
        failed_attempts += sum(not item["converged"] for item in values)
        converged = [item for item in values if item["converged"]]
        selected[key] = converged[-1] if converged else values[-1]

    input_config_digests = sorted({point["config_sha256"] for point in selected.values()})
    if input_config_digests and input_config_digests != [config_digest]:
        global_failures.append("input metadata configuration hash does not match analysis config")

    series_payload = {}
    points_tsv = [
        "material\tseries_id\tvolume_ratio\tvolume_per_atom_angstrom3\texperiment_id\t"
        "converged\tenergy_ev_per_atom\tpressure_gpa\tnominal_electron_relative_error"
    ]
    max_residual = float(config["eos_acceptance"]["max_fit_residual_mev_per_atom"])
    for material in sorted(config["materials"]):
        for series_id in EXPECTED_SERIES:
            series_key = f"{material}/{series_id}"
            points = []
            missing = []
            for ratio in expected_ratios:
                point = selected.get((material, series_id, ratio))
                if point is None or not point["converged"]:
                    missing.append(ratio)
                    continue
                points.append(point)
                points_tsv.append(
                    "\t".join(
                        str(value)
                        for value in (
                            material,
                            series_id,
                            ratio,
                            point["volume_per_atom_angstrom3"],
                            point["experiment_id"],
                            point["converged"],
                            point["energy_ev_per_atom"],
                            point["pressure_gpa"],
                            point["nominal_electron_relative_error"],
                        )
                    )
                )
            if missing:
                series_payload[series_key] = {
                    "status": "indeterminate",
                    "missing_or_unconverged_volume_ratios": missing,
                    "points": points,
                }
                global_failures.append(f"{series_key}: incomplete series")
                continue
            points.sort(key=lambda point: point["volume_per_atom_angstrom3"])
            try:
                fit = fit_bm3(
                    [point["volume_per_atom_angstrom3"] for point in points],
                    [point["energy_ev_per_atom"] for point in points],
                )
                quality_passed, quality_failures = _fit_quality(points, fit, max_residual)
            except ValueError as error:
                fit = None
                quality_passed = False
                quality_failures = [str(error)]
            status = "accepted" if quality_passed else "indeterminate"
            if not quality_passed:
                global_failures.append(f"{series_key}: {','.join(quality_failures)}")
            series_payload[series_key] = {
                "status": status,
                "fit": fit,
                "fit_quality_failures": quality_failures,
                "pressure_diagnostic": _pressure_diagnostic(points),
                "sampled_shape_diagnostic": _sampled_shape_diagnostic(points),
                "points": points,
            }

    comparisons = {}
    for material in sorted(config["materials"]):
        standard = series_payload[f"{material}/ksdft_standard"]
        half = series_payload[f"{material}/ksdft_half"]
        if standard["status"] != "accepted" or half["status"] != "accepted":
            comparison = {"status": "indeterminate", "failure_reason": "invalid_sigma_series"}
        else:
            standard_points = standard["points"]
            half_points = half["points"]
            std_sigma = standard_points[0]["smearing_sigma_ry"]
            half_sigma = half_points[0]["smearing_sigma_ry"]
            if not math.isclose(half_sigma, std_sigma / 2.0, rel_tol=0.0, abs_tol=1e-12):
                comparison = {"status": "indeterminate", "failure_reason": "sigma_not_exact_half"}
            else:
                invariant_keys = (
                    "config_sha256",
                    "ecutrho_ry",
                    "ecutwfc_ry",
                    "energy_observable",
                    "kmesh",
                    "pseudopotential_sha256",
                    "structure",
                    "xc",
                )
                mismatch = any(
                    std[key] != half_point[key]
                    for std, half_point in zip(standard_points, half_points)
                    for key in invariant_keys
                ) or any(
                    std["stru_sha256"] != half_point["stru_sha256"]
                    for std, half_point in zip(standard_points, half_points)
                )
                if mismatch:
                    comparison = {
                        "status": "indeterminate",
                        "failure_reason": "sigma_series_metadata_mismatch",
                    }
                else:
                    comparison = compare_sigma_series(
                        standard_points,
                        half_points,
                        standard["fit"],
                        half["fit"],
                        float(
                            config["eos_acceptance"][
                                "max_relative_energy_difference_mev_per_atom"
                            ]
                        ),
                        float(
                            config["eos_acceptance"][
                                "max_equilibrium_volume_difference_percent"
                            ]
                        ),
                    )
        comparisons[material] = comparison
        if comparison["status"] != "accepted":
            global_failures.append(f"{material}: sigma comparison {comparison['status']}")

    complete_count = sum(
        payload["status"] == "accepted" for payload in series_payload.values()
    )
    if global_failures:
        core_status = (
            "rejected"
            if any(comparison.get("status") == "rejected" for comparison in comparisons.values())
            else "indeterminate"
        )
    else:
        core_status = "accepted"
    payload = {
        "analysis_provenance": {
            "analyzer_code_commit": subprocess.check_output(
                ["git", "-C", str(project_root), "rev-parse", "HEAD"], text=True
            ).strip(),
            "analyzer_script_sha256": sha256(Path(__file__).resolve()),
            "config_path": str(args.config.resolve()),
            "config_sha256": config_digest,
            "input_abacus_sha256_values": sorted(
                {point["abacus_sha256"] for point in selected.values()}
            ),
            "input_code_commits": sorted(
                {point["code_commit"] for point in selected.values()}
            ),
            "input_config_sha256_values": input_config_digests,
        },
        "core_eos_status": core_status,
        "expected_calculations": 42,
        "selected_calculations": len(selected),
        "accepted_series": complete_count,
        "failed_attempts": failed_attempts,
        "fit_model": config["eos_acceptance"]["fit_model"],
        "energy_observables": {
            "ofdft": "total_energy",
            "ksdft": config["eos_acceptance"]["ks_energy_observable"],
        },
        "series": series_payload,
        "smearing_comparisons": comparisons,
        "failures": global_failures,
        "g1_status": "pending",
        "g1_pending": [
            "non_equilibrium_cutoff_and_kmesh_relative_energy_checks",
            "integrated_electron_number_check_not_nominal_input_count",
            "independent_program_cross_check",
            "ten_case_regeneration_failure_rate",
        ],
    }
    args.output_directory.mkdir(parents=True, exist_ok=True)
    (args.output_directory / "summary.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (args.output_directory / "points.tsv").write_text(
        "\n".join(points_tsv) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if core_status == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
