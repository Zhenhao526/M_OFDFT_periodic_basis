#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from copy import deepcopy
from pathlib import Path

from generate_s1_inputs import (
    base_structure,
    determinant,
    input_text,
    kpt_text,
    ratio_label,
    scaled_cell,
    sha256,
    stru_text,
    write_json,
)


DATASET_KIND = "eos_non_equilibrium_convergence"


def _resolve_project_path(project_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else project_root / path


def _core_point_map(core_summary: dict) -> dict[tuple[str, str, float], dict]:
    points = {}
    for series_key, payload in core_summary["series"].items():
        material, series_id = series_key.split("/", 1)
        for point in payload.get("points", []):
            key = (material, series_id, round(float(point["volume_ratio"]), 12))
            if key in points:
                raise ValueError(f"duplicate core EOS point: {key}")
            points[key] = point
    return points


def _load_frozen_references(
    project_root: Path, config: dict
) -> tuple[dict, str, dict, str]:
    reference = config["core_reference"]
    baseline_path = _resolve_project_path(project_root, reference["baseline_config_path"])
    baseline_digest = sha256(baseline_path)
    if baseline_digest != reference["baseline_config_sha256"]:
        raise ValueError("baseline protocol SHA-256 does not match S1-R8 registration")
    core_summary_path = _resolve_project_path(project_root, reference["summary_path"])
    core_summary_digest = sha256(core_summary_path)
    if core_summary_digest != reference["summary_sha256"]:
        raise ValueError("core EOS summary SHA-256 does not match S1-R8 registration")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    core_summary = json.loads(core_summary_path.read_text(encoding="utf-8"))
    if core_summary.get("core_eos_status") != "accepted":
        raise ValueError("S1-R8 requires an accepted frozen core EOS summary")
    return baseline, baseline_digest, core_summary, core_summary_digest


def generate(
    project_root: Path, config_path: Path, output_root: Path
) -> int:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config_digest = sha256(config_path)
    baseline, baseline_digest, core_summary, core_summary_digest = _load_frozen_references(
        project_root, config
    )
    core_points = _core_point_map(core_summary)
    count = 0

    for material_id in sorted(config["materials"]):
        material = baseline["materials"][material_id]
        pseudo = project_root / "assets" / "pseudo" / material["pseudopotential"]
        actual_pseudo_sha = sha256(pseudo)
        if actual_pseudo_sha != material["pseudopotential_sha256"]:
            raise ValueError(f"pseudopotential SHA-256 mismatch: {pseudo}")
        base_cell, positions = base_structure(material)
        base_volume = abs(determinant(base_cell))

        for volume_ratio in config["volume_ratios"]:
            ratio = round(float(volume_ratio), 12)
            label = ratio_label(ratio)
            cell = scaled_cell(base_cell, ratio)
            volume = abs(determinant(cell))
            structure_text = stru_text(material, cell, positions)

            for series_id in config["series_order"]:
                spec = config["materials"][material_id][series_id]
                solver = spec["solver"]
                baseline_series_id = spec["baseline_series_id"]
                baseline_point = core_points.get((material_id, baseline_series_id, ratio))
                if baseline_point is None or not baseline_point.get("converged"):
                    raise ValueError(
                        f"missing accepted core reference for {material_id}/{series_id}/{ratio}"
                    )

                variant = deepcopy(material)
                variant[solver]["ecutwfc_ry"] = spec["ecutwfc_ry"]
                variant[solver]["ecutrho_ry"] = spec["ecutrho_ry"]
                variant[solver]["kmesh"] = spec["kmesh"]
                if solver == "ksdft":
                    variant[solver]["smearing_sigma_ry"] = spec["smearing_sigma_ry"]

                job_dir = output_root / material_id / label / series_id
                job_dir.mkdir(parents=True, exist_ok=True)
                pseudo_dir = os.path.relpath(project_root / "assets" / "pseudo", job_dir)
                suffix = f"s1_{material_id}_{label}_{series_id}"
                (job_dir / "INPUT").write_text(
                    input_text(variant, solver, pseudo_dir, suffix), encoding="utf-8"
                )
                (job_dir / "STRU").write_text(structure_text, encoding="utf-8")
                (job_dir / "KPT").write_text(kpt_text(spec["kmesh"]), encoding="utf-8")
                stru_digest = sha256(job_dir / "STRU")
                if stru_digest != baseline_point["stru_sha256"]:
                    raise ValueError(
                        f"S1-R8 structure differs from frozen core EOS: {material_id}/{label}"
                    )

                write_json(
                    job_dir / "metadata.json",
                    {
                        "atom_count": len(positions),
                        "baseline_config_sha256": baseline_digest,
                        "baseline_experiment_id": baseline_point["experiment_id"],
                        "baseline_series_id": baseline_series_id,
                        "candidate_status": config["status"],
                        "cell_angstrom": cell,
                        "comparison_axis": spec["comparison_axis"],
                        "config_sha256": config_digest,
                        "core_summary_sha256": core_summary_digest,
                        "dataset_kind": DATASET_KIND,
                        "ecutrho_ry": spec["ecutrho_ry"],
                        "ecutwfc_ry": spec["ecutwfc_ry"],
                        "energy_observable": baseline_point["energy_observable"],
                        "expected_electrons": material["valence_electrons"] * len(positions),
                        "kmesh": spec["kmesh"],
                        "material": material_id,
                        "protocol_revision": config["protocol_revision"],
                        "pseudopotential": material["pseudopotential"],
                        "pseudopotential_sha256": actual_pseudo_sha,
                        "relative_energy_reference_volume_ratio": config["acceptance"][
                            "relative_energy_reference_volume_ratio"
                        ],
                        "series_id": series_id,
                        "smearing_method": "fd" if solver == "ksdft" else None,
                        "smearing_sigma_ry": spec["smearing_sigma_ry"],
                        "solver": solver,
                        "structure": material["structure"],
                        "structure_id": f"{material_id}_{label}",
                        "stru_sha256": stru_digest,
                        "volume_angstrom3": volume,
                        "volume_per_atom_angstrom3": volume / len(positions),
                        "volume_ratio": volume / base_volume,
                        "xc": material["xc"],
                    },
                )
                count += 1
    return count


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=project_root / "config" / "S1_non_equilibrium_convergence.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=project_root / "inputs" / "s1" / "non_equilibrium_convergence",
    )
    args = parser.parse_args()
    count = generate(project_root, args.config.resolve(), args.output.resolve())
    print(f"Generated {count} S1-R8 jobs under {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
