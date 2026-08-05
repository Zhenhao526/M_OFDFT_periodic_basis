#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


SERIES = (
    ("ofdft", "ofdft"),
    ("ksdft_standard", "ksdft"),
    ("ksdft_half", "ksdft_half"),
)


def build_entries(config: dict, date: str, start_sequence: int) -> list[dict]:
    entries = []
    sequence = start_sequence
    for material in sorted(config["materials"]):
        for series_id, directory_name in SERIES:
            for ratio in config["volume_ratios"]:
                volume_label = f"v{round(float(ratio) * 100):03d}"
                entries.append(
                    {
                        "experiment_id": f"S1-{date}-{sequence:03d}",
                        "input_directory": (
                            f"inputs/s1/eos_candidates/{material}/{volume_label}/{directory_name}"
                        ),
                        "material": material,
                        "series_id": series_id,
                        "volume_ratio": float(ratio),
                    }
                )
                sequence += 1
    return entries


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--start-sequence", required=True, type=int)
    parser.add_argument(
        "--config", type=Path, default=project_root / "config" / "S1_baseline_protocol.json"
    )
    parser.add_argument(
        "--output", type=Path, default=project_root / "config" / "S1_eos_run_manifest.tsv"
    )
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    entries = build_entries(config, args.date, args.start_sequence)
    lines = ["experiment_id\tinput_directory\tmaterial\tseries_id\tvolume_ratio"]
    lines.extend(
        "\t".join(
            str(entry[key])
            for key in (
                "experiment_id",
                "input_directory",
                "material",
                "series_id",
                "volume_ratio",
            )
        )
        for entry in entries
    )
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Generated {len(entries)} EOS experiments in {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
