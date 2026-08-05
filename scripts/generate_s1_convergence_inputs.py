#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

import generate_s1_inputs as common


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
                variant = deepcopy(material)
                variant[solver]["ecutwfc_ry"] = cutoff
                variant[solver]["ecutrho_ry"] = 4 * cutoff
                label = f"ecut{int(cutoff):03d}"
                job_dir = output_root / material_id / solver / "cutoff" / label
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
                    common.kpt_text(material[solver]["kmesh"]), encoding="utf-8"
                )
                volume = abs(common.determinant(base_cell))
                common.write_json(
                    job_dir / "metadata.json",
                    {
                        "atom_count": len(positions),
                        "candidate_status": config["status"],
                        "config_sha256": config_sha256,
                        "ecutrho_ry": 4 * cutoff,
                        "ecutwfc_ry": cutoff,
                        "expected_electrons": material["valence_electrons"] * len(positions),
                        "kmesh": material[solver]["kmesh"],
                        "material": material_id,
                        "pseudopotential": material["pseudopotential"],
                        "pseudopotential_sha256": pseudo_sha256,
                        "scan_axis": "cutoff",
                        "solver": solver,
                        "structure": material["structure"],
                        "volume_per_atom_angstrom3": volume / len(positions),
                        "volume_ratio": 1.0,
                    },
                )
                count += 1
    print(f"Generated {count} S1 cutoff candidates under {output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
