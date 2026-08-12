#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
from scipy.signal import resample

import analyze_s2_g2_al_localized_analysis_r2 as r2
import analyze_s2_g2_al_localized_r1 as r1
import s2_g2_al_localized_common_r1 as common

CONFIG_REL = Path("config/S2_g2_al_localized_dense_grid_r1.json")
ANALYZER_REL = Path("scripts/analyze_s2_g2_al_localized_dense_grid_r1.py")
BASE_COMMIT = "85b0a3da4b60094757264540342b2517c85436bc"


def load_config(root: Path) -> dict:
    return json.loads((root / CONFIG_REL).read_text())


def git(root: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    common.require(p.returncode == 0, f"git {' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout.strip()


def validate_config(config: dict) -> None:
    common.require(config["schema_version"] == 1, "schema differs")
    common.require(config["protocol_revision"] == "S2-G2-AL-LOCALIZED-DENSE-GRID-20260812-R1", "protocol differs")
    common.require(config["base_commit"] == BASE_COMMIT and config["new_solver_run_count"] == 0, "revision denominator differs")
    common.require(config["analysis"]["target_grids"] == [128, 144] and config["analysis"]["phase_count"] == 16, "grid denominator differs")
    common.require(config["analysis"]["near_null_subspace_dimension"] == 2, "subspace denominator differs")


def validate_sources(root: Path, config: dict) -> tuple[dict, Path, dict, dict]:
    source = config["source"]
    head = git(root, "rev-parse", "HEAD")
    common.require(subprocess.run(["git", "merge-base", "--is-ancestor", source["localized_r2_evidence_commit"], head], cwd=root).returncode == 0, "R2 evidence is not an ancestor")
    identities = {
        source["localized_r2_config_path"]: source["localized_r2_config_sha256"],
        "scripts/analyze_s2_g2_al_localized_analysis_r2.py": source["localized_r2_analyzer_sha256"],
        "scripts/analyze_s2_g2_al_localized_r1.py": source["localized_r1_analyzer_sha256"],
        "scripts/s2_g2_al_localized_common_r1.py": source["localized_r1_common_sha256"],
        source["localized_r2_summary_path"]: source["localized_r2_summary_sha256"],
        source["diagnostic_96_path"]: source["diagnostic_96_sha256"],
        source["diagnostic_128_path"]: source["diagnostic_128_sha256"],
    }
    for rel, expected in identities.items():
        path = root / rel
        common.require(path.is_file() and not path.is_symlink(), f"source missing: {rel}")
        common.require(common.sha256_path(path) == expected, f"source SHA differs: {rel}")
    r2_config = json.loads((root / source["localized_r2_config_path"]).read_text())
    r1_config = json.loads((root / r2_config["source"]["r1_config_path"]).read_text())
    run, reference, _, _ = r2.recovered_reference(root, r1_config, r2_config)
    metadata = json.loads((run / "input_metadata.json").read_text())
    counts, density = r2.parse_cube_108(run / reference["density_path"], metadata["geometry"]["cell_bohr"], metadata["geometry"]["fractional_positions"])
    common.require(counts.tolist() == [96, 96, 96], "source cube grid differs")
    historical = json.loads((root / source["localized_r2_summary_path"]).read_text())
    common.require(historical["status"] == "evidence_valid_scientific_gate_rejected", "historical status differs")
    common.require(sorted(historical["failed_gates"]) == ["eggbox_pseudoforce", "full_effective_rank"], "historical failed gates differ")
    return r1_config, run, reference, {"metadata": metadata, "density": density}


def fourier_resample_periodic(values: np.ndarray, target: int) -> np.ndarray:
    out = np.asarray(values, dtype=np.float64)
    for axis in range(3):
        out = resample(out, target, axis=axis, domain="time")
    return np.asarray(out.real, dtype=np.float64)


def normalized_spectrum(gram: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    diagonal = np.diag(gram)
    common.require(bool(np.all(diagonal > 0.0)), "atomic complement has nonpositive norm")
    scale = np.sqrt(diagonal)
    normalized = gram / scale[:, None] / scale[None, :]
    values, vectors = np.linalg.eigh(0.5 * (normalized + normalized.T))
    return values, vectors, scale


def principal_angles_degrees(left: np.ndarray, right: np.ndarray) -> list[float]:
    singular = np.linalg.svd(left.T @ right, compute_uv=False)
    singular = np.clip(singular, -1.0, 1.0)
    return sorted((np.degrees(np.arccos(singular))).tolist(), reverse=True)


def cyclic_pseudoforce(energies: list[float], phase_step_angstrom: float) -> list[float]:
    n = len(energies)
    return [abs(-(energies[(i + 1) % n] - energies[(i - 1) % n]) / (2.0 * phase_step_angstrom)) for i in range(n)]


def analyze_grid(root: Path, config: dict, r1_config: dict, reference: dict, metadata: dict, density96: np.ndarray, grid_size: int):
    cell = np.asarray(reference["cell_bohr"], dtype=float)
    positions = np.asarray(reference["fractional_positions"], dtype=float)
    density = fourier_resample_periodic(density96, grid_size)
    vectors = r1.source_low_g(root, r1_config, cell)
    paths = r2.exact_rank_position_sets(metadata, r1_config)
    counts = np.asarray([grid_size] * 3, dtype=int)
    grams, kernels, mask = common.atomic_complement_grams(cell, counts, paths, r1_config["source"]["alpha_bohr_minus2"], vectors, r1_config["projection"]["interpolation_order"])
    cutoff_rows, subspaces = [], []
    for displacement, gram in zip(config["analysis"]["rank_displacements_angstrom"], grams):
        values, eigenvectors, _ = normalized_spectrum(gram)
        subspaces.append(eigenvectors[:, : config["analysis"]["near_null_subspace_dimension"]])
        for cutoff in config["analysis"]["rank_relative_cutoffs"]:
            maximum = float(values[-1]); threshold = maximum * cutoff
            retained = values[values > threshold]
            cutoff_rows.append({
                "grid": grid_size,
                "displacement_angstrom": displacement,
                "relative_cutoff": cutoff,
                "atomic_rank": int(retained.size),
                "total_effective_rank": int(1 + 2 * len(vectors) + retained.size),
                "rank_deficiency": int(config["acceptance"]["required_total_basis_count"] - (1 + 2 * len(vectors) + retained.size)),
                "effective_condition_number": float(maximum / retained[0]),
                "minimum_retained_eigenvalue_over_cutoff": float(retained[0] / threshold),
                "bottom_eigenvalues": [float(x) for x in values[:4]],
            })
    adjacent = []
    for index in range(len(subspaces) - 1):
        angles = principal_angles_degrees(subspaces[index], subspaces[index + 1])
        adjacent.append({
            "grid": grid_size,
            "kind": "adjacent_geometry",
            "left_displacement_angstrom": config["analysis"]["rank_displacements_angstrom"][index],
            "right_displacement_angstrom": config["analysis"]["rank_displacements_angstrom"][index + 1],
            "principal_angles_degrees": angles,
            "maximum_angle_degrees": max(angles),
        })
    production_gram = grams[-1]
    fit = common.fit_localized_density_from_components(
        density, cell, positions, r1_config["source"]["alpha_bohr_minus2"], vectors, r1_config,
        production_gram, kernels, mask, relative_cutoff=config["analysis"]["production_rank_relative_cutoff"],
        eigensystem=np.linalg.eigh(production_gram), include_rank=False,
    )
    selected = np.asarray(fit["selected_density"], dtype=np.float64)
    volume = float(abs(np.linalg.det(cell))); dv = volume / density.size
    reference_electrons = float(np.sum(density, dtype=np.float64) * dv)
    selected_electrons = float(np.sum(selected, dtype=np.float64) * dv)
    density_l2 = float(np.linalg.norm(selected - density) / np.linalg.norm(density))
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        base_energies, _ = r1.evaluate_components(r1_config, cell, counts, positions, {"reference": density, "selected": selected})
    component_errors = {name: (base_energies["selected"][name] - base_energies["reference"][name]) * 1000.0 / 108.0 for name in ("hartree", "external", "xc", "combined_hartree_external_xc", "fixed_kedf", "total_with_fixed_wt")}
    phase_count = config["analysis"]["phase_count"]
    phase_rows = []
    for index in range(phase_count):
        phase = index / phase_count
        delta = np.zeros(3); delta[0] = phase / grid_size
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            energies, _ = r1.evaluate_components(
                r1_config, cell, counts, (positions + delta) % 1.0,
                {"reference": r1.shifted_density(density, phase, 0), "selected": r1.shifted_density(selected, phase, 0)},
            )
        phase_rows.append({"phase_fraction": phase, "reference": energies["reference"]["total_with_fixed_wt"] / 108.0, "selected": energies["selected"]["total_with_fixed_wt"] / 108.0})
        print(json.dumps({"grid": grid_size, "completed_phase": index + 1, "phase_count": phase_count}, sort_keys=True), flush=True)
    reference_totals = [row["reference"] for row in phase_rows]
    selected_totals = [row["selected"] for row in phase_rows]
    excess_totals = [s - r for s, r in zip(selected_totals, reference_totals)]
    grid_step = float(np.linalg.norm(cell[0]) * 0.529177210903 / grid_size)
    phase_step = grid_step / phase_count
    ref_force = cyclic_pseudoforce(reference_totals, phase_step)
    sel_force = cyclic_pseudoforce(selected_totals, phase_step)
    exc_force = cyclic_pseudoforce(excess_totals, phase_step)
    for row, a, b, c in zip(phase_rows, ref_force, sel_force, exc_force):
        row.update(reference_pseudoforce=a, selected_pseudoforce=b, projection_excess_pseudoforce=c)
    production_rows = [row for row in cutoff_rows if row["relative_cutoff"] == config["analysis"]["production_rank_relative_cutoff"]]
    metrics = {
        "grid": grid_size,
        "reference_electrons": reference_electrons,
        "selected_electrons": selected_electrons,
        "density_relative_l2": density_l2,
        "selected_density_minimum": float(selected.min()),
        "component_energy_errors_mev_per_atom": component_errors,
        "rank_minimum": min(row["total_effective_rank"] for row in production_rows),
        "rank_maximum": max(row["total_effective_rank"] for row in production_rows),
        "rank_deficiency_maximum": max(row["rank_deficiency"] for row in production_rows),
        "condition_maximum": max(row["effective_condition_number"] for row in production_rows),
        "retained_margin_minimum": min(row["minimum_retained_eigenvalue_over_cutoff"] for row in production_rows),
        "adjacent_subspace_angle_maximum_degrees": max(row["maximum_angle_degrees"] for row in adjacent),
        "reference_energy_peak_to_peak_mev_per_atom": float((max(reference_totals) - min(reference_totals)) * 1000.0),
        "selected_energy_peak_to_peak_mev_per_atom": float((max(selected_totals) - min(selected_totals)) * 1000.0),
        "projection_excess_energy_peak_to_peak_mev_per_atom": float((max(excess_totals) - min(excess_totals)) * 1000.0),
        "reference_pseudoforce_max_ev_per_angstrom": max(ref_force),
        "selected_pseudoforce_max_ev_per_angstrom": max(sel_force),
        "projection_excess_pseudoforce_max_ev_per_angstrom": max(exc_force),
    }
    return {"metrics": metrics, "rank_rows": cutoff_rows, "subspace_rows": adjacent, "subspaces": subspaces, "phase_rows": phase_rows}


def build_analysis(root: Path, config: dict) -> tuple[dict, dict[str, bytes]]:
    validate_config(config)
    r1_config, _, reference, recovered = validate_sources(root, config)
    results = [analyze_grid(root, config, r1_config, reference, recovered["metadata"], recovered["density"], grid) for grid in config["analysis"]["target_grids"]]
    cross_rows = []
    for index, displacement in enumerate(config["analysis"]["rank_displacements_angstrom"]):
        angles = principal_angles_degrees(results[0]["subspaces"][index], results[1]["subspaces"][index])
        cross_rows.append({"grid": "128_to_144", "kind": "cross_grid", "left_displacement_angstrom": displacement, "right_displacement_angstrom": displacement, "principal_angles_degrees": angles, "maximum_angle_degrees": max(angles)})
    metrics = [row["metrics"] for row in results]
    gates_cfg = config["acceptance"]
    gates = {
        "rank_floor": all(row["rank_minimum"] >= gates_cfg["minimum_effective_rank"] for row in metrics),
        "rank_deficiency": all(row["rank_deficiency_maximum"] <= gates_cfg["maximum_rank_deficiency"] for row in metrics),
        "rank_path_span": all(row["rank_maximum"] - row["rank_minimum"] <= gates_cfg["maximum_rank_span_across_path"] for row in metrics),
        "condition": all(row["condition_maximum"] < gates_cfg["effective_condition_number_strict_lt"] for row in metrics),
        "retained_margin": all(row["retained_margin_minimum"] >= gates_cfg["minimum_retained_eigenvalue_over_cutoff_min"] for row in metrics),
        "adjacent_subspace": all(row["adjacent_subspace_angle_maximum_degrees"] <= gates_cfg["adjacent_near_null_subspace_principal_angle_max_degrees"] for row in metrics),
        "cross_grid_subspace": max(row["maximum_angle_degrees"] for row in cross_rows) <= gates_cfg["cross_grid_near_null_subspace_principal_angle_max_degrees"],
        "density": all(row["density_relative_l2"] < gates_cfg["localized_density_relative_l2_strict_lt"] for row in metrics),
        "electron_number": all(abs(row["reference_electrons"] - 324.0) / 324.0 < gates_cfg["electron_relative_error_strict_lt"] and abs(row["selected_electrons"] - 324.0) / 324.0 < gates_cfg["electron_relative_error_strict_lt"] for row in metrics),
        "component_energies": all(max(abs(row["component_energy_errors_mev_per_atom"][key]) for key in ("hartree", "external", "xc")) <= gates_cfg["component_energy_abs_error_max_mev_per_atom"] for row in metrics),
        "combined_energy": all(abs(row["component_energy_errors_mev_per_atom"]["combined_hartree_external_xc"]) <= gates_cfg["combined_energy_abs_error_max_mev_per_atom"] for row in metrics),
        "fixed_wt_total": all(abs(row["component_energy_errors_mev_per_atom"]["total_with_fixed_wt"]) <= gates_cfg["fixed_kedf_total_energy_error_max_mev_per_atom"] for row in metrics),
        "eggbox_energy": all(row["selected_energy_peak_to_peak_mev_per_atom"] <= gates_cfg["eggbox_energy_peak_to_peak_max_mev_per_atom"] for row in metrics),
        "reference_pseudoforce": all(row["reference_pseudoforce_max_ev_per_angstrom"] <= gates_cfg["reference_pseudoforce_max_ev_per_angstrom"] for row in metrics),
        "projection_excess_pseudoforce": all(row["projection_excess_pseudoforce_max_ev_per_angstrom"] <= gates_cfg["projection_excess_pseudoforce_max_ev_per_angstrom"] for row in metrics),
        "selected_pseudoforce": all(row["selected_pseudoforce_max_ev_per_angstrom"] <= gates_cfg["selected_pseudoforce_max_ev_per_angstrom"] for row in metrics),
        "cross_grid_pseudoforce": abs(metrics[1]["projection_excess_pseudoforce_max_ev_per_angstrom"] - metrics[0]["projection_excess_pseudoforce_max_ev_per_angstrom"]) <= gates_cfg["cross_grid_pseudoforce_change_max_ev_per_angstrom"],
        "cross_grid_energy": abs(metrics[1]["projection_excess_energy_peak_to_peak_mev_per_atom"] - metrics[0]["projection_excess_energy_peak_to_peak_mev_per_atom"]) <= gates_cfg["cross_grid_energy_peak_to_peak_change_max_mev_per_atom"],
    }
    accepted = all(gates.values())
    status = "accepted_dense_grid_effective_subspace_pilot" if accepted else "evidence_valid_scientific_gate_rejected"
    summary = {"schema_version": 1, "protocol_revision": config["protocol_revision"], "status": status, "evidence_valid": True, "scientific_gate_accepted": accepted, "new_solver_run_count": 0, "candidate_id": "r08_eta100_complementary", "atom_count": 108, "target_grids": config["analysis"]["target_grids"], "phase_count": config["analysis"]["phase_count"], "grid_metrics": metrics, "cross_grid_subspace_angle_maximum_degrees": max(row["maximum_angle_degrees"] for row in cross_rows), "gates": gates, "failed_gates": sorted(key for key, value in gates.items() if not value), "g2_overall_accepted": False}
    grid_lines = ["grid\tdensity_relative_l2\trank_minimum\trank_maximum\tcondition_maximum\tretained_margin_minimum\tadjacent_subspace_angle_maximum_degrees\treference_energy_p2p_mev_per_atom\tselected_energy_p2p_mev_per_atom\texcess_energy_p2p_mev_per_atom\treference_pseudoforce_ev_per_angstrom\tselected_pseudoforce_ev_per_angstrom\texcess_pseudoforce_ev_per_angstrom"]
    for row in metrics:
        grid_lines.append("\t".join(str(row[key]) for key in ("grid", "density_relative_l2", "rank_minimum", "rank_maximum", "condition_maximum", "retained_margin_minimum", "adjacent_subspace_angle_maximum_degrees", "reference_energy_peak_to_peak_mev_per_atom", "selected_energy_peak_to_peak_mev_per_atom", "projection_excess_energy_peak_to_peak_mev_per_atom", "reference_pseudoforce_max_ev_per_angstrom", "selected_pseudoforce_max_ev_per_angstrom", "projection_excess_pseudoforce_max_ev_per_angstrom")))
    rank_lines = ["grid\tdisplacement_angstrom\trelative_cutoff\tatomic_rank\ttotal_effective_rank\trank_deficiency\teffective_condition_number\tminimum_retained_eigenvalue_over_cutoff\tbottom_eigenvalues"]
    for result in results:
        for row in result["rank_rows"]:
            rank_lines.append("\t".join(str(row[key]) if key != "bottom_eigenvalues" else json.dumps(row[key], separators=(",", ":")) for key in ("grid", "displacement_angstrom", "relative_cutoff", "atomic_rank", "total_effective_rank", "rank_deficiency", "effective_condition_number", "minimum_retained_eigenvalue_over_cutoff", "bottom_eigenvalues")))
    sub_lines = ["grid\tkind\tleft_displacement_angstrom\tright_displacement_angstrom\tmaximum_angle_degrees\tprincipal_angles_degrees"]
    for row in [x for result in results for x in result["subspace_rows"]] + cross_rows:
        sub_lines.append("\t".join(str(row[key]) if key != "principal_angles_degrees" else json.dumps(row[key], separators=(",", ":")) for key in ("grid", "kind", "left_displacement_angstrom", "right_displacement_angstrom", "maximum_angle_degrees", "principal_angles_degrees")))
    readme = f"""# S2/G2 Al 108-atom dense-grid and effective-subspace pilot\n\nStatus: `{status}`.\n\nThis preregistered analysis rebuilds the fixed 23-function candidate independently on 128^3 and 144^3 grids, evaluates sixteen translation phases per grid, and tracks the bottom-two normalized-Gram eigenspaces over five localized geometries. No KS solver was started.\n\n- failed gates: {', '.join(summary['failed_gates']) if summary['failed_gates'] else 'none'}\n- cross-grid maximum near-null subspace angle: {summary['cross_grid_subspace_angle_maximum_degrees']:.12g} degrees\n- G2 overall remains open.\n""".encode()
    outputs = {"README.md": readme, "grid_metrics.tsv": ("\n".join(grid_lines) + "\n").encode(), "rank_continuity.tsv": ("\n".join(rank_lines) + "\n").encode(), "subspace_continuity.tsv": ("\n".join(sub_lines) + "\n").encode(), "summary.json": common.canonical_json(summary)}
    common.require(sorted(outputs) == sorted(config["output_files"]), "output denominator differs")
    return summary, outputs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(); root = args.project_root.resolve(); config = load_config(root)
    summary, outputs = build_analysis(root, config)
    target = root / config["execution"]["analysis_root"]
    if not args.dry_run:
        common.require(not target.exists(), "analysis root exists")
        target.mkdir(parents=True)
        for name, data in outputs.items(): (target / name).write_bytes(data)
    print(json.dumps({"status": summary["status"], "failed_gates": summary["failed_gates"], "new_solver_run_count": 0, "output_written": not args.dry_run}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
