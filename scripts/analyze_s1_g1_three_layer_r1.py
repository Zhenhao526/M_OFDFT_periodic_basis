#!/usr/bin/env python3
"""Evaluate the P0 barrier and build deterministic three-layer EOS evidence."""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import shutil
import subprocess
from pathlib import Path

from analyze_s1_eos import fit_bm3
from s1_electron_number_common import parse_stru
from s1_g1_thermodynamic_label_common import parse_abacus_cube
from s1_g1_three_layer_common import (
    atomic_write,
    canonical_json_bytes,
    find_project_root,
    git,
    load_config,
    load_manifest,
    read_json,
    require,
    sha256_file,
)


BOHR_TO_ANGSTROM = 1.0 / 1.8897261254578281
VOLUME_RATIOS = (0.90, 0.94, 0.97, 1.00, 1.03, 1.06, 1.10)
POINT_FIELDS = (
    "material",
    "series",
    "experiment_id",
    "volume_ratio",
    "volume_angstrom3_per_atom",
    "energy_ev_per_atom",
    "anchored_energy_mev_per_atom",
    "pressure_gpa",
    "scientific_role",
    "source_result_sha256",
    "source_stru_sha256",
)


def _result_path(run_dir: Path) -> Path:
    return run_dir / "result.json"


def verify_new_result(run_dir: Path, expected_id: str, config: dict) -> dict:
    result = read_json(_result_path(run_dir))
    require(isinstance(result, dict), "result must be object")
    require(result.get("status") == "accepted", f"new result not accepted: {expected_id}")
    require(result.get("experiment_id") == expected_id, "new result ID differs")
    require(result.get("protocol_revision") == config["protocol_revision"], "new result protocol differs")
    material = result["material"]
    expected_pseudo = config["pseudodojo"]["materials"][material]
    pseudo = result["pseudo_identity"]
    require(pseudo["sha256"] == expected_pseudo["sha256"], "result PP SHA differs")
    require(pseudo["number_of_proj"] == expected_pseudo["number_of_proj_per_atom"], "result nproj differs")
    require(result["semantic_limits"]["local_only_T_sU_identity_applied"] is False, "local-only identity applied")
    require(result["semantic_limits"]["zero_temperature_exact_claim"] is False, "exact 0 K claim present")
    for identity in result["evidence_files"]:
        path = run_dir / identity["path"]
        require(path.is_file() and not path.is_symlink(), f"missing evidence: {path}")
        require(path.stat().st_size == identity["size_bytes"], f"evidence size differs: {path}")
        require(sha256_file(path) == identity["sha256"], f"evidence SHA differs: {path}")
    runner_return = read_json(run_dir / "runner_return.json")
    require(isinstance(runner_return, dict) and runner_return.get("return_code") == 0, "runner return differs")
    metadata = read_json(run_dir / "metadata.json")
    require(isinstance(metadata, dict) and metadata.get("experiment_id") == expected_id, "metadata differs")
    density_path = run_dir / f"OUT.{metadata['suffix']}" / "chg.cube"
    cube = parse_abacus_cube(
        density_path,
        quantity="electron_density",
        units="electron_per_bohr3",
        structure_path=run_dir / "STRU",
    )
    integrated = cube.voxel_volume_bohr3 * math.fsum(cube.values)
    expected_electrons = float(result["expected_electrons"])
    relative_error = abs(integrated - expected_electrons) / expected_electrons
    require(
        relative_error < float(config["acceptance"]["electron_relative_error_strictly_less_than"]),
        f"replayed electron gate failed: {expected_id}",
    )
    require(abs(relative_error - float(result["electron_number"]["relative_error"])) < 1e-15, "electron replay differs")
    return result


def load_new_results(source_root: Path, ids: list[str], config: dict) -> dict[str, dict]:
    return {experiment_id: verify_new_result(source_root / experiment_id, experiment_id, config) for experiment_id in ids}


