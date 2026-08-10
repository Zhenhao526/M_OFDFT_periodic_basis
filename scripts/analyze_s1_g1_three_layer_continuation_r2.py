#!/usr/bin/env python3
"""Build deterministic analysis from recovered R1 P0 plus continuation R2."""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import shutil
from pathlib import Path

from analyze_s1_eos import fit_bm3
from s1_electron_number_common import parse_stru, pseudopotential_zion
from s1_g1_three_layer_continuation_r2_common import (
    atomic_write,
    canonical_json_bytes,
    find_project_root,
    git,
    load_config,
    load_manifest,
    parse_input_values,
    parse_kmesh,
    read_json,
    require,
    sha256_file,
)


BOHR_TO_ANGSTROM = 1.0 / 1.8897261254578281
VOLUME_RATIOS = (0.90, 0.94, 0.97, 1.00, 1.03, 1.06, 1.10)
SOURCE_IDS = tuple(f"S1-20260810-{value:03d}" for value in range(301, 307))
CONTINUATION_IDS = tuple(f"S1-20260810-{value:03d}" for value in range(327, 335))
POINT_FIELDS = (
    "material", "series", "experiment_id", "volume_ratio", "volume_angstrom3_per_atom",
    "energy_ev_per_atom", "anchored_energy_mev_per_atom", "pressure_gpa", "scientific_role",
    "source_result_sha256", "source_stru_sha256",
)


def git_blob_identity(project_root: Path, path: Path) -> dict:
    relative = path.relative_to(project_root).as_posix()
    blob = git(project_root, "rev-parse", f"HEAD:{relative}")
    mode = git(project_root, "ls-files", "-s", "--", relative).split()
    require(len(mode) >= 3 and mode[1] == blob, f"Git identity differs: {relative}")
    return {"path": relative, "sha256": sha256_file(path), "git_blob_oid": blob, "git_mode": mode[0]}


def volume(run_dir: Path, atom_count: int) -> float:
    return parse_stru(run_dir / "STRU").volume_bohr3 * BOHR_TO_ANGSTROM**3 / atom_count


def parse_kpt(path: Path) -> tuple[int, int, int]:
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    require(len(lines) == 4 and lines[:3] == ["K_POINTS", "0", "Gamma"], "KPT format differs")
    fields = lines[3].split()
    require(len(fields) == 6 and fields[3:] == ["0", "0", "0"], "KPT row differs")
    return tuple(int(value) for value in fields[:3])


def verify_new_result(run_dir: Path, expected_id: str, row: dict[str, str], config: dict) -> dict:
    result = read_json(run_dir / "result.json")
    metadata = read_json(run_dir / "metadata.json")
    accepted = read_json(run_dir / "accepted.json")
    attempt = read_json(run_dir / "attempt.json")
    runner_return = read_json(run_dir / "runner_return.json")
    require(all(isinstance(value, dict) for value in (result, metadata, accepted, attempt, runner_return)), "new payload type differs")
    require(result.get("status") == "accepted" and result.get("experiment_id") == expected_id, "new result identity differs")
    require(result.get("protocol_revision") == config["protocol_revision"], "new protocol differs")
    require(accepted.get("result_sha256") == sha256_file(run_dir / "result.json"), "new accepted/result SHA differs")
    require(attempt.get("retry_policy") == "same_id_forbidden_new_revision_and_new_ids_only", "new retry policy differs")
    require(runner_return.get("return_code") == 0, "new runner return differs")
    bindings = {
        "experiment_id": expected_id,
        "phase": row["phase"],
        "requirement": row["requirement"],
        "material": row["material"],
        "role": row["role"],
        "suffix": row["suffix"],
    }
    for key, expected in bindings.items():
        require(str(metadata.get(key)) == str(expected), f"new manifest binding differs: {expected_id}/{key}")
    require(abs(float(metadata["volume_ratio"]) - float(row["volume_ratio"])) < 1e-12, "new volume binding differs")
    require(int(metadata["ecutwfc_ry"]) == int(row["ecutwfc_ry"]) and int(metadata["ecutrho_ry"]) == int(row["ecutrho_ry"]), "new cutoff binding differs")
    require(metadata["kmesh"] == list(parse_kmesh(row["kmesh"])), "new kmesh binding differs")
    require(abs(float(metadata["expected_electrons"]) - float(row["expected_electrons"])) < 1e-12, "new Ne binding differs")
    require(metadata["runtime"]["binary_sha256"] == config["runtime"]["binary_sha256"], "new runtime binary differs")
    pseudo = result["pseudo_identity"]
    expected_pseudo = config["pseudodojo"]["materials"][row["material"]]
    require(pseudo["sha256"] == row["pseudo_sha256"] == expected_pseudo["sha256"], "new PP SHA differs")
    require(pseudo["number_of_proj"] == expected_pseudo["number_of_proj_per_atom"], "new radial projector count differs")
    require(result["runtime_nonlocal_projectors_total"] == expected_pseudo["expanded_nonlocal_projectors_per_atom"] * int(row["atom_count"]), "new expanded projector count differs")
    expected_ne = float(row["expected_electrons"])
    electron = result["electron_number"]
    for value in (electron["upf_zval_times_atom_count"], electron["reported"], electron["integrated_cube"], electron["integrated_eig_occ"]):
        require(abs(float(value) - expected_ne) < max(1e-8, expected_ne * 1e-10), "new independent Ne multiplication gate differs")
    require(result["cube_geometry"]["accepted"] and result["eigen_occupations"]["accepted"], "new cube/eig gate differs")
    require(result["stress_trace_gate"]["accepted"] and result["warning_log"]["accepted"], "new stress/warning gate differs")
    require(result["affinity"]["accepted"] and result["affinity"]["physical_core_ids"] == config["runtime"]["physical_core_ids"], "new affinity differs")
    for identity in result["evidence_files"]:
        path = run_dir / identity["path"]
        require(path.is_file() and not path.is_symlink(), f"new evidence missing: {path}")
        require(path.stat().st_size == identity["size_bytes"] and sha256_file(path) == identity["sha256"], "new evidence identity differs")
    return result


