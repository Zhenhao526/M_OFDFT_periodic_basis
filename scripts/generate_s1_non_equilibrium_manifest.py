#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_entries(config: dict) -> list[dict]:
    entries = []
    id_block = config["experiment_id_block"]
    date = id_block["date"]
    sequence = int(id_block["start_sequence"])
    for material in sorted(config["materials"]):
        for series_id in config["series_order"]:
            if series_id not in config["materials"][material]:
                raise ValueError(f"missing {material}/{series_id} S1-R8 specification")
            for ratio in config["volume_ratios"]:
                volume_label = f"v{round(float(ratio) * 100):03d}"
                entries.append(
                    {
                        "experiment_id": f"S1-{date}-{sequence:03d}",
                        "input_directory": (
                            "inputs/s1/non_equilibrium_convergence/"
                            f"{material}/{volume_label}/{series_id}"
                        ),
                        "material": material,
                        "series_id": series_id,
                        "volume_ratio": float(ratio),
                    }
                )
                sequence += 1
    if sequence - 1 != int(id_block["end_sequence"]):
        raise ValueError("S1-R8 experiment ID block does not match matrix size")
    return entries


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
        default=project_root / "config" / "S1_non_equilibrium_run_manifest.tsv",
    )
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    entries = build_entries(config)
    for entry in entries:
        metadata_path = project_root / entry["input_directory"] / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        entry["comparison_axis"] = metadata["comparison_axis"]
        entry["reference_experiment_id"] = metadata["baseline_experiment_id"]
        entry["input_metadata_sha256"] = sha256(metadata_path)
    lines = [
        "experiment_id\tinput_directory\tmaterial\tseries_id\tcomparison_axis\t"
        "volume_ratio\treference_experiment_id\tinput_metadata_sha256"
    ]
    lines.extend(
        "\t".join(
            str(entry[key])
            for key in (
                "experiment_id",
                "input_directory",
                "material",
                "series_id",
                "comparison_axis",
                "volume_ratio",
                "reference_experiment_id",
                "input_metadata_sha256",
            )
        )
        for entry in entries
    )
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Generated {len(entries)} S1-R8 experiments in {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
