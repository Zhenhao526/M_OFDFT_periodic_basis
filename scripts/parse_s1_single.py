#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ENERGY_PATTERN = re.compile(r"!FINAL_ETOT_IS\s+([-+0-9.eE]+)\s+eV")
ZERO_TEMP_ENERGY_PATTERN = re.compile(
    r"E_KS\(sigma->0\)\s+[-+0-9.eE]+\s+([-+0-9.eE]+)"
)
ENTROPY_MINUS_TS_PATTERN = re.compile(
    r"E_entropy\(-TS\)\s+[-+0-9.eE]+\s+([-+0-9.eE]+)"
)
PRESSURE_PATTERN = re.compile(r"#TOTAL-PRESSURE#.*?:\s*([-+0-9.eE]+)\s+kbar")
ELECTRON_PATTERN = re.compile(r"Autoset the number of electrons\s*=\s*([-+0-9.eE]+)")


def parse_log(text: str, expected_electrons: float, atom_count: int, solver: str = "unknown") -> dict:
    energy_matches = ENERGY_PATTERN.findall(text)
    zero_temp_energy_matches = ZERO_TEMP_ENERGY_PATTERN.findall(text)
    entropy_minus_ts_matches = ENTROPY_MINUS_TS_PATTERN.findall(text)
    pressure_matches = PRESSURE_PATTERN.findall(text)
    electron_matches = ELECTRON_PATTERN.findall(text)
    converged = "#SCF IS CONVERGED#" in text
    explicitly_not_converged = "!!SCF IS NOT CONVERGED!!" in text
    if (not converged and not explicitly_not_converged) or not energy_matches or not pressure_matches or not electron_matches:
        raise ValueError("missing SCF status, energy, pressure, or electron marker")
    energy_ev = float(energy_matches[-1])
    zero_temp_energy_ev = (
        float(zero_temp_energy_matches[-1]) if zero_temp_energy_matches else None
    )
    entropy_minus_ts_ev = (
        float(entropy_minus_ts_matches[-1]) if entropy_minus_ts_matches else None
    )
    if solver == "ksdft" and (zero_temp_energy_ev is None or entropy_minus_ts_ev is None):
        raise ValueError("missing KS zero-temperature extrapolated energy or entropy marker")
    internal_energy_ev = (
        energy_ev - entropy_minus_ts_ev if entropy_minus_ts_ev is not None else None
    )
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
        "energy_ev_kind": "helmholtz_free_energy" if solver == "ksdft" else "total_energy",
        "free_energy_ev": energy_ev if solver == "ksdft" else None,
        "free_energy_ev_per_atom": energy_ev / atom_count if solver == "ksdft" else None,
        "entropy_minus_ts_ev": entropy_minus_ts_ev,
        "entropy_minus_ts_ev_per_atom": (
            entropy_minus_ts_ev / atom_count if entropy_minus_ts_ev is not None else None
        ),
        "internal_energy_ev": internal_energy_ev,
        "internal_energy_ev_per_atom": (
            internal_energy_ev / atom_count if internal_energy_ev is not None else None
        ),
        "zero_temp_extrapolated_energy_ev": zero_temp_energy_ev,
        "zero_temp_extrapolated_energy_ev_per_atom": (
            zero_temp_energy_ev / atom_count if zero_temp_energy_ev is not None else None
        ),
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
        str(metadata.get("solver", "unknown")),
    )
    (run_directory / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["converged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
