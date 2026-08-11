#!/usr/bin/env python3
"""Deterministically replay R1 P0 base and enhanced raw gates for follow-up R3."""

from __future__ import annotations

import math
import os
import re
import shutil
import tempfile
from decimal import Decimal
from pathlib import Path

from parse_s1_g1_three_layer_r1 import parse_run as parse_r1_run
from s1_electron_number_common import parse_stru
from s1_g1_thermodynamic_label_common import parse_abacus_cube
from s1_g1_three_layer_al_followup_r3_common import (
    canonical_json_bytes,
    file_identity,
    read_json,
    read_text,
    require,
    sha256_bytes,
    sha256_file,
    validate_pseudo,
)


FLOAT = r"[+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?"
PRESSURE = re.compile(rf"#TOTAL-PRESSURE#.*?:\s*({FLOAT})\s+kbar")
ELECTRONS = re.compile(rf"Autoset the number of electrons\s*=\s*({FLOAT})")
NBANDS = re.compile(r"Number of electronic states \(NBANDS\)\s*=\s*([0-9]+)")
KPOINT_HEADER = re.compile(r"\s*spin=\d+\s+k-point=(\d+)/(\d+)\b")
STRESS_HEADER = re.compile(r"^\s*Stress_x\s+Stress_y\s+Stress_z\s*$")
FORBIDDEN_WARNING = re.compile(r"(?:\bfatal\b|\berror\b|segmentation|not converged|\bnan\b|\binf\b)", re.I)


def parse_eig_occ(path: Path, nbands: int, expected_electrons: float, config: dict) -> dict:
    expected_kpoints: int | None = None
    current_count = 0
    completed_counts: list[int] = []
    occupations: list[Decimal] = []
    last_band_occupations: list[Decimal] = []
    for line in read_text(path).splitlines():
        header = KPOINT_HEADER.match(line)
        if header is not None:
            if expected_kpoints is not None:
                completed_counts.append(current_count)
            expected_kpoints = int(header.group(2))
            require(int(header.group(1)) == len(completed_counts) + 1, "R1 eig_occ k-point order differs")
            current_count = 0
            continue
        fields = line.split()
        if expected_kpoints is None or len(fields) != 3 or not fields[0].isdigit():
            continue
        band = int(fields[0])
        require(band == current_count + 1, "R1 eig_occ band order differs")
        occupation = Decimal(fields[2])
        require(occupation.is_finite() and occupation >= 0, "R1 eig_occ occupation invalid")
        occupations.append(occupation)
        current_count += 1
        if band == nbands:
            last_band_occupations.append(occupation)
    require(expected_kpoints is not None, "R1 eig_occ contains no k-points")
    completed_counts.append(current_count)
    require(len(completed_counts) == expected_kpoints, "R1 eig_occ k-point count differs")
    require(all(count == nbands for count in completed_counts), "R1 eig_occ NBANDS coverage differs")
    require(len(last_band_occupations) == expected_kpoints, "R1 eig_occ last-band coverage differs")
    occupation_sum = sum(occupations, Decimal(0))
    sum_error = abs(occupation_sum - Decimal(str(expected_electrons)))
    last_max = max(abs(value) for value in last_band_occupations)
    sum_limit = Decimal(str(config["acceptance"]["eig_occupation_sum_abs_error_strictly_less_than"]))
    last_limit = Decimal(str(config["acceptance"]["last_band_weighted_occupation_abs_max_strictly_less_than"]))
    require(sum_error < sum_limit, "R1 eig_occ electron-sum gate failed")
    require(last_max < last_limit, "R1 eig_occ last-band gate failed")
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


def parse_stress(text: str, pressure_kbar: Decimal, config: dict) -> dict:
    lines = text.splitlines()
    headers = [index for index, line in enumerate(lines) if STRESS_HEADER.fullmatch(line)]
    require(headers, "R1 stress tensor missing")
    rows: list[list[Decimal]] = []
    cursor = headers[-1] + 1
    while cursor < len(lines) and len(rows) < 3:
        fields = lines[cursor].split()
        if len(fields) == 3 and all(re.fullmatch(FLOAT, field) for field in fields):
            rows.append([Decimal(field) for field in fields])
        cursor += 1
    require(len(rows) == 3 and all(value.is_finite() for row in rows for value in row), "R1 stress tensor invalid")
    trace_pressure = sum((rows[index][index] for index in range(3)), Decimal(0)) / Decimal(3)
    difference = abs(trace_pressure - pressure_kbar)
    limit = Decimal(str(config["acceptance"]["stress_trace_pressure_abs_difference_kbar_strictly_less_than"]))
    symmetry = max(abs(rows[i][j] - rows[j][i]) for i in range(3) for j in range(3))
    symmetry_limit = Decimal(str(config["acceptance"]["stress_symmetry_abs_difference_kbar_strictly_less_than"]))
    require(difference < limit and symmetry < symmetry_limit, "R1 stress/pressure gate failed")
    return {
        "tensor_kbar": [[float(value) for value in row] for row in rows],
        "trace_pressure_kbar": float(trace_pressure),
        "reported_pressure_kbar": float(pressure_kbar),
        "abs_difference_kbar": float(difference),
        "strict_limit_kbar": float(limit),
        "symmetry_abs_difference_kbar_max": float(symmetry),
        "symmetry_strict_limit_kbar": float(symmetry_limit),
        "accepted": True,
    }


