#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import os
import subprocess
import tempfile
from pathlib import Path

from s2_g2_al_localized_common_r1 import (
    ANALYZER_REL, CONFIG_REL, atomic_complement_grams, canonical_json,
    file_identity, fit_localized_density_from_components, frozen_low_g_vectors,
    git, load_config, normalized_rank, parse_reference_run, pilot, require,
    sha256_bytes, sha256_path, validate_config,
)


def validate_reference_state(root: Path, config: dict) -> tuple[Path, dict, dict]:
    state = Path(config["execution"]["state_root"])
    experiment_id = config["reference"]["experiment_id"]
    run = state / "runs" / experiment_id
    require(state.is_dir() and run.is_dir(), "reference state is absent")
    require(not any(path.is_symlink() for path in state.rglob("*")), "reference state contains symlink")
    session = json.loads((state / "session.json").read_text())
    terminal = json.loads((state / "terminal.json").read_text())
    accepted = json.loads((state / "accepted" / f"{experiment_id}.json").read_text())
    attempt = json.loads((state / "attempts" / f"{experiment_id}.json").read_text())
    runner_return = json.loads((run / "runner_return.json").read_text())
    require(session["status"] == "accepted_terminal" and session["accepted_count"] == 1, "session differs")
    require(terminal["status"] == "accepted", "terminal differs")
    require([terminal[key] for key in ("attempted", "accepted", "failed", "retried", "runner_return_code")] == [1, 1, 0, 0, 0], "terminal denominator differs")
    require(terminal["experiment_ids"] == [experiment_id], "terminal IDs differ")
    require(attempt["status"] == "formal_attempt_started" and accepted["status"] == "accepted_reference", "marker status differs")
    require(runner_return["return_code"] == 0 and runner_return["timed_out"] is False, "runner return differs")
    require(accepted["result_sha256"] == sha256_path(run / "result.json") == terminal["result_sha256"], "result chain differs")
    require(terminal["session_sha256"] == sha256_path(state / "session.json"), "session chain differs")
    stored = json.loads((run / "result.json").read_text())
    replayed = parse_reference_run(run, config)
    require(canonical_json(stored) == canonical_json(replayed), "reference result replay differs")
    return run, stored, terminal


def source_low_g(root: Path, config: dict, cell_bohr) -> list[tuple[int, int, int]]:
    source = root / config["source"]["scale_geometry_low_g_path"]
    require(sha256_path(source) == config["source"]["scale_geometry_low_g_sha256"], "low-G source SHA differs")
    payload = json.loads(source.read_text())
    cells = [row for row in payload["cells"] if row["atom_count"] == 108]
    require(len(cells) == 1, "108-atom low-G source denominator differs")
    frozen = [tuple(row["integer_vector"]) for row in cells[0]["equilibrium_low_g_vectors"]]
    require(len(frozen) == config["source"]["frozen_108_half_space_low_g_count"], "frozen low-G count differs")
    independent = frozen_low_g_vectors(
        cell_bohr, config["reference"]["expected_electrons"], config["source"]["low_g_eta_max"],
        config["projection"]["integer_search_bound"],
    )
    require(independent == frozen, "independently rebuilt low-G set differs")
    return frozen


def shifted_density(density, phase_fraction: float, axis: int):
    import numpy as np
    counts = np.asarray(density.shape, dtype=int)
    axes = [np.fft.fftfreq(int(n)) * int(n) for n in counts]
    mesh = np.meshgrid(*axes, indexing="ij")
    phase = np.exp(-2j * math.pi * mesh[axis] * phase_fraction / counts[axis])
    return np.fft.ifftn(np.fft.fftn(density) * phase).real


