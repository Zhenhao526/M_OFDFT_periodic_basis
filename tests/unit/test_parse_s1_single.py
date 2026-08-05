from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "parse_s1_single.py"
SPEC = importlib.util.spec_from_file_location("parse_s1_single", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ParseS1SingleTest(unittest.TestCase):
    def test_parse_converged_result(self) -> None:
        text = """
Autoset the number of electrons = 3
 #SCF IS CONVERGED#
 #TOTAL-PRESSURE# (EXCLUDE KINETIC PART OF IONS): 7.5 kbar
 !FINAL_ETOT_IS -57.25 eV
"""
        result = MODULE.parse_log(text, expected_electrons=3.0, atom_count=1)
        self.assertEqual(result["energy_ev_per_atom"], -57.25)
        self.assertEqual(result["pressure_gpa"], 0.75)
        self.assertEqual(result["electron_count_nominal_relative_error"], 0.0)

    def test_missing_convergence_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            MODULE.parse_log("!FINAL_ETOT_IS -1 eV", expected_electrons=3.0, atom_count=1)

    def test_nonconverged_result_is_preserved(self) -> None:
        text = """
Autoset the number of electrons = 3
 !!SCF IS NOT CONVERGED!!
 #TOTAL-PRESSURE# (EXCLUDE KINETIC PART OF IONS): 7.5 kbar
 !FINAL_ETOT_IS -57.25 eV
"""
        result = MODULE.parse_log(text, expected_electrons=3.0, atom_count=1)
        self.assertFalse(result["converged"])
        self.assertEqual(result["failure_reason"], "scf_not_converged")


if __name__ == "__main__":
    unittest.main()
