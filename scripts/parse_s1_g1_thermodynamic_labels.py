#!/usr/bin/env python3
"""Parse one registered S1-G1 thermodynamic-label run, fail closed.

The default mode validates the complete raw record and prints JSON.  Passing
``--write`` creates ``thermodynamic_labels.json`` with exclusive-create
semantics; an existing label file is immutable evidence and is never replaced.
"""

from __future__ import annotations

import argparse
import json
import math
from decimal import Decimal, InvalidOperation
from pathlib import Path

from s1_electron_number_common import (
    expected_electrons,
    integrate_cube,
    parse_charge_grid,
    parse_stru,
)
from s1_g1_thermodynamic_label_common import (
    AUDIT_IDS,
    CUBE_PRECISION,
    ELECTRON_RELATIVE_ERROR_LIMIT,
    PROTOCOL_REVISION,
    canonical_json_bytes,
    json_safe,
    parse_abacus_cube,
    parse_input_text,
    parse_kpt_text,
    parse_thermodynamic_log,
    read_manifest,
    read_regular_bytes,
    read_regular_text,
    require,
    require_same_cube_geometry,
    sha256_regular_file,
)


DEFAULT_CONFIG = Path("config/S1_g1_thermodynamic_label_audit_r1.json")
DEFAULT_MANIFEST = Path(
    "config/S1_g1_thermodynamic_label_audit_r1_manifest.tsv"
)
OUTPUT_BASENAME = "thermodynamic_labels.json"


def _load_object(path: Path) -> dict[str, object]:
    payload = json.loads(read_regular_text(path))
    require(isinstance(payload, dict), f"JSON root is not an object: {path}")
    return payload


def _decimal(value: object, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"invalid {label}: {value}") from error
    require(result.is_finite(), f"non-finite {label}")
    return result


def _single_regular(paths: list[Path], label: str) -> Path:
    require(len(paths) == 1, f"expected exactly one {label}")
    path = paths[0]
    require(path.is_file() and not path.is_symlink(), f"{label} is not a regular file")
    return path


def _validate_registration(
    config_path: Path, manifest_path: Path
) -> tuple[dict[str, object], list[dict[str, str]]]:
    config = _load_object(config_path)
    require(
        config.get("protocol_revision") == PROTOCOL_REVISION,
        "configuration protocol revision differs",
    )
    require(config.get("status") == "preregistered", "configuration is not preregistered")
    require(
        config.get("registered_experiment_ids") == list(AUDIT_IDS),
        "configuration experiment IDs differ",
    )
    manifest_registration = config.get("manifest")
    require(isinstance(manifest_registration, dict), "configuration lacks manifest registration")
    require(
        manifest_registration.get("sha256") == sha256_regular_file(manifest_path),
        "manifest SHA-256 differs from configuration",
    )
    rows = read_manifest(manifest_path)
    require(len(rows) == len(AUDIT_IDS), "manifest must contain exactly 40 rows")
    require(
        tuple(row["experiment_id"] for row in rows) == AUDIT_IDS,
        "manifest row ID order differs",
    )
    execution_order = config.get("execution_order")
    require(
        isinstance(execution_order, list)
        and len(execution_order) == 40
        and set(execution_order) == set(AUDIT_IDS),
        "configuration execution order differs",
    )
    observed_indices = {
        row["experiment_id"]: int(row["execution_index"]) for row in rows
    }
    require(
        observed_indices
        == {experiment_id: index for index, experiment_id in enumerate(execution_order, 1)},
        "manifest execution indices differ",
    )
    return config, rows


