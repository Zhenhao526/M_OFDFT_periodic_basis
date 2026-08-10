#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from launch_s1_g1_regeneration_10_r1 import (
    is_compute_rank_command,
    runner_command,
)
from run_s1_g1_regeneration_case_r1 import command_argv
from s1_g1_regeneration_10_common import (
    FROZEN_IMPLEMENTATION_PATHS,
    PROTOCOL_REVISION,
    read_config,
    read_manifest,
    sha256,
    validate_registration,
    write_exclusive,
)


CONFIG_PATH = ROOT / "config/S1_g1_regeneration_10_r1.json"
MANIFEST_PATH = ROOT / "config/S1_g1_regeneration_10_r1_manifest.tsv"


class RegenerationTenRegistrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = read_config(CONFIG_PATH)
        cls.rows = read_manifest(MANIFEST_PATH)

    def test_protocol_and_denominator(self) -> None:
        self.assertEqual(self.config["protocol_revision"], PROTOCOL_REVISION)
        self.assertEqual(len(self.rows), 10)
        self.assertEqual(
            [row["case_id"] for row in self.rows],
            [f"S1-G1-REGEN10-20260810-{index:03d}" for index in range(1, 11)],
        )
        self.assertEqual(
            [row["profile"] for row in self.rows],
            ["ofdft_scalar"] * 4 + ["ks_r4_field"] * 6,
        )
        self.assertEqual(len({row["source_run_id"] for row in self.rows}), 10)

    def test_frozen_sources_and_runtime(self) -> None:
        _, _, registration = validate_registration(
            ROOT, CONFIG_PATH, MANIFEST_PATH,
            require_clean=False, require_state_absent=False,
        )
        self.assertEqual(len(registration["sources"]), 10)
        self.assertEqual(set(registration["runtime"]), {
            "python", "taskset", "time", "mpirun", "abacus",
        })

    def test_profile_specific_fields(self) -> None:
        for row in self.rows[:4]:
            self.assertEqual(row["density_relpath"], "")
            self.assertEqual(row["potential_relpath"], "")
            self.assertEqual(row["grid"], "")
            self.assertEqual(int(row["timeout_seconds"]), 600)
        for row in self.rows[4:7]:
            self.assertTrue(row["density_relpath"].endswith("/chg.cube"))
            self.assertTrue(row["potential_relpath"].endswith("/pot.cube"))
            self.assertEqual(int(row["timeout_seconds"]), 1800)
        for row in self.rows[7:]:
            self.assertEqual(int(row["timeout_seconds"]), 3600)

    def test_exact_commands(self) -> None:
        expected_all = [
            "/usr/bin/python3", "-s",
            str(ROOT / "scripts/launch_s1_g1_regeneration_10_r1.py"), "--all",
        ]
        self.assertEqual(self.config["command_contract"]["all_cases"], expected_all)
        commands = [runner_command(self.config, row) for row in self.rows]
        self.assertEqual(len(commands), 10)
        for row, command in zip(self.rows, commands):
            self.assertEqual(command[-2:], ["--case-id", row["case_id"]])
        solver = command_argv(self.config, Path("/tmp/fixed-case"))
        self.assertEqual(solver[:3], ["/usr/bin/taskset", "--cpu-list", "20,21,22,23"])
        self.assertEqual(solver[-4:-1], ["/usr/bin/taskset", "--cpu-list", "20,21,22,23"])
        self.assertEqual(solver[-1], self.config["runtime"]["tools"]["abacus"]["path"])

    def test_cpu_conflict_filter_uses_actual_rank_executable(self) -> None:
        self.assertTrue(is_compute_rank_command([
            "/path/abacus_pw_para", "--irrelevant",
        ]))
        self.assertTrue(is_compute_rank_command([
            "/path/s1_rank_wrapper.sh", "20",
        ]))
        self.assertFalse(is_compute_rank_command([
            "/path/mpirun", "-np", "4", "/path/abacus_pw_para",
        ]))
        self.assertFalse(is_compute_rank_command([]))

    def test_o_excl_no_retry_primitive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "attempt.json"
            write_exclusive(path, {"case_id": "fixed", "status": "started"})
            with self.assertRaises(FileExistsError):
                write_exclusive(path, {"case_id": "fixed", "status": "retry"})
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["status"], "started"
            )

    def test_no_shared_progress_document_in_frozen_scope(self) -> None:
        self.assertNotIn("docs/M_OFDFT_项目进度与交接.md", FROZEN_IMPLEMENTATION_PATHS)
        self.assertEqual(len(FROZEN_IMPLEMENTATION_PATHS), 9)

    def test_config_runtime_hashes(self) -> None:
        for registration in self.config["runtime"]["tools"].values():
            self.assertEqual(
                sha256(Path(registration["realpath"])), registration["sha256"]
            )


if __name__ == "__main__":
    unittest.main()
