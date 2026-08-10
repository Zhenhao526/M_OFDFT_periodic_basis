#!/usr/bin/env python3
"""Analyze and freeze the 15-point displacement/strain reference evidence."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from s1_g1_thermodynamic_label_common import (
    json_safe,
    parse_abacus_cube,
    parse_kpt_text,
    parse_thermodynamic_log,
    require_same_cube_geometry,
)
from s1_electron_number_common import expected_electrons as independent_expected_electrons
from s1_electron_number_common import parse_stru as parse_stru_exact


PROTOCOL = "S1-G1-DISPLACEMENT-STRAIN-REFERENCE-ANALYSIS-R2"
SOURCE_EXECUTION_PROTOCOL = "S1-G1-DISPLACEMENT-STRAIN-REFERENCE-R1"
CONFIG_REL = Path("config/S1_g1_displacement_strain_reference_analysis_r2.json")
SOURCE_CONFIG_REL = Path("config/S1_g1_displacement_strain_reference_r1.json")
MANIFEST_REL = Path("config/S1_g1_displacement_strain_reference_r1_manifest.tsv")
BOHR_TO_ANGSTROM = 0.529177210903
FLOAT = r"[-+]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][-+]?\d+)?"
FORCE_ROW = re.compile(rf"^\s*([A-Za-z]+\d+)\s+({FLOAT})\s+({FLOAT})\s+({FLOAT})\s*$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    if len(rows) != 15:
        raise ValueError("manifest does not contain exactly 15 rows")
    return rows


def parse_forces_stress(text: str, expected_atoms: int) -> tuple[list[dict[str, object]], list[list[float]]]:
    lines = text.splitlines()
    force_markers = [i for i, line in enumerate(lines) if "#TOTAL-FORCE (eV/Angstrom)#" in line]
    stress_markers = [i for i, line in enumerate(lines) if "#TOTAL-STRESS (kbar)#" in line]
    if not force_markers or not stress_markers:
        raise ValueError("missing final force/stress block")
    forces: list[dict[str, object]] = []
    for line in lines[force_markers[-1] + 1 : stress_markers[-1]]:
        match = FORCE_ROW.fullmatch(line)
        if match:
            vector = [float(match.group(i)) for i in (2, 3, 4)]
            if not all(math.isfinite(value) for value in vector):
                raise ValueError("non-finite force")
            forces.append({"atom": match.group(1), "ev_per_angstrom": vector})
    if len(forces) != expected_atoms:
        raise ValueError(f"force row count differs: {len(forces)} != {expected_atoms}")
    stress: list[list[float]] = []
    for line in lines[stress_markers[-1] + 1 :]:
        fields = line.split()
        if len(fields) == 3:
            try:
                vector = [float(value) for value in fields]
            except ValueError:
                continue
            if all(math.isfinite(value) for value in vector):
                stress.append(vector)
                if len(stress) == 3:
                    break
    if len(stress) != 3:
        raise ValueError("stress tensor is incomplete")
    return forces, stress


def cube_geometry_audit(cube, metadata: dict) -> dict[str, object]:
    lattice = metadata["expected_lattice_vectors_bohr"]
    positions = metadata["expected_cartesian_positions_bohr"]
    lattice_errors = []
    for axis in range(3):
        reconstructed = [float(value) * cube.dimensions[axis] for value in cube.axis_steps_bohr[axis]]
        lattice_errors.extend(abs(reconstructed[j] - float(lattice[axis][j])) for j in range(3))
    if len(cube.atom_rows) != len(positions):
        raise ValueError("cube atom count differs from registered positions")
    raw_atom_errors = []
    minimum_image_errors = []
    wrapped_atom_rows = 0
    for row, expected in zip(cube.atom_rows, positions):
        delta_cart = [float(row[2 + j]) - float(expected[j]) for j in range(3)]
        raw_atom_errors.extend(abs(value) for value in delta_cart)
        delta_fractional = row_times_matrix(delta_cart, inverse(lattice))
        translation = [round(value) for value in delta_fractional]
        wrapped_fractional = [delta_fractional[i] - translation[i] for i in range(3)]
        minimum_image = row_times_matrix(wrapped_fractional, lattice)
        minimum_image_errors.extend(abs(value) for value in minimum_image)
        if any(value != 0 for value in translation):
            wrapped_atom_rows += 1
    return {
        "maximum_lattice_absolute_error_bohr": max(lattice_errors, default=0.0),
        "maximum_raw_atom_absolute_error_bohr": max(raw_atom_errors, default=0.0),
        "maximum_minimum_image_atom_absolute_error_bohr": max(minimum_image_errors, default=0.0),
        "maximum_accepted_geometry_error_bohr": max(
            max(lattice_errors, default=0.0), max(minimum_image_errors, default=0.0)
        ),
        "pbc_wrapped_atom_rows": wrapped_atom_rows,
        "atom_comparison": "delta_cartesian -> delta_fractional via inverse lattice -> subtract nearest integer -> Cartesian minimum image",
        "lattice_comparison": "absolute Cartesian; no PBC reduction",
    }


def precision_digits(token: str) -> int:
    mantissa = token.lower().split("e", 1)[0]
    return sum(character.isdigit() for character in mantissa)


def determinant(matrix: list[list[float]]) -> float:
    a, b, c = matrix
    return (
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - a[1] * (b[0] * c[2] - b[2] * c[0])
        + a[2] * (b[0] * c[1] - b[1] * c[0])
    )


def inverse(matrix: list[list[float]]) -> list[list[float]]:
    a, b, c = matrix
    det = determinant(matrix)
    if abs(det) <= 1.0e-14:
        raise ValueError("singular physical lattice")
    return [
        [(b[1]*c[2]-b[2]*c[1])/det, (a[2]*c[1]-a[1]*c[2])/det, (a[1]*b[2]-a[2]*b[1])/det],
        [(b[2]*c[0]-b[0]*c[2])/det, (a[0]*c[2]-a[2]*c[0])/det, (a[2]*b[0]-a[0]*b[2])/det],
        [(b[0]*c[1]-b[1]*c[0])/det, (a[1]*c[0]-a[0]*c[1])/det, (a[0]*b[1]-a[1]*b[0])/det],
    ]


def matrix_product(left: list[list[float]], right: list[list[float]]) -> list[list[float]]:
    return [[sum(left[i][k] * right[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def row_times_matrix(row: list[float], matrix: list[list[float]]) -> list[float]:
    return [sum(row[k] * matrix[k][j] for k in range(3)) for j in range(3)]


def max_vector_error(actual: list[float], expected: list[float]) -> float:
    return max(abs(actual[i] - expected[i]) for i in range(3))


def parse_stru_physical_geometry(path: Path) -> dict[str, object]:
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    ilc = lines.index("LATTICE_CONSTANT")
    ilv = lines.index("LATTICE_VECTORS")
    ipos = lines.index("ATOMIC_POSITIONS")
    lattice_constant_bohr = float(lines[ilc + 1])
    lattice_dimensionless = [[float(value) for value in lines[ilv + offset].split()] for offset in (1, 2, 3)]
    lattice_angstrom = [
        [value * lattice_constant_bohr * BOHR_TO_ANGSTROM for value in row]
        for row in lattice_dimensionless
    ]
    if lines[ipos + 1].lower() != "direct":
        raise ValueError(f"independent geometry gate requires Direct STRU: {path}")
    atom_count = int(lines[ipos + 4])
    fractional = [[float(value) for value in lines[ipos + 5 + i].split()[:3]] for i in range(atom_count)]
    cartesian = [row_times_matrix(position, lattice_angstrom) for position in fractional]
    return {
        "lattice_constant_bohr": lattice_constant_bohr,
        "lattice_angstrom": lattice_angstrom,
        "fractional_positions": fractional,
        "cartesian_positions_angstrom": cartesian,
        "atom_order": [f"{lines[ipos + 2]}{index + 1}" for index in range(atom_count)],
    }


def deformation_from_lattices(parent: list[list[float]], candidate: list[list[float]]) -> list[list[float]]:
    # A' = A F^T for row-wise lattice vectors.
    transpose = matrix_product(inverse(parent), candidate)
    return [[transpose[j][i] for j in range(3)] for i in range(3)]


def expected_deformation(kind: str, signed: float) -> list[list[float]]:
    if kind == "al_tetragonal":
        q = (1.0 + signed) ** -0.5
        return [[1.0 + signed, 0.0, 0.0], [0.0, q, 0.0], [0.0, 0.0, q]]
    if kind == "mg_axial":
        q = (1.0 + signed) ** -0.5
        return [[q, 0.0, 0.0], [0.0, q, 0.0], [0.0, 0.0, 1.0 + signed]]
    matrix = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    if kind == "al_shear_xy":
        matrix[0][1] = signed
    elif kind == "mg_shear_xz":
        matrix[0][2] = signed
    else:
        raise ValueError(f"unsupported independent strain geometry: {kind}")
    return matrix


def independent_geometry_audit(root: Path, state: Path, rows: list[dict[str, str]]) -> dict[str, dict[str, object]]:
    parent = {
        "al": parse_stru_physical_geometry(root / "runs/S1-20260807-043/STRU"),
        "mg": parse_stru_physical_geometry(root / "runs/S1-20260807-045/STRU"),
    }
    geometries = {
        row["experiment_id"]: parse_stru_physical_geometry(state / "runs" / row["experiment_id"] / "STRU")
        for row in rows
    }
    registrations: dict[str, dict[str, object]] = {}
    displacement_tolerance = 1.0e-9
    matrix_tolerance = 1.0e-12
    base = geometries["S1-20260810-201"]
    al_parent_lattice = parent["al"]["lattice_angstrom"]
    expected_base_lattice = [
        [2.0 * value for value in al_parent_lattice[0]],
        list(al_parent_lattice[1]),
        list(al_parent_lattice[2]),
    ]
    base_lattice_error = max(
        abs(base["lattice_angstrom"][i][j] - expected_base_lattice[i][j])
        for i in range(3) for j in range(3)
    )
    base_fractional_expected = [[0.0, 0.0, 0.0], [0.5, 0.0, 0.0]]
    base_fractional_error = max(
        abs(base["fractional_positions"][i][j] - base_fractional_expected[i][j])
        for i in range(2) for j in range(3)
    )
    if base_lattice_error >= matrix_tolerance or base_fractional_error >= matrix_tolerance:
        raise ValueError("independent Al 2x1x1 base geometry gate failed")
    registrations["S1-20260810-201"] = {
        "kind": "al_2x1x1_base",
        "atom_order": base["atom_order"],
        "base_lattice_max_abs_error_angstrom": base_lattice_error,
        "base_fractional_max_abs_error": base_fractional_error,
        "accepted": True,
    }
    for row in rows[1:]:
        experiment_id = row["experiment_id"]
        kind = row["perturbation_kind"]
        sign = 1.0 if row["sign"] == "+" else -1.0
        magnitude = float(row["amplitude"])
        candidate = geometries[experiment_id]
        if "displacement" in kind:
            reference = base if row["material"] == "al" else parent["mg"]
            actual = [
                candidate["cartesian_positions_angstrom"][1][i]
                - reference["cartesian_positions_angstrom"][1][i]
                for i in range(3)
            ]
            axis = 2 if kind == "mg_displacement_c_z" else 0
            expected = [0.0, 0.0, 0.0]
            expected[axis] = sign * magnitude
            error = max_vector_error(actual, expected)
            lattice_error = max(
                abs(candidate["lattice_angstrom"][i][j] - reference["lattice_angstrom"][i][j])
                for i in range(3) for j in range(3)
            )
            stationary_error = max_vector_error(
                candidate["cartesian_positions_angstrom"][0],
                reference["cartesian_positions_angstrom"][0],
            )
            if max(error, lattice_error, stationary_error) >= displacement_tolerance:
                raise ValueError(
                    f"independent physical displacement gate failed: {experiment_id}: "
                    f"actual={actual}, expected={expected}, error={error}"
                )
            registrations[experiment_id] = {
                "kind": kind,
                "atom_order": candidate["atom_order"],
                "actual_cartesian_displacement_angstrom": actual,
                "expected_cartesian_displacement_angstrom": expected,
                "max_abs_error_angstrom": error,
                "unchanged_lattice_max_abs_error_angstrom": lattice_error,
                "stationary_atom_max_abs_error_angstrom": stationary_error,
                "acceptance_tolerance_angstrom": displacement_tolerance,
                "accepted": True,
            }
        else:
            reference = parent[row["material"]]
            actual_matrix = deformation_from_lattices(
                reference["lattice_angstrom"], candidate["lattice_angstrom"]
            )
            expected_matrix = expected_deformation(kind, sign * magnitude)
            error = max(
                abs(actual_matrix[i][j] - expected_matrix[i][j])
                for i in range(3) for j in range(3)
            )
            direct_error = max(
                abs(candidate["fractional_positions"][i][j] - reference["fractional_positions"][i][j])
                for i in range(len(candidate["fractional_positions"])) for j in range(3)
            )
            det = determinant(actual_matrix)
            determinant_error = abs(det - 1.0)
            if max(error, direct_error, determinant_error) >= matrix_tolerance:
                raise ValueError(f"independent physical strain gate failed: {experiment_id}: {error}")
            registrations[experiment_id] = {
                "kind": kind,
                "atom_order": candidate["atom_order"],
                "actual_deformation_gradient": actual_matrix,
                "expected_deformation_gradient": expected_matrix,
                "max_abs_matrix_error": error,
                "unchanged_direct_coordinate_max_abs_error": direct_error,
                "acceptance_tolerance": matrix_tolerance,
                "determinant": det,
                "determinant_error_from_one": determinant_error,
                "accepted": True,
            }
    return registrations


def copy_evidence(
    source: Path,
    destination: Path,
    state: Path,
    analysis_staging: Path,
    row: dict[str, str],
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []

    def frozen_copy(source_path: Path, destination_path: Path) -> None:
        shutil.copyfile(source_path, destination_path)
        records.append({
            "experiment_id": row["experiment_id"],
            "source_relative_to_state": str(source_path.relative_to(state)),
            "frozen_relative_to_analysis": str(destination_path.relative_to(analysis_staging)),
            "sha256": sha256(source_path),
            "size_bytes": source_path.stat().st_size,
        })

    destination.mkdir(parents=True)
    for name in (
        "INPUT", "STRU", "KPT", row["pseudopotential"], "input_metadata.json",
        "input_sha256.json", "runtime.json", "runner_result.json", "run.stdout",
        "resource_usage.txt",
    ):
        path = source / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"missing raw evidence: {path}")
        frozen_copy(path, destination / name)
    affinity_destination = destination / "rank_affinity"
    affinity_destination.mkdir()
    for rank in range(4):
        source_path = source / "rank_affinity" / f"rank-{rank}.txt"
        frozen_copy(source_path, affinity_destination / source_path.name)
    suffix = row["suffix"]
    output_source = source / f"OUT.{suffix}"
    output_destination = destination / f"OUT.{suffix}"
    output_destination.mkdir()
    for name in ("running_scf.log", "chg.cube", "pot.cube", "warning.log"):
        path = output_source / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"missing solver evidence: {path}")
        frozen_copy(path, output_destination / name)
    for category in ("attempts", "completions"):
        marker = state / category / f"{row['experiment_id']}.json"
        frozen_copy(marker, destination / f"{category[:-1]}_marker.json")
    return records


def flatten(values: object) -> list[float]:
    output: list[float] = []
    if isinstance(values, (int, float)):
        output.append(float(values))
    elif isinstance(values, list):
        for value in values:
            output.extend(flatten(value))
    elif isinstance(values, dict):
        for key in sorted(values):
            output.extend(flatten(values[key]))
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if not args.write:
        raise SystemExit("analysis is materializing; pass --write")
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / CONFIG_REL).read_text(encoding="utf-8"))
    if config.get("protocol_revision") != PROTOCOL or config.get("status") != "preregistered_analysis_only":
        raise SystemExit("config protocol differs")
    rows = read_manifest(root / MANIFEST_REL)
    if [row["experiment_id"] for row in rows] != config["run_ids_exact"]:
        raise SystemExit("source manifest IDs differ from analysis-only registration")
    source_registration = config["source_execution"]
    if sha256(root / SOURCE_CONFIG_REL) != source_registration["source_config_sha256"]:
        raise SystemExit("source execution config differs from analysis registration")
    if sha256(root / MANIFEST_REL) != source_registration["source_manifest_sha256"]:
        raise SystemExit("source manifest differs from analysis registration")
    state = Path(config["state_root"])
    terminal = json.loads((state / "terminal.json").read_text(encoding="utf-8"))
    if terminal.get("status") != "accepted" or terminal.get("runner_return_code") != 0:
        raise SystemExit("formal runner terminal is not accepted")
    if terminal.get("accepted_run_count") != 15:
        raise SystemExit("formal runner did not accept exactly 15 runs")
    launch = json.loads((state / "launch.json").read_text(encoding="utf-8"))
    if launch.get("git_head") != source_registration["formal_git_head"]:
        raise SystemExit("source execution Git head differs from analysis registration")
    geometry_audit = independent_geometry_audit(root, state, rows)
    if len(geometry_audit) != 15 or not all(row.get("accepted") is True for row in geometry_audit.values()):
        raise SystemExit("independent physical geometry audit did not accept exactly 15 points")
    analysis = root / config["analysis_root"]
    if analysis.exists():
        raise SystemExit(f"refusing to overwrite analysis: {analysis}")
    analysis.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".g1-displacement-staging-", dir=analysis.parent))
    point_results: dict[str, dict[str, object]] = {}
    point_rows: list[dict[str, object]] = []
    state_sha_records: list[dict[str, object]] = []
    try:
        raw_root = staging / "raw"
        raw_root.mkdir()
        for row in rows:
            experiment_id = row["experiment_id"]
            run = state / "runs" / experiment_id
            metadata = json.loads((run / "input_metadata.json").read_text(encoding="utf-8"))
            runner_result = json.loads((run / "runner_result.json").read_text(encoding="utf-8"))
            if runner_result.get("status") != "accepted" or not runner_result.get("rank_affinity_verified"):
                raise ValueError(f"runner evidence rejected: {experiment_id}")
            suffix = row["suffix"]
            output = run / f"OUT.{suffix}"
            log_path = output / "running_scf.log"
            log_text = log_path.read_text(encoding="utf-8", errors="strict")
            thermo = parse_thermodynamic_log(log_text, expected_atom_count=int(row["atom_count"]))
            forces, stress = parse_forces_stress(log_text, int(row["atom_count"]))
            density = parse_abacus_cube(
                output / "chg.cube", quantity="electron density", units="electron/bohr^3",
                structure_path=run / "STRU",
            )
            potential = parse_abacus_cube(
                output / "pot.cube", quantity="effective potential", units="Ry",
                structure_path=run / "STRU", expected_grid=density.dimensions,
            )
            require_same_cube_geometry(density, potential)
            expected_atomic_number = 13 if row["material"] == "al" else 12
            expected_valence = 3.0 if row["material"] == "al" else 2.0
            for cube_name, cube in (("density", density), ("potential", potential)):
                if any(
                    int(atom[0]) != expected_atomic_number
                    or abs(float(atom[1]) - expected_valence) > 1.0e-12
                    for atom in cube.atom_rows
                ):
                    raise ValueError(f"cube atom order/identity differs: {experiment_id}/{cube_name}")
            density_geometry = cube_geometry_audit(density, metadata)
            potential_geometry = cube_geometry_audit(potential, metadata)
            geometry_error = max(
                float(density_geometry["maximum_accepted_geometry_error_bohr"]),
                float(potential_geometry["maximum_accepted_geometry_error_bohr"]),
            )
            geometry_limit = float(config["acceptance"]["cube_geometry_absolute_tolerance_bohr"])
            if not geometry_error < geometry_limit:
                raise ValueError(f"cube geometry gate failed: {experiment_id}: {geometry_error}")
            expected_electrons = float(row["expected_electrons"])
            integrated_electrons = density.voxel_volume_bohr3 * math.fsum(density.values)
            electron_relative_error = abs(integrated_electrons - expected_electrons) / expected_electrons
            if not electron_relative_error < float(config["acceptance"]["electron_number_relative_error_strictly_less_than"]):
                raise ValueError(f"electron-number gate failed: {experiment_id}: {electron_relative_error}")
            minimum_precision = min(
                min(precision_digits(token) for token in density.value_tokens),
                min(precision_digits(token) for token in potential.value_tokens),
            )
            if minimum_precision < int(config["acceptance"]["cube_output_precision_exact"]):
                raise ValueError(f"cube precision gate failed: {experiment_id}: {minimum_precision}")
            labels = thermo["energy_labels_ev_per_cell"]
            if set(labels) != set(config["acceptance"]["thermodynamic_labels_exact"]):
                raise ValueError(f"thermodynamic label set differs: {experiment_id}")
            if not all(math.isfinite(float(value)) for value in labels.values()):
                raise ValueError(f"non-finite thermodynamic label: {experiment_id}")
            identity_max = max(
                abs(float(identity["residual_ev_per_atom"]))
                for identity in thermo["identities"].values()
            )
            expected_from_pseudo, electron_derivation = independent_expected_electrons(run)
            if abs(expected_from_pseudo - expected_electrons) > 1.0e-14:
                raise ValueError(f"pseudo-zion electron count differs: {experiment_id}")
            structure_exact = parse_stru_exact(run / "STRU")
            if sum(structure_exact.species_counts.values()) != int(row["atom_count"]):
                raise ValueError(f"independent STRU atom count differs: {experiment_id}")
            if parse_kpt_text((run / "KPT").read_bytes()) != tuple(int(value) for value in row["kmesh"].split("x")):
                raise ValueError(f"independent KPT mesh differs: {experiment_id}")
            if sha256(run / row["pseudopotential"]) != row["pseudopotential_sha256"]:
                raise ValueError(f"independent pseudopotential identity differs: {experiment_id}")
            expected_force_order = geometry_audit[experiment_id].get("atom_order")
            if expected_force_order is None:
                expected_force_order = parse_stru_physical_geometry(run / "STRU")["atom_order"]
            actual_force_order = [item["atom"] for item in forces]
            if actual_force_order != expected_force_order:
                raise ValueError(f"force atom order differs: {experiment_id}")
            stress_symmetry_error = max(abs(stress[i][j] - stress[j][i]) for i in range(3) for j in range(3))
            stress_pressure_error = abs(sum(stress[i][i] for i in range(3)) / 3.0 - float(thermo["pressure_kbar"]))
            if stress_symmetry_error >= float(config["acceptance"]["stress_symmetry_absolute_error_kbar_strictly_less_than"]):
                raise ValueError(f"stress symmetry gate failed: {experiment_id}")
            if stress_pressure_error >= float(config["acceptance"]["stress_pressure_trace_absolute_error_kbar_strictly_less_than"]):
                raise ValueError(f"stress/pressure trace gate failed: {experiment_id}: {stress_pressure_error}")
            point = {
                "protocol_revision": PROTOCOL,
                "experiment_id": experiment_id,
                "material": row["material"],
                "perturbation_kind": row["perturbation_kind"],
                "pair_id": row["pair_id"],
                "sign": row["sign"],
                "amplitude": float(row["amplitude"]),
                "status": "accepted",
                "thermodynamic": json_safe(thermo),
                "forces": forces,
                "stress_kbar": stress,
                "stress_validation": {
                    "symmetry_max_abs_error_kbar": stress_symmetry_error,
                    "trace_over_three_minus_pressure_abs_kbar": stress_pressure_error,
                },
                "density": {
                    "sha256": density.sha256,
                    "grid": list(density.dimensions),
                    "integrated_electrons": integrated_electrons,
                    "expected_electrons": expected_electrons,
                    "electron_relative_error": electron_relative_error,
                },
                "potential": {"sha256": potential.sha256, "grid": list(potential.dimensions)},
                "cube_geometry_max_abs_error_bohr": geometry_error,
                "cube_geometry_pbc_audit": {
                    "density": density_geometry,
                    "potential": potential_geometry,
                },
                "cube_minimum_mantissa_digits": minimum_precision,
                "identity_max_abs_residual_ev_per_atom": identity_max,
                "hostname": runner_result["hostname"],
                "rank_affinity_verified": runner_result["rank_affinity_verified"],
                "elapsed_seconds": runner_result["elapsed_seconds"],
                "independent_physical_geometry": geometry_audit[experiment_id],
                "independent_input_integrity": {
                    "atom_order": expected_force_order,
                    "atom_count": int(row["atom_count"]),
                    "kmesh": [int(value) for value in row["kmesh"].split("x")],
                    "pseudopotential_sha256": row["pseudopotential_sha256"],
                    "expected_electrons_from_pseudo_zion": expected_from_pseudo,
                    "exact_stru_volume_fraction": electron_derivation["cell_volume_exact_fraction"],
                    "density_potential_geometry_identical": True,
                    "cube_atom_order_and_identity_verified": True,
                },
                "zero_temperature_exact_claim": False,
            }
            (raw_root / experiment_id).mkdir()
            (raw_root / experiment_id / "analysis_result.json").write_text(
                json.dumps(point, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
            )
            state_sha_records.extend(
                copy_evidence(
                    run,
                    raw_root / experiment_id / "run_evidence",
                    state,
                    staging,
                    row,
                )
            )
            point_results[experiment_id] = point
            point_rows.append({
                "experiment_id": experiment_id,
                "material": row["material"],
                "kind": row["perturbation_kind"],
                "pair_id": row["pair_id"],
                "sign": row["sign"],
                "atom_count": row["atom_count"],
                "E_ec_ev_per_atom": thermo["energy_labels_ev_per_atom"]["E_ec"],
                "pressure_gpa": thermo["pressure_gpa"],
                "electron_relative_error": electron_relative_error,
                "cube_geometry_error_bohr": geometry_error,
                "identity_residual_ev_per_atom": identity_max,
                "elapsed_seconds": runner_result["elapsed_seconds"],
                "status": "accepted",
            })

        pairs: list[dict[str, object]] = []
        pair_names = sorted({row["pair_id"] for row in rows if row["pair_id"]})
        if len(pair_names) != 7:
            raise ValueError("expected exactly seven central-difference pairs")
        for pair_id in pair_names:
            pair_rows = [row for row in rows if row["pair_id"] == pair_id]
            plus_rows = [row for row in pair_rows if row["sign"] == "+"]
            minus_rows = [row for row in pair_rows if row["sign"] == "-"]
            if len(plus_rows) != 1 or len(minus_rows) != 1:
                raise ValueError(f"pair signs differ: {pair_id}")
            plus = point_results[plus_rows[0]["experiment_id"]]
            minus = point_results[minus_rows[0]["experiment_id"]]
            h = float(plus["amplitude"])
            eplus = float(plus["thermodynamic"]["energy_labels_ev_per_cell"]["E_ec"])
            eminus = float(minus["thermodynamic"]["energy_labels_ev_per_cell"]["E_ec"])
            plus_force = [value for item in plus["forces"] for value in item["ev_per_angstrom"]]
            minus_force = [value for item in minus["forces"] for value in item["ev_per_angstrom"]]
            plus_stress = flatten(plus["stress_kbar"])
            minus_stress = flatten(minus["stress_kbar"])
            pairs.append({
                "pair_id": pair_id,
                "plus_id": plus["experiment_id"],
                "minus_id": minus["experiment_id"],
                "amplitude": h,
                "amplitude_units": "angstrom" if "displacement" in pair_id else "dimensionless",
                "central_E_ec_derivative_ev_per_cell_per_unit": (eplus - eminus) / (2.0 * h),
                "central_force_derivative_max_abs_per_unit": max(abs(a - b) / (2.0 * h) for a, b in zip(plus_force, minus_force)),
                "central_stress_derivative_max_abs_kbar_per_unit": max(abs(a - b) / (2.0 * h) for a, b in zip(plus_stress, minus_stress)),
                "status": "diagnostic_only_not_G4_gate",
            })

        with (staging / "points.tsv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=tuple(point_rows[0]), delimiter="\t", lineterminator="\n")
            writer.writeheader(); writer.writerows(point_rows)
        with (staging / "pairs.tsv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=tuple(pairs[0]), delimiter="\t", lineterminator="\n")
            writer.writeheader(); writer.writerows(pairs)
        state_sha_records.sort(key=lambda row: (str(row["experiment_id"]), str(row["source_relative_to_state"])))
        with (staging / "state_sha256.tsv").open("w", encoding="utf-8", newline="") as handle:
            fields = ("experiment_id", "source_relative_to_state", "frozen_relative_to_analysis", "sha256", "size_bytes")
            writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t", lineterminator="\n")
            writer.writeheader(); writer.writerows(state_sha_records)
        summary = {
            "protocol_revision": PROTOCOL,
            "status": "accepted",
            "accepted_run_count": 15,
            "registered_run_count": 15,
            "accepted_pair_count": 7,
            "diagnostic_pair_count": 7,
            "failed_ids": [],
            "formal_state_root": str(state),
            "source_execution_protocol": SOURCE_EXECUTION_PROTOCOL,
            "source_execution_reused_without_new_solver_ids": True,
            "formal_git_head": json.loads((state / "launch.json").read_text(encoding="utf-8"))["git_head"],
            "hostname_set": sorted({str(point["hostname"]) for point in point_results.values()}),
            "rank_logical_cpus_exact": [40, 41, 42, 43],
            "runtime_identity": {
                "audit_abacus_sha256": "2d68a57c7b25608b3550854dabc2e63601eeca956bf185ad7d0967052bdbb4ba",
                "r4_parent_relocated_abacus_sha256": "438c74b9ada4c8df15ffbb66da6755907dfd2a3812ecf868fafd4d7dc4db62e1",
                "byte_identical_to_r4_runtime": False,
                "equivalence_evidence_path": "analysis/s1/runtime_relocation_equivalence_20260805/summary.json",
                "equivalence_evidence_commit": "a01ac707e8e4d2604ea01a947d9c32738aa264df",
                "equivalence_evidence_points": 6,
                "equivalence_tier_exact": "storage_exact",
                "interpretation": "old-prefix binary used here; prior six-point closure supports scientific equivalence but is not a claim of runtime byte identity",
            },
            "maximum_electron_relative_error": max(float(point["density"]["electron_relative_error"]) for point in point_results.values()),
            "maximum_cube_geometry_error_bohr": max(float(point["cube_geometry_max_abs_error_bohr"]) for point in point_results.values()),
            "pbc_wrapped_cube_atom_rows": sum(
                int(point["cube_geometry_pbc_audit"][quantity]["pbc_wrapped_atom_rows"])
                for point in point_results.values() for quantity in ("density", "potential")
            ),
            "pbc_minimum_image_correction": "analysis-only R2 fixes R1 false-negative for legal periodic cube atom wrapping; lattice axes remain absolute",
            "maximum_identity_residual_ev_per_atom": max(float(point["identity_max_abs_residual_ev_per_atom"]) for point in point_results.values()),
            "minimum_cube_mantissa_digits": min(int(point["cube_minimum_mantissa_digits"]) for point in point_results.values()),
            "maximum_stress_symmetry_error_kbar": max(
                float(point["stress_validation"]["symmetry_max_abs_error_kbar"])
                for point in point_results.values()
            ),
            "maximum_stress_pressure_trace_error_kbar": max(
                float(point["stress_validation"]["trace_over_three_minus_pressure_abs_kbar"])
                for point in point_results.values()
            ),
            "state_sha256_record_count": len(state_sha_records),
            "independent_physical_geometry": {
                "accepted_point_count": len(geometry_audit),
                "maximum_displacement_error_angstrom": max(
                    float(row.get("max_abs_error_angstrom", 0.0)) for row in geometry_audit.values()
                ),
                "maximum_strain_matrix_error": max(
                    float(row.get("max_abs_matrix_error", 0.0)) for row in geometry_audit.values()
                ),
                "maximum_base_lattice_error_angstrom": float(
                    geometry_audit["S1-20260810-201"]["base_lattice_max_abs_error_angstrom"]
                ),
                "displacement_tolerance_angstrom": 1.0e-9,
                "strain_matrix_tolerance": 1.0e-12,
                "method": "independent STRU LATTICE_CONSTANT*bohr_to_angstrom reconstruction; metadata is not used",
            },
            "elapsed_seconds_total": sum(float(point["elapsed_seconds"]) for point in point_results.values()),
            "thermodynamic_semantics": "finite-temperature Mermin labels; E_ec is an entropy-corrected estimator",
            "central_difference_responses": "diagnostic only; no G4 acceptance claim",
            "g4_acceptance_claim": False,
            "deterministic_analysis": {
                "wall_clock_time_in_scientific_summary": False,
                "state_manifest": "state_sha256.tsv",
                "analysis_preregistration_git_head": subprocess.check_output(
                    ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
                ).strip(),
                "analyzer_sha256": sha256(Path(__file__).resolve()),
                "validator_sha256": sha256(root / "scripts/validate_s1_g1_displacement_strain_reference_r2.py"),
                "analysis_config_sha256": sha256(root / CONFIG_REL),
                "source_execution_config_sha256": sha256(root / SOURCE_CONFIG_REL),
                "source_manifest_sha256": sha256(root / MANIFEST_REL),
            },
        }
        (staging / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
        )
        (staging / "README.md").write_text(
            "# S1 G1 displacement/strain reference analysis R2\n\n"
            "Analysis-only R2 accepted 15/15 frozen R1 finite-temperature KS calculations and 7/7 signed pairs; no solver ID was rerun. "
            "All runs include complete thermodynamic labels, forces, stress, 17-digit density and "
            "potential cubes, and an independently integrated electron-number check. Central "
            "differences are diagnostics only and are not a G4 acceptance result.\n\n"
            "R1 analysis was rejected as a false negative because S1-20260810-203's cube legally "
            "wrapped an atom across periodic boundaries. R2 reduces cube/STRU atom-coordinate "
            "differences by the lattice minimum image while retaining the absolute lattice-axis gate.\n\n"
            "Runtime note: these runs use the frozen old-prefix ABACUS binary `2d68a57c...`, not "
            "the R4 relocated binary `438c74b9...`; they are therefore not runtime-byte-identical "
            "to R4. The prior six-point old→relocated closure in "
            "`analysis/s1/runtime_relocation_equivalence_20260805/summary.json` (commit "
            "`a01ac707e8e4d2604ea01a947d9c32738aa264df`) was storage-exact at all six points and "
            "supports scientific equivalence only.\n",
            encoding="utf-8",
        )
        os.rename(staging, analysis)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
