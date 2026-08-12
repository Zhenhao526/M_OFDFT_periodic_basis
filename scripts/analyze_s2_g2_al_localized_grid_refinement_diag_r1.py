#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
from scipy.signal import resample


def fourier_resample_periodic(values: np.ndarray, target: int) -> np.ndarray:
    out = np.asarray(values, dtype=np.float64)
    for axis in range(3):
        out = resample(out, target, axis=axis, domain="time")
    return np.asarray(out.real, dtype=np.float64)


def cyclic_pseudoforce(energies_ev_per_atom: list[float], phase_step_angstrom: float) -> list[float]:
    n = len(energies_ev_per_atom)
    return [
        abs(-(energies_ev_per_atom[(i + 1) % n] - energies_ev_per_atom[(i - 1) % n]) / (2.0 * phase_step_angstrom))
        for i in range(n)
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--target-grid", type=int, default=128)
    parser.add_argument("--phase-count", type=int, default=16)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    root = args.project_root.resolve()
    sys.path.insert(0, str(root / "scripts"))
    import analyze_s2_g2_al_localized_analysis_r2 as r2
    import analyze_s2_g2_al_localized_r1 as r1

    r2_config = r2.load_config(root)
    r1_config = json.loads((root / r2_config["source"]["r1_config_path"]).read_text())
    run, reference, _, _ = r2.recovered_reference(root, r1_config, r2_config)
    metadata = json.loads((run / "input_metadata.json").read_text())
    counts, density96 = r2.parse_cube_108(
        run / reference["density_path"],
        metadata["geometry"]["cell_bohr"],
        metadata["geometry"]["fractional_positions"],
    )
    if counts.tolist() != [96, 96, 96]:
        raise ValueError(f"source grid differs: {counts.tolist()}")

    cell = np.asarray(reference["cell_bohr"], dtype=float)
    positions = np.asarray(reference["fractional_positions"], dtype=float)
    vectors = r1.source_low_g(root, r1_config, cell)
    paths = r2.exact_rank_position_sets(metadata, r1_config)
    started = time.time()
    grams, kernels, mask = r1.atomic_complement_grams(
        cell,
        counts,
        paths,
        r1_config["source"]["alpha_bohr_minus2"],
        vectors,
        r1_config["projection"]["interpolation_order"],
    )
    eigensystem = np.linalg.eigh(grams[-1])
    fit = r1.fit_localized_density_from_components(
        density96,
        cell,
        positions,
        r1_config["source"]["alpha_bohr_minus2"],
        vectors,
        r1_config,
        grams[-1],
        kernels,
        mask,
        relative_cutoff=r1_config["projection"]["production_rank_relative_cutoff"],
        eigensystem=eigensystem,
        include_rank=False,
    )
    selected96 = np.asarray(fit["selected_density"], dtype=np.float64)

    density = fourier_resample_periodic(density96, args.target_grid)
    selected = fourier_resample_periodic(selected96, args.target_grid)
    volume = float(abs(np.linalg.det(cell)))
    source_integrals = {
        "reference_96": float(np.sum(density96, dtype=np.float64) * volume / density96.size),
        "selected_96": float(np.sum(selected96, dtype=np.float64) * volume / selected96.size),
    }
    resampled_integrals = {
        f"reference_{args.target_grid}": float(np.sum(density, dtype=np.float64) * volume / density.size),
        f"selected_{args.target_grid}": float(np.sum(selected, dtype=np.float64) * volume / selected.size),
    }
    phases = [i / args.phase_count for i in range(args.phase_count)]
    rows = []
    for index, phase in enumerate(phases):
        delta = np.zeros(3, dtype=float)
        delta[0] = phase / args.target_grid
        shifted_positions = (positions + delta) % 1.0
        reference_shift = r1.shifted_density(density, phase, 0)
        selected_shift = r1.shifted_density(selected, phase, 0)
        energies, stdout_sha = r1.evaluate_components(
            r1_config,
            cell,
            np.asarray([args.target_grid] * 3, dtype=int),
            shifted_positions,
            {"reference": reference_shift, "selected": selected_shift},
        )
        rows.append({
            "phase_fraction": phase,
            "reference_total_ev_per_atom": energies["reference"]["total_with_fixed_wt"] / 108.0,
            "selected_total_ev_per_atom": energies["selected"]["total_with_fixed_wt"] / 108.0,
            "functional_stdout_sha256": stdout_sha,
        })
        print(json.dumps({"completed_phase": index + 1, "phase_count": args.phase_count, "phase": phase}, sort_keys=True), flush=True)

    ref = [row["reference_total_ev_per_atom"] for row in rows]
    sel = [row["selected_total_ev_per_atom"] for row in rows]
    excess = [s - r for s, r in zip(sel, ref)]
    uniform_grid_step_angstrom = float(np.linalg.norm(cell[0]) * 0.529177210903 / args.target_grid)
    phase_step_angstrom = uniform_grid_step_angstrom / args.phase_count
    reference_forces = cyclic_pseudoforce(ref, phase_step_angstrom)
    selected_forces = cyclic_pseudoforce(sel, phase_step_angstrom)
    excess_forces = cyclic_pseudoforce(excess, phase_step_angstrom)
    for row, rf, sf, ef in zip(rows, reference_forces, selected_forces, excess_forces):
        row["reference_pseudoforce_ev_per_angstrom"] = rf
        row["selected_pseudoforce_ev_per_angstrom"] = sf
        row["projection_excess_pseudoforce_ev_per_angstrom"] = ef

    result = {
        "schema_version": 1,
        "status": "diagnostic_grid_refinement_complete",
        "source_grid": [96, 96, 96],
        "target_grid": [args.target_grid] * 3,
        "phase_count": args.phase_count,
        "resampling": "periodic_Fourier_resample_each_axis_scipy_signal_resample",
        "source_integrals_electron": source_integrals,
        "resampled_integrals_electron": resampled_integrals,
        "reference_density_minimum": float(np.min(density)),
        "selected_density_minimum": float(np.min(selected)),
        "reference_energy_peak_to_peak_mev_per_atom": float((max(ref) - min(ref)) * 1000.0),
        "selected_energy_peak_to_peak_mev_per_atom": float((max(sel) - min(sel)) * 1000.0),
        "projection_excess_energy_peak_to_peak_mev_per_atom": float((max(excess) - min(excess)) * 1000.0),
        "reference_pseudoforce_max_ev_per_angstrom": float(max(reference_forces)),
        "selected_pseudoforce_max_ev_per_angstrom": float(max(selected_forces)),
        "projection_excess_pseudoforce_max_ev_per_angstrom": float(max(excess_forces)),
        "elapsed_seconds": time.time() - started,
        "rows": rows,
    }
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(encoded)
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
