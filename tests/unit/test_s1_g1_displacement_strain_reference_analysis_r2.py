from __future__ import annotations

import importlib.util
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


generator = load("g1_disp_generator_for_r2", "scripts/generate_s1_g1_displacement_strain_reference.py")
analysis = load("g1_disp_analysis_r2", "scripts/analyze_s1_g1_displacement_strain_reference_r2.py")


class AnalysisR2Tests(unittest.TestCase):
    def test_pbc_minimum_image_accepts_legal_cube_wrap(self):
        lattice = [
            [0.0, 7.653390808104204, 7.653390808104204],
            [3.826695404052102, 0.0, 3.826695404052102],
            [3.826695404052102, 3.826695404052102, 0.0],
        ]
        expected = [-0.018897261254578284, 3.826695404052102, 3.826695404052102]
        wrapped = [expected[j] + lattice[1][j] + lattice[2][j] for j in range(3)]
        dimensions = (20, 20, 20)
        steps = tuple(
            tuple(Decimal(str(lattice[i][j] / dimensions[i])) for j in range(3))
            for i in range(3)
        )
        cube = SimpleNamespace(
            dimensions=dimensions,
            axis_steps_bohr=steps,
            atom_rows=((Decimal(13), Decimal(3), *(Decimal(str(value)) for value in wrapped)),),
        )
        metadata = {
            "expected_lattice_vectors_bohr": lattice,
            "expected_cartesian_positions_bohr": [expected],
        }
        audit = analysis.cube_geometry_audit(cube, metadata)
        self.assertGreater(audit["maximum_raw_atom_absolute_error_bohr"], 7.0)
        self.assertLess(audit["maximum_minimum_image_atom_absolute_error_bohr"], 1.0e-12)
        self.assertEqual(audit["pbc_wrapped_atom_rows"], 1)

    def test_lattice_axis_error_is_not_periodically_reduced(self):
        lattice = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        cube = SimpleNamespace(
            dimensions=(1, 1, 1),
            axis_steps_bohr=(
                (Decimal("2"), Decimal("0"), Decimal("0")),
                (Decimal("0"), Decimal("1"), Decimal("0")),
                (Decimal("0"), Decimal("0"), Decimal("1")),
            ),
            atom_rows=((Decimal(13), Decimal(3), Decimal(0), Decimal(0), Decimal(0)),),
        )
        audit = analysis.cube_geometry_audit(
            cube,
            {"expected_lattice_vectors_bohr": lattice, "expected_cartesian_positions_bohr": [[0, 0, 0]]},
        )
        self.assertEqual(audit["maximum_lattice_absolute_error_bohr"], 1.0)
        self.assertEqual(audit["maximum_accepted_geometry_error_bohr"], 1.0)

    def test_physical_angstrom_conversion_and_negative_fractional_regression(self):
        lattice_dimensionless = [[0.0, 4.05, 4.05], [2.025, 0.0, 2.025], [2.025, 2.025, 0.0]]
        lattice_angstrom = [
            [value * 1.8897261254578281 * analysis.BOHR_TO_ANGSTROM for value in row]
            for row in lattice_dimensionless
        ]
        intended = [0.01, 0.0, 0.0]
        fractional = generator.row_times_matrix(intended, generator.inverse(lattice_dimensionless))
        measured = analysis.row_times_matrix(fractional, lattice_angstrom)
        self.assertLess(analysis.max_vector_error(measured, intended), 1.0e-9)
        wrong = analysis.row_times_matrix([0.01, 0.0, 0.0], lattice_angstrom)
        self.assertGreater(analysis.max_vector_error(wrong, intended), 0.03)

    def test_deformation_reconstruction_matches_row_lattice_convention(self):
        parent = [[0.0, 2.025, 2.025], [2.025, 0.0, 2.025], [2.025, 2.025, 0.0]]
        for kind in ("al_tetragonal", "al_shear_xy"):
            expected = generator.deformation(kind, 0.005)
            candidate = generator.apply_deformation(parent, expected)
            actual = analysis.deformation_from_lattices(parent, candidate)
            for i in range(3):
                for j in range(3):
                    self.assertAlmostEqual(actual[i][j], expected[i][j], places=14)


if __name__ == "__main__":
    unittest.main()
