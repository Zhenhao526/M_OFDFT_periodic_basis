#!/usr/bin/env python3
"""Run the frozen 14-point DFTpy/ABACUS cross-code G1 comparison."""

from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path


BOHR_TO_ANGSTROM = 0.529177210903


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    required = {
        "experiment_id",
        "material",
        "volume_ratio",
        "abacus_run_id",
        "stru_path",
        "pseudopotential",
        "xc",
        "grid_nx",
        "grid_ny",
        "grid_nz",
        "expected_electrons",
        "atom_count",
    }
    if not rows or set(rows[0]) != required:
        raise ValueError("cross-code manifest fields differ from the frozen schema")
    ids = [row["experiment_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate experiment ID in cross-code manifest")
    return rows


def _git(project_root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(project_root), *args], text=True
    ).strip()


def _nonempty_stru_lines(path: Path) -> list[str]:
    rows = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        value = raw.split("#", 1)[0].strip()
        if value:
            rows.append(value)
    return rows


def load_stru(path: Path):
    """Read the frozen simple ABACUS STRU subset and return an ASE Atoms."""
    import numpy as np
    from ase import Atoms

    rows = _nonempty_stru_lines(path)
    lattice_index = rows.index("LATTICE_CONSTANT")
    vectors_index = rows.index("LATTICE_VECTORS")
    positions_index = rows.index("ATOMIC_POSITIONS")
    lattice_constant_bohr = float(rows[lattice_index + 1].split()[0])
    vectors = np.asarray(
        [[float(value) for value in rows[vectors_index + offset].split()[:3]] for offset in (1, 2, 3)],
        dtype=float,
    )
    cell_angstrom = vectors * lattice_constant_bohr * BOHR_TO_ANGSTROM
    mode = rows[positions_index + 1].lower()
    if mode != "direct":
        raise ValueError(f"only Direct STRU positions are frozen, observed {mode!r}")
    cursor = positions_index + 2
    symbols: list[str] = []
    scaled_positions: list[list[float]] = []
    while cursor < len(rows):
        symbol = rows[cursor].split()[0]
        if cursor + 2 >= len(rows):
            raise ValueError(f"incomplete atomic block in {path}")
        float(rows[cursor + 1].split()[0])
        count = int(rows[cursor + 2].split()[0])
        cursor += 3
        for _ in range(count):
            values = rows[cursor].split()
            if len(values) < 3:
                raise ValueError(f"short coordinate row in {path}")
            symbols.append(symbol)
            scaled_positions.append([float(value) for value in values[:3]])
            cursor += 1
    return Atoms(symbols=symbols, scaled_positions=scaled_positions, cell=cell_angstrom, pbc=True)


def validate_environment(project_root: Path, config: dict) -> dict:
    lock_path = project_root / config["environment_lock"]
    lock = read_json(lock_path)
    if Path(sys.executable) != Path(config["python"]):
        raise ValueError(f"wrong Python executable: {sys.executable}")
    if sha256(Path(sys.executable)) != lock["python"]["executable_sha256"]:
        raise ValueError("Python executable SHA-256 differs from lock")
    state = Path("/home/shenwei01/.local/share/m_ofdft/dftpy-2.2.0")
    artifacts = {
        "dftpy_source": (state / "source/dftpy-2.2.0.tar.gz", lock["dftpy"]["source_sha256"]),
        "libxc_source": (state / "libxc-7.0.0.tar.bz2", lock["libxc"]["source_sha256"]),
        "libxc_shared": (
            Path(sys.prefix) / "lib/python3.11/site-packages/libxc.so.15",
            lock["libxc"]["installed_shared_library_sha256"],
        ),
    }
    observed = {}
    for name, (path, expected) in artifacts.items():
        if not path.is_file():
            raise ValueError(f"locked environment artifact is missing: {path}")
        actual = sha256(path)
        if actual != expected:
            raise ValueError(f"locked environment artifact differs: {path}")
        observed[name] = {"path": str(path), "sha256": actual}
    for relative, expected in lock["pseudopotential_sha256"].items():
        actual = sha256(project_root / relative)
        if actual != expected:
            raise ValueError(f"locked pseudopotential differs: {relative}")
    for variable in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        if os.environ.get(variable) != "1":
            raise ValueError(f"{variable} must be exactly 1")
    return {
        "lock_path": config["environment_lock"],
        "lock_sha256": sha256(lock_path),
        "artifacts": observed,
    }


