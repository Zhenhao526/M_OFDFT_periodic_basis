#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import s2_g2_al1_pilot_common_r1 as pilot

BASE_COMMIT = "9c774e8c30142c1351f43cda7b61c145ff4964c1"
CONFIG_REL = Path("config/S2_g2_al1_basis_convergence_r2.json")
PROTOCOL_REL = Path("docs/S2_G2_AL1_BASIS_CONVERGENCE_R2_PROTOCOL.md")
COMMON_REL = Path("scripts/s2_g2_al1_basis_convergence_common_r2.py")
ANALYZER_REL = Path("scripts/analyze_s2_g2_al1_basis_convergence_r2.py")
VALIDATOR_REL = Path("scripts/validate_s2_g2_al1_basis_convergence_r2.py")
TEST_REL = Path("tests/unit/test_s2_g2_al1_basis_convergence_r2.py")
IMPLEMENTATION_PATHS = {str(p) for p in (CONFIG_REL, PROTOCOL_REL, COMMON_REL, ANALYZER_REL, VALIDATOR_REL, TEST_REL)}

require = pilot.require
canonical_json = pilot.canonical_json
sha256_bytes = pilot.sha256_bytes
sha256_path = pilot.sha256_path
git = pilot.git


def load_config(root: Path) -> dict:
    return json.loads((root / CONFIG_REL).read_text())


def expected_radial_levels() -> list[dict]:
    return [
        {"id": "r04", "alpha_bohr_minus2": [0.3, 0.6, 1.2, 2.4]},
        {"id": "r06", "alpha_bohr_minus2": [0.15, 0.3, 0.6, 1.2, 2.4, 4.8]},
        {"id": "r08", "alpha_bohr_minus2": [0.075, 0.15, 0.3, 0.6, 1.2, 2.4, 4.8, 9.6]},
        {"id": "r10", "alpha_bohr_minus2": [0.0375, 0.075, 0.15, 0.3, 0.6, 1.2, 2.4, 4.8, 9.6, 19.2]},
    ]


def candidate_specs(config: dict) -> list[dict]:
    specs = []
    for radial in config["convergence_matrix"]["radial_levels"]:
        for eta in config["convergence_matrix"]["low_g_eta_levels"]:
            for gauge in config["convergence_matrix"]["gauges"]:
                specs.append({
                    "candidate_id": f"{radial['id']}_eta{int(round(float(eta) * 100)):03d}_{gauge}",
                    "radial_id": radial["id"],
                    "radial_count": len(radial["alpha_bohr_minus2"]),
                    "alpha_bohr_minus2": list(radial["alpha_bohr_minus2"]),
                    "low_g_eta_max": float(eta),
                    "gauge": gauge,
                })
    return specs


