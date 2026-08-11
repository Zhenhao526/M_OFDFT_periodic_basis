from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import s2_g2_al1_pilot_common_r1 as common


class S2G2Al1ProjectionOperatorPilotR1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = common.load_config(ROOT)
        cls.analysis = common.build_analysis(ROOT, cls.config)

    def test_source_density_and_runtime_are_exact(self) -> None:
        source = self.analysis["runtime"]["source"]
        self.assertEqual(source["density_sha256"], self.config["source"]["density_sha256"])
        self.assertAlmostEqual(source["integrated_electrons"], 3.0, places=9)
        self.assertEqual(self.analysis["runtime"]["versions"], self.config["runtime"]["versions"])

    def test_low_g_vector_denominator_is_exact(self) -> None:
        vectors = [row["integer_vector"] for row in self.analysis["basis_spectrum"]["low_g_half_vectors"]]
        self.assertEqual(vectors, [[0, 0, 1], [0, 1, 0], [1, 0, 0], [1, 1, 1], [0, 1, 1], [1, 0, 1], [1, 1, 0]])
        self.assertEqual(self.analysis["basis_spectrum"]["real_low_g_function_count"], 14)

    def test_four_routes_are_evaluated_and_reference_is_exact(self) -> None:
        rows = {row["candidate_id"]: row for row in self.analysis["metrics"]}
        self.assertEqual(list(rows), self.config["candidate_order"])
        reference = rows["pw_fft_reference"]
        self.assertEqual(reference["status"], "accepted_reference")
        self.assertEqual(reference["density_relative_l2"], 0.0)
        self.assertTrue(all(reference["gates"].values()))

    def test_low_g_improves_density_and_complementary_improves_conditioning(self) -> None:
        rows = {row["candidate_id"]: row for row in self.analysis["metrics"]}
        atomic = rows["atomic_fft"]
        explicit = rows["atomic_low_g_explicit"]
        complementary = rows["atomic_low_g_complementary"]
        self.assertGreater(atomic["density_relative_l2"], 0.01)
        self.assertLess(explicit["density_relative_l2"], 0.01)
        self.assertAlmostEqual(explicit["density_relative_l2"], complementary["density_relative_l2"], places=13)
        self.assertLess(complementary["effective_condition_number"], explicit["effective_condition_number"] / 10.0)

    def test_gate_disposition_is_evidence_valid_even_when_no_candidate_passes(self) -> None:
        summary = self.analysis["summary"]
        self.assertEqual(summary["status"], "evidence_valid_no_candidate_passes_all_g2a_pilot_gates")
        self.assertEqual(summary["accepted_candidates"], [])
        self.assertEqual(summary["new_solver_run_count"], 0)
        rows = {row["candidate_id"]: row for row in summary["metrics"]}
        self.assertIn("density_l2", rows["atomic_fft"]["failed_gates"])
        self.assertIn("fixed_kedf", rows["atomic_low_g_explicit"]["failed_gates"])
        self.assertIn("fixed_kedf", rows["atomic_low_g_complementary"]["failed_gates"])

    def test_threshold_and_scope_tampering_fail_closed(self) -> None:
        for key, value in (("density_relative_l2_strict_lt", 0.1), ("fixed_kedf_abs_error_strict_lt_mev_per_atom", 20.0)):
            tampered = copy.deepcopy(self.config)
            tampered["acceptance"][key] = value
            with self.assertRaises(ValueError):
                common.validate_config(tampered)
        tampered = copy.deepcopy(self.config)
        tampered["scope"]["ml_enabled"] = True
        with self.assertRaises(ValueError):
            common.validate_config(tampered)

    def test_output_rendering_is_deterministic(self) -> None:
        first = common.render_outputs(self.analysis)
        second = common.render_outputs(self.analysis)
        self.assertEqual(first, second)
        self.assertEqual(sorted(first), sorted(self.config["output"]["files"]))
        self.assertIn(b"no_candidate_passes", first["summary.json"])


if __name__ == "__main__":
    unittest.main()
