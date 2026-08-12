#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import io
import json
import subprocess
from pathlib import Path

import numpy as np

import analyze_s2_g2_al_localized_dense_grid_r1 as dense
import analyze_s2_g2_al_localized_r1 as r1
import analyze_s2_g2_al_localized_analysis_r2 as r2
import s2_g2_al_localized_common_r1 as common

CONFIG_REL = Path("config/S2_g2_al_joint_svd_r1.json")
BASE_COMMIT = "5e21d779fb9c79d08ad81ef2cf095731f2d93736"


def load_config(root: Path) -> dict:
    return json.loads((root / CONFIG_REL).read_text())


def git(root: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    common.require(p.returncode == 0, f"git {' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout.strip()


def validate_config(config: dict) -> None:
    common.require(config["schema_version"] == 1, "schema differs")
    common.require(config["protocol_revision"] == "S2-G2-AL-JOINT-SVD-20260812-R1", "protocol differs")
    common.require(config["base_commit"] == BASE_COMMIT and config["new_solver_run_count"] == 0, "revision denominator differs")
    analysis = config["analysis"]
    common.require(analysis["target_grids"] == [128, 144], "grid denominator differs")
    common.require(analysis["rank_displacements_angstrom"] == [-0.05, -0.025, 0.0, 0.025, 0.05], "geometry denominator differs")
    common.require(analysis["candidate_dropped_dimensions"] == [1, 2], "candidate denominator differs")
    common.require(analysis["joint_svd_gram_count"] == 10 and analysis["phase_count"] == 16, "analysis denominator differs")


def validate_sources(root: Path, config: dict) -> tuple[dict, dict, dict]:
    source = config["source"]
    head = git(root, "rev-parse", "HEAD")
    common.require(subprocess.run(["git", "merge-base", "--is-ancestor", source["dense_grid_evidence_commit"], head], cwd=root).returncode == 0, "dense-grid evidence is not an ancestor")
    for path_key, sha_key in (
        ("dense_grid_config_path", "dense_grid_config_sha256"),
        ("dense_grid_analyzer_path", "dense_grid_analyzer_sha256"),
        ("dense_grid_summary_path", "dense_grid_summary_sha256"),
    ):
        path = root / source[path_key]
        common.require(path.is_file() and not path.is_symlink(), f"source missing: {path}")
        common.require(common.sha256_path(path) == source[sha_key], f"source SHA differs: {path}")
    historical = json.loads((root / source["dense_grid_summary_path"]).read_text())
    common.require(historical["status"] == "evidence_valid_scientific_gate_rejected", "dense-grid status differs")
    common.require(sorted(historical["failed_gates"]) == ["adjacent_subspace", "condition", "cross_grid_subspace", "rank_path_span", "retained_margin"], "dense-grid failed gates differ")
    dense_config = json.loads((root / source["dense_grid_config_path"]).read_text())
    r1_config, _, reference, recovered = dense.validate_sources(root, dense_config)
    return r1_config, reference, recovered


def joint_projector(grams: list[np.ndarray], dropped: int) -> dict:
    common.require(len(grams) == 10 and dropped in (1, 2), "joint projector denominator differs")
    diagonal = np.mean(np.stack([np.diag(g) for g in grams], axis=0), axis=0)
    common.require(bool(np.all(diagonal > 0.0)), "joint diagonal has nonpositive entry")
    scale = np.sqrt(diagonal)
    normalized = [(g / scale[:, None] / scale[None, :] + (g / scale[:, None] / scale[None, :]).T) * 0.5 for g in grams]
    aggregate = np.mean(np.stack(normalized, axis=0), axis=0)
    values, vectors = np.linalg.eigh(aggregate)
    discarded = vectors[:, :dropped]
    kept = vectors[:, dropped:]
    transform = kept / scale[:, None]
    checksum = common.sha256_bytes(np.asarray(transform, dtype="<f8").tobytes(order="C"))
    return {"scale": scale, "normalized_grams": normalized, "joint_eigenvalues": values, "discarded": discarded, "transform": transform, "transform_sha256": checksum}


def reduced_rank_rows(config: dict, grid: int, grams: list[np.ndarray], normalized: list[np.ndarray], projector: dict, dropped: int) -> tuple[list[dict], list[dict]]:
    rows, alignment = [], []
    transform = projector["transform"]
    for displacement, gram, local in zip(config["analysis"]["rank_displacements_angstrom"], grams, normalized):
        reduced = 0.5 * (transform.T @ gram @ transform + (transform.T @ gram @ transform).T)
        values = np.linalg.eigvalsh(reduced)
        maximum = float(values[-1])
        local_values, local_vectors = np.linalg.eigh(local)
        angles = dense.principal_angles_degrees(projector["discarded"], local_vectors[:, :dropped])
        alignment.append({
            "candidate": f"joint_svd_drop{dropped}", "grid": grid, "displacement_angstrom": displacement,
            "maximum_angle_degrees": max(angles), "principal_angles_degrees": angles,
            "local_bottom_eigenvalues": [float(x) for x in local_values[:dropped]],
        })
        for cutoff in config["analysis"]["rank_relative_cutoffs"]:
            threshold = maximum * cutoff
            retained = values[values > threshold]
            rows.append({
                "candidate": f"joint_svd_drop{dropped}", "grid": grid, "displacement_angstrom": displacement,
                "relative_cutoff": cutoff, "reduced_dimension": int(values.size), "reduced_rank": int(retained.size),
                "full_reduced_rank": int(retained.size) == int(values.size),
                "effective_condition_number": float(maximum / values[0]) if values[0] > 0.0 else float("inf"),
                "minimum_eigenvalue_over_cutoff": float(values[0] / threshold),
                "bottom_eigenvalues": [float(x) for x in values[:4]],
            })
    return rows, alignment


def fit_with_transform(density: np.ndarray, cell: np.ndarray, positions: np.ndarray, alphas: list[float], vectors: list[tuple[int, int, int]], r1_config: dict, gram: np.ndarray, kernels: list[np.ndarray], mask: np.ndarray, transform: np.ndarray, cutoff: float) -> np.ndarray:
    target_fft = np.fft.fftn(density)
    rhs = np.concatenate([
        common.sample_periodic(np.fft.ifftn(target_fft * kernel * mask).real, positions, int(r1_config["projection"]["interpolation_order"]))
        for kernel in kernels
    ])
    reduced = 0.5 * (transform.T @ gram @ transform + (transform.T @ gram @ transform).T)
    values, eigenvectors = np.linalg.eigh(reduced)
    keep = values > float(values[-1]) * cutoff
    reduced_coefficients = eigenvectors[:, keep] @ ((eigenvectors[:, keep].T @ (transform.T @ rhs)) / values[keep])
    coefficients = transform @ reduced_coefficients
    nat = len(positions)
    coeff_by_atom = coefficients.reshape(len(alphas), nat).T
    selected_fft = np.zeros_like(target_fft, dtype=complex)
    selected_fft[mask == 0.0] = target_fft[mask == 0.0]
    _, mesh = common.reciprocal_g2(cell, np.asarray(density.shape, dtype=int))
    flat_k = np.column_stack([axis.reshape(-1) for axis in mesh])
    flat_out = selected_fft.reshape(-1)
    flat_mask = mask.reshape(-1).astype(bool)
    kernel_flat = np.column_stack([kernel.reshape(-1) for kernel in kernels])
    factor = density.size / float(abs(np.linalg.det(cell)))
    chunk = int(r1_config["projection"]["structure_factor_chunk_size"])
    for start in range(0, len(flat_k), chunk):
        stop = min(start + chunk, len(flat_k))
        active = flat_mask[start:stop]
        if not bool(np.any(active)):
            continue
        phase = np.exp(-2j * np.pi * (flat_k[start:stop][active] @ positions.T))
        structure = phase @ coeff_by_atom
        block = flat_out[start:stop]
        block[active] = factor * np.sum(structure * kernel_flat[start:stop][active], axis=1)
        flat_out[start:stop] = block
    return np.asarray(np.fft.ifftn(selected_fft).real, dtype=np.float64)


def evaluate_grid(config: dict, r1_config: dict, reference: dict, density96: np.ndarray, grid: int, gram_rows: dict, projector: dict, dropped: int) -> dict:
    cell = np.asarray(reference["cell_bohr"], dtype=float)
    positions = np.asarray(reference["fractional_positions"], dtype=float)
    density = dense.fourier_resample_periodic(density96, grid)
    grams, kernels, mask = gram_rows[grid]
    vectors = r1.source_low_g(Path(config["_root"]), r1_config, cell)
    selected = fit_with_transform(
        density, cell, positions, r1_config["source"]["alpha_bohr_minus2"], vectors, r1_config,
        grams[-1], kernels, mask, projector["transform"], config["analysis"]["production_rank_relative_cutoff"],
    )
    volume = float(abs(np.linalg.det(cell))); dv = volume / density.size
    reference_electrons = float(np.sum(density, dtype=np.float64) * dv)
    selected_electrons = float(np.sum(selected, dtype=np.float64) * dv)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        energies, _ = r1.evaluate_components(r1_config, cell, np.asarray([grid] * 3), positions, {"reference": density, "selected": selected})
    component_errors = {name: (energies["selected"][name] - energies["reference"][name]) * 1000.0 / 108.0 for name in ("hartree", "external", "xc", "combined_hartree_external_xc", "fixed_kedf", "total_with_fixed_wt")}
    phase_rows = []
    for index in range(config["analysis"]["phase_count"]):
        phase = index / config["analysis"]["phase_count"]
        delta = np.zeros(3); delta[0] = phase / grid
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rows, _ = r1.evaluate_components(r1_config, cell, np.asarray([grid] * 3), (positions + delta) % 1.0, {
                "reference": r1.shifted_density(density, phase, 0), "selected": r1.shifted_density(selected, phase, 0),
            })
        phase_rows.append((rows["reference"]["total_with_fixed_wt"] / 108.0, rows["selected"]["total_with_fixed_wt"] / 108.0))
        print(json.dumps({"candidate": f"drop{dropped}", "grid": grid, "completed_phase": index + 1, "phase_count": config["analysis"]["phase_count"]}, sort_keys=True), flush=True)
    reference_totals = [x[0] for x in phase_rows]; selected_totals = [x[1] for x in phase_rows]
    excess_totals = [b - a for a, b in phase_rows]
    phase_step = float(np.linalg.norm(cell[0]) * 0.529177210903 / grid / config["analysis"]["phase_count"])
    reference_force = dense.cyclic_pseudoforce(reference_totals, phase_step)
    selected_force = dense.cyclic_pseudoforce(selected_totals, phase_step)
    excess_force = dense.cyclic_pseudoforce(excess_totals, phase_step)
    return {
        "candidate": f"joint_svd_drop{dropped}", "grid": grid,
        "density_relative_l2": float(np.linalg.norm(selected - density) / np.linalg.norm(density)),
        "reference_electrons": reference_electrons, "selected_electrons": selected_electrons,
        "component_energy_errors_mev_per_atom": component_errors,
        "reference_energy_peak_to_peak_mev_per_atom": float((max(reference_totals) - min(reference_totals)) * 1000.0),
        "selected_energy_peak_to_peak_mev_per_atom": float((max(selected_totals) - min(selected_totals)) * 1000.0),
        "projection_excess_energy_peak_to_peak_mev_per_atom": float((max(excess_totals) - min(excess_totals)) * 1000.0),
        "reference_pseudoforce_max_ev_per_angstrom": max(reference_force),
        "selected_pseudoforce_max_ev_per_angstrom": max(selected_force),
        "projection_excess_pseudoforce_max_ev_per_angstrom": max(excess_force),
    }


def candidate_gates(config: dict, rank_rows: list[dict], alignment: list[dict], metrics: list[dict]) -> dict:
    a = config["acceptance"]
    gates = {
        "full_reduced_rank": all(row["full_reduced_rank"] for row in rank_rows),
        "condition": all(row["effective_condition_number"] < a["effective_condition_number_strict_lt"] for row in rank_rows),
        "retained_margin": all(row["minimum_eigenvalue_over_cutoff"] >= a["minimum_retained_eigenvalue_over_cutoff_min"] for row in rank_rows),
        "discarded_mode_alignment": all(row["maximum_angle_degrees"] <= a["discarded_mode_principal_angle_max_degrees"] for row in alignment),
        "density": all(row["density_relative_l2"] < a["localized_density_relative_l2_strict_lt"] for row in metrics),
        "electron_number": all(abs(row["reference_electrons"] - 324.0) / 324.0 < a["electron_relative_error_strict_lt"] and abs(row["selected_electrons"] - 324.0) / 324.0 < a["electron_relative_error_strict_lt"] for row in metrics),
        "component_energies": all(max(abs(row["component_energy_errors_mev_per_atom"][key]) for key in ("hartree", "external", "xc")) <= a["component_energy_abs_error_max_mev_per_atom"] for row in metrics),
        "combined_energy": all(abs(row["component_energy_errors_mev_per_atom"]["combined_hartree_external_xc"]) <= a["combined_energy_abs_error_max_mev_per_atom"] for row in metrics),
        "fixed_wt_total": all(abs(row["component_energy_errors_mev_per_atom"]["total_with_fixed_wt"]) <= a["fixed_kedf_total_energy_error_max_mev_per_atom"] for row in metrics),
        "eggbox_energy": all(row["selected_energy_peak_to_peak_mev_per_atom"] <= a["eggbox_energy_peak_to_peak_max_mev_per_atom"] for row in metrics),
        "reference_pseudoforce": all(row["reference_pseudoforce_max_ev_per_angstrom"] <= a["reference_pseudoforce_max_ev_per_angstrom"] for row in metrics),
        "projection_excess_pseudoforce": all(row["projection_excess_pseudoforce_max_ev_per_angstrom"] <= a["projection_excess_pseudoforce_max_ev_per_angstrom"] for row in metrics),
        "selected_pseudoforce": all(row["selected_pseudoforce_max_ev_per_angstrom"] <= a["selected_pseudoforce_max_ev_per_angstrom"] for row in metrics),
        "cross_grid_pseudoforce": abs(metrics[1]["projection_excess_pseudoforce_max_ev_per_angstrom"] - metrics[0]["projection_excess_pseudoforce_max_ev_per_angstrom"]) <= a["cross_grid_pseudoforce_change_max_ev_per_angstrom"],
        "cross_grid_energy": abs(metrics[1]["projection_excess_energy_peak_to_peak_mev_per_atom"] - metrics[0]["projection_excess_energy_peak_to_peak_mev_per_atom"]) <= a["cross_grid_energy_peak_to_peak_change_max_mev_per_atom"],
    }
    return gates


def build_analysis(root: Path, config: dict) -> tuple[dict, dict[str, bytes]]:
    validate_config(config)
    config = json.loads(json.dumps(config)); config["_root"] = str(root)
    r1_config, reference, recovered = validate_sources(root, config)
    cell = np.asarray(reference["cell_bohr"], dtype=float)
    vectors = r1.source_low_g(root, r1_config, cell)
    paths = r2.exact_rank_position_sets(recovered["metadata"], r1_config)
    gram_rows: dict[int, tuple] = {}
    all_grams = []
    for grid in config["analysis"]["target_grids"]:
        grams, kernels, mask = common.atomic_complement_grams(cell, np.asarray([grid] * 3), paths, r1_config["source"]["alpha_bohr_minus2"], vectors, r1_config["projection"]["interpolation_order"])
        gram_rows[grid] = (grams, kernels, mask); all_grams.extend(grams)
    common.require(len(all_grams) == config["analysis"]["joint_svd_gram_count"], "joint Gram denominator differs")
    candidates = []
    all_rank, all_alignment, all_metrics = [], [], []
    for dropped in config["analysis"]["candidate_dropped_dimensions"]:
        projector = joint_projector(all_grams, dropped)
        candidate_rank, candidate_alignment = [], []
        offset = 0
        for grid in config["analysis"]["target_grids"]:
            grams = gram_rows[grid][0]
            rows, align = reduced_rank_rows(config, grid, grams, projector["normalized_grams"][offset:offset + 5], projector, dropped)
            candidate_rank.extend(rows); candidate_alignment.extend(align); offset += 5
        metrics = [evaluate_grid(config, r1_config, reference, recovered["density"], grid, gram_rows, projector, dropped) for grid in config["analysis"]["target_grids"]]
        gates = candidate_gates(config, candidate_rank, candidate_alignment, metrics)
        accepted = all(gates.values())
        candidates.append({
            "candidate_id": f"joint_svd_drop{dropped}", "dropped_dimension": dropped,
            "retained_atomic_dimension": int(projector["transform"].shape[1]), "fixed_transform_sha256": projector["transform_sha256"],
            "joint_bottom_eigenvalues": [float(x) for x in projector["joint_eigenvalues"][:4]],
            "scientific_gate_accepted": accepted, "gates": gates,
            "failed_gates": sorted(key for key, value in gates.items() if not value),
        })
        all_rank.extend(candidate_rank); all_alignment.extend(candidate_alignment); all_metrics.extend(metrics)
    passing = [row for row in candidates if row["scientific_gate_accepted"]]
    selected = passing[0]["candidate_id"] if passing else None
    accepted = selected is not None
    status = "accepted_fixed_joint_svd_subspace_pilot" if accepted else "evidence_valid_scientific_gate_rejected"
    summary = {
        "schema_version": 1, "protocol_revision": config["protocol_revision"], "status": status,
        "evidence_valid": True, "scientific_gate_accepted": accepted, "new_solver_run_count": 0,
        "source_candidate_id": "r08_eta100_complementary", "atom_count": 108,
        "target_grids": config["analysis"]["target_grids"], "geometry_count": 5, "joint_gram_count": 10,
        "candidate_selection": config["analysis"]["candidate_selection"], "selected_candidate_id": selected,
        "candidates": candidates, "g2_overall_accepted": False,
    }
    candidate_lines = ["candidate_id\tdropped_dimension\tretained_atomic_dimension\tfixed_transform_sha256\tscientific_gate_accepted\tfailed_gates\tjoint_bottom_eigenvalues"]
    for row in candidates:
        candidate_lines.append("\t".join(str(row[key]) if key not in ("failed_gates", "joint_bottom_eigenvalues") else json.dumps(row[key], separators=(",", ":")) for key in ("candidate_id", "dropped_dimension", "retained_atomic_dimension", "fixed_transform_sha256", "scientific_gate_accepted", "failed_gates", "joint_bottom_eigenvalues")))
    grid_lines = ["candidate\tgrid\tdensity_relative_l2\ttotal_with_fixed_wt_mev_per_atom\treference_energy_p2p_mev_per_atom\tselected_energy_p2p_mev_per_atom\texcess_energy_p2p_mev_per_atom\treference_pseudoforce_ev_per_angstrom\tselected_pseudoforce_ev_per_angstrom\texcess_pseudoforce_ev_per_angstrom"]
    for row in all_metrics:
        grid_lines.append("\t".join(str(x) for x in (row["candidate"], row["grid"], row["density_relative_l2"], row["component_energy_errors_mev_per_atom"]["total_with_fixed_wt"], row["reference_energy_peak_to_peak_mev_per_atom"], row["selected_energy_peak_to_peak_mev_per_atom"], row["projection_excess_energy_peak_to_peak_mev_per_atom"], row["reference_pseudoforce_max_ev_per_angstrom"], row["selected_pseudoforce_max_ev_per_angstrom"], row["projection_excess_pseudoforce_max_ev_per_angstrom"])))
    rank_lines = ["candidate\tgrid\tdisplacement_angstrom\trelative_cutoff\treduced_dimension\treduced_rank\tfull_reduced_rank\teffective_condition_number\tminimum_eigenvalue_over_cutoff\tbottom_eigenvalues"]
    for row in all_rank:
        rank_lines.append("\t".join(str(row[key]) if key != "bottom_eigenvalues" else json.dumps(row[key], separators=(",", ":")) for key in ("candidate", "grid", "displacement_angstrom", "relative_cutoff", "reduced_dimension", "reduced_rank", "full_reduced_rank", "effective_condition_number", "minimum_eigenvalue_over_cutoff", "bottom_eigenvalues")))
    align_lines = ["candidate\tgrid\tdisplacement_angstrom\tmaximum_angle_degrees\tprincipal_angles_degrees\tlocal_bottom_eigenvalues"]
    for row in all_alignment:
        align_lines.append("\t".join(str(row[key]) if key not in ("principal_angles_degrees", "local_bottom_eigenvalues") else json.dumps(row[key], separators=(",", ":")) for key in ("candidate", "grid", "displacement_angstrom", "maximum_angle_degrees", "principal_angles_degrees", "local_bottom_eigenvalues")))
    readme = f"""# S2/G2 Al joint-SVD fixed-subspace pilot\n\nStatus: `{status}`.\n\nTen Gram matrices (128³/144³ × five geometries) define one geometry- and grid-independent projection subspace. Registered candidates drop one or two joint modes; selection is the smallest passing candidate. No KS solver was started.\n\n- selected candidate: {selected or 'none'}\n- candidate results: {', '.join(row['candidate_id'] + '=' + ('pass' if row['scientific_gate_accepted'] else 'reject') for row in candidates)}\n- G2 overall remains open.\n""".encode()
    outputs = {
        "README.md": readme,
        "candidate_metrics.tsv": ("\n".join(candidate_lines) + "\n").encode(),
        "grid_metrics.tsv": ("\n".join(grid_lines) + "\n").encode(),
        "rank_continuity.tsv": ("\n".join(rank_lines) + "\n").encode(),
        "discarded_mode_alignment.tsv": ("\n".join(align_lines) + "\n").encode(),
        "summary.json": common.canonical_json(summary),
    }
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
        for name, data in outputs.items():
            (target / name).write_bytes(data)
    print(json.dumps({"status": summary["status"], "selected_candidate_id": summary["selected_candidate_id"], "new_solver_run_count": 0, "output_written": not args.dry_run}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