def validate_config(config: dict) -> None:
    require(config["schema_version"] == 1, "schema differs")
    require(config["protocol_revision"] == "S2-G2-AL1-BASIS-CONVERGENCE-20260811-R2", "protocol differs")
    require(config["base_commit"] == BASE_COMMIT, "base commit differs")
    require(config["scope"] == {
        "stage": "S2", "gate": "G2a_basis_convergence", "material": "Al", "atom_count": 1,
        "analysis_only": True, "new_solver_run_count": 0, "mg_enabled": False,
        "larger_cells_enabled": False, "ml_enabled": False,
        "self_consistent_optimization_enabled": False,
    }, "scope differs")
    previous = config["previous_pilot"]
    require(previous["evidence_commit"] == "269fe3a9e69ee8ce6842999c75930942e2a36675", "previous evidence differs")
    require(previous["required_status"] == "evidence_valid_no_candidate_passes_all_g2a_pilot_gates", "previous status differs")
    require(previous["reused_common_sha256"] == "6bf4c6dd6601b5f6a4bd67e8619144bb66d3ca5e573079347491766e2f9d2329", "reused common identity differs")
    matrix = config["convergence_matrix"]
    require(matrix["radial_levels"] == expected_radial_levels(), "radial ladder differs")
    require(matrix["low_g_eta_levels"] == [1.0, 1.3, 1.5, 1.6, 1.8, 2.0], "low-G ladder differs")
    require(matrix["expected_half_space_vector_counts"] == [7, 13, 25, 29, 32, 56], "low-G counts differ")
    require(matrix["gauges"] == ["explicit", "complementary"], "gauge denominator differs")
    require(matrix["compressed_candidate_count"] == 48 and len(candidate_specs(config)) == 48, "candidate denominator differs")
    require(matrix["integer_search_bound"] == 6, "integer search bound differs")
    require(matrix["development_probe_is_not_formal_evidence"] is True, "probe boundary differs")
    require(matrix["lstsq_rcond"] == 1e-13 and matrix["rank_eigen_relative_cutoff"] == 1e-12, "linear algebra cutoff differs")
    acceptance = config["acceptance"]
    require(acceptance["electron_number_relative_error_strict_lt"] == 1e-10, "electron gate differs")
    require(acceptance["density_relative_l2_strict_lt"] == 0.01, "density gate differs")
    require(acceptance["minimum_density_floor_electron_per_bohr3"] == -1e-12, "positivity gate differs")
    require(acceptance["effective_condition_number_strict_lt"] == 1e8, "condition gate differs")
    for key in (
        "hartree_abs_error_strict_lt_mev_per_atom", "external_abs_error_strict_lt_mev_per_atom",
        "xc_abs_error_strict_lt_mev_per_atom", "combined_hartree_external_xc_abs_error_strict_lt_mev_per_atom",
        "fixed_kedf_abs_error_strict_lt_mev_per_atom",
    ):
        require(acceptance[key] == 10.0, f"energy gate differs: {key}")
    require(acceptance["gauge_density_relative_difference_strict_lt"] == 1e-7, "gauge density gate differs")
    require(acceptance["gauge_operator_error_difference_strict_lt_mev_per_atom"] == 0.001, "gauge operator gate differs")
    require(config["selection"] == {
        "eligible_gauge": "complementary",
        "order": ["basis_count", "effective_condition_number", "candidate_id"],
        "require_pair_equivalence": True,
    }, "selection policy differs")
    require(config["allowed_scientific_dispositions"] == [
        "accepted_converged_candidate_exists", "evidence_valid_no_converged_candidate"
    ], "allowed dispositions differ")
    require(config["output"]["files"] == [
        "README.md", "basis_spectrum.json", "convergence.tsv", "pair_equivalence.tsv", "runtime.json", "summary.json"
    ], "output denominator differs")


def validate_previous_pilot(root: Path, config: dict) -> dict:
    previous = config["previous_pilot"]
    head = git(root, "rev-parse", "HEAD")
    require(subprocess.run(["git", "merge-base", "--is-ancestor", previous["evidence_commit"], head], cwd=root).returncode == 0,
            "previous evidence is not an ancestor")
    observed = {}
    for key in ("summary", "metrics"):
        path = root / previous[f"{key}_path"]
        require(path.is_file() and not path.is_symlink(), f"previous {key} is absent or symlinked")
        require(sha256_path(path) == previous[f"{key}_sha256"], f"previous {key} SHA differs")
        committed = subprocess.run(
            ["git", "show", f"{previous['evidence_commit']}:{previous[f'{key}_path']}"], cwd=root,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        ).stdout
        require(sha256_bytes(committed) == previous[f"{key}_sha256"], f"committed previous {key} differs")
        observed[f"{key}_sha256"] = previous[f"{key}_sha256"]
    summary = json.loads((root / previous["summary_path"]).read_text())
    require(summary["status"] == previous["required_status"], "previous scientific status differs")
    require(summary["new_solver_run_count"] == 0 and summary["accepted_candidates"] == [], "previous pilot denominator differs")
    dependency = root / previous["reused_common_path"]
    require(dependency.is_file() and not dependency.is_symlink(), "reused common module is absent or symlinked")
    require(sha256_path(dependency) == previous["reused_common_sha256"], "reused common module SHA differs")
    base_bytes = subprocess.run(
        ["git", "show", f"{BASE_COMMIT}:{previous['reused_common_path']}"], cwd=root,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    ).stdout
    require(sha256_bytes(base_bytes) == previous["reused_common_sha256"], "base common module differs")
    return {
        "evidence_commit": previous["evidence_commit"], "status": summary["status"],
        **observed, "reused_common_sha256": previous["reused_common_sha256"],
    }


