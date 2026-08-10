#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from analyze_s1_g1_three_layer_analysis_r3 import rejection_signatures  # noqa: E402


class ThreeLayerAnalysisR3Tests(unittest.TestCase):
    def test_exact_scientific_rejection_signature(self) -> None:
        summary = {
            "p0_recovery": {"al_hard": {"status": "accepted"}},
            "al_three_layer_eos": {
                "fits": {name: {"status": "accepted"} for name in ("ks_nl", "ks_l", "of_l")},
                "ks_l_vs_ks_nl": {
                    "equilibrium_volume_difference_percent": 0.766,
                    "bulk_modulus_difference_percent": 2.466,
                },
            },
        }
        config = {"acceptance": {"al_ksl_vs_ksnl_equilibrium_volume_difference_percent_max": 0.5, "al_ksl_vs_ksnl_bulk_modulus_difference_percent_max": 10.0}}
        self.assertEqual(rejection_signatures(summary, config), ["al_ks_l_vs_ks_nl_equilibrium_volume_difference_percent"])

    def test_extra_failure_is_not_hidden(self) -> None:
        summary = {
            "p0_recovery": {"al_hard": {"status": "rejected"}},
            "al_three_layer_eos": {
                "fits": {"ks_nl": {"status": "rejected"}, "ks_l": {"status": "accepted"}, "of_l": {"status": "accepted"}},
                "ks_l_vs_ks_nl": {"equilibrium_volume_difference_percent": 0.8, "bulk_modulus_difference_percent": 12.0},
            },
        }
        config = {"acceptance": {"al_ksl_vs_ksnl_equilibrium_volume_difference_percent_max": 0.5, "al_ksl_vs_ksnl_bulk_modulus_difference_percent_max": 10.0}}
        self.assertEqual(len(rejection_signatures(summary, config)), 4)


if __name__ == "__main__":
    unittest.main()
