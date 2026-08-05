#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ENERGY_PATTERN = re.compile(r"!FINAL_ETOT_IS\s+([-+0-9.eE]+)\s+eV")


def read_energy(log_path: Path) -> float:
    text = log_path.read_text(encoding="utf-8", errors="replace")
    if "#SCF IS CONVERGED#" not in text:
        raise RuntimeError(f"SCF did not converge: {log_path}")
    matches = ENERGY_PATTERN.findall(text)
    if not matches:
        raise RuntimeError(f"Final energy not found: {log_path}")
    return float(matches[-1])


def evaluate(experiment_root: Path, natoms: int, tolerance_mev_per_atom: float) -> dict:
    energies = []
    for repeat in ("repeat1", "repeat2"):
        logs = sorted((experiment_root / repeat).glob("OUT.*/running_scf.log"))
        if len(logs) != 1:
            raise RuntimeError(f"Expected one running_scf.log in {repeat}, found {len(logs)}")
        energies.append(read_energy(logs[0]))

    difference_mev_per_atom = abs(energies[0] - energies[1]) * 1000.0 / natoms
    passed = difference_mev_per_atom < tolerance_mev_per_atom
    return {
        "natoms": natoms,
        "energies_ev": energies,
        "difference_mev_per_atom": difference_mev_per_atom,
        "tolerance_mev_per_atom": tolerance_mev_per_atom,
        "passed": passed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("experiment_root", type=Path)
    parser.add_argument("--natoms", type=int, default=4)
    parser.add_argument("--tolerance-mev-per-atom", type=float, default=0.1)
    args = parser.parse_args()

    result = evaluate(args.experiment_root, args.natoms, args.tolerance_mev_per_atom)
    output_path = args.experiment_root / "smoke_result.json"
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

