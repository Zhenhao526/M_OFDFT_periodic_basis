#!/usr/bin/env python3
"""Analyze the complete preregistered S1-G1 thermodynamic-label audit R1."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import subprocess
from pathlib import Path

from analyze_s1_eos import fit_bm3
from s1_electron_number_common import expected_electrons, find_single_log
from s1_g1_thermodynamic_label_common import (
    AUDIT_IDS,
    EXECUTION_ORDER,
    json_safe,
    parse_thermodynamic_log,
    read_manifest,
    read_regular_text,
    sha256_regular_file,
)
from validate_s1_g1_thermodynamic_label_audit_r1 import (
    COMMON_QUARTER_IDS,
    CONFIG_PATH,
    EVIDENCE_NAME,
    HALF_IDS,
    MANIFEST_PATH,
    PROTOCOL_REVISION,
    RUN_IDS,
    STANDARD_REPLAY_IDS,
    _find_output,
    evaluate_k_gate,
    field_metrics,
    replay_evidence,
    validate_registration,
)


DEFAULT_OUTPUT = Path("analysis/s1/g1_thermodynamic_label_audit_r1_20260806")
RATIOS = (0.90, 0.94, 0.97, 1.00, 1.03, 1.06, 1.10)
BOHR_TO_ANGSTROM = 0.529177210903
FAILURE_CATEGORIES = (
    "completion",
    "source_integrity",
    "input_hash",
    "scf",
    "thermodynamic_identity",
    "electron_number",
    "eos_fit",
    "adjacent_smearing_energy",
    "equilibrium_volume",
    "replay_equivalence",
    "density",
    "derivative",
    "k_gate",
    "runtime_kmp",
)


def _git_clean(project_root: Path) -> bool:
    return not subprocess.check_output(
        ["git", "-C", str(project_root), "status", "--porcelain"], text=True
    )


def _read_json(path: Path) -> dict:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"not a regular JSON file: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return payload


def _atomic_write(path: Path, data: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(data, encoding="utf-8")
    os.replace(temporary, path)


def _source_ids(material: str) -> tuple[str, ...]:
    first = 85 if material == "al" else 106
    return tuple(f"S1-20260805-{first + index:03d}" for index in range(7))


def _new_ids(material: str, level: str) -> tuple[str, ...]:
    first = {
        ("al", "half"): 7,
        ("mg", "half"): 14,
        ("al", "quarter"): 21,
        ("mg", "quarter"): 28,
    }[(material, level)]
    return tuple(f"S1-20260806-{first + index:03d}" for index in range(7))


def _point_from_run(
    run: Path,
    *,
    experiment_id: str,
    material: str,
    level: str,
    volume_ratio: float,
) -> dict[str, object]:
    log = find_single_log(run)
    metadata_path = run / "input_metadata.json"
    metadata = _read_json(metadata_path)
    atom_count = int(metadata["atom_count"])
    thermo = parse_thermodynamic_log(
        read_regular_text(log), expected_atom_count=atom_count
    )
    labels = thermo["energy_labels_ev_per_atom"]
    if not isinstance(labels, dict):
        raise ValueError(f"{experiment_id}: per-atom label table is missing")
    volume = metadata.get("volume_per_atom_angstrom3")
    if not isinstance(volume, (int, float)) or not math.isfinite(float(volume)):
        # R1 input metadata is intentionally compact.  The immutable STRU is
        # authoritative when the historical convenience field is absent.
        from s1_electron_number_common import parse_stru

        structure = parse_stru(run / "STRU")
        volume = (
            structure.volume_bohr3
            * BOHR_TO_ANGSTROM**3
            / sum(structure.species_counts.values())
        )
    return {
        "experiment_id": experiment_id,
        "material": material,
        "smearing_level": level,
        "volume_ratio": volume_ratio,
        "volume_per_atom_angstrom3": float(volume),
        "e_ec_ev_per_atom": float(labels["E_ec"]),
        "free_energy_ev_per_atom": float(labels["F"]),
        "entropy_minus_ts_ev_per_atom": float(labels["m"]),
        "pressure_gpa": float(thermo["pressure_gpa"]),
        "atom_count": atom_count,
        "raw_log_path": str(log),
        "raw_log_sha256": sha256_regular_file(log),
        "thermodynamic_labels": json_safe(thermo),
    }


def _fit(points: list[dict[str, object]]) -> tuple[dict[str, object], list[str]]:
    ordered = sorted(points, key=lambda point: float(point["volume_per_atom_angstrom3"]))
    failures: list[str] = []
    if tuple(sorted(float(point["volume_ratio"]) for point in ordered)) != RATIOS:
        failures.append("seven_registered_volume_ratios_differ")
    if len({float(point["volume_per_atom_angstrom3"]) for point in ordered}) != 7:
        failures.append("volume_points_are_not_unique")
    if failures:
        return {}, failures
    try:
        result = fit_bm3(
            [float(point["volume_per_atom_angstrom3"]) for point in ordered],
            [float(point["e_ec_ev_per_atom"]) for point in ordered],
        )
    except ValueError as error:
        return {}, [f"bm3_failed:{error}"]
    low = float(ordered[0]["volume_per_atom_angstrom3"])
    high = float(ordered[-1]["volume_per_atom_angstrom3"])
    if not low < float(result["v0_angstrom3_per_atom"]) < high:
        failures.append("fitted_v0_not_strictly_inside_sampled_range")
    if not float(result["b0_gpa"]) > 0.0:
        failures.append("bulk_modulus_not_positive")
    if not float(result["max_abs_residual_mev_per_atom"]) < 1.0:
        failures.append("maximum_fit_residual_not_below_1_mev_per_atom")
    return result, failures


def compare_adjacent(
    coarse: list[dict[str, object]],
    fine: list[dict[str, object]],
    coarse_fit: dict[str, object],
    fine_fit: dict[str, object],
) -> dict[str, object]:
    coarse_by_ratio = {float(point["volume_ratio"]): point for point in coarse}
    fine_by_ratio = {float(point["volume_ratio"]): point for point in fine}
    if set(coarse_by_ratio) != set(RATIOS) or set(fine_by_ratio) != set(RATIOS):
        raise ValueError("adjacent series do not contain the seven frozen ratios")
    coarse_zero = float(coarse_by_ratio[1.0]["e_ec_ev_per_atom"])
    fine_zero = float(fine_by_ratio[1.0]["e_ec_ev_per_atom"])
    rows = []
    for ratio in RATIOS:
        coarse_relative = (
            float(coarse_by_ratio[ratio]["e_ec_ev_per_atom"]) - coarse_zero
        ) * 1000.0
        fine_relative = (
            float(fine_by_ratio[ratio]["e_ec_ev_per_atom"]) - fine_zero
        ) * 1000.0
        rows.append(
            {
                "volume_ratio": ratio,
                "coarse_relative_energy_mev_per_atom": coarse_relative,
                "fine_relative_energy_mev_per_atom": fine_relative,
                "difference_mev_per_atom": fine_relative - coarse_relative,
            }
        )
    max_energy = max(abs(float(row["difference_mev_per_atom"])) for row in rows)
    coarse_v0 = float(coarse_fit["v0_angstrom3_per_atom"])
    fine_v0 = float(fine_fit["v0_angstrom3_per_atom"])
    volume_percent = abs(fine_v0 - coarse_v0) / coarse_v0 * 100.0
    return {
        "rows": rows,
        "max_anchored_energy_difference_mev_per_atom": max_energy,
        "equilibrium_volume_difference_percent": volume_percent,
        "energy_accepted": max_energy < 2.0,
        "volume_accepted": volume_percent < 0.2,
        "accepted": max_energy < 2.0 and volume_percent < 0.2,
    }


def _field_pair(
    project_root: Path, coarse_id: str, reference_id: str
) -> dict[str, object]:
    coarse = project_root / "runs" / coarse_id
    reference = project_root / "runs" / reference_id
    expected, _ = expected_electrons(reference)
    metrics = field_metrics(
        _find_output(coarse, "chg.cube"),
        _find_output(reference, "chg.cube"),
        _find_output(coarse, "pot.cube"),
        _find_output(reference, "pot.cube"),
        structure_path=reference / "STRU",
        expected_electron_count=expected,
    )
    return {
        "coarse_experiment_id": coarse_id,
        "reference_experiment_id": reference_id,
        **metrics,
    }


def _readme(summary: dict[str, object]) -> str:
    failures = summary["failure_ids"]
    assert isinstance(failures, dict)
    lines = [
        "# S1 G1 thermodynamic-label audit R1",
        "",
        f"- Audit status: `{summary['audit_status']}`",
        f"- Accepted new runs: `{summary['accepted_new_run_count']}/40`",
        f"- Main EOS scalar points: `{summary['main_eos_scalar_count']}/42`",
        f"- G1 status after this audit: `{summary['g1_status']}`",
        "",
        "The six EOS curves use the entropy-corrected finite-smearing estimator "
        "E_ec. Density and projected-potential labels remain a finite-temperature "
        "Mermin bundle; this report makes no exact-zero-temperature field claim.",
        "",
        "## Failure ledger",
        "",
    ]
    for category in FAILURE_CATEGORIES:
        values = failures[category]
        lines.append(f"- `{category}`: {', '.join(values) if values else 'none'}")
    return "\n".join(lines) + "\n"


def classify_run_failures(messages: list[str]) -> set[str]:
    """Map validator evidence to every applicable protocol ledger category."""

    text = "\n".join(messages).lower()
    categories: set[str] = set()
    mappings = {
        "input_hash": ("input", "sha-256", "pseudopotential", "metadata", "manifest"),
        "scf": ("converg", "raw log", "running_scf"),
        "thermodynamic_identity": ("thermodynamic", "label", "identity", "entropy"),
        "electron_number": ("electron", "density electron integral"),
        "runtime_kmp": (
            "runtime", "kmp", "namespace", "old prefix", "old-prefix",
            "mapping", "strace", "audit",
        ),
        "replay_equivalence": ("standard replay equivalence", "scientific equivalence"),
        "source_integrity": ("source", "reference"),
    }
    for category, markers in mappings.items():
        if any(marker in text for marker in markers):
            categories.add(category)
    return categories


def has_capability_or_evidence_failure(messages: list[str]) -> bool:
    """Distinguish unavailable/corrupt evidence from complete gate rejection."""

    text = "\n".join(messages).lower()
    markers = (
        "missing", "symbolic", "not a regular", "unavailable", "cannot ",
        "malformed", "invalid json", "raw-log reparse failed", "sha-256 mismatch",
        "differs from head", "not tracked", "input comparison failed",
        "expected exactly one", "ambiguous", "grid differs", "geometry differs",
        "value count differs", "non-finite", "zero projected", "zero density",
        "basename is not", "invalid cube", "does not reconstruct", "cube/stru",
        "atom identity/position differs", "disagree with stru",
    )
    return any(marker in text for marker in markers)


def analyze(
    project_root: Path,
    config_path: Path,
    manifest_path: Path,
    output_directory: Path,
    *,
    require_committed: bool = True,
) -> dict[str, object]:
    project_root = project_root.resolve()
    if output_directory.exists() or output_directory.is_symlink():
        raise ValueError(f"refusing to overwrite analysis output: {output_directory}")
    if require_committed and not _git_clean(project_root):
        raise ValueError("refusing final analysis from a dirty worktree")
    config, rows, registration = validate_registration(
        project_root,
        config_path.resolve(),
        manifest_path.resolve(),
        require_committed=require_committed,
    )
    if tuple(row["experiment_id"] for row in rows) != AUDIT_IDS:
        raise ValueError("manifest denominator differs from common schema")
    if tuple(config["execution_order"]) != EXECUTION_ORDER:
        raise ValueError("execution order differs from preregistration")

    failures: dict[str, list[str]] = {name: [] for name in FAILURE_CATEGORIES}
    capability_failures: list[str] = []
    by_id = {row["experiment_id"]: row for row in rows}
    evidence_by_id: dict[str, dict[str, object]] = {}
    for experiment_id in RUN_IDS:
        payload, run_failures = replay_evidence(
            project_root,
            config,
            by_id[experiment_id],
            require_committed=require_committed,
            require_replay_status=True,
        )
        if run_failures:
            failures["completion"].append(experiment_id)
            classified = classify_run_failures(run_failures)
            for category in classified:
                failures[category].append(experiment_id)
            numerical_only = (
                not has_capability_or_evidence_failure(run_failures)
                and bool(classified)
                and classified.issubset(
                    {
                        "thermodynamic_identity",
                        "electron_number",
                        "replay_equivalence",
                    }
                )
            )
            if not numerical_only:
                capability_failures.append(experiment_id)
            continue
        evidence_by_id[experiment_id] = payload
        if payload.get("electron_number_integration", {}).get("accepted") is not True:
            failures["electron_number"].append(experiment_id)
        if payload.get("kmp_runtime_contract", {}).get("accepted") is not True:
            failures["runtime_kmp"].append(experiment_id)
        if experiment_id in STANDARD_REPLAY_IDS:
            if payload.get("standard_replay_equivalence", {}).get("accepted") is not True:
                failures["replay_equivalence"].append(experiment_id)

    series: dict[str, dict[str, object]] = {}
    all_points: list[dict[str, object]] = []
    for material in ("al", "mg"):
        specs = (
            ("standard", _source_ids(material)),
            ("half", _new_ids(material, "half")),
            ("quarter", _new_ids(material, "quarter")),
        )
        for level, ids in specs:
            points: list[dict[str, object]] = []
            for ratio, experiment_id in zip(RATIOS, ids):
                try:
                    points.append(
                        _point_from_run(
                            project_root / "runs" / experiment_id,
                            experiment_id=experiment_id,
                            material=material,
                            level=level,
                            volume_ratio=ratio,
                        )
                    )
                except (FileNotFoundError, KeyError, TypeError, ValueError) as error:
                    category = "source_integrity" if level == "standard" else "scf"
                    failures[category].append(experiment_id)
                    capability_failures.append(experiment_id)
                    points.append({"experiment_id": experiment_id, "error": str(error)})
            valid_points = [point for point in points if "error" not in point]
            fit, fit_failures = _fit(valid_points) if len(valid_points) == 7 else ({}, ["incomplete"])
            key = f"{material}/{level}"
            if fit_failures:
                failures["eos_fit"].append(key)
            series[key] = {
                "status": "accepted" if not fit_failures else "rejected",
                "points": points,
                "fit": fit,
                "fit_failures": fit_failures,
            }
            all_points.extend(valid_points)

    comparisons: dict[str, dict[str, object]] = {}
    for material in ("al", "mg"):
        for coarse_level, fine_level in (("standard", "half"), ("half", "quarter")):
            key = f"{material}/{coarse_level}_to_{fine_level}"
            coarse_payload = series[f"{material}/{coarse_level}"]
            fine_payload = series[f"{material}/{fine_level}"]
            if coarse_payload["status"] != "accepted" or fine_payload["status"] != "accepted":
                comparisons[key] = {"accepted": False, "reason": "invalid_input_fit"}
                failures["adjacent_smearing_energy"].append(key)
                failures["equilibrium_volume"].append(key)
                continue
            comparison = compare_adjacent(
                coarse_payload["points"],  # type: ignore[arg-type]
                fine_payload["points"],  # type: ignore[arg-type]
                coarse_payload["fit"],  # type: ignore[arg-type]
                fine_payload["fit"],  # type: ignore[arg-type]
            )
            comparisons[key] = comparison
            if comparison["energy_accepted"] is not True:
                failures["adjacent_smearing_energy"].append(key)
            if comparison["volume_accepted"] is not True:
                failures["equilibrium_volume"].append(key)

    smearing_diagnostics: dict[str, list[dict[str, object]]] = {}
    for material in ("al", "mg"):
        for coarse_level, fine_level in (("standard", "half"), ("half", "quarter")):
            key = f"{material}/{coarse_level}_to_{fine_level}"
            coarse_points = {
                float(point["volume_ratio"]): point
                for point in series[f"{material}/{coarse_level}"]["points"]  # type: ignore[index]
                if "error" not in point
            }
            fine_points = {
                float(point["volume_ratio"]): point
                for point in series[f"{material}/{fine_level}"]["points"]  # type: ignore[index]
                if "error" not in point
            }
            rows_diagnostic: list[dict[str, object]] = []
            for ratio in sorted(set(coarse_points) & set(fine_points)):
                coarse_m = abs(float(coarse_points[ratio]["entropy_minus_ts_ev_per_atom"]))
                fine_m = abs(float(fine_points[ratio]["entropy_minus_ts_ev_per_atom"]))
                rows_diagnostic.append(
                    {
                        "volume_ratio": ratio,
                        "coarse_abs_m_ev_per_atom": coarse_m,
                        "fine_abs_m_ev_per_atom": fine_m,
                        "fine_to_coarse_abs_m_ratio": (
                            fine_m / coarse_m if coarse_m > 0.0 else None
                        ),
                        "coarse_pressure_gpa": float(coarse_points[ratio]["pressure_gpa"]),
                        "fine_pressure_gpa": float(fine_points[ratio]["pressure_gpa"]),
                        "pressure_difference_gpa": float(fine_points[ratio]["pressure_gpa"])
                        - float(coarse_points[ratio]["pressure_gpa"]),
                        "acceptance_role": "mandatory_diagnostic_only",
                    }
                )
            smearing_diagnostics[key] = rows_diagnostic

    label_metrics: list[dict[str, object]] = []
    for half_number in range(7, 21):
        half_id = f"S1-20260806-{half_number:03d}"
        quarter_id = f"S1-20260806-{half_number + 14:03d}"
        try:
            metric = _field_pair(project_root, half_id, quarter_id)
            metric["comparison_kind"] = "half_to_quarter_hard_gate"
            label_metrics.append(metric)
            if not float(metric["d1"]) < 0.005 or not float(metric["d2"]) < 0.005:
                failures["density"].append(f"{half_id}:{quarter_id}")
            if not float(metric["dg"]) < 0.01 or not float(metric["rms_g_ev"]) < 0.005:
                failures["derivative"].append(f"{half_id}:{quarter_id}")
        except (FileNotFoundError, KeyError, TypeError, ValueError) as error:
            pair = f"{half_id}:{quarter_id}"
            failures["density"].append(pair)
            failures["derivative"].append(pair)
            capability_failures.append(pair)
            label_metrics.append({"comparison_kind": "half_to_quarter_hard_gate", "pair": pair, "error": str(error)})

    standard_half_pairs = (
        (1, 7), (2, 10), (3, 13), (4, 14), (5, 17), (6, 20)
    )
    for standard_number, half_number in standard_half_pairs:
        standard_id = f"S1-20260806-{standard_number:03d}"
        half_id = f"S1-20260806-{half_number:03d}"
        try:
            metric = _field_pair(project_root, standard_id, half_id)
            metric["comparison_kind"] = "standard_to_half_diagnostic_only"
            label_metrics.append(metric)
        except (FileNotFoundError, KeyError, TypeError, ValueError) as error:
            pair = f"{standard_id}:{half_id}"
            failures["density"].append(pair)
            failures["derivative"].append(pair)
            capability_failures.append(pair)
            label_metrics.append(
                {
                    "comparison_kind": "standard_to_half_diagnostic_only",
                    "pair": pair,
                    "error": str(error),
                }
            )

    try:
        k_gate = evaluate_k_gate(project_root, rows, require_committed=require_committed)
        if k_gate["accepted"] is not True:
            failures["k_gate"].extend(["al", "mg"])
        for pair in k_gate["pairs"]:  # type: ignore[index]
            fields = pair["field_metrics"]
            label_metrics.append(
                {
                    "comparison_kind": "common_to_extra_k_hard_gate",
                    "coarse_experiment_id": pair["common_experiment_id"],
                    "reference_experiment_id": pair["extra_experiment_id"],
                    **fields,
                }
            )
    except (FileNotFoundError, KeyError, TypeError, ValueError) as error:
        k_gate = {"accepted": False, "error": str(error)}
        failures["k_gate"].extend(["al", "mg"])
        capability_failures.append("k_gate")

    lifecycle_count = sum(
        int(payload["kmp_runtime_contract"]["lifecycle_count"])
        for payload in evidence_by_id.values()
        if isinstance(payload.get("kmp_runtime_contract"), dict)
    )
    syscall_count = sum(
        int(payload["kmp_runtime_contract"]["successful_syscall_count"])
        for payload in evidence_by_id.values()
        if isinstance(payload.get("kmp_runtime_contract"), dict)
    )
    if not (
        len(evidence_by_id) == 40
        and lifecycle_count == 160
        and syscall_count == 480
    ):
        failures["runtime_kmp"].append("aggregate")

    for category in failures:
        failures[category] = sorted(set(failures[category]))
    exact_counts = {
        "accepted_new_runs": len(evidence_by_id),
        "main_eos_scalar_points": len(all_points),
        "half_quarter_field_pairs": sum(
            row.get("comparison_kind") == "half_to_quarter_hard_gate" and "error" not in row
            for row in label_metrics
        ),
        "standard_half_diagnostic_pairs": sum(
            row.get("comparison_kind") == "standard_to_half_diagnostic_only"
            and "error" not in row
            for row in label_metrics
        ),
        "k_anchor_pairs": len(k_gate.get("pairs", [])) if isinstance(k_gate, dict) else 0,
        "kmp_rank_lifecycles": lifecycle_count,
        "kmp_successful_syscalls": syscall_count,
    }
    if exact_counts["main_eos_scalar_points"] != 42:
        failures["completion"].append("main_eos_42_point_denominator")
    hard_accepted = (
        all(not values for values in failures.values())
        and exact_counts
        == {
            "accepted_new_runs": 40,
            "main_eos_scalar_points": 42,
            "half_quarter_field_pairs": 14,
            "standard_half_diagnostic_pairs": 6,
            "k_anchor_pairs": 6,
            "kmp_rank_lifecycles": 160,
            "kmp_successful_syscalls": 480,
        }
        and len(series) == 6
        and all(payload["status"] == "accepted" for payload in series.values())
        and len(comparisons) == 4
        and all(payload["accepted"] is True for payload in comparisons.values())
        and k_gate.get("accepted") is True
    )
    status = (
        "accepted"
        if hard_accepted
        else (
            "indeterminate_paused"
            if capability_failures
            else "rejected"
        )
    )
    summary: dict[str, object] = {
        "schema_version": 1,
        "protocol_revision": PROTOCOL_REVISION,
        "audit_status": status,
        "accepted_new_run_count": len(evidence_by_id),
        "main_eos_scalar_count": len(all_points),
        "series": series,
        "adjacent_smearing_comparisons": comparisons,
        "smearing_entropy_pressure_diagnostics": smearing_diagnostics,
        "k_gate": k_gate,
        "runtime_kmp_aggregate": {
            "accepted_run_count": len(evidence_by_id),
            "rank_lifecycle_count": lifecycle_count,
            "successful_syscall_count": syscall_count,
            "accepted": lifecycle_count == 160 and syscall_count == 480 and len(evidence_by_id) == 40,
        },
        "exact_counts": exact_counts,
        "failure_ids": failures,
        "capability_failure_ids": sorted(set(capability_failures)),
        "g1_status": "pending (2/6)" if hard_accepted else "pending (1/6)",
        "authorized_scope": (
            "close_only_third_smearing_dense_k_thermodynamic_label_item"
            if hard_accepted else "no_G1_advancement"
        ),
        "zero_temperature_exact_claim": False,
        "registration": registration,
        "config_sha256": sha256_regular_file(config_path),
        "manifest_sha256": sha256_regular_file(manifest_path),
    }

    output_directory.mkdir(parents=True, exist_ok=False)
    _atomic_write(
        output_directory / "summary.json",
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
    )
    with (output_directory / "points.tsv").open("x", encoding="utf-8", newline="") as handle:
        fieldnames = (
            "material", "smearing_level", "volume_ratio",
            "volume_per_atom_angstrom3", "experiment_id", "e_ec_ev_per_atom",
            "free_energy_ev_per_atom", "entropy_minus_ts_ev_per_atom", "pressure_gpa",
        )
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sorted(all_points, key=lambda point: (str(point["material"]), str(point["smearing_level"]), float(point["volume_ratio"]))))
    metric_fields = (
        "comparison_kind", "coarse_experiment_id", "reference_experiment_id",
        "d1", "d2", "dg", "rms_g_ev", "accepted", "error",
    )
    with (output_directory / "label_metrics.tsv").open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=metric_fields, delimiter="\t", lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(label_metrics)
    _atomic_write(output_directory / "README.md", _readme(summary))
    return summary


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_directory", nargs="?", type=Path, default=project_root / DEFAULT_OUTPUT)
    parser.add_argument("--config", type=Path, default=project_root / CONFIG_PATH)
    parser.add_argument("--manifest", type=Path, default=project_root / MANIFEST_PATH)
    parser.add_argument("--allow-uncommitted", action="store_true")
    arguments = parser.parse_args()
    summary = analyze(
        project_root,
        arguments.config.resolve(),
        arguments.manifest.resolve(),
        arguments.output_directory.resolve(),
        require_committed=not arguments.allow_uncommitted,
    )
    print(json.dumps({"audit_status": summary["audit_status"], "output_directory": str(arguments.output_directory.resolve())}, indent=2, sort_keys=True))
    return 0 if summary["audit_status"] == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