def evaluate_components(config: dict, cell_bohr, counts, positions_frac, densities: dict[str, object]) -> tuple[dict, str]:
    import numpy as np
    from ase import Atoms
    from dftpy.constants import ENERGY_CONV, Units
    from dftpy.field import DirectField
    from dftpy.functional import Functional, LocalPseudo
    from dftpy.grid import DirectGrid
    from dftpy.ions import Ions

    cell = np.asarray(cell_bohr, dtype=float)
    positions = np.asarray(positions_frac, dtype=float) @ cell
    atoms = Atoms(symbols=["Al"] * len(positions), positions=positions * Units.Bohr, cell=cell * Units.Bohr, pbc=True)
    ions = Ions.from_ase(atoms)
    grid = DirectGrid(lattice=ions.cell, nr=np.asarray(counts, dtype=int), full=False)
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
        functionals = {
            "hartree": Functional(type="HARTREE"),
            "external": LocalPseudo(grid=grid, ions=ions, PP_list={"Al": config["source"]["pseudopotential_path"]}),
            "xc": Functional(type="XC", name="PBE"),
            "fixed_kedf": Functional(type="KEDF", name="WT", alpha=5.0 / 6.0, beta=5.0 / 6.0, rho0=None),
        }
        conversion = float(ENERGY_CONV["Hartree"]["eV"])
        results = {}
        for label, values in densities.items():
            field = DirectField(grid=grid)
            field[:] = np.asarray(values, dtype=float).reshape(tuple(int(x) for x in counts))
            row = {name: float(functional.compute(field, calcType={"E"}).energy) * conversion for name, functional in functionals.items()}
            row["combined_hartree_external_xc"] = row["hartree"] + row["external"] + row["xc"]
            row["total_with_fixed_wt"] = row["combined_hartree_external_xc"] + row["fixed_kedf"]
            results[label] = row
    return results, sha256_bytes(captured.getvalue().encode())


def position_sets(reference: dict, config: dict):
    import numpy as np
    base = np.asarray(reference["fractional_positions"], dtype=float)
    undisplaced = np.asarray(json.loads((Path(config["execution"]["state_root"]) / "runs" / config["reference"]["experiment_id"] / "input_metadata.json").read_text())["geometry"]["undisplaced_fractional_positions"], dtype=float)
    cell = np.asarray(reference["cell_bohr"], dtype=float)
    atom = int(reference["localized_atom_index_zero_based"])
    rows = []
    for displacement in config["projection"]["localized_rank_displacements_angstrom"]:
        positions = undisplaced.copy()
        delta_bohr = np.asarray([displacement, 0.0, 0.0]) / 0.529177210903
        positions[atom] = (positions[atom] + delta_bohr @ np.linalg.inv(cell)) % 1.0
        rows.append(positions)
    require(bool(np.allclose(rows[-1], base, atol=5e-14, rtol=0.0)), "rank path reference endpoint differs")
    return rows


