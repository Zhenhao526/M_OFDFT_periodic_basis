#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import itertools
import json
import math
import os
import re
import subprocess
from pathlib import Path

import s2_g2_al_scale_geometry_common_r3 as scale
import s2_g2_al1_pilot_common_r1 as pilot

BASE_COMMIT = "84dc83560bbdd8712f291a5a304e41e65c33ed47"
CONFIG_REL = Path("config/S2_g2_al_localized_eggbox_rank_r1.json")
PROTOCOL_REL = Path("docs/S2_G2_AL_LOCALIZED_EGGBOX_RANK_R1_PROTOCOL.md")
COMMON_REL = Path("scripts/s2_g2_al_localized_common_r1.py")
GENERATOR_REL = Path("scripts/generate_s2_g2_al_localized_r1.py")
WRAPPER_REL = Path("scripts/s2_g2_al_localized_rank_wrapper_r1.py")
RUNNER_REL = Path("scripts/run_s2_g2_al_localized_r1.py")
ANALYZER_REL = Path("scripts/analyze_s2_g2_al_localized_r1.py")
VALIDATOR_REL = Path("scripts/validate_s2_g2_al_localized_r1.py")
TEST_REL = Path("tests/unit/test_s2_g2_al_localized_r1.py")
INPUT_NAMES = ("INPUT", "STRU", "KPT", "metadata.json")

require = pilot.require
canonical_json = pilot.canonical_json
sha256_bytes = pilot.sha256_bytes
sha256_path = pilot.sha256_path
git = pilot.git

FLOAT = r"[+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?"
FINAL_ENERGY = re.compile(rf"!FINAL_ETOT_IS\s+({FLOAT})\s+eV")
ELECTRONS = re.compile(rf"Autoset the number of electrons\s*=\s*({FLOAT})")
ATOM_COUNT = re.compile(r"TOTAL ATOM NUMBER\s*=\s*([0-9]+)")
FORCE_ROW = re.compile(rf"^\s*([A-Za-z]+[0-9]+)\s+({FLOAT})\s+({FLOAT})\s+({FLOAT})\s*$")


def load_config(root: Path) -> dict:
    return json.loads((root / CONFIG_REL).read_text())


def validate_config(config: dict) -> None:
    require(config["schema_version"] == 1, "schema differs")
    require(config["protocol_revision"] == "S2-G2-AL-LOCALIZED-EGGBOX-RANK-20260811-R1", "protocol differs")
    require(config["base_commit"] == BASE_COMMIT, "base differs")
    require(config["scope"]["atom_count"] == 108 and config["scope"]["new_solver_run_count"] == 1, "scope differs")
    require(config["scope"]["selected_candidate_id"] == "r08_eta100_complementary", "candidate differs")
    require(config["source"]["scale_geometry_evidence_commit"] == "472b6c35fcd751bec5f2f833c203000a10ea357f", "source evidence differs")
    require(config["source"]["alpha_bohr_minus2"] == [0.075, 0.15, 0.3, 0.6, 1.2, 2.4, 4.8, 9.6], "radial family differs")
    require(config["source"]["frozen_108_half_space_low_g_count"] == 654, "low-G count differs")
    require(config["source"]["frozen_108_total_basis_count"] == 2173, "basis count differs")
    reference = config["reference"]
    require(reference["experiment_id"] == "S2-20260811-001", "experiment ID differs")
    require(reference["supercell_matrix"] == [[-3, 3, 3], [3, -3, 3], [3, 3, -3]], "supercell differs")
    require(reference["displacement_cartesian_angstrom"] == [0.05, 0.0, 0.0], "displacement differs")
    require(reference["expected_electrons"] == 324.0 and reference["kpoint_mesh"] == [1, 1, 1], "reference denominator differs")
    runtime = config["runtime"]
    require(runtime["hostname"] == "node05" and runtime["rank_count"] == 16, "runtime host/ranks differ")
    require(runtime["physical_core_ids"] == list(range(16)), "runtime cores differ")
    require(runtime["logical_cpu_pairs"] == [[i, i + 76] for i in range(16)], "runtime siblings differ")
    projection = config["projection"]
    require(projection["rank_relative_cutoffs"] == [1e-8, 1e-9, 1e-10], "rank cutoffs differ")
    require(projection["localized_rank_displacements_angstrom"] == [-0.05, -0.025, 0.0, 0.025, 0.05], "rank path differs")
    require(projection["eggbox_phase_fractions_of_grid_step"] == [i / 8 for i in range(8)], "eggbox phases differ")
    acceptance = config["acceptance"]
    require(acceptance["eggbox_energy_peak_to_peak_max_mev_per_atom"] == 1.0, "eggbox energy gate differs")
    require(acceptance["eggbox_pseudoforce_max_ev_per_angstrom"] == 0.002, "eggbox force gate differs")
    require(acceptance["required_total_basis_count"] == 2173 and acceptance["required_effective_rank"] == 2173, "rank denominator differs")
    require(config["limitations"] == {"low_q_response_validated": False, "mg_validated": False, "g2c_performance_validated": False, "g2_overall_accepted": False}, "limits differ")


