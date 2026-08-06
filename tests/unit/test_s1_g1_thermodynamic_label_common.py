from __future__ import annotations

import hashlib
import math
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import generate_s1_g1_thermodynamic_label_audit_r1 as GENERATOR  # noqa: E402
import s1_g1_thermodynamic_label_common as COMMON  # noqa: E402


class S1G1ThermodynamicLabelCommonTest(unittest.TestCase):
    def _write_structure(self, directory: Path) -> Path:
        path = directory / "STRU"
        path.write_text(
            "ATOMIC_SPECIES\n"
            "Al 26.9815 al.gga.psp\n\n"
            "LATTICE_CONSTANT\n"
            "1.0\n\n"
            "LATTICE_VECTORS\n"
            "1 0 0\n"
            "0 1 0\n"
            "0 0 1\n\n"
            "ATOMIC_POSITIONS\n"
            "Direct\n"
            "Al\n"
            "0.0\n"
            "1\n"
            "0 0 0\n",
            encoding="utf-8",
        )
        (directory / "al.gga.psp").write_text(
            "13.0000000000 3.0000000000 zatom zion pspd\n"
            "0 0 0 0 0 nproj\n",
            encoding="utf-8",
        )
        return path

    def _write_cube(
        self,
        path: Path,
        values: tuple[float, float],
    ) -> Path:
        path.write_text(
            "Ionic_Step 1 Cubefile created from ABACUS. Inner loop is z, followed by y and x\n"
            "1 # number of spin directions\n"
            "1 0.0 0.0 0.0\n"
            "2 0.5 0.0 0.0\n"
            "1 0.0 1.0 0.0\n"
            "1 0.0 0.0 1.0\n"
            "13 3.0 0.0 0.0 0.0\n"
            f"{values[0]:.17e} {values[1]:.17e}\n",
            encoding="utf-8",
        )
        return path

    def test_frozen_run_matrix_ids_axes_and_gate_order(self) -> None:
        rows = GENERATOR.build_plan()
        self.assertEqual(len(rows), 40)
        self.assertEqual(tuple(row["experiment_id"] for row in rows), COMMON.AUDIT_IDS)
        self.assertEqual(
            tuple(
                row["experiment_id"]
                for row in sorted(rows, key=lambda row: row["execution_index"])[:12]
            ),
            COMMON.K_GATE_EXECUTION_IDS,
        )
        by_id = {row["experiment_id"]: row for row in rows}
        self.assertEqual(by_id["S1-20260806-010"]["volume_ratio"], "1.00")
        self.assertEqual(by_id["S1-20260806-017"]["volume_ratio"], "1.00")
        self.assertEqual(by_id["S1-20260806-024"]["kmesh"], (28, 28, 28))
        self.assertEqual(by_id["S1-20260806-031"]["kmesh"], (24, 24, 16))
        self.assertEqual(by_id["S1-20260806-036"]["kmesh"], (32, 32, 32))
        self.assertEqual(by_id["S1-20260806-039"]["kmesh"], (28, 28, 18))
        self.assertEqual(by_id["S1-20260806-021"]["smearing_sigma_ry"], "0.001837465")
        self.assertEqual(by_id["S1-20260806-035"]["common_quarter_partner_id"], "S1-20260806-021")
        self.assertEqual(len({row["suffix"] for row in rows}), 40)

    def test_input_derivation_changes_only_registered_fields(self) -> None:
        source = (
            b"INPUT_PARAMETERS\n"
            b"suffix old\n"
            b"calculation scf\n"
            b"pseudo_dir .\n"
            b"ecutwfc 40\n"
            b"smearing_sigma 0.00734986\n"
        )
        derived = COMMON.derive_label_input(
            source,
            suffix="g1tlr1_al_v100_quarter",
            smearing_sigma_ry="0.001837465",
        )
        parsed = COMMON.parse_input_text(derived)
        self.assertEqual(parsed["pseudo_dir"], (".",))
        self.assertEqual(parsed["ecutwfc"], ("40",))
        self.assertEqual(parsed["out_chg"], ("1", "17"))
        self.assertEqual(parsed["out_pot"], ("1", "17"))
        with self.assertRaisesRegex(ValueError, "already contains out_pot"):
            COMMON.derive_label_input(
                source + b"out_pot 1 17\n",
                suffix="other",
                smearing_sigma_ry="0.001837465",
            )

    def test_kpt_derivation_is_gamma_zero_shift_and_fail_closed(self) -> None:
        source = b"K_POINTS\n0\nGamma\n28 28 28 0 0 0\n"
        derived = COMMON.derive_kpt(source, (32, 32, 32))
        self.assertEqual(COMMON.parse_kpt_text(derived), (32, 32, 32))
        with self.assertRaisesRegex(ValueError, "shifts must be zero"):
            COMMON.parse_kpt_text(b"K_POINTS\n0\nGamma\n32 32 32 1 0 0\n")

    def test_real_r8_logs_parse_entropy_key_and_per_atom_identities(self) -> None:
        cases = (
            ("S1-20260805-085", 1),
            ("S1-20260805-106", 2),
        )
        for experiment_id, atom_count in cases:
            with self.subTest(experiment_id=experiment_id):
                paths = list((PROJECT_ROOT / "runs" / experiment_id).glob("OUT.*/running_scf.log"))
                self.assertEqual(len(paths), 1)
                payload = COMMON.parse_thermodynamic_log(
                    paths[0].read_text(encoding="utf-8"),
                    expected_atom_count=atom_count,
                )
                self.assertLessEqual(payload["entropy_minus_ts_ev"], 0)
                self.assertFalse(payload["zero_temperature_exact_claim"])
                self.assertEqual(payload["atom_count"], atom_count)
                for identity in payload["identities"].values():
                    self.assertTrue(identity["accepted"])
                    self.assertLess(
                        abs(float(identity["residual_ev_per_atom"])),
                        float(COMMON.IDENTITY_RESIDUAL_EV_PER_ATOM_LIMIT),
                    )
                self.assertIn("final_equals_kohn_sham", payload["identities"])

    def test_thermodynamic_parser_rejects_missing_entropy_and_bad_final_energy(self) -> None:
        path = next((PROJECT_ROOT / "runs" / "S1-20260805-085").glob("OUT.*/running_scf.log"))
        text = path.read_text(encoding="utf-8")
        with self.assertRaisesRegex(ValueError, r"missing final E_entropy\(-TS\)"):
            COMMON.parse_thermodynamic_log(
                text.replace("E_entropy(-TS)", "E_entropy_BAD", 1),
                expected_atom_count=1,
            )
        with self.assertRaisesRegex(ValueError, "thermodynamic identity failed"):
            COMMON.parse_thermodynamic_log(
                text.replace(
                    "!FINAL_ETOT_IS -57.1542036815019188 eV",
                    "!FINAL_ETOT_IS -57.1542026815019188 eV",
                ),
                expected_atom_count=1,
            )

    def test_density_metrics_use_registered_normalizations_and_strict_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            structure = self._write_structure(directory)
            reference = COMMON.parse_abacus_cube(
                self._write_cube(directory / "rho_ref.cube", (2.0, 4.0)),
                quantity="thermal_density",
                units="electron/bohr^3",
                structure_path=structure,
                expected_grid=(2, 1, 1),
            )
            passing = COMMON.parse_abacus_cube(
                self._write_cube(directory / "rho_pass.cube", (2.01, 3.99)),
                quantity="thermal_density",
                units="electron/bohr^3",
                structure_path=structure,
                expected_grid=(2, 1, 1),
            )
            result = COMMON.compare_density_fields(reference, passing)
            self.assertTrue(result["accepted"])
            self.assertAlmostEqual(result["d1"], 1.0 / 300.0)
            self.assertIsNone(result["low_density_mask"])

            boundary = COMMON.parse_abacus_cube(
                self._write_cube(directory / "rho_boundary.cube", (2.011, 4.022)),
                quantity="thermal_density",
                units="electron/bohr^3",
                structure_path=structure,
                expected_grid=(2, 1, 1),
            )
            boundary_result = COMMON.compare_density_fields(reference, boundary)
            self.assertGreater(boundary_result["d1"], COMMON.DENSITY_D1_LIMIT)
            self.assertGreater(boundary_result["d2"], COMMON.DENSITY_D2_LIMIT)
            self.assertFalse(boundary_result["accepted"])
            # Set one gate to the exact already-computed binary64 metric.  A
            # <= implementation would pass; the registered strict < must fail.
            old_d1, old_d2 = COMMON.DENSITY_D1_LIMIT, COMMON.DENSITY_D2_LIMIT
            try:
                COMMON.DENSITY_D1_LIMIT = boundary_result["d1"]
                COMMON.DENSITY_D2_LIMIT = math.inf
                equality_result = COMMON.compare_density_fields(reference, boundary)
                self.assertEqual(equality_result["d1"], COMMON.DENSITY_D1_LIMIT)
                self.assertFalse(equality_result["accepted"])
            finally:
                COMMON.DENSITY_D1_LIMIT, COMMON.DENSITY_D2_LIMIT = old_d1, old_d2

    def test_projected_potential_derivative_removes_only_constant_gauge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            structure = self._write_structure(directory)
            reference = COMMON.parse_abacus_cube(
                self._write_cube(directory / "pot_ref.cube", (0.0, 2.0)),
                quantity="local_effective_ks_potential",
                units="Ry",
                structure_path=structure,
                expected_grid=(2, 1, 1),
            )
            constant_shift = COMMON.parse_abacus_cube(
                self._write_cube(directory / "pot_shift.cube", (5.0, 7.0)),
                quantity="local_effective_ks_potential",
                units="Ry",
                structure_path=structure,
                expected_grid=(2, 1, 1),
            )
            result = COMMON.compare_potential_derivative_fields(reference, constant_shift)
            self.assertTrue(result["accepted"])
            self.assertAlmostEqual(result["dg"], 0.0)
            self.assertAlmostEqual(result["absolute_rms_ev"], 0.0)
            self.assertIsNone(result["low_density_mask"])

    def test_cube_parser_rejects_wrong_value_count_and_pseudo_requires_nproj_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            structure = self._write_structure(directory)
            pseudo = COMMON.validate_local_pseudopotential(directory / "al.gga.psp")
            self.assertTrue(pseudo["local_only"])
            bad = self._write_cube(directory / "bad.cube", (1.0, 2.0))
            bad.write_text(bad.read_text(encoding="utf-8").rsplit(" ", 1)[0] + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "value count differs"):
                COMMON.parse_abacus_cube(
                    bad,
                    quantity="thermal_density",
                    units="electron/bohr^3",
                    structure_path=structure,
                    expected_grid=(2, 1, 1),
                )
            (directory / "nonlocal.psp").write_text("0 0 1 nproj\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "nonlocal projectors"):
                COMMON.validate_local_pseudopotential(directory / "nonlocal.psp")

    def test_source_semantic_validator_accepts_explicit_fixture_spec(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "source.cpp"
            source.write_text("marker one\nmarker two\n", encoding="utf-8")
            archive = directory / "source.tar.gz"
            archive.write_bytes(b"fixture archive")
            sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
            payload = GENERATOR.validate_source_semantics(
                directory,
                archive,
                specs=(
                    {
                        "relative_path": "source.cpp",
                        "sha256": sha(source),
                        "markers": ("marker one", "marker two"),
                    },
                ),
                archive_sha256=sha(archive),
            )
            self.assertTrue(payload["files"][0]["validated"])


if __name__ == "__main__":
    unittest.main()
