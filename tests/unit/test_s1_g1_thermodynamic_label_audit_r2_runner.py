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

    def test_legacy_and_non_exact_argument_counts_fail_before_preflight(self) -> None:
        for arguments in ((), ("manifest", "config"), ("1", "2", "3", "4"),
                          ("1", "2", "3", "4", "5", "6")):
            with self.subTest(arguments=arguments):
                completed = subprocess.run(
                    ["bash", str(RUNNER), *arguments],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                self.assertEqual(completed.returncode, 2)
                self.assertIn("/proc/self/fd/200", completed.stderr)
                self.assertNotIn("Traceback", completed.stderr)

    def test_attempt_is_committed_before_solver_and_no_retry_is_fail_closed(self) -> None:
        text = RUNNER.read_text(encoding="utf-8")
        loop = text.index("for ((offset = 0;")
        marker = text.index('create_attempt_marker "$experiment_id" "$logical_id"', loop)
        marker_validator = text.index('--check-attempt-marker "$experiment_id"')
        solver = text.index("scripts/run_s1_single.sh", marker)
        self.assertLess(marker, solver)
        self.assertLess(marker_validator, solver)
        self.assertIn("os.O_CREAT | os.O_EXCL", text)
        self.assertIn('git commit -m "start G1 thermodynamic-label audit R2 $experiment_id"', text)
        self.assertIn("attempt marker exists without accepted run; solver restart forbidden", text)
        self.assertIn('"supervisor_go_path": str(go_path)', text)
        self.assertIn('"supervisor_go_sha256": fixed_go_sha256', text)
        self.assertIn('"go_git_head": fixed_go_git_head', text)
        self.assertIn("attempt marker fixed GO bytes/head differ", text)
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
        self.assertIn('preflight_output=$("$bootstrap_python" -s -', text)
        self.assertIn('sys.executable != "/usr/bin/python3"', text)
        self.assertLess(
            text.index('preflight_output=$("$bootstrap_python" -s -'),
            text.index('"$python_tool"'),
        )
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

    def test_preflight_binds_exact_seven_argv_and_sealed_inputs_before_science(self) -> None:
        text = RUNNER.read_text(encoding="utf-8")
        self.assertIn("if [[ $# -ne 5 ]]", text)
        for registration in (
            "runner_fd_path=$0",
            "project_root=$1",
            "canonical_manifest=$2",
            "canonical_config=$3",
            "frozen_manifest=$4",
            "frozen_config=$5",
        ):
            self.assertIn(registration, text)
        self.assertIn('FIXED_FDS = {"runner": 200, "manifest": 201, "config": 202}', text)
        self.assertIn("fcntl.F_GET_SEALS", text)
        self.assertIn("os.pread", text)
        self.assertIn("expected_runner_argv = [", text)
        self.assertIn("len(expected_runner_argv) != 7", text)
        self.assertIn("actual_runner_argv != expected_runner_argv", text)
        self.assertIn('launch.get("runner_argv") != expected_runner_argv', text)
        self.assertIn('Path(f"/proc/{runner_bash_pid}/cmdline")', text)
        self.assertIn('runner_observed["ppid"] == bound_pid', text)
        self.assertIn("canonical {name} SHA-256 differs from sealed execution input", text)
        self.assertIn('launch.get("sealed_execution_inputs") != sealed_record', text)
        self.assertIn(
            'go.get("sealed_execution_inputs_sha256") != sealed_record_sha256', text
        )
        self.assertLess(text.index("preflight_output=$("), text.index('cd "$project_root"'))
        self.assertLess(text.index("preflight_output=$("), text.index("mapfile -d '' run_plan"))

    def test_scientific_inputs_are_frozen_and_git_validation_is_canonical(self) -> None:
        text = RUNNER.read_text(encoding="utf-8")
        self.assertIn('"$python_tool" -s - "$frozen_config" "$frozen_manifest"', text)
        self.assertIn('"$python_tool" -s - "$frozen_config" "$run_directory"', text)
        self.assertIn('--scientific-config "$frozen_config"', text)
        self.assertIn('--scientific-manifest "$frozen_manifest"', text)
        self.assertIn('--config "$canonical_config"', text)
        self.assertIn('--manifest "$canonical_manifest"', text)
        logical_commands = text.replace("\\\n", " ")
        validator_lines = [
            line for line in logical_commands.splitlines() if '"$validator"' in line
        ]
        self.assertTrue(validator_lines)
        self.assertTrue(
            all(
                '"$canonical_manifest" --config "$canonical_config"' in line
                for line in validator_lines
            ),
            validator_lines,
        )
        self.assertTrue(
            all(
                '--scientific-config "$frozen_config"' in line
                and '--scientific-manifest "$frozen_manifest"' in line
                for line in validator_lines
            ),
            validator_lines,
        )
        self.assertNotIn('"$validator" "$frozen_manifest"', text)

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
        self.assertIn("supervisor_parent = int(supervisor_parent_value)", text)
        self.assertIn("supervisor_parent == bound_pid", text)
        self.assertIn('set(go) != go_keys', text)
        self.assertIn('type(go.get("schema_version")) is not int', text)
        self.assertIn('go_hash == bound_go_hash', text)
        self.assertIn('go_accepted[0].get("go_sha256") != bound_go_hash', text)
        self.assertIn('getattr(os, "O_NOFOLLOW", None)', text)
        self.assertIn("introduction_commits != [git_head]", text)
        self.assertIn("runner GO Git HEAD is not the unique detachment introduction commit", text)
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
