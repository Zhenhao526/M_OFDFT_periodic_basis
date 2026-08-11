from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import s2_g2_architecture_common_r1 as common


class S2G2ArchitectureCandidatesR1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = common.load_config(ROOT)

    def test_four_by_three_case_matrix_and_ids_are_exact(self) -> None:
        cases = common.expected_cases(self.config)
        self.assertEqual(len(cases), 12)
        self.assertEqual([case["case_id"] for case in cases], [f"S2-G2-20260811-{i:03d}" for i in range(1, 13)])
        self.assertEqual({case["candidate_id"] for case in cases}, set(common.CANDIDATE_ORDER))
        self.assertEqual({case["atom_count"] for case in cases}, {1, 32, 108})
        self.assertTrue(all(case["solver_started"] is False for case in cases))

    def test_supercell_matrices_have_exact_atom_count_determinants(self) -> None:
        observed = []
        for cell in self.config["cell_ladder"]:
            observed.append(abs(common.determinant3(cell["supercell_matrix"])))
        self.assertEqual(observed, [1, 32, 108])

    def test_candidate_g0_and_complementarity_contracts_are_distinct(self) -> None:
        common.validate_config(self.config)
        candidates = self.config["candidates"]
        self.assertEqual(candidates["atomic_low_g_explicit"]["g0_convention"], "g0_is_unique_charge_channel")
        self.assertIn("projected_out", candidates["atomic_low_g_complementary"]["density_representation"])
        self.assertFalse(candidates["pw_fft_reference"]["may_be_selected_as_compressed_winner"])

    def test_scientific_scope_and_threshold_tampering_fail_closed(self) -> None:
        for mutate in (
            lambda cfg: cfg["scope"].__setitem__("mg_enabled", True),
            lambda cfg: cfg["scope"].__setitem__("ml_enabled", True),
            lambda cfg: cfg["acceptance"].__setitem__("electron_number_relative_error_strict_lt", 1e-6),
            lambda cfg: cfg["rank_contract"].__setitem__("per_geometry_hard_rank_selection_forbidden", False),
        ):
            tampered = copy.deepcopy(self.config)
            mutate(tampered)
            with self.assertRaises(ValueError):
                common.validate_config(tampered)

    def test_registered_artifacts_are_deterministic_and_exact(self) -> None:
        artifacts = common.registered_artifacts(self.config)
        self.assertEqual(len(artifacts), 13)
        common.validate_registered_artifacts(ROOT, self.config)
        self.assertEqual(artifacts, common.registered_artifacts(self.config))

    def test_reference_density_and_g1_ancestry_are_exact(self) -> None:
        common.validate_sources(ROOT, self.config)

    def test_manifest_and_metadata_tampering_are_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative, data in common.registered_artifacts(self.config).items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            (root / common.MANIFEST_REL).write_text("tampered\n")
            with self.assertRaises(ValueError):
                common.validate_registered_artifacts(root, self.config)


if __name__ == "__main__":
    unittest.main()
