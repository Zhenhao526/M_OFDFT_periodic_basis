#!/usr/bin/env python3
"""Parse one Al-domain follow-up run and enforce all per-point hard gates."""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

import parse_s1_g1_three_layer_r1 as base_parser
from s1_electron_number_common import parse_stru
from s1_g1_thermodynamic_label_common import parse_abacus_cube
from s1_g1_three_layer_al_followup_r3_common import (
    canonical_json_bytes,
    file_identity,
    find_project_root,
    load_config,
    read_json,
    read_text,
    require,
    sha256_file,
)


FLOAT = r"[+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?"
NBANDS = re.compile(r"Number of electronic states \(NBANDS\)\s*=\s*([0-9]+)")
EIG_HEADER = re.compile(r"^\s*spin=1 k-point=([0-9]+)/([0-9]+)\b")
EIG_ROW = re.compile(rf"^\s*([0-9]+)\s+({FLOAT})\s+({FLOAT})\s*$")
THREE_FLOATS = re.compile(rf"^\s*({FLOAT})\s+({FLOAT})\s+({FLOAT})\s*$")
FORCE_ROW = re.compile(rf"^\s*([A-Za-z]+[0-9]+)\s+({FLOAT})\s+({FLOAT})\s+({FLOAT})\s*$")


def parse_eig_occ(path: Path, nbands: int, expected_electrons: float, config: dict) -> dict:
    lines = read_text(path).splitlines()
    blocks: list[tuple[int, int, list[tuple[int, float]]]] = []
    current: tuple[int, int, list[tuple[int, float]]] | None = None
    for line in lines:
        header = EIG_HEADER.match(line)
        if header is not None:
            if current is not None:
                blocks.append(current)
            current = (int(header.group(1)), int(header.group(2)), [])
            continue
        row = EIG_ROW.fullmatch(line)
        if row is not None and current is not None:
            current[2].append((int(row.group(1)), float(row.group(3))))
    if current is not None:
        blocks.append(current)
    require(blocks, "eig_occ contains no k-point blocks")
    total_k = blocks[0][1]
    require([block[0] for block in blocks] == list(range(1, total_k + 1)), "eig_occ k-point sequence differs")
    require(all(block[1] == total_k for block in blocks), "eig_occ k-point denominator differs")
    require(len(blocks) == total_k, "eig_occ k-point count differs")
    occupations: list[float] = []
    last_band: list[float] = []
    for _, _, rows in blocks:
        require([band for band, _ in rows] == list(range(1, nbands + 1)), "eig_occ band sequence differs")
        require(all(math.isfinite(value) and value >= 0.0 for _, value in rows), "invalid occupation")
        occupations.extend(value for _, value in rows)
        last_band.append(rows[-1][1])
    total = math.fsum(occupations)
    relative_error = abs(total - expected_electrons) / expected_electrons
    electron_limit = float(config["acceptance"]["electron_relative_error_strictly_less_than"])
    last_limit = float(config["acceptance"]["last_band_occupation_strictly_less_than"])
    maximum_last = max(last_band)
    require(relative_error < electron_limit, "eig_occ electron sum gate failed")
    require(maximum_last < last_limit, "last-band occupation gate failed")
    return {
        "nbands": nbands,
        "kpoint_count": total_k,
        "row_count": len(occupations),
        "occupation_sum": total,
        "electron_relative_error": relative_error,
        "electron_relative_error_strict_limit": electron_limit,
        "last_band_maximum_occupation": maximum_last,
        "last_band_occupation_strict_limit": last_limit,
        "accepted": True,
    }