def source_result(run_dir: Path, expected_id: str, barrier: dict) -> dict:
    result = read_json(run_dir / "result.json")
    require(isinstance(result, dict) and result.get("status") == "accepted" and result.get("experiment_id") == expected_id, "source result differs")
    recovered = {row["experiment_id"]: row for row in barrier["per_run_recovery"]}[expected_id]
    require(recovered["status"] == "accepted_source_evidence", "source recovery row differs")
    require(recovered["result_sha256"] == sha256_file(run_dir / "result.json"), "source recovery/result SHA differs")
    require(recovered["accepted_result_sha256"] == recovered["result_sha256"], "source accepted/result binding differs")
    return result


def legacy_point(project_root: Path, experiment_id: str, ratio: float, material: str, series: str) -> tuple[dict, list[dict], dict]:
    run_dir = project_root / "runs" / experiment_id
    paths = {name: run_dir / name for name in ("result.json", "STRU", "INPUT", "KPT", "input_metadata.json", "experiment_metadata.json")}
    result = read_json(paths["result.json"])
    metadata = read_json(paths["input_metadata.json"])
    require(isinstance(result, dict) and result.get("converged") is True, f"legacy result rejected: {experiment_id}")
    require(isinstance(metadata, dict) and metadata.get("experiment_id") == experiment_id, "legacy metadata ID differs")
    require(metadata.get("material") == material and abs(float(metadata["volume_ratio"]) - ratio) < 1e-12, "legacy material/volume differs")
    local = {
        "al": ("al.gga.psp", "d76ceac60058e230eac514fc419269433a33b199eb6abfa7d6ab43cde248bd1d", 3.0, "XC_GGA_X_PBE+XC_GGA_C_PBE"),
        "mg": ("mg.lda.lps", "4b964580cfbd798708299bb0e0baef2f437983d29e6d7040f63b7532038b4259", 2.0, "XC_LDA_X+XC_LDA_C_PZ"),
    }[material]
    pseudo_path = run_dir / local[0]
    require(sha256_file(pseudo_path) == local[1] and abs(pseudopotential_zion(pseudo_path) - local[2]) < 1e-12, "legacy local PP identity differs")
    require(metadata["pseudopotential"] == local[0] and metadata["pseudopotential_sha256"] == local[1], "legacy metadata PP differs")
    parsed_input = parse_input_values(paths["INPUT"])
    require(parsed_input.get("basis_type") == ("pw",) and parsed_input.get("dft_functional") == (local[3],), "legacy XC/basis differs")
    if series == "ks_l":
        require(parsed_input.get("esolver_type") == ("ksdft",), "legacy KS solver differs")
        require(parsed_input.get("smearing_method") == ("fd",) and parsed_input.get("smearing_sigma") == ("0.001837465",), "legacy quarter smearing differs")
        expected_k = (28, 28, 28) if material == "al" else (24, 24, 16)
        require(parse_kpt(paths["KPT"]) == expected_k, "legacy dense k mesh differs")
        require(parsed_input.get("ecutwfc") == ("40",) and parsed_input.get("ecutrho") == ("160",), "legacy KS cutoff differs")
        runtime = read_json(paths["experiment_metadata.json"])
        require(isinstance(runtime, dict) and runtime.get("abacus_sha256") == "438c74b9ada4c8df15ffbb66da6755907dfd2a3812ecf868fafd4d7dc4db62e1", "legacy KS runtime differs")
        require(runtime.get("mpi_ranks") == 4, "legacy KS rank count differs")
        energy = result.get("zero_temp_extrapolated_energy_ev_per_atom")
        role = "legacy_local_PP_quarter_smearing_E_ec"
    else:
        require(parsed_input.get("esolver_type") == ("ofdft",), "legacy OF solver differs")
        require(parse_kpt(paths["KPT"]) == (1, 1, 1), "legacy OF KPT differs")
        energy = result.get("energy_ev_per_atom")
        role = "legacy_local_PP_OFDFT_total_energy"
    require(energy is not None and math.isfinite(float(energy)), "legacy energy missing")
    atom_count = int(result["atom_count"])
    point = {
        "material": material, "series": series, "experiment_id": experiment_id, "volume_ratio": ratio,
        "volume_angstrom3_per_atom": volume(run_dir, atom_count), "energy_ev_per_atom": float(energy),
        "pressure_gpa": float(result["pressure_gpa"]), "scientific_role": role,
        "source_result_sha256": sha256_file(paths["result.json"]), "source_stru_sha256": sha256_file(paths["STRU"]),
    }
    identity_paths = list(paths.values()) + [pseudo_path]
    identities = [git_blob_identity(project_root, path) for path in identity_paths]
    audit = {"experiment_id": experiment_id, "series": series, "material": material, "volume_ratio": ratio, "pseudo_sha256": local[1], "zion": local[2], "xc": local[3], "input_sha256": sha256_file(paths["INPUT"]), "stru_sha256": sha256_file(paths["STRU"]), "kpt_sha256": sha256_file(paths["KPT"]), "result_sha256": sha256_file(paths["result.json"]), "status": "accepted"}
    return point, identities, audit


