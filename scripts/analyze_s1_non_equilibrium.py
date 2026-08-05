#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
from pathlib import Path

from analyze_s1_eos import fit_bm3
from parse_s1_single import parse_log
from validate_s1_non_equilibrium_manifest import read_manifest, sha256, validate


def _project_path(project_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else project_root / path


def _energy(metadata: dict, result: dict) -> float | None:
    if not result.get("converged"):
        return None
    if metadata.get("solver") == "ksdft":
        value = result.get("zero_temp_extrapolated_energy_ev_per_atom")
    else:
        value = result.get("energy_ev_per_atom")
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return value


def _finite_float(value) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


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


def _series_status(
    provenance_failures: list[str], fit_failures: list[str], comparison: dict
) -> str:
    if provenance_failures or comparison.get("status") == "indeterminate":
        return "indeterminate"
    if fit_failures or comparison.get("status") == "rejected":
        return "rejected"
    return "accepted"


def _baseline_points(core_summary: dict, material: str, series_id: str) -> list[dict]:
    payload = core_summary["series"][f"{material}/{series_id}"]
    if payload["status"] != "accepted":
        raise ValueError(f"frozen baseline series is not accepted: {material}/{series_id}")
    return payload["points"]


def _tracked_head_failures(project_root: Path, paths: list[Path]) -> list[str]:
    failures = []
    relative_paths = []
    for path in paths:
        if path.is_symlink():
            failures.append(f"symbolic_link_run_artifact:{path}")
        try:
            relative_paths.append(path.relative_to(project_root).as_posix())
        except ValueError:
            failures.append(f"archive_path_outside_project:{path}")
    if failures or not relative_paths:
        return failures

    tracked = subprocess.run(
        ["git", "-C", str(project_root), "ls-files", "--", *relative_paths],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if tracked.returncode != 0:
        return ["git_ls_files_failed"]
    tracked_paths = {line.strip() for line in tracked.stdout.splitlines() if line.strip()}
    for relative_path in relative_paths:
        if relative_path not in tracked_paths:
            failures.append(f"untracked_run_artifact:{relative_path}")

    clean = subprocess.run(
        ["git", "-C", str(project_root), "diff", "--quiet", "HEAD", "--", *relative_paths],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if clean.returncode != 0:
        failures.append("run_artifact_differs_from_head")
    return failures


def _normalized_run_input(source: bytes) -> bytes:
    pattern = re.compile(br"(?m)^pseudo_dir[ \t]+[^\r\n]*(\r?\n|$)")
    matches = list(pattern.finditer(source))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one source pseudo_dir line, found {len(matches)}")
    return pattern.sub(lambda match: b"pseudo_dir ." + match.group(1), source, count=1)


def _checksum_failures(run_directory: Path, pseudopotential: str) -> list[str]:
    checksum_path = run_directory / "INPUT_SHA256SUMS"
    if not checksum_path.is_file():
        return ["missing_run_artifact:INPUT_SHA256SUMS"]
    records: dict[str, str] = {}
    failures = []
    for line_number, line in enumerate(
        checksum_path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
    ):
        fields = line.split(maxsplit=1)
        if len(fields) != 2 or not re.fullmatch(r"[0-9a-fA-F]{64}", fields[0]):
            failures.append(f"invalid_checksum_line:{line_number}")
            continue
        recorded_path = fields[1]
        if recorded_path.startswith("*"):
            recorded_path = recorded_path[1:]
        basename = Path(recorded_path).name
        if not basename:
            failures.append(f"invalid_checksum_path:{line_number}")
        elif basename in records:
            failures.append(f"duplicate_checksum_basename:{basename}")
        else:
            records[basename] = fields[0].lower()

    expected = {"INPUT", "STRU", "KPT", pseudopotential}
    if set(records) != expected:
        missing = sorted(expected - set(records))
        extra = sorted(set(records) - expected)
        if missing:
            failures.append(f"missing_checksum_entries:{','.join(missing)}")
        if extra:
            failures.append(f"unexpected_checksum_entries:{','.join(extra)}")
    for basename in sorted(expected & set(records)):
        path = run_directory / basename
        if not path.is_file():
            failures.append(f"missing_run_artifact:{basename}")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != records[basename]:
            failures.append(f"checksum_mismatch:{basename}")
    return failures


def _result_reparse_failures(log_path: Path, metadata: dict, result: dict) -> list[str]:
    try:
        reparsed = parse_log(
            log_path.read_text(encoding="utf-8", errors="replace"),
            float(metadata["expected_electrons"]),
            int(metadata["atom_count"]),
            str(metadata["solver"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        return [f"running_log_reparse_failed:{error}"]
    if reparsed != result:
        return ["result_json_does_not_match_running_log"]
    return []


def _archive_failures(
    project_root: Path,
    row: dict[str, str],
    run_directory: Path,
    metadata: dict,
    result: dict,
) -> list[str]:
    failures = []
    source_directory = project_root / row["input_directory"]
    pseudopotential = metadata.get("pseudopotential")
    if not isinstance(pseudopotential, str) or Path(pseudopotential).name != pseudopotential:
        return ["invalid_pseudopotential_basename"]

    required_names = (
        "INPUT",
        "STRU",
        "KPT",
        "input_metadata.json",
        "experiment_metadata.json",
        "result.json",
        "INPUT_SHA256SUMS",
        pseudopotential,
    )
    required_paths = [run_directory / name for name in required_names]
    for name, path in zip(required_names, required_paths):
        if not path.is_file():
            failures.append(f"missing_run_artifact:{name}")

    logs = list(run_directory.glob("OUT.*/running_scf.log"))
    if len(logs) != 1:
        failures.append(f"expected_one_running_scf_log_found:{len(logs)}")
    tracked_paths = [path for path in required_paths if path.is_file()]
    if len(logs) == 1:
        tracked_paths.append(logs[0])
    failures.extend(_tracked_head_failures(project_root, tracked_paths))

    source_metadata = source_directory / "metadata.json"
    if source_metadata.is_file() and (run_directory / "input_metadata.json").is_file():
        if source_metadata.read_bytes() != (run_directory / "input_metadata.json").read_bytes():
            failures.append("run_metadata_differs_from_frozen_source")

    for name in ("STRU", "KPT"):
        source = source_directory / name
        archived = run_directory / name
        if source.is_file() and archived.is_file() and source.read_bytes() != archived.read_bytes():
            failures.append(f"run_{name.lower()}_differs_from_frozen_source")

    source_input = source_directory / "INPUT"
    archived_input = run_directory / "INPUT"
    if source_input.is_file() and archived_input.is_file():
        try:
            expected_input = _normalized_run_input(source_input.read_bytes())
        except ValueError as error:
            failures.append(f"source_input_normalization_failed:{error}")
        else:
            if archived_input.read_bytes() != expected_input:
                failures.append("run_input_has_changes_beyond_pseudo_dir_normalization")

    source_pseudo = project_root / "assets" / "pseudo" / pseudopotential
    archived_pseudo = run_directory / pseudopotential
    if source_pseudo.is_file() and archived_pseudo.is_file():
        if source_pseudo.read_bytes() != archived_pseudo.read_bytes():
            failures.append("run_pseudopotential_differs_from_frozen_source")
        if sha256(archived_pseudo) != metadata.get("pseudopotential_sha256"):
            failures.append("run_pseudopotential_sha256_mismatch")

    failures.extend(_checksum_failures(run_directory, pseudopotential))
    if len(logs) == 1:
        failures.extend(_result_reparse_failures(logs[0], metadata, result))
    return failures


def _read_refined_point(project_root: Path, row: dict[str, str]) -> tuple[dict | None, list[str]]:
    run_directory = project_root / "runs" / row["experiment_id"]
    failures = []
    required = (
        "input_metadata.json",
        "experiment_metadata.json",
        "result.json",
    )
    missing = [name for name in required if not (run_directory / name).is_file()]
    if missing:
        return None, [f"missing_run_artifacts:{','.join(missing)}"]
    try:
        metadata = json.loads(
            (run_directory / "input_metadata.json").read_text(encoding="utf-8")
        )
        experiment = json.loads(
            (run_directory / "experiment_metadata.json").read_text(encoding="utf-8")
        )
        result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return None, [f"invalid_run_json:{error}"]
    if not all(isinstance(payload, dict) for payload in (metadata, experiment, result)):
        return None, ["invalid_run_json_object_type"]
    failures.extend(_archive_failures(project_root, row, run_directory, metadata, result))
    if sha256(run_directory / "input_metadata.json") != row["input_metadata_sha256"]:
        failures.append("run_input_metadata_sha256_mismatch")
    if experiment.get("experiment_id") != row["experiment_id"]:
        failures.append("experiment_id_mismatch")
    abacus_sha256 = experiment.get("abacus_sha256")
    if not isinstance(abacus_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", abacus_sha256
    ):
        failures.append("invalid_abacus_sha256_provenance")
    code_commit = experiment.get("code_commit")
    if not isinstance(code_commit, str) or not re.fullmatch(r"[0-9a-f]{40,64}", code_commit):
        failures.append("invalid_code_commit_provenance")
    else:
        commit = subprocess.run(
            ["git", "-C", str(project_root), "cat-file", "-e", f"{code_commit}^{{commit}}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if commit.returncode != 0:
            failures.append("missing_code_commit_provenance")
    if metadata.get("series_id") != row["series_id"]:
        failures.append("series_id_mismatch")
    if metadata.get("baseline_experiment_id") != row["reference_experiment_id"]:
        failures.append("reference_experiment_id_mismatch")
    if not result.get("converged"):
        failures.append("run_not_converged")
    energy = _energy(metadata, result)
    if energy is None:
        failures.append("missing_registered_energy_observable")
    pressure = _finite_float(result.get("pressure_gpa"))
    if pressure is None:
        failures.append("missing_finite_pressure_gpa")
    volume_per_atom = _finite_float(metadata.get("volume_per_atom_angstrom3"))
    if volume_per_atom is None or volume_per_atom <= 0.0:
        failures.append("missing_positive_volume_per_atom")
    return (
        {
            "abacus_sha256": abacus_sha256,
            "code_commit": code_commit,
            "converged": bool(result.get("converged")),
            "ecutrho_ry": metadata.get("ecutrho_ry"),
            "ecutwfc_ry": metadata.get("ecutwfc_ry"),
            "energy_ev_per_atom": energy,
            "energy_observable": metadata.get("energy_observable"),
            "experiment_id": row["experiment_id"],
            "kmesh": metadata.get("kmesh"),
            "material": row["material"],
            "pressure_gpa": pressure,
            "series_id": row["series_id"],
            "smearing_sigma_ry": metadata.get("smearing_sigma_ry"),
            "solver": metadata.get("solver"),
            "stru_sha256": metadata.get("stru_sha256"),
            "volume_per_atom_angstrom3": volume_per_atom,
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
            if isinstance(point["code_commit"], str):
                input_code_commits.add(point["code_commit"])
            if isinstance(point["abacus_sha256"], str):
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
            provenance_failures = list(runtime_failures.get(key, []))
            refined = grouped.get(key, [])
            expected_ratios = {
                round(float(value), 12) for value in config["volume_ratios"]
            }
            actual_ratios = {point["volume_ratio"] for point in refined if point["converged"]}
            if actual_ratios != expected_ratios:
                provenance_failures.append("incomplete_or_duplicate_seven_point_series")
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
                    provenance_failures.append(
                        f"{point['experiment_id']}:reference_not_in_baseline"
                    )
                    continue
                if point["abacus_sha256"] != base["abacus_sha256"]:
                    provenance_failures.append(
                        f"{point['experiment_id']}:abacus_binary_mismatch"
                    )
                if point["stru_sha256"] != base["stru_sha256"]:
                    provenance_failures.append(f"{point['experiment_id']}:structure_mismatch")

            fit = None
            fit_failures = []
            comparison = {"status": "indeterminate", "failure_reason": "invalid_series"}
            if not provenance_failures:
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
                try:
                    fit, fit_failures = _fit_quality(refined)
                except ValueError as error:
                    fit_failures = [f"bm3_fit_failed:{error}"]
            status = _series_status(provenance_failures, fit_failures, comparison)
            failures = provenance_failures + fit_failures
            if status != "accepted":
                global_failures.append(f"{material}/{series_id}:{status}")
            series_payload[f"{material}/{series_id}"] = {
                "material": material,
                "series_id": series_id,
                "comparison_axis": spec["comparison_axis"],
                "baseline_series_id": spec["baseline_series_id"],
                "status": status,
                "failures": failures,
                "provenance_failures": provenance_failures,
                "fit_failures": fit_failures,
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
            "result_parser_script_sha256": sha256(
                project_root / "scripts" / "parse_s1_single.py"
            ),
            "config_path": str(config_path.relative_to(project_root)),
            "config_sha256": sha256(config_path),
            "manifest_path": str(manifest_path.relative_to(project_root)),
            "manifest_sha256": sha256(manifest_path),
            "core_summary_path": str(core_summary_path.relative_to(project_root)),
            "core_summary_sha256": sha256(core_summary_path),
            "input_abacus_sha256_values": sorted(input_abacus_hashes),
            "input_code_commits": sorted(input_code_commits),
            "preregistration_commit": manifest_validation["preregistration_commit"],
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