def parse_force_stress(log_text: str, atom_count: int, pressure_kbar: float, config: dict) -> dict:
    lines = log_text.splitlines()
    force_markers = [index for index, line in enumerate(lines) if "#TOTAL-FORCE (eV/Angstrom)#" in line]
    stress_markers = [index for index, line in enumerate(lines) if "#TOTAL-STRESS (kbar)#" in line]
    require(force_markers and stress_markers and force_markers[-1] < stress_markers[-1], "missing final force/stress blocks")
    forces: list[dict] = []
    for line in lines[force_markers[-1] + 1 : stress_markers[-1]]:
        match = FORCE_ROW.fullmatch(line)
        if match is not None:
            forces.append({"atom": match.group(1), "ev_per_angstrom": [float(match.group(i)) for i in (2, 3, 4)]})
    require(len(forces) == atom_count and [row["atom"] for row in forces] == ["Al1"], "force atom order differs")
    stress: list[list[float]] = []
    for line in lines[stress_markers[-1] + 1 :]:
        if "#TOTAL-PRESSURE#" in line:
            break
        match = THREE_FLOATS.fullmatch(line)
        if match is not None:
            stress.append([float(match.group(i)) for i in (1, 2, 3)])
    require(len(stress) == 3, "final stress must be 3x3")
    require(all(math.isfinite(value) for row in stress for value in row), "non-finite stress")
    symmetry = max(abs(stress[i][j] - stress[j][i]) for i in range(3) for j in range(3))
    trace_pressure = abs(sum(stress[i][i] for i in range(3)) / 3.0 - pressure_kbar)
    symmetry_limit = float(config["acceptance"]["stress_symmetry_abs_kbar_max"])
    trace_limit = float(config["acceptance"]["stress_trace_pressure_abs_kbar_strictly_less_than"])
    require(symmetry <= symmetry_limit, "stress symmetry gate failed")
    require(trace_pressure < trace_limit, "stress trace/pressure gate failed")
    return {
        "forces": forces,
        "stress_kbar": stress,
        "stress_symmetry_max_abs_error_kbar": symmetry,
        "stress_trace_over_three_minus_pressure_abs_kbar": trace_pressure,
        "accepted": True,
    }


def require_zero_cube_origin(origin_bohr: tuple[float, float, float] | list[float]) -> float:
    """The ABACUS density cube contract is an exactly zero origin."""
    require(len(origin_bohr) == 3, "cube origin must contain three coordinates")
    maximum = max(abs(float(value)) for value in origin_bohr)
    require(maximum == 0.0, "cube origin differs from exact zero")
    return maximum


def parse_explicit_r3_affinity(run_dir: Path, config: dict) -> list[dict]:
    runtime = config["runtime"]
    rows: list[dict] = []
    for rank in range(int(runtime["rank_count"])):
        path = run_dir / "affinity" / f"rank_{rank:03d}.json"
        payload = read_json(path)
        require(isinstance(payload, dict), "R3 affinity payload must be object")
        require("physical_core_ids" not in payload and "expected_physical_core_id" not in payload, "ambiguous physical-core label is forbidden in R3 raw evidence")
        require(payload.get("rank") == rank and payload.get("local_rank") == rank, "R3 rank identity differs")
        require(payload.get("hostname") == runtime["required_hostname"], "R3 rank hostname differs")
        require(payload.get("os_logical_cpu_affinity") == runtime["thread_siblings_by_rank"][rank], "R3 OS logical affinity differs")
        require(payload.get("primary_os_logical_cpu_id") == runtime["primary_os_logical_cpu_ids_by_rank"][rank], "R3 primary OS CPU differs")
        require(payload.get("physical_package_id") == runtime["required_physical_package_id"], "R3 package differs")
        require(payload.get("sysfs_core_id") == runtime["sysfs_core_id_by_rank"][rank], "R3 /sys core_id differs")
        require(payload.get("thread_siblings") == runtime["thread_siblings_by_rank"][rank], "R3 thread siblings differ")
        require(payload.get("accepted") is True, "R3 rank affinity rejected")
        # The inherited parser constructs its old result shape from this in-memory
        # compatibility key. It is never written to raw evidence and is renamed
        # immediately after the inherited parse returns.
        rows.append({**payload, "physical_core_ids": [payload["sysfs_core_id"]], "evidence_sha256": sha256_file(path)})
    require([row["sysfs_core_id"] for row in rows] == runtime["sysfs_core_id_by_rank"], "R3 sysfs core_id denominator differs")
    return rows


