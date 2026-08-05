#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ENERGY_PATTERN = re.compile(r"!FINAL_ETOT_IS\s+([-+0-9.eE]+)\s+eV")
PRESSURE_PATTERN = re.compile(r"#TOTAL-PRESSURE#.*?:\s*([-+0-9.eE]+)\s+kbar")
ELECTRON_PATTERN = re.compile(r"Autoset the number of electrons\s*=\s*([-+0-9.eE]+)")


def parse_log(text: str, expected_electrons: float, atom_count: int) -> dict:
    energy_matches = ENERGY_PATTERN.findall(text)
    pressure_matches = PRESSURE_PATTERN.findall(text)
    electron_matches = ELECTRON_PATTERN.findall(text)
    converged = "#SCF IS CONVERGED#" in text
    explicitly_not_converged = "!!SCF IS NOT CONVERGED!!" in text
    if (not converged and not explicitly_not_converged) or not energy_matches or not pressure_matches or not electron_matches:
        raise ValueError("missing SCF status, energy, pressure, or electron marker")
    energy_ev = float(energy_matches[-1])
    pressure_kbar = float(pressure_matches[-1])
    electron_count = float(electron_matches[-1])
    return {
        "atom_count": atom_count,
        "converged": converged,
        "failure_reason": None if converged else "scf_not_converged",
        "electron_count_expected": expected_electrons,
        "electron_count_reported": electron_count,
        "electron_count_nominal_relative_error": abs(electron_count - expected_electrons) / expected_electrons,
        "energy_ev": energy_ev,
        "energy_ev_per_atom": energy_ev / atom_count,
        "pressure_gpa": pressure_kbar * 0.1,
        "pressure_kbar": pressure_kbar,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_directory", type=Path)
    args = parser.parse_args()
    run_directory = args.run_directory.resolve()
    metadata = json.loads((run_directory / "input_metadata.json").read_text(encoding="utf-8"))
    logs = list(run_directory.glob("OUT.*/running_scf.log"))
    if len(logs) != 1:
        raise SystemExit(f"expected one running_scf.log, found {len(logs)}")
    result = parse_log(
        logs[0].read_text(encoding="utf-8", errors="replace"),
        float(metadata["expected_electrons"]),
        int(metadata["atom_count"]),
    )
    (run_directory / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["converged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
