#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import time
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from launch_s1_g1_regeneration_10_r2 import (
    is_compute_rank_command,
    runner_command,
)
from run_s1_g1_regeneration_case_r2 import command_argv
from s1_g1_regeneration_10_common_r2 import (
    FROZEN_IMPLEMENTATION_PATHS,
    PROTOCOL_REVISION,
    read_config,
    read_manifest,
    sha256,
    validate_registration,
    write_exclusive,
)


CONFIG_PATH = ROOT / "config/S1_g1_regeneration_10_r2.json"
MANIFEST_PATH = ROOT / "config/S1_g1_regeneration_10_r2_manifest.tsv"


class RegenerationTenR2RegistrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = read_config(CONFIG_PATH)
        cls.rows = read_manifest(MANIFEST_PATH)

    def test_protocol_and_denominator(self) -> None:
        self.assertEqual(self.config["protocol_revision"], PROTOCOL_REVISION)
        self.assertEqual(len(self.rows), 10)
        self.assertEqual(
            [row["case_id"] for row in self.rows],
            [f"S1-G1-REGEN10-20260810-{index:03d}" for index in range(11, 21)],
        )
        self.assertEqual(
            [row["profile"] for row in self.rows],
            ["ofdft_scalar"] * 4 + ["ks_r4_field"] * 6,
        )
        self.assertEqual(len({row["source_run_id"] for row in self.rows}), 10)

    def test_r1_is_rejected_wholesale_and_rank_barrier_is_frozen(self) -> None:
        rejected = self.config["rejected_r1"]
        self.assertEqual(rejected["terminal_status"], "failed_no_retry")
        self.assertEqual(rejected["credited_cases"], 0)
        self.assertTrue(rejected["ids_must_not_be_retried"])
        self.assertEqual(
            rejected["ids"],
            [f"S1-G1-REGEN10-20260810-{index:03d}" for index in range(1, 11)],
        )
        self.assertEqual(
            self.config["rank_identity_barrier"]["mode"],
            "per_rank_o_excl_proof_go_ack_exec_v1",
        )

    def test_frozen_sources_and_runtime(self) -> None:
        _, _, registration = validate_registration(
            ROOT, CONFIG_PATH, MANIFEST_PATH,
            require_clean=False, require_state_absent=False,
        )
        self.assertEqual(len(registration["sources"]), 10)
        self.assertEqual(set(registration["runtime"]), {
            "python", "taskset", "time", "mpirun", "abacus", "rank_wrapper",
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
            str(ROOT / "scripts/launch_s1_g1_regeneration_10_r2.py"), "--all",
        ]
        self.assertEqual(self.config["command_contract"]["all_cases"], expected_all)
        commands = [runner_command(self.config, row) for row in self.rows]
        self.assertEqual(len(commands), 10)
        for row, command in zip(self.rows, commands):
            self.assertEqual(command[-2:], ["--case-id", row["case_id"]])
        solver = command_argv(self.config, Path("/tmp/fixed-case"), self.rows[0])
        self.assertEqual(solver[:3], ["/usr/bin/taskset", "--cpu-list", "20,21,22,23"])
        self.assertIn(self.config["runtime"]["tools"]["rank_wrapper"]["path"], solver)
        self.assertEqual(solver[solver.index("--case-id") + 1], self.rows[0]["case_id"])
        self.assertEqual(solver[solver.index("--cpu-list", 3) + 1], "20,21,22,23")
        self.assertEqual(solver[solver.index("--abacus") + 1],
                         self.config["runtime"]["tools"]["abacus"]["path"])

    def test_cpu_conflict_filter_uses_actual_rank_executable(self) -> None:
        self.assertTrue(is_compute_rank_command([
            "/path/abacus_pw_para", "--irrelevant",
        ]))
        self.assertTrue(is_compute_rank_command([
            "/path/s1_rank_wrapper.sh", "20",
        ]))
        self.assertTrue(is_compute_rank_command([
            "/usr/bin/python3", "-s", "/path/s1_g1_regeneration_rank_wrapper_r2.py",
        ]))
        self.assertFalse(is_compute_rank_command([
            "/path/mpirun", "-np", "4", "/usr/bin/python3", "-s",
            "/path/s1_g1_regeneration_rank_wrapper_r2.py",
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
        self.assertEqual(len(FROZEN_IMPLEMENTATION_PATHS), 10)

    def test_config_runtime_hashes(self) -> None:
        for registration in self.config["runtime"]["tools"].values():
            self.assertEqual(
                sha256(Path(registration["realpath"])), registration["sha256"]
            )
    def _rank_wrapper_command(
        self, proof_dir: Path, rank: int, case_id: str = "R2-BARRIER-TEST"
    ) -> tuple[list[str], dict[str, str]]:
        wrapper = ROOT / "scripts/s1_g1_regeneration_rank_wrapper_r2.py"
        command = [
            "/usr/bin/python3", "-s", str(wrapper),
            "--case-id", case_id,
            "--proof-dir", str(proof_dir),
            "--cpu-list", "20,21,22,23",
            "--abacus", "/usr/bin/true",
            "--runner-commit", "a" * 40,
        ]
        environment = dict(os.environ)
        environment.update({
            "OMPI_COMM_WORLD_RANK": str(rank),
            "OMPI_COMM_WORLD_SIZE": "4",
            "OMPI_COMM_WORLD_LOCAL_RANK": str(rank),
            "OMPI_COMM_WORLD_LOCAL_SIZE": "4",
        })
        return command, environment

    @staticmethod
    def _wait_for(paths: list[Path], timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if all(path.is_file() for path in paths):
                return
            time.sleep(0.01)
        raise AssertionError(f"timed out waiting for {paths}")

    def test_rank_wrapper_positive_proof_ack_exec_barrier(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            proof_dir = Path(temporary)
            processes = []
            for rank in range(4):
                command, environment = self._rank_wrapper_command(proof_dir, rank)
                processes.append(subprocess.Popen(
                    command, env=environment, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                ))
            proof_paths = [
                proof_dir / f"rank-{rank:03d}.proof.json" for rank in range(4)
            ]
            self._wait_for(proof_paths)
            proof_hashes = {str(rank): sha256(path) for rank, path in enumerate(proof_paths)}
            write_exclusive(proof_dir / "GO.json", {
                "protocol_revision": PROTOCOL_REVISION,
                "case_id": "R2-BARRIER-TEST",
                "proof_sha256": proof_hashes,
            })
            go_sha = sha256(proof_dir / "GO.json")
            ack_paths = [
                proof_dir / f"rank-{rank:03d}.ack.json" for rank in range(4)
            ]
            self._wait_for(ack_paths)
            ack_hashes = {str(rank): sha256(path) for rank, path in enumerate(ack_paths)}
            write_exclusive(proof_dir / "EXEC.json", {
                "protocol_revision": PROTOCOL_REVISION,
                "case_id": "R2-BARRIER-TEST",
                "ack_sha256": ack_hashes,
            })
            for rank, process in enumerate(processes):
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, (rank, stdout, stderr))
                proof = json.loads(proof_paths[rank].read_text(encoding="utf-8"))
                ack = json.loads(ack_paths[rank].read_text(encoding="utf-8"))
                self.assertEqual(proof["affinity"], [20 + rank])
                self.assertEqual(ack["affinity"], [20 + rank])
                self.assertEqual(ack["go_sha256"], go_sha)

    def test_rank_wrapper_negative_duplicate_proof_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            proof_dir = Path(temporary)
            occupied = proof_dir / "rank-000.proof.json"
            occupied.write_text('{"sentinel":"do-not-overwrite"}\n', encoding="utf-8")
            original = occupied.read_bytes()
            command, environment = self._rank_wrapper_command(proof_dir, 0)
            process = subprocess.run(
                command, env=environment, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5,
            )
            self.assertNotEqual(process.returncode, 0)
            self.assertEqual(occupied.read_bytes(), original)



if __name__ == "__main__":
    unittest.main()
