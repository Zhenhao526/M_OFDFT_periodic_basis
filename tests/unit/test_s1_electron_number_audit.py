from __future__ import annotations

import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PROJECT_ROOT / "scripts"
RUNNER = SCRIPTS / "run_s1_electron_number_audit.sh"
sys.path.insert(0, str(SCRIPTS))

import s1_electron_number_common as COMMON  # noqa: E402
import validate_s1_electron_number_audit as VALIDATOR  # noqa: E402


class S1ElectronNumberAuditTest(unittest.TestCase):
    def _write_structure(self, directory: Path) -> Path:
        structure = directory / "STRU"
        structure.write_text(
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
            "13.0000000000 3.0000000000 zatom zion pspd\n",
            encoding="utf-8",
        )
        return structure

    def _write_cube(
        self,
        directory: Path,
        density_token: str = "3.00000000000000000e+00",
        *,
        dimensions: tuple[int, int, int] = (1, 1, 1),
    ) -> Path:
        path = directory / "chg.cube"
        rows = [
            "Ionic_Step 1 Cubefile created from ABACUS. Inner loop is z, followed by y and x",
            "1 # number of spin directions",
            "1 0.0 0.0 0.0",
            f"{dimensions[0]} 1.0 0.0 0.0",
            f"{dimensions[1]} 0.0 1.0 0.0",
            f"{dimensions[2]} 0.0 0.0 1.0",
            "13 3.0 0.0 0.0 0.0",
        ]
        rows.extend(density_token for _ in range(math.prod(dimensions)))
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        return path

    def _write_restart(
        self,
        path: Path,
        *,
        miller: tuple[tuple[int, int, int], ...] = ((0, 0, 0), (1, 0, 0)),
        g0_real: float = 3.0,
        g0_imaginary: float = 0.0,
        spin_count: int = 1,
        trailing: bytes = b"",
    ) -> None:
        plane_wave_count = len(miller)
        data = bytearray()
        data.extend(struct.pack("<5i", 3, 1, plane_wave_count, spin_count, 3))
        data.extend(struct.pack("<i", 9))
        data.extend(struct.pack("<9d", *([0.0] * 9)))
        data.extend(struct.pack("<i", 9))
        data.extend(struct.pack("<i", 3 * plane_wave_count))
        data.extend(struct.pack(f"<{3 * plane_wave_count}i", *(v for row in miller for v in row)))
        data.extend(struct.pack("<i", 3 * plane_wave_count))
        for _ in range(spin_count):
            values = [g0_real, g0_imaginary]
            values.extend([0.125, 0.0] for _ in range(plane_wave_count - 1))
            flattened: list[float] = []
            for value in values:
                if isinstance(value, list):
                    flattened.extend(value)
                else:
                    flattened.append(value)
            data.extend(struct.pack("<i", plane_wave_count))
            data.extend(struct.pack(f"<{2 * plane_wave_count}d", *flattened))
            data.extend(struct.pack("<i", plane_wave_count))
        path.write_bytes(bytes(data) + trailing)

    def test_expected_electrons_comes_from_structure_and_local_zion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self._write_structure(directory)
            expected, provenance = COMMON.expected_electrons(directory)
            self.assertEqual(expected, 3.0)
            self.assertEqual(
                provenance["expected_electrons_exact_fraction"],
                {"numerator": 3, "denominator": 1},
            )
            self.assertEqual(provenance["components"]["Al"]["atom_count"], 1)

    def test_output_input_changes_only_suffix_and_high_precision_density_control(self) -> None:
        source = (
            b"INPUT_PARAMETERS\n"
            b"pseudo_dir /frozen/pseudo\n"
            b"suffix\told_name\n"
            b"esolver_type ofdft\n"
            b"ecutwfc 60\n"
        )
        derived = COMMON.derive_output_input(source, "g1_ne_119_from_113")
        self.assertEqual(
            derived,
            b"INPUT_PARAMETERS\n"
            b"pseudo_dir /frozen/pseudo\n"
            b"suffix g1_ne_119_from_113\n"
            b"out_chg 1 17\n"
            b"esolver_type ofdft\n"
            b"ecutwfc 60\n",
        )
        with self.assertRaisesRegex(ValueError, "already contains out_chg"):
            COMMON.derive_output_input(source + b"out_chg\t1 16\n", "other")

    def test_reciprocal_restart_exact_integration_and_fail_closed_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            structure = self._write_structure(directory)
            restart = directory / "SPIN1_CHARGE-DENSITY.restart"
            self._write_restart(restart)
            payload = COMMON.integrate_reciprocal_restart(restart, structure, 3.0)
            self.assertTrue(payload["accepted"])
            self.assertEqual(payload["spin_count"], 1)
            self.assertEqual(payload["numerical_absolute_error_bound"], 0.0)

            self._write_restart(restart, trailing=b"unexpected")
            with self.assertRaisesRegex(ValueError, "trailing bytes"):
                COMMON.integrate_reciprocal_restart(restart, structure, 3.0)
            self._write_restart(restart, miller=((0, 0, 0), (0, 0, 0)))
            with self.assertRaisesRegex(ValueError, "exactly one G=0"):
                COMMON.integrate_reciprocal_restart(restart, structure, 3.0)
            self._write_restart(restart, spin_count=2)
            with self.assertRaisesRegex(ValueError, "header values"):
                COMMON.integrate_reciprocal_restart(restart, structure, 3.0)
            self._write_restart(restart, g0_real=float("nan"))
            with self.assertRaisesRegex(ValueError, "non-finite G=0"):
                COMMON.integrate_reciprocal_restart(restart, structure, 3.0)

    def test_reciprocal_strict_relative_boundary_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            structure = self._write_structure(directory)
            restart = directory / "density.restart"
            self._write_restart(restart, g0_real=3.0 * (1.0 + 1.0e-10))
            payload = COMMON.integrate_reciprocal_restart(restart, structure, 3.0)
            self.assertFalse(payload["accepted"])

    def test_cube_uses_exact_decimal_certification_and_strict_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            structure = self._write_structure(directory)
            cube = self._write_cube(directory)
            payload = COMMON.integrate_cube(cube, structure, 3.0, (1, 1, 1))
            self.assertTrue(payload["accepted"])
            self.assertEqual(payload["floating_absolute_error_bound"], 0.0)
            self.assertEqual(payload["grid_dimension_authority"], "raw_running_scf_log")

            cube = self._write_cube(directory, "3.00000000030000000e+00")
            payload = COMMON.integrate_cube(cube, structure, 3.0, (1, 1, 1))
            self.assertFalse(payload["accepted"])

    def test_cube_rejects_grid_mismatch_truncation_nonfinite_and_signed_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            structure = self._write_structure(directory)
            cube = self._write_cube(directory)
            with self.assertRaisesRegex(ValueError, "grid dimensions differ"):
                COMMON.integrate_cube(cube, structure, 3.0, (2, 1, 1))

            lines = cube.read_text(encoding="utf-8").splitlines()
            cube.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "density count differs"):
                COMMON.integrate_cube(cube, structure, 3.0, (1, 1, 1))

            cube = self._write_cube(directory, "nan")
            with self.assertRaisesRegex(ValueError, "malformed density token"):
                COMMON.integrate_cube(cube, structure, 3.0, (1, 1, 1))

            cube = self._write_cube(directory, dimensions=(-1, 1, 1))
            with self.assertRaisesRegex(ValueError, "unexpected spin, atom, or grid count"):
                COMMON.integrate_cube(cube, structure, 3.0, (-1, 1, 1))

    def test_raw_charge_grid_requires_one_exact_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "running_scf.log"
            log.write_text(
                "header\nFFT grid for charge/potential = [ 20, 24, 28 ]\n",
                encoding="utf-8",
            )
            self.assertEqual(COMMON.parse_charge_grid(log), (20, 24, 28))
            log.write_text(
                "FFT grid for charge/potential = [ 20, 24, 28 ]\n"
                "FFT grid for charge/potential = [ 20, 24, 28 ]\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "exactly one"):
                COMMON.parse_charge_grid(log)

    def test_scientific_equivalence_thresholds_are_strict(self) -> None:
        source = {"converged": True, "energy_ev_per_atom": 0.0, "pressure_gpa": 0.0}
        energy_boundary = {
            "converged": True,
            "energy_ev_per_atom": 0.0001,
            "pressure_gpa": 0.0,
        }
        pressure_boundary = {
            "converged": True,
            "energy_ev_per_atom": 0.0,
            "pressure_gpa": 0.02,
        }
        self.assertFalse(COMMON.scientific_equivalence(source, energy_boundary)["accepted"])
        self.assertFalse(COMMON.scientific_equivalence(source, pressure_boundary)["accepted"])

    def test_frozen_mapping_is_independent_of_source_manifest_order(self) -> None:
        solver_by_source = {
            experiment_id: json.loads(
                (PROJECT_ROOT / "runs" / experiment_id / "input_metadata.json").read_text()
            )["solver"]
            for experiment_id in COMMON.TARGET_IDS
        }
        observed = COMMON.ordered_of_replay_sources(solver_by_source)
        expected = (
            "S1-20260805-113",
            "S1-20260805-116",
            *(f"S1-20260805-{value:03d}" for value in range(29, 36)),
            *(f"S1-20260805-{value:03d}" for value in range(50, 57)),
            *(f"S1-20260805-{value:03d}" for value in range(71, 78)),
            *(f"S1-20260805-{value:03d}" for value in range(92, 99)),
        )
        self.assertEqual(observed, expected)
        self.assertEqual(tuple(zip(COMMON.AUDIT_IDS, observed))[:2], (
            ("S1-20260805-119", "S1-20260805-113"),
            ("S1-20260805-120", "S1-20260805-116"),
        ))

    def test_latest_run_introduction_supports_archived_same_id_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            self._git(repository, "init")
            self._git(repository, "config", "user.name", "Unit Test")
            self._git(repository, "config", "user.email", "unit@example.invalid")
            (repository / "README").write_text("base\n")
            self._git(repository, "add", ".")
            self._git(repository, "commit", "-m", "base")

            run = repository / "runs/S1-20260805-119"
            run.mkdir(parents=True)
            (run / "experiment_metadata.json").write_text("{\"attempt\": 1}\n")
            self._git(repository, "add", str(run))
            self._git(repository, "commit", "-m", "failed attempt")
            failed_commit = self._git_output(repository, "rev-parse", "HEAD")
            archive = repository / f"failed_runs/runtime_relocation/S1-20260805-119/attempt-{failed_commit[:12]}"
            archive.parent.mkdir(parents=True)
            self._git(repository, "mv", str(run), str(archive))
            self._git(repository, "commit", "-m", "archive failed attempt")

            run.mkdir(parents=True)
            metadata = run / "experiment_metadata.json"
            metadata.write_text("{\"attempt\": 2}\n")
            (run / "electron_number_audit.json").write_text("{}\n")
            self._git(repository, "add", str(run))
            self._git(repository, "commit", "-m", "accepted retry")
            latest = VALIDATOR._latest_introduction_commit(repository, metadata)
            self.assertEqual(latest, self._git_output(repository, "rev-parse", "HEAD"))
            self.assertEqual(
                VALIDATOR._complete_run_tree_failures(repository, run, latest), []
            )

            metadata.write_text("{\"attempt\": 3}\n")
            self._git(repository, "add", str(metadata))
            self._git(repository, "commit", "-m", "mutate accepted retry")
            self.assertTrue(
                VALIDATOR._complete_run_tree_failures(repository, run, latest)
            )

    def test_preregistered_blob_cannot_be_changed_by_a_later_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            self._git(repository, "init")
            self._git(repository, "config", "user.name", "Unit Test")
            self._git(repository, "config", "user.email", "unit@example.invalid")
            frozen = repository / "config/frozen.json"
            frozen.parent.mkdir(parents=True)
            frozen.write_text("{\"value\": 1}\n")
            self._git(repository, "add", str(frozen))
            self._git(repository, "commit", "-m", "preregister")
            introduction = VALIDATOR._introduction_commit(repository, frozen)
            self.assertIsNone(
                VALIDATOR._frozen_blob_failure(repository, frozen, introduction)
            )
            frozen.write_text("{\"value\": 2}\n")
            self._git(repository, "add", str(frozen))
            self._git(repository, "commit", "-m", "mutate frozen config")
            self.assertIn(
                "differs from preregistration blob",
                VALIDATOR._frozen_blob_failure(repository, frozen, introduction) or "",
            )

    def test_git_mode_change_is_visible_even_when_blob_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            self._git(repository, "init")
            self._git(repository, "config", "user.name", "Unit Test")
            self._git(repository, "config", "user.email", "unit@example.invalid")
            runner = repository / "runner.sh"
            runner.write_text("#!/usr/bin/env bash\nexit 0\n")
            runner.chmod(0o755)
            self._git(repository, "add", str(runner))
            self._git(repository, "commit", "-m", "executable runner")
            introduction = self._git_output(repository, "rev-parse", "HEAD")
            original = VALIDATOR._tree_entries(repository, introduction, "runner.sh")
            runner.chmod(0o644)
            self._git(repository, "add", str(runner))
            self._git(repository, "commit", "-m", "remove executable bit")
            current = VALIDATOR._tree_entries(repository, "HEAD", "runner.sh")
            self.assertEqual(original["runner.sh"][2], current["runner.sh"][2])
            self.assertEqual(original["runner.sh"][0], "100755")
            self.assertEqual(current["runner.sh"][0], "100644")

    def test_runner_has_pilot_order_failure_archive_and_final_total_gate(self) -> None:
        source = RUNNER.read_text(encoding="utf-8")
        self.assertTrue(os.access(RUNNER, os.X_OK))
        self.assertIn('sorted(rows, key=lambda item: item["audit_experiment_id"])', source)
        self.assertIn('archive_failed_attempt "$audit_id"', source)
        self.assertIn("--check-failure-run", source)
        self.assertIn("Failed attempt is not HEAD", source)
        self.assertIn("--check-failure-archives", source)
        self.assertIn("processed -ne 30", source)
        self.assertIn("--require-committed --require-all-runs", source)
        self.assertIn("9<&- </dev/null", source)

    @staticmethod
    def _git(repository: Path, *arguments: str) -> None:
        subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            text=True,
            capture_output=True,
        )

    @staticmethod
    def _git_output(repository: Path, *arguments: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(repository), *arguments], text=True
        ).strip()


if __name__ == "__main__":
    unittest.main()
