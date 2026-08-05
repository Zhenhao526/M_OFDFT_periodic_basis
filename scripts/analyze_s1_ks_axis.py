#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def axis_key(metadata: dict, axis: str) -> tuple:
    if axis == "kpoint":
        return tuple(int(value) for value in metadata["kmesh"])
    if axis == "smearing":
        return (float(metadata["smearing_sigma_ry"]),)
    raise ValueError(f"unsupported KS scan axis: {axis}")


def display_key(key: tuple, axis: str):
    return list(key) if axis == "kpoint" else key[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("axis", choices=("kpoint", "smearing"))
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("run_directories", nargs="+", type=Path)
    args = parser.parse_args()

    attempts_by_value: dict[tuple, list[dict]] = defaultdict(list)
    for run_directory in args.run_directories:
        run_directory = run_directory.resolve()
        result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))
        metadata = json.loads((run_directory / "input_metadata.json").read_text(encoding="utf-8"))
        experiment = json.loads(
            (run_directory / "experiment_metadata.json").read_text(encoding="utf-8")
        )
        if metadata.get("solver") != "ksdft" or metadata.get("scan_axis") != args.axis:
            raise ValueError(f"{run_directory} is not a KSDFT {args.axis} scan run")
        attempts_by_value[axis_key(metadata, args.axis)].append(
            {
                "code_commit": experiment["code_commit"],
                "converged": bool(result["converged"]),
                "energy_ev_per_atom": result["energy_ev_per_atom"],
                "experiment_id": run_directory.name,
                "failure_reason": result.get("failure_reason"),
                "pressure_gpa": result["pressure_gpa"],
            }
        )

    ordered_keys = sorted(attempts_by_value)
    if args.axis == "smearing":
        ordered_keys.reverse()  # protocol compares the standard sigma with its halved value
    rows = []
    for key in ordered_keys:
        attempts = sorted(attempts_by_value[key], key=lambda item: item["experiment_id"])
        converged = [attempt for attempt in attempts if attempt["converged"]]
        chosen = converged[-1] if converged else attempts[-1]
        rows.append({"value": display_key(key, args.axis), "attempts": attempts, "chosen": chosen})

    for index, row in enumerate(rows):
        if index == len(rows) - 1:
            row["delta_to_next_mev_per_atom"] = None
            row["passes_energy_threshold"] = None
            continue
        current = row["chosen"]
        following = rows[index + 1]["chosen"]
        delta = abs(current["energy_ev_per_atom"] - following["energy_ev_per_atom"]) * 1000.0
        row["delta_to_next_mev_per_atom"] = delta
        row["passes_energy_threshold"] = (
            current["converged"] and following["converged"] and delta < 2.0
        )

    energy_passed = any(row["passes_energy_threshold"] for row in rows[:-1])
    if args.axis == "kpoint":
        recommended = next(
            (row["value"] for row in rows[:-1] if row["passes_energy_threshold"]), None
        )
        passed = recommended is not None
        pending = []
    else:
        recommended = rows[0]["value"] if energy_passed else None
        passed = False
        pending = ["equilibrium_volume_change_below_0.2_percent"]

    payload = {
        "axis": args.axis,
        "energy_prescreen_passed": energy_passed,
        "failed_attempts": sum(
            not attempt["converged"]
            for attempts in attempts_by_value.values()
            for attempt in attempts
        ),
        "passed": passed,
        "pending_acceptance": pending,
        "recommended_value": recommended,
        "rows": rows,
        "thresholds": {"energy_mev_per_atom": 2.0},
    }
    args.output_directory.mkdir(parents=True, exist_ok=True)
    (args.output_directory / "summary.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        "value\texperiment_id\tconverged\tenergy_ev_per_atom\tpressure_gpa\t"
        "delta_to_next_mev_per_atom\tpasses_energy_threshold"
    ]
    for row in rows:
        chosen = row["chosen"]
        value = "x".join(map(str, row["value"])) if isinstance(row["value"], list) else row["value"]
        lines.append(
            "\t".join(
                str(item)
                for item in (
                    value,
                    chosen["experiment_id"],
                    chosen["converged"],
                    chosen["energy_ev_per_atom"],
                    chosen["pressure_gpa"],
                    row["delta_to_next_mev_per_atom"],
                    row["passes_energy_threshold"],
                )
            )
        )
    (args.output_directory / "summary.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if (passed or (args.axis == "smearing" and energy_passed)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
