#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from analyze_s1_g1_three_layer_r1 import p0_metrics  # noqa: E402
from generate_s1_g1_three_layer_r1 import generate  # noqa: E402
from s1_g1_three_layer_common import (  # noqa: E402
    load_config,
    load_manifest,
    parse_upf_header,
    sha256_file,
)


class ThreeLayerR1Tests(unittest.TestCase):
    def test_manifest_and_generated_inputs_are_deterministic(self) -> None:
        rows = load_manifest(PROJECT_ROOT)
        self.assertEqual(len(rows), 18)
        first_hashes = {
            path: sha256_file(path)
            for directory in generate(PROJECT_ROOT)
            for path in directory.iterdir()
            if path.is_file()
        }
        second_hashes = {
            path: sha256_file(path)
            for directory in generate(PROJECT_ROOT)
            for path in directory.iterdir()
            if path.is_file()
        }
        self.assertEqual(first_hashes, second_hashes)

    def test_upf2_header_parser_counts_projectors(self) -> None:
        content = """<UPF version=\"2.0.1\">
<PP_HEADER pseudo_type=\"NC\" functional=\"PBE\" z_valence=\"3.0\" number_of_proj=\"2\" core_correction=\"T\"/>
<PP_NONLOCAL><PP_BETA.1 angular_momentum=\"0\"></PP_BETA.1><PP_BETA.2 angular_momentum=\"1\"></PP_BETA.2><PP_DIJ></PP_DIJ></PP_NONLOCAL>
</UPF>\n"""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "test.upf"
            path.write_text(content, encoding="utf-8")
            header = parse_upf_header(path)
        self.assertEqual(header["upf_version"], "2.0.1")
        self.assertEqual(header["number_of_proj"], 2)
        self.assertEqual(header["pp_beta_element_count"], 2)
        self.assertEqual(header["expanded_nonlocal_projectors_per_atom"], 4)
        self.assertTrue(header["pp_dij_present"])

    def test_p0_metrics_strict_thresholds(self) -> None:
        config = load_config(PROJECT_ROOT)

        def point(energy: float, pressure: float) -> dict:
            return {
                "thermodynamic_labels_ev_per_atom": {"E_ec": energy},
                "pressure_gpa": pressure,
            }

        results = {
            "S1-20260810-301": point(-10.0, 0.0),
            "S1-20260810-302": point(-9.9990, 0.0),
            "S1-20260810-303": point(-9.9995, 0.01),
            "S1-20260810-304": point(-20.0, 0.0),
            "S1-20260810-305": point(-19.9990, 0.0),
            "S1-20260810-306": point(-19.9995, 0.01),
        }
        metrics = p0_metrics(results, config)
        self.assertEqual(metrics["status"], "accepted")
        results["S1-20260810-303"] = point(-9.9980, 0.01)
        self.assertEqual(p0_metrics(results, config)["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