def _validate_inputs(run: Path, row: dict[str, str]) -> dict[str, object]:
    hashes = {
        "INPUT": (run / "INPUT", row["input_sha256"]),
        "STRU": (run / "STRU", row["stru_sha256"]),
        "KPT": (run / "KPT", row["kpt_sha256"]),
        "input_metadata.json": (
            run / "input_metadata.json",
            row["metadata_sha256"],
        ),
        row["pseudopotential"]: (
            run / row["pseudopotential"],
            row["pseudopotential_sha256"],
        ),
    }
    for label, (path, expected_hash) in hashes.items():
        require(expected_hash != "", f"manifest lacks {label} hash")
        require(sha256_regular_file(path) == expected_hash, f"{label} SHA-256 differs")

    parsed_input = parse_input_text(read_regular_bytes(run / "INPUT"))
    require(parsed_input.get("suffix") == (row["suffix"],), "INPUT suffix differs")
    require(
        parsed_input.get("smearing_sigma") == (row["smearing_sigma_ry"],),
        "INPUT smearing sigma differs",
    )
    require(
        parsed_input.get("out_chg") == ("1", str(CUBE_PRECISION)),
        "INPUT out_chg contract differs",
    )
    require(
        parsed_input.get("out_pot") == ("1", str(CUBE_PRECISION)),
        "INPUT out_pot contract differs",
    )
    registered_mesh = tuple(int(value) for value in row["kmesh"].split("x"))
    require(parse_kpt_text(read_regular_bytes(run / "KPT")) == registered_mesh, "KPT differs")

    metadata = _load_object(run / "input_metadata.json")
    exact_metadata = {
        "protocol_revision": PROTOCOL_REVISION,
        "experiment_id": row["experiment_id"],
        "material": row["material"],
        "volume_ratio": row["volume_ratio"],
        "smearing_level": row["smearing_level"],
        "smearing_sigma_ry": row["smearing_sigma_ry"],
        "run_role": row["run_role"],
        "source_experiment_id": row["source_experiment_id"],
        "reference_experiment_id": row["reference_experiment_id"],
        "common_quarter_partner_id": row["common_quarter_partner_id"],
        "pseudopotential": row["pseudopotential"],
        "expected_electrons": row["expected_electrons"],
        "atom_count": row["atom_count"],
        "cube_precision": str(CUBE_PRECISION),
        "density_basename": "chg.cube",
        "potential_basename": "pot.cube",
    }
    for key, expected in exact_metadata.items():
        require(str(metadata.get(key, "")) == expected, f"metadata {key} differs")
    require(metadata.get("kmesh") == list(registered_mesh), "metadata kmesh differs")
    return metadata


