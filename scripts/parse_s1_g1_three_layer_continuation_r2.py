#!/usr/bin/env python3
"""Parse KS-NL output with independent geometry, occupation and stress gates."""

from __future__ import annotations

import argparse
import json
import math
import re
from decimal import Decimal
from pathlib import Path

from s1_g1_thermodynamic_label_common import parse_abacus_cube
from s1_g1_three_layer_continuation_r2_common import (
    canonical_json_bytes,
    file_identity,
    find_project_root,
    load_config,
    read_json,
    read_text,
    require,
    sha256_file,
    validate_pseudo,
)


FLOAT = r"[+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?"
ENERGY_ROW = re.compile(rf"^\s*(E_[A-Za-z_]+(?:\([^\s()]+\))?)\s+({FLOAT})\s+({FLOAT})\s*$")
FINAL_ENERGY = re.compile(rf"!FINAL_ETOT_IS\s+({FLOAT})\s+eV")
PRESSURE = re.compile(rf"#TOTAL-PRESSURE#.*?:\s*({FLOAT})\s+kbar")
ELECTRONS = re.compile(rf"Autoset the number of electrons\s*=\s*({FLOAT})")
ATOM_COUNT = re.compile(r"TOTAL ATOM NUMBER\s*=\s*([0-9]+)")
PROJECTORS = re.compile(r"TOTAL NUMBER OF NONLOCAL PROJECTORS\s*=\s*([0-9]+)")
NBANDS = re.compile(r"Number of electronic states \(NBANDS\)\s*=\s*([0-9]+)")
KPOINT_HEADER = re.compile(r"\s*spin=\d+\s+k-point=(\d+)/(\d+)\b")
STRESS_HEADER = re.compile(r"^\s*Stress_x\s+Stress_y\s+Stress_z\s*$")
FORBIDDEN_WARNING = re.compile(r"(?:\bfatal\b|\berror\b|segmentation|not converged|\bnan\b|\binf\b)", re.I)


def last(matches: list[str], label: str) -> Decimal:
    require(matches, f"missing {label}")
    value = Decimal(matches[-1])
    require(value.is_finite(), f"non-finite {label}")
    return value


def parse_affinity(run_dir: Path, config: dict) -> list[dict]:
    rank_count = int(config["runtime"]["rank_count"])
    expected_cores = list(config["runtime"]["physical_core_ids"])
    rows: list[dict] = []
    for rank in range(rank_count):
        path = run_dir / "affinity" / f"rank_{rank:03d}.json"
        payload = read_json(path)
        require(isinstance(payload, dict), "affinity payload must be object")
        require(payload.get("rank") == rank and payload.get("local_rank") == rank, "rank identity differs")
        require(payload.get("hostname") == config["runtime"]["required_hostname"], "rank hostname differs")
        require(payload.get("physical_core_ids") == [expected_cores[rank]], "rank physical core differs")
        require(payload.get("accepted") is True, "rank affinity rejected")
        rows.append({**payload, "evidence_sha256": sha256_file(path)})
    require(sorted(row["physical_core_ids"][0] for row in rows) == expected_cores, "core set differs")
    return rows


def parse_stress(text: str, pressure_kbar: Decimal, config: dict) -> dict:
    lines = text.splitlines()
    headers = [index for index, line in enumerate(lines) if STRESS_HEADER.fullmatch(line)]
    require(headers, "missing stress tensor")
    rows: list[list[Decimal]] = []
    cursor = headers[-1] + 1
    while cursor < len(lines) and len(rows) < 3:
        fields = lines[cursor].split()
        if len(fields) == 3 and all(re.fullmatch(FLOAT, field) for field in fields):
            rows.append([Decimal(field) for field in fields])
        cursor += 1
    require(len(rows) == 3, "truncated stress tensor")
    require(all(value.is_finite() for row in rows for value in row), "non-finite stress tensor")
    trace_pressure = sum((rows[index][index] for index in range(3)), Decimal(0)) / Decimal(3)
    difference = abs(trace_pressure - pressure_kbar)
    limit = Decimal(str(config["acceptance"]["stress_trace_pressure_abs_difference_kbar_strictly_less_than"]))
    require(difference < limit, "stress trace/pressure gate failed")
    return {
        "tensor_kbar": [[float(value) for value in row] for row in rows],
        "trace_pressure_kbar": float(trace_pressure),
        "reported_pressure_kbar": float(pressure_kbar),
        "abs_difference_kbar": float(difference),
        "strict_limit_kbar": float(limit),
        "accepted": True,
    }