def file_identity(path: Path, relative_to: Path | None = None) -> dict:
    require(path.is_file() and not path.is_symlink(), f"invalid evidence file: {path}")
    return {"path": str(path.relative_to(relative_to) if relative_to else path), "size_bytes": path.stat().st_size, "sha256": sha256_path(path)}


def atomic_write(path: Path, data: bytes, exclusive: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def utc_now() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def read_primitive_stru(path: Path) -> tuple[float, list[list[float]], list[str]]:
    lines = path.read_text().splitlines()
    constant_index = lines.index("LATTICE_CONSTANT")
    vector_index = lines.index("LATTICE_VECTORS")
    constant = float(lines[constant_index + 1].strip())
    vectors = [[float(value) for value in lines[vector_index + 1 + row].split()] for row in range(3)]
    return constant, vectors, lines


def localized_structure_payload(root: Path, config: dict) -> dict:
    import numpy as np

    source = root / config["source"]["primitive_structure_path"]
    require(sha256_path(source) == config["source"]["primitive_structure_sha256"], "primitive STRU SHA differs")
    constant, primitive_vectors_angstrom, _ = read_primitive_stru(source)
    matrix = np.asarray(config["reference"]["supercell_matrix"], dtype=int)
    primitive = np.asarray(primitive_vectors_angstrom, dtype=float)
    supercell = matrix @ primitive
    positions = np.asarray(scale.coset_positions(matrix), dtype=float)
    distances = np.linalg.norm(positions - 0.5, axis=1)
    localized_index = sorted(range(len(positions)), key=lambda i: (round(float(distances[i]), 14), tuple(positions[i])))[0]
    displacement = np.asarray(config["reference"]["displacement_cartesian_angstrom"], dtype=float)
    direct_delta = displacement @ np.linalg.inv(supercell)
    displaced = positions.copy()
    displaced[localized_index] = (displaced[localized_index] + direct_delta) % 1.0
    cell_bohr = supercell * constant
    return {
        "lattice_constant": constant,
        "primitive_vectors_angstrom": primitive.tolist(),
        "supercell_vectors_angstrom": supercell.tolist(),
        "cell_bohr": cell_bohr.tolist(),
        "fractional_positions": displaced.tolist(),
        "undisplaced_fractional_positions": positions.tolist(),
        "localized_atom_index_zero_based": localized_index,
        "localized_atom_index_one_based": localized_index + 1,
        "localized_atom_original_fractional": positions[localized_index].tolist(),
        "localized_atom_displaced_fractional": displaced[localized_index].tolist(),
        "direct_displacement": direct_delta.tolist(),
        "cartesian_displacement_angstrom": displacement.tolist(),
        "atom_count": len(positions),
    }


def render_stru(payload: dict) -> bytes:
    lines = [
        "ATOMIC_SPECIES", "Al 26.9815385 Al_std.upf upf201", "", "LATTICE_CONSTANT",
        f"{payload['lattice_constant']:.16f}", "", "LATTICE_VECTORS",
    ]
    lines.extend(" ".join(f"{value:.16f}" for value in row) for row in payload["supercell_vectors_angstrom"])
    lines.extend(["", "ATOMIC_POSITIONS", "Direct", "", "Al", "0.0", str(payload["atom_count"])])
    lines.extend(" ".join(f"{value:.16f}" for value in row) + " 1 1 1" for row in payload["fractional_positions"])
    return ("\n".join(lines) + "\n").encode()


def render_input(config: dict) -> bytes:
    spec = config["reference"]["input"]
    suffix = "s2_g2_al108_localized_r1"
    rows = [
        "INPUT_PARAMETERS", f"suffix {suffix}", "out_chg 1 17", "calculation scf", "esolver_type ksdft",
        "basis_type pw", "dft_functional PBE", "symmetry 0", "pseudo_dir .", "pseudo_rcut 16",
        f"ecutwfc {spec['ecutwfc_ry']}", f"ecutrho {spec['ecutrho_ry']}", f"nbands {spec['nbands']}",
        f"scf_nmax {spec['scf_nmax']}", f"scf_thr {spec['scf_thr']:.1e}", "cal_force 1", "cal_stress 1",
        "ks_solver cg", f"smearing_method {spec['smearing_method']}", f"smearing_sigma {spec['smearing_sigma_ry']}",
        f"mixing_type {spec['mixing_type']}", f"mixing_beta {spec['mixing_beta']}", "vnl_in_h 1",
    ]
    return ("\n".join(rows) + "\n").encode()


def render_kpt(config: dict) -> bytes:
    mesh = config["reference"]["kpoint_mesh"]
    shift = config["reference"]["kpoint_shift"]
    return ("K_POINTS\n0\nGamma\n" + " ".join(str(x) for x in mesh + shift) + "\n").encode()


def registered_input_payloads(root: Path, config: dict) -> dict[str, bytes]:
    geometry = localized_structure_payload(root, config)
    scientific_config = json.loads(json.dumps(config))
    scientific_config["status"] = "implementation_pending_preregistration"
    scientific_config["implementation_commit"] = "__FREEZE_IMPLEMENTATION_COMMIT__"
    metadata = {
        "schema_version": 1,
        "protocol_revision": config["protocol_revision"],
        "experiment_id": config["reference"]["experiment_id"],
        "role": config["reference"]["role"],
        "material": "Al",
        "atom_count": 108,
        "expected_electrons": 324.0,
        "suffix": "s2_g2_al108_localized_r1",
        "geometry": geometry,
        "pseudo": {"basename": "Al_std.upf", "sha256": config["source"]["pseudopotential_sha256"]},
        "scientific_config_sha256": sha256_bytes(canonical_json(scientific_config)),
    }
    return {"INPUT": render_input(config), "STRU": render_stru(geometry), "KPT": render_kpt(config), "metadata.json": canonical_json(metadata)}


def frozen_low_g_vectors(cell_bohr, electrons: float, eta_max: float, bound: int) -> list[tuple[int, int, int]]:
    rows = scale.integer_low_g_rows(cell_bohr, electrons, eta_max, bound)
    return [tuple(vector) for _, vector in rows]


def fft_integer_mesh(counts):
    import numpy as np
    axes = [np.fft.fftfreq(int(n)) * int(n) for n in counts]
    return np.meshgrid(*axes, indexing="ij")


def reciprocal_g2(cell_bohr, counts):
    import numpy as np
    mesh = fft_integer_mesh(counts)
    reciprocal = 2.0 * math.pi * np.linalg.inv(np.asarray(cell_bohr, dtype=float)).T
    gcart = sum(mesh[i][..., None] * reciprocal[i] for i in range(3))
    return np.einsum("...i,...i->...", gcart, gcart), mesh


def low_g_mask(counts, vectors):
    import numpy as np
    mask = np.ones(tuple(int(n) for n in counts), dtype=float)
    mask[(0, 0, 0)] = 0.0
    for vector in vectors:
        positive = tuple(int(vector[i]) % int(counts[i]) for i in range(3))
        negative = tuple((-int(vector[i])) % int(counts[i]) for i in range(3))
        mask[positive] = 0.0
        mask[negative] = 0.0
    return mask


def periodic_coordinates(frac, counts):
    import numpy as np
    return np.asarray(frac, dtype=float).T * np.asarray(counts, dtype=float)[:, None]


def sample_periodic(field, frac, order: int):
    from scipy.ndimage import map_coordinates
    return map_coordinates(field, periodic_coordinates(frac, field.shape), order=order, mode="wrap", prefilter=True)


def atomic_complement_gram(cell_bohr, counts, positions_frac, alphas, vectors, order: int):
    import numpy as np
    g2, _ = reciprocal_g2(cell_bohr, counts)
    mask = low_g_mask(counts, vectors)
    positions = np.asarray(positions_frac, dtype=float)
    differences = (positions[:, None, :] - positions[None, :, :]) % 1.0
    nat = len(positions)
    nalpha = len(alphas)
    volume = float(abs(np.linalg.det(cell_bohr)))
    gram = np.empty((nat * nalpha, nat * nalpha), dtype=float)
    kernels = [np.exp(-g2 / (4.0 * float(alpha))) for alpha in alphas]
    for ia, ka in enumerate(kernels):
        for ib in range(ia, nalpha):
            correlation = np.fft.ifftn(ka * kernels[ib] * mask).real * (g2.size / volume)
            block = sample_periodic(correlation, differences.reshape(-1, 3), order).reshape(nat, nat)
            gram[ia * nat:(ia + 1) * nat, ib * nat:(ib + 1) * nat] = block
            gram[ib * nat:(ib + 1) * nat, ia * nat:(ia + 1) * nat] = block.T
    return (gram + gram.T) * 0.5, kernels, mask


def atomic_complement_grams(cell_bohr, counts, position_sets, alphas, vectors, order: int):
    import numpy as np
    g2, _ = reciprocal_g2(cell_bohr, counts)
    mask = low_g_mask(counts, vectors)
    sets = [np.asarray(positions, dtype=float) for positions in position_sets]
    nat = len(sets[0])
    require(all(len(positions) == nat for positions in sets), "position-set denominator differs")
    differences = [(positions[:, None, :] - positions[None, :, :]) % 1.0 for positions in sets]
    nalpha = len(alphas)
    volume = float(abs(np.linalg.det(cell_bohr)))
    grams = [np.empty((nat * nalpha, nat * nalpha), dtype=float) for _ in sets]
    kernels = [np.exp(-g2 / (4.0 * float(alpha))) for alpha in alphas]
    for ia, ka in enumerate(kernels):
        for ib in range(ia, nalpha):
            correlation = np.fft.ifftn(ka * kernels[ib] * mask).real * (g2.size / volume)
            for gram, diff in zip(grams, differences):
                block = sample_periodic(correlation, diff.reshape(-1, 3), order).reshape(nat, nat)
                gram[ia * nat:(ia + 1) * nat, ib * nat:(ib + 1) * nat] = block
                gram[ib * nat:(ib + 1) * nat, ia * nat:(ia + 1) * nat] = block.T
    return [0.5 * (gram + gram.T) for gram in grams], kernels, mask


def normalized_rank(gram, relative_cutoff: float) -> dict:
    import numpy as np
    diagonal = np.diag(gram)
    require(bool(np.all(diagonal > 0.0)), "atomic complement has nonpositive norm")
    scale_values = np.sqrt(diagonal)
    normalized = gram / scale_values[:, None] / scale_values[None, :]
    eigenvalues = np.linalg.eigvalsh((normalized + normalized.T) * 0.5)
    maximum = float(eigenvalues[-1])
    cutoff = maximum * relative_cutoff
    retained = eigenvalues[eigenvalues > cutoff]
    require(retained.size > 0, "no retained atomic spectrum")
    return {
        "atomic_rank": int(retained.size),
        "minimum_retained_eigenvalue": float(retained[0]),
        "maximum_eigenvalue": maximum,
        "relative_cutoff": relative_cutoff,
        "minimum_retained_over_cutoff": float(retained[0] / cutoff),
        "effective_condition_number": float(maximum / retained[0]),
        "atomic_normalization": scale_values,
        "normalized_eigenvalues": eigenvalues,
    }


def fit_localized_density(rho, cell_bohr, positions_frac, alphas, vectors, config):
    import numpy as np
    counts = np.asarray(rho.shape, dtype=int)
    gram, kernels, mask = atomic_complement_gram(
        cell_bohr, counts, positions_frac, alphas, vectors, int(config["projection"]["interpolation_order"])
    )
    return fit_localized_density_from_components(rho, cell_bohr, positions_frac, alphas, vectors, config, gram, kernels, mask)


def fit_localized_density_from_components(
    rho, cell_bohr, positions_frac, alphas, vectors, config, gram, kernels, mask,
    relative_cutoff=None, eigensystem=None, include_rank=True,
):
    import numpy as np
    counts = np.asarray(rho.shape, dtype=int)
    target_fft = np.fft.fftn(np.asarray(rho, dtype=float))
    rhs_blocks = []
    for kernel in kernels:
        convolved = np.fft.ifftn(target_fft * kernel * mask).real
        rhs_blocks.append(sample_periodic(convolved, positions_frac, int(config["projection"]["interpolation_order"])))
    rhs = np.concatenate(rhs_blocks)
    eigenvalues, eigenvectors = np.linalg.eigh(gram) if eigensystem is None else eigensystem
    maximum = float(eigenvalues[-1])
    relative = float(
        config["projection"]["production_rank_relative_cutoff"]
        if relative_cutoff is None else relative_cutoff
    )
    keep = eigenvalues > maximum * relative
    coefficients = eigenvectors[:, keep] @ ((eigenvectors[:, keep].T @ rhs) / eigenvalues[keep])
    nat = len(positions_frac)
    coeff_by_atom = coefficients.reshape(len(alphas), nat).T
    selected_fft = np.zeros_like(target_fft, dtype=complex)
    low_keep = mask == 0.0
    selected_fft[low_keep] = target_fft[low_keep]
    _, mesh = reciprocal_g2(cell_bohr, counts)
    flat_k = np.column_stack([axis.reshape(-1) for axis in mesh])
    flat_out = selected_fft.reshape(-1)
    flat_mask = mask.reshape(-1).astype(bool)
    chunk = int(config["projection"]["structure_factor_chunk_size"])
    positions = np.asarray(positions_frac, dtype=float)
    kernel_flat = np.column_stack([kernel.reshape(-1) for kernel in kernels])
    factor = rho.size / float(abs(np.linalg.det(cell_bohr)))
    for start in range(0, len(flat_k), chunk):
        stop = min(start + chunk, len(flat_k))
        active = flat_mask[start:stop]
        if not np.any(active):
            continue
        phase = np.exp(-2j * math.pi * (flat_k[start:stop][active] @ positions.T))
        structure = phase @ coeff_by_atom
        output_view = flat_out[start:stop]
        output_view[active] = factor * np.sum(structure * kernel_flat[start:stop][active], axis=1)
    selected = np.fft.ifftn(selected_fft).real
    return {
        "selected_density": selected,
        "selected_fft": selected_fft,
        "target_fft": target_fft,
        "coefficients": coefficients,
        "gram": gram,
        "rank": normalized_rank(gram, relative) if include_rank else None,
        "eigensystem": (eigenvalues, eigenvectors),
    }


def parse_force_block(log_text: str, atom_count: int) -> list[list[float]]:
    lines = log_text.splitlines()
    markers = [i for i, line in enumerate(lines) if "#TOTAL-FORCE (eV/Angstrom)#" in line]
    require(markers, "missing force block")
    rows = []
    for line in lines[markers[-1] + 1:]:
        if "#TOTAL-STRESS" in line:
            break
        match = FORCE_ROW.fullmatch(line)
        if match:
            rows.append([float(match.group(i)) for i in (2, 3, 4)])
    require(len(rows) == atom_count, "force denominator differs")
    return rows


def parse_reference_run(run_dir: Path, config: dict) -> dict:
    import numpy as np
    metadata = json.loads((run_dir / "metadata.json").read_text())
    require(metadata["experiment_id"] == config["reference"]["experiment_id"], "run ID differs")
    geometry = metadata["geometry"]
    cell_bohr = np.asarray(geometry["cell_bohr"], dtype=float)
    output = run_dir / f"OUT.{metadata['suffix']}"
    log_path = output / "running_scf.log"
    cube_path = output / "chg.cube"
    log_text = log_path.read_text(errors="strict")
    require(log_text.count("#SCF IS CONVERGED#") == 1, "SCF convergence marker differs")
    require("!!SCF IS NOT CONVERGED!!" not in log_text, "SCF reports nonconvergence")
    require(ATOM_COUNT.findall(log_text) == ["108"], "log atom count differs")
    reported = [float(value) for value in ELECTRONS.findall(log_text)]
    require(reported and abs(reported[-1] - 324.0) < 1e-10, "reported electrons differ")
    energy = [float(value) for value in FINAL_ENERGY.findall(log_text)]
    require(energy, "final energy missing")
    counts, rho = pilot.parse_cube(cube_path, cell_bohr)
    volume = float(abs(np.linalg.det(cell_bohr)))
    electrons = float(np.sum(rho, dtype=np.float64) * volume / rho.size)
    relative = abs(electrons - 324.0) / 324.0
    require(relative < config["acceptance"]["cube_electron_relative_error_strict_lt"], "cube electron gate failed")
    forces = parse_force_block(log_text, 108)
    evidence = [run_dir / name for name in ("INPUT", "STRU", "KPT", "input_metadata.json", "metadata.json", "runner_return.json", "run.stdout", "run.stderr", "Al_std.upf")]
    evidence.extend([log_path, cube_path])
    evidence.extend(run_dir / "affinity" / f"rank_{rank:03d}.json" for rank in range(config["runtime"]["rank_count"]))
    require(all(path.is_file() and not path.is_symlink() for path in evidence), "run evidence denominator differs")
    affinity_rows = [json.loads((run_dir / "affinity" / f"rank_{rank:03d}.json").read_text()) for rank in range(config["runtime"]["rank_count"])]
    for rank, row in enumerate(affinity_rows):
        require(row["accepted"] is True and row["rank"] == rank and row["local_rank"] == rank, "rank identity differs")
        require(row["hostname"] == config["runtime"]["hostname"], "rank hostname differs")
        require(row["expected_sysfs_core_id"] == config["runtime"]["physical_core_ids"][rank], "rank core ID differs")
        require(row["os_logical_cpu_affinity"] == config["runtime"]["logical_cpu_pairs"][rank], "rank logical affinity differs")
        require(row["binary_sha256"] == config["runtime"]["binary_sha256"], "rank binary SHA differs")
    return {
        "schema_version": 1,
        "protocol_revision": config["protocol_revision"],
        "status": "accepted_reference",
        "experiment_id": metadata["experiment_id"],
        "atom_count": 108,
        "cube_grid": [int(x) for x in counts],
        "cube_electrons": electrons,
        "cube_electron_relative_error": relative,
        "final_energy_ev_per_cell": energy[-1],
        "final_energy_ev_per_atom": energy[-1] / 108.0,
        "forces_ev_per_angstrom": forces,
        "maximum_force_ev_per_angstrom": max(math.sqrt(sum(x * x for x in row)) for row in forces),
        "cell_bohr": geometry["cell_bohr"],
        "fractional_positions": geometry["fractional_positions"],
        "localized_atom_index_zero_based": geometry["localized_atom_index_zero_based"],
        "density_path": str(cube_path.relative_to(run_dir)),
        "density_sha256": sha256_path(cube_path),
        "evidence_files": [file_identity(path, run_dir) for path in evidence],
    }