def build_projections(config: dict, cell, reference_density):
    import numpy as np

    matrix_cfg = config["convergence_matrix"]
    counts = np.asarray(reference_density.shape, dtype=int)
    frac = pilot.fractional_grid(counts)
    target = np.asarray(reference_density, dtype=float).reshape(-1)
    volume = float(abs(np.linalg.det(cell)))
    dv = volume / target.size
    r2 = pilot.minimum_image_r2(frac, cell)
    constant = np.full((target.size, 1), 1.0 / volume)
    projections = {
        "pw_fft_reference": {
            "density": target.copy(), "coefficients": [], "basis_count": int(target.size),
            "effective_rank": int(target.size), "effective_condition_number": 1.0,
            "normalized_overlap_eigenvalues": [1.0],
            "spec": {"candidate_id": "pw_fft_reference", "gauge": "reference", "radial_id": None,
                     "radial_count": 0, "low_g_eta_max": None, "half_space_vector_count": 0},
        }
    }
    shell_context = []
    low_blocks = {}
    kf = None
    for eta, expected_count in zip(matrix_cfg["low_g_eta_levels"], matrix_cfg["expected_half_space_vector_counts"]):
        rows, functions, observed_kf = pilot.low_g_vectors(
            frac, cell, config["source"]["expected_electrons"], float(eta), matrix_cfg["integer_search_bound"]
        )
        expanded_rows, _, _ = pilot.low_g_vectors(
            frac, cell, config["source"]["expected_electrons"], float(eta), matrix_cfg["integer_search_bound"] + 1
        )
        require([row[1] for row in rows] == [row[1] for row in expanded_rows], "integer search bound is incomplete")
        require(len(rows) == expected_count, "registered low-G shell count differs")
        low_blocks[float(eta)] = (rows, functions)
        kf = observed_kf
        shell_context.append({
            "low_g_eta_max": float(eta), "half_space_vector_count": len(rows),
            "real_function_count": 2 * len(rows),
            "vectors": [{"eta_q_over_2kf": value, "integer_vector": list(vector)} for value, vector in rows],
        })
    for radial in matrix_cfg["radial_levels"]:
        gaussians = []
        for alpha in radial["alpha_bohr_minus2"]:
            function = np.exp(-float(alpha) * r2)
            function /= float(function.sum(dtype=np.float64) * dv)
            gaussians.append(function)
        atomic = np.stack(gaussians, axis=1)
        compensated = atomic - np.mean(atomic, axis=0, keepdims=True)
        for eta in matrix_cfg["low_g_eta_levels"]:
            rows, low_functions = low_blocks[float(eta)]
            explicit = np.column_stack((constant, compensated, low_functions))
            low_basis = np.column_stack((constant, low_functions))
            low_q, _ = np.linalg.qr(low_basis, mode="reduced")
            complementary_atomic = compensated - low_q @ (low_q.T @ compensated)
            complementary = np.column_stack((constant, low_functions, complementary_atomic))
            for gauge, matrix in (("explicit", explicit), ("complementary", complementary)):
                candidate_id = f"{radial['id']}_eta{int(round(float(eta) * 100)):03d}_{gauge}"
                coefficients, density = pilot.constrained_fit(
                    matrix, target, dv, config["source"]["expected_electrons"], matrix_cfg["lstsq_rcond"]
                )
                eigenvalues, rank, condition = pilot.normalized_spectrum(
                    matrix, dv, matrix_cfg["rank_eigen_relative_cutoff"]
                )
                projections[candidate_id] = {
                    "density": density, "coefficients": coefficients.tolist(), "basis_count": int(matrix.shape[1]),
                    "effective_rank": rank, "effective_condition_number": condition,
                    "normalized_overlap_eigenvalues": eigenvalues.tolist(),
                    "spec": {
                        "candidate_id": candidate_id, "gauge": gauge, "radial_id": radial["id"],
                        "radial_count": len(radial["alpha_bohr_minus2"]), "low_g_eta_max": float(eta),
                        "half_space_vector_count": len(rows),
                    },
                }
    require(list(projections)[1:] == [spec["candidate_id"] for spec in candidate_specs(config)], "candidate order differs")
    return projections, {
        "volume_bohr3": volume, "voxel_volume_bohr3": dv, "kf_bohr_inverse": kf,
        "cell_bohr": np.asarray(cell).tolist(), "low_g_shells": shell_context,
    }


