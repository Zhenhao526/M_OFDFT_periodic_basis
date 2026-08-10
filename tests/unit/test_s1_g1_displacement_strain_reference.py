from __future__ import annotations

import importlib.util
import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


generator = load("g1_displacement_generator", "scripts/generate_s1_g1_displacement_strain_reference.py")
runner = load("g1_displacement_runner", "scripts/run_s1_g1_displacement_strain_reference.py")
analyzer = load("g1_displacement_analyzer", "scripts/analyze_s1_g1_displacement_strain_reference.py")


class DisplacementStrainReferenceTests(unittest.TestCase):
    def test_frozen_case_matrix(self):
        cases = generator.case_definitions()
        self.assertEqual(len(cases), 15)
        self.assertEqual([row["id"] for row in cases], list(generator.RUN_IDS))
        self.assertEqual(len({row["pair"] for row in cases if row["pair"]}), 7)

    def test_volume_preserving_deformations(self):
        for kind in ("al_tetragonal", "mg_axial"):
            for sign in (-1.0, 1.0):
                matrix = generator.deformation(kind, sign * 0.005)
                self.assertAlmostEqual(generator.determinant(matrix), 1.0, places=14)

    def test_shear_convention(self):
        al = generator.deformation("al_shear_xy", 0.005)
        mg = generator.deformation("mg_shear_xz", -0.005)
        self.assertEqual(al[0][1], 0.005)
        self.assertEqual(mg[0][2], -0.005)
        self.assertEqual(generator.determinant(al), 1.0)
        self.assertEqual(generator.determinant(mg), 1.0)

    def test_cartesian_fractional_round_trip(self):
        lattice = [[0.0, 4.05, 4.05], [2.025, 0.0, 2.025], [2.025, 2.025, 0.0]]
        cart = [0.01, 0.0, 0.0]
        fractional = generator.row_times_matrix(cart, generator.inverse(lattice))
        recovered = generator.row_times_matrix(fractional, lattice)
        for expected, actual in zip(cart, recovered):
            self.assertAlmostEqual(expected, actual, places=15)

    def test_force_and_stress_parser(self):
        text = """
 #TOTAL-FORCE (eV/Angstrom)#
 -------------------------------------------------------------------------
     Atoms              Force_x              Force_y              Force_z
 -------------------------------------------------------------------------
       Mg1         0.1000000000         0.0000000000        -0.2000000000
       Mg2        -0.1000000000         0.0000000000         0.2000000000
 -------------------------------------------------------------------------
 #TOTAL-STRESS (kbar)#
 ----------------------------------------------------------------
          1.0000000000         0.1000000000         0.0000000000
          0.1000000000         2.0000000000         0.0000000000
          0.0000000000         0.0000000000         3.0000000000
 ----------------------------------------------------------------
"""
        forces, stress = analyzer.parse_forces_stress(text, 2)
        self.assertEqual(len(forces), 2)
        self.assertEqual(stress[2][2], 3.0)

    def test_exclusive_json_is_single_use(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "marker.json"
            runner.write_exclusive_json(path, {"status": "started"})
            self.assertEqual(json.loads(path.read_text())["status"], "started")
            with self.assertRaises(FileExistsError):
                runner.write_exclusive_json(path, {"status": "retry"})


if __name__ == "__main__":
    unittest.main()
