#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from analyze_s1_g1_three_layer_al_followup_r1 import evaluate_gates  # noqa: E402
from parse_s1_g1_three_layer_al_followup_r1 import parse_eig_occ, parse_force_stress  # noqa: E402
from s1_g1_three_layer_al_followup_common import (  # noqa: E402
    geometry_payload_bytes,
    parse_cpu_list,
    replace_species_for_nlpp,
)


class AlDomainFollowupTests(unittest.TestCase):
    def config(self) -> dict:
        return {
            "acceptance": {
                "electron_relative_error_strictly_less_than": 1e-10,
                "last_band_occupation_strictly_less_than": 1e-8,
                "stress_symmetry_abs_kbar_max": 1e-10,
                "stress_trace_pressure_abs_kbar_strictly_less_than": 1e-6,
                "strain_anchored_difference_mev_per_atom_max": 20.0,
                "endpoint_anchored_k_difference_mev_per_atom_strictly_less_than": 2.0,
                "endpoint_anchored_cutoff_difference_mev_per_atom_strictly_less_than": 1.0,
                "endpoint_cutoff_pressure_difference_gpa_strictly_less_than": 0.02,
            }
        }

    def test_geometry_payload_survives_only_species_replacement(self) -> None:
        source = (
            "ATOMIC_SPECIES\nAl 26.9815385 al.gga.psp blps\n\n"
            "LATTICE_CONSTANT\n1.8897261254578281\n\nLATTICE_VECTORS\n"
            "0 1 1\n1 0 1\n1 1 0\n\nATOMIC_POSITIONS\nDirect\n\nAl\n0.0\n1\n0 0 0 1 1 1\n"
        ).encode()
        output = replace_species_for_nlpp(source)
        self.assertIn(b"Al_std.upf upf201", output)
        self.assertEqual(geometry_payload_bytes(source), geometry_payload_bytes(output))

    def test_cpu_list_parser(self) -> None:
        self.assertEqual(parse_cpu_list("0-2,5,40-43"), {0, 1, 2, 5, 40, 41, 42, 43})

    def test_eig_occ_ne_and_last_band_gates(self) -> None:
        content = """1 # ionic step
 spin=1 k-point=1/2 Cartesian=0 0 0 (1 plane wave)
 1 -1.0 1.5
 2 2.0 0.0
 spin=1 k-point=2/2 Cartesian=0 0 0 (1 plane wave)
 1 -1.0 1.5
 2 2.0 0.0
"""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "eig_occ.txt"
            path.write_text(content)
            result = parse_eig_occ(path, 2, 3.0, self.config())
        self.assertTrue(result["accepted"])
        self.assertEqual(result["row_count"], 4)
        self.assertEqual(result["last_band_maximum_occupation"], 0.0)

    def test_force_stress_trace_gate(self) -> None:
        text = """#TOTAL-FORCE (eV/Angstrom)#
       Al1 0.0 0.0 0.0
 #TOTAL-STRESS (kbar)#
 1.0 0.0 0.0
 0.0 2.0 0.0
 0.0 0.0 3.0
 #TOTAL-PRESSURE# (EXCLUDE KINETIC PART OF IONS): 2.0 kbar
"""
        parsed = parse_force_stress(text, 1, 2.0, self.config())
        self.assertTrue(parsed["accepted"])
        self.assertEqual(parsed["forces"][0]["atom"], "Al1")

    def test_galileo_uses_v100_anchors_and_strict_endpoint_limits(self) -> None:
        def result(value: float, pressure: float = 0.0) -> dict:
            return {"thermodynamic_labels_ev_per_atom": {"E_ec": value}, "pressure_gpa": pressure}

        parent = {
            "S1-20260810-301": result(-10.000),
            "S1-20260810-302": result(-9.900),
            "S1-20260810-303": result(-9.800),
            "S1-20260810-307": result(-9.950),
            "S1-20260810-308": result(-9.940),
        }
        new = {
            "S1-20260810-319": result(-9.980),
            "S1-20260810-320": result(-9.980),
            "S1-20260810-321": result(-9.980),
            "S1-20260810-322": result(-9.980),
            # anchored shapes equal the common shapes; raw total energies differ by 0.1/0.2 eV
            "S1-20260810-323": result(-9.850),
            "S1-20260810-324": result(-9.750, 0.01),
            "S1-20260810-325": result(-9.840),
            "S1-20260810-326": result(-9.740, 0.01),
        }
        local = {"S1-20260807-043": -20.000}
        local.update({experiment_id: -19.980 for experiment_id in ("S1-20260810-204", "S1-20260810-205", "S1-20260810-206", "S1-20260810-207")})
        gates, _ = evaluate_gates(new, parent, local, self.config())
        self.assertEqual(gates["status"], "accepted")
        # Exact endpoint k threshold is rejected because the contract is strict.
        new["S1-20260810-323"] = result(-9.848)
        gates, _ = evaluate_gates(new, parent, local, self.config())
        self.assertEqual(gates["endpoints"]["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
