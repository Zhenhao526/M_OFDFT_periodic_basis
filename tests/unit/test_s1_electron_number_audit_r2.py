from __future__ import annotations

import csv
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PROJECT_ROOT / "scripts"
RUNNER = SCRIPTS / "run_s1_electron_number_audit_r2.sh"
sys.path.insert(0, str(SCRIPTS))

import validate_s1_electron_number_audit_r2 as R2  # noqa: E402


class S1ElectronNumberAuditR2Test(unittest.TestCase):
    def test_incremental_scope_and_execution_order_are_exact(self) -> None:
        self.assertEqual(len(R2.R1_REUSED_IDS), 11)
        self.assertEqual(len(R2.R2_IDS), 19)
        self.assertEqual(R2.R2_PILOTS, ("S1-20260805-130", "S1-20260805-135"))
        self.assertEqual(R2.R2_EXECUTION_ORDER[:2], R2.R2_PILOTS)
        self.assertEqual(set(R2.R2_EXECUTION_ORDER), set(R2.R2_IDS))
        self.assertEqual(len(R2.R2_EXECUTION_ORDER), len(set(R2.R2_EXECUTION_ORDER)))

    def test_r2_manifest_rows_are_an_unmodified_r1_subset(self) -> None:
        rows = R2._selected_r1_rows(PROJECT_ROOT)
        self.assertEqual(tuple(row["audit_experiment_id"] for row in rows), R2.R2_IDS)
        with (PROJECT_ROOT / R2.R1_MANIFEST_PATH).open(
            encoding="utf-8", newline=""
        ) as handle:
            source_rows = list(csv.DictReader(handle, delimiter="\t"))
        source_by_audit = {
            row["audit_experiment_id"]: row
            for row in source_rows
            if row["audit_experiment_id"]
        }
        self.assertEqual(rows, [source_by_audit[value] for value in R2.R2_IDS])

    def test_kmp_mapping_pattern_is_narrow_and_anchored(self) -> None:
        self.assertEqual(
            R2.KMP_PATTERN,
            r"^/dev/shm/__KMP_REGISTERED_LIB_[1-9][0-9]*_0$",
        )
        import re

        self.assertIsNotNone(
            re.fullmatch(R2.KMP_PATTERN, "/dev/shm/__KMP_REGISTERED_LIB_21_0")
        )
        for value in (
            "/dev/shm/__KMP_REGISTERED_LIB_0_0",
            "/dev/shm/__KMP_REGISTERED_LIB_21_1000",
            "/dev/shm/__KMP_REGISTERED_LIB_21_0.extra",
            "/tmp/__KMP_REGISTERED_LIB_21_0",
        ):
            self.assertIsNone(re.fullmatch(R2.KMP_PATTERN, value))

    def test_archive_anchor_requires_adjacent_move(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            self._git(repository, "init")
            self._git(repository, "config", "user.name", "Unit Test")
            self._git(repository, "config", "user.email", "unit@example.invalid")
            (repository / "README").write_text("base\n", encoding="utf-8")
            self._git(repository, "add", ".")
            self._git(repository, "commit", "-m", "base")
            experiment_id = "S1-20260805-130"
            run = repository / "runs" / experiment_id
            run.mkdir(parents=True)
            (run / "failure.json").write_text("{}\n", encoding="utf-8")
            self._git(repository, "add", str(run))
            self._git(repository, "commit", "-m", "failed")
            failed = self._git_output(repository, "rev-parse", "HEAD")
            archive = (
                repository
                / "failed_runs/runtime_relocation"
                / experiment_id
                / f"attempt-{failed[:12]}"
            )
            archive.parent.mkdir(parents=True)
            self._git(repository, "mv", str(run), str(archive))
            self._git(repository, "commit", "-m", "archive")
            archive_commit = self._git_output(repository, "rev-parse", "HEAD")
            extra = archive.parent / "attempt-111111111111"
            extra.mkdir()
            (extra / "failure.json").write_text("{}\n", encoding="utf-8")
            self._git(repository, "add", str(extra))
            self._git(repository, "commit", "-m", "later R2 archive")
            frozen = R2.R1_FAILURE_COMMITS[experiment_id]
            R2.R1_FAILURE_COMMITS[experiment_id] = failed
            try:
                anchor = R2._archive_anchor(repository, experiment_id)
            finally:
                R2.R1_FAILURE_COMMITS[experiment_id] = frozen
            self.assertEqual(anchor["failed_attempt_commit"], failed)
            self.assertEqual(anchor["archive_commit"], archive_commit)

    def test_archived_nonpilot_attempt_before_pilots_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            self._git(repository, "init")
            self._git(repository, "config", "user.name", "Unit Test")
            self._git(repository, "config", "user.email", "unit@example.invalid")
            (repository / "README").write_text("base\n", encoding="utf-8")
            self._git(repository, "add", ".")
            self._git(repository, "commit", "-m", "base")
            marker = repository / "config/r2-preregistered"
            marker.parent.mkdir()
            marker.write_text("frozen\n", encoding="utf-8")
            self._git(repository, "add", str(marker))
            self._git(repository, "commit", "-m", "preregister")
            preregistration = self._git_output(repository, "rev-parse", "HEAD")

            experiment_id = "S1-20260805-131"
            run = repository / "runs" / experiment_id
            run.mkdir(parents=True)
            (run / "experiment_metadata.json").write_text(
                "{\"status\": \"failed\"}\n", encoding="utf-8"
            )
            self._git(repository, "add", str(run))
            self._git(repository, "commit", "-m", "early failed nonpilot")
            failed = self._git_output(repository, "rev-parse", "HEAD")
            archive = (
                repository
                / "failed_runs/runtime_relocation"
                / experiment_id
                / f"attempt-{failed[:12]}"
            )
            archive.parent.mkdir(parents=True)
            self._git(repository, "mv", str(run), str(archive))
            self._git(repository, "commit", "-m", "archive early nonpilot")

            errors: list[str] = []
            event_ids = R2._validate_execution_order(
                repository, preregistration, errors
            )
            self.assertIn(experiment_id, event_ids)
            self.assertTrue(
                any("exists before accepted S1-20260805-130" in error for error in errors),
                errors,
            )
            self.assertTrue(
                any("exists before accepted S1-20260805-135" in error for error in errors),
                errors,
            )

    def test_runner_freezes_dual_pilot_gate_and_final_total(self) -> None:
        if not RUNNER.exists():
            self.skipTest("R2 runner is assembled by the parallel implementation task")
        source = RUNNER.read_text(encoding="utf-8")
        self.assertTrue(os.access(RUNNER, os.X_OK))
        self.assertIn("execution_order", source)
        self.assertIn("--check-failure-archives", source)
        self.assertIn("processed -ne 19", source)
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