def metric_gates(row: dict, acceptance: dict) -> dict:
    return {
        "electron_number": row["electron_number_relative_error"] < acceptance["electron_number_relative_error_strict_lt"],
        "density_l2": row["density_relative_l2"] < acceptance["density_relative_l2_strict_lt"],
        "nonnegative_density": row["density_min"] >= acceptance["minimum_density_floor_electron_per_bohr3"],
        "condition_number": row["effective_condition_number"] < acceptance["effective_condition_number_strict_lt"],
        "hartree": abs(row["errors_mev_per_atom"]["hartree"]) < acceptance["hartree_abs_error_strict_lt_mev_per_atom"],
        "external": abs(row["errors_mev_per_atom"]["external"]) < acceptance["external_abs_error_strict_lt_mev_per_atom"],
        "xc": abs(row["errors_mev_per_atom"]["xc"]) < acceptance["xc_abs_error_strict_lt_mev_per_atom"],
        "combined_hartree_external_xc": abs(row["combined_hartree_external_xc_error_mev_per_atom"]) < acceptance["combined_hartree_external_xc_abs_error_strict_lt_mev_per_atom"],
        "fixed_kedf": abs(row["errors_mev_per_atom"]["fixed_kedf"]) < acceptance["fixed_kedf_abs_error_strict_lt_mev_per_atom"],
    }


