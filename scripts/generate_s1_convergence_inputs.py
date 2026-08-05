#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

import generate_s1_inputs as common


def kmesh_label(kmesh: list[int]) -> str:
    return "k" + "x".join(f"{value:03d}" for value in kmesh)


def sigma_label(sigma: float) -> str:
    return f"sigma{sigma:.8f}".replace(".", "p")


def write_candidate(
    *,
    project_root: Path,
    output_root: Path,
    config: dict,
    config_sha256: str,
    material_id: str,
    material: dict,
    solver: str,
    scan_axis: str,
    label: str,
    base_cell: list[list[float]],
    positions: list[list[float]],
    pseudo_sha256: str,
    settings: dict,
) -> None:
    variant = deepcopy(material)
    variant[solver].update(settings)
    job_dir = output_root / material_id / solver / scan_axis / label
    job_dir.mkdir(parents=True, exist_ok=True)
    pseudo_dir = os.path.relpath(project_root / "assets" / "pseudo", job_dir)
    suffix = f"s1_{material_id}_{solver}_{label}"
    (job_dir / "INPUT").write_text(
        common.input_text(variant, solver, pseudo_dir, suffix), encoding="utf-8"
    )
    (job_dir / "STRU").write_text(
        common.stru_text(material, base_cell, positions), encoding="utf-8"
    )
    (job_dir / "KPT").write_text(
        common.kpt_text(variant[solver]["kmesh"]), encoding="utf-8"
    )
    volume = abs(common.determinant(base_cell))
    metadata = {
        "atom_count": len(positions),
        "candidate_status": config["status"],
        "config_sha256": config_sha256,
        "ecutrho_ry": variant[solver]["ecutrho_ry"],
        "ecutwfc_ry": variant[solver]["ecutwfc_ry"],
        "expected_electrons": material["valence_electrons"] * len(positions),
        "kmesh": variant[solver]["kmesh"],
        "material": material_id,
        "pseudopotential": material["pseudopotential"],
        "pseudopotential_sha256": pseudo_sha256,
        "scan_axis": scan_axis,
        "solver": solver,
        "structure": material["structure"],
        "volume_per_atom_angstrom3": volume / len(positions),
        "volume_ratio": 1.0,
    }
    if solver == "ksdft":
        metadata["smearing_sigma_ry"] = variant[solver]["smearing_sigma_ry"]
    common.write_json(job_dir / "metadata.json", metadata)


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    config_path = project_root / "config" / "S1_baseline_protocol.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config_sha256 = common.sha256(config_path)
    output_root = project_root / "inputs" / "s1" / "convergence_candidates"
    count = 0

    for material_id, material in sorted(config["materials"].items()):
        base_cell, positions = common.base_structure(material)
        pseudo = project_root / "assets" / "pseudo" / material["pseudopotential"]
        pseudo_sha256 = common.sha256(pseudo)
        if pseudo_sha256 != material["pseudopotential_sha256"]:
            raise ValueError(f"pseudopotential SHA-256 mismatch: {pseudo}")

        for solver in ("ofdft", "ksdft"):
            for cutoff in material[solver]["cutoff_scan_ry"]:
                label = f"ecut{int(cutoff):03d}"
                write_candidate(
                    project_root=project_root,
                    output_root=output_root,
                    config=config,
                    config_sha256=config_sha256,
                    material_id=material_id,
                    material=material,
                    solver=solver,
                    scan_axis="cutoff",
                    label=label,
                    base_cell=base_cell,
                    positions=positions,
                    pseudo_sha256=pseudo_sha256,
                    settings={"ecutwfc_ry": cutoff, "ecutrho_ry": 4 * cutoff},
                )
                count += 1

        for kmesh in material["ksdft"]["kmesh_scan"]:
            write_candidate(
                project_root=project_root,
                output_root=output_root,
                config=config,
                config_sha256=config_sha256,
                material_id=material_id,
                material=material,
                solver="ksdft",
                scan_axis="kpoint",
                label=kmesh_label(kmesh),
                base_cell=base_cell,
                positions=positions,
                pseudo_sha256=pseudo_sha256,
                settings={"kmesh": kmesh},
            )
            count += 1

        for sigma in material["ksdft"]["smearing_scan_ry"]:
            write_candidate(
                project_root=project_root,
                output_root=output_root,
                config=config,
                config_sha256=config_sha256,
                material_id=material_id,
                material=material,
                solver="ksdft",
                scan_axis="smearing",
                label=sigma_label(sigma),
                base_cell=base_cell,
                positions=positions,
                pseudo_sha256=pseudo_sha256,
                settings={"smearing_sigma_ry": sigma},
            )
            count += 1
    print(f"Generated {count} S1 convergence candidates under {output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