def compress_zstd(path: Path) -> bytes:
    completed = subprocess.run(["zstd", "-19", "-q", "--stdout", str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    require(completed.returncode == 0, "zstd compression failed")
    return completed.stdout


def build_analysis(root: Path, config: dict) -> tuple[dict, dict[str, bytes]]:
    import numpy as np
    validate_config(config)
    run, reference, terminal = validate_reference_state(root, config)
    counts, density = pilot.parse_cube(run / reference["density_path"], np.asarray(reference["cell_bohr"], dtype=float))
    cell = np.asarray(reference["cell_bohr"], dtype=float)
    positions = np.asarray(reference["fractional_positions"], dtype=float)
    vectors = source_low_g(root, config, cell)
    alphas = config["source"]["alpha_bohr_minus2"]
    paths = position_sets(reference, config)
    grams, kernels, mask = atomic_complement_grams(cell, counts, paths, alphas, vectors, config["projection"]["interpolation_order"])
    rank_rows = []
    rank_by_path = []
    for displacement, gram in zip(config["projection"]["localized_rank_displacements_angstrom"], grams):
        row_cutoffs = []
        for cutoff in config["projection"]["rank_relative_cutoffs"]:
            rank = normalized_rank(gram, cutoff)
            total_rank = 1 + 2 * len(vectors) + rank["atomic_rank"]
            row_cutoffs.append({
                "relative_cutoff": cutoff, "atomic_rank": rank["atomic_rank"], "total_effective_rank": total_rank,
                "effective_condition_number": rank["effective_condition_number"],
                "minimum_retained_eigenvalue_over_cutoff": rank["minimum_retained_over_cutoff"],
            })
        rank_by_path.append(row_cutoffs)
        production = row_cutoffs[-1]
        rank_rows.append({"displacement_angstrom": displacement, **production})
    production_gram = grams[-1]
    eigensystem = np.linalg.eigh(production_gram)
    fits = {}
    for cutoff in config["projection"]["rank_relative_cutoffs"]:
        fits[str(cutoff)] = fit_localized_density_from_components(
            density, cell, positions, alphas, vectors, config, production_gram, kernels, mask,
            relative_cutoff=cutoff, eigensystem=eigensystem, include_rank=False,
        )
    selected = np.asarray(fits[str(config["projection"]["production_rank_relative_cutoff"])]["selected_density"], dtype=float)
    volume = float(abs(np.linalg.det(cell)))
    dv = volume / density.size
    expected = config["reference"]["expected_electrons"]
    selected_electrons = float(np.sum(selected, dtype=np.float64) * dv)
    density_l2 = float(np.linalg.norm(selected - density) / np.linalg.norm(density))
    base_energies, base_stdout_sha = evaluate_components(config, cell, counts, positions, {"reference": density, "selected": selected})
    component_errors = {name: (base_energies["selected"][name] - base_energies["reference"][name]) * 1000.0 / 108.0 for name in ("hartree", "external", "xc", "combined_hartree_external_xc", "fixed_kedf", "total_with_fixed_wt")}
    threshold_totals = []
    for cutoff in config["projection"]["rank_relative_cutoffs"]:
        key = str(cutoff)
        energies, _ = evaluate_components(config, cell, counts, positions, {key: fits[key]["selected_density"]})
        threshold_totals.append({"relative_cutoff": cutoff, "total_energy_ev_per_atom": energies[key]["total_with_fixed_wt"] / 108.0})
    threshold_change = max(abs(threshold_totals[i + 1]["total_energy_ev_per_atom"] - threshold_totals[i]["total_energy_ev_per_atom"]) * 1000.0 for i in range(len(threshold_totals) - 1))
    phases = config["projection"]["eggbox_phase_fractions_of_grid_step"]
    axis = config["projection"]["eggbox_axis"]
    eggbox = []
    for phase in phases:
        delta = np.zeros(3); delta[axis] = phase / counts[axis]
        shifted_positions = (positions + delta) % 1.0
        ref_shift = shifted_density(density, phase, axis)
        selected_shift = shifted_density(selected, phase, axis)
        energies, stdout_sha = evaluate_components(config, cell, counts, shifted_positions, {"reference": ref_shift, "selected": selected_shift})
        eggbox.append({"phase_fraction": phase, "translation_fractional": delta.tolist(), "reference": energies["reference"], "selected": energies["selected"], "functional_stdout_sha256": stdout_sha})
    selected_totals = [row["selected"]["total_with_fixed_wt"] / 108.0 for row in eggbox]
    reference_totals = [row["reference"]["total_with_fixed_wt"] / 108.0 for row in eggbox]
    grid_step_angstrom = float(np.linalg.norm(cell[axis]) / counts[axis] * 0.529177210903)
    phase_step_angstrom = grid_step_angstrom / 8.0
    pseudoforces = [abs(-(selected_totals[(i + 1) % 8] - selected_totals[(i - 1) % 8]) / (2.0 * phase_step_angstrom)) for i in range(8)]
    energy_p2p = (max(selected_totals) - min(selected_totals)) * 1000.0
    reference_p2p = (max(reference_totals) - min(reference_totals)) * 1000.0
    maximum_pseudoforce = max(pseudoforces)
    basis_count = 1 + 2 * len(vectors) + 8 * 108
    coefficient_fraction = basis_count / density.size
    gates_cfg = config["acceptance"]
    gates = {
        "reference_solver": terminal["status"] == "accepted",
        "low_g_unchanged": len(vectors) == 654,
        "basis_count": basis_count == gates_cfg["required_total_basis_count"],
        "rank_path_count": len(rank_rows) == gates_cfg["required_rank_path_case_count"],
        "full_effective_rank": all(row["total_effective_rank"] == gates_cfg["required_effective_rank"] for row in rank_rows),
        "condition": max(row["effective_condition_number"] for row in rank_rows) < gates_cfg["effective_condition_number_strict_lt"],
        "retained_margin": min(row["minimum_retained_eigenvalue_over_cutoff"] for row in rank_rows) >= gates_cfg["minimum_retained_eigenvalue_over_cutoff_min"],
        "density_l2": density_l2 < gates_cfg["localized_density_relative_l2_strict_lt"],
        "density_floor": float(np.min(selected)) >= gates_cfg["minimum_density_floor_electron_per_bohr3"],
        "electron_number": abs(selected_electrons - expected) / expected < gates_cfg["cube_electron_relative_error_strict_lt"],
        "component_energies": max(abs(component_errors[name]) for name in ("hartree", "external", "xc")) <= gates_cfg["component_energy_abs_error_max_mev_per_atom"],
        "combined_energy": abs(component_errors["combined_hartree_external_xc"]) <= gates_cfg["combined_energy_abs_error_max_mev_per_atom"],
        "fixed_wt_total": abs(component_errors["total_with_fixed_wt"]) <= gates_cfg["fixed_kedf_non_scf_total_energy_error_max_mev_per_atom"],
        "threshold_decade": threshold_change <= gates_cfg["threshold_decade_total_energy_change_max_mev_per_atom"],
        "eggbox_energy": energy_p2p <= gates_cfg["eggbox_energy_peak_to_peak_max_mev_per_atom"],
        "eggbox_pseudoforce": maximum_pseudoforce <= gates_cfg["eggbox_pseudoforce_max_ev_per_angstrom"],
        "coefficient_fraction": coefficient_fraction < gates_cfg["effective_coefficient_fraction_max"],
        "eggbox_phase_count": len(eggbox) == gates_cfg["required_eggbox_phase_count"],
    }
    status = "accepted_localized_eggbox_rank_pilot" if all(gates.values()) else "evidence_valid_scientific_gate_rejected"
    projection = {
        "schema_version": 1, "status": status, "basis_count": basis_count,
        "half_space_low_g_count": len(vectors), "real_low_g_count": 2 * len(vectors), "atomic_function_count": 8 * 108,
        "quadrature_point_count": int(density.size), "effective_coefficient_fraction": coefficient_fraction,
        "reference_electrons": float(np.sum(density, dtype=np.float64) * dv), "selected_electrons": selected_electrons,
        "selected_density_minimum": float(np.min(selected)), "density_relative_l2": density_l2,
        "component_energy_errors_mev_per_atom": component_errors, "threshold_totals": threshold_totals,
        "threshold_decade_max_change_mev_per_atom": threshold_change, "base_operator_energies_ev_per_cell": base_energies,
        "base_functional_stdout_sha256": base_stdout_sha,
    }
    summary = {
        "schema_version": 1, "protocol_revision": config["protocol_revision"], "status": status,
        "evidence_valid": True, "scientific_gate_accepted": all(gates.values()), "experiment_id": config["reference"]["experiment_id"],
        "atom_count": 108, "new_solver_run_count": 1, "candidate_id": config["scope"]["selected_candidate_id"],
        "gates": gates, "failed_gates": sorted(key for key, value in gates.items() if not value),
        "eggbox_energy_peak_to_peak_mev_per_atom": energy_p2p,
        "reference_eggbox_energy_peak_to_peak_mev_per_atom": reference_p2p,
        "eggbox_pseudoforce_max_ev_per_angstrom": maximum_pseudoforce,
        "rank_path_minimum_effective_rank": min(row["total_effective_rank"] for row in rank_rows),
        "rank_path_maximum_condition_number": max(row["effective_condition_number"] for row in rank_rows),
        "rank_path_minimum_retained_over_cutoff": min(row["minimum_retained_eigenvalue_over_cutoff"] for row in rank_rows),
        "density_relative_l2": density_l2, "component_energy_errors_mev_per_atom": component_errors,
        "threshold_decade_max_change_mev_per_atom": threshold_change,
        "limitations": config["limitations"],
    }
    rank_lines = ["displacement_angstrom\trelative_cutoff\tatomic_rank\ttotal_effective_rank\teffective_condition_number\tminimum_retained_eigenvalue_over_cutoff"]
    for displacement, rows in zip(config["projection"]["localized_rank_displacements_angstrom"], rank_by_path):
        for row in rows:
            rank_lines.append("\t".join(str(x) for x in [displacement, row["relative_cutoff"], row["atomic_rank"], row["total_effective_rank"], row["effective_condition_number"], row["minimum_retained_eigenvalue_over_cutoff"]]))
    egg_lines = ["phase_fraction\ttranslation_angstrom\treference_total_ev_per_atom\tselected_total_ev_per_atom\tselected_pseudoforce_ev_per_angstrom"]
    for index, row in enumerate(eggbox):
        egg_lines.append("\t".join(str(x) for x in [row["phase_fraction"], row["phase_fraction"] * grid_step_angstrom, reference_totals[index], selected_totals[index], pseudoforces[index]]))
    reference_identity = {**reference, "state_terminal_sha256": sha256_path(Path(config["execution"]["state_root"]) / "terminal.json"), "source_low_g_sha256": config["source"]["scale_geometry_low_g_sha256"]}
    runtime = {"schema_version": 1, "solver": config["runtime"], "analyzer_commit": git(root, "rev-parse", "HEAD"), "analyzer_sha256": sha256_path(root / ANALYZER_REL), "config_sha256": sha256_path(root / CONFIG_REL)}
    readme = f"""# S2/G2 Al 局域扰动 108 原子 pilot\n\n状态：`{status}`。\n\n本证据以一次 108 原子 KS-NL 局域位移计算为独立参考，固定 23 函数原始候选的周期展开，检查五点几何秩连续性、八相位 eggbox、伪力、密度和算子误差。\n\n- eggbox 能量峰峰值：{energy_p2p:.12g} meV/atom\n- 最大伪力：{maximum_pseudoforce:.12g} eV/Å\n- 密度相对 L2：{density_l2:.12g}\n- 秩路径最小总秩：{summary['rank_path_minimum_effective_rank']} / 2173\n- 未通过门：{', '.join(summary['failed_gates']) if summary['failed_gates'] else '无'}\n\n本 pilot 不关闭低 q 响应、Mg、G2c 或 G2 总闸门。\n""".encode()
    outputs = {
        "README.md": readme,
        "eggbox_metrics.tsv": ("\n".join(egg_lines) + "\n").encode(),
        "projection_metrics.json": canonical_json(projection),
        "rank_continuity.tsv": ("\n".join(rank_lines) + "\n").encode(),
        "reference_density.cube.zst": compress_zstd(run / reference["density_path"]),
        "reference_identity.json": canonical_json(reference_identity),
        "reference_log.txt": (run / "OUT.s2_g2_al108_localized_r1" / "running_scf.log").read_bytes(),
        "reference_result.json": canonical_json(reference),
        "reference_stru.txt": (run / "STRU").read_bytes(),
        "runtime.json": canonical_json(runtime),
        "summary.json": canonical_json(summary),
    }
    require(sorted(outputs) == sorted(config["output"]["files"]), "output denominator differs")
    return summary, outputs


def write_outputs(root: Path, config: dict, outputs: dict[str, bytes]) -> None:
    target = root / config["execution"]["analysis_root"]
    require(not target.exists(), "analysis root already exists")
    target.mkdir(parents=True)
    for name, data in outputs.items():
        (target / name).write_bytes(data)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = args.project_root.resolve()
    config = load_config(root)
    summary, outputs = build_analysis(root, config)
    if not args.dry_run:
        write_outputs(root, config, outputs)
    print(json.dumps({"status": summary["status"], "failed_gates": summary["failed_gates"], "output_written": not args.dry_run}, sort_keys=True))
    return 0 if summary["evidence_valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
