#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import s2_g2_al1_pilot_common_r1 as pilot

BASE_COMMIT = "76ff25f3879d48f42d5293b1b00e092375d5ca32"
CONFIG_REL = Path("config/S2_g2_atomic_fft_stage_a_r1.json")
PROTOCOL_REL = Path("docs/S2_G2_ATOMIC_FFT_STAGE_A_R1_PROTOCOL.md")
COMMON_REL = Path("scripts/s2_g2_atomic_fft_stage_a_common_r1.py")
ANALYZER_REL = Path("scripts/analyze_s2_g2_atomic_fft_stage_a_r1.py")
VALIDATOR_REL = Path("scripts/validate_s2_g2_atomic_fft_stage_a_r1.py")
TEST_REL = Path("tests/test_s2_g2_atomic_fft_stage_a_r1.py")
IMPLEMENTATION_PATHS = {str(p) for p in (CONFIG_REL, PROTOCOL_REL, COMMON_REL, ANALYZER_REL, VALIDATOR_REL, TEST_REL)}
require = pilot.require
canonical_json = pilot.canonical_json
git = pilot.git


def load_config(root: Path) -> dict:
    return json.loads((root / CONFIG_REL).read_text())


def validate_config(config: dict) -> None:
    require(config["schema_version"] == 1, "schema differs")
    require(config["protocol_revision"] == "S2-G2-ATOMIC-FFT-STAGE-A-20260812-R1", "protocol differs")
    require(config["base_commit"] == BASE_COMMIT, "base differs")
    require(config["matrix_preregistration_commit"] == "5e9054e3c5d4a2d69749c3b27a9ff794dd34ee4a", "matrix preregistration differs")
    require(config["scope"] == {"stage": "S2", "gate": "G2a_atomic_fft_stage_A_reentry", "material": "Al", "atom_count": 1, "analysis_only": True, "new_solver_run_count": 0, "stage_b_enabled": False, "g2c_enabled": False, "s3_enabled": False, "mg_enabled": False}, "scope differs")
    require(config["candidates"] == [
        {"candidate_id": "r08_atomic_fft", "radial_id": "r08", "alpha_bohr_minus2": [0.075, 0.15, 0.3, 0.6, 1.2, 2.4, 4.8, 9.6]},
        {"candidate_id": "r10_atomic_fft", "radial_id": "r10", "alpha_bohr_minus2": [0.0375, 0.075, 0.15, 0.3, 0.6, 1.2, 2.4, 4.8, 9.6, 19.2]},
    ], "candidate denominator differs")
    require(config["linear_algebra"] == {"charge_constraint": "exact_KKT_integral_equals_3", "lstsq_rcond": 1e-13, "rank_eigen_relative_cutoff": 1e-12}, "linear algebra differs")
    a = config["acceptance"]
    require(a == {"electron_number_relative_error_strict_lt": 1e-10, "density_relative_l2_strict_lt": 0.01, "minimum_density_floor_electron_per_bohr3": -1e-12, "effective_condition_number_strict_lt": 1e8, "hartree_abs_error_strict_lt_mev_per_atom": 10.0, "external_abs_error_strict_lt_mev_per_atom": 10.0, "xc_abs_error_strict_lt_mev_per_atom": 10.0, "combined_hartree_external_xc_abs_error_strict_lt_mev_per_atom": 10.0, "fixed_kedf_abs_error_strict_lt_mev_per_atom": 10.0}, "acceptance differs")
    require(config["allowed_dispositions"] == ["accepted_atomic_fft_stage_A_candidate_exists", "evidence_valid_no_atomic_fft_stage_A_candidate"], "dispositions differ")
    require(config["output"]["files"] == ["README.md", "metrics.tsv", "runtime.json", "spectra.json", "summary.json"], "output differs")


def gates(row: dict, a: dict) -> dict:
    return {
        "electron_number": row["electron_number_relative_error"] < a["electron_number_relative_error_strict_lt"],
        "density_l2": row["density_relative_l2"] < a["density_relative_l2_strict_lt"],
        "nonnegative_density": row["density_min"] >= a["minimum_density_floor_electron_per_bohr3"],
        "condition_number": row["effective_condition_number"] < a["effective_condition_number_strict_lt"],
        "hartree": abs(row["errors_mev_per_atom"]["hartree"]) < a["hartree_abs_error_strict_lt_mev_per_atom"],
        "external": abs(row["errors_mev_per_atom"]["external"]) < a["external_abs_error_strict_lt_mev_per_atom"],
        "xc": abs(row["errors_mev_per_atom"]["xc"]) < a["xc_abs_error_strict_lt_mev_per_atom"],
        "combined_hartree_external_xc": abs(row["combined_hartree_external_xc_error_mev_per_atom"]) < a["combined_hartree_external_xc_abs_error_strict_lt_mev_per_atom"],
        "fixed_kedf": abs(row["errors_mev_per_atom"]["fixed_kedf"]) < a["fixed_kedf_abs_error_strict_lt_mev_per_atom"],
    }


