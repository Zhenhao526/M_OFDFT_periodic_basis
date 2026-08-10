#!/usr/bin/env python3
"""Analyze and freeze the 15-point displacement/strain reference evidence."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from pathlib import Path

from s1_g1_thermodynamic_label_common import (
    json_safe,
    parse_abacus_cube,
    parse_thermodynamic_log,
)


PROTOCOL = "S1-G1-DISPLACEMENT-STRAIN-REFERENCE-R1"
CONFIG_REL = Path("config/S1_g1_displacement_strain_reference_r1.json")
MANIFEST_REL = Path("config/S1_g1_displacement_strain_reference_r1_manifest.tsv")
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


def max_cube_geometry_error(cube, metadata: dict) -> float:
    lattice = metadata["expected_lattice_vectors_bohr"]
    positions = metadata["expected_cartesian_positions_bohr"]
    errors = []
    for axis in range(3):
        reconstructed = [float(value) * cube.dimensions[axis] for value in cube.axis_steps_bohr[axis]]
        errors.extend(abs(reconstructed[j] - float(lattice[axis][j])) for j in range(3))
    if len(cube.atom_rows) != len(positions):
        raise ValueError("cube atom count differs from registered positions")
    for row, expected in zip(cube.atom_rows, positions):
        errors.extend(abs(float(row[2 + j]) - float(expected[j])) for j in range(3))
    return max(errors, default=0.0)


def precision_digits(token: str) -> int:
    mantissa = token.lower().split("e", 1)[0]
    return sum(character.isdigit() for character in mantissa)


def copy_evidence(source: Path, destination: Path, state: Path, row: dict[str, str]) -> None:
    destination.mkdir(parents=True)
    for name in (
        "INPUT", "STRU", "KPT", row["pseudopotential"], "input_metadata.json",
        "input_sha256.json", "runtime.json", "runner_result.json", "run.stdout",
        "resource_usage.txt",
    ):
        path = source / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"missing raw evidence: {path}")
        shutil.copyfile(path, destination / name)
    affinity_destination = destination / "rank_affinity"
    affinity_destination.mkdir()
    for rank in range(4):
        source_path = source / "rank_affinity" / f"rank-{rank}.txt"
        shutil.copyfile(source_path, affinity_destination / source_path.name)
    suffix = row["suffix"]
    output_source = source / f"OUT.{suffix}"
    output_destination = destination / f"OUT.{suffix}"
    output_destination.mkdir()
    for name in ("running_scf.log", "chg.cube", "pot.cube", "warning.log"):
        path = output_source / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"missing solver evidence: {path}")
        shutil.copyfile(path, output_destination / name)
    for category in ("attempts", "completions"):
        marker = state / category / f"{row['experiment_id']}.json"
        shutil.copyfile(marker, destination / f"{category[:-1]}_marker.json")


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
    if config.get("protocol_revision") != PROTOCOL:
        raise SystemExit("config protocol differs")
    rows = read_manifest(root / MANIFEST_REL)
    state = Path(config["state_root"])
    terminal = json.loads((state / "terminal.json").read_text(encoding="utf-8"))
    if terminal.get("status") != "accepted" or terminal.get("runner_return_code") != 0:
        raise SystemExit("formal runner terminal is not accepted")
    if terminal.get("accepted_run_count") != 15:
        raise SystemExit("formal runner did not accept exactly 15 runs")
    analysis = root / config["analysis_root"]
    if analysis.exists():
        raise SystemExit(f"refusing to overwrite analysis: {analysis}")
    analysis.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".g1-displacement-staging-", dir=analysis.parent))
    point_results: dict[str, dict[str, object]] = {}
    point_rows: list[dict[str, object]] = []
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
            density_geometry_error = max_cube_geometry_error(density, metadata)
            potential_geometry_error = max_cube_geometry_error(potential, metadata)
            geometry_error = max(density_geometry_error, potential_geometry_error)
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
                "density": {
                    "sha256": density.sha256,
                    "grid": list(density.dimensions),
                    "integrated_electrons": integrated_electrons,
                    "expected_electrons": expected_electrons,
                    "electron_relative_error": electron_relative_error,
                },
                "potential": {"sha256": potential.sha256, "grid": list(potential.dimensions)},
                "cube_geometry_max_abs_error_bohr": geometry_error,
                "cube_minimum_mantissa_digits": minimum_precision,
                "identity_max_abs_residual_ev_per_atom": identity_max,
                "hostname": runner_result["hostname"],
                "rank_affinity_verified": runner_result["rank_affinity_verified"],
                "elapsed_seconds": runner_result["elapsed_seconds"],
                "zero_temperature_exact_claim": False,
            }
            (raw_root / experiment_id).mkdir()
            (raw_root / experiment_id / "analysis_result.json").write_text(
                json.dumps(point, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
            )
            copy_evidence(run, raw_root / experiment_id / "run_evidence", state, row)
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
        summary = {
            "protocol_revision": PROTOCOL,
            "status": "accepted",
            "accepted_run_count": 15,
            "registered_run_count": 15,
            "accepted_pair_count": 7,
            "diagnostic_pair_count": 7,
            "failed_ids": [],
            "formal_state_root": str(state),
            "formal_git_head": json.loads((state / "launch.json").read_text(encoding="utf-8"))["git_head"],
            "hostname_set": sorted({str(point["hostname"]) for point in point_results.values()}),
            "rank_logical_cpus_exact": [40, 41, 42, 43],
            "maximum_electron_relative_error": max(float(point["density"]["electron_relative_error"]) for point in point_results.values()),
            "maximum_cube_geometry_error_bohr": max(float(point["cube_geometry_max_abs_error_bohr"]) for point in point_results.values()),
            "maximum_identity_residual_ev_per_atom": max(float(point["identity_max_abs_residual_ev_per_atom"]) for point in point_results.values()),
            "minimum_cube_mantissa_digits": min(int(point["cube_minimum_mantissa_digits"]) for point in point_results.values()),
            "elapsed_seconds_total": sum(float(point["elapsed_seconds"]) for point in point_results.values()),
            "thermodynamic_semantics": "finite-temperature Mermin labels; E_ec is an entropy-corrected estimator",
            "central_difference_responses": "diagnostic only; no G4 acceptance claim",
            "g4_acceptance_claim": False,
            "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        (staging / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
        )
        (staging / "README.md").write_text(
            "# S1 G1 displacement/strain reference R1\n\n"
            "Accepted 15/15 finite-temperature KS reference calculations and 7/7 signed pairs. "
            "All runs include complete thermodynamic labels, forces, stress, 17-digit density and "
            "potential cubes, and an independently integrated electron-number check. Central "
            "differences are diagnostics only and are not a G4 acceptance result.\n",
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