def anchor(points: list[dict]) -> list[dict]:
    by_ratio = {round(point["volume_ratio"], 8): point for point in points}
    require(len(by_ratio) == len(points) and 1.0 in by_ratio, "series lacks unique v100 anchor")
    reference = by_ratio[1.0]["energy_ev_per_atom"]
    for point in points:
        point["anchored_energy_mev_per_atom"] = (point["energy_ev_per_atom"] - reference) * 1000.0
    return sorted(points, key=lambda item: item["volume_ratio"])


def fit_series(points: list[dict], residual_limit: float) -> dict:
    require(len(points) == 7, "BM3 requires seven registered points")
    fit = fit_bm3([point["volume_angstrom3_per_atom"] for point in points], [point["energy_ev_per_atom"] for point in points])
    sampled = [point["volume_angstrom3_per_atom"] for point in points]
    inside = min(sampled) < fit["v0_angstrom3_per_atom"] < max(sampled)
    residual = fit["max_abs_residual_mev_per_atom"] < residual_limit
    return {**fit, "equilibrium_volume_inside_sampled_interval": inside, "residual_passed": residual, "status": "accepted" if inside and residual else "rejected"}


def compare_series(reference: list[dict], comparison: list[dict], reference_fit: dict, comparison_fit: dict) -> dict:
    ref = {point["volume_ratio"]: point for point in reference}
    cmp = {point["volume_ratio"]: point for point in comparison}
    require(set(ref) == set(cmp) == set(VOLUME_RATIOS), "EOS volume domain differs")
    rows = []
    offsets = []
    for ratio in VOLUME_RATIOS:
        anchored = cmp[ratio]["anchored_energy_mev_per_atom"] - ref[ratio]["anchored_energy_mev_per_atom"]
        offset = cmp[ratio]["energy_ev_per_atom"] - ref[ratio]["energy_ev_per_atom"]
        rows.append({"volume_ratio": ratio, "reference_anchored_energy_mev_per_atom": ref[ratio]["anchored_energy_mev_per_atom"], "comparison_anchored_energy_mev_per_atom": cmp[ratio]["anchored_energy_mev_per_atom"], "anchored_difference_mev_per_atom": anchored, "raw_absolute_energy_offset_ev_per_atom": offset})
        offsets.append(offset)
    mean = sum(offsets) / len(offsets)
    return {
        "equilibrium_volume_difference_percent": abs(comparison_fit["v0_angstrom3_per_atom"] - reference_fit["v0_angstrom3_per_atom"]) / reference_fit["v0_angstrom3_per_atom"] * 100,
        "bulk_modulus_difference_percent": abs(comparison_fit["b0_gpa"] - reference_fit["b0_gpa"]) / reference_fit["b0_gpa"] * 100,
        "anchored_curve_max_abs_difference_mev_per_atom": max(abs(row["anchored_difference_mev_per_atom"]) for row in rows),
        "anchored_curve_rms_difference_mev_per_atom": math.sqrt(sum(row["anchored_difference_mev_per_atom"] ** 2 for row in rows) / len(rows)),
        "raw_absolute_energy_offset_mean_ev_per_atom": mean,
        "raw_absolute_energy_offset_centered_rms_mev_per_atom": math.sqrt(sum((value - mean) ** 2 for value in offsets) / len(offsets)) * 1000,
        "absolute_energy_gate_applied": False, "rows": rows,
    }


