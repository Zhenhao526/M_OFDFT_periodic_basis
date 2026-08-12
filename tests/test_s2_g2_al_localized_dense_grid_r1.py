from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import analyze_s2_g2_al_localized_dense_grid_r1 as dense


class DenseGridTests(unittest.TestCase):
    def setUp(self):
        self.config = dense.load_config(ROOT)

    def test_01_registered_contract(self):
        dense.validate_config(self.config)
        self.assertEqual(self.config["analysis"]["target_grids"], [128, 144])
        self.assertEqual(self.config["acceptance"]["maximum_rank_deficiency"], 2)
        self.assertEqual(self.config["acceptance"]["projection_excess_pseudoforce_max_ev_per_angstrom"], 0.002)

    def test_02_fourier_resampling_preserves_constant_and_integral(self):
        x = np.arange(12.0).reshape(3, 2, 2)
        y = dense.fourier_resample_periodic(x, 8)
        self.assertEqual(y.shape, (8, 8, 8))
        self.assertAlmostEqual(float(x.mean()), float(y.mean()), places=13)
        one = dense.fourier_resample_periodic(np.ones((4, 4, 4)), 9)
        self.assertLess(float(np.max(np.abs(one - 1.0))), 1e-13)

    def test_03_principal_angles(self):
        q = np.eye(5)[:, :2]
        self.assertLess(max(dense.principal_angles_degrees(q, q)), 1e-12)
        r = np.eye(5)[:, 2:4]
        self.assertAlmostEqual(max(dense.principal_angles_degrees(q, r)), 90.0, places=12)

    def test_04_cyclic_force(self):
        n = 16; step = 0.01
        energies = [np.sin(2 * np.pi * i / n) for i in range(n)]
        forces = dense.cyclic_pseudoforce(energies, step)
        self.assertEqual(len(forces), n)
        self.assertGreater(max(forces), 0.0)
        self.assertEqual(dense.cyclic_pseudoforce([1.0] * n, step), [0.0] * n)

    def test_05_sources_and_historical_diagnostic(self):
        dense.validate_sources(ROOT, self.config)
        old = json.loads((ROOT / self.config["source"]["diagnostic_96_path"]).read_text())
        refined = json.loads((ROOT / self.config["source"]["diagnostic_128_path"]).read_text())
        self.assertGreater(old["projection_excess_pseudoforce_max_ev_per_angstrom"], 0.002)
        self.assertLess(refined["projection_excess_pseudoforce_max_ev_per_angstrom"], 0.002)


if __name__ == "__main__":
    unittest.main()
