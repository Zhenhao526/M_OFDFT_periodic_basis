from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "analyze_s1_eos.py"
SPEC = importlib.util.spec_from_file_location("analyze_s1_eos", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AnalyzeS1EosTest(unittest.TestCase):
    def test_bm3_fit_recovers_synthetic_parameters(self) -> None:
        volumes = [14.4, 15.04, 15.52, 16.0, 16.48, 16.96, 17.6]
        energies = [MODULE.bm3_energy(v, -4.0, 16.1, 0.48, 4.3) for v in volumes]
        fit = MODULE.fit_bm3(volumes, energies)
        self.assertAlmostEqual(fit["v0_angstrom3_per_atom"], 16.1, places=3)
        self.assertAlmostEqual(fit["b0_ev_per_angstrom3"], 0.48, places=3)
        self.assertAlmostEqual(fit["b0_prime"], 4.3, places=2)
        self.assertLess(fit["max_abs_residual_mev_per_atom"], 1e-4)

    def test_relative_curve_ignores_constant_energy_shift(self) -> None:
        ratios = [0.9, 0.94, 0.97, 1.0, 1.03, 1.06, 1.1]
        standard = [
            {"volume_ratio": ratio, "energy_ev_per_atom": (ratio - 1.0) ** 2}
            for ratio in ratios
        ]
        half = [
            {"volume_ratio": ratio, "energy_ev_per_atom": (ratio - 1.0) ** 2 + 0.01}
            for ratio in ratios
        ]
        fit = {"v0_angstrom3_per_atom": 16.0}
        comparison = MODULE.compare_sigma_series(standard, half, fit, fit)
        self.assertEqual(comparison["status"], "accepted")
        self.assertAlmostEqual(comparison["max_relative_energy_difference_mev_per_atom"], 0.0)

    def test_strict_energy_threshold_rejects_exact_boundary(self) -> None:
        ratios = [0.9, 0.94, 0.97, 1.0, 1.03, 1.06, 1.1]
        standard = [{"volume_ratio": ratio, "energy_ev_per_atom": 0.0} for ratio in ratios]
        half = [{"volume_ratio": ratio, "energy_ev_per_atom": 0.0} for ratio in ratios]
        half[-1]["energy_ev_per_atom"] = 0.002
        fit = {"v0_angstrom3_per_atom": 16.0}
        comparison = MODULE.compare_sigma_series(standard, half, fit, fit)
        self.assertEqual(comparison["status"], "rejected")
        self.assertFalse(comparison["relative_energy_passed"])

    def test_strict_volume_threshold_rejects_exact_boundary(self) -> None:
        ratios = [0.9, 0.94, 0.97, 1.0, 1.03, 1.06, 1.1]
        points = [{"volume_ratio": ratio, "energy_ev_per_atom": 0.0} for ratio in ratios]
        standard_fit = {"v0_angstrom3_per_atom": 16.0}
        half_fit = {"v0_angstrom3_per_atom": 16.032}
        comparison = MODULE.compare_sigma_series(points, points, standard_fit, half_fit)
        self.assertEqual(comparison["status"], "rejected")
        self.assertFalse(comparison["equilibrium_volume_passed"])

    def test_endpoint_sampled_minimum_is_diagnostic_when_fit_is_bracketed(self) -> None:
        points = [
            {
                "energy_ev_per_atom": energy,
                "volume_per_atom_angstrom3": volume,
                "volume_ratio": ratio,
            }
            for ratio, volume, energy in (
                (0.90, 21.0, -1.0000),
                (0.94, 22.0, -0.9998),
                (0.97, 23.0, -0.9900),
                (1.00, 24.0, -0.9700),
            )
        ]
        fit = {
            "v0_angstrom3_per_atom": 21.4,
            "b0_gpa": 37.0,
            "max_abs_residual_mev_per_atom": 0.01,
        }
        passed, failures = MODULE._fit_quality(points, fit, 1.0)
        diagnostic = MODULE._sampled_shape_diagnostic(points)
        self.assertTrue(passed)
        self.assertEqual(failures, [])
        self.assertTrue(diagnostic["discrete_minimum_at_sampled_endpoint"])
        self.assertEqual(diagnostic["acceptance_role"], "diagnostic_only")

    def test_baseline_comparison_reports_shape_not_constant_offset(self) -> None:
        ratios = [0.9, 1.0, 1.1]
        ksdft = [
            {"volume_ratio": ratio, "energy_ev_per_atom": (ratio - 1.0) ** 2}
            for ratio in ratios
        ]
        ofdft = [
            {"volume_ratio": ratio, "energy_ev_per_atom": (ratio - 1.0) ** 2 + 2.0}
            for ratio in ratios
        ]
        ksdft_fit = {"v0_angstrom3_per_atom": 16.0, "b0_gpa": 80.0}
        ofdft_fit = {"v0_angstrom3_per_atom": 16.16, "b0_gpa": 76.0}
        comparison = MODULE.compare_baseline_series(
            ofdft, ksdft, ofdft_fit, ksdft_fit
        )
        self.assertEqual(comparison["status"], "diagnostic")
        self.assertAlmostEqual(
            comparison["max_abs_relative_energy_difference_mev_per_atom"], 0.0
        )
        self.assertAlmostEqual(
            comparison["equilibrium_volume_signed_difference_percent"], 1.0
        )
        self.assertAlmostEqual(
            comparison["bulk_modulus_signed_difference_percent"], -5.0
        )


if __name__ == "__main__":
    unittest.main()
