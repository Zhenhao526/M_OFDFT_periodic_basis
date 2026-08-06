from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = PROJECT_ROOT / "scripts/launch_s1_g1_thermodynamic_label_audit_r2.py"
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import launch_s1_g1_thermodynamic_label_audit_r2 as LAUNCHER_MODULE  # noqa: E402


class S1G1ThermodynamicLabelAuditR2LauncherTest(unittest.TestCase):
    def test_exclusive_evidence_publish_is_atomic_and_never_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "evidence.json"
            LAUNCHER_MODULE._write_exclusive(output, {"sequence": 1})
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"sequence": 1})
            with self.assertRaises(FileExistsError):
                LAUNCHER_MODULE._write_exclusive(output, {"sequence": 2})
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"sequence": 1})
            self.assertEqual(list(output.parent.glob(f".{output.name}.tmp-*")), [])

    def _run(self, *arguments: object, cwd: Path) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [
                sys.executable,
                "-s",
                str(LAUNCHER),
                *(str(value) for value in arguments),
            ],
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(LAUNCHER_MODULE.FROZEN_AMBIENT_ENVIRONMENT_VALUES),
        )
        if result.returncode:
            self.fail(f"launcher failed ({result.returncode}): {result.stderr}")
        return result

    def _fixture_repository(self, temporary: Path) -> Path:
        project = temporary / "project"
        (project / "config").mkdir(parents=True)
        (project / "scripts").mkdir()
        manifest_text = "experiment_id\n"
        manifest_sha256 = hashlib.sha256(manifest_text.encode()).hexdigest()
        python_path = Path(sys.executable)
        python_realpath = python_path.resolve()
        bash_value = shutil.which("bash")
        if bash_value is None:
            raise unittest.SkipTest("bash is unavailable")
        bash_path = Path(bash_value)
        bash_realpath = bash_path.resolve()
        config = {
            "protocol_revision": "S1-G1-THERMODYNAMIC-LABEL-AUDIT-R2",
            "status": "preregistered",
            "runtime": {
                "tools": {
                    "python": {
                        "path": str(python_path),
                        "realpath": str(python_realpath),
                        "sha256": hashlib.sha256(
                            python_realpath.read_bytes()
                        ).hexdigest(),
                    },
                    "bash": {
                        "path": str(bash_path),
                        "realpath": str(bash_realpath),
                        "sha256": hashlib.sha256(
                            bash_realpath.read_bytes()
                        ).hexdigest(),
                    },
                }
            },
            "execution": {
                "supervisor_state_directory": str(temporary / "single-use-state"),
                "ambient_environment": {
                    "keys_exact": list(
                        LAUNCHER_MODULE.FROZEN_AMBIENT_ENVIRONMENT_KEYS
                    ),
                    "values_exact": dict(
                        LAUNCHER_MODULE.FROZEN_AMBIENT_ENVIRONMENT_VALUES
                    ),
                    "canonical_values_sha256": hashlib.sha256(
                        LAUNCHER_MODULE._canonical_values(
                            LAUNCHER_MODULE.FROZEN_AMBIENT_ENVIRONMENT_VALUES
                        )
                    ).hexdigest(),
                    "mutating_launcher_exact_match_required": True,
                    "python_no_user_site_required": True,
                    "validator_subprocess_explicit_environment_required": True,
                    "supervisor_subprocess_explicit_environment_required": True,
                    "runner_additional_binding_keys_exact": list(
                        LAUNCHER_MODULE.RUNNER_BINDING_ENVIRONMENT_KEYS
                    ),
                    "runner_registered_bash_required": True,
                },
                "detachment_attestation_path": "orchestration/r2/detachment.json",
                "supervisor_completion_path": (
                    "orchestration/s1/g1_thermodynamic_label_audit_r2_20260806/"
                    "supervisor_completion.json"
                ),
            },
            "manifest": {
                "path": "config/S1_g1_thermodynamic_label_audit_r2_manifest.tsv",
                "sha256": manifest_sha256,
            },
        }
        (project / "config/S1_g1_thermodynamic_label_audit_r2.json").write_text(
            json.dumps(config) + "\n", encoding="utf-8"
        )
        (project / "config/S1_g1_thermodynamic_label_audit_r2_manifest.tsv").write_text(
            manifest_text, encoding="utf-8"
        )
        validator = project / "scripts/validate_s1_g1_thermodynamic_label_audit_r2.py"
        validator.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            f"expected = {dict(LAUNCHER_MODULE.FROZEN_AMBIENT_ENVIRONMENT_VALUES)!r}\n"
            "if dict(os.environ) != expected or not sys.flags.no_user_site:\n"
            "    raise SystemExit(91)\n"
            "raise SystemExit(0)\n",
            encoding="utf-8",
        )
        runner = project / "scripts/run_s1_g1_thermodynamic_label_audit_r2.sh"
        runner.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "mkdir -p analysis/s1/g1_thermodynamic_label_audit_r2_20260806\n"
            "printf '%s\\n' '{\"protocol_revision\":\"S1-G1-THERMODYNAMIC-LABEL-AUDIT-R2\","
            "\"audit_status\":\"accepted\",\"overall_protocol_status\":"
            "\"pending_supervisor_completion\",\"g1_status\":\"pending (1/6)\","
            "\"authorized_scope\":\"no_G1_advancement\"}' "
            "> analysis/s1/g1_thermodynamic_label_audit_r2_20260806/summary.json\n"
            "git add -- analysis/s1/g1_thermodynamic_label_audit_r2_20260806\n"
            "git commit -qm 'fixture accepted analysis'\n",
            encoding="utf-8",
        )
        validator.chmod(0o755)
        subprocess.check_call(["git", "init", "-q"], cwd=project)
        subprocess.check_call(["git", "config", "user.name", "R2 test"], cwd=project)
        subprocess.check_call(["git", "config", "user.email", "r2@example.invalid"], cwd=project)
        subprocess.check_call(["git", "add", "."], cwd=project)
        subprocess.check_call(["git", "commit", "-qm", "fixture"], cwd=project)
        return project

    def test_registered_absolute_bash_builds_runner_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            project = self._fixture_repository(temporary)
            config = json.loads(
                (
                    project / "config/S1_g1_thermodynamic_label_audit_r2.json"
                ).read_text(encoding="utf-8")
            )
            bash_tool = LAUNCHER_MODULE._registered_tool(config, "bash")
            command = LAUNCHER_MODULE._runner_command(
                bash_tool,
                project,
                Path("scripts/run_s1_g1_thermodynamic_label_audit_r2.sh"),
                Path("config/S1_g1_thermodynamic_label_audit_r2_manifest.tsv"),
                Path("config/S1_g1_thermodynamic_label_audit_r2.json"),
            )
            self.assertTrue(Path(command[0]).is_absolute())
            self.assertEqual(command[0], config["runtime"]["tools"]["bash"]["path"])
            self.assertEqual(
                command[1],
                str(project / "scripts/run_s1_g1_thermodynamic_label_audit_r2.sh"),
            )

    def test_runner_environment_is_only_frozen_plus_six_bindings(self) -> None:
        frozen = dict(LAUNCHER_MODULE.FROZEN_AMBIENT_ENVIRONMENT_VALUES)
        observed = LAUNCHER_MODULE._runner_environment(
            frozen,
            Path("/external/state"),
            123,
            456,
            "boot-id",
            "a" * 64,
            "b" * 64,
        )
        self.assertEqual(
            set(observed),
            set(LAUNCHER_MODULE.FROZEN_AMBIENT_ENVIRONMENT_KEYS)
            | set(LAUNCHER_MODULE.RUNNER_BINDING_ENVIRONMENT_KEYS),
        )
        self.assertEqual(len(observed), 16)
        self.assertNotIn("BASH_ENV", observed)

    def test_deleted_supervisor_log_descriptor_is_rejected(self) -> None:
        state = Path("/external/state")
        registered = {"start_time_ticks": 99}
        observed = {
            "pid": 123,
            "process_group_id": 123,
            "session_id": 123,
            "tty_nr": 0,
            "start_time_ticks": 99,
            "stdin": "/dev/null",
            "stdout": f"{state}/supervisor.log (deleted)",
            "stderr": f"{state}/supervisor.log",
        }
        with self.assertRaisesRegex(ValueError, "exact persistent log path"):
            LAUNCHER_MODULE._require_detached_process_record(
                observed, registered, 123, state
            )

    def test_mutating_launcher_rejects_extra_and_bash_env_variables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            project = self._fixture_repository(temporary)
            bash_env = temporary / "bash-env.sh"
            bash_env.write_text("exit 0\n", encoding="utf-8")
            base = dict(LAUNCHER_MODULE.FROZEN_AMBIENT_ENVIRONMENT_VALUES)
            for name, value in (
                ("UNREGISTERED_EXTRA", "forbidden"),
                ("BASH_ENV", str(bash_env)),
            ):
                environment = {**base, name: value}
                result = subprocess.run(
                    [
                        sys.executable,
                        "-s",
                        str(LAUNCHER),
                        "start",
                        "--project-root",
                        str(project),
                        "--state-directory",
                        str(temporary / "single-use-state"),
                    ],
                    cwd=project,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=environment,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ambient environment differs", result.stderr)
                self.assertFalse((temporary / "single-use-state").exists())

    @unittest.skipUnless(Path("/proc/self/stat").is_file(), "Linux /proc integration test")
    def test_malformed_go_is_preserved_as_go_rejected_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            project = self._fixture_repository(temporary)
            state = temporary / "single-use-state"
            self._run(
                "start",
                "--project-root",
                project,
                "--state-directory",
                state,
                cwd=project,
            )
            launch = json.loads((state / "launch.json").read_text(encoding="utf-8"))
            pid = int(launch["process"]["pid"])
            try:
                (state / "go.json").write_text("{malformed\n", encoding="utf-8")
                deadline = time.monotonic() + 10.0
                while time.monotonic() < deadline and not (state / "terminal.json").is_file():
                    time.sleep(0.05)
                terminal = json.loads((state / "terminal.json").read_text(encoding="utf-8"))
                self.assertEqual(terminal["status"], "go_rejected")
                journal = [
                    json.loads(line)
                    for line in (state / "journal.jsonl").read_text(encoding="utf-8").splitlines()
                ]
                self.assertEqual(
                    [row["event"] for row in journal][-1], "go_rejected"
                )
            finally:
                try:
                    os.kill(pid, 15)
                except ProcessLookupError:
                    pass

    @unittest.skipUnless(Path("/proc/self/stat").is_file(), "Linux /proc integration test")
    def test_detached_launch_hup_attestation_and_go(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            project = self._fixture_repository(temporary)
            state = temporary / "single-use-state"
            attestation = Path("orchestration/r2/detachment.json")
            self._run(
                "start",
                "--project-root",
                project,
                "--state-directory",
                state,
                cwd=project,
            )
            launch = json.loads((state / "launch.json").read_text(encoding="utf-8"))
            config = json.loads(
                (
                    project / "config/S1_g1_thermodynamic_label_audit_r2.json"
                ).read_text(encoding="utf-8")
            )
            ambient = config["execution"]["ambient_environment"]
            self.assertEqual(
                launch["environment"],
                {
                    "keys_exact": ambient["keys_exact"],
                    "values_exact": ambient["values_exact"],
                    "canonical_values_sha256": ambient[
                        "canonical_values_sha256"
                    ],
                },
            )
            self.assertEqual(
                launch["runner_argv"][0], config["runtime"]["tools"]["bash"]["path"]
            )
            self.assertTrue(Path(launch["runner_argv"][0]).is_absolute())
            self.assertFalse(
                os.access(
                    project / "scripts/run_s1_g1_thermodynamic_label_audit_r2.sh",
                    os.X_OK,
                )
            )
            pid = int(launch["process"]["pid"])
            try:
                verified = self._run(
                    "verify",
                    "--project-root",
                    project,
                    "--state-directory",
                    state,
                    "--output",
                    attestation,
                    cwd=project,
                )
                self.assertEqual(json.loads(verified.stdout)["status"], "detachment_accepted")
                evidence = json.loads((project / attestation).read_text(encoding="utf-8"))
                self.assertEqual(evidence["status"], "accepted")
                self.assertEqual(
                    evidence["hup_event_count_after"],
                    evidence["hup_event_count_before"] + 1,
                )
                os.kill(pid, 0)

                subprocess.check_call(["git", "add", str(attestation)], cwd=project)
                subprocess.check_call(["git", "commit", "-qm", "attest detachment"], cwd=project)
                self._run(
                    "go",
                    "--project-root",
                    project,
                    "--state-directory",
                    state,
                    "--attestation",
                    attestation,
                    cwd=project,
                )
                deadline = time.monotonic() + 10.0
                while time.monotonic() < deadline and not (state / "terminal.json").is_file():
                    time.sleep(0.05)
                terminal = json.loads((state / "terminal.json").read_text(encoding="utf-8"))
                self.assertEqual(terminal["status"], "accepted")
                self.assertEqual(terminal["runner_return_code"], 0)
                finalized = self._run(
                    "finalize",
                    "--project-root",
                    project,
                    "--state-directory",
                    state,
                    cwd=project,
                )
                self.assertEqual(
                    json.loads(finalized.stdout)["status"],
                    "supervisor_completion_accepted",
                )
                completion_path = project / (
                    "orchestration/s1/g1_thermodynamic_label_audit_r2_20260806/"
                    "supervisor_completion.json"
                )
                completion = json.loads(completion_path.read_text(encoding="utf-8"))
                self.assertEqual(completion["status"], "supervisor_completed")
                self.assertEqual(completion["runner_exit_code"], 0)
                self.assertEqual(completion["analysis_audit_status"], "accepted")
                self.assertEqual(
                    set(completion),
                    {
                        "schema_version",
                        "protocol_revision",
                        "status",
                        "created_utc",
                        "config_path",
                        "config_sha256",
                        "manifest_path",
                        "manifest_sha256",
                        "git_head_before_completion",
                        "supervisor_state_directory",
                        "supervisor_launch_path",
                        "supervisor_launch_sha256",
                        "supervisor_terminal_path",
                        "supervisor_terminal_sha256",
                        "supervisor_journal_path",
                        "supervisor_journal_sha256",
                        "supervisor_pid",
                        "supervisor_start_time_ticks",
                        "boot_id",
                        "runner_exit_code",
                        "analysis_path",
                        "analysis_sha256",
                        "analysis_audit_status",
                        "final_acceptance_policy",
                    },
                )
            finally:
                try:
                    os.kill(pid, 15)
                except ProcessLookupError:
                    pass

    def test_start_refuses_state_inside_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            project = self._fixture_repository(temporary)
            result = subprocess.run(
                [
                    sys.executable,
                    "-s",
                    str(LAUNCHER),
                    "start",
                    "--project-root",
                    str(project),
                    "--state-directory",
                    str(project / "forbidden-state"),
                ],
                cwd=project,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=dict(LAUNCHER_MODULE.FROZEN_AMBIENT_ENVIRONMENT_VALUES),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((project / "forbidden-state").exists())


if __name__ == "__main__":
    unittest.main()