def run_point(project_root: Path, row: dict[str, str], config: dict, run_directory: Path) -> None:
    import numpy as np
    import dftpy
    import pylibxc
    from dftpy.constants import ENERGY_CONV, STRESS_CONV, Units, environ as dftpy_environ
    from dftpy.field import DirectField
    from dftpy.functional import Functional, LocalPseudo, TotalFunctional
    from dftpy.grid import DirectGrid
    from dftpy.ions import Ions
    from dftpy.optimization import Optimization

    stdout = io.StringIO()
    started = time.time()
    stru_path = project_root / row["stru_path"]
    pp_path = project_root / row["pseudopotential"]
    abacus_result_path = project_root / "runs" / row["abacus_run_id"] / "result.json"
    atoms = load_stru(stru_path)
    expected_atoms = int(row["atom_count"])
    if len(atoms) != expected_atoms:
        raise ValueError(f"STRU atom count differs for {row['experiment_id']}")
    nr = np.asarray([int(row["grid_nx"]), int(row["grid_ny"]), int(row["grid_nz"])], dtype=int)
    opt_cfg = config["optimization"]

    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stdout):
        dftpy_environ["STDOUT"] = stdout
        ions = Ions.from_ase(atoms)
        grid = DirectGrid(lattice=ions.cell, nr=nr, full=False)
        if not np.array_equal(np.asarray(grid.nrR, dtype=int), nr):
            raise ValueError("DFTpy direct-grid dimensions differ from manifest")
        pseudo = LocalPseudo(
            grid=grid,
            ions=ions,
            PP_list={atoms[0].symbol: str(pp_path)},
        )
        expected_electrons = float(row["expected_electrons"])
        if abs(float(ions.get_ncharges()) - expected_electrons) > 1.0e-12:
            raise ValueError("DFTpy pseudopotential valence differs from manifest")
        rho_initial = DirectField(grid=grid)
        rho_initial[:] = ions.get_ncharges() / ions.cell.volume
        kedf = Functional(
            type="KEDF",
            name="WT",
            alpha=config["functional_contract"]["wt_alpha"],
            beta=config["functional_contract"]["wt_beta"],
            rho0=None,
        )
        xc = Functional(type="XC", name=row["xc"])
        hartree = Functional(type="HARTREE")
        evaluator = TotalFunctional(KE=kedf, XC=xc, HARTREE=hartree, PSEUDO=pseudo)
        optimizer = Optimization(
            EnergyEvaluator=evaluator,
            optimization_method=opt_cfg["method"],
            optimization_options={
                "econv": opt_cfg["energy_convergence_hartree_per_atom"] * ions.nat,
                "maxfun": opt_cfg["max_direction_steps"],
                "maxiter": opt_cfg["max_density_iterations"],
            },
        )
        rho = optimizer.optimize_rho(guess_rho=rho_initial)
        parts = evaluator.get_energy_potential(rho, calcType={"E"}, split=True)
        stress = evaluator.get_stress(rho, split=True)

    dftpy_environ["STDOUT"] = sys.stdout
    stdout_text = stdout.getvalue()
    if "Density Optimization Converged" not in stdout_text:
        raise RuntimeError("DFTpy optimizer convergence marker is absent from captured stdout")
    run_directory.mkdir(parents=True, exist_ok=False)
    (run_directory / "run.stdout").write_text(stdout_text, encoding="utf-8")
    density = np.asarray(rho, dtype=np.float64)
    np.save(run_directory / "density.npy", density, allow_pickle=False)
    stress_gpa = np.asarray(stress["TOTAL"], dtype=float) * STRESS_CONV["Ha/Bohr3"]["GPa"]
    pressure_gpa = -float(np.trace(stress_gpa)) / 3.0
    energy_ev = float(parts["TOTAL"].energy * ENERGY_CONV["Hartree"]["eV"])
    electron_count = float(rho.integral())
    affinity = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else []
    result = {
        "experiment_id": row["experiment_id"],
        "status": "accepted" if optimizer.converged == opt_cfg["required_dftpy_converged_code"] else "failed",
        "dftpy_converged_code": int(optimizer.converged),
        "material": row["material"],
        "volume_ratio": float(row["volume_ratio"]),
        "atom_count": ions.nat,
        "cell_angstrom": np.asarray(atoms.cell.array, dtype=float).tolist(),
        "cell_volume_angstrom3": float(atoms.get_volume()),
        "cell_volume_bohr3": float(ions.cell.volume),
        "grid_dv_bohr3": float(ions.cell.volume / np.prod(nr)),
        "dftpy_bohr_to_angstrom": float(Units.Bohr),
        "volume_per_atom_angstrom3": float(atoms.get_volume() / ions.nat),
        "grid": nr.tolist(),
        "density_unit": "electron_per_Bohr3",
        "density_sha256": sha256(run_directory / "density.npy"),
        "electron_count_expected": float(row["expected_electrons"]),
        "electron_count_reported": electron_count,
        "electron_count_abs_error": abs(electron_count - float(row["expected_electrons"])),
        "energy_ev": energy_ev,
        "energy_ev_per_atom": energy_ev / ions.nat,
        "energy_components_ev": {
            key: float(value.energy * ENERGY_CONV["Hartree"]["eV"])
            for key, value in sorted(parts.items())
        },
        "stress_gpa": stress_gpa.tolist(),
        "pressure_gpa": pressure_gpa,
        "pressure_sign_convention": "negative_trace_of_DFTpy_stress_over_three",
        "wall_seconds": time.time() - started,
        "runtime": {
            "hostname": socket.gethostname(),
            "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
            "slurm_job_name": os.environ.get("SLURM_JOB_NAME"),
            "cpu_affinity": affinity,
            "omp_num_threads": os.environ.get("OMP_NUM_THREADS"),
            "mkl_num_threads": os.environ.get("MKL_NUM_THREADS"),
            "openblas_num_threads": os.environ.get("OPENBLAS_NUM_THREADS"),
            "python": sys.version,
            "python_executable": sys.executable,
            "dftpy_version": str(getattr(dftpy, "__version__", "unknown")),
            "pylibxc_version": str(getattr(pylibxc, "__version__", "unknown")),
            "numpy_version": np.__version__,
        },
        "inputs": {
            "manifest_row": row,
            "stru_sha256": sha256(stru_path),
            "pseudopotential_sha256": sha256(pp_path),
            "abacus_result_sha256": sha256(abacus_result_path),
        },
        "abacus_reference_result": read_json(abacus_result_path),
    }
    write_json(run_directory / "result.json", result)
    write_json(run_directory / "manifest_row.json", row)
    if result["status"] != "accepted":
        raise RuntimeError(f"DFTpy did not converge for {row['experiment_id']}")


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--config",
        type=Path,
        default=project_root / "config/S1_g1_cross_code_dftpy.json",
    )
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--experiment-id", action="append", default=[])
    args = parser.parse_args()
    manifest = args.manifest.resolve()
    config_path = args.config.resolve()
    config = read_json(config_path)
    rows = read_manifest(manifest)
    if len(rows) != config["formal_run_count"]:
        raise ValueError("manifest count differs from frozen formal denominator")
    selected = set(args.experiment_id)
    if selected:
        unknown = selected - {row["experiment_id"] for row in rows}
        if unknown:
            raise ValueError(f"unknown experiment IDs: {sorted(unknown)}")
        rows = [row for row in rows if row["experiment_id"] in selected]
    output_root = (args.output_root or Path(config["external_state_root"])).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    existing = [row["experiment_id"] for row in rows if (output_root / row["experiment_id"]).exists()]
    if existing:
        raise FileExistsError(f"formal IDs already exist and cannot be retried: {existing}")
    status = _git(project_root, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise ValueError("formal cross-code run requires a clean preregistered worktree")
    environment = validate_environment(project_root, config)
    runner_commit = _git(project_root, "rev-parse", "HEAD")
    failures = []
    completed = []
    for row in rows:
        experiment_id = row["experiment_id"]
        temp_path = Path(tempfile.mkdtemp(prefix=f".{experiment_id}.", dir=output_root))
        staged = temp_path / experiment_id
        try:
            run_point(project_root, row, config, staged)
        except Exception as exc:
            staged.mkdir(parents=True, exist_ok=True)
            failure = {
                "experiment_id": experiment_id,
                "status": "failed",
                "exception": repr(exc),
                "traceback": traceback.format_exc(),
            }
            write_json(staged / "failure.json", failure)
            failures.append(failure)
        final = output_root / experiment_id
        staged.rename(final)
        temp_path.rmdir()
        completed.append(experiment_id)
    summary = {
        "protocol_revision": config["protocol_revision"],
        "runner_commit": runner_commit,
        "manifest_path": str(manifest.relative_to(project_root)),
        "manifest_sha256": sha256(manifest),
        "config_path": str(config_path.relative_to(project_root)),
        "config_sha256": sha256(config_path),
        "environment": environment,
        "selected_ids": completed,
        "completed_count": len(completed),
        "failure_count": len(failures),
        "failures": failures,
    }
    summary_path = output_root / "runner_summary.json"
    if summary_path.exists():
        raise FileExistsError(f"runner summary already exists: {summary_path}")
    write_json(summary_path, summary)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