def raw_roots(raw_root: Path) -> tuple[Path, Path, Path]:
    return raw_root / "source_r1", raw_root / "continuation", raw_root.parent / "orchestration"


def build_final_analysis(project_root: Path, config: dict, rows: list[dict[str, str]], raw_root: Path) -> tuple[dict, list[dict], list[dict]]:
    source_root, continuation_root, orchestration = raw_roots(raw_root)
    barrier = read_json(orchestration / "r1_p0_recovery.json")
    require(isinstance(barrier, dict) and barrier.get("status") == "accepted", "collected recovery barrier rejected")
    require(barrier.get("source_operational_phase_accepted") is False and barrier.get("scientific_p0_recovery_status") == "accepted", "R1 disposition differs")
    source_results = {experiment_id: source_result(source_root / experiment_id, experiment_id, barrier) for experiment_id in SOURCE_IDS}
    row_by_id = {row["experiment_id"]: row for row in rows}
    continuation_results = {experiment_id: verify_new_result(continuation_root / experiment_id, experiment_id, row_by_id[experiment_id], config) for experiment_id in CONTINUATION_IDS}
    point_rows: list[dict] = []
    source_identities: list[dict] = []
    legacy_audit: list[dict] = []
    al_ids = ("S1-20260810-327", "S1-20260810-329", "S1-20260810-330", "S1-20260810-301", "S1-20260810-331", "S1-20260810-332", "S1-20260810-328")
    al_ksnl = []
    for ratio, experiment_id in zip(VOLUME_RATIOS, al_ids):
        root = source_root if experiment_id in SOURCE_IDS else continuation_root
        result = source_results.get(experiment_id) or continuation_results[experiment_id]
        require(abs(float(result["volume_ratio"]) - ratio) < 1e-12, "Al KS-NL ratio differs")
        al_ksnl.append({"material": "al", "series": "ks_nl", "experiment_id": experiment_id, "volume_ratio": ratio, "volume_angstrom3_per_atom": volume(root / experiment_id, int(result["atom_count"])), "energy_ev_per_atom": result["thermodynamic_labels_ev_per_atom"]["E_ec"], "pressure_gpa": result["pressure_gpa"], "scientific_role": "ABACUS_PBE3e_PseudoDojo_KS_NL_Al_hard_EOS", "source_result_sha256": sha256_file(root / experiment_id / "result.json"), "source_stru_sha256": sha256_file(root / experiment_id / "STRU")})
    legacy: dict[str, list[dict]] = {"al_ks_l": [], "al_of_l": [], "mg_ks_l": [], "mg_of_l": []}
    for material in ("al", "mg"):
        for series in ("ks_l", "of_l"):
            key = f"{material}_{series}"
            for ratio, experiment_id in zip(VOLUME_RATIOS, config["references"][material][series]):
                point, identities, audit = legacy_point(project_root, experiment_id, ratio, material, series)
                legacy[key].append(point)
                source_identities.extend(identities)
                legacy_audit.append(audit)
    for index in range(7):
        require((project_root / "runs" / config["references"]["al"]["ks_l"][index] / "STRU").read_bytes() == (project_root / "runs" / config["references"]["al"]["of_l"][index] / "STRU").read_bytes(), "Al KS-L/OF-L volume mapping differs")
    al_ksnl, al_ksl, al_ofl = anchor(al_ksnl), anchor(legacy["al_ks_l"]), anchor(legacy["al_of_l"])
    point_rows.extend(al_ksnl + al_ksl + al_ofl)
    residual_limit = float(config["acceptance"]["bm3_max_abs_residual_mev_per_atom_strictly_less_than"])
    fits = {"ks_nl": fit_series(al_ksnl, residual_limit), "ks_l": fit_series(al_ksl, residual_limit), "of_l": fit_series(al_ofl, residual_limit)}
    ksl = compare_series(al_ksnl, al_ksl, fits["ks_nl"], fits["ks_l"])
    ksl_v = float(config["acceptance"]["al_ksl_vs_ksnl_equilibrium_volume_difference_percent_max"])
    ksl_b = float(config["acceptance"]["al_ksl_vs_ksnl_bulk_modulus_difference_percent_max"])
    ksl["thresholds"] = {"equilibrium_volume_difference_percent_max": ksl_v, "bulk_modulus_difference_percent_max": ksl_b}
    ksl["status"] = "accepted" if ksl["equilibrium_volume_difference_percent"] <= ksl_v and ksl["bulk_modulus_difference_percent"] <= ksl_b and fits["ks_nl"]["status"] == fits["ks_l"]["status"] == "accepted" else "rejected"
    ofl = compare_series(al_ksl, al_ofl, fits["ks_l"], fits["of_l"])
    of_v = float(config["acceptance"]["ofdft_vs_ksl_equilibrium_volume_difference_percent_max"])
    of_b = float(config["acceptance"]["ofdft_vs_ksl_bulk_modulus_difference_percent_max"])
    ofl["thresholds"] = {"equilibrium_volume_difference_percent_max": of_v, "bulk_modulus_difference_percent_max": of_b}
    ofl_pass = ofl["equilibrium_volume_difference_percent"] <= of_v and ofl["bulk_modulus_difference_percent"] <= of_b and fits["of_l"]["status"] == "accepted"
    ofl["status"] = "accepted" if ofl_pass else "accepted_error_portrait"
    al_p0 = barrier["p0_metrics"]["materials"]["al"]
    al_status = "accepted" if al_p0["status"] == "accepted" and ksl["status"] == "accepted" and all(fit["status"] == "accepted" for fit in fits.values()) else "rejected"
    mg_ids = {0.90: "S1-20260810-333", 1.00: "S1-20260810-304", 1.10: "S1-20260810-334"}
    mg_new = []
    for ratio, experiment_id in mg_ids.items():
        root = source_root if experiment_id in SOURCE_IDS else continuation_root
        result = source_results.get(experiment_id) or continuation_results[experiment_id]
        mg_new.append({"material": "mg", "series": "ks_nl_diagnostic", "experiment_id": experiment_id, "volume_ratio": ratio, "volume_angstrom3_per_atom": volume(root / experiment_id, int(result["atom_count"])), "energy_ev_per_atom": result["thermodynamic_labels_ev_per_atom"]["E_ec"], "pressure_gpa": result["pressure_gpa"], "scientific_role": "diagnostic_only_PBE10e", "source_result_sha256": sha256_file(root / experiment_id / "result.json"), "source_stru_sha256": sha256_file(root / experiment_id / "STRU")})
    mg_new = anchor(mg_new)
    mg_ksl = anchor([point for point in legacy["mg_ks_l"] if point["volume_ratio"] in mg_ids])
    mg_ofl = anchor([point for point in legacy["mg_of_l"] if point["volume_ratio"] in mg_ids])
    point_rows.extend(mg_new + mg_ksl + mg_ofl)
    mg_rows = []
    series_by_name = {name: {point["volume_ratio"]: point for point in values} for name, values in (("ks_nl_PBE10e", mg_new), ("ks_l_LDA_PZ2e", mg_ksl), ("of_l_LDA_PZ2e", mg_ofl))}
    for ratio in sorted(mg_ids):
        mg_rows.append({"volume_ratio": ratio, **{f"{name}_anchored_energy_mev_per_atom": values[ratio]["anchored_energy_mev_per_atom"] for name, values in series_by_name.items()}})
    mg = {"status": "accepted_diagnostic_only", "coverage": "mandatory_three_point_three_curve", "ks_nl_point_count": 3, "ks_l_point_count": 3, "of_l_point_count": 3, "scientifically_gated_against_legacy": False, "reason": "KS-NL is PBE/10e while legacy KS-L and OF-L are LDA-PZ/2e", "three_curve_rows": mg_rows, "p0_diagnostic_status": barrier["p0_metrics"]["materials"]["mg"]["status"], "affects_overall": False}
    source_identities = sorted({item["path"]: item for item in source_identities}.values(), key=lambda item: item["path"])
    overall = "accepted" if al_status == "accepted" else "rejected"
    session = read_json(orchestration / "session.json")
    summary = {
        "schema_version": 2, "protocol_revision": config["protocol_revision"], "status": overall,
        "scope_status": "accepted_al_seven_point_EOS_with_mg_three_curve_diagnostic" if overall == "accepted" else "rejected_al_EOS_scope",
        "runner_commit": session["runner_commit"], "recovery_barrier_sha256": sha256_file(orchestration / "r1_p0_recovery.json"),
        "source_r1_run_count": 6, "source_r1_new_run_count": 0, "formal_continuation_run_count": 8,
        "formal_al_hard_continuation_count": 6, "formal_mg_diagnostic_continuation_count": 2,
        "source_r1_operational_disposition": {"status": barrier["source_operational_status"], "phase_accepted": False, "scientific_p0_recovery": "accepted", "joint_operational_barrier_depended_on_al_and_mg": True},
        "p0_recovery": {"status": "accepted", "al_hard": barrier["p0_metrics"]["materials"]["al"], "mg_diagnostic_only": barrier["p0_metrics"]["materials"]["mg"], "overall_hard_domain_uses_al_only": True},
        "al_three_layer_eos": {"status": al_status, "fits": fits, "ks_l_vs_ks_nl": ksl, "of_l_vs_ks_l": ofl, "interpretation": "registered PP-pair scheme plus construction discrepancy and LPP suitability bound; not a projector-only causal experiment"},
        "mg_compatibility_diagnostic": mg,
        "runtime_provenance": config["runtime_provenance"],
        "scope_limits": {"second_independent_KS_engine_closed": False, "D_026_QE_scope_closed": False, "phase_or_strain_20_meV_gate_closed": False, "endpoint_cutoff_and_k_recheck_closed": False, "exact_zero_temperature_claim": False, "Mg_LPP_hard_comparison": False, "nonlocal_T_sU_decomposition_claim": False, "G1_six_of_six_closed_by_this_scope": False},
        "absolute_energy_policy": "raw offsets reported for audit; no cross-PP absolute-energy gate",
        "legacy_domain_audit": legacy_audit, "reference_source_identities": source_identities,
    }
    return summary, sorted(point_rows, key=lambda item: (item["material"], item["series"], item["volume_ratio"])), source_identities


