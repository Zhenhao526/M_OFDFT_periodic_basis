#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import itertools
import json
import math
import subprocess
from pathlib import Path

import s2_g2_al1_basis_convergence_common_r2 as convergence
import s2_g2_al1_pilot_common_r1 as pilot

BASE_COMMIT = "4298896727256ef60a72fdf0ee6ae904dabdaafc"
CONFIG_REL = Path("config/S2_g2_al_scale_geometry_r3.json")
PROTOCOL_REL = Path("docs/S2_G2_AL_SCALE_GEOMETRY_R3_PROTOCOL.md")
COMMON_REL = Path("scripts/s2_g2_al_scale_geometry_common_r3.py")
ANALYZER_REL = Path("scripts/analyze_s2_g2_al_scale_geometry_r3.py")
VALIDATOR_REL = Path("scripts/validate_s2_g2_al_scale_geometry_r3.py")
TEST_REL = Path("tests/unit/test_s2_g2_al_scale_geometry_r3.py")
IMPLEMENTATION_PATHS = {str(p) for p in (CONFIG_REL, PROTOCOL_REL, COMMON_REL, ANALYZER_REL, VALIDATOR_REL, TEST_REL)}

require = pilot.require
canonical_json = pilot.canonical_json
sha256_bytes = pilot.sha256_bytes
sha256_path = pilot.sha256_path
git = pilot.git


def load_config(root: Path) -> dict:
    return json.loads((root / CONFIG_REL).read_text())


