from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "analyze_s1_ks_axis.py"
SPEC = importlib.util.spec_from_file_location("analyze_s1_ks_axis", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AnalyzeS1KsAxisTest(unittest.TestCase):
    def test_kpoint_axis_key(self) -> None:
        metadata = {"kmesh": [16, 16, 10]}
        self.assertEqual(MODULE.axis_key(metadata, "kpoint"), (16, 16, 10))
        self.assertEqual(MODULE.display_key((16, 16, 10), "kpoint"), [16, 16, 10])

    def test_smearing_axis_key(self) -> None:
        metadata = {"smearing_sigma_ry": 0.00367493}
        self.assertEqual(MODULE.axis_key(metadata, "smearing"), (0.00367493,))
        self.assertEqual(MODULE.display_key((0.00367493,), "smearing"), 0.00367493)

    def test_unknown_axis_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported KS scan axis"):
            MODULE.axis_key({}, "cutoff")

    def test_tail_stability_rejects_early_isolated_pass(self) -> None:
        rows = [
            {"passes_energy_threshold": True},
            {"passes_energy_threshold": False},
            {"passes_energy_threshold": True},
            {"passes_energy_threshold": None},
        ]
        MODULE.mark_tail_stability(rows)
        self.assertEqual(
            [row["passes_all_denser_steps"] for row in rows],
            [False, False, True, None],
        )


if __name__ == "__main__":
    unittest.main()
