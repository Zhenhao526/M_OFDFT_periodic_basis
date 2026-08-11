from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import s2_g2_al1_basis_convergence_common_r2 as common


class S2G2Al1BasisConvergenceR2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = common.load_config(ROOT)
        cls.analysis = common.build_analysis(ROOT, cls.config)

    def test_previous_pilot_and_source_are_exact(self) -> None:
        runtime = self.analysis["runtime"]
        self.assertEqual(runtime["previous_pilot"]["status"], self.config["previous_pilot"]["required_status"])
        self.assertEqual(runtime["previous_pilot"]["summary_sha256"], self.config["previous_pilot"]["summary_sha256"])
        self.assertEqual(runtime["source"]["density_sha256"], self.config["source"]["density_sha256"])
        self.assertAlmostEqual(runtime["source"]["integrated_electrons"], 3.0, places=9)

    def test_matrix_denominator_and_low_g_shells_are_exact(self) -> None:
        self.assertEqual(len(common.candidate_specs(self.config)), 48)
        shells = self.analysis["basis_spectrum"]["low_g_shells"]
        self.assertEqual([row["half_space_vector_count"] for row in shells], [7, 13, 25, 29, 32, 56])
        self.assertEqual([row["real_function_count"] for row in shells], [14, 26, 50, 58, 64, 112])
        self.assertEqual(self.analysis["summary"]["compressed_candidate_count"], 48)

    def test_reference_and_all_explicit_complementary_pairs_are_evaluated(self) -> None:
        summary = self.analysis["summary"]
        self.assertEqual(len(summary["metrics"]), 49)
        self.assertEqual(summary["metrics"][0]["status"], "accepted_reference")
        self.assertEqual(summary["pair_count"], 24)
        self.assertEqual(summary["accepted_pair_count"], 24)

    def test_converged_complementary_candidate_is_selected(self) -> None:
        summary = self.analysis["summary"]
        self.assertEqual(summary["status"], "accepted_converged_candidate_exists")
        selected = summary["selected_candidate"]
        self.assertIsNotNone(selected)
        self.assertEqual(selected["gauge"], "complementary")
        self.assertTrue(all(selected["gates"].values()))
        self.assertTrue(selected["pair_equivalence"])
        eligible = [
            row for row in summary["metrics"]
            if row["status"] == "accepted_convergence" and row["gauge"] == "complementary"
        ]
        expected = min(eligible, key=lambda row: (row["basis_count"], row["effective_condition_number"], row["candidate_id"]))
        self.assertEqual(selected["candidate_id"], expected["candidate_id"])

    def test_selected_candidate_passes_unchanged_wt_gate(self) -> None:
        selected = self.analysis["summary"]["selected_candidate"]
        self.assertLess(abs(selected["errors_mev_per_atom"]["fixed_kedf"]), 10.0)
        self.assertLess(selected["density_relative_l2"], 0.01)
        self.assertLess(selected["effective_condition_number"], 1e8)

    def test_complementary_gauge_improves_conditioning_for_selected_pair(self) -> None:
        selected = self.analysis["summary"]["selected_candidate"]
        pair_id = selected["candidate_id"].rsplit("_", 1)[0]
        pair = next(row for row in self.analysis["pairs"] if row["pair_id"] == pair_id)
        self.assertEqual(pair["status"], "accepted_equivalent_pair")
        self.assertGreater(pair["condition_number_ratio_explicit_over_complementary"], 1.0)

    def test_threshold_scope_and_matrix_tampering_fail_closed(self) -> None:
        tampered = copy.deepcopy(self.config)
        tampered["acceptance"]["fixed_kedf_abs_error_strict_lt_mev_per_atom"] = 20.0
        with self.assertRaises(ValueError):
            common.validate_config(tampered)
        tampered = copy.deepcopy(self.config)
        tampered["scope"]["larger_cells_enabled"] = True
        with self.assertRaises(ValueError):
            common.validate_config(tampered)
        tampered = copy.deepcopy(self.config)
        tampered["convergence_matrix"]["low_g_eta_levels"].append(2.2)
        with self.assertRaises(ValueError):
            common.validate_config(tampered)

    def test_output_rendering_is_deterministic(self) -> None:
        first = common.render_outputs(self.analysis)
        second = common.render_outputs(self.analysis)
        self.assertEqual(first, second)
        self.assertEqual(sorted(first), sorted(self.config["output"]["files"]))
        self.assertIn(b"accepted_converged_candidate_exists", first["summary.json"])


if __name__ == "__main__":
    unittest.main()
