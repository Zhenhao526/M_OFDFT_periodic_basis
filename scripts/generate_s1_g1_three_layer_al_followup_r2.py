#!/usr/bin/env python3
"""Generate or byte-check the eight immutable Al-domain follow-up R2 inputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from s1_electron_number_common import parse_stru
from s1_g1_three_layer_al_followup_r2_common import (
    CONFIG_PATH,
    MANIFEST_PATH,
    canonical_json_bytes,
    find_project_root,
    geometry_payload_bytes,
    load_config,
    load_manifest,
    parse_input_values,
    parse_kmesh,
    read_json,
    render_strain_from_base,
    require,
    sha256_bytes,
    sha256_file,
    verify_strain_geometry,
    write_if_identical_or_absent,
)


def render_input(row: dict[str, str], config: dict) -> bytes:
    contract = config["input_contract"]
    lines = [
        "INPUT_PARAMETERS",
        f"suffix {row['suffix']}",
        f"out_chg 1 {contract['density_cube_precision']}",
        "calculation scf",
        "esolver_type ksdft",
        "basis_type pw",
        "dft_functional PBE",
        "symmetry 0",
        "pseudo_dir .",
        "pseudo_rcut 16",
        f"ecutwfc {row['ecutwfc_ry']}",
        f"ecutrho {row['ecutrho_ry']}",
        f"scf_nmax {contract['scf_nmax']}",
        "cal_force 1",
        "cal_stress 1",
        f"scf_thr {contract['scf_thr']:.0e}",
        "ks_solver cg",
        "smearing_method fd",
        f"smearing_sigma {contract['smearing_sigma_ry']}",
        "mixing_type broyden",
        "mixing_beta 0.4",
        "vnl_in_h 1",
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def render_kpt(row: dict[str, str]) -> bytes:
    mesh = parse_kmesh(row["kmesh"])
    return ("K_POINTS\n0\nGamma\n" + " ".join(str(value) for value in mesh) + " 0 0 0\n").encode("utf-8")


def render_case(project_root: Path, row: dict[str, str], config: dict) -> dict[str, bytes | dict]:
    binding = config["geometry_bindings"][row["experiment_id"]]
    for key in ("construction_base_id", "construction_base_path", "registered_geometry_id", "registered_geometry_path"):
        require(binding[key] == row[key], f"binding {key} differs")
    base_path = project_root / row["construction_base_path"]
    reference_path = project_root / row["registered_geometry_path"]
    base_bytes = base_path.read_bytes()
    reference_bytes = reference_path.read_bytes()
    require(sha256_bytes(base_bytes) == row["construction_base_stru_sha256"], "construction base STRU SHA differs")
    require(sha256_bytes(reference_bytes) == row["registered_geometry_stru_sha256"], "registered geometry STRU SHA differs")
    require(sha256_bytes(geometry_payload_bytes(reference_bytes)) == row["geometry_payload_sha256"], "registered geometry payload SHA differs")
    construction: dict
    if row["phase"] == "strain":
        deformation = binding["deformation_gradient"]
        stru_bytes = render_strain_from_base(base_bytes, deformation)
        construction = verify_strain_geometry(base_bytes, stru_bytes, reference_bytes, deformation)
        require(row["accepted_common_id"] == "", "strain must not name continuation common source")
    else:
        require(base_bytes == reference_bytes, "endpoint registered/base STRU bytes differ")
        stru_bytes = reference_bytes
        require(binding["accepted_common_id"] == row["accepted_common_id"] in {"S1-20260810-327", "S1-20260810-328"}, "endpoint continuation mapping differs")
        construction = {
            "convention": "complete preregistered endpoint STRU byte identity; accepted 327/328 source is checked at runtime",
            "complete_registered_stru_byte_identical": True,
            "accepted_common_id": row["accepted_common_id"],
            "accepted": True,
        }
    require(sha256_bytes(geometry_payload_bytes(stru_bytes)) == row["geometry_payload_sha256"], "derived geometry payload differs")
    parsed = parse_stru_bytes_for_gate(project_root, stru_bytes)
    require(parsed.species_counts == {"Al": 1}, "derived atom count/order differs")
    input_bytes = render_input(row, config)
    kpt_bytes = render_kpt(row)
    metadata = {
        "schema_version": 1,
        "protocol_revision": config["protocol_revision"],
        "experiment_id": row["experiment_id"],
        "phase": row["phase"],
        "role": row["role"],
        "material": "al",
        "volume_ratio": row["volume_ratio"],
        "suffix": row["suffix"],
        "ecutwfc_ry": int(row["ecutwfc_ry"]),
        "ecutrho_ry": int(row["ecutrho_ry"]),
        "kmesh": list(parse_kmesh(row["kmesh"])),
        "atom_count": 1,
        "expected_electrons": 3.0,
        "pseudo": {
            "basename": row["pseudo_basename"],
            "sha256": row["pseudo_sha256"],
            "z_valence": 3.0,
            "expanded_nonlocal_projectors_per_atom": 18,
            "format": "upf201",
        },
        "geometry_binding": {
            "construction_base_id": row["construction_base_id"],
            "construction_base_path": row["construction_base_path"],
            "construction_base_stru_sha256": row["construction_base_stru_sha256"],
            "registered_geometry_id": row["registered_geometry_id"],
            "registered_geometry_path": row["registered_geometry_path"],
            "registered_geometry_stru_sha256": row["registered_geometry_stru_sha256"],
            "geometry_payload_definition": "STRU bytes from LATTICE_CONSTANT marker through EOF",
            "geometry_payload_sha256": row["geometry_payload_sha256"],
            "accepted_common_id": row["accepted_common_id"] or None,
            "construction_hard_gate": construction,
        },
        "input_identity": {
            "INPUT_sha256": sha256_bytes(input_bytes),
            "STRU_sha256": sha256_bytes(stru_bytes),
            "KPT_sha256": sha256_bytes(kpt_bytes),
            "config_sha256": sha256_file(project_root / CONFIG_PATH),
            "manifest_sha256": sha256_file(project_root / MANIFEST_PATH),
        },
        "thermodynamic_semantics": {
            "F": "E_KohnSham=!FINAL_ETOT_IS",
            "m": "E_entropy(-TS)<=0",
            "U": "F-m",
            "E_ec": "F-m/2 finite-smearing estimator",
            "zero_temperature_exact_claim": False,
            "local_only_kinetic_decomposition_claim": False,
        },
    }
    return {"INPUT": input_bytes, "STRU": stru_bytes, "KPT": kpt_bytes, "metadata.json": canonical_json_bytes(metadata), "metadata": metadata}


def parse_stru_bytes_for_gate(project_root: Path, payload: bytes):
    """Use the established strict parser without permitting temporary state."""
    # Every payload is also compared with a tracked reference; parse that tracked
    # reference after verifying the generated payload equality below.
    for row in load_manifest(project_root):
        reference = project_root / row["registered_geometry_path"]
        if geometry_payload_bytes(reference.read_bytes()) == geometry_payload_bytes(payload):
            return parse_stru(reference)
    raise ValueError("generated STRU does not match any registered geometry")


def validate_case(directory: Path, row: dict[str, str], config: dict) -> None:
    parsed = parse_input_values(directory / "INPUT")
    expected = {
        "suffix": (row["suffix"],), "basis_type": ("pw",), "dft_functional": ("PBE",),
        "ecutwfc": (row["ecutwfc_ry"],), "ecutrho": (row["ecutrho_ry"],),
        "smearing_method": ("fd",), "smearing_sigma": (str(config["input_contract"]["smearing_sigma_ry"]),),
        "out_chg": ("1", "17"), "cal_force": ("1",), "cal_stress": ("1",), "vnl_in_h": ("1",),
    }
    for key, value in expected.items():
        require(parsed.get(key) == value, f"INPUT {key} differs: {row['experiment_id']}")
    require(parse_kmesh(row["kmesh"]) == tuple(int(value) for value in (directory / "KPT").read_text().splitlines()[3].split()[:3]), "KPT differs")
    require(sha256_bytes(geometry_payload_bytes((directory / "STRU").read_bytes())) == row["geometry_payload_sha256"], "tracked geometry payload differs")
    metadata = read_json(directory / "metadata.json")
    require(isinstance(metadata, dict) and metadata["geometry_binding"]["construction_hard_gate"]["accepted"] is True, "construction hard gate missing")


def generate(project_root: Path, *, mode: str = "write") -> list[Path]:
    require(mode in {"write", "check", "dry-run"}, "invalid generation mode")
    config = load_config(project_root)
    rows = load_manifest(project_root)
    require([row["experiment_id"] for row in rows] == config["formal_ids"], "config/manifest order differs")
    output: list[Path] = []
    for row in rows:
        rendered = render_case(project_root, row, config)
        directory = project_root / config["input_root"] / row["experiment_id"]
        if mode == "write":
            directory.mkdir(parents=True, exist_ok=True)
            for name in ("INPUT", "STRU", "KPT", "metadata.json"):
                write_if_identical_or_absent(directory / name, rendered[name])  # type: ignore[arg-type]
            validate_case(directory, row, config)
        elif mode == "check":
            for name in ("INPUT", "STRU", "KPT", "metadata.json"):
                path = directory / name
                require(path.is_file() and path.read_bytes() == rendered[name], f"generated bytes differ: {path}")
            validate_case(directory, row, config)
        output.append(directory)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check-only", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    project_root = find_project_root(args.project_root)
    selected = "check" if args.check_only else "dry-run" if args.dry_run else "write"
    directories = generate(project_root, mode=selected)
    print(json.dumps({"status": "accepted", "mode": selected, "case_count": len(directories), "solver_started": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