def validate_cube_geometry(run_dir: Path, cube_path: Path, config: dict) -> dict:
    cube = parse_abacus_cube(cube_path, quantity="electron_density", units="electron_per_bohr3", structure_path=run_dir / "STRU")
    structure = parse_stru(run_dir / "STRU")
    origin_error = require_zero_cube_origin(cube.origin_bohr)
    expected = [[structure.lattice_constant_bohr * value for value in row] for row in structure.lattice_vectors]
    actual = [[float(cube.axis_steps_bohr[i][j]) * cube.dimensions[i] for j in range(3)] for i in range(3)]
    maximum = max(abs(actual[i][j] - expected[i][j]) for i in range(3) for j in range(3))
    tolerance = float(config["acceptance"]["cube_geometry_absolute_tolerance_bohr"])
    require(maximum < tolerance, "cube axis/STRU geometry gate failed")
    require(cube.atom_count == 1 and len(cube.atom_rows) == 1, "cube atom count differs")
    atom = [float(value) for value in cube.atom_rows[0]]
    require(abs(atom[0] - 13.0) < 1e-12 and abs(atom[1] - 3.0) < 1e-12, "cube Al identity/zval differs")
    atom_error = max(abs(value) for value in atom[2:])
    require(atom_error < tolerance, "cube Al1 coordinate differs")
    return {
        "origin_bohr": [float(value) for value in cube.origin_bohr],
        "maximum_origin_absolute_error_bohr": origin_error,
        "origin_exactly_zero": True,
        "grid": list(cube.dimensions),
        "axis_times_grid_bohr": actual,
        "stru_lattice_bohr": expected,
        "maximum_axis_absolute_error_bohr": maximum,
        "atom_order": ["Al1"],
        "maximum_atom_absolute_error_bohr": atom_error,
        "absolute_tolerance_bohr": tolerance,
        "accepted": True,
    }