def copy_exact(source: Path, destination: Path) -> None:
    require(source.is_file() and not source.is_symlink() and not destination.exists(), f"unsafe evidence copy: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    require(sha256_file(source) == sha256_file(destination), "evidence copy SHA differs")


def collect_run(source_state: Path, experiment_id: str, destination: Path, config: dict) -> None:
    run = source_state / "runs" / experiment_id
    metadata = read_json(run / "metadata.json")
    require(isinstance(metadata, dict), "collection metadata differs")
    for name in ("INPUT", "STRU", "KPT", "input_metadata.json", "metadata.json", "pseudo_identity.json", "run.stdout", "run.stderr", "runner_return.json", "result.json"):
        copy_exact(run / name, destination / name)
    for rank in range(int(config["runtime"]["rank_count"])):
        copy_exact(run / "affinity" / f"rank_{rank:03d}.json", destination / "affinity" / f"rank_{rank:03d}.json")
    out = run / f"OUT.{metadata['suffix']}"
    for name in ("running_scf.log", "chg.cube", "eig_occ.txt", "warning.log"):
        copy_exact(out / name, destination / f"OUT.{metadata['suffix']}" / name)
    copy_exact(source_state / "attempts" / f"{experiment_id}.json", destination / "attempt.json")
    copy_exact(source_state / "accepted" / f"{experiment_id}.json", destination / "accepted.json")


def collect_evidence(project_root: Path, config: dict) -> Path:
    analysis = project_root / config["analysis_root"]
    require(not analysis.exists(), "analysis root already exists")
    source_state = Path(config["source_r1"]["state_root"])
    continuation_state = Path(config["external_state_root"])
    for experiment_id in SOURCE_IDS:
        collect_run(source_state, experiment_id, analysis / "raw" / "source_r1" / experiment_id, config)
    for experiment_id in CONTINUATION_IDS:
        collect_run(continuation_state, experiment_id, analysis / "raw" / "continuation" / experiment_id, config)
    copy_exact(continuation_state / "barriers" / "r1_p0_recovery.json", analysis / "orchestration" / "r1_p0_recovery.json")
    copy_exact(continuation_state / "session.json", analysis / "orchestration" / "session.json")
    for phase in ("al_eos", "mg_required"):
        copy_exact(continuation_state / "phases" / f"{phase}.json", analysis / "orchestration" / "phases" / f"{phase}.json")
    copy_exact(source_state / "session.json", analysis / "orchestration" / "source_r1_session.json")
    return analysis


def points_tsv_bytes(points: list[dict]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=POINT_FIELDS, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    for point in points:
        writer.writerow({field: point[field] for field in POINT_FIELDS})
    return buffer.getvalue().encode("utf-8")


def readme_bytes(summary: dict) -> bytes:
    al = summary["al_three_layer_eos"]
    ksl, ofl = al["ks_l_vs_ks_nl"], al["of_l_vs_ks_l"]
    lines = [
        "# S1/G1 三层 EOS continuation R2 结果", "",
        f"限定总体：`{summary['scope_status']}`。R1 301–306 是只读 recovery source（0 个新 run）；R2 新 run 为 8 个。", "",
        f"R1 operational phase 仍未接受；独立 scientific P0 recovery 为 `{summary['p0_recovery']['status']}`。最终 hard-domain 只由 Al 决定，Mg 不影响 overall。", "",
        f"Al KS-L vs KS-NL：`{ksl['status']}`，|ΔV0|={ksl['equilibrium_volume_difference_percent']:.6f}%，|ΔB0|={ksl['bulk_modulus_difference_percent']:.6f}%，锚定最大差={ksl['anchored_curve_max_abs_difference_mev_per_atom']:.6f} meV/atom。", "",
        f"Al OF-L vs KS-L：`{ofl['status']}`，|ΔV0|={ofl['equilibrium_volume_difference_percent']:.6f}%，|ΔB0|={ofl['bulk_modulus_difference_percent']:.6f}%。`accepted_error_portrait` 只表示误差画像完整。", "",
        "Mg 是 PBE/10e KS-NL、LDA-PZ/2e KS-L、LDA-PZ/2e OF-L 的三点三曲线纯诊断，不作硬比较。", "",
        "科学边界：Al 仅是登记 PP 对的 scheme+construction discrepancy / LPP suitability bound；不是 projector-only 因果实验，也不是独立第二 KS/QE。端点复查、应变/相门和完整 G1 仍未由本范围闭合。", "",
        "新 NL 与 legacy KS-L 使用同冻结源码的科学等价实现，但二进制 SHA 不同；a01ac707 的 6/6 storage-exact bridge 不等于 byte-identical same-engine。",
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def write_analysis(output_root: Path, summary: dict, points: list[dict]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    atomic_write(output_root / "summary.json", canonical_json_bytes(summary))
    atomic_write(output_root / "points.tsv", points_tsv_bytes(points))
    atomic_write(output_root / "README.md", readme_bytes(summary))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--collect", action="store_true")
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args()
    project_root = find_project_root(args.project_root)
    config = load_config(project_root)
    rows = load_manifest(project_root)
    analysis = collect_evidence(project_root, config) if args.collect else project_root / config["analysis_root"]
    raw_root = args.source_root or analysis / "raw"
    output = args.output_root or analysis
    summary, points, _ = build_final_analysis(project_root, config, rows, raw_root.resolve())
    write_analysis(output.resolve(), summary, points)
    print(json.dumps({"status": summary["status"], "scope_status": summary["scope_status"], "output_root": str(output)}, sort_keys=True))
    return 0 if summary["status"] == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
