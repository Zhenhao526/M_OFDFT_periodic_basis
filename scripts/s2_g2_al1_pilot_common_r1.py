#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import hashlib
import io
import itertools
import json
import math
import os
import subprocess
import sys
from pathlib import Path

BASE_COMMIT = "0c0cf82b6b051e4f0dbf421911569ef5f8298afd"
CONFIG_REL = Path("config/S2_g2_al1_projection_operator_pilot_r1.json")
PROTOCOL_REL = Path("docs/S2_G2_AL1_PROJECTION_OPERATOR_PILOT_R1_PROTOCOL.md")
COMMON_REL = Path("scripts/s2_g2_al1_pilot_common_r1.py")
ANALYZER_REL = Path("scripts/analyze_s2_g2_al1_projection_operator_pilot_r1.py")
VALIDATOR_REL = Path("scripts/validate_s2_g2_al1_projection_operator_pilot_r1.py")
TEST_REL = Path("tests/unit/test_s2_g2_al1_projection_operator_pilot_r1.py")
IMPLEMENTATION_PATHS = {str(p) for p in (CONFIG_REL, PROTOCOL_REL, COMMON_REL, ANALYZER_REL, VALIDATOR_REL, TEST_REL)}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def canonical_json(payload: object) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git(root: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if completed.returncode != 0:
        raise ValueError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def load_config(root: Path) -> dict:
    return json.loads((root / CONFIG_REL).read_text())


def validate_config(config: dict) -> None:
    require(config["schema_version"] == 1, "schema differs")
    require(config["protocol_revision"] == "S2-G2-AL1-PROJECTION-OPERATOR-PILOT-20260811-R1", "protocol differs")
    require(config["base_commit"] == BASE_COMMIT, "base commit differs")
    scope = config["scope"]
    require(scope == {
        "stage": "S2", "gate": "G2a_pilot", "material": "Al", "atom_count": 1,
        "analysis_only": True, "new_solver_run_count": 0, "mg_enabled": False,
        "ml_enabled": False, "self_consistent_optimization_enabled": False,
    }, "scope differs")
    require(config["candidate_order"] == [
        "pw_fft_reference", "atomic_fft", "atomic_low_g_explicit", "atomic_low_g_complementary"
    ], "candidate order differs")
    projection = config["projection"]
    require(projection["alpha_bohr_minus2"] == [0.15, 0.3, 0.6, 1.2, 2.4, 4.8], "alpha ladder differs")
    require(projection["low_g_eta_max"] == 1.0, "low-G cutoff differs")
    require(projection["integer_search_bound"] == 3, "integer search bound differs")
    require(projection["development_probe_is_not_formal_evidence"] is True, "development-probe boundary differs")
    require(projection["lstsq_rcond"] == 1e-13, "least-squares cutoff differs")
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
    require(config["allowed_scientific_dispositions"] == [
        "accepted_candidate_exists", "evidence_valid_no_candidate_passes_all_g2a_pilot_gates"
    ], "allowed dispositions differ")
    require(config["output"]["files"] == ["README.md", "basis_spectrum.json", "metrics.tsv", "runtime.json", "summary.json"], "output denominator differs")


def validate_runtime(root: Path, config: dict) -> dict:
    import ase
    import dftpy
    import numpy as np
    import pylibxc
    import scipy

    runtime = config["runtime"]
    python_path = Path(runtime["python"])
    resolved = Path(runtime["resolved_python"])
    require(python_path.exists(), "registered Python is absent")
    require(python_path.resolve() == resolved, "Python symlink chain differs")
    require(resolved.is_file() and sha256_path(resolved) == runtime["resolved_python_sha256"], "Python binary differs")
    require(Path(sys.executable).resolve() == resolved, "current Python executable differs")
    lock = root / runtime["environment_lock_path"]
    require(lock.is_file() and sha256_path(lock) == runtime["environment_lock_sha256"], "environment lock differs")
    pseudo = Path(config["pseudopotential"]["path"])
    require(pseudo.is_file() and not pseudo.is_symlink(), "pseudopotential is absent or symlinked")
    require(pseudo.stat().st_size == config["pseudopotential"]["size_bytes"], "pseudopotential size differs")
    require(sha256_path(pseudo) == config["pseudopotential"]["sha256"], "pseudopotential SHA differs")
    observed_versions = {
        "python": sys.version.split()[0], "dftpy": dftpy.__version__, "pylibxc": pylibxc.__version__,
        "numpy": np.__version__, "scipy": scipy.__version__, "ase": ase.__version__,
    }
    require(observed_versions == runtime["versions"], "runtime versions differ")
    for key, value in runtime["thread_environment"].items():
        require(os.environ.get(key) == value, f"thread environment differs: {key}")
    return {
        "python_executable": str(python_path), "resolved_python": str(resolved),
        "resolved_python_sha256": runtime["resolved_python_sha256"],
        "environment_lock_path": runtime["environment_lock_path"],
        "environment_lock_sha256": runtime["environment_lock_sha256"],
        "versions": observed_versions, "thread_environment": runtime["thread_environment"],
        "pseudopotential_path": str(pseudo), "pseudopotential_sha256": config["pseudopotential"]["sha256"],
    }


def parse_structure_cell_bohr(path: Path):
    import numpy as np

    lines = path.read_text().splitlines()
    constant_index = lines.index("LATTICE_CONSTANT")
    vector_index = lines.index("LATTICE_VECTORS")
    lattice_constant = float(lines[constant_index + 1])
    vectors = np.asarray([[float(x) for x in lines[vector_index + i + 1].split()] for i in range(3)], dtype=float)
    cell = lattice_constant * vectors
    require(float(abs(np.linalg.det(cell))) > 0.0, "structure cell is singular")
    return cell


def parse_cube(path: Path, exact_cell):
    import numpy as np

    lines = path.read_text().splitlines()
    header = lines[2].split()
    nat = int(header[0])
    origin = np.asarray([float(x) for x in header[1:4]], dtype=float)
    require(nat == 1 and np.max(np.abs(origin)) == 0.0, "cube atom count/origin differs")
    counts = []
    axes = []
    for offset in range(3):
        fields = lines[3 + offset].split()
        counts.append(int(fields[0]))
        axes.append([float(x) for x in fields[1:4]])
    counts = np.asarray(counts, dtype=int)
    axes = np.asarray(axes, dtype=float)
    require(np.all(counts > 0), "cube grid counts must be positive")
    printed_cell = axes * counts[:, None]
    require(float(np.max(np.abs(printed_cell - exact_cell))) < 2e-5, "cube axes differ from exact structure")
    atom = lines[6].split()
    require(int(atom[0]) == 13 and abs(float(atom[1]) - 3.0) < 1e-12, "cube atom identity differs")
    require(max(abs(float(x)) for x in atom[2:5]) < 1e-14, "cube atom is not at the registered origin")
    values = np.asarray([float(x) for line in lines[7:] for x in line.split()], dtype=float)
    require(values.size == int(np.prod(counts)), "cube value denominator differs")
    require(np.all(np.isfinite(values)) and float(values.min()) >= 0.0, "reference density is nonfinite or negative")
    return counts, values.reshape(tuple(int(x) for x in counts))


def validate_sources(root: Path, config: dict) -> tuple[object, object, dict]:
    import numpy as np

    source = config["source"]
    density_path = root / source["density_path"]
    result_path = root / source["result_path"]
    structure_path = root / source["structure_path"]
    for path in (density_path, result_path, structure_path):
        require(path.is_file() and not path.is_symlink(), f"source missing or symlinked: {path}")
    require(density_path.stat().st_size == source["density_size_bytes"], "density size differs")
    require(sha256_path(density_path) == source["density_sha256"], "density SHA differs")
    require(sha256_path(result_path) == source["result_sha256"], "result SHA differs")
    require(sha256_path(structure_path) == source["structure_sha256"], "structure SHA differs")
    head = git(root, "rev-parse", "HEAD")
    for commit in (config["architecture_preregistration_commit"], source["source_commit"]):
        completed = subprocess.run(["git", "merge-base", "--is-ancestor", commit, head], cwd=root, check=False)
        require(completed.returncode == 0, f"source commit is not an ancestor: {commit}")
    committed_density = subprocess.run(
        ["git", "show", f"{source['source_commit']}:{source['density_path']}"], cwd=root,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    ).stdout
    require(sha256_bytes(committed_density) == source["density_sha256"], "committed density differs")
    cell = parse_structure_cell_bohr(structure_path)
    counts, rho = parse_cube(density_path, cell)
    require(list(int(x) for x in counts) == source["grid"], "source grid differs")
    volume = float(abs(np.linalg.det(cell)))
    electron_count = float(np.sum(rho, dtype=np.float64) * volume / rho.size)
    result = json.loads(result_path.read_text())
    require(result["status"] == "accepted" and result["experiment_id"] == source["source_run_id"], "source result status/ID differs")
    registered_electrons = float(result["electron_number"]["integrated_cube"])
    require(abs(electron_count - registered_electrons) < 5e-13, "source cube replay differs")
    require(result["electron_number"]["relative_error"] < 1e-10, "source electron-number gate is not accepted")
    return cell, rho, {
        "density_path": source["density_path"], "density_sha256": source["density_sha256"],
        "result_path": source["result_path"], "result_sha256": source["result_sha256"],
        "structure_path": source["structure_path"], "structure_sha256": source["structure_sha256"],
        "grid": source["grid"], "volume_bohr3": volume, "integrated_electrons": electron_count,
    }


def fractional_grid(counts):
    import numpy as np
    return np.stack(np.meshgrid(*[np.arange(int(n), dtype=float) / int(n) for n in counts], indexing="ij"), axis=-1).reshape(-1, 3)


def minimum_image_r2(frac, cell):
    import numpy as np
    r2 = np.full(len(frac), np.inf)
    for shift in itertools.product((-1, 0, 1), repeat=3):
        displacement = (frac - np.asarray(shift, dtype=float)) @ cell
        r2 = np.minimum(r2, np.einsum("ij,ij->i", displacement, displacement))
    return r2


def low_g_vectors(frac, cell, electrons: float, eta_max: float, bound: int):
    import numpy as np
    volume = float(abs(np.linalg.det(cell)))
    kf = float((3.0 * math.pi**2 * electrons / volume) ** (1.0 / 3.0))
    reciprocal = 2.0 * math.pi * np.linalg.inv(cell).T
    rows = []
    for vector in itertools.product(range(-bound, bound + 1), repeat=3):
        if vector == (0, 0, 0):
            continue
        first = next(component for component in vector if component != 0)
        if first < 0:
            continue
        eta = float(np.linalg.norm(np.asarray(vector, dtype=float) @ reciprocal) / (2.0 * kf))
        if eta <= eta_max + 1e-12:
            rows.append((eta, vector))
    rows.sort(key=lambda row: (round(row[0], 12), row[1]))
    functions = []
    for eta, vector in rows:
        phase = 2.0 * math.pi * (frac @ np.asarray(vector, dtype=float))
        functions.extend((np.cos(phase), np.sin(phase)))
    return rows, np.stack(functions, axis=1), kf


def normalized_spectrum(matrix, dv: float, relative_cutoff: float):
    import numpy as np
    gram = matrix.T @ matrix * dv
    scale = np.sqrt(np.diag(gram))
    require(bool(np.all(scale > 0.0)), "basis contains a zero-norm column")
    normalized = gram / scale[:, None] / scale[None, :]
    eigenvalues = np.linalg.eigvalsh(normalized)
    retained = eigenvalues[eigenvalues > float(eigenvalues.max()) * relative_cutoff]
    require(retained.size > 0, "basis has no retained spectrum")
    return eigenvalues, int(retained.size), float(retained.max() / retained.min())


def constrained_fit(matrix, target, dv: float, electrons: float, rcond: float):
    import numpy as np
    charge = matrix.sum(axis=0, dtype=np.float64) * dv
    gram = matrix.T @ matrix * dv
    rhs = matrix.T @ target * dv
    kkt = np.block([[gram, charge[:, None]], [charge[None, :], np.zeros((1, 1))]])
    solution = np.linalg.lstsq(kkt, np.concatenate((rhs, [electrons])), rcond=rcond)[0]
    coefficients = solution[:-1]
    density = matrix @ coefficients
    require(abs(float(np.sum(density, dtype=np.float64) * dv) - electrons) < 5e-12, "KKT electron constraint differs")
    return coefficients, density


def build_projections(config: dict, cell, reference_density):
    import numpy as np
    counts = np.asarray(reference_density.shape, dtype=int)
    frac = fractional_grid(counts)
    target = np.asarray(reference_density, dtype=float).reshape(-1)
    volume = float(abs(np.linalg.det(cell)))
    dv = volume / target.size
    r2 = minimum_image_r2(frac, cell)
    gaussians = []
    for alpha in config["projection"]["alpha_bohr_minus2"]:
        function = np.exp(-float(alpha) * r2)
        function /= float(function.sum(dtype=np.float64) * dv)
        gaussians.append(function)
    atomic = np.stack(gaussians, axis=1)
    low_rows, low_functions, kf = low_g_vectors(
        frac, cell, config["source"]["expected_electrons"],
        config["projection"]["low_g_eta_max"], config["projection"]["integer_search_bound"],
    )
    require([row[1] for row in low_rows] == [
        (0, 0, 1), (0, 1, 0), (1, 0, 0), (1, 1, 1), (0, 1, 1), (1, 0, 1), (1, 1, 0)
    ], "registered low-G vector set differs")
    constant = np.full((target.size, 1), 1.0 / volume)
    compensated = atomic - np.mean(atomic, axis=0, keepdims=True)
    explicit = np.column_stack((constant, compensated, low_functions))
    low_block = np.column_stack((constant, low_functions))
    low_q, _ = np.linalg.qr(low_block, mode="reduced")
    complementary_atomic = compensated - low_q @ (low_q.T @ compensated)
    complementary = np.column_stack((constant, low_functions, complementary_atomic))
    matrices = {
        "atomic_fft": atomic,
        "atomic_low_g_explicit": explicit,
        "atomic_low_g_complementary": complementary,
    }
    projections = {
        "pw_fft_reference": {
            "density": target.copy(), "coefficients": [], "basis_count": int(target.size),
            "effective_rank": int(target.size), "effective_condition_number": 1.0,
            "normalized_overlap_eigenvalues": [1.0],
        }
    }
    for name, matrix in matrices.items():
        coefficients, density = constrained_fit(
            matrix, target, dv, config["source"]["expected_electrons"], config["projection"]["lstsq_rcond"]
        )
        eigenvalues, rank, condition = normalized_spectrum(
            matrix, dv, config["projection"]["rank_eigen_relative_cutoff"]
        )
        projections[name] = {
            "density": density, "coefficients": coefficients.tolist(), "basis_count": int(matrix.shape[1]),
            "effective_rank": rank, "effective_condition_number": condition,
            "normalized_overlap_eigenvalues": eigenvalues.tolist(),
        }
    return projections, {
        "volume_bohr3": volume, "voxel_volume_bohr3": dv, "kf_bohr_inverse": kf,
        "low_g_half_vectors": [{"eta_q_over_2kf": eta, "integer_vector": list(vector)} for eta, vector in low_rows],
        "real_low_g_function_count": int(low_functions.shape[1]), "cell_bohr": np.asarray(cell).tolist(),
    }


def evaluate_operators(config: dict, cell, counts, projections: dict):
    import numpy as np
    from ase import Atoms
    from dftpy.constants import ENERGY_CONV, Units
    from dftpy.field import DirectField
    from dftpy.functional import Functional, LocalPseudo
    from dftpy.grid import DirectGrid
    from dftpy.ions import Ions

    atoms = Atoms("Al", positions=[[0.0, 0.0, 0.0]], cell=np.asarray(cell) * Units.Bohr, pbc=True)
    ions = Ions.from_ase(atoms)
    grid = DirectGrid(lattice=ions.cell, nr=np.asarray(counts, dtype=int), full=False)
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
        pseudo = LocalPseudo(grid=grid, ions=ions, PP_list={"Al": config["pseudopotential"]["path"]})
        functionals = {
            "hartree": Functional(type="HARTREE"),
            "external": pseudo,
            "xc": Functional(type="XC", name="PBE"),
            "fixed_kedf": Functional(type="KEDF", name="WT", alpha=5.0 / 6.0, beta=5.0 / 6.0, rho0=None),
        }
        conversion = float(ENERGY_CONV["Hartree"]["eV"])
        energies = {}
        for name in config["candidate_order"]:
            field = DirectField(grid=grid)
            field[:] = projections[name]["density"].reshape(tuple(int(x) for x in counts))
            energies[name] = {
                label: float(functional.compute(field, calcType={"E"}).energy) * conversion
                for label, functional in functionals.items()
            }
    return energies, sha256_bytes(captured.getvalue().encode())


def density_sha256(density) -> str:
    import numpy as np
    return sha256_bytes(np.asarray(density, dtype="<f8").tobytes(order="C"))


def build_analysis(root: Path, config: dict) -> dict:
    import numpy as np

    validate_config(config)
    runtime = validate_runtime(root, config)
    cell, rho, source = validate_sources(root, config)
    projections, basis_context = build_projections(config, cell, rho)
    energies, functional_stdout_sha = evaluate_operators(config, cell, rho.shape, projections)
    reference = np.asarray(projections["pw_fft_reference"]["density"], dtype=float)
    reference_energies = energies["pw_fft_reference"]
    dv = basis_context["voxel_volume_bohr3"]
    expected_electrons = config["source"]["expected_electrons"]
    gates_cfg = config["acceptance"]
    metrics = []
    spectra = {}
    for name in config["candidate_order"]:
        projection = projections[name]
        density = np.asarray(projection["density"], dtype=float)
        electrons = float(np.sum(density, dtype=np.float64) * dv)
        relative_electron_error = abs(electrons - expected_electrons) / expected_electrons
        relative_l2 = float(np.linalg.norm(density - reference) / np.linalg.norm(reference))
        errors = {label: (energies[name][label] - reference_energies[label]) * 1000.0 for label in reference_energies}
        combined = errors["hartree"] + errors["external"] + errors["xc"]
        if name == "pw_fft_reference":
            gate_values = {
                "electron_number": True, "density_l2": True, "nonnegative_density": True,
                "condition_number": True, "hartree": True, "external": True, "xc": True,
                "combined_hartree_external_xc": True, "fixed_kedf": True,
            }
            status = "accepted_reference"
        else:
            gate_values = {
                "electron_number": relative_electron_error < gates_cfg["electron_number_relative_error_strict_lt"],
                "density_l2": relative_l2 < gates_cfg["density_relative_l2_strict_lt"],
                "nonnegative_density": float(density.min()) >= gates_cfg["minimum_density_floor_electron_per_bohr3"],
                "condition_number": projection["effective_condition_number"] < gates_cfg["effective_condition_number_strict_lt"],
                "hartree": abs(errors["hartree"]) < gates_cfg["hartree_abs_error_strict_lt_mev_per_atom"],
                "external": abs(errors["external"]) < gates_cfg["external_abs_error_strict_lt_mev_per_atom"],
                "xc": abs(errors["xc"]) < gates_cfg["xc_abs_error_strict_lt_mev_per_atom"],
                "combined_hartree_external_xc": abs(combined) < gates_cfg["combined_hartree_external_xc_abs_error_strict_lt_mev_per_atom"],
                "fixed_kedf": abs(errors["fixed_kedf"]) < gates_cfg["fixed_kedf_abs_error_strict_lt_mev_per_atom"],
            }
            status = "accepted_pilot" if all(gate_values.values()) else "rejected_pilot"
        failed_gates = [key for key, accepted in gate_values.items() if not accepted]
        metrics.append({
            "candidate_id": name, "status": status, "basis_count": projection["basis_count"],
            "effective_rank": projection["effective_rank"],
            "effective_condition_number": projection["effective_condition_number"],
            "electron_count": electrons, "electron_number_relative_error": relative_electron_error,
            "density_relative_l2": relative_l2, "density_min": float(density.min()), "density_max": float(density.max()),
            "density_sha256_float64_le": density_sha256(density),
            "energies_ev_per_atom": energies[name], "errors_mev_per_atom": errors,
            "combined_hartree_external_xc_error_mev_per_atom": combined,
            "gates": gate_values, "failed_gates": failed_gates,
        })
        spectra[name] = {
            "basis_count": projection["basis_count"], "effective_rank": projection["effective_rank"],
            "effective_condition_number": projection["effective_condition_number"],
            "normalized_overlap_eigenvalues": projection["normalized_overlap_eigenvalues"],
            "coefficients": projection["coefficients"],
        }
    accepted_candidates = [row["candidate_id"] for row in metrics if row["status"] == "accepted_pilot"]
    disposition = "accepted_candidate_exists" if accepted_candidates else "evidence_valid_no_candidate_passes_all_g2a_pilot_gates"
    require(disposition in config["allowed_scientific_dispositions"], "scientific disposition is not registered")
    return {
        "summary": {
            "schema_version": 1, "protocol_revision": config["protocol_revision"], "status": disposition,
            "evidence_valid": True, "stage": "S2", "gate": "G2a_pilot", "material": "Al",
            "atom_count": 1, "candidate_count": 4, "accepted_candidates": accepted_candidates,
            "new_solver_run_count": 0,
            "next_action": "new_revision_expand_basis_or_low_g_without_modifying_this_pilot" if not accepted_candidates else "advance_accepted_candidates_to_geometry_continuity_pilot",
            "metrics": metrics,
        },
        "basis_spectrum": {
            "schema_version": 1, "protocol_revision": config["protocol_revision"],
            **basis_context, "candidates": spectra,
        },
        "runtime": {
            "schema_version": 1, "protocol_revision": config["protocol_revision"], **runtime,
            "source": source, "functional_stdout_sha256": functional_stdout_sha,
        },
        "metrics": metrics,
    }


def metrics_tsv(metrics: list[dict]) -> bytes:
    columns = [
        "candidate_id", "status", "basis_count", "effective_rank", "effective_condition_number",
        "electron_number_relative_error", "density_relative_l2", "density_min",
        "hartree_error_mev_per_atom", "external_error_mev_per_atom", "xc_error_mev_per_atom",
        "combined_error_mev_per_atom", "fixed_kedf_error_mev_per_atom", "failed_gates",
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


def readme_bytes(analysis: dict) -> bytes:
    summary = analysis["summary"]
    rows = summary["metrics"]
    lines = [
        "# S2/G2 Al one-atom projection and operator pilot R1", "",
        f"Disposition: `{summary['status']}`.", "",
        "This is committed analysis-only evidence over the frozen Al KS-NL density; no solver or density optimizer was run.", "",
        "| candidate | status | density L2 | condition | combined H+ext+XC (meV/atom) | WT error (meV/atom) | failed gates |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['candidate_id']} | {row['status']} | {row['density_relative_l2']:.9g} | "
            f"{row['effective_condition_number']:.9g} | {row['combined_hartree_external_xc_error_mev_per_atom']:.9g} | "
            f"{row['errors_mev_per_atom']['fixed_kedf']:.9g} | {','.join(row['failed_gates']) or '—'} |"
        )
    lines.extend(["", f"Next action: `{summary['next_action']}`.", ""])
    return "\n".join(lines).encode()


def render_outputs(analysis: dict) -> dict[str, bytes]:
    return {
        "README.md": readme_bytes(analysis),
        "basis_spectrum.json": canonical_json(analysis["basis_spectrum"]),
        "metrics.tsv": metrics_tsv(analysis["metrics"]),
        "runtime.json": canonical_json(analysis["runtime"]),
        "summary.json": canonical_json(analysis["summary"]),
    }