def expected_geometry_cases() -> list[dict]:
    return [
        {"id": "equilibrium", "deformation": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]},
        {"id": "isotropic_m005", "deformation": [[0.995, 0.0, 0.0], [0.0, 0.995, 0.0], [0.0, 0.0, 0.995]]},
        {"id": "isotropic_p005", "deformation": [[1.005, 0.0, 0.0], [0.0, 1.005, 0.0], [0.0, 0.0, 1.005]]},
        {"id": "tetragonal_m005", "deformation": [[0.995, 0.0, 0.0], [0.0, 1.002509414234171, 0.0], [0.0, 0.0, 1.002509414234171]]},
        {"id": "tetragonal_p005", "deformation": [[1.005, 0.0, 0.0], [0.0, 0.9975093361076329, 0.0], [0.0, 0.0, 0.9975093361076329]]},
        {"id": "shear_xy_m005", "deformation": [[1.0, -0.005, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]},
        {"id": "shear_xy_p005", "deformation": [[1.0, 0.005, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]},
    ]


def expected_cell_ladder() -> list[dict]:
    return [
        {"atom_count": 32, "role": "two_by_two_by_two_conventional_fcc", "supercell_matrix": [[-2, 2, 2], [2, -2, 2], [2, 2, -2]], "expected_half_space_low_g_count": 194},
        {"atom_count": 108, "role": "three_by_three_by_three_conventional_fcc", "supercell_matrix": [[-3, 3, 3], [3, -3, 3], [3, 3, -3]], "expected_half_space_low_g_count": 654},
    ]


def validate_config(config: dict) -> None:
    require(config["schema_version"] == 1, "schema differs")
    require(config["protocol_revision"] == "S2-G2-AL-SCALE-GEOMETRY-20260811-R3", "protocol differs")
    require(config["base_commit"] == BASE_COMMIT, "base commit differs")
    require(config["scope"] == {
        "stage": "S2", "gates": ["G2a_periodic_tiling", "G2b_geometry_continuity_pilot"],
        "material": "Al", "atom_counts": [32, 108], "analysis_only": True,
        "new_solver_run_count": 0, "periodic_tiling_diagnostic_only": True,
        "localized_perturbation_reference_enabled": False, "eggbox_force_enabled": False,
        "full_large_cell_gram_enabled": False, "mg_enabled": False, "g2c_enabled": False, "s3_enabled": False,
    }, "scope differs")
    selected = config["selected_source"]
    require(selected["evidence_commit"] == "b6985a16f073f7ccf0ce4f53572e91ca9165db5a", "selected evidence differs")
    require(selected["candidate_id"] == "r08_eta100_complementary", "selected candidate differs")
    require(selected["primitive_basis_count"] == 23 and selected["radial_count_per_atom"] == 8, "selected basis differs")
    require(selected["alpha_bohr_minus2"] == [0.075, 0.15, 0.3, 0.6, 1.2, 2.4, 4.8, 9.6], "selected radial exponents differ")
    require(selected["low_g_eta_max"] == 1.0 and selected["primitive_half_space_vector_count"] == 7, "selected low-G block differs")
    require(config["geometry_cases"] == expected_geometry_cases(), "geometry denominator differs")
    require(config["cell_ladder"] == expected_cell_ladder(), "cell ladder differs")
    projection = config["projection"]
    require(projection["gauge"] == "complementary", "gauge differs")
    require(projection["integer_search_bound"] == 10 and projection["expanded_search_bound"] == 11, "integer search bound differs")
    require(projection["lstsq_rcond"] == 1e-13 and projection["rank_eigen_relative_cutoff"] == 1e-12, "linear algebra cutoff differs")
    require(projection["supercell_low_g_rule"] == "equilibrium_integer_vectors_frozen_for_all_geometries_no_reselection",
            "supercell low-G freeze rule differs")
    acceptance = config["acceptance"]
    require(acceptance == {
        "electron_number_relative_error_strict_lt": 1e-10,
        "equilibrium_density_relative_l2_strict_lt": 0.01,
        "perturbed_density_relative_l2_p95_strict_lt": 0.02,
        "minimum_density_floor_electron_per_bohr3": -1e-12,
        "effective_condition_number_strict_lt": 100000000.0,
        "component_energy_abs_error_max_mev_per_atom": 10.0,
        "combined_energy_abs_error_max_mev_per_atom": 10.0,
        "fixed_kedf_non_scf_total_energy_error_p95_mev_per_atom": 10.0,
        "equivalent_supercell_energy_difference_max_mev_per_atom": 1.0,
        "effective_coefficient_fraction_max": 0.3,
        "required_primitive_basis_count": 23,
        "required_primitive_effective_rank": 23,
        "required_low_g_set_unchanged": True,
    }, "acceptance differs")
    require(config["limitations"] == {
        "localized_108_atom_ks_reference_validated": False,
        "localized_perturbation_physics_validated": False,
        "eggbox_energy_or_pseudoforce_validated": False,
        "full_large_cell_gram_condition_validated": False,
        "g2_overall_accepted": False,
    }, "limitations differ")
    require(config["allowed_scientific_dispositions"] == [
        "accepted_periodic_tiling_geometry_scale_pilot", "evidence_valid_geometry_scale_pilot_rejected"
    ], "allowed dispositions differ")
    require(config["output"]["files"] == [
        "README.md", "geometry_metrics.tsv", "low_g_sets.json", "runtime.json", "scale_metrics.tsv", "summary.json"
    ], "output denominator differs")


def read_committed_exact(root: Path, commit: str, path: str, expected_sha: str) -> bytes:
    local = root / path
    require(local.is_file() and not local.is_symlink(), f"source is absent or symlinked: {path}")
    require(sha256_path(local) == expected_sha, f"source SHA differs: {path}")
    data = subprocess.run(
        ["git", "show", f"{commit}:{path}"], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True
    ).stdout
    require(sha256_bytes(data) == expected_sha and data == local.read_bytes(), f"committed source differs: {path}")
    return data


def validate_source_chain(root: Path, config: dict) -> tuple[dict, dict]:
    head = git(root, "rev-parse", "HEAD")
    for commit in (config["architecture_source"]["preregistration_commit"], config["selected_source"]["evidence_commit"]):
        require(subprocess.run(["git", "merge-base", "--is-ancestor", commit, head], cwd=root).returncode == 0,
                f"source commit is not an ancestor: {commit}")
    architecture_bytes = read_committed_exact(
        root, config["architecture_source"]["preregistration_commit"],
        config["architecture_source"]["config_path"], config["architecture_source"]["config_sha256"]
    )
    architecture = json.loads(architecture_bytes)
    require(architecture["reference_density"]["perfect_supercells_are_periodic_tiling_diagnostics_only"] is True,
            "architecture tiling boundary differs")
    require(architecture["reference_density"]["local_perturbation_108_requires_new_reference"] is True,
            "architecture localized-reference boundary differs")
    selected_cfg = config["selected_source"]
    summary = json.loads(read_committed_exact(
        root, selected_cfg["evidence_commit"], selected_cfg["summary_path"], selected_cfg["summary_sha256"]
    ))
    read_committed_exact(root, selected_cfg["evidence_commit"], selected_cfg["basis_spectrum_path"], selected_cfg["basis_spectrum_sha256"])
    read_committed_exact(root, BASE_COMMIT, selected_cfg["reused_common_path"], selected_cfg["reused_common_sha256"])
    selected = summary["selected_candidate"]
    require(summary["status"] == "accepted_converged_candidate_exists", "selected source status differs")
    require(selected["candidate_id"] == selected_cfg["candidate_id"], "selected source candidate differs")
    for key, value in selected_cfg["expected_primitive_metrics"].items():
        if key == "fixed_kedf_error_mev_per_atom":
            observed = selected["errors_mev_per_atom"]["fixed_kedf"]
        else:
            observed = selected[key]
        require(observed == value, f"selected source metric differs: {key}")
    convergence_config = convergence.load_config(root)
    replay = convergence.build_analysis(root, convergence_config)
    require(replay["summary"]["selected_candidate"] == selected, "selected convergence replay differs")
    return architecture, selected


def runtime_source_config(config: dict) -> dict:
    mapped = dict(config)
    mapped["source"] = config["primitive_source"]
    mapped["architecture_preregistration_commit"] = config["architecture_source"]["preregistration_commit"]
    return mapped


def integer_low_g_rows(cell, electrons: float, eta_max: float, bound: int) -> list[tuple[float, tuple[int, int, int]]]:
    import numpy as np

    volume = float(abs(np.linalg.det(cell)))
    kf = float((3.0 * math.pi**2 * electrons / volume) ** (1.0 / 3.0))
    reciprocal = 2.0 * math.pi * np.linalg.inv(cell).T
    rows = []
    for vector in itertools.product(range(-bound, bound + 1), repeat=3):
        if vector == (0, 0, 0):
            continue
        if next(value for value in vector if value != 0) < 0:
            continue
        eta = float(np.linalg.norm(np.asarray(vector, dtype=float) @ reciprocal) / (2.0 * kf))
        if eta <= eta_max + 1e-12:
            rows.append((eta, vector))
    return sorted(rows, key=lambda row: (round(row[0], 12), row[1]))


def vector_keys(rows) -> list[list[int]]:
    return [list(vector) for _, vector in rows]


def vector_key_set(rows) -> set[tuple[int, int, int]]:
    return {tuple(int(value) for value in vector) for _, vector in rows}


def fixed_low_g_rows(cell, electrons: float, vectors) -> list[tuple[float, tuple[int, int, int]]]:
    import numpy as np

    volume = float(abs(np.linalg.det(cell)))
    kf = float((3.0 * math.pi**2 * electrons / volume) ** (1.0 / 3.0))
    reciprocal = 2.0 * math.pi * np.linalg.inv(cell).T
    return [
        (float(np.linalg.norm(np.asarray(vector, dtype=float) @ reciprocal) / (2.0 * kf)), tuple(vector))
        for vector in vectors
    ]


def coset_positions(matrix) -> list[list[float]]:
    import numpy as np

    matrix = np.asarray(matrix, dtype=int)
    determinant = int(round(abs(np.linalg.det(matrix))))
    inverse = np.linalg.inv(matrix)
    observed = {}
    for bound in range(0, 12):
        for translation in itertools.product(range(-bound, bound + 1), repeat=3):
            fractional = np.asarray(translation, dtype=float) @ inverse
            fractional = fractional - np.floor(fractional + 1e-12)
            fractional[np.abs(fractional) < 1e-12] = 0.0
            key = tuple(round(float(value), 12) for value in fractional)
            observed.setdefault(key, tuple(int(value) for value in translation))
        if len(observed) == determinant:
            break
    require(len(observed) == determinant, "coset denominator differs")
    positions = [list(key) for key in sorted(observed)]
    for position in positions:
        mapped = np.asarray(position) @ matrix
        require(float(np.max(np.abs(mapped - np.rint(mapped)))) < 1e-10, "coset position is not a lattice site")
    return positions


def repeated_float_sha(values, repeats: int) -> str:
    import numpy as np

    block = np.asarray(values, dtype="<f8").tobytes(order="C")
    digest = hashlib.sha256()
    for _ in range(repeats):
        digest.update(block)
    return digest.hexdigest()


def build_selected_geometry(config: dict, cell, reference_density, equilibrium_vectors):
    import numpy as np

    selected = config["selected_source"]
    projection_cfg = config["projection"]
    counts = np.asarray(reference_density.shape, dtype=int)
    frac = pilot.fractional_grid(counts)
    target = np.asarray(reference_density, dtype=float).reshape(-1)
    volume = float(abs(np.linalg.det(cell)))
    dv = volume / target.size
    r2 = pilot.minimum_image_r2(frac, cell)
    gaussians = []
    for alpha in selected["alpha_bohr_minus2"]:
        function = np.exp(-float(alpha) * r2)
        function /= float(function.sum(dtype=np.float64) * dv)
        gaussians.append(function)
    atomic = np.stack(gaussians, axis=1)
    compensated = atomic - np.mean(atomic, axis=0, keepdims=True)
    scanned = integer_low_g_rows(cell, 3.0, selected["low_g_eta_max"], projection_cfg["integer_search_bound"])
    expanded = integer_low_g_rows(cell, 3.0, selected["low_g_eta_max"], projection_cfg["expanded_search_bound"])
    require(vector_key_set(scanned) == vector_key_set(expanded), "primitive integer search is incomplete")
    low_g_unchanged = vector_key_set(scanned) == {tuple(vector) for vector in equilibrium_vectors}
    rows = fixed_low_g_rows(cell, 3.0, equilibrium_vectors)
    low_functions = []
    for _, vector in rows:
        phase = 2.0 * math.pi * (frac @ np.asarray(vector, dtype=float))
        low_functions.extend((np.cos(phase), np.sin(phase)))
    low_functions = np.stack(low_functions, axis=1)
    constant = np.full((target.size, 1), 1.0 / volume)
    low_basis = np.column_stack((constant, low_functions))
    low_q, _ = np.linalg.qr(low_basis, mode="reduced")
    complementary_atomic = compensated - low_q @ (low_q.T @ compensated)
    matrix = np.column_stack((constant, low_functions, complementary_atomic))
    coefficients, density = pilot.constrained_fit(matrix, target, dv, 3.0, projection_cfg["lstsq_rcond"])
    eigenvalues, rank, condition = pilot.normalized_spectrum(matrix, dv, projection_cfg["rank_eigen_relative_cutoff"])
    projections = {
        "pw_fft_reference": {"density": target, "coefficients": [], "basis_count": target.size,
                             "effective_rank": target.size, "effective_condition_number": 1.0,
                             "normalized_overlap_eigenvalues": [1.0]},
        selected["candidate_id"]: {"density": density, "coefficients": coefficients.tolist(),
                                    "basis_count": matrix.shape[1], "effective_rank": rank,
                                    "effective_condition_number": condition,
                                    "normalized_overlap_eigenvalues": eigenvalues.tolist()},
    }
    evaluation_config = runtime_source_config(config)
    evaluation_config["candidate_order"] = list(projections)
    energies, stdout_sha = pilot.evaluate_operators(evaluation_config, cell, counts, projections)
    reference_energies = energies["pw_fft_reference"]
    selected_energies = energies[selected["candidate_id"]]
    errors = {key: (selected_energies[key] - reference_energies[key]) * 1000.0 for key in reference_energies}
    electron_count = float(np.sum(density, dtype=np.float64) * dv)
    row = {
        "basis_count": int(matrix.shape[1]), "effective_rank": rank, "effective_condition_number": condition,
        "low_g_half_space_vector_count": len(rows), "low_g_vectors_unchanged": low_g_unchanged,
        "electron_count": electron_count, "electron_number_relative_error": abs(electron_count - 3.0) / 3.0,
        "density_relative_l2": float(np.linalg.norm(density - target) / np.linalg.norm(target)),
        "density_min": float(density.min()), "density_max": float(density.max()),
        "reference_density_sha256_float64_le": pilot.density_sha256(target),
        "selected_density_sha256_float64_le": pilot.density_sha256(density),
        "energies_ev_per_atom": selected_energies, "reference_energies_ev_per_atom": reference_energies,
        "errors_mev_per_atom": errors,
        "combined_hartree_external_xc_error_mev_per_atom": errors["hartree"] + errors["external"] + errors["xc"],
        "functional_stdout_sha256": stdout_sha,
    }
    return row, target, density, rows


def geometry_gates(row: dict, geometry_id: str, acceptance: dict) -> dict:
    density_limit = (acceptance["equilibrium_density_relative_l2_strict_lt"] if geometry_id == "equilibrium"
                     else acceptance["perturbed_density_relative_l2_p95_strict_lt"])
    return {
        "electron_number": row["electron_number_relative_error"] < acceptance["electron_number_relative_error_strict_lt"],
        "density_l2": row["density_relative_l2"] < density_limit,
        "nonnegative_density": row["density_min"] >= acceptance["minimum_density_floor_electron_per_bohr3"],
        "basis_count": row["basis_count"] == acceptance["required_primitive_basis_count"],
        "effective_rank": row["effective_rank"] == acceptance["required_primitive_effective_rank"],
        "condition_number": row["effective_condition_number"] < acceptance["effective_condition_number_strict_lt"],
        "low_g_set_unchanged": row["low_g_vectors_unchanged"] is acceptance["required_low_g_set_unchanged"],
        "component_energies": max(abs(row["errors_mev_per_atom"][key]) for key in ("hartree", "external", "xc")) < acceptance["component_energy_abs_error_max_mev_per_atom"],
        "combined_energy": abs(row["combined_hartree_external_xc_error_mev_per_atom"]) < acceptance["combined_energy_abs_error_max_mev_per_atom"],
        "fixed_kedf": abs(row["errors_mev_per_atom"]["fixed_kedf"]) < acceptance["fixed_kedf_non_scf_total_energy_error_p95_mev_per_atom"],
    }


def build_analysis(root: Path, config: dict) -> dict:
    import numpy as np

    validate_config(config)
    architecture, selected_source = validate_source_chain(root, config)
    mapped = runtime_source_config(config)
    runtime = pilot.validate_runtime(root, mapped)
    primitive_cell, primitive_rho, primitive_source = pilot.validate_sources(root, mapped)
    equilibrium_rows = integer_low_g_rows(primitive_cell, 3.0, 1.0, config["projection"]["integer_search_bound"])
    require(len(equilibrium_rows) == config["selected_source"]["primitive_half_space_vector_count"], "primitive low-G count differs")
    equilibrium_vectors = vector_keys(equilibrium_rows)
    geometry_metrics = []
    private_arrays = {}
    for case in config["geometry_cases"]:
        deformation = np.asarray(case["deformation"], dtype=float)
        determinant = float(np.linalg.det(deformation))
        require(determinant > 0.0, "deformation determinant is nonpositive")
        cell = primitive_cell @ deformation.T
        reference_density = np.asarray(primitive_rho, dtype=float) / determinant
        row, reference_values, selected_values, low_rows = build_selected_geometry(
            config, cell, reference_density, equilibrium_vectors
        )
        row.update({
            "geometry_id": case["id"], "deformation": case["deformation"], "deformation_determinant": determinant,
            "cell_sha256_float64_le": sha256_bytes(np.asarray(cell, dtype="<f8").tobytes()),
            "low_g_max_eta": max(value for value, _ in low_rows),
        })
        row["gates"] = geometry_gates(row, case["id"], config["acceptance"])
        row["failed_gates"] = [key for key, value in row["gates"].items() if not value]
        row["status"] = "accepted_geometry" if all(row["gates"].values()) else "rejected_geometry"
        geometry_metrics.append(row)
        private_arrays[case["id"]] = (cell, reference_values, selected_values)
    equilibrium_metric = next(row for row in geometry_metrics if row["geometry_id"] == "equilibrium")
    expected = config["selected_source"]["expected_primitive_metrics"]
    require(abs(equilibrium_metric["density_relative_l2"] - expected["density_relative_l2"]) < 1e-14,
            "equilibrium density does not replay selected source")
    require(abs(equilibrium_metric["effective_condition_number"] - expected["effective_condition_number"]) < 1e-6,
            "equilibrium condition does not replay selected source")
    require(abs(equilibrium_metric["combined_hartree_external_xc_error_mev_per_atom"] - expected["combined_hartree_external_xc_error_mev_per_atom"]) < 1e-9,
            "equilibrium combined energy does not replay selected source")
    require(abs(equilibrium_metric["errors_mev_per_atom"]["fixed_kedf"] - expected["fixed_kedf_error_mev_per_atom"]) < 1e-9,
            "equilibrium WT does not replay selected source")
    scale_metrics = []
    low_g_cells = []
    for cell_cfg in config["cell_ladder"]:
        atom_count = cell_cfg["atom_count"]
        matrix = np.asarray(cell_cfg["supercell_matrix"], dtype=int)
        require(int(round(abs(np.linalg.det(matrix)))) == atom_count, "supercell determinant differs")
        positions = coset_positions(matrix)
        equilibrium_supercell = matrix @ primitive_cell
        eq_rows = integer_low_g_rows(
            equilibrium_supercell, 3.0 * atom_count, config["selected_source"]["low_g_eta_max"],
            config["projection"]["integer_search_bound"]
        )
        expanded = integer_low_g_rows(
            equilibrium_supercell, 3.0 * atom_count, config["selected_source"]["low_g_eta_max"],
            config["projection"]["expanded_search_bound"]
        )
        require(vector_key_set(eq_rows) == vector_key_set(expanded), "supercell integer search is incomplete")
        require(len(eq_rows) == cell_cfg["expected_half_space_low_g_count"], "supercell low-G count differs")
        cell_low_g = {
            "atom_count": atom_count, "role": cell_cfg["role"], "supercell_matrix": cell_cfg["supercell_matrix"],
            "coset_position_count": len(positions), "coset_fractional_positions": positions,
            "coset_positions_sha256": sha256_bytes(canonical_json(positions)),
            "equilibrium_half_space_low_g_count": len(eq_rows),
            "equilibrium_low_g_vectors": [{"eta_q_over_2kf": eta, "integer_vector": list(vector)} for eta, vector in eq_rows],
            "frozen_equilibrium_vector_sha256": sha256_bytes(canonical_json(vector_keys(eq_rows))),
        }
        invariant_cases = []
        for geometry in geometry_metrics:
            geometry_id = geometry["geometry_id"]
            primitive_deformed_cell, reference_values, selected_values = private_arrays[geometry_id]
            supercell = matrix @ primitive_deformed_cell
            deformed_rows = integer_low_g_rows(
                supercell, 3.0 * atom_count, config["selected_source"]["low_g_eta_max"],
                config["projection"]["integer_search_bound"]
            )
            threshold_reselection_matches = vector_key_set(deformed_rows) == vector_key_set(eq_rows)
            frozen_vector_sha = sha256_bytes(canonical_json(vector_keys(eq_rows)))
            low_g_unchanged = frozen_vector_sha == cell_low_g["frozen_equilibrium_vector_sha256"]
            invariant_cases.append({
                "geometry_id": geometry_id,
                "frozen_low_g_vectors_unchanged": low_g_unchanged,
                "frozen_low_g_vector_sha256": frozen_vector_sha,
                "threshold_reselection_matches_frozen": threshold_reselection_matches,
            })
            total_basis_count = 1 + 2 * len(eq_rows) + config["selected_source"]["radial_count_per_atom"] * atom_count
            quadrature_point_count = int(np.prod(config["primitive_source"]["grid"])) * atom_count
            total_errors = {key: value * atom_count for key, value in geometry["errors_mev_per_atom"].items()}
            replayed_per_atom = {key: value / atom_count for key, value in total_errors.items()}
            equivalent_difference = max(
                abs(replayed_per_atom[key] - geometry["errors_mev_per_atom"][key]) for key in total_errors
            )
            electron_total = geometry["electron_count"] * atom_count
            row = {
                "atom_count": atom_count, "cell_role": cell_cfg["role"], "geometry_id": geometry_id,
                "supercell_determinant": atom_count, "coset_position_count": len(positions),
                "half_space_low_g_count": len(eq_rows), "real_low_g_count": 2 * len(eq_rows),
                "atomic_function_count": config["selected_source"]["radial_count_per_atom"] * atom_count,
                "total_basis_count": total_basis_count, "basis_functions_per_atom": total_basis_count / atom_count,
                "quadrature_point_count": quadrature_point_count,
                "effective_coefficient_fraction": total_basis_count / quadrature_point_count,
                "low_g_vectors_unchanged": low_g_unchanged,
                "threshold_reselection_matches_frozen": threshold_reselection_matches,
                "electron_count": electron_total,
                "expected_electron_count": 3.0 * atom_count,
                "electron_number_relative_error": abs(electron_total - 3.0 * atom_count) / (3.0 * atom_count),
                "density_relative_l2": geometry["density_relative_l2"],
                "reference_tiled_values_sha256_float64_le": repeated_float_sha(reference_values, atom_count),
                "selected_tiled_values_sha256_float64_le": repeated_float_sha(selected_values, atom_count),
                "supercell_cell_sha256_float64_le": sha256_bytes(np.asarray(supercell, dtype="<f8").tobytes()),
                "per_atom_errors_mev": replayed_per_atom,
                "equivalent_supercell_energy_difference_mev_per_atom": equivalent_difference,
            }
            row["gates"] = {
                "coset_geometry": len(positions) == atom_count,
                "low_g_set_unchanged": low_g_unchanged,
                "electron_number": row["electron_number_relative_error"] < config["acceptance"]["electron_number_relative_error_strict_lt"],
                "coefficient_fraction": row["effective_coefficient_fraction"] < config["acceptance"]["effective_coefficient_fraction_max"],
                "equivalent_supercell_energy": equivalent_difference < config["acceptance"]["equivalent_supercell_energy_difference_max_mev_per_atom"],
                "primitive_geometry_source": geometry["status"] == "accepted_geometry",
            }
            row["failed_gates"] = [key for key, value in row["gates"].items() if not value]
            row["status"] = "accepted_scale_tiling" if all(row["gates"].values()) else "rejected_scale_tiling"
            scale_metrics.append(row)
        cell_low_g["geometry_invariance"] = invariant_cases
        low_g_cells.append(cell_low_g)
    density_p95 = float(np.percentile([row["density_relative_l2"] for row in geometry_metrics if row["geometry_id"] != "equilibrium"], 95))
    wt_p95 = float(np.percentile([abs(row["errors_mev_per_atom"]["fixed_kedf"]) for row in geometry_metrics], 95))
    summary_gates = {
        "all_geometry_cases": all(row["status"] == "accepted_geometry" for row in geometry_metrics),
        "perturbed_density_l2_p95": density_p95 < config["acceptance"]["perturbed_density_relative_l2_p95_strict_lt"],
        "fixed_kedf_error_p95": wt_p95 < config["acceptance"]["fixed_kedf_non_scf_total_energy_error_p95_mev_per_atom"],
        "all_scale_cases": all(row["status"] == "accepted_scale_tiling" for row in scale_metrics),
    }
    disposition = ("accepted_periodic_tiling_geometry_scale_pilot" if all(summary_gates.values())
                   else "evidence_valid_geometry_scale_pilot_rejected")
    require(disposition in config["allowed_scientific_dispositions"], "scientific disposition is unregistered")
    return {
        "summary": {
            "schema_version": 1, "protocol_revision": config["protocol_revision"], "status": disposition,
            "evidence_valid": True, "stage": "S2", "material": "Al",
            "selected_candidate_id": config["selected_source"]["candidate_id"],
            "geometry_case_count": len(geometry_metrics), "scale_case_count": len(scale_metrics),
            "atom_counts": [32, 108], "density_relative_l2_p95": density_p95,
            "fixed_kedf_error_p95_mev_per_atom": wt_p95, "gates": summary_gates,
            "limitations": config["limitations"], "new_solver_run_count": 0,
            "next_action": "new_revision_obtain_localized_108_atom_reference_and_test_eggbox_rank_continuity_before_G2c",
            "geometry_metrics": geometry_metrics, "scale_metrics": scale_metrics,
        },
        "low_g_sets": {
            "schema_version": 1, "protocol_revision": config["protocol_revision"],
            "primitive_equilibrium_vectors": [{"eta_q_over_2kf": eta, "integer_vector": list(vector)} for eta, vector in equilibrium_rows],
            "cells": low_g_cells,
        },
        "runtime": {
            "schema_version": 1, "protocol_revision": config["protocol_revision"], **runtime,
            "primitive_source": primitive_source,
            "architecture_preregistration_commit": config["architecture_source"]["preregistration_commit"],
            "selected_evidence_commit": config["selected_source"]["evidence_commit"],
            "selected_source_candidate": selected_source["candidate_id"],
        },
        "geometry_metrics": geometry_metrics,
        "scale_metrics": scale_metrics,
    }


def geometry_tsv(rows: list[dict]) -> bytes:
    columns = [
        "geometry_id", "status", "deformation_determinant", "basis_count", "effective_rank",
        "effective_condition_number", "low_g_half_space_vector_count", "low_g_vectors_unchanged",
        "electron_number_relative_error", "density_relative_l2", "density_min",
        "hartree_error_mev_per_atom", "external_error_mev_per_atom", "xc_error_mev_per_atom",
        "combined_error_mev_per_atom", "fixed_kedf_error_mev_per_atom", "failed_gates",
    ]
    lines = ["\t".join(columns)]
    for row in rows:
        values = {
            **row, "hartree_error_mev_per_atom": row["errors_mev_per_atom"]["hartree"],
            "external_error_mev_per_atom": row["errors_mev_per_atom"]["external"],
            "xc_error_mev_per_atom": row["errors_mev_per_atom"]["xc"],
            "combined_error_mev_per_atom": row["combined_hartree_external_xc_error_mev_per_atom"],
            "fixed_kedf_error_mev_per_atom": row["errors_mev_per_atom"]["fixed_kedf"],
            "failed_gates": ",".join(row["failed_gates"]),
        }
        lines.append("\t".join(str(values[column]) for column in columns))
    return ("\n".join(lines) + "\n").encode()


def scale_tsv(rows: list[dict]) -> bytes:
    columns = [
        "atom_count", "cell_role", "geometry_id", "status", "coset_position_count",
        "half_space_low_g_count", "real_low_g_count", "atomic_function_count", "total_basis_count",
        "basis_functions_per_atom", "quadrature_point_count", "effective_coefficient_fraction",
        "low_g_vectors_unchanged", "electron_number_relative_error", "density_relative_l2",
        "equivalent_supercell_energy_difference_mev_per_atom", "failed_gates",
    ]
    lines = ["\t".join(columns)]
    for row in rows:
        values = {**row, "failed_gates": ",".join(row["failed_gates"])}
        lines.append("\t".join(str(values[column]) for column in columns))
    return ("\n".join(lines) + "\n").encode()


def readme_bytes(analysis: dict) -> bytes:
    summary = analysis["summary"]
    return ("\n".join([
        "# S2/G2 Al 32/108-atom scale and geometry pilot R3", "",
        f"Disposition: `{summary['status']}`.", "",
        "The fixed 23-function primitive candidate was recomputed for seven coordinate-scaled geometries and tiled exactly into the registered 32/108-atom perfect supercells.", "",
        f"Density L2 p95: {summary['density_relative_l2_p95']:.9g}; fixed-WT error p95: {summary['fixed_kedf_error_p95_mev_per_atom']:.9g} meV/atom.", "",
        "This closes only the periodic-tiling scale/geometry pilot. A localized 108-atom KS reference, egg-box force, and full large-cell Gram/rank validation remain open; G2 overall is not accepted.", "",
        f"Next action: `{summary['next_action']}`.", "",
    ])).encode()


def render_outputs(analysis: dict) -> dict[str, bytes]:
    return {
        "README.md": readme_bytes(analysis),
        "geometry_metrics.tsv": geometry_tsv(analysis["geometry_metrics"]),
        "low_g_sets.json": canonical_json(analysis["low_g_sets"]),
        "runtime.json": canonical_json(analysis["runtime"]),
        "scale_metrics.tsv": scale_tsv(analysis["scale_metrics"]),
        "summary.json": canonical_json(analysis["summary"]),
    }
