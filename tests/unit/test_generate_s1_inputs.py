from __future__ import annotations

import importlib.util
import math
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "generate_s1_inputs.py"
SPEC = importlib.util.spec_from_file_location("generate_s1_inputs", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class GenerateS1InputsTest(unittest.TestCase):
    def test_fcc_primitive_volume_and_scaling(self) -> None:
        material = {"structure": "fcc_primitive", "a0_angstrom": 4.05}
        cell, positions = MODULE.base_structure(material)
        base_volume = abs(MODULE.determinant(cell))
        self.assertEqual(len(positions), 1)
        self.assertAlmostEqual(base_volume, 4.05**3 / 4.0, places=12)
        scaled = MODULE.scaled_cell(cell, 0.90)
        self.assertAlmostEqual(abs(MODULE.determinant(scaled)) / base_volume, 0.90, places=12)

    def test_hcp_primitive_volume_and_labels(self) -> None:
        material = {
            "structure": "hcp_primitive",
            "a0_angstrom": 3.2094,
            "c0_angstrom": 5.2108,
        }
        cell, positions = MODULE.base_structure(material)
        expected = math.sqrt(3.0) * 3.2094**2 * 5.2108 / 2.0
        self.assertEqual(len(positions), 2)
        self.assertAlmostEqual(abs(MODULE.determinant(cell)), expected, places=12)
        self.assertEqual(MODULE.ratio_label(0.90), "v090")
        self.assertEqual(MODULE.ratio_label(1.10), "v110")


if __name__ == "__main__":
    unittest.main()