def parse_run(run_dir: Path, config: dict, *, require_followup_orchestration: bool = True) -> dict:
    if require_followup_orchestration:
        original_affinity_parser = base_parser.parse_affinity
        base_parser.parse_affinity = parse_explicit_r3_affinity
        try:
            result = base_parser.parse_run(run_dir, config)
        finally:
            base_parser.parse_affinity = original_affinity_parser
        result["affinity"]["sysfs_core_ids"] = result["affinity"].pop("physical_core_ids")
        for row in result["affinity"]["ranks"]:
            row.pop("physical_core_ids", None)
    else:
        # The frozen R1/continuation source evidence predates R3 and retains its
        # registered legacy affinity schema. Replay it byte-exactly without
        # relabelling that read-only source as newly generated R3 evidence.
        result = base_parser.parse_run(run_dir, config)
    require(result["material"] == "al" and result["atom_count"] == 1, "follow-up is Al/one-atom only")
    pseudo = result["pseudo_identity"]
    require(pseudo["z_valence"] == 3.0, "UPF zval differs")
    require(pseudo["expanded_nonlocal_projectors_per_atom"] == 18, "UPF expanded projector count differs")
    require(result["runtime_nonlocal_projectors_total"] == 18, "runtime projector total differs")
    expected_electrons = float(result["expected_electrons"])
    require(abs(pseudo["z_valence"] * result["atom_count"] - expected_electrons) < 1e-12, "zval*nat differs from expected Ne")
    metadata = __import__("json").loads((run_dir / "metadata.json").read_text())
    require(isinstance(metadata, dict), "metadata must be an object")
    if require_followup_orchestration:
        require(metadata.get("protocol_revision") == config["protocol_revision"], "metadata protocol differs")
        orchestration = metadata.get("orchestration_identity")
        require(isinstance(orchestration, dict), "metadata orchestration identity missing")
        require(orchestration.get("protocol_revision") == config["protocol_revision"], "metadata orchestration protocol differs")
        require(orchestration.get("experiment_id") == metadata.get("experiment_id"), "metadata orchestration ID differs")
        require(orchestration.get("runner_commit") == metadata.get("runner_commit"), "metadata runner identity differs")
        require(isinstance(orchestration.get("runner_commit"), str) and len(orchestration["runner_commit"]) == 40, "metadata runner commit invalid")
        require(isinstance(orchestration.get("config_sha256"), str) and len(orchestration["config_sha256"]) == 64, "metadata config identity invalid")
        require(isinstance(orchestration.get("manifest_sha256"), str) and len(orchestration["manifest_sha256"]) == 64, "metadata manifest identity invalid")
        require(all(row.get("config_sha256") == orchestration["config_sha256"] for row in result["affinity"]["ranks"]), "rank-wrapper/config identity differs")
        wrapper_sha = sha256_file(Path(__file__).resolve().parent / "s1_g1_three_layer_al_followup_r3_rank_wrapper.py")
        require(all(row.get("rank_wrapper_sha256") == wrapper_sha for row in result["affinity"]["ranks"]), "rank-wrapper code identity differs")
    output_dir = run_dir / f"OUT.{metadata['suffix']}"
    log_path = output_dir / "running_scf.log"
    eig_path = output_dir / "eig_occ.txt"
    cube_path = output_dir / "chg.cube"
    text = read_text(log_path)
    nbands_matches = NBANDS.findall(text)
    require(len(nbands_matches) == 1, "expected exactly one NBANDS declaration")
    nbands = int(nbands_matches[0])
    require(nbands == int(config["acceptance"]["nbands_exact"]), "NBANDS differs")
    eig = parse_eig_occ(eig_path, nbands, expected_electrons, config)
    cube_geometry = validate_cube_geometry(run_dir, cube_path, config)
    mechanics = parse_force_stress(text, result["atom_count"], result["pressure_kbar"], config)
    require(abs(result["electron_number"]["reported"] - expected_electrons) < 1e-12, "log Ne differs")
    require(result["electron_number"]["relative_error"] < config["acceptance"]["electron_relative_error_strictly_less_than"], "cube Ne differs")
    result["independent_electron_identity"] = {
        "zval_times_atom_count": pseudo["z_valence"] * result["atom_count"],
        "log_reported": result["electron_number"]["reported"],
        "cube_integrated": result["electron_number"]["integrated_cube"],
        "eig_occ_sum": eig["occupation_sum"],
        "accepted": True,
    }
    result["band_occupation"] = eig
    result["cube_geometry"] = cube_geometry
    result["mechanics"] = mechanics
    if require_followup_orchestration:
        result["orchestration_identity"] = orchestration
    result["hard_gates"] = {
        "pseudo_header_sha_zval_projector18": True,
        "zval_nat_log_cube_eig_occ_electron_identity": True,
        "nbands_and_last_occupation": True,
        "cube_origin_geometry_and_atom_order": True,
        "force_stress_pressure": True,
        "thermodynamic_labels": True,
        "rank_affinity": result["affinity"]["accepted"],
    }
    known = {item["path"] for item in result["evidence_files"]}
    for path in (eig_path,):
        relative = path.relative_to(run_dir).as_posix()
        if relative not in known:
            result["evidence_files"].append(file_identity(path, relative_to=run_dir))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    project_root = find_project_root(args.project_root)
    result = parse_run(args.run_dir.resolve(), load_config(project_root))
    output = args.output or args.run_dir / "result.json"
    output.write_bytes(canonical_json_bytes(result))
    print(json.dumps({"experiment_id": result["experiment_id"], "status": result["status"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
