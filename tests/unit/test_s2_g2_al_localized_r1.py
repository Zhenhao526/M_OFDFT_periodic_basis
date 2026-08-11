#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import analyze_s2_g2_al_localized_r1 as analyzer
import s2_g2_al_localized_common_r1 as common


class LocalizedPilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[2]
        cls.config = common.load_config(cls.root)

    def test_01_config_contract(self):
        common.validate_config(self.config)
        self.assertEqual(self.config["scope"]["selected_candidate_id"], "r08_eta100_complementary")
        self.assertEqual(self.config["acceptance"]["required_total_basis_count"], 2173)

    def test_02_geometry_is_108_and_localized(self):
        geometry = common.localized_structure_payload(self.root, self.config)
        self.assertEqual(geometry["atom_count"], 108)
        self.assertAlmostEqual(abs(np.linalg.det(np.asarray(self.config["reference"]["supercell_matrix"]))), 108.0)
        delta = np.asarray(geometry["cartesian_displacement_angstrom"])
        self.assertTrue(np.allclose(delta, [0.05, 0.0, 0.0], atol=0.0, rtol=0.0))
        self.assertEqual(len({tuple(np.round(x, 14)) for x in geometry["undisplaced_fractional_positions"]}), 108)

    def test_03_registered_inputs_are_deterministic(self):
        first = common.registered_input_payloads(self.root, self.config)
        second = common.registered_input_payloads(self.root, self.config)
        self.assertEqual(first, second)
        self.assertIn(b"nbands 180", first["INPUT"])
        self.assertIn(b"1 1 1 0 0 0", first["KPT"])
        self.assertEqual(first["STRU"].count(b" 1 1 1\n"), 108)

    def test_04_shift_is_periodic_and_norm_preserving(self):
        axes = np.meshgrid(*[np.arange(n) / n for n in (8, 6, 4)], indexing="ij")
        rho = 0.3 + np.exp(-18.0 * sum((axis - 0.4) ** 2 for axis in axes))
        shifted = analyzer.shifted_density(rho, 0.375, 0)
        self.assertAlmostEqual(float(np.sum(shifted)), float(np.sum(rho)), places=12)
        self.assertLess(abs(float(np.linalg.norm(np.fft.fftn(shifted))) - float(np.linalg.norm(np.fft.fftn(rho)))) / np.linalg.norm(np.fft.fftn(rho)), 2e-4)

    def test_05_low_g_mask_denominator(self):
        mask = common.low_g_mask((12, 12, 12), [(1, 0, 0), (0, 1, 0)])
        self.assertEqual(int(np.count_nonzero(mask == 0.0)), 5)
        self.assertEqual(mask[1, 0, 0], 0.0)
        self.assertEqual(mask[-1, 0, 0], 0.0)

    def test_06_rank_detects_full_and_deficient(self):
        full = common.normalized_rank(np.eye(6), 1e-10)
        self.assertEqual(full["atomic_rank"], 6)
        deficient = common.normalized_rank(np.asarray([[1.0, 0.0, 0.0], [0.0, 1.0, 1.0], [0.0, 1.0, 1.0]]), 1e-10)
        self.assertEqual(deficient["atomic_rank"], 2)

    def test_07_output_denominator_is_self_contained(self):
        names = set(self.config["output"]["files"])
        self.assertIn("reference_density.cube.zst", names)
        self.assertIn("reference_log.txt", names)
        self.assertEqual(len(names), 11)

    def test_08_scientific_disposition_preserves_open_limits(self):
        self.assertEqual(self.config["limitations"], {
            "low_q_response_validated": False,
            "mg_validated": False,
            "g2c_performance_validated": False,
            "g2_overall_accepted": False,
        })


if __name__ == "__main__":
    unittest.main()
