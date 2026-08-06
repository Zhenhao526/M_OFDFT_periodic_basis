from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import generate_s1_g1_thermodynamic_label_audit_r2 as REGISTRATION  # noqa: E402
import launch_s1_g1_thermodynamic_label_audit_r2 as LAUNCHER  # noqa: E402


RUNNER = SCRIPTS / "run_s1_g1_thermodynamic_label_audit_r2.sh"


class S1G1ThermodynamicLabelAuditR2RunnerTest(unittest.TestCase):
    def test_shell_syntax_and_executable_mode(self) -> None:
        subprocess.check_call(["bash", "-n", str(RUNNER)])
        self.assertTrue(RUNNER.stat().st_mode & 0o111)

    def test_attempt_is_committed_before_solver_and_no_retry_is_fail_closed(self) -> None:
        text = RUNNER.read_text(encoding="utf-8")
        loop = text.index("for ((offset = 0;")
        marker = text.index('create_attempt_marker "$experiment_id" "$logical_id"', loop)
        solver = text.index("scripts/run_s1_single.sh", marker)
        self.assertLess(marker, solver)
        self.assertIn("os.O_CREAT | os.O_EXCL", text)
        self.assertIn('git commit -m "start G1 thermodynamic-label audit R2 $experiment_id"', text)
        self.assertIn("attempt marker exists without accepted run; solver restart forbidden", text)
        self.assertNotIn("run_s1_g1_thermodynamic_label_audit_r1.sh", text)

    def test_barriers_and_state_namespace_are_frozen(self) -> None:
        text = RUNNER.read_text(encoding="utf-8")
        self.assertIn("--require-pilot-gate", text)
        self.assertIn("--require-k-gate", text)
        self.assertIn("--require-half-quarter-pair", text)
        self.assertIn("--require-adjacent-eos", text)
        self.assertEqual(
            Path(REGISTRATION.SUPERVISOR_STATE_DIRECTORY),
            LAUNCHER.DEFAULT_STATE_DIRECTORY,
        )
        self.assertEqual(len(REGISTRATION.R2_AUDIT_IDS), 30)
        self.assertEqual(len(REGISTRATION.LOGICAL_TO_EFFECTIVE_ID), 40)

    def test_every_python_invocation_is_absolute_and_disables_user_site(self) -> None:
        text = RUNNER.read_text(encoding="utf-8")
        self.assertIn("bootstrap_python=/usr/bin/python3", text)
        self.assertIn('"$bootstrap_python" -s - "$config"', text)
        invocations = [
            line.strip()
            for line in text.splitlines()
            if '"$python_tool"' in line
        ]
        self.assertTrue(invocations)
        self.assertTrue(
            all('"$python_tool" -s ' in line for line in invocations),
            invocations,
        )

    def test_runner_is_live_child_bound_and_all_release_failures_are_recorded(self) -> None:
        text = RUNNER.read_text(encoding="utf-8")
        for binding in (
            "M_OFDFT_G1_R2_SUPERVISOR_STATE_DIRECTORY",
            "M_OFDFT_G1_R2_SUPERVISOR_PID",
            "M_OFDFT_G1_R2_SUPERVISOR_START_TIME_TICKS",
            "M_OFDFT_G1_R2_BOOT_ID",
            "M_OFDFT_G1_R2_LAUNCH_SHA256",
            "M_OFDFT_G1_R2_GO_SHA256",
        ):
            self.assertIn(binding, text)
        self.assertIn('bash_parent = int(sys.argv[2])', text)
        self.assertIn('bash_parent == bound_pid', text)
        self.assertIn("record_gate_failure final-analysis", text)
        self.assertIn("run_gate final-analysis-status", text)
        self.assertIn("--check-analysis-summary", text)

        failure_block = text[
            text.index("record_gate_failure()") : text.index("run_gate()")
        ]
        for key in REGISTRATION.BARRIER_FAILURE_REQUIRED_KEYS:
            self.assertIn(f'"{key}"', failure_block)
        self.assertIn('"status": "barrier_failed"', failure_block)
        self.assertIn(
            '"retry_policy": "stop_after_exact_scope_commit_no_continue_or_retry"',
            failure_block,
        )
        self.assertNotIn('"status": "rejected"', failure_block)


if __name__ == "__main__":
    unittest.main()