def build_analysis(root: Path, config: dict) -> dict:
    import numpy as np

    validate_config(config)
    previous = validate_previous_pilot(root, config)
    runtime = pilot.validate_runtime(root, config)
    cell, rho, source = pilot.validate_sources(root, config)
    projections, basis_context = build_projections(config, cell, rho)
    candidate_order = list(projections)
    evaluation_config = dict(config)
    evaluation_config["candidate_order"] = candidate_order
    energies, functional_stdout_sha = pilot.evaluate_operators(evaluation_config, cell, rho.shape, projections)
    reference_density = np.asarray(projections["pw_fft_reference"]["density"], dtype=float)
    reference_energies = energies["pw_fft_reference"]
    dv = basis_context["voxel_volume_bohr3"]
    expected_electrons = config["source"]["expected_electrons"]
    metrics = []
    spectra = {}
    for candidate_id in candidate_order:
        projection = projections[candidate_id]
        density = np.asarray(projection["density"], dtype=float)
        electrons = float(np.sum(density, dtype=np.float64) * dv)
        errors = {key: (energies[candidate_id][key] - reference_energies[key]) * 1000.0 for key in reference_energies}
        combined = errors["hartree"] + errors["external"] + errors["xc"]
        row = {
            **projection["spec"], "basis_count": projection["basis_count"],
            "effective_rank": projection["effective_rank"],
            "effective_condition_number": projection["effective_condition_number"],
            "electron_count": electrons,
            "electron_number_relative_error": abs(electrons - expected_electrons) / expected_electrons,
            "density_relative_l2": float(np.linalg.norm(density - reference_density) / np.linalg.norm(reference_density)),
            "density_min": float(density.min()), "density_max": float(density.max()),
            "density_sha256_float64_le": pilot.density_sha256(density),
            "energies_ev_per_atom": energies[candidate_id], "errors_mev_per_atom": errors,
            "combined_hartree_external_xc_error_mev_per_atom": combined,
        }
        if candidate_id == "pw_fft_reference":
            row["gates"] = {key: True for key in (
                "electron_number", "density_l2", "nonnegative_density", "condition_number", "hartree",
                "external", "xc", "combined_hartree_external_xc", "fixed_kedf",
            )}
            row["failed_gates"] = []
            row["pair_equivalence"] = True
            row["status"] = "accepted_reference"
        else:
            row["gates"] = metric_gates(row, config["acceptance"])
            row["failed_gates"] = [key for key, value in row["gates"].items() if not value]
            row["pair_equivalence"] = None
            row["status"] = "pending_pair_equivalence"
        metrics.append(row)
        spectra[candidate_id] = {
            **projection["spec"], "basis_count": projection["basis_count"],
            "effective_rank": projection["effective_rank"],
            "effective_condition_number": projection["effective_condition_number"],
            "normalized_overlap_eigenvalues": projection["normalized_overlap_eigenvalues"],
            "coefficients": projection["coefficients"],
        }
    metric_map = {row["candidate_id"]: row for row in metrics}
    pairs = []
    pair_map = {}
    pair_acceptance = config["acceptance"]
    for radial in config["convergence_matrix"]["radial_levels"]:
        for eta in config["convergence_matrix"]["low_g_eta_levels"]:
            stem = f"{radial['id']}_eta{int(round(float(eta) * 100)):03d}"
            explicit_id = f"{stem}_explicit"
            complementary_id = f"{stem}_complementary"
            explicit_density = np.asarray(projections[explicit_id]["density"], dtype=float)
            complementary_density = np.asarray(projections[complementary_id]["density"], dtype=float)
            density_difference = float(np.linalg.norm(explicit_density - complementary_density) / np.linalg.norm(reference_density))
            operator_differences = {
                key: abs(metric_map[explicit_id]["errors_mev_per_atom"][key] - metric_map[complementary_id]["errors_mev_per_atom"][key])
                for key in reference_energies
            }
            operator_differences["combined_hartree_external_xc"] = abs(
                metric_map[explicit_id]["combined_hartree_external_xc_error_mev_per_atom"]
                - metric_map[complementary_id]["combined_hartree_external_xc_error_mev_per_atom"]
            )
            gates = {
                "density_equivalence": density_difference < pair_acceptance["gauge_density_relative_difference_strict_lt"],
                "operator_equivalence": max(operator_differences.values()) < pair_acceptance["gauge_operator_error_difference_strict_lt_mev_per_atom"],
            }
            pair = {
                "pair_id": stem, "radial_id": radial["id"], "radial_count": len(radial["alpha_bohr_minus2"]),
                "low_g_eta_max": float(eta), "explicit_candidate_id": explicit_id,
                "complementary_candidate_id": complementary_id,
                "density_relative_difference": density_difference,
                "operator_error_difference_mev_per_atom": operator_differences,
                "condition_number_ratio_explicit_over_complementary": metric_map[explicit_id]["effective_condition_number"] / metric_map[complementary_id]["effective_condition_number"],
                "gates": gates, "status": "accepted_equivalent_pair" if all(gates.values()) else "rejected_nonequivalent_pair",
            }
            pairs.append(pair)
            pair_map[stem] = pair
    for row in metrics[1:]:
        pair_id = row["candidate_id"].rsplit("_", 1)[0]
        row["pair_equivalence"] = pair_map[pair_id]["status"] == "accepted_equivalent_pair"
        all_pass = all(row["gates"].values()) and row["pair_equivalence"]
        row["status"] = "accepted_convergence" if all_pass else "rejected_convergence"
        if not row["pair_equivalence"]:
            row["failed_gates"].append("pair_equivalence")
    accepted = [row for row in metrics[1:] if row["status"] == "accepted_convergence"]
    eligible = [row for row in accepted if row["gauge"] == config["selection"]["eligible_gauge"]]
    eligible.sort(key=lambda row: (row["basis_count"], row["effective_condition_number"], row["candidate_id"]))
    selected = eligible[0] if eligible else None
    disposition = "accepted_converged_candidate_exists" if selected else "evidence_valid_no_converged_candidate"
    require(disposition in config["allowed_scientific_dispositions"], "scientific disposition is unregistered")
    frontier = {}
    for radial in config["convergence_matrix"]["radial_levels"]:
        candidates = [row for row in eligible if row["radial_id"] == radial["id"]]
        frontier[radial["id"]] = candidates[0]["candidate_id"] if candidates else None
    return {
        "summary": {
            "schema_version": 1, "protocol_revision": config["protocol_revision"], "status": disposition,
            "evidence_valid": True, "stage": "S2", "gate": "G2a_basis_convergence", "material": "Al",
            "atom_count": 1, "reference_candidate_count": 1, "compressed_candidate_count": 48,
            "accepted_candidate_count": len(accepted), "accepted_complementary_candidate_count": len(eligible),
            "accepted_candidates": [row["candidate_id"] for row in accepted],
            "selected_candidate": selected, "minimum_passing_candidate_by_radial_level": frontier,
            "pair_count": len(pairs), "accepted_pair_count": sum(pair["status"] == "accepted_equivalent_pair" for pair in pairs),
            "new_solver_run_count": 0,
            "next_action": "new_revision_validate_selected_candidate_on_32_and_108_atom_cells_before_G2c" if selected else "new_revision_extend_registered_convergence_matrix",
            "metrics": metrics,
        },
        "basis_spectrum": {
            "schema_version": 1, "protocol_revision": config["protocol_revision"],
            **basis_context, "candidates": spectra,
        },
        "runtime": {
            "schema_version": 1, "protocol_revision": config["protocol_revision"], **runtime,
            "source": source, "previous_pilot": previous,
            "functional_stdout_sha256": functional_stdout_sha,
        },
        "metrics": metrics,
        "pairs": pairs,
    }