def p0_metrics(results: dict[str, dict], config: dict) -> dict:
    by_material: dict[str, dict] = {}
    mapping = {
        "al": ("S1-20260810-301", "S1-20260810-302", "S1-20260810-303"),
        "mg": ("S1-20260810-304", "S1-20260810-305", "S1-20260810-306"),
    }
    energy_cutoff_limit = float(config["acceptance"]["p0_cutoff_energy_difference_mev_per_atom_strictly_less_than"])
    pressure_limit = float(config["acceptance"]["p0_cutoff_pressure_difference_gpa_strictly_less_than"])
    k_limit = float(config["acceptance"]["p0_k_energy_difference_mev_per_atom_strictly_less_than"])
    for material, (common_id, extra_id, high_id) in mapping.items():
        common = results[common_id]
        extra = results[extra_id]
        high = results[high_id]
        cutoff_energy = abs(high["thermodynamic_labels_ev_per_atom"]["E_ec"] - extra["thermodynamic_labels_ev_per_atom"]["E_ec"]) * 1000.0
        cutoff_pressure = abs(high["pressure_gpa"] - extra["pressure_gpa"])
        k_energy = abs(extra["thermodynamic_labels_ev_per_atom"]["E_ec"] - common["thermodynamic_labels_ev_per_atom"]["E_ec"]) * 1000.0
        cutoff_energy_passed = cutoff_energy < energy_cutoff_limit
        cutoff_pressure_passed = cutoff_pressure < pressure_limit
        k_energy_passed = k_energy < k_limit
        by_material[material] = {
            "normal_common_id": common_id,
            "normal_extra_k_id": extra_id,
            "high_extra_k_id": high_id,
            "cutoff_abs_E_ec_difference_mev_per_atom": cutoff_energy,
            "cutoff_abs_pressure_difference_gpa": cutoff_pressure,
            "normal_k_abs_E_ec_difference_mev_per_atom": k_energy,
            "cutoff_energy_passed": cutoff_energy_passed,
            "cutoff_pressure_passed": cutoff_pressure_passed,
            "k_energy_passed": k_energy_passed,
            "status": "accepted" if cutoff_energy_passed and cutoff_pressure_passed and k_energy_passed else "rejected",
        }
    accepted = all(row["status"] == "accepted" for row in by_material.values())
    return {
        "status": "accepted" if accepted else "rejected",
        "run_count": len(results),
        "materials": by_material,
        "thresholds": {
            "cutoff_abs_E_ec_difference_mev_per_atom_strictly_less_than": energy_cutoff_limit,
            "cutoff_abs_pressure_difference_gpa_strictly_less_than": pressure_limit,
            "normal_k_abs_E_ec_difference_mev_per_atom_strictly_less_than": k_limit,
        },
    }


def write_p0_barrier(project_root: Path, config: dict) -> dict:
    state_root = Path(config["external_state_root"])
    phase = read_json(state_root / "phases" / "p0.json")
    require(isinstance(phase, dict) and phase.get("status") == "accepted", "P0 runner phase incomplete")
    ids = list(config["execution_phases"]["p0"])
    results = load_new_results(state_root / "runs", ids, config)
    metrics = p0_metrics(results, config)
    session = read_json(state_root / "session.json")
    payload = {
        "schema_version": 1,
        "protocol_revision": config["protocol_revision"],
        "status": metrics["status"],
        "runner_commit": session["runner_commit"],
        "accepted_run_ids": ids,
        "metrics": metrics,
        "retry_policy_if_rejected": "stop_R1_no_same_ID_retry",
    }
    path = state_root / "barriers" / "p0_gate.json"
    atomic_write(path, canonical_json_bytes(payload), exclusive=True)
    return payload


