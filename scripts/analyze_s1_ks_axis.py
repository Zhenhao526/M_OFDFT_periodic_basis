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


def mark_tail_stability(rows: list[dict]) -> None:
    for index, row in enumerate(rows):
        if index == len(rows) - 1:
            row["passes_all_denser_steps"] = None
        else:
            row["passes_all_denser_steps"] = all(
                candidate["passes_energy_threshold"] is True
                for candidate in rows[index:-1]
            )


def convert_smearing_rows_to_diagnostics(rows: list[dict]) -> None:
    for row in rows:
        row["absolute_energy_shift_to_next_mev_per_atom"] = row.pop(
            "delta_to_next_mev_per_atom"
        )
        row.pop("passes_energy_threshold")


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

    if args.axis == "kpoint":
        mark_tail_stability(rows)
        recommended = next(
            (row["value"] for row in rows[:-1] if row["passes_all_denser_steps"]), None
        )
        passed = recommended is not None
        pending = []
        diagnostic_complete = None
        metric_interpretation = "adjacent_relative_energy_convergence"
    else:
        convert_smearing_rows_to_diagnostics(rows)
        recommended = None
        passed = False
        pending = [
            "eos_relative_energy_change_below_2_mev_per_atom",
            "equilibrium_volume_change_below_0.2_percent",
        ]
        diagnostic_complete = len(rows) >= 2 and all(row["chosen"]["converged"] for row in rows)
        metric_interpretation = "single_volume_absolute_energy_shift_not_acceptance_metric"

    payload = {
        "axis": args.axis,
        "diagnostic_complete": diagnostic_complete,
        "failed_attempts": sum(
            not attempt["converged"]
            for attempts in attempts_by_value.values()
            for attempt in attempts
        ),
        "passed": passed,
        "metric_interpretation": metric_interpretation,
        "pending_acceptance": pending,
        "recommended_value": recommended,
        "rows": rows,
        "thresholds": (
            {"relative_energy_mev_per_atom": 2.0}
            if args.axis == "kpoint"
            else {
                "eos_relative_energy_mev_per_atom": 2.0,
                "equilibrium_volume_percent": 0.2,
            }
        ),
    }
    args.output_directory.mkdir(parents=True, exist_ok=True)
    (args.output_directory / "summary.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if args.axis == "kpoint":
        lines = [
            "value\texperiment_id\tconverged\tenergy_ev_per_atom\tpressure_gpa\t"
            "delta_to_next_mev_per_atom\tpasses_energy_threshold\tpasses_all_denser_steps"
        ]
    else:
        lines = [
            "value\texperiment_id\tconverged\tenergy_ev_per_atom\tpressure_gpa\t"
            "absolute_energy_shift_to_next_mev_per_atom"
        ]
    for row in rows:
        chosen = row["chosen"]
        value = "x".join(map(str, row["value"])) if isinstance(row["value"], list) else row["value"]
        common = (
            value,
            chosen["experiment_id"],
            chosen["converged"],
            chosen["energy_ev_per_atom"],
            chosen["pressure_gpa"],
        )
        if args.axis == "kpoint":
            values = common + (
                row["delta_to_next_mev_per_atom"],
                row["passes_energy_threshold"],
                row["passes_all_denser_steps"],
            )
        else:
            values = common + (row["absolute_energy_shift_to_next_mev_per_atom"],)
        lines.append("\t".join(str(item) for item in values))
    (args.output_directory / "summary.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if (passed or (args.axis == "smearing" and diagnostic_complete)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