def direct_cartesian_positions_bohr(path: Path) -> list[tuple[float, float, float]]:
    lines = [line.strip() for line in read_text(path).splitlines() if line.strip()]
    lattice_index = lines.index("LATTICE_CONSTANT")
    vectors_index = lines.index("LATTICE_VECTORS")
    positions_index = lines.index("ATOMIC_POSITIONS")
    lattice_constant = float(lines[lattice_index + 1].split()[0])
    vectors = [[float(value) for value in lines[vectors_index + offset].split()] for offset in (1, 2, 3)]
    require(lines[positions_index + 1].lower() == "direct", "R1 replay requires Direct positions")
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


def validate_cube_atoms(cube: object, stru_path: Path, material: str, pseudo: dict, config: dict) -> dict:
    rows = cube.atom_rows
    expected_positions = direct_cartesian_positions_bohr(stru_path)
    require(len(rows) == len(expected_positions), "R1 cube/STRU atom count differs")
    atomic_number = int(config["materials"][material]["atomic_number"])
    zval = float(pseudo["z_valence"])
    limit = float(config["acceptance"]["cube_atom_coordinate_abs_difference_bohr_strictly_less_than"])
    lattice_limit = float(config["acceptance"]["cube_lattice_component_abs_difference_bohr_strictly_less_than"])
    maximum = 0.0
    for row, expected in zip(rows, expected_positions):
        require(int(row[0]) == atomic_number and abs(float(row[1]) - zval) < 1e-12, "R1 cube atom/zval differs")
        maximum = max(maximum, *(abs(float(row[index + 2]) - expected[index]) for index in range(3)))
    require(maximum < limit, "R1 cube atom coordinates differ")
    structure = parse_stru(stru_path)
    observed_lattice = [[float(cube.axis_steps_bohr[i][j]) * int(cube.dimensions[i]) for j in range(3)] for i in range(3)]
    expected_lattice = [[structure.lattice_vectors[i][j] * structure.lattice_constant_bohr for j in range(3)] for i in range(3)]
    lattice_maximum = max(abs(observed_lattice[i][j] - expected_lattice[i][j]) for i in range(3) for j in range(3))
    require(lattice_maximum < lattice_limit, "R1 cube lattice differs")
    return {
        "origin_bohr": [float(value) for value in cube.origin_bohr],
        "dimensions": list(cube.dimensions),
        "axis_steps_bohr": [[float(value) for value in row] for row in cube.axis_steps_bohr],
        "axis_times_dimensions_bohr": observed_lattice,
        "stru_lattice_bohr": expected_lattice,
        "max_lattice_component_abs_difference_bohr": lattice_maximum,
        "lattice_component_strict_limit_bohr": lattice_limit,
        "atom_rows": [[float(value) for value in row] for row in rows],
        "max_atom_coordinate_abs_difference_bohr": maximum,
        "strict_limit_bohr": limit,
        "accepted": True,
    }


