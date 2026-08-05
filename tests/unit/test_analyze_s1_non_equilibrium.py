from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from analyze_s1_non_equilibrium import (  # noqa: E402
    _archive_failures,
    _checksum_failures,
    _energy,
    _finite_float,
    _fit_quality,
    _normalized_run_input,
    _result_reparse_failures,
    _series_status,
    _tracked_head_failures,
    compare_series,
)


RATIOS = (0.90, 0.94, 0.97, 1.00, 1.03, 1.06, 1.10)


def points(energy_changes=None, pressure_changes=None):
    energy_changes = energy_changes or {}
    pressure_changes = pressure_changes or {}
    payload = []
    for index, ratio in enumerate(RATIOS):
        payload.append(
            {
                "volume_ratio": ratio,
                "experiment_id": f"X-{index}",
                "energy_ev_per_atom": (ratio - 1.0) ** 2 + energy_changes.get(ratio, 0.0),
                "pressure_gpa": (1.0 - ratio) * 10.0 + pressure_changes.get(ratio, 0.0),
            }
        )
    return payload


class AnalyzeS1NonEquilibriumTest(unittest.TestCase):
    def test_curve_anchor_ignores_constant_energy_shift(self) -> None:
        baseline = points()
        refined = points({ratio: 7.5 for ratio in RATIOS})
        result = compare_series(baseline, refined, 1.0, 1.0, 0.02)
        self.assertEqual(result["status"], "accepted")
        self.assertAlmostEqual(
            result["max_relative_energy_difference_mev_per_atom"], 0.0, places=9
        )

    def test_energy_threshold_is_strict(self) -> None:
        baseline = points()
        refined = points({0.90: 0.001})
        result = compare_series(baseline, refined, 1.0, 1.0, 0.02)
        self.assertEqual(result["status"], "rejected")
        self.assertFalse(result["relative_energy_passed"])
        self.assertAlmostEqual(
            result["max_relative_energy_difference_mev_per_atom"], 1.0, places=9
        )

    def test_pressure_threshold_is_strict_for_cutoff(self) -> None:
        baseline = points()
        refined = points(pressure_changes={0.90: 0.02})
        result = compare_series(baseline, refined, 1.0, 1.0, 0.02)
        self.assertEqual(result["status"], "rejected")
        self.assertFalse(result["pressure_passed"])

    def test_kmesh_pressure_is_diagnostic_only(self) -> None:
        baseline = points()
        refined = points(pressure_changes={0.90: 4.0})
        result = compare_series(baseline, refined, 1.0, 2.0, None)
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["pressure_acceptance_role"], "diagnostic_only")

    def test_mismatched_ratios_are_indeterminate(self) -> None:
        result = compare_series(points(), points()[:-1], 1.0, 1.0, 0.02)
        self.assertEqual(result["status"], "indeterminate")

    def test_nonfinite_registered_energy_and_pressure_are_rejected(self) -> None:
        self.assertIsNone(
            _energy(
                {"solver": "ofdft"},
                {"converged": True, "energy_ev_per_atom": float("nan")},
            )
        )
        self.assertIsNone(
            _energy(
                {"solver": "ksdft"},
                {
                    "converged": True,
                    "zero_temp_extrapolated_energy_ev_per_atom": float("inf"),
                },
            )
        )
        self.assertIsNone(_finite_float(float("nan")))
        self.assertIsNone(_finite_float("not-a-number"))

    def test_absolute_checksum_paths_are_verified_by_basename(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            names = ("INPUT", "STRU", "KPT", "al.gga.psp")
            for name in names:
                (run / name).write_bytes(f"payload:{name}\n".encode())
            lines = []
            for name in names:
                digest = hashlib.sha256((run / name).read_bytes()).hexdigest()
                lines.append(
                    f"{digest}  /home/shenwei01/old/repository/runs/S1-X/{name}"
                )
            (run / "INPUT_SHA256SUMS").write_text("\n".join(lines) + "\n")
            self.assertEqual(_checksum_failures(run, "al.gga.psp"), [])

    def test_checksum_rejects_empty_duplicate_extra_and_wrong_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            for name in ("INPUT", "STRU", "KPT", "al.gga.psp"):
                (run / name).write_text(name)
            (run / "INPUT_SHA256SUMS").write_text("")
            self.assertTrue(
                any(
                    value.startswith("missing_checksum_entries")
                    for value in _checksum_failures(run, "al.gga.psp")
                )
            )
            (run / "INPUT_SHA256SUMS").write_text(
                ("0" * 64) + "  /remote/INPUT\n"
                + ("1" * 64) + "  /other/INPUT\n"
                + ("2" * 64) + "  /remote/EXTRA\n"
            )
            failures = _checksum_failures(run, "al.gga.psp")
            self.assertTrue(any(value.startswith("duplicate_checksum_basename") for value in failures))
            self.assertTrue(any(value.startswith("missing_checksum_entries") for value in failures))
            self.assertTrue(any(value.startswith("unexpected_checksum_entries") for value in failures))
            self.assertIn("checksum_mismatch:INPUT", failures)

    def test_only_pseudo_dir_is_normalized_in_archived_input(self) -> None:
        source = b"INPUT_PARAMETERS\necutwfc 30\npseudo_dir ../../assets/pseudo\nscf_nmax 200\n"
        expected = b"INPUT_PARAMETERS\necutwfc 30\npseudo_dir .\nscf_nmax 200\n"
        self.assertEqual(_normalized_run_input(source), expected)
        self.assertNotEqual(
            _normalized_run_input(source), expected.replace(b"ecutwfc 30", b"ecutwfc 40")
        )
        with self.assertRaisesRegex(ValueError, "exactly one"):
            _normalized_run_input(source + b"pseudo_dir ../second\n")

    def test_missing_archived_inputs_are_provenance_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            run = project_root / "runs/S1-X"
            run.mkdir(parents=True)
            failures = _archive_failures(
                project_root,
                {"input_directory": "inputs/frozen"},
                run,
                {"pseudopotential": "al.gga.psp"},
                {},
            )
            for name in ("INPUT", "STRU", "KPT", "al.gga.psp"):
                self.assertIn(f"missing_run_artifact:{name}", failures)

    def test_complete_raw_or_bm3_failure_is_rejected(self) -> None:
        baseline = points()
        refined = points({0.94: 0.010})
        raw = compare_series(baseline, refined, 1.0, 1.0, 0.02)
        fit_points = [
            {
                **point,
                "volume_per_atom_angstrom3": 16.0 * point["volume_ratio"],
            }
            for point in refined
        ]
        _, fit_failures = _fit_quality(fit_points)
        self.assertEqual(raw["status"], "rejected")
        self.assertTrue(fit_failures)
        self.assertEqual(_series_status([], fit_failures, raw), "rejected")
        self.assertEqual(_series_status([], [], raw), "rejected")
        self.assertEqual(
            _series_status([], ["fit_residual_threshold_failed"], {"status": "accepted"}),
            "rejected",
        )
        self.assertEqual(
            _series_status(["archive_provenance_conflict"], [], raw), "indeterminate"
        )

    def test_raw_log_is_reparsed_and_full_result_dict_is_checked(self) -> None:
        project_root = Path(__file__).resolve().parents[2]
        run = project_root / "runs" / "S1-20260805-036"
        metadata = json.loads((run / "input_metadata.json").read_text())
        result = json.loads((run / "result.json").read_text())
        log = next(run.glob("OUT.*/running_scf.log"))
        self.assertEqual(_result_reparse_failures(log, metadata, result), [])
        tampered = dict(result)
        tampered["pressure_gpa"] += 0.1
        self.assertEqual(
            _result_reparse_failures(log, metadata, tampered),
            ["result_json_does_not_match_running_log"],
        )

    def test_run_artifacts_must_be_tracked_and_equal_head(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            artifact = repository / "runs/S1-X/result.json"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("{}\n")
            self._git(repository, "init")
            self._git(repository, "config", "user.name", "Unit Test")
            self._git(repository, "config", "user.email", "unit@example.invalid")
            self._git(repository, "add", ".")
            self._git(repository, "commit", "-m", "fixture")
            self.assertEqual(_tracked_head_failures(repository, [artifact]), [])
            artifact.write_text('{"changed": true}\n')
            self.assertIn(
                "run_artifact_differs_from_head",
                _tracked_head_failures(repository, [artifact]),
            )
            untracked = repository / "runs/S1-X/untracked"
            untracked.write_text("x")
            self.assertTrue(
                any(
                    failure.startswith("untracked_run_artifact")
                    for failure in _tracked_head_failures(repository, [untracked])
                )
            )
            symlink = repository / "runs/S1-X/result-link.json"
            symlink.symlink_to(artifact.name)
            self.assertTrue(
                any(
                    failure.startswith("symbolic_link_run_artifact")
                    for failure in _tracked_head_failures(repository, [symlink])
                )
            )

    @staticmethod
    def _git(repository: Path, *arguments: str) -> None:
        subprocess.run(
            ["git", *arguments],
            cwd=repository,
            check=True,
            text=True,
            capture_output=True,
        )


if __name__ == "__main__":
    unittest.main()
