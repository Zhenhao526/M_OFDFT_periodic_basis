#!/usr/bin/env python3
"""Generate the immutable ABACUS inputs for the S1/G1 three-layer R1 matrix."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from s1_g1_three_layer_common import (
    CONFIG_PATH,
    MANIFEST_PATH,
    canonical_json_bytes,
    find_project_root,
    load_config,
    load_manifest,
    parse_input_values,
    parse_kmesh,
    require,
    sha256_file,
    write_if_identical_or_absent,
)


# Preserve the exact lattice-unit conversion already frozen in every S1 STRU.
LATTICE_CONSTANT_BOHR_PER_ANGSTROM = 1.8897261254578281


def render_input(row: dict[str, str], config: dict) -> bytes:
    contract = config["input_contract"]
    lines = [
        "INPUT_PARAMETERS",
        f"suffix {row['suffix']}",
        f"out_chg 1 {contract['density_cube_precision']}",
        "calculation scf",
        "esolver_type ksdft",
        f"basis_type {contract['basis_type']}",
        f"dft_functional {contract['dft_functional']}",
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
        f"smearing_method {contract['smearing_method']}",
        f"smearing_sigma {contract['smearing_sigma_ry']}",
        "mixing_type broyden",
        "mixing_beta 0.4",
        f"vnl_in_h {1 if contract['vnl_in_h'] else 0}",
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def render_stru(row: dict[str, str], config: dict) -> bytes:
    material = row["material"]
    spec = config["materials"][material]
    pseudo = config["pseudodojo"]["materials"][material]
    scale = float(row["volume_ratio"]) ** (1.0 / 3.0)
    if material == "al":
        half = float(spec["a0_angstrom"]) * scale / 2.0
        vectors = (
            (0.0, half, half),
            (half, 0.0, half),
            (half, half, 0.0),
        )
        positions = ((0.0, 0.0, 0.0),)
    elif material == "mg":
        a = float(spec["a0_angstrom"]) * scale
        c = float(spec["c0_angstrom"]) * scale
        vectors = (
            (a, 0.0, 0.0),
            (-a / 2.0, math.sqrt(3.0) * a / 2.0, 0.0),
            (0.0, 0.0, c),
        )
        positions = ((0.0, 0.0, 0.0), (2.0 / 3.0, 1.0 / 3.0, 0.5))
    else:
        raise ValueError(f"unknown material: {material}")
    lines = [
        "ATOMIC_SPECIES",
        f"{spec['element']} {spec['mass']} {pseudo['basename']} {config['input_contract']['upf_format']}",
        "",
        "LATTICE_CONSTANT",
        f"{LATTICE_CONSTANT_BOHR_PER_ANGSTROM:.16f}",
        "",
        "LATTICE_VECTORS",
    ]
    lines.extend(" ".join(f"{value:.16f}" for value in vector) for vector in vectors)
    lines.extend(["", "ATOMIC_POSITIONS", "Direct", "", spec["element"], "0.0", str(len(positions))])
    lines.extend(
        " ".join(f"{value:.16f}" for value in position) + " 1 1 1" for position in positions
    )
    return ("\n".join(lines) + "\n").encode("utf-8")


def render_kpt(row: dict[str, str]) -> bytes:
    kmesh = parse_kmesh(row["kmesh"])
    return (
        "K_POINTS\n0\nGamma\n"
        + " ".join(str(value) for value in kmesh)
        + " 0 0 0\n"
    ).encode("utf-8")


def validate_generated(directory: Path, row: dict[str, str], config: dict) -> None:
    parsed = parse_input_values(directory / "INPUT")
    required_exact = {
        "suffix": (row["suffix"],),
        "basis_type": ("pw",),
        "dft_functional": ("PBE",),
        "ecutwfc": (row["ecutwfc_ry"],),
        "ecutrho": (row["ecutrho_ry"],),
        "smearing_method": ("fd",),
        "smearing_sigma": (str(config["input_contract"]["smearing_sigma_ry"]),),
        "vnl_in_h": ("1",),
        "out_chg": ("1", "17"),
    }
    for key, expected in required_exact.items():
        require(parsed.get(key) == expected, f"generated INPUT {key} differs for {row['experiment_id']}")
    kpt = (directory / "KPT").read_text(encoding="utf-8")
    require(" ".join(str(value) for value in parse_kmesh(row["kmesh"])) in kpt, "KPT differs")
    stru = (directory / "STRU").read_text(encoding="utf-8")
    require(f"{row['pseudo_basename']} upf201" in stru, "STRU PP format differs")


def generate(project_root: Path) -> list[Path]:
    config = load_config(project_root)
    rows = load_manifest(project_root)
    input_root = project_root / config["input_root"]
    generated: list[Path] = []
    config_sha = sha256_file(project_root / CONFIG_PATH)
    manifest_sha = sha256_file(project_root / MANIFEST_PATH)
    phase_ids = config["execution_phases"]
    require(
        [row["experiment_id"] for row in rows]
        == phase_ids["p0"] + phase_ids["al_eos"] + phase_ids["mg_required"] + phase_ids["mg_optional"],
        "config/manifest execution order differs",
    )
    for row in rows:
        material = row["material"]
        spec = config["materials"][material]
        pseudo = config["pseudodojo"]["materials"][material]
        require(row["pseudo_basename"] == pseudo["basename"], "manifest PP basename differs")
        require(row["pseudo_sha256"] == pseudo["sha256"], "manifest PP SHA differs")
        require(int(row["atom_count"]) == int(spec["atom_count"]), "atom count differs")
        require(abs(float(row["expected_electrons"]) - float(spec["expected_electrons"])) < 1e-12, "electron count differs")
        directory = input_root / row["experiment_id"]
        directory.mkdir(parents=True, exist_ok=True)
        input_bytes = render_input(row, config)
        stru_bytes = render_stru(row, config)
        kpt_bytes = render_kpt(row)
        write_if_identical_or_absent(directory / "INPUT", input_bytes)
        write_if_identical_or_absent(directory / "STRU", stru_bytes)
        write_if_identical_or_absent(directory / "KPT", kpt_bytes)
        metadata = {
            "schema_version": 1,
            "protocol_revision": config["protocol_revision"],
            "experiment_id": row["experiment_id"],
            "phase": row["phase"],
            "requirement": row["requirement"],
            "material": material,
            "volume_ratio": row["volume_ratio"],
            "role": row["role"],
            "suffix": row["suffix"],
            "ecutwfc_ry": int(row["ecutwfc_ry"]),
            "ecutrho_ry": int(row["ecutrho_ry"]),
            "kmesh": list(parse_kmesh(row["kmesh"])),
            "atom_count": int(row["atom_count"]),
            "expected_electrons": float(row["expected_electrons"]),
            "pseudo": {
                "basename": row["pseudo_basename"],
                "sha256": row["pseudo_sha256"],
                "upstream_url": pseudo["url"],
                "upstream_commit": config["pseudodojo"]["commit"],
                "format": "upf201",
            },
            "input_identity": {
                "INPUT_sha256": __import__("hashlib").sha256(input_bytes).hexdigest(),
                "STRU_sha256": __import__("hashlib").sha256(stru_bytes).hexdigest(),
                "KPT_sha256": __import__("hashlib").sha256(kpt_bytes).hexdigest(),
                "config_sha256": config_sha,
                "manifest_sha256": manifest_sha,
            },
            "thermodynamic_semantics": {
                "F": "E_KohnSham=!FINAL_ETOT_IS",
                "m": "E_entropy(-TS)<=0",
                "U": "F-m",
                "E_ec": "F-m/2; finite-smearing estimator",
                "zero_temperature_exact_claim": False,
                "local_only_kinetic_decomposition_claim": False,
            },
        }
        write_if_identical_or_absent(directory / "metadata.json", canonical_json_bytes(metadata))
        validate_generated(directory, row, config)
        generated.append(directory)
    return generated


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path)
    args = parser.parse_args()
    project_root = find_project_root(args.project_root)
    generated = generate(project_root)
    print(f"generated_or_verified={len(generated)} input_root={generated[0].parent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
