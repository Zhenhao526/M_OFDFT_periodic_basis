#!/usr/bin/env python3
"""Parse KS-NL ABACUS output without applying local-only energy identities."""

from __future__ import annotations

import argparse
import json
import math
import re
from decimal import Decimal
from pathlib import Path

from s1_g1_thermodynamic_label_common import parse_abacus_cube
from s1_g1_three_layer_common import (
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
    relative_error = abs(integrated - expected_electrons) / expected_electrons
    limit = float(config["acceptance"]["electron_relative_error_strictly_less_than"])
    require(relative_error < limit, "independent cube electron-number gate failed")
    require(abs(float(electrons_reported) - expected_electrons) < 1e-12, "reported electron count differs")
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
            "reported": float(electrons_reported),
            "integrated_cube": integrated,
            "relative_error": relative_error,
            "strict_limit": limit,
            "accepted": True,
            "cube_grid": list(density.dimensions),
            "cube_voxel_volume_bohr3": density.voxel_volume_bohr3,
        },
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
