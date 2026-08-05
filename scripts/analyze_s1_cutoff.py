#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("run_directories", nargs="+", type=Path)
    args = parser.parse_args()

    attempts_by_cutoff: dict[float, list[dict]] = defaultdict(list)
    for run_directory in args.run_directories:
        run_directory = run_directory.resolve()
        result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))
        input_metadata = json.loads((run_directory / "input_metadata.json").read_text(encoding="utf-8"))
        experiment_metadata = json.loads(
            (run_directory / "experiment_metadata.json").read_text(encoding="utf-8")
        )
        cutoff = float(input_metadata["ecutwfc_ry"])
        attempts_by_cutoff[cutoff].append(
            {
                "code_commit": experiment_metadata["code_commit"],
                "converged": bool(result["converged"]),
                "energy_ev_per_atom": result["energy_ev_per_atom"],
                "experiment_id": run_directory.name,
                "failure_reason": result.get("failure_reason"),
                "pressure_gpa": result["pressure_gpa"],
            }
        )

    rows = []
    for cutoff in sorted(attempts_by_cutoff):
        attempts = sorted(attempts_by_cutoff[cutoff], key=lambda item: item["experiment_id"])
        converged = [attempt for attempt in attempts if attempt["converged"]]
        chosen = converged[-1] if converged else attempts[-1]
        rows.append({"ecutwfc_ry": cutoff, "attempts": attempts, "chosen": chosen})

    for index, row in enumerate(rows):
        if index == len(rows) - 1:
            row["delta_to_next_mev_per_atom"] = None
            row["delta_pressure_to_next_gpa"] = None
            row["passes_next_step"] = None
            continue
        current = row["chosen"]
        following = rows[index + 1]["chosen"]
        energy_delta = abs(current["energy_ev_per_atom"] - following["energy_ev_per_atom"]) * 1000.0
        pressure_delta = abs(current["pressure_gpa"] - following["pressure_gpa"])
        row["delta_to_next_mev_per_atom"] = energy_delta
        row["delta_pressure_to_next_gpa"] = pressure_delta
        row["passes_next_step"] = (
            current["converged"]
            and following["converged"]
            and energy_delta < 1.0
            and pressure_delta < 0.02
        )

    recommended = next(
        (row["ecutwfc_ry"] for row in rows[:-1] if row["passes_next_step"]), None
    )
    payload = {
        "failed_attempts": sum(
            not attempt["converged"]
            for attempts in attempts_by_cutoff.values()
            for attempt in attempts
        ),
        "passed": recommended is not None,
        "recommended_ecutwfc_ry": recommended,
        "rows": rows,
        "thresholds": {"energy_mev_per_atom": 1.0, "pressure_gpa": 0.02},
    }
    args.output_directory.mkdir(parents=True, exist_ok=True)
    (args.output_directory / "summary.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        "ecutwfc_ry\texperiment_id\tconverged\tenergy_ev_per_atom\tpressure_gpa\t"
        "delta_to_next_mev_per_atom\tdelta_pressure_to_next_gpa\tpasses_next_step"
    ]
    for row in rows:
        chosen = row["chosen"]
        lines.append(
            "\t".join(
                str(value)
                for value in (
                    row["ecutwfc_ry"],
                    chosen["experiment_id"],
                    chosen["converged"],
                    chosen["energy_ev_per_atom"],
                    chosen["pressure_gpa"],
                    row["delta_to_next_mev_per_atom"],
                    row["delta_pressure_to_next_gpa"],
                    row["passes_next_step"],
                )
            )
        )
    (args.output_directory / "summary.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