def parse_eig_occ(path: Path, nbands: int, expected_electrons: float, config: dict) -> dict:
    text = read_text(path)
    expected_kpoints: int | None = None
    current_count = 0
    completed_counts: list[int] = []
    occupations: list[Decimal] = []
    last_band_occupations: list[Decimal] = []
    for line in text.splitlines():
        header = KPOINT_HEADER.match(line)
        if header is not None:
            if expected_kpoints is not None:
                completed_counts.append(current_count)
            expected_kpoints = int(header.group(2))
            require(int(header.group(1)) == len(completed_counts) + 1, "eig_occ k-point order differs")
            current_count = 0
            continue
        fields = line.split()
        if expected_kpoints is None or len(fields) != 3 or not fields[0].isdigit():
            continue
        band = int(fields[0])
        require(band == current_count + 1, "eig_occ band order differs")
        occupation = Decimal(fields[2])
        require(occupation.is_finite() and occupation >= 0, "invalid eig_occ occupation")
        occupations.append(occupation)
        current_count += 1
        if band == nbands:
            last_band_occupations.append(occupation)
    require(expected_kpoints is not None, "eig_occ contains no k-points")
    completed_counts.append(current_count)
    require(len(completed_counts) == expected_kpoints, "eig_occ k-point count differs")
    require(all(count == nbands for count in completed_counts), "eig_occ NBANDS coverage differs")
    require(len(last_band_occupations) == expected_kpoints, "eig_occ last-band coverage differs")
    occupation_sum = sum(occupations, Decimal(0))
    sum_error = abs(occupation_sum - Decimal(str(expected_electrons)))
    last_max = max(abs(value) for value in last_band_occupations)
    sum_limit = Decimal(str(config["acceptance"]["eig_occupation_sum_abs_error_strictly_less_than"]))
    last_limit = Decimal(str(config["acceptance"]["last_band_weighted_occupation_abs_max_strictly_less_than"]))
    require(sum_error < sum_limit, "eig_occ electron-sum gate failed")
    require(last_max < last_limit, "eig_occ last-band occupation gate failed")
    return {
        "kpoint_count": expected_kpoints,
        "nbands": nbands,
        "weighted_occupation_sum": float(occupation_sum),
        "sum_abs_error_electrons": float(sum_error),
        "sum_strict_limit_electrons": float(sum_limit),
        "last_band_weighted_occupation_abs_max": float(last_max),
        "last_band_strict_limit": float(last_limit),
        "sha256": sha256_file(path),
        "accepted": True,
    }


def direct_cartesian_positions_bohr(path: Path) -> list[tuple[float, float, float]]:
    lines = [line.strip() for line in read_text(path).splitlines() if line.strip()]
    lattice_index = lines.index("LATTICE_CONSTANT")
    vectors_index = lines.index("LATTICE_VECTORS")
    positions_index = lines.index("ATOMIC_POSITIONS")
    lattice_constant = float(lines[lattice_index + 1].split()[0])
    vectors = [[float(value) for value in lines[vectors_index + offset].split()] for offset in (1, 2, 3)]
    require(lines[positions_index + 1].lower() == "direct", "only Direct positions are registered")
    cursor = positions_index + 2
    positions: list[tuple[float, float, float]] = []
    while cursor < len(lines):
        cursor += 2
        count = int(lines[cursor].split()[0])
        cursor += 1
        for row in lines[cursor : cursor + count]:
            fractional = [float(value) for value in row.split()[:3]]
            positions.append(tuple(lattice_constant * sum(fractional[j] * vectors[j][i] for j in range(3)) for i in range(3)))
        cursor += count
    return positions