def copy_file_exact(source: Path, destination: Path) -> None:
    require(source.is_file() and not source.is_symlink(), f"not a regular source: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    require(not destination.exists(), f"evidence destination exists: {destination}")
    shutil.copyfile(source, destination)
    require(sha256_file(source) == sha256_file(destination), f"copy SHA differs: {source}")


def collect_evidence(project_root: Path, config: dict, rows: list[dict[str, str]]) -> Path:
    state_root = Path(config["external_state_root"])
    analysis_root = project_root / config["analysis_root"]
    require(not analysis_root.exists(), f"analysis root already exists: {analysis_root}")
    raw_root = analysis_root / "raw"
    required_ids = list(config["execution_phases"]["p0"] + config["execution_phases"]["al_eos"] + config["execution_phases"]["mg_required"])
    optional_ids = [
        experiment_id
        for experiment_id in config["execution_phases"]["mg_optional"]
        if (state_root / "accepted" / f"{experiment_id}.json").is_file()
    ]
    ids = required_ids + optional_ids
    row_by_id = {row["experiment_id"]: row for row in rows}
    for experiment_id in ids:
        source = state_root / "runs" / experiment_id
        result = read_json(source / "result.json")
        require(isinstance(result, dict) and result.get("status") == "accepted", "cannot collect unaccepted run")
        destination = raw_root / experiment_id
        metadata = read_json(source / "metadata.json")
        allow = [
            "INPUT",
            "STRU",
            "KPT",
            "input_metadata.json",
            "metadata.json",
            "pseudo_identity.json",
            "run.stdout",
            "run.stderr",
            "runner_return.json",
            "result.json",
        ]
        for name in allow:
            copy_file_exact(source / name, destination / name)
        for rank in range(int(config["runtime"]["rank_count"])):
            name = f"rank_{rank:03d}.json"
            copy_file_exact(source / "affinity" / name, destination / "affinity" / name)
        suffix = metadata["suffix"]
        for name in ("running_scf.log", "chg.cube"):
            copy_file_exact(source / f"OUT.{suffix}" / name, destination / f"OUT.{suffix}" / name)
        copy_file_exact(state_root / "attempts" / f"{experiment_id}.json", destination / "attempt.json")
        copy_file_exact(state_root / "accepted" / f"{experiment_id}.json", destination / "accepted.json")
        require(row_by_id[experiment_id]["pseudo_basename"] not in {path.name for path in destination.rglob("*")}, "PP bytes copied")
    orchestration = analysis_root / "orchestration"
    for name in ("session.json",):
        copy_file_exact(state_root / name, orchestration / name)
    for phase in ("p0", "al_eos", "mg_required"):
        copy_file_exact(state_root / "phases" / f"{phase}.json", orchestration / "phases" / f"{phase}.json")
    optional_phase = state_root / "phases" / "mg_optional.json"
    if optional_ids:
        require(len(optional_ids) == 4 and optional_phase.is_file(), "partial optional phase cannot be collected")
        copy_file_exact(optional_phase, orchestration / "phases" / "mg_optional.json")
    copy_file_exact(state_root / "barriers" / "p0_gate.json", orchestration / "barriers" / "p0_gate.json")
    return analysis_root


def git_blob_identity(project_root: Path, path: Path) -> dict[str, object]:
    relative = path.relative_to(project_root).as_posix()
    blob = git(project_root, "rev-parse", f"HEAD:{relative}")
    mode_row = git(project_root, "ls-files", "-s", "--", relative).split()
    require(len(mode_row) >= 3 and mode_row[1] == blob, f"git identity differs: {relative}")
    return {"path": relative, "sha256": sha256_file(path), "git_blob_oid": blob, "git_mode": mode_row[0]}


def new_volume(run_dir: Path, atom_count: int) -> float:
    structure = parse_stru(run_dir / "STRU")
    return structure.volume_bohr3 * BOHR_TO_ANGSTROM**3 / atom_count


def reference_point(project_root: Path, experiment_id: str, ratio: float, material: str, series: str) -> tuple[dict, list[dict]]:
    run_dir = project_root / "runs" / experiment_id
    result_path = run_dir / "result.json"
    stru_path = run_dir / "STRU"
    result = read_json(result_path)
    require(isinstance(result, dict) and result.get("converged") is True, f"reference not converged: {experiment_id}")
    atom_count = int(result["atom_count"])
    if series == "ks_l":
        energy = result.get("zero_temp_extrapolated_energy_ev_per_atom")
        require(energy is not None, f"KS-L lacks E_ec: {experiment_id}")
        role = "legacy_local_PP_finite_smearing_E_ec"
    else:
        energy = result["energy_ev_per_atom"]
        role = "legacy_local_PP_OFDFT_total_energy"
    structure = parse_stru(stru_path)
    volume = structure.volume_bohr3 * BOHR_TO_ANGSTROM**3 / atom_count
    point = {
        "material": material,
        "series": series,
        "experiment_id": experiment_id,
        "volume_ratio": ratio,
        "volume_angstrom3_per_atom": volume,
        "energy_ev_per_atom": float(energy),
        "pressure_gpa": float(result["pressure_gpa"]),
        "scientific_role": role,
        "source_result_sha256": sha256_file(result_path),
        "source_stru_sha256": sha256_file(stru_path),
    }
    return point, [git_blob_identity(project_root, result_path), git_blob_identity(project_root, stru_path)]


def anchor(points: list[dict]) -> list[dict]:
    by_ratio = {round(point["volume_ratio"], 8): point for point in points}
    require(len(by_ratio) == len(points) and 1.0 in by_ratio, "series missing unique v100 anchor")
    reference = by_ratio[1.0]["energy_ev_per_atom"]
    for point in points:
        point["anchored_energy_mev_per_atom"] = (point["energy_ev_per_atom"] - reference) * 1000.0
    return sorted(points, key=lambda item: item["volume_ratio"])


def fit_series(points: list[dict], residual_limit: float) -> dict:
    require(len(points) == 7, "BM3 requires seven registered EOS points")
    fit = fit_bm3(
        [point["volume_angstrom3_per_atom"] for point in points],
        [point["energy_ev_per_atom"] for point in points],
    )
    sampled = [point["volume_angstrom3_per_atom"] for point in points]
    inside = min(sampled) < fit["v0_angstrom3_per_atom"] < max(sampled)
    residual_passed = fit["max_abs_residual_mev_per_atom"] < residual_limit
    return {
        **fit,
        "equilibrium_volume_inside_sampled_interval": inside,
        "residual_passed": residual_passed,
        "status": "accepted" if inside and residual_passed else "rejected",
    }


def compare_series(reference: list[dict], comparison: list[dict], reference_fit: dict, comparison_fit: dict) -> dict:
    ref_by_ratio = {point["volume_ratio"]: point for point in reference}
    cmp_by_ratio = {point["volume_ratio"]: point for point in comparison}
    require(set(ref_by_ratio) == set(cmp_by_ratio) == set(VOLUME_RATIOS), "EOS ratios differ")
    rows = []
    absolute_offsets = []
    for ratio in VOLUME_RATIOS:
        ref = ref_by_ratio[ratio]
        cmp = cmp_by_ratio[ratio]
        anchored_difference = cmp["anchored_energy_mev_per_atom"] - ref["anchored_energy_mev_per_atom"]
        absolute_offset = cmp["energy_ev_per_atom"] - ref["energy_ev_per_atom"]
        absolute_offsets.append(absolute_offset)
        rows.append({
            "volume_ratio": ratio,
            "reference_anchored_energy_mev_per_atom": ref["anchored_energy_mev_per_atom"],
            "comparison_anchored_energy_mev_per_atom": cmp["anchored_energy_mev_per_atom"],
            "anchored_difference_mev_per_atom": anchored_difference,
            "raw_absolute_energy_offset_ev_per_atom": absolute_offset,
        })
    mean_offset = sum(absolute_offsets) / len(absolute_offsets)
    centered_rms = math.sqrt(sum((value - mean_offset) ** 2 for value in absolute_offsets) / len(absolute_offsets)) * 1000.0
    dv = abs(comparison_fit["v0_angstrom3_per_atom"] - reference_fit["v0_angstrom3_per_atom"]) / reference_fit["v0_angstrom3_per_atom"] * 100.0
    db = abs(comparison_fit["b0_gpa"] - reference_fit["b0_gpa"]) / reference_fit["b0_gpa"] * 100.0
    return {
        "equilibrium_volume_difference_percent": dv,
        "bulk_modulus_difference_percent": db,
        "anchored_curve_max_abs_difference_mev_per_atom": max(abs(row["anchored_difference_mev_per_atom"]) for row in rows),
        "anchored_curve_rms_difference_mev_per_atom": math.sqrt(sum(row["anchored_difference_mev_per_atom"] ** 2 for row in rows) / len(rows)),
        "raw_absolute_energy_offset_mean_ev_per_atom": mean_offset,
        "raw_absolute_energy_offset_centered_rms_mev_per_atom": centered_rms,
        "absolute_energy_gate_applied": False,
        "rows": rows,
    }


def build_final_analysis(project_root: Path, config: dict, rows: list[dict[str, str]], source_root: Path) -> tuple[dict, list[dict], list[dict]]:
    required_ids = list(config["execution_phases"]["p0"] + config["execution_phases"]["al_eos"] + config["execution_phases"]["mg_required"])
    optional_ids = [experiment_id for experiment_id in config["execution_phases"]["mg_optional"] if (source_root / experiment_id / "result.json").is_file()]
    require(len(optional_ids) in {0, 4}, "partial Mg optional evidence")
    new_results = load_new_results(source_root, required_ids + optional_ids, config)
    p0 = p0_metrics({experiment_id: new_results[experiment_id] for experiment_id in config["execution_phases"]["p0"]}, config)
    require(p0["status"] == "accepted", "final analysis requires accepted P0")
    barrier = read_json((source_root.parent / "orchestration" / "barriers" / "p0_gate.json")) if source_root.name == "raw" else read_json(Path(config["external_state_root"]) / "barriers" / "p0_gate.json")
    require(isinstance(barrier, dict) and barrier.get("status") == "accepted", "collected P0 barrier differs")
    point_rows: list[dict] = []
    source_identities: list[dict] = []
    row_by_id = {row["experiment_id"]: row for row in rows}
    al_new_ids = ["S1-20260810-307", "S1-20260810-309", "S1-20260810-310", "S1-20260810-301", "S1-20260810-311", "S1-20260810-312", "S1-20260810-308"]
    al_ksnl = []
    for ratio, experiment_id in zip(VOLUME_RATIOS, al_new_ids):
        result = new_results[experiment_id]
        require(abs(result["volume_ratio"] - ratio) < 1e-12, "Al KS-NL ratio differs")
        al_ksnl.append({
            "material": "al",
            "series": "ks_nl",
            "experiment_id": experiment_id,
            "volume_ratio": ratio,
            "volume_angstrom3_per_atom": new_volume(source_root / experiment_id, int(result["atom_count"])),
            "energy_ev_per_atom": result["thermodynamic_labels_ev_per_atom"]["E_ec"],
            "pressure_gpa": result["pressure_gpa"],
            "scientific_role": "R1_ABACUS_PBE3e_PseudoDojo_KS_NL_hard_EOS",
            "source_result_sha256": sha256_file(source_root / experiment_id / "result.json"),
            "source_stru_sha256": sha256_file(source_root / experiment_id / "STRU"),
        })
    al_ksl: list[dict] = []
    al_ofl: list[dict] = []
    for ratio, experiment_id in zip(VOLUME_RATIOS, config["references"]["al"]["ks_l"]):
        point, identities = reference_point(project_root, experiment_id, ratio, "al", "ks_l")
        al_ksl.append(point)
        source_identities.extend(identities)
    for ratio, experiment_id in zip(VOLUME_RATIOS, config["references"]["al"]["of_l"]):
        point, identities = reference_point(project_root, experiment_id, ratio, "al", "of_l")
        al_ofl.append(point)
        source_identities.extend(identities)
    al_ksnl, al_ksl, al_ofl = anchor(al_ksnl), anchor(al_ksl), anchor(al_ofl)
    point_rows.extend(al_ksnl + al_ksl + al_ofl)
    residual_limit = float(config["acceptance"]["bm3_max_abs_residual_mev_per_atom_strictly_less_than"])
    fits = {
        "ks_nl": fit_series(al_ksnl, residual_limit),
        "ks_l": fit_series(al_ksl, residual_limit),
        "of_l": fit_series(al_ofl, residual_limit),
    }
    ksl_vs_ksnl = compare_series(al_ksnl, al_ksl, fits["ks_nl"], fits["ks_l"])
    ksl_v_limit = float(config["acceptance"]["al_ksl_vs_ksnl_equilibrium_volume_difference_percent_max"])
    ksl_b_limit = float(config["acceptance"]["al_ksl_vs_ksnl_bulk_modulus_difference_percent_max"])
    ksl_vs_ksnl["thresholds"] = {"equilibrium_volume_difference_percent_max": ksl_v_limit, "bulk_modulus_difference_percent_max": ksl_b_limit}
    ksl_vs_ksnl["status"] = "accepted" if ksl_vs_ksnl["equilibrium_volume_difference_percent"] <= ksl_v_limit and ksl_vs_ksnl["bulk_modulus_difference_percent"] <= ksl_b_limit and fits["ks_nl"]["status"] == fits["ks_l"]["status"] == "accepted" else "rejected"
    ofl_vs_ksl = compare_series(al_ksl, al_ofl, fits["ks_l"], fits["of_l"])
    of_v_limit = float(config["acceptance"]["ofdft_vs_ksl_equilibrium_volume_difference_percent_max"])
    of_b_limit = float(config["acceptance"]["ofdft_vs_ksl_bulk_modulus_difference_percent_max"])
    of_passed = ofl_vs_ksl["equilibrium_volume_difference_percent"] <= of_v_limit and ofl_vs_ksl["bulk_modulus_difference_percent"] <= of_b_limit and fits["of_l"]["status"] == "accepted"
    ofl_vs_ksl["thresholds"] = {"equilibrium_volume_difference_percent_max": of_v_limit, "bulk_modulus_difference_percent_max": of_b_limit}
    ofl_vs_ksl["status"] = "accepted" if of_passed else "accepted_error_portrait"
    al_status = "accepted" if ksl_vs_ksnl["status"] == "accepted" and all(fit["status"] == "accepted" for fit in fits.values()) else "rejected"

    mg_ids_by_ratio = {1.00: "S1-20260810-304", 0.90: "S1-20260810-313", 1.10: "S1-20260810-314"}
    if optional_ids:
        mg_ids_by_ratio.update({0.94: "S1-20260810-315", 0.97: "S1-20260810-316", 1.03: "S1-20260810-317", 1.06: "S1-20260810-318"})
    mg_new: list[dict] = []
    for ratio in sorted(mg_ids_by_ratio):
        experiment_id = mg_ids_by_ratio[ratio]
        result = new_results[experiment_id]
        mg_new.append({
            "material": "mg",
            "series": "ks_nl_diagnostic",
            "experiment_id": experiment_id,
            "volume_ratio": ratio,
            "volume_angstrom3_per_atom": new_volume(source_root / experiment_id, int(result["atom_count"])),
            "energy_ev_per_atom": result["thermodynamic_labels_ev_per_atom"]["E_ec"],
            "pressure_gpa": result["pressure_gpa"],
            "scientific_role": "diagnostic_only_PBE10e_not_comparable_to_legacy_LDA_PZ2e",
            "source_result_sha256": sha256_file(source_root / experiment_id / "result.json"),
            "source_stru_sha256": sha256_file(source_root / experiment_id / "STRU"),
        })
    mg_new = anchor(mg_new)
    point_rows.extend(mg_new)
    mg_legacy: list[dict] = []
    for ratio, experiment_id in zip(VOLUME_RATIOS, config["references"]["mg"]["ks_l"]):
        if ratio not in mg_ids_by_ratio:
            continue
        point, identities = reference_point(project_root, experiment_id, ratio, "mg", "ks_l")
        mg_legacy.append(point)
        source_identities.extend(identities)
    mg_legacy = anchor(mg_legacy)
    point_rows.extend(mg_legacy)
    mg_rows = []
    mg_by_ratio = {point["volume_ratio"]: point for point in mg_new}
    legacy_by_ratio = {point["volume_ratio"]: point for point in mg_legacy}
    for ratio in sorted(mg_by_ratio):
        mg_rows.append({
            "volume_ratio": ratio,
            "new_PBE10e_anchored_energy_mev_per_atom": mg_by_ratio[ratio]["anchored_energy_mev_per_atom"],
            "legacy_LDA_PZ2e_anchored_energy_mev_per_atom": legacy_by_ratio[ratio]["anchored_energy_mev_per_atom"],
            "confounded_difference_mev_per_atom": mg_by_ratio[ratio]["anchored_energy_mev_per_atom"] - legacy_by_ratio[ratio]["anchored_energy_mev_per_atom"],
        })
    mg_fit = fit_series(mg_new, residual_limit) if len(mg_new) == 7 else None
    mg = {
        "status": "accepted_diagnostic_only",
        "point_count": len(mg_new),
        "coverage": "seven_point" if len(mg_new) == 7 else "mandatory_three_point",
        "bm3_fit": mg_fit,
        "scientifically_gated_against_legacy": False,
        "reason": "new KS-NL is PBE/10e while legacy KS-L/OF-L is LDA-PZ/2e",
        "confounded_shape_rows": mg_rows,
    }
    session_path = (source_root.parent / "orchestration" / "session.json") if source_root.name == "raw" else Path(config["external_state_root"]) / "session.json"
    session = read_json(session_path)
    source_identities = sorted({item["path"]: item for item in source_identities}.values(), key=lambda item: item["path"])
    overall = "accepted" if al_status == "accepted" and p0["status"] == "accepted" else "rejected"
    summary = {
        "schema_version": 1,
        "protocol_revision": config["protocol_revision"],
        "status": overall,
        "scope_status": "accepted_al_seven_point_EOS_with_mg_diagnostic" if overall == "accepted" else "rejected_al_EOS_scope",
        "runner_commit": session["runner_commit"],
        "formal_required_run_count": 14,
        "optional_run_count": len(optional_ids),
        "accepted_new_run_count": len(required_ids) + len(optional_ids),
        "p0": p0,
        "al_three_layer_eos": {
            "status": al_status,
            "fits": fits,
            "ks_l_vs_ks_nl": ksl_vs_ksnl,
            "of_l_vs_ks_l": ofl_vs_ksl,
            "interpretation": "same-engine PBE/3e LPP-scheme-bias upper bound; PP construction is not controlled",
        },
        "mg_compatibility_diagnostic": mg,
        "scope_limits": {
            "second_independent_KS_engine_closed": False,
            "D_026_QE_scope_closed": False,
            "phase_or_strain_20_meV_gate_closed": False,
            "endpoint_cutoff_and_k_recheck_closed": False,
            "exact_zero_temperature_claim": False,
            "Mg_LPP_hard_comparison": False,
            "nonlocal_T_sU_decomposition_claim": False,
        },
        "absolute_energy_policy": "raw offsets reported, but no cross-PP absolute-energy acceptance gate",
        "reference_source_identities": source_identities,
    }
    return summary, sorted(point_rows, key=lambda item: (item["material"], item["series"], item["volume_ratio"])), source_identities


def points_tsv_bytes(points: list[dict]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=POINT_FIELDS, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    for point in points:
        writer.writerow({field: point[field] for field in POINT_FIELDS})
    return buffer.getvalue().encode("utf-8")


def readme_bytes(summary: dict) -> bytes:
    p0 = summary["p0"]
    al = summary["al_three_layer_eos"]
    ksl = al["ks_l_vs_ks_nl"]
    ofl = al["of_l_vs_ks_l"]
    mg = summary["mg_compatibility_diagnostic"]
    lines = [
        "# S1/G1 三层 EOS R1 结果",
        "",
        f"总体（限定范围）：`{summary['scope_status']}`；新计算 {summary['accepted_new_run_count']} 点（强制 14，可选 {summary['optional_run_count']}）。",
        "",
        f"P0：`{p0['status']}`。Al cutoff/k 的 |ΔE_ec| 分别为 {p0['materials']['al']['cutoff_abs_E_ec_difference_mev_per_atom']:.6f}/{p0['materials']['al']['normal_k_abs_E_ec_difference_mev_per_atom']:.6f} meV/atom，cutoff |ΔP|={p0['materials']['al']['cutoff_abs_pressure_difference_gpa']:.6f} GPa；Mg 对应为 {p0['materials']['mg']['cutoff_abs_E_ec_difference_mev_per_atom']:.6f}/{p0['materials']['mg']['normal_k_abs_E_ec_difference_mev_per_atom']:.6f} meV/atom、{p0['materials']['mg']['cutoff_abs_pressure_difference_gpa']:.6f} GPa。",
        "",
        f"Al KS-L vs KS-NL：`{ksl['status']}`，|ΔV0|={ksl['equilibrium_volume_difference_percent']:.6f}%，|ΔB0|={ksl['bulk_modulus_difference_percent']:.6f}%，锚定七点最大差={ksl['anchored_curve_max_abs_difference_mev_per_atom']:.6f} meV/atom。",
        "",
        f"Al OF-L vs KS-L：`{ofl['status']}`，|ΔV0|={ofl['equilibrium_volume_difference_percent']:.6f}%，|ΔB0|={ofl['bulk_modulus_difference_percent']:.6f}%，锚定七点最大差={ofl['anchored_curve_max_abs_difference_mev_per_atom']:.6f} meV/atom。若状态为 `accepted_error_portrait`，只表示误差画像被完整记录，不表示 KEDF 准确性通过。",
        "",
        f"Mg：`{mg['status']}`，{mg['point_count']} 点/{mg['coverage']}。它是 PBE/10e ABACUS 兼容性与收敛诊断；旧曲线为 LDA-PZ/2e，任何差值都不进入硬门。",
        "",
        "范围警告：本轮不是独立第二 KS/QE 验证；PseudoDojo NLPP 与现有 local BLPS 构造不同，Al 差值是 LPP 方案偏差上界；相/应变、端点 cutoff/k 复查仍未闭合；E_ec 是有限展宽估计量，不是严格 0 K 标签。",
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def write_analysis(output_root: Path, summary: dict, points: list[dict]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    atomic_write(output_root / "summary.json", canonical_json_bytes(summary))
    atomic_write(output_root / "points.tsv", points_tsv_bytes(points))
    atomic_write(output_root / "README.md", readme_bytes(summary))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("p0", "final", "replay"), required=True)
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--collect", action="store_true")
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args()
    project_root = find_project_root(args.project_root)
    config = load_config(project_root)
    rows = load_manifest(project_root)
    if args.stage == "p0":
        payload = write_p0_barrier(project_root, config)
        print(json.dumps(payload, sort_keys=True))
        return 0 if payload["status"] == "accepted" else 2
    if args.collect:
        require(args.stage == "final", "collect only applies to final stage")
        analysis_root = collect_evidence(project_root, config, rows)
        source_root = analysis_root / "raw"
    else:
        source_root = args.source_root or project_root / config["analysis_root"] / "raw"
    output_root = args.output_root or project_root / config["analysis_root"]
    summary, points, _ = build_final_analysis(project_root, config, rows, source_root.resolve())
    write_analysis(output_root.resolve(), summary, points)
    print(json.dumps({"status": summary["status"], "scope_status": summary["scope_status"], "output_root": str(output_root)}, sort_keys=True))
    return 0 if summary["status"] == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