def convergence_tsv(metrics: list[dict]) -> bytes:
    columns = [
        "candidate_id", "status", "gauge", "radial_id", "radial_count", "low_g_eta_max",
        "half_space_vector_count", "basis_count", "effective_rank", "effective_condition_number",
        "electron_number_relative_error", "density_relative_l2", "density_min",
        "hartree_error_mev_per_atom", "external_error_mev_per_atom", "xc_error_mev_per_atom",
        "combined_error_mev_per_atom", "fixed_kedf_error_mev_per_atom", "pair_equivalence", "failed_gates",
    ]
    lines = ["\t".join(columns)]
    for row in metrics:
        values = {
            **row,
            "hartree_error_mev_per_atom": row["errors_mev_per_atom"]["hartree"],
            "external_error_mev_per_atom": row["errors_mev_per_atom"]["external"],
            "xc_error_mev_per_atom": row["errors_mev_per_atom"]["xc"],
            "combined_error_mev_per_atom": row["combined_hartree_external_xc_error_mev_per_atom"],
            "fixed_kedf_error_mev_per_atom": row["errors_mev_per_atom"]["fixed_kedf"],
            "failed_gates": ",".join(row["failed_gates"]),
        }
        lines.append("\t".join(str(values[column]) for column in columns))
    return ("\n".join(lines) + "\n").encode()


def pair_tsv(pairs: list[dict]) -> bytes:
    columns = [
        "pair_id", "status", "radial_count", "low_g_eta_max", "density_relative_difference",
        "condition_number_ratio_explicit_over_complementary", "hartree_difference_mev_per_atom",
        "external_difference_mev_per_atom", "xc_difference_mev_per_atom",
        "combined_difference_mev_per_atom", "fixed_kedf_difference_mev_per_atom",
    ]
    lines = ["\t".join(columns)]
    for pair in pairs:
        diffs = pair["operator_error_difference_mev_per_atom"]
        values = {
            **pair,
            "hartree_difference_mev_per_atom": diffs["hartree"],
            "external_difference_mev_per_atom": diffs["external"],
            "xc_difference_mev_per_atom": diffs["xc"],
            "combined_difference_mev_per_atom": diffs["combined_hartree_external_xc"],
            "fixed_kedf_difference_mev_per_atom": diffs["fixed_kedf"],
        }
        lines.append("\t".join(str(values[column]) for column in columns))
    return ("\n".join(lines) + "\n").encode()


def readme_bytes(analysis: dict) -> bytes:
    summary = analysis["summary"]
    selected = summary["selected_candidate"]
    lines = [
        "# S2/G2 Al one-atom basis convergence R2", "",
        f"Disposition: `{summary['status']}`.", "",
        "This is committed analysis-only convergence evidence over the frozen Al KS-NL density; no solver or density optimizer was run.", "",
        f"Compressed candidates: {summary['compressed_candidate_count']}; accepted: {summary['accepted_candidate_count']}; accepted complementary: {summary['accepted_complementary_candidate_count']}.", "",
    ]
    if selected:
        lines.extend([
            f"Selected candidate: `{selected['candidate_id']}` with {selected['basis_count']} functions, density L2 {selected['density_relative_l2']:.9g}, condition {selected['effective_condition_number']:.9g}, and WT error {selected['errors_mev_per_atom']['fixed_kedf']:.9g} meV/atom.", "",
        ])
    lines.extend([
        "The selection closes only the Al one-atom representation/operator convergence substep. Larger-cell continuity remains required before G2c or S3.", "",
        f"Next action: `{summary['next_action']}`.", "",
    ])
    return "\n".join(lines).encode()


def render_outputs(analysis: dict) -> dict[str, bytes]:
    return {
        "README.md": readme_bytes(analysis),
        "basis_spectrum.json": canonical_json(analysis["basis_spectrum"]),
        "convergence.tsv": convergence_tsv(analysis["metrics"]),
        "pair_equivalence.tsv": pair_tsv(analysis["pairs"]),
        "runtime.json": canonical_json(analysis["runtime"]),
        "summary.json": canonical_json(analysis["summary"]),
    }