def validate_cube_atoms(cube: object, stru_path: Path, material: str, pseudo_identity: dict, config: dict) -> dict:
    rows = cube.atom_rows
    expected_positions = direct_cartesian_positions_bohr(stru_path)
    require(len(rows) == len(expected_positions), "cube/STRU coordinate count differs")
    atomic_number = int(config["materials"][material]["atomic_number"])
    zval = float(pseudo_identity["z_valence"])
    limit = float(config["acceptance"]["cube_atom_coordinate_abs_difference_bohr_strictly_less_than"])
    maximum = 0.0
    for row, expected in zip(rows, expected_positions):
        require(int(row[0]) == atomic_number, "cube atomic number differs")
        require(abs(float(row[1]) - zval) < 1e-12, "cube zval differs")
        maximum = max(maximum, *(abs(float(row[index + 2]) - expected[index]) for index in range(3)))
    require(maximum < limit, "cube atom coordinates differ from STRU")
    return {
        "origin_bohr": [float(value) for value in cube.origin_bohr],
        "dimensions": list(cube.dimensions),
        "axis_steps_bohr": [[float(value) for value in row] for row in cube.axis_steps_bohr],
        "atom_rows": [[float(value) for value in row] for row in rows],
        "max_atom_coordinate_abs_difference_bohr": maximum,
        "strict_limit_bohr": limit,
        "accepted": True,
    }


