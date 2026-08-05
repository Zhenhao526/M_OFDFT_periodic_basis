#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Iterable, Sequence

ANGSTROM_TO_BOHR = 1.8897261254578281


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def determinant(cell: Sequence[Sequence[float]]) -> float:
    a, b, c = cell
    return (
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - a[1] * (b[0] * c[2] - b[2] * c[0])
        + a[2] * (b[0] * c[1] - b[1] * c[0])
    )


def base_structure(material: dict) -> tuple[list[list[float]], list[list[float]]]:
    if material["structure"] == "fcc_primitive":
        half = float(material["a0_angstrom"]) / 2.0
        return (
            [[0.0, half, half], [half, 0.0, half], [half, half, 0.0]],
            [[0.0, 0.0, 0.0]],
        )
    if material["structure"] == "hcp_primitive":
        a = float(material["a0_angstrom"])
        c = float(material["c0_angstrom"])
        return (
            [[a, 0.0, 0.0], [-0.5 * a, math.sqrt(3.0) * a / 2.0, 0.0], [0.0, 0.0, c]],
            [[0.0, 0.0, 0.0], [2.0 / 3.0, 1.0 / 3.0, 0.5]],
        )
    raise ValueError(f"unsupported structure: {material['structure']}")


def scaled_cell(cell: Sequence[Sequence[float]], volume_ratio: float) -> list[list[float]]:
    scale = volume_ratio ** (1.0 / 3.0)
    return [[scale * value for value in vector] for vector in cell]


def ratio_label(volume_ratio: float) -> str:
    return f"v{round(volume_ratio * 100):03d}"


def vector_text(vector: Iterable[float]) -> str:
    return " ".join(f"{value:.16f}" for value in vector)


def stru_text(material: dict, cell: Sequence[Sequence[float]], positions: Sequence[Sequence[float]]) -> str:
    element = material["element"]
    lines = [
        "ATOMIC_SPECIES",
        f"{element} {material['mass']} {material['pseudopotential']} blps",
        "",
        "LATTICE_CONSTANT",
        f"{ANGSTROM_TO_BOHR:.16f}",
        "",
        "LATTICE_VECTORS",
        *(vector_text(vector) for vector in cell),
        "",
        "ATOMIC_POSITIONS",
        "Direct",
        "",
        element,
        "0.0",
        str(len(positions)),
        *(f"{vector_text(position)} 1 1 1" for position in positions),
    ]
    return "\n".join(lines) + "\n"


def kpt_text(kmesh: Sequence[int]) -> str:
    return "\n".join(["K_POINTS", "0", "Gamma", f"{kmesh[0]} {kmesh[1]} {kmesh[2]} 0 0 0", ""])


def input_text(material: dict, solver: str, pseudo_dir: str, suffix: str) -> str:
    settings = material[solver]
    common = [
        "INPUT_PARAMETERS",
        f"suffix {suffix}",
        "calculation scf",
        f"esolver_type {'ofdft' if solver == 'ofdft' else 'ksdft'}",
        "basis_type pw",
        f"dft_functional {material['xc']}",
        "symmetry 0",
        f"pseudo_dir {pseudo_dir}",
        "pseudo_rcut 16",
        f"ecutwfc {settings['ecutwfc_ry']}",
        f"ecutrho {settings['ecutrho_ry']}",
        "scf_nmax 200",
        "cal_force 1",
        "cal_stress 1",
    ]
    if solver == "ofdft":
        common.extend(
            [
                "of_kinetic wt",
                "of_method tn",
                "of_conv both",
                "of_tole 1e-7",
                "of_tolp 1e-6",
                "of_wt_alpha 0.8333333333333334",
                "of_wt_beta 0.8333333333333334",
                "of_wt_rho0 0.0",
                "of_hold_rho0 0",
            ]
        )
    else:
        common.extend(
            [
                "scf_thr 1e-10",
                "ks_solver cg",
                "smearing_method fd",
                f"smearing_sigma {settings['smearing_sigma_ry']}",
                "mixing_type broyden",
                "mixing_beta 0.4",
            ]
        )
    return "\n".join(common) + "\n"


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def generate(project_root: Path, config_path: Path, output_root: Path) -> int:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config_digest = sha256(config_path)
    count = 0
    for material_id, material in sorted(config["materials"].items()):
        pseudo = project_root / "assets" / "pseudo" / material["pseudopotential"]
        actual_pseudo_sha = sha256(pseudo)
        if actual_pseudo_sha != material["pseudopotential_sha256"]:
            raise ValueError(f"pseudopotential SHA-256 mismatch: {pseudo}")
        base_cell, positions = base_structure(material)
        base_volume = abs(determinant(base_cell))
        for volume_ratio in config["volume_ratios"]:
            cell = scaled_cell(base_cell, float(volume_ratio))
            label = ratio_label(float(volume_ratio))
            for solver in ("ofdft", "ksdft"):
                job_dir = output_root / material_id / label / solver
                job_dir.mkdir(parents=True, exist_ok=True)
                pseudo_dir = os.path.relpath(project_root / "assets" / "pseudo", job_dir)
                suffix = f"s1_{material_id}_{label}_{solver}"
                (job_dir / "INPUT").write_text(
                    input_text(material, solver, pseudo_dir, suffix), encoding="utf-8"
                )
                (job_dir / "STRU").write_text(stru_text(material, cell, positions), encoding="utf-8")
                (job_dir / "KPT").write_text(kpt_text(material[solver]["kmesh"]), encoding="utf-8")
                volume = abs(determinant(cell))
                write_json(
                    job_dir / "metadata.json",
                    {
                        "atom_count": len(positions),
                        "candidate_status": config["status"],
                        "cell_angstrom": cell,
                        "config_sha256": config_digest,
                        "ecutrho_ry": material[solver]["ecutrho_ry"],
                        "ecutwfc_ry": material[solver]["ecutwfc_ry"],
                        "expected_electrons": material["valence_electrons"] * len(positions),
                        "kmesh": material[solver]["kmesh"],
                        "material": material_id,
                        "pseudopotential": material["pseudopotential"],
                        "pseudopotential_sha256": actual_pseudo_sha,
                        "solver": solver,
                        "structure": material["structure"],
                        "volume_angstrom3": volume,
                        "volume_per_atom_angstrom3": volume / len(positions),
                        "volume_ratio": volume / base_volume,
                    },
                )
                count += 1
    return count


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=project_root / "config" / "S1_baseline_protocol.json")
    parser.add_argument("--output", type=Path, default=project_root / "inputs" / "s1" / "eos_candidates")
    args = parser.parse_args()
    count = generate(project_root, args.config.resolve(), args.output.resolve())
    print(f"Generated {count} S1 candidate jobs under {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
