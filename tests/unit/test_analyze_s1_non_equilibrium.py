from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from analyze_s1_non_equilibrium import compare_series  # noqa: E402


RATIOS = (0.90, 0.94, 0.97, 1.00, 1.03, 1.06, 1.10)


def points(energy_changes=None, pressure_changes=None):
    energy_changes = energy_changes or {}
    pressure_changes = pressure_changes or {}
    payload = []
    for index, ratio in enumerate(RATIOS):
        payload.append(
            {
                "volume_ratio": ratio,
                "experiment_id": f"X-{index}",
                "energy_ev_per_atom": (ratio - 1.0) ** 2 + energy_changes.get(ratio, 0.0),
                "pressure_gpa": (1.0 - ratio) * 10.0 + pressure_changes.get(ratio, 0.0),
            }
        )
    return payload


class AnalyzeS1NonEquilibriumTest(unittest.TestCase):
    def test_curve_anchor_ignores_constant_energy_shift(self) -> None:
        baseline = points()
        refined = points({ratio: 7.5 for ratio in RATIOS})
        result = compare_series(baseline, refined, 1.0, 1.0, 0.02)
        self.assertEqual(result["status"], "accepted")
        self.assertAlmostEqual(
            result["max_relative_energy_difference_mev_per_atom"], 0.0, places=9
        )

    def test_energy_threshold_is_strict(self) -> None:
        baseline = points()
        refined = points({0.90: 0.001})
        result = compare_series(baseline, refined, 1.0, 1.0, 0.02)
        self.assertEqual(result["status"], "rejected")
        self.assertFalse(result["relative_energy_passed"])
        self.assertAlmostEqual(
            result["max_relative_energy_difference_mev_per_atom"], 1.0, places=9
        )

    def test_pressure_threshold_is_strict_for_cutoff(self) -> None:
        baseline = points()
        refined = points(pressure_changes={0.90: 0.02})
        result = compare_series(baseline, refined, 1.0, 1.0, 0.02)
        self.assertEqual(result["status"], "rejected")
        self.assertFalse(result["pressure_passed"])

    def test_kmesh_pressure_is_diagnostic_only(self) -> None:
        baseline = points()
        refined = points(pressure_changes={0.90: 4.0})
        result = compare_series(baseline, refined, 1.0, 2.0, None)
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["pressure_acceptance_role"], "diagnostic_only")

    def test_mismatched_ratios_are_indeterminate(self) -> None:
        result = compare_series(points(), points()[:-1], 1.0, 1.0, 0.02)
        self.assertEqual(result["status"], "indeterminate")


if __name__ == "__main__":
    unittest.main()