def parse_run(run_dir: Path, config: dict) -> dict:
    metadata = read_json(run_dir / "metadata.json")
    require(isinstance(metadata, dict), "metadata must be object")
    material = metadata["material"]
    pseudo_path = run_dir / metadata["pseudo"]["basename"]
    pseudo_identity = validate_pseudo(pseudo_path, material, config)
    suffix = metadata["suffix"]
    output_dir = run_dir / f"OUT.{suffix}"
    log_path = output_dir / "running_scf.log"
    density_path = output_dir / "chg.cube"
    text = read_text(log_path)
    require(text.count("#SCF IS CONVERGED#") == 1, "expected one converged marker")
    require("!!SCF IS NOT CONVERGED!!" not in text, "contradictory nonconvergence marker")
    energy_rows: dict[str, list[Decimal]] = {}
    for line in text.splitlines():
        match = ENERGY_ROW.fullmatch(line)
        if match is not None:
            energy_rows.setdefault(match.group(1), []).append(Decimal(match.group(3)))
    for key in ("E_KohnSham", "E_KS(sigma->0)", "E_entropy(-TS)", "E_Fermi"):
        require(key in energy_rows and energy_rows[key], f"missing final {key}")
    final_f = last(FINAL_ENERGY.findall(text), "!FINAL_ETOT_IS")
    kohn_sham = energy_rows["E_KohnSham"][-1]
    e_ec = energy_rows["E_KS(sigma->0)"][-1]
    minus_ts = energy_rows["E_entropy(-TS)"][-1]
    chemical_potential = energy_rows["E_Fermi"][-1]
    pressure_kbar = last(PRESSURE.findall(text), "pressure")
    electrons_reported = last(ELECTRONS.findall(text), "reported electrons")
    atom_matches = ATOM_COUNT.findall(text)
    require(len(atom_matches) == 1, "expected exactly one atom count")
    atom_count = int(atom_matches[0])
    require(atom_count == int(metadata["atom_count"]), "log atom count differs")
    projector_matches = PROJECTORS.findall(text)
    require(projector_matches, "missing nonlocal projector count")
    total_projectors = int(projector_matches[-1])
    expected_projectors = int(pseudo_identity["expanded_nonlocal_projectors_per_atom"]) * atom_count
    require(total_projectors == expected_projectors, "runtime projector count differs")
    nbands_matches = NBANDS.findall(text)
    require(len(nbands_matches) == 1, "expected exactly one NBANDS declaration")
    nbands = int(nbands_matches[0])
    require(nbands == int(config["pseudodojo"]["materials"][material]["expected_nbands"]), "NBANDS differs")
    require(abs(final_f - kohn_sham) / atom_count < Decimal("1e-8"), "F table identity failed")
    require(minus_ts <= 0, "m=-TS must be nonpositive")
    internal = final_f - minus_ts
    estimator_from_labels = final_f - minus_ts / 2
    require(abs(e_ec - estimator_from_labels) / atom_count < Decimal("1e-8"), "E_ec identity failed")
    require(final_f <= e_ec <= internal, "expected F <= E_ec <= U")
    density = parse_abacus_cube(
        density_path,
        quantity="electron_density",
        units="electron_per_bohr3",
        structure_path=run_dir / "STRU",
    )
    integrated = density.voxel_volume_bohr3 * math.fsum(density.values)
    expected_electrons = float(metadata["expected_electrons"])
    zval_electrons = float(pseudo_identity["z_valence"]) * atom_count
    require(abs(zval_electrons - expected_electrons) < 1e-12, "UPF zval*nat differs from expected Ne")
    relative_error = abs(integrated - expected_electrons) / expected_electrons
    limit = float(config["acceptance"]["electron_relative_error_strictly_less_than"])
    require(relative_error < limit, "independent cube electron-number gate failed")
    require(abs(float(electrons_reported) - expected_electrons) < 1e-12, "reported electron count differs")
    cube_geometry = validate_cube_atoms(density, run_dir / "STRU", material, pseudo_identity, config)
    eig_occ_path = output_dir / "eig_occ.txt"
    occupations = parse_eig_occ(eig_occ_path, nbands, expected_electrons, config)
    warning_path = output_dir / "warning.log"
    warning_text = read_text(warning_path)
    auto_nbands = re.findall(r"AUTO_SET NBANDS to\s+([0-9]+)", warning_text)
    require(auto_nbands == [str(nbands)], "warning.log NBANDS differs")
    require(FORBIDDEN_WARNING.search(warning_text) is None, "warning.log contains forbidden marker")
    stress = parse_stress(text, pressure_kbar, config)
    affinities = parse_affinity(run_dir, config)
    evidence_paths = [
        run_dir / "INPUT",
        run_dir / "STRU",
        run_dir / "KPT",
        run_dir / "metadata.json",
        run_dir / "run.stdout",
        run_dir / "run.stderr",
        log_path,
        density_path,
        eig_occ_path,
        warning_path,
    ] + [run_dir / "affinity" / f"rank_{rank:03d}.json" for rank in range(len(affinities))]
    require(all(path.is_file() for path in evidence_paths), "missing per-run evidence")
    return {
        "schema_version": 1,
        "protocol_revision": config["protocol_revision"],
        "status": "accepted",
        "experiment_id": metadata["experiment_id"],
        "phase": metadata["phase"],
        "requirement": metadata["requirement"],
        "material": material,
        "volume_ratio": float(metadata["volume_ratio"]),
        "role": metadata["role"],
        "atom_count": atom_count,
        "expected_electrons": expected_electrons,
        "runtime_nonlocal_projectors_total": total_projectors,
        "nbands": nbands,
        "pseudo_identity": pseudo_identity,
        "thermodynamic_labels_ev_per_cell": {
            "F": float(final_f),
            "m": float(minus_ts),
            "U": float(internal),
            "E_ec": float(e_ec),
            "mu": float(chemical_potential),
        },
        "thermodynamic_labels_ev_per_atom": {
            "F": float(final_f) / atom_count,
            "m": float(minus_ts) / atom_count,
            "U": float(internal) / atom_count,
            "E_ec": float(e_ec) / atom_count,
            "mu": float(chemical_potential),
        },
        "pressure_kbar": float(pressure_kbar),
        "pressure_gpa": float(pressure_kbar) / 10.0,
        "electron_number": {
            "upf_zval_times_atom_count": zval_electrons,
            "reported": float(electrons_reported),
            "integrated_cube": integrated,
            "integrated_eig_occ": occupations["weighted_occupation_sum"],
            "relative_error": relative_error,
            "strict_limit": limit,
            "accepted": True,
            "cube_grid": list(density.dimensions),
            "cube_voxel_volume_bohr3": density.voxel_volume_bohr3,
        },
        "cube_geometry": cube_geometry,
        "eigen_occupations": occupations,
        "stress_trace_gate": stress,
        "warning_log": {"sha256": sha256_file(warning_path), "forbidden_marker_present": False, "auto_nbands": nbands, "accepted": True},
        "affinity": {
            "rank_count": len(affinities),
            "physical_core_ids": [row["physical_core_ids"][0] for row in affinities],
            "accepted": True,
            "ranks": affinities,
        },
        "semantic_limits": {
            "zero_temperature_exact_claim": False,
            "entropy_corrected_estimator": "finite-smearing E_ec=F-m/2",
            "local_only_T_sU_identity_applied": False,
            "nonlocal_kinetic_decomposition_claim": False,
        },
        "evidence_files": [file_identity(path, relative_to=run_dir) for path in evidence_paths],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    project_root = find_project_root(args.project_root)
    config = load_config(project_root)
    result = parse_run(args.run_dir.resolve(), config)
    output = args.output or args.run_dir / "result.json"
    output.write_bytes(canonical_json_bytes(result))
    print(json.dumps({"experiment_id": result["experiment_id"], "status": result["status"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