def enhanced_raw_checks(run_dir: Path, material: str, config: dict, reparsed: dict) -> dict:
    metadata = read_json(run_dir / "metadata.json")
    require(isinstance(metadata, dict), "R1 metadata must be object")
    out = run_dir / f"OUT.{metadata['suffix']}"
    require(out.is_dir() and not out.is_symlink(), "R1 registered OUT directory missing")
    require([path for path in run_dir.glob("OUT.*") if path.is_dir()] == [out], "R1 OUT directory denominator differs")
    log_path = out / "running_scf.log"
    text = read_text(log_path)
    nbands_rows = NBANDS.findall(text)
    require(nbands_rows == [str(config["pseudodojo"]["materials"][material]["expected_nbands"])], "R1 NBANDS differs")
    nbands = int(nbands_rows[0])
    pseudo = validate_pseudo(run_dir / metadata["pseudo"]["basename"], material, config)
    atom_count = int(metadata["atom_count"])
    expected_ne = float(metadata["expected_electrons"])
    require(abs(float(pseudo["z_valence"]) * atom_count - expected_ne) < 1e-12, "R1 UPF zval*nat gate failed")
    reported_rows = ELECTRONS.findall(text)
    require(reported_rows and abs(float(reported_rows[-1]) - expected_ne) < 1e-12, "R1 raw log Ne gate failed")
    cube_path = out / "chg.cube"
    cube = parse_abacus_cube(cube_path, quantity="electron_density", units="electron_per_bohr3", structure_path=run_dir / "STRU")
    integrated = cube.voxel_volume_bohr3 * math.fsum(cube.values)
    cube_relative_error = abs(integrated - expected_ne) / expected_ne
    require(cube_relative_error < float(config["acceptance"]["electron_relative_error_strictly_less_than"]), "R1 raw cube Ne gate failed")
    cube_geometry = validate_cube_atoms(cube, run_dir / "STRU", material, pseudo, config)
    occupation = parse_eig_occ(out / "eig_occ.txt", nbands, expected_ne, config)
    pressure_rows = PRESSURE.findall(text)
    require(pressure_rows, "R1 raw pressure missing")
    stress = parse_stress(text, Decimal(pressure_rows[-1]), config)
    warning_path = out / "warning.log"
    warning_text = read_text(warning_path)
    require(re.findall(r"AUTO_SET NBANDS to\s+([0-9]+)", warning_text) == [str(nbands)], "R1 warning NBANDS differs")
    require(FORBIDDEN_WARNING.search(warning_text) is None, "R1 warning contains forbidden marker")
    require(reparsed["affinity"]["accepted"] is True, "R1 raw affinity gate failed")
    require(reparsed["runtime_nonlocal_projectors_total"] == int(pseudo["expanded_nonlocal_projectors_per_atom"]) * atom_count, "R1 projector gate failed")
    evidence = [run_dir / name for name in (
        "INPUT", "STRU", "KPT", "input_metadata.json", "metadata.json", "pseudo_identity.json",
        "run.stdout", "run.stderr", "runner_return.json", "result.json",
    )]
    evidence.extend(run_dir / "affinity" / f"rank_{rank:03d}.json" for rank in range(int(config["runtime"]["rank_count"])))
    evidence.extend((log_path, cube_path, out / "eig_occ.txt", warning_path))
    return {
        "upf_zval_times_atom_count": float(pseudo["z_valence"]) * atom_count,
        "expected_electrons": expected_ne,
        "reported_electrons": float(reported_rows[-1]),
        "integrated_cube_electrons": integrated,
        "cube_relative_error": cube_relative_error,
        "eig_occupations": occupation,
        "cube_geometry": cube_geometry,
        "stress_trace_gate": stress,
        "nbands": nbands,
        "warning_log": {"sha256": sha256_file(warning_path), "forbidden_marker_present": False, "accepted": True},
        "pseudo_identity": pseudo,
        "external_upf_identity": file_identity(run_dir / metadata["pseudo"]["basename"], relative_to=run_dir),
        "affinity": reparsed["affinity"],
        "evidence_files": [file_identity(path, relative_to=run_dir) for path in evidence],
        "accepted": True,
    }


def _hardlink_or_copy(source: str, destination: str) -> str:
    source_path = Path(source)
    require(source_path.is_file() and not source_path.is_symlink(), f"unsafe R1 replay source: {source_path}")
    try:
        os.link(source_path, destination)
    except OSError:
        shutil.copyfile(source_path, destination)
    return destination


def replay_r1_p0(run_dir: Path, r1_config: dict, continuation_config: dict) -> tuple[dict, dict, dict]:
    metadata = read_json(run_dir / "metadata.json")
    require(isinstance(metadata, dict), "R1 replay metadata must be object")
    material = metadata["material"]
    basename = continuation_config["pseudodojo"]["materials"][material]["basename"]
    cache_path = Path(continuation_config["external_pseudo_cache"]) / basename
    validated = validate_pseudo(cache_path, material, continuation_config)
    require(validated["sha256"] == continuation_config["pseudodojo"]["materials"][material]["sha256"], "R1 replay external UPF differs")
    for path in run_dir.rglob("*"):
        require(not path.is_symlink(), f"R1 snapshot contains symlink: {path}")
    with tempfile.TemporaryDirectory(prefix="g1_al_followup_r1_p0_replay_") as temporary:
        replay_dir = Path(temporary) / "run"
        shutil.copytree(run_dir, replay_dir, copy_function=_hardlink_or_copy, ignore=shutil.ignore_patterns(basename))
        shutil.copyfile(cache_path, replay_dir / basename)
        base = parse_r1_run(replay_dir, r1_config)
        require(canonical_json_bytes(base) == (run_dir / "result.json").read_bytes(), "committed R1 base parser replay differs")
        enhanced = enhanced_raw_checks(replay_dir, material, continuation_config, base)
    return base, enhanced, {
        "base_parser": "parse_s1_g1_three_layer_r1.parse_run",
        "base_reparsed_sha256": sha256_bytes(canonical_json_bytes(base)),
        "base_result_byte_exact": True,
        "enhanced_parser": "replay_s1_g1_three_layer_r1_p0_followup_r3.enhanced_raw_checks",
        "enhanced_reparsed_sha256": sha256_bytes(canonical_json_bytes(enhanced)),
        "external_pseudo_sha256": validated["sha256"],
        "accepted": True,
    }


__all__ = ["enhanced_raw_checks", "replay_r1_p0"]