def parse_run(
    run_directory: Path,
    *,
    config_path: Path,
    manifest_path: Path,
) -> dict[str, object]:
    run = run_directory.resolve()
    require(run.is_dir() and not run.is_symlink(), f"invalid run directory: {run}")
    experiment_id = run.name
    require(experiment_id in AUDIT_IDS, "run directory basename is not a registered ID")
    config, rows = _validate_registration(config_path.resolve(), manifest_path.resolve())
    row = next(item for item in rows if item["experiment_id"] == experiment_id)
    metadata = _validate_inputs(run, row)

    logs = sorted(run.glob("OUT.*/running_scf.log"))
    log_path = _single_regular(logs, "OUT.*/running_scf.log")
    density_path = _single_regular(sorted(run.glob("OUT.*/chg.cube")), "OUT.*/chg.cube")
    potential_candidates = sorted(
        path
        for path in run.glob("OUT.*/*.cube")
        if "pot" in path.name.lower()
    )
    potential_path = _single_regular(potential_candidates, "potential cube")
    require(potential_path.name == "pot.cube", "potential cube basename is not pot.cube")

    structure = parse_stru(run / "STRU")
    atom_count = sum(structure.species_counts.values())
    require(atom_count == int(row["atom_count"]), "registered atom count differs from STRU")
    raw_grid = parse_charge_grid(log_path)
    density = parse_abacus_cube(
        density_path,
        quantity="thermal_density",
        units="electron/bohr^3",
        structure_path=run / "STRU",
        expected_grid=raw_grid,
    )
    potential = parse_abacus_cube(
        potential_path,
        quantity="local_effective_ks_potential",
        units="Ry",
        structure_path=run / "STRU",
        expected_grid=raw_grid,
    )
    require_same_cube_geometry(density, potential)

    expected, electron_derivation = expected_electrons(run)
    require(
        _decimal(expected, "independent expected electrons")
        == _decimal(row["expected_electrons"], "registered expected electrons"),
        "independent expected electron count differs",
    )
    electron_integration = integrate_cube(
        density_path, run / "STRU", expected, raw_grid
    )
    require(electron_integration.get("accepted") is True, "density electron integration failed")
    require(
        float(electron_integration["certified_relative_error"])
        < ELECTRON_RELATIVE_ERROR_LIMIT,
        "density electron relative error is not strictly below the limit",
    )
    thermodynamics = parse_thermodynamic_log(
        read_regular_text(log_path), expected_atom_count=atom_count
    )
    require(
        thermodynamics["electron_count_reported"]
        == _decimal(row["expected_electrons"], "registered electron count"),
        "raw-log electron count differs",
    )

    config_hash = sha256_regular_file(config_path)
    manifest_hash = sha256_regular_file(manifest_path)
    payload: dict[str, object] = {
        "schema_revision": "S1-G1-THERMODYNAMIC-LABELS-R1",
        "protocol_revision": PROTOCOL_REVISION,
        "status": "accepted",
        "experiment_id": experiment_id,
        "registration": {
            "config_path": str(config_path),
            "config_sha256": config_hash,
            "manifest_path": str(manifest_path),
            "manifest_sha256": manifest_hash,
            "execution_index": int(row["execution_index"]),
            "execution_phase": row["execution_phase"],
        },
        "registered_axes": {
            "material": row["material"],
            "volume_ratio": row["volume_ratio"],
            "smearing_level": row["smearing_level"],
            "smearing_sigma_ry": row["smearing_sigma_ry"],
            "kmesh": [int(value) for value in row["kmesh"].split("x")],
            "run_role": row["run_role"],
        },
        "thermodynamic_labels": thermodynamics,
        "density_field": {
            "path": str(density_path.relative_to(run)),
            "sha256": density.sha256,
            "quantity": density.quantity,
            "units": density.units,
            "dimensions": list(density.dimensions),
            "grid_count": density.grid_count,
            "cell_volume_bohr3": density.cell_volume_bohr3,
            "voxel_volume_bohr3": density.voxel_volume_bohr3,
            "spin_count": density.spin_count,
        },
        "potential_field": {
            "path": str(potential_path.relative_to(run)),
            "sha256": potential.sha256,
            "quantity": potential.quantity,
            "units": potential.units,
            "dimensions": list(potential.dimensions),
            "grid_count": potential.grid_count,
            "cell_volume_bohr3": potential.cell_volume_bohr3,
            "spin_count": potential.spin_count,
            "registered_derivative": "g_sigma=-(v_eff_eV-unweighted_cell_average_v_eff_eV)",
            "gauge": "fixed-electron-number_constant_mode_projection",
        },
        "electron_number": {
            "independent_derivation": electron_derivation,
            "cube_integration": electron_integration,
        },
        "input_metadata_sha256": sha256_regular_file(run / "input_metadata.json"),
        "raw_log_sha256": sha256_regular_file(log_path),
        "metadata": metadata,
        "zero_temperature_exact_claim": False,
    }
    safe = json_safe(payload)
    require(isinstance(safe, dict), "internal JSON conversion failed")
    # A final serialization pass also rejects accidentally retained NaN/Infinity.
    json.dumps(safe, allow_nan=False)
    return safe


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--write", action="store_true")
    arguments = parser.parse_args()
    payload = parse_run(
        arguments.run_directory,
        config_path=arguments.config,
        manifest_path=arguments.manifest,
    )
    encoded = canonical_json_bytes(payload)
    if arguments.write:
        output = arguments.run_directory.resolve() / OUTPUT_BASENAME
        with output.open("xb") as handle:
            handle.write(encoded)
        print(output)
    else:
        print(encoded.decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
