#!/usr/bin/env python3
"""Parse one preregistered S1-G1 thermodynamic-label audit R4 run.

R4 reuses the frozen, tested R1 scientific helper functions, but owns its
registration entry point and run orchestration.  It never mutates the R1
module's protocol, ID, or validator globals at runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import parse_s1_g1_thermodynamic_labels as r1_parser
from generate_s1_g1_thermodynamic_label_audit_r4 import (
    CONFIG_PATH,
    MANIFEST_PATH,
    PROTOCOL_REVISION,
    R4_AUDIT_IDS,
)
from s1_g1_thermodynamic_label_common import (
    CUBE_PRECISION,
    canonical_json_bytes,
    parse_input_text,
    parse_kpt_text,
    read_manifest,
    read_regular_bytes,
    require,
    sha256_regular_file,
)


OUTPUT_BASENAME = "thermodynamic_labels.json"


def _stable_regular_bytes(path: Path, *, allow_proc_fd: bool) -> tuple[bytes, str]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    if not allow_proc_fd:
        nofollow = getattr(os, "O_NOFOLLOW", None)
        require(nofollow is not None, "stable provenance read requires O_NOFOLLOW")
        flags |= nofollow
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode), f"registration input is not regular: {path}")
        blocks: list[bytes] = []
        offset = 0
        while offset < before.st_size:
            block = os.pread(
                descriptor, min(1024 * 1024, before.st_size - offset), offset
            )
            if not block:
                break
            blocks.append(block)
            offset += len(block)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    fields = (
        "st_dev",
        "st_ino",
        "st_mode",
        "st_nlink",
        "st_uid",
        "st_gid",
        "st_rdev",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    payload = b"".join(blocks)
    require(
        not any(getattr(before, key) != getattr(after, key) for key in fields)
        and len(payload) == before.st_size,
        f"registration input changed or was read short: {path}",
    )
    return payload, hashlib.sha256(payload).hexdigest()


def _write_readonly(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            require(written > 0, "short registration materialization write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(path, 0o400)


def _validate_r4_registration(
    config_path: Path, manifest_path: Path
) -> tuple[dict[str, object], list[dict[str, str]]]:
    """Validate the R4 namespace without changing the frozen R1 parser file."""

    config = r1_parser._load_object(config_path)
    require(
        config.get("protocol_revision") == PROTOCOL_REVISION,
        "configuration protocol revision differs",
    )
    require(config.get("status") == "preregistered", "configuration is not preregistered")
    require(
        config.get("registered_experiment_ids") == list(R4_AUDIT_IDS),
        "configuration experiment IDs differ",
    )
    manifest_registration = config.get("manifest")
    require(isinstance(manifest_registration, dict), "configuration lacks manifest registration")
    require(
        manifest_registration.get("sha256") == sha256_regular_file(manifest_path),
        "manifest SHA-256 differs from configuration",
    )
    rows = read_manifest(manifest_path)
    require(
        len(rows) == len(R4_AUDIT_IDS),
        f"manifest must contain exactly {len(R4_AUDIT_IDS)} rows",
    )
    require(
        tuple(row["experiment_id"] for row in rows) == R4_AUDIT_IDS,
        "manifest row ID order differs",
    )
    execution_order = config.get("execution_order")
    require(
        execution_order == list(R4_AUDIT_IDS),
        "configuration execution order differs",
    )
    observed_indices = {
        row["experiment_id"]: int(row["execution_index"]) for row in rows
    }
    require(
        observed_indices
        == {
            experiment_id: index
            for index, experiment_id in enumerate(R4_AUDIT_IDS, 1)
        },
        "manifest execution indices differ",
    )
    return config, rows


def _validate_r4_inputs(run: Path, row: dict[str, str]) -> dict[str, object]:
    """Validate generated R4 inputs without the R1 metadata namespace."""

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
    require(
        parse_kpt_text(read_regular_bytes(run / "KPT")) == registered_mesh,
        "KPT differs",
    )

    metadata = r1_parser._load_object(run / "input_metadata.json")
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


def _bound_registration_bytes(
    config_path: Path,
    manifest_path: Path,
    scientific_config_path: Path | None,
    scientific_manifest_path: Path | None,
) -> tuple[bytes, bytes, str, str]:
    require(
        (scientific_config_path is None) == (scientific_manifest_path is None),
        "scientific config and manifest must be supplied together",
    )
    canonical_config, config_sha256 = _stable_regular_bytes(
        config_path, allow_proc_fd=False
    )
    canonical_manifest, manifest_sha256 = _stable_regular_bytes(
        manifest_path, allow_proc_fd=False
    )
    scientific_config, scientific_config_sha256 = _stable_regular_bytes(
        scientific_config_path or config_path,
        allow_proc_fd=scientific_config_path is not None,
    )
    scientific_manifest, scientific_manifest_sha256 = _stable_regular_bytes(
        scientific_manifest_path or manifest_path,
        allow_proc_fd=scientific_manifest_path is not None,
    )
    require(
        scientific_config_sha256 == config_sha256
        and scientific_manifest_sha256 == manifest_sha256
        and scientific_config == canonical_config
        and scientific_manifest == canonical_manifest,
        "sealed scientific registration differs from canonical provenance",
    )
    return scientific_config, scientific_manifest, config_sha256, manifest_sha256


@contextmanager
def _materialized_registration(
    config_bytes: bytes, manifest_bytes: bytes
) -> Iterator[tuple[Path, Path]]:
    with tempfile.TemporaryDirectory(prefix="m-ofdft-g1-r4-registration-") as temporary:
        private = Path(temporary)
        materialized_config = private / "config.json"
        materialized_manifest = private / "manifest.tsv"
        _write_readonly(materialized_config, config_bytes)
        _write_readonly(materialized_manifest, manifest_bytes)
        yield materialized_config, materialized_manifest


def validate_registration_contract(
    *,
    config_path: Path,
    manifest_path: Path,
    scientific_config_path: Path | None = None,
    scientific_manifest_path: Path | None = None,
) -> dict[str, object]:
    """Exercise the exact registration hook used by the scientific parser."""

    config_bytes, manifest_bytes, config_sha256, manifest_sha256 = (
        _bound_registration_bytes(
            config_path,
            manifest_path,
            scientific_config_path,
            scientific_manifest_path,
        )
    )
    with _materialized_registration(config_bytes, manifest_bytes) as materialized:
        config, rows = _validate_r4_registration(*materialized)
    return {
        "protocol_revision": config["protocol_revision"],
        "registered_experiment_ids": [row["experiment_id"] for row in rows],
        "config_sha256": config_sha256,
        "manifest_sha256": manifest_sha256,
    }


def parse_run(
    run_directory: Path,
    *,
    config_path: Path,
    manifest_path: Path,
    scientific_config_path: Path | None = None,
    scientific_manifest_path: Path | None = None,
) -> dict[str, object]:
    """Parse an R4 run with a local registration and frozen R1 helpers."""

    scientific_config, scientific_manifest, config_sha256, manifest_sha256 = (
        _bound_registration_bytes(
            config_path,
            manifest_path,
            scientific_config_path,
            scientific_manifest_path,
        )
    )
    with _materialized_registration(
        scientific_config, scientific_manifest
    ) as (materialized_config, materialized_manifest):
        config, rows = _validate_r4_registration(
            materialized_config, materialized_manifest
        )

    run = run_directory.resolve()
    require(run.is_dir() and not run.is_symlink(), f"invalid run directory: {run}")
    experiment_id = run.name
    require(experiment_id in R4_AUDIT_IDS, "run directory basename is not a registered ID")
    row = next(item for item in rows if item["experiment_id"] == experiment_id)
    metadata = _validate_r4_inputs(run, row)

    log_path = r1_parser._single_regular(
        sorted(run.glob("OUT.*/running_scf.log")), "OUT.*/running_scf.log"
    )
    density_path = r1_parser._single_regular(
        sorted(run.glob("OUT.*/chg.cube")), "OUT.*/chg.cube"
    )
    potential_candidates = sorted(
        path for path in run.glob("OUT.*/*.cube") if "pot" in path.name.lower()
    )
    potential_path = r1_parser._single_regular(
        potential_candidates, "potential cube"
    )
    require(potential_path.name == "pot.cube", "potential cube basename is not pot.cube")

    structure = r1_parser.parse_stru(run / "STRU")
    atom_count = sum(structure.species_counts.values())
    require(atom_count == int(row["atom_count"]), "registered atom count differs from STRU")
    raw_grid = r1_parser.parse_charge_grid(log_path)
    density = r1_parser.parse_abacus_cube(
        density_path,
        quantity="thermal_density",
        units="electron/bohr^3",
        structure_path=run / "STRU",
        expected_grid=raw_grid,
    )
    potential = r1_parser.parse_abacus_cube(
        potential_path,
        quantity="local_effective_ks_potential",
        units="Ry",
        structure_path=run / "STRU",
        expected_grid=raw_grid,
    )
    r1_parser.require_same_cube_geometry(density, potential)

    expected, electron_derivation = r1_parser.expected_electrons(run)
    require(
        r1_parser._decimal(expected, "independent expected electrons")
        == r1_parser._decimal(row["expected_electrons"], "registered expected electrons"),
        "independent expected electron count differs",
    )
    electron_integration = r1_parser.integrate_cube(
        density_path, run / "STRU", expected, raw_grid
    )
    require(electron_integration.get("accepted") is True, "density electron integration failed")
    require(
        float(electron_integration["certified_relative_error"])
        < r1_parser.ELECTRON_RELATIVE_ERROR_LIMIT,
        "density electron relative error is not strictly below the limit",
    )
    thermodynamics = r1_parser.parse_thermodynamic_log(
        r1_parser.read_regular_text(log_path), expected_atom_count=atom_count
    )
    require(
        thermodynamics["electron_count_reported"]
        == r1_parser._decimal(row["expected_electrons"], "registered electron count"),
        "raw-log electron count differs",
    )

    payload: dict[str, object] = {
        "schema_revision": "S1-G1-THERMODYNAMIC-LABELS-R4",
        "protocol_revision": PROTOCOL_REVISION,
        "status": "accepted",
        "experiment_id": experiment_id,
        "registration": {
            "config_path": str(config_path),
            "config_sha256": config_sha256,
            "manifest_path": str(manifest_path),
            "manifest_sha256": manifest_sha256,
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
        "parser_reuse_contract": {
            "scientific_helper_module": "parse_s1_g1_thermodynamic_labels.py",
            "registration_namespace": "R4",
            "registration_runtime_global_mutation": False,
            "r1_evidence_reinterpretation": False,
        },
    }
    safe = r1_parser.json_safe(payload)
    require(isinstance(safe, dict), "internal JSON conversion failed")
    json.dumps(safe, allow_nan=False)
    return safe


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--config", type=Path, default=project_root / CONFIG_PATH)
    parser.add_argument("--manifest", type=Path, default=project_root / MANIFEST_PATH)
    parser.add_argument("--scientific-config", type=Path)
    parser.add_argument("--scientific-manifest", type=Path)
    parser.add_argument("--write", action="store_true")
    arguments = parser.parse_args()
    payload = parse_run(
        arguments.run_directory.resolve(),
        config_path=arguments.config.resolve(),
        manifest_path=arguments.manifest.resolve(),
        scientific_config_path=arguments.scientific_config,
        scientific_manifest_path=arguments.scientific_manifest,
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
