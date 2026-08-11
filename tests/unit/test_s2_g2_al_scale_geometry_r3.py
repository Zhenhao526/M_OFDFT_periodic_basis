from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import s2_g2_al_scale_geometry_common_r3 as common


class S2G2AlScaleGeometryR3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = common.load_config(ROOT)
        cls.analysis = common.build_analysis(ROOT, cls.config)

    def test_selected_source_and_runtime_are_exact(self) -> None:
        runtime = self.analysis["runtime"]
        self.assertEqual(runtime["selected_evidence_commit"], self.config["selected_source"]["evidence_commit"])
        self.assertEqual(runtime["selected_source_candidate"], "r08_eta100_complementary")
        self.assertEqual(runtime["primitive_source"]["density_sha256"], self.config["primitive_source"]["density_sha256"])

    def test_geometry_denominator_rank_and_low_g_set_are_fixed(self) -> None:
        rows = self.analysis["geometry_metrics"]
        self.assertEqual([row["geometry_id"] for row in rows], [case["id"] for case in self.config["geometry_cases"]])
        self.assertEqual(len(rows), 7)
        self.assertTrue(all(row["basis_count"] == 23 for row in rows))
        self.assertTrue(all(row["effective_rank"] == 23 for row in rows))
        self.assertTrue(all(row["low_g_vectors_unchanged"] for row in rows))

    def test_all_geometry_cases_pass_unchanged_accuracy_gates(self) -> None:
        self.assertTrue(all(row["status"] == "accepted_geometry" for row in self.analysis["geometry_metrics"]))
        self.assertLess(self.analysis["summary"]["density_relative_l2_p95"], 0.02)
        self.assertLess(self.analysis["summary"]["fixed_kedf_error_p95_mev_per_atom"], 10.0)

    def test_supercell_cosets_g_sets_and_scale_cases_are_exact(self) -> None:
        cells = self.analysis["low_g_sets"]["cells"]
        self.assertEqual([cell["atom_count"] for cell in cells], [32, 108])
        self.assertEqual([cell["coset_position_count"] for cell in cells], [32, 108])
        self.assertEqual([cell["equilibrium_half_space_low_g_count"] for cell in cells], [194, 654])
        self.assertTrue(all(item["frozen_low_g_vectors_unchanged"] for cell in cells for item in cell["geometry_invariance"]))
        self.assertTrue(any(not item["threshold_reselection_matches_frozen"] for cell in cells for item in cell["geometry_invariance"]))
        self.assertEqual(len(self.analysis["scale_metrics"]), 14)
        self.assertTrue(all(row["status"] == "accepted_scale_tiling" for row in self.analysis["scale_metrics"]))

    def test_coefficient_fraction_and_per_atom_extensivity_pass(self) -> None:
        for row in self.analysis["scale_metrics"]:
            self.assertLess(row["effective_coefficient_fraction"], 0.3)
            self.assertLess(row["equivalent_supercell_energy_difference_mev_per_atom"], 1.0)
            self.assertLess(row["electron_number_relative_error"], 1e-10)

    def test_disposition_preserves_open_physics_boundaries(self) -> None:
        summary = self.analysis["summary"]
        self.assertEqual(summary["status"], "accepted_periodic_tiling_geometry_scale_pilot")
        self.assertTrue(all(summary["gates"].values()))
        self.assertFalse(summary["limitations"]["localized_108_atom_ks_reference_validated"])
        self.assertFalse(summary["limitations"]["eggbox_energy_or_pseudoforce_validated"])
        self.assertFalse(summary["limitations"]["full_large_cell_gram_condition_validated"])
        self.assertFalse(summary["limitations"]["g2_overall_accepted"])

    def test_scope_threshold_and_geometry_tampering_fail_closed(self) -> None:
        tampered = copy.deepcopy(self.config)
        tampered["scope"]["localized_perturbation_reference_enabled"] = True
        with self.assertRaises(ValueError):
            common.validate_config(tampered)
        tampered = copy.deepcopy(self.config)
        tampered["acceptance"]["fixed_kedf_non_scf_total_energy_error_p95_mev_per_atom"] = 20.0
        with self.assertRaises(ValueError):
            common.validate_config(tampered)
        tampered = copy.deepcopy(self.config)
        tampered["geometry_cases"][1]["deformation"][0][0] = 0.99
        with self.assertRaises(ValueError):
            common.validate_config(tampered)

    def test_output_rendering_is_deterministic(self) -> None:
        first = common.render_outputs(self.analysis)
        second = common.render_outputs(self.analysis)
        self.assertEqual(first, second)
        self.assertEqual(sorted(first), sorted(self.config["output"]["files"]))
        self.assertIn(b"accepted_periodic_tiling_geometry_scale_pilot", first["summary.json"])


if __name__ == "__main__":
    unittest.main()