def build_analysis(root: Path, config: dict) -> dict:
    import numpy as np

    validate_config(config)
    head = git(root, "rev-parse", "HEAD")
    for commit in (config["matrix_preregistration_commit"], config["source"]["source_commit"]):
        require(subprocess.run(["git", "merge-base", "--is-ancestor", commit, head], cwd=root).returncode == 0, f"source is not ancestor: {commit}")
    runtime = pilot.validate_runtime(root, config)
    cell, rho, source = pilot.validate_sources(root, config)
    target = np.asarray(rho, dtype=float).reshape(-1)
    counts = np.asarray(rho.shape, dtype=int)
    frac = pilot.fractional_grid(counts)
    r2 = pilot.minimum_image_r2(frac, cell)
    volume = float(abs(np.linalg.det(cell)))
    dv = volume / target.size
    projections = {"pw_fft_reference": {"density": target.copy()}}
    spectra = {}
    for spec in config["candidates"]:
        functions = []
        for alpha in spec["alpha_bohr_minus2"]:
            fn = np.exp(-float(alpha) * r2)
            fn /= float(fn.sum(dtype=np.float64) * dv)
            functions.append(fn)
        matrix = np.stack(functions, axis=1)
        coefficients, density = pilot.constrained_fit(matrix, target, dv, config["source"]["expected_electrons"], config["linear_algebra"]["lstsq_rcond"])
        eigenvalues, rank, condition = pilot.normalized_spectrum(matrix, dv, config["linear_algebra"]["rank_eigen_relative_cutoff"])
        projections[spec["candidate_id"]] = {"density": density}
        spectra[spec["candidate_id"]] = {**spec, "basis_count": int(matrix.shape[1]), "effective_rank": rank, "effective_condition_number": condition, "normalized_overlap_eigenvalues": eigenvalues.tolist(), "coefficients": coefficients.tolist()}
    evaluation_config = dict(config)
    evaluation_config["candidate_order"] = list(projections)
    energies, stdout_sha = pilot.evaluate_operators(evaluation_config, cell, rho.shape, projections)
    reference_energy = energies["pw_fft_reference"]
    rows = []
    for spec in config["candidates"]:
        cid = spec["candidate_id"]
        density = np.asarray(projections[cid]["density"], dtype=float)
        electron_count = float(density.sum(dtype=np.float64) * dv)
        errors = {key: (energies[cid][key] - reference_energy[key]) * 1000.0 for key in reference_energy}
        row = {**spec, **{k: spectra[cid][k] for k in ("basis_count", "effective_rank", "effective_condition_number")}, "electron_count": electron_count, "electron_number_relative_error": abs(electron_count - 3.0) / 3.0, "density_relative_l2": float(np.linalg.norm(density - target) / np.linalg.norm(target)), "density_min": float(density.min()), "density_max": float(density.max()), "density_sha256_float64_le": pilot.density_sha256(density), "energies_ev_per_atom": energies[cid], "errors_mev_per_atom": errors, "combined_hartree_external_xc_error_mev_per_atom": errors["hartree"] + errors["external"] + errors["xc"]}
        row["gates"] = gates(row, config["acceptance"])
        row["failed_gates"] = [key for key, value in row["gates"].items() if not value]
        row["status"] = "accepted_stage_A" if not row["failed_gates"] else "rejected_stage_A"
        rows.append(row)
    promoted = [row["candidate_id"] for row in rows if row["status"] == "accepted_stage_A"]
    disposition = "accepted_atomic_fft_stage_A_candidate_exists" if promoted else "evidence_valid_no_atomic_fft_stage_A_candidate"
    require(disposition in config["allowed_dispositions"], "disposition differs")
    summary = {"schema_version": 1, "protocol_revision": config["protocol_revision"], "status": disposition, "evidence_valid": True, "stage": "S2", "gate": "G2a_atomic_fft_stage_A_reentry", "candidate_count": 2, "accepted_candidate_count": len(promoted), "promoted_to_stage_B": promoted, "metrics": rows, "new_solver_run_count": 0, "next_action": "execute_frozen_stage_B_with_promoted_atomic_fft_and_five_historical_low_g_candidates"}
    return {"summary": summary, "spectra": {"schema_version": 1, "protocol_revision": config["protocol_revision"], "volume_bohr3": volume, "voxel_volume_bohr3": dv, "candidates": spectra}, "runtime": {"schema_version": 1, "protocol_revision": config["protocol_revision"], **runtime, "source": source, "functional_stdout_sha256": stdout_sha}, "metrics": rows}


def render_outputs(analysis: dict) -> dict[str, bytes]:
    rows = analysis["metrics"]
    cols = ["candidate_id", "status", "basis_count", "effective_rank", "effective_condition_number", "electron_number_relative_error", "density_relative_l2", "density_min", "hartree_error_mev_per_atom", "external_error_mev_per_atom", "xc_error_mev_per_atom", "combined_error_mev_per_atom", "fixed_kedf_error_mev_per_atom", "failed_gates"]
    lines = ["\t".join(cols)]
    for row in rows:
        values = {**row, "hartree_error_mev_per_atom": row["errors_mev_per_atom"]["hartree"], "external_error_mev_per_atom": row["errors_mev_per_atom"]["external"], "xc_error_mev_per_atom": row["errors_mev_per_atom"]["xc"], "combined_error_mev_per_atom": row["combined_hartree_external_xc_error_mev_per_atom"], "fixed_kedf_error_mev_per_atom": row["errors_mev_per_atom"]["fixed_kedf"], "failed_gates": ",".join(row["failed_gates"])}
        lines.append("\t".join(str(values[col]) for col in cols))
    summary = analysis["summary"]
    readme = "\n".join(["# S2/G2 atomic_fft Stage A R1", "", f"Disposition: `{summary['status']}`.", "", f"Promoted to Stage B: `{', '.join(summary['promoted_to_stage_B']) or 'none'}`.", "", "This is deterministic analysis-only evidence over the committed Al one-atom density; no solver was run.", ""])
    return {"README.md": readme.encode(), "metrics.tsv": ("\n".join(lines) + "\n").encode(), "runtime.json": canonical_json(analysis["runtime"]), "spectra.json": canonical_json(analysis["spectra"]), "summary.json": canonical_json(summary)}
