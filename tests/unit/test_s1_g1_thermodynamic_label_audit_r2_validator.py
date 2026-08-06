from __future__ import annotations

import inspect
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import validate_s1_g1_thermodynamic_label_audit_r1 as R1  # noqa: E402
import validate_s1_g1_thermodynamic_label_audit_r2 as VALIDATOR  # noqa: E402


def logical_config() -> dict:
    return {
        "logical_run_matrix": [
            {
                "logical_experiment_id": logical,
                "physical_experiment_id": VALIDATOR.effective_id(logical),
            }
            for logical in VALIDATOR.LOGICAL_IDS
        ]
    }


def ambient_contract() -> dict[str, object]:
    return {
        "keys_exact": list(VALIDATOR.registration.FROZEN_AMBIENT_ENVIRONMENT_KEYS),
        "values_exact": dict(
            VALIDATOR.registration.FROZEN_AMBIENT_ENVIRONMENT_VALUES
        ),
        "canonical_values_sha256": (
            VALIDATOR.registration.FROZEN_AMBIENT_ENVIRONMENT_SHA256
        ),
        "mutating_launcher_exact_match_required": True,
        "supervisor_umask_exact": "0022",
        "python_no_user_site_required": True,
        "validator_subprocess_explicit_environment_required": True,
        "supervisor_subprocess_explicit_environment_required": True,
        "runner_additional_binding_keys_exact": list(
            VALIDATOR.registration.RUNNER_BINDING_ENVIRONMENT_KEYS
        ),
        "runner_registered_bash_required": True,
    }


def launch_environment() -> dict[str, object]:
    contract = ambient_contract()
    return {
        key: contract[key]
        for key in ("keys_exact", "values_exact", "canonical_values_sha256")
    }


def exact_launch_record(
    root: Path,
    state: Path,
    process: dict[str, object],
    registered: dict[str, object],
    boot_id: str,
    git_head: str,
) -> dict[str, object]:
    launcher_path = (
        root / "scripts/launch_s1_g1_thermodynamic_label_audit_r2.py"
    )
    return {
        "schema_version": 1,
        "protocol_revision": VALIDATOR.PROTOCOL_REVISION,
        "status": "waiting_for_detachment_attestation",
        "launch_method": "python_subprocess_start_new_session",
        "restart_policy": "never",
        "project_root": str(root),
        "hostname": os.uname().nodename,
        "working_directory": str(root),
        "umask": "0022",
        "environment": launch_environment(),
        "state_directory": str(state),
        "lock_path": str(state / "supervisor.lock"),
        "log_path": str(state / "supervisor.log"),
        "boot_id": boot_id,
        "process": process,
        "git_head_at_launch": git_head,
        "registered_files": registered,
        "sealed_execution_inputs": VALIDATOR._expected_sealed_execution_inputs(
            root, registered
        ),
        "launcher": {
            "path": str(launcher_path),
            "sha256": hashlib.sha256(launcher_path.read_bytes()).hexdigest(),
            "python_path": "/registered/python",
            "python_realpath": "/registered/python-real",
            "python_sha256": "1" * 64,
        },
        "runner_argv": [
            "/registered/bash",
            "/proc/self/fd/200",
            str(root),
            str(root / VALIDATOR.MANIFEST_PATH),
            str(root / VALIDATOR.CONFIG_PATH),
            "/proc/self/fd/201",
            "/proc/self/fd/202",
        ],
        "started_utc": "2026-08-06T12:00:00.000000Z",
    }


class S1G1ThermodynamicLabelAuditR2ValidatorTest(unittest.TestCase):
    def test_cli_detachment_mode_requires_live_sealed_inputs(self) -> None:
        config = logical_config()
        argv = [
            str(SCRIPTS / "validate_s1_g1_thermodynamic_label_audit_r2.py"),
            "--check-detachment-attestation",
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(
                VALIDATOR,
                "validate_registration",
                return_value=(
                    config,
                    [],
                    {
                        "preregistration_commit": "a" * 40,
                        "r1_bridge": {"reused": {}},
                    },
                ),
            ),
            mock.patch.object(
                VALIDATOR,
                "validate_detachment_attestation",
                return_value=({"status": "accepted"}, []),
            ) as detachment_check,
            mock.patch.object(VALIDATOR, "sha256", return_value="0" * 64),
            mock.patch("builtins.print"),
        ):
            self.assertEqual(VALIDATOR.main(), 0)
        detachment_check.assert_called_once_with(
            PROJECT_ROOT,
            config,
            require_committed=False,
            require_live_sealed_inputs=True,
        )

    def test_cli_detachment_record_mode_allows_closed_sealed_inputs(self) -> None:
        config = logical_config()
        argv = [
            str(SCRIPTS / "validate_s1_g1_thermodynamic_label_audit_r2.py"),
            "--check-detachment-attestation-record",
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(
                VALIDATOR,
                "validate_registration",
                return_value=(
                    config,
                    [],
                    {
                        "preregistration_commit": "a" * 40,
                        "r1_bridge": {"reused": {}},
                    },
                ),
            ),
            mock.patch.object(
                VALIDATOR,
                "validate_detachment_attestation",
                return_value=({"status": "accepted"}, []),
            ) as detachment_check,
            mock.patch.object(VALIDATOR, "sha256", return_value="0" * 64),
            mock.patch("builtins.print"),
        ):
            self.assertEqual(VALIDATOR.main(), 0)
        detachment_check.assert_called_once_with(
            PROJECT_ROOT,
            config,
            require_committed=False,
            require_live_sealed_inputs=False,
        )

    def test_cli_completion_mode_forces_committed_registration_and_revalidation(
        self,
    ) -> None:
        config = logical_config()
        details = {
            "preregistration_commit": "a" * 40,
            "r1_bridge": {"reused": {}},
        }
        argv = [
            str(SCRIPTS / "validate_s1_g1_thermodynamic_label_audit_r2.py"),
            "--require-supervisor-completion",
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(
                VALIDATOR,
                "validate_registration",
                return_value=(config, [], details),
            ) as registration_check,
            mock.patch.object(
                VALIDATOR,
                "validate_supervisor_completion",
                return_value=({"status": "supervisor_completed"}, []),
            ) as completion_check,
            mock.patch.object(
                VALIDATOR,
                "recompute_final_analysis",
                return_value=[],
            ) as science_replay,
            mock.patch.object(VALIDATOR, "sha256", return_value="0" * 64),
            mock.patch("builtins.print"),
        ):
            self.assertEqual(VALIDATOR.main(), 0)
        self.assertTrue(registration_check.call_args.kwargs["require_committed"])
        self.assertTrue(
            registration_check.call_args.kwargs["skip_terminal_evidence_validation"]
        )
        completion_check.assert_called_once_with(
            PROJECT_ROOT, config, require_committed=True
        )
        science_replay.assert_called_once()

    def test_cli_imported_p0_gate_needs_no_new_r2_run(self) -> None:
        registration = {
            "preregistration_commit": "a" * 40,
            "r1_bridge": {"reused": {}},
        }
        accepted = {"pair_count": 2, "pairs": [], "accepted": True}
        argv = [
            str(SCRIPTS / "validate_s1_g1_thermodynamic_label_audit_r2.py"),
            "--require-pilot-gate",
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(
                VALIDATOR,
                "validate_registration",
                return_value=(logical_config(), [], registration),
            ),
            mock.patch.object(
                VALIDATOR,
                "evaluate_imported_p0_gate",
                return_value=accepted,
            ) as imported,
            mock.patch.object(VALIDATOR, "sha256", return_value="0" * 64),
            mock.patch("builtins.print"),
        ):
            self.assertEqual(VALIDATOR.main(), 0)
        imported.assert_called_once_with(PROJECT_ROOT, require_committed=True)

    def test_final_science_replay_compares_regenerated_artifacts_byte_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            committed = root / VALIDATOR.ANALYSIS_ROOT
            committed.mkdir(parents=True)
            expected = {
                "summary.json": b'{"audit_status":"accepted"}\n',
                "points.tsv": b"point\n",
                "label_metrics.tsv": b"metric\n",
                "README.md": b"accepted\n",
            }
            for relative, data in expected.items():
                (committed / relative).write_bytes(data)

            def regenerate(
                _root,
                _config,
                _manifest,
                output,
                **kwargs,
            ):
                self.assertTrue(kwargs["require_committed"])
                self.assertTrue(kwargs["skip_terminal_evidence_validation"])
                output.mkdir(parents=True)
                for relative, data in expected.items():
                    (output / relative).write_bytes(data)
                return {"audit_status": "accepted"}

            with mock.patch(
                "analyze_s1_g1_thermodynamic_label_audit_r2.analyze",
                side_effect=regenerate,
            ):
                errors = VALIDATOR.recompute_final_analysis(
                    root, root / "config.json", root / "manifest.tsv"
                )
            self.assertEqual(errors, [])

            def forge(*arguments, **kwargs):
                result = regenerate(*arguments, **kwargs)
                output = arguments[3]
                (output / "summary.json").write_bytes(b'{"audit_status":"forged"}\n')
                return result

            with mock.patch(
                "analyze_s1_g1_thermodynamic_label_audit_r2.analyze",
                side_effect=forge,
            ):
                errors = VALIDATOR.recompute_final_analysis(
                    root, root / "config.json", root / "manifest.tsv"
                )
            self.assertTrue(any("summary.json" in error for error in errors))

    def test_detachment_attestation_replays_exact_hup_and_process_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "external-state"
            state.mkdir()
            (state / "supervisor.log").write_text("", encoding="utf-8")
            for relative, data in (
                (VALIDATOR.CONFIG_PATH, b"{}\n"),
                (VALIDATOR.MANIFEST_PATH, b"header\n"),
                (
                    Path("scripts/run_s1_g1_thermodynamic_label_audit_r2.sh"),
                    b"#!/bin/sh\n",
                ),
                (
                    Path("scripts/launch_s1_g1_thermodynamic_label_audit_r2.py"),
                    b"# frozen launcher\n",
                ),
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)

            def digest(path: Path) -> str:
                return hashlib.sha256(path.read_bytes()).hexdigest()

            registered = {
                "config_path": str(VALIDATOR.CONFIG_PATH),
                "config_sha256": digest(root / VALIDATOR.CONFIG_PATH),
                "manifest_path": str(VALIDATOR.MANIFEST_PATH),
                "manifest_sha256": digest(root / VALIDATOR.MANIFEST_PATH),
                "runner_path": "scripts/run_s1_g1_thermodynamic_label_audit_r2.sh",
                "runner_sha256": digest(
                    root / "scripts/run_s1_g1_thermodynamic_label_audit_r2.sh"
                ),
            }
            process = {
                "pid": 1234,
                "ppid": 1,
                "process_group_id": 1234,
                "session_id": 1234,
                "tty_nr": 0,
                "start_time_ticks": 999,
                "stdin": "/dev/null",
                "stdout": str(state / "supervisor.log"),
                "stderr": str(state / "supervisor.log"),
            }
            boot_id = "11111111-2222-3333-4444-555555555555"
            launch_path = state / "launch.json"
            launch_path.write_text(
                json.dumps(
                    exact_launch_record(
                        root, state, process, registered, boot_id, "0" * 40
                    )
                ),
                encoding="utf-8",
            )
            (state / "journal.jsonl").write_text(
                "".join(
                    json.dumps(event, sort_keys=True) + "\n"
                    for event in (
                        {
                            "event": "waiting_for_go",
                            "pid": process["pid"],
                            "utc": "2026-08-06T12:00:00.000001Z",
                        },
                        {
                            "event": "sighup_received",
                            "pid": process["pid"],
                            "utc": "2026-08-06T12:00:00.000002Z",
                        },
                    )
                ),
                encoding="utf-8",
            )
            attestation = {
                "schema_version": 1,
                "protocol_revision": VALIDATOR.PROTOCOL_REVISION,
                "status": "accepted",
                "launch_path": str(launch_path),
                "launch_sha256": digest(launch_path),
                "boot_id": boot_id,
                "supervisor_process_before_hup": process,
                "supervisor_process_after_hup": dict(process),
                "hup_event_count_before": 0,
                "hup_event_count_after": 1,
                "registered_files": registered,
                "git_head": "0" * 40,
                "attested_utc": "2026-08-06T12:00:00.123456Z",
            }
            attestation_path = root / VALIDATOR.registration.DETACHMENT_ATTESTATION_PATH
            attestation_path.parent.mkdir(parents=True, exist_ok=True)
            config = {
                "execution": {
                    "detachment_attestation_path": str(
                        VALIDATOR.registration.DETACHMENT_ATTESTATION_PATH
                    ),
                    "ambient_environment": ambient_contract(),
                },
                "runtime": {
                    "tools": {
                        "bash": {"path": "/registered/bash"},
                        "python": {
                            "path": "/registered/python",
                            "realpath": "/registered/python-real",
                            "sha256": "1" * 64,
                        },
                    }
                },
            }
            subprocess.check_call(["git", "init", "-q", str(root)])
            subprocess.check_call(
                ["git", "-C", str(root), "config", "user.email", "r2@test.invalid"]
            )
            subprocess.check_call(
                ["git", "-C", str(root), "config", "user.name", "R2 Test"]
            )
            subprocess.check_call(
                [
                    "git",
                    "-C",
                    str(root),
                    "add",
                    str(VALIDATOR.CONFIG_PATH),
                    str(VALIDATOR.MANIFEST_PATH),
                    "scripts/run_s1_g1_thermodynamic_label_audit_r2.sh",
                    "scripts/launch_s1_g1_thermodynamic_label_audit_r2.py",
                ]
            )
            subprocess.check_call(
                ["git", "-C", str(root), "commit", "-q", "-m", "preregister"]
            )
            prereg = subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
            ).strip()
            launch_payload = json.loads(launch_path.read_text(encoding="utf-8"))
            launch_payload["git_head_at_launch"] = prereg
            launch_path.write_text(json.dumps(launch_payload), encoding="utf-8")
            attestation["git_head"] = prereg
            attestation["launch_sha256"] = digest(launch_path)
            attestation_path.write_text(json.dumps(attestation), encoding="utf-8")
            subprocess.check_call(
                [
                    "git",
                    "-C",
                    str(root),
                    "add",
                    str(VALIDATOR.registration.DETACHMENT_ATTESTATION_PATH),
                ]
            )
            subprocess.check_call(
                ["git", "-C", str(root), "commit", "-q", "-m", "attest"]
            )
            with mock.patch.object(
                VALIDATOR, "SUPERVISOR_STATE_DIRECTORY", str(state)
            ):
                _, errors = VALIDATOR.validate_detachment_attestation(
                    root, config, require_committed=True
                )
                self.assertEqual(errors, [])
                with (state / "journal.jsonl").open("a", encoding="utf-8") as handle:
                    for event in (
                        {
                            "event": "go_accepted",
                            "pid": process["pid"],
                            "utc": "2026-08-06T12:00:00.000003Z",
                            "git_head": prereg,
                            "go_sha256": "a" * 64,
                        },
                        {
                            "event": "runner_started",
                            "pid": process["pid"],
                            "utc": "2026-08-06T12:00:00.000004Z",
                            "child_pid": 4321,
                            "child_start_time_ticks": 777,
                        },
                        {
                            "event": "runner_finished",
                            "pid": process["pid"],
                            "utc": "2026-08-06T12:00:00.000005Z",
                            "return_code": 0,
                        },
                    ):
                        handle.write(json.dumps(event, sort_keys=True) + "\n")
                _, completed_journal_errors = (
                    VALIDATOR.validate_detachment_attestation(
                        root, config, require_committed=True
                    )
                )
                self.assertEqual(completed_journal_errors, [])
                exact_launch = json.loads(launch_path.read_text(encoding="utf-8"))
                forged_launch = deepcopy(exact_launch)
                forged_launch.pop("launcher")
                launch_path.write_text(json.dumps(forged_launch), encoding="utf-8")
                attestation["launch_sha256"] = digest(launch_path)
                attestation_path.write_text(json.dumps(attestation), encoding="utf-8")
                _, launch_errors = VALIDATOR.validate_detachment_attestation(
                    root, config, require_committed=False
                )
                self.assertTrue(
                    any("launch key set differs" in failure for failure in launch_errors)
                )
                self.assertTrue(
                    any("launch tool identity differs" in failure for failure in launch_errors)
                )
                forged_launch = deepcopy(exact_launch)
                forged_launch["schema_version"] = True
                launch_path.write_text(json.dumps(forged_launch), encoding="utf-8")
                attestation["launch_sha256"] = digest(launch_path)
                attestation_path.write_text(json.dumps(attestation), encoding="utf-8")
                _, launch_errors = VALIDATOR.validate_detachment_attestation(
                    root, config, require_committed=False
                )
                self.assertTrue(
                    any(
                        "launch schema_version differs" in failure
                        for failure in launch_errors
                    )
                )
                launch_path.write_text(json.dumps(exact_launch), encoding="utf-8")
                attestation["launch_sha256"] = digest(launch_path)
                attestation["hup_event_count_after"] = 2
                attestation["supervisor_process_after_hup"]["tty_nr"] = 7
                attestation_path.write_text(json.dumps(attestation), encoding="utf-8")
                _, errors = VALIDATOR.validate_detachment_attestation(
                    root, config, require_committed=False
                )
                attestation["hup_event_count_after"] = 1
                attestation["supervisor_process_after_hup"] = dict(process)
                attestation["supervisor_process_after_hup"]["ppid"] = "1"
                attestation["supervisor_process_after_hup"]["tty_nr"] = False
                attestation_path.write_text(json.dumps(attestation), encoding="utf-8")
                _, integer_errors = VALIDATOR.validate_detachment_attestation(
                    root, config, require_committed=False
                )
            self.assertTrue(any("HUP event count" in failure for failure in errors))
            self.assertTrue(any("fully detached" in failure for failure in errors))
            self.assertTrue(
                any("fully detached" in failure for failure in integer_errors)
            )

    def test_detachment_rejects_static_launch_and_hup_journal_forgery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "external-state"
            state.mkdir()
            for relative, data in (
                (VALIDATOR.CONFIG_PATH, b"{}\n"),
                (VALIDATOR.MANIFEST_PATH, b"header\n"),
                (
                    Path("scripts/run_s1_g1_thermodynamic_label_audit_r2.sh"),
                    b"#!/bin/sh\n",
                ),
                (
                    Path("scripts/launch_s1_g1_thermodynamic_label_audit_r2.py"),
                    b"# frozen launcher\n",
                ),
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)

            def digest(path: Path) -> str:
                return hashlib.sha256(path.read_bytes()).hexdigest()

            registered = {
                "config_path": str(VALIDATOR.CONFIG_PATH),
                "config_sha256": digest(root / VALIDATOR.CONFIG_PATH),
                "manifest_path": str(VALIDATOR.MANIFEST_PATH),
                "manifest_sha256": digest(root / VALIDATOR.MANIFEST_PATH),
                "runner_path": "scripts/run_s1_g1_thermodynamic_label_audit_r2.sh",
                "runner_sha256": digest(
                    root / "scripts/run_s1_g1_thermodynamic_label_audit_r2.sh"
                ),
            }
            boot_id = "11111111-2222-3333-4444-555555555555"
            attested_process = {
                "pid": 701,
                "ppid": 1,
                "process_group_id": 701,
                "session_id": 701,
                "tty_nr": 0,
                "start_time_ticks": 99,
                "stdin": "/dev/null",
                "stdout": str(state / "supervisor.log"),
                "stderr": str(state / "supervisor.log"),
            }
            (state / "supervisor.log").write_bytes(b"")
            launch_path = state / "launch.json"
            launch_path.write_text(
                json.dumps(
                    exact_launch_record(
                        root,
                        state,
                        {"pid": 700, "start_time_ticks": 88},
                        registered,
                        boot_id,
                        "0" * 40,
                    ),
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            (state / "journal.jsonl").write_text(
                "".join(
                    json.dumps(event, sort_keys=True) + "\n"
                    for event in (
                        {
                            "event": "waiting_for_go",
                            "pid": 700,
                            "utc": "2026-08-06T12:00:00.000001Z",
                        },
                        {
                            "event": "sighup_received",
                            "pid": 700,
                            "utc": "2026-08-06T12:00:00.000002Z",
                        },
                        {
                            "event": "sighup_received",
                            "pid": 700,
                            "utc": "2026-08-06T12:00:00.000003Z",
                        },
                    )
                ),
                encoding="utf-8",
            )
            attestation = {
                "schema_version": 1,
                "protocol_revision": VALIDATOR.PROTOCOL_REVISION,
                "status": "accepted",
                "launch_path": str(launch_path),
                "launch_sha256": digest(launch_path),
                "boot_id": boot_id,
                "supervisor_process_before_hup": attested_process,
                "supervisor_process_after_hup": dict(attested_process),
                "hup_event_count_before": 0,
                "hup_event_count_after": 1,
                "registered_files": registered,
                "git_head": "0" * 40,
                "attested_utc": "2026-08-06T12:00:00.000004Z",
            }
            attestation_path = (
                root / VALIDATOR.registration.DETACHMENT_ATTESTATION_PATH
            )
            attestation_path.parent.mkdir(parents=True)
            attestation_path.write_text(
                json.dumps(attestation, sort_keys=True) + "\n", encoding="utf-8"
            )
            config = {
                "execution": {
                    "detachment_attestation_path": str(
                        VALIDATOR.registration.DETACHMENT_ATTESTATION_PATH
                    ),
                    "ambient_environment": ambient_contract(),
                },
                "runtime": {
                    "tools": {
                        "bash": {"path": "/registered/bash"},
                        "python": {
                            "path": "/registered/python",
                            "realpath": "/registered/python-real",
                            "sha256": "1" * 64,
                        },
                    }
                },
            }
            with mock.patch.object(
                VALIDATOR, "SUPERVISOR_STATE_DIRECTORY", str(state)
            ):
                _, forgery_errors = VALIDATOR.validate_detachment_attestation(
                    root, config, require_committed=False
                )
                launch = json.loads(launch_path.read_text(encoding="utf-8"))
                launch["process"] = {
                    "pid": attested_process["pid"],
                    "start_time_ticks": attested_process["start_time_ticks"],
                }
                launch_path.write_text(
                    json.dumps(launch, sort_keys=True) + "\n", encoding="utf-8"
                )
                attestation["launch_sha256"] = digest(launch_path)
                attestation_path.write_text(
                    json.dumps(attestation, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                (state / "journal.jsonl").write_text(
                    "".join(
                        json.dumps(event, sort_keys=True) + "\n"
                        for event in (
                            {
                                "event": "sighup_received",
                                "pid": attested_process["pid"],
                                "utc": "2026-08-06T12:00:00.000001Z",
                            },
                            {
                                "event": "waiting_for_go",
                                "pid": attested_process["pid"],
                                "utc": "2026-08-06T12:00:00.000002Z",
                            },
                        )
                    ),
                    encoding="utf-8",
                )
                _, order_errors = VALIDATOR.validate_detachment_attestation(
                    root, config, require_committed=False
                )
            self.assertTrue(
                any(
                    "does not match external launch process" in error
                    for error in forgery_errors
                )
            )
            self.assertTrue(
                any(
                    "journal HUP event count differs" in error
                    for error in forgery_errors
                )
            )
            self.assertTrue(
                any(
                    "must start with one waiting event" in error
                    for error in order_errors
                )
            )

    def test_supervisor_completion_binds_external_receipts_and_exact_git_parent(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            root = temporary_root / "repo"
            state = temporary_root / "state"
            root.mkdir()
            state.mkdir()

            def write(relative: Path | str, data: bytes) -> Path:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                return path

            def digest(path: Path) -> str:
                return hashlib.sha256(path.read_bytes()).hexdigest()

            def git(*arguments: str) -> str:
                return subprocess.check_output(
                    ["git", "-C", str(root), *arguments], text=True
                ).strip()

            config_path = write(VALIDATOR.CONFIG_PATH, b"{}\n")
            manifest_path = write(VALIDATOR.MANIFEST_PATH, b"header\n")
            runner_path = write(
                "scripts/run_s1_g1_thermodynamic_label_audit_r2.sh",
                b"#!/bin/sh\n",
            )
            subprocess.check_call(["git", "init", "-q", str(root)])
            subprocess.check_call(
                ["git", "-C", str(root), "config", "user.email", "r2@test.invalid"]
            )
            subprocess.check_call(
                ["git", "-C", str(root), "config", "user.name", "R2 Test"]
            )
            subprocess.check_call(["git", "-C", str(root), "add", "."])
            subprocess.check_call(
                ["git", "-C", str(root), "commit", "-q", "-m", "registered"]
            )
            attestation_path = write(
                VALIDATOR.registration.DETACHMENT_ATTESTATION_PATH,
                b'{"status":"accepted"}\n',
            )
            subprocess.check_call(
                [
                    "git",
                    "-C",
                    str(root),
                    "add",
                    str(VALIDATOR.registration.DETACHMENT_ATTESTATION_PATH),
                ]
            )
            subprocess.check_call(
                ["git", "-C", str(root), "commit", "-q", "-m", "detachment"]
            )
            go_head = git("rev-parse", "HEAD")
            first_marker = write(
                VALIDATOR.ATTEMPT_LEDGER_ROOT
                / f"{VALIDATOR.R2_AUDIT_IDS[0]}.json",
                b"{}\n",
            )
            subprocess.check_call(
                ["git", "-C", str(root), "add", str(first_marker.relative_to(root))]
            )
            subprocess.check_call(
                ["git", "-C", str(root), "commit", "-q", "-m", "first marker"]
            )

            analysis_relative = Path(
                "analysis/s1/g1_thermodynamic_label_audit_r2_20260806/summary.json"
            )
            analysis_path = write(
                analysis_relative,
                (
                    json.dumps(
                        {
                            "schema_version": 2,
                            "protocol_revision": VALIDATOR.PROTOCOL_REVISION,
                            "audit_status": "accepted",
                            "overall_protocol_status": "pending_supervisor_completion",
                            "g1_status": "pending (1/6)",
                            "authorized_scope": "no_G1_advancement",
                        },
                        sort_keys=True,
                    )
                    + "\n"
                ).encode(),
            )
            subprocess.check_call(
                ["git", "-C", str(root), "add", analysis_relative.as_posix()]
            )
            subprocess.check_call(
                ["git", "-C", str(root), "commit", "-q", "-m", "analysis"]
            )
            analysis_commit = git("rev-parse", "HEAD")

            registered = {
                "config_path": str(VALIDATOR.CONFIG_PATH),
                "config_sha256": digest(config_path),
                "manifest_path": str(VALIDATOR.MANIFEST_PATH),
                "manifest_sha256": digest(manifest_path),
                "runner_path": "scripts/run_s1_g1_thermodynamic_label_audit_r2.sh",
                "runner_sha256": digest(runner_path),
            }
            sealed_inputs = VALIDATOR._expected_sealed_execution_inputs(
                root, registered
            )
            boot_id = "11111111-2222-3333-4444-555555555555"
            supervisor_pid = 111
            supervisor_start = 222
            launch_path = state / "launch.json"
            launch_path.write_text(
                json.dumps(
                    {
                        "protocol_revision": VALIDATOR.PROTOCOL_REVISION,
                        "status": "waiting_for_detachment_attestation",
                        "state_directory": str(state),
                        "boot_id": boot_id,
                        "process": {
                            "pid": supervisor_pid,
                            "ppid": 1,
                            "process_group_id": supervisor_pid,
                            "session_id": supervisor_pid,
                            "tty_nr": 0,
                            "start_time_ticks": supervisor_start,
                        },
                        "registered_files": registered,
                        "sealed_execution_inputs": sealed_inputs,
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            go_path = state / "go.json"
            go_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "protocol_revision": VALIDATOR.PROTOCOL_REVISION,
                        "status": "go",
                        "launch_sha256": digest(launch_path),
                        "boot_id": boot_id,
                        "supervisor_pid": supervisor_pid,
                        "supervisor_start_time_ticks": supervisor_start,
                        "attestation_path": str(
                            VALIDATOR.registration.DETACHMENT_ATTESTATION_PATH
                        ),
                        "attestation_sha256": digest(attestation_path),
                        "git_head": go_head,
                        "registered_files": registered,
                        "sealed_execution_inputs_sha256": (
                            VALIDATOR._sealed_execution_inputs_sha256(
                                sealed_inputs
                            )
                        ),
                        "created_utc": "2026-08-06T12:00:00.000001Z",
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            log_path = state / "supervisor.log"
            log_path.write_bytes(b"")
            journal_path = state / "journal.jsonl"
            journal = [
                {
                    "event": "waiting_for_go",
                    "pid": supervisor_pid,
                    "utc": "2026-08-06T12:00:00.000002Z",
                },
                {
                    "event": "go_accepted",
                    "pid": supervisor_pid,
                    "utc": "2026-08-06T12:00:00.000002Z",
                    "git_head": go_head,
                    "go_sha256": digest(go_path),
                },
                {
                    "event": "runner_started",
                    "pid": supervisor_pid,
                    "utc": "2026-08-06T12:00:00.000003Z",
                    "child_pid": 333,
                    "child_start_time_ticks": 444,
                },
                {
                    "event": "runner_finished",
                    "pid": supervisor_pid,
                    "utc": "2026-08-06T12:00:00.000004Z",
                    "return_code": 0,
                },
            ]
            journal_path.write_text(
                "".join(json.dumps(item, sort_keys=True) + "\n" for item in journal),
                encoding="utf-8",
            )
            terminal_path = state / "terminal.json"
            terminal_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "protocol_revision": VALIDATOR.PROTOCOL_REVISION,
                        "status": "accepted",
                        "runner_return_code": 0,
                        "runner_pid": 333,
                        "runner_start_time_ticks": 444,
                        "launch_sha256": digest(launch_path),
                        "go_sha256": digest(go_path),
                        "journal_sha256": digest(journal_path),
                        "git_head_after_runner": analysis_commit,
                        "analysis_summary_path": analysis_relative.as_posix(),
                        "analysis_summary_sha256": digest(analysis_path),
                        "log_sha256": digest(log_path),
                        "finished_utc": "2026-08-06T12:00:00.000005Z",
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

            completion_contract = {
                "required_keys_exact": list(
                    VALIDATOR.registration.SUPERVISOR_COMPLETION_REQUIRED_KEYS
                ),
                "schema_version": 1,
                "status": "supervisor_completed",
                "runner_exit_code": 0,
                "scientific_analysis_status": "accepted",
                "overall_protocol_status_before_completion": (
                    "pending_supervisor_completion"
                ),
                "analysis_audit_status": "accepted",
                "final_acceptance_policy": (
                    "committed_completion_then_validator_revalidation"
                ),
                "allowed_post_completion_commit_paths_exact": list(
                    VALIDATOR.POST_TERMINAL_DOCUMENTATION_PATHS
                ),
            }
            config = {
                "execution": {
                    "supervisor_completion_path": str(
                        VALIDATOR.SUPERVISOR_COMPLETION_PATH
                    ),
                    "supervisor_completion_contract": completion_contract,
                }
            }
            completion = {
                "schema_version": 1,
                "protocol_revision": VALIDATOR.PROTOCOL_REVISION,
                "status": "supervisor_completed",
                "created_utc": "2026-08-06T12:00:00.000006Z",
                "config_path": str(VALIDATOR.CONFIG_PATH),
                "config_sha256": digest(config_path),
                "manifest_path": str(VALIDATOR.MANIFEST_PATH),
                "manifest_sha256": digest(manifest_path),
                "git_head_before_completion": analysis_commit,
                "supervisor_state_directory": str(state),
                "supervisor_launch_path": str(launch_path),
                "supervisor_launch_sha256": digest(launch_path),
                "supervisor_terminal_path": str(terminal_path),
                "supervisor_terminal_sha256": digest(terminal_path),
                "supervisor_journal_path": str(journal_path),
                "supervisor_journal_sha256": digest(journal_path),
                "supervisor_pid": supervisor_pid,
                "supervisor_start_time_ticks": supervisor_start,
                "boot_id": boot_id,
                "runner_exit_code": 0,
                "analysis_path": analysis_relative.as_posix(),
                "analysis_sha256": digest(analysis_path),
                "analysis_audit_status": "accepted",
                "final_acceptance_policy": (
                    "committed_completion_then_validator_revalidation"
                ),
            }
            completion_path = write(
                VALIDATOR.SUPERVISOR_COMPLETION_PATH,
                (json.dumps(completion, sort_keys=True) + "\n").encode(),
            )
            subprocess.check_call(
                [
                    "git",
                    "-C",
                    str(root),
                    "add",
                    str(VALIDATOR.SUPERVISOR_COMPLETION_PATH),
                ]
            )
            subprocess.check_call(
                ["git", "-C", str(root), "commit", "-q", "-m", "completion"]
            )
            with (
                mock.patch.object(
                    VALIDATOR, "SUPERVISOR_STATE_DIRECTORY", str(state)
                ),
                mock.patch.object(
                    VALIDATOR,
                    "validate_detachment_attestation",
                    return_value=({"status": "accepted"}, []),
                ) as detachment_replay,
            ):
                _, errors = VALIDATOR.validate_supervisor_completion(
                    root, config, require_committed=True
                )
                self.assertEqual(errors, [])
                documentation = root / VALIDATOR.POST_TERMINAL_DOCUMENTATION_PATHS[0]
                documentation.parent.mkdir(parents=True, exist_ok=True)
                documentation.write_text("post-completion handoff\n", encoding="utf-8")
                subprocess.check_call(["git", "-C", str(root), "add", str(documentation)])
                subprocess.check_call(
                    ["git", "-C", str(root), "commit", "-q", "-m", "document completion"]
                )
                _, errors = VALIDATOR.validate_supervisor_completion(
                    root, config, require_committed=True
                )
                self.assertEqual(errors, [])
                frozen_go = go_path.read_bytes()
                frozen_journal = journal_path.read_bytes()
                frozen_terminal = terminal_path.read_bytes()
                frozen_completion = completion_path.read_bytes()
                forged_go = json.loads(frozen_go)
                forged_go["git_head"] = analysis_commit
                go_path.write_text(json.dumps(forged_go), encoding="utf-8")
                forged_journal = [dict(item) for item in journal]
                forged_go_event = next(
                    item
                    for item in forged_journal
                    if item.get("event") == "go_accepted"
                )
                forged_go_event["git_head"] = analysis_commit
                forged_go_event["go_sha256"] = digest(go_path)
                journal_path.write_text(
                    "".join(
                        json.dumps(item, sort_keys=True) + "\n"
                        for item in forged_journal
                    ),
                    encoding="utf-8",
                )
                forged_terminal = json.loads(frozen_terminal)
                forged_terminal["go_sha256"] = digest(go_path)
                forged_terminal["journal_sha256"] = digest(journal_path)
                terminal_path.write_text(json.dumps(forged_terminal), encoding="utf-8")
                forged_completion = json.loads(frozen_completion)
                forged_completion["supervisor_terminal_sha256"] = digest(
                    terminal_path
                )
                forged_completion["supervisor_journal_sha256"] = digest(journal_path)
                completion_path.write_text(
                    json.dumps(forged_completion), encoding="utf-8"
                )
                _, causal_errors = VALIDATOR.validate_supervisor_completion(
                    root, config, require_committed=True
                )
                self.assertTrue(
                    any(
                        "GO Git HEAD is not the detachment introduction" in failure
                        for failure in causal_errors
                    )
                )
                self.assertTrue(
                    any(
                        "first R2 attempt marker parent" in failure
                        for failure in causal_errors
                    )
                )
                go_path.write_bytes(frozen_go)
                journal_path.write_bytes(frozen_journal)
                terminal_path.write_bytes(frozen_terminal)
                completion_path.write_bytes(frozen_completion)
                terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
                terminal["journal_sha256"] = "0" * 64
                terminal_path.write_text(json.dumps(terminal), encoding="utf-8")
                _, errors = VALIDATOR.validate_supervisor_completion(
                    root, config, require_committed=True
                )
                terminal_path.write_bytes(frozen_terminal)
                boolean_terminal = json.loads(frozen_terminal)
                boolean_terminal["runner_return_code"] = False
                terminal_path.write_text(
                    json.dumps(boolean_terminal), encoding="utf-8"
                )
                _, boolean_errors = VALIDATOR.validate_supervisor_completion(
                    root, config, require_committed=True
                )
                terminal_path.write_bytes(frozen_terminal)
                boolean_completion = json.loads(frozen_completion)
                boolean_completion["runner_exit_code"] = False
                completion_path.write_text(
                    json.dumps(boolean_completion), encoding="utf-8"
                )
                _, completion_boolean_errors = (
                    VALIDATOR.validate_supervisor_completion(
                        root, config, require_committed=True
                    )
                )
            self.assertEqual(detachment_replay.call_count, 6)
            detachment_replay.assert_called_with(
                root, config, require_committed=True
            )
            self.assertTrue(
                any("terminal external-evidence hashes" in failure for failure in errors)
            )
            self.assertTrue(
                any("supervisor_terminal_sha256" in failure for failure in errors)
            )
            self.assertTrue(
                any(
                    "terminal identity/status differs" in failure
                    for failure in boolean_errors
                )
            )
            self.assertTrue(
                any(
                    "runner exit code type differs" in failure
                    for failure in completion_boolean_errors
                )
            )
            self.assertTrue(completion_path.is_file())

    def test_barrier_failure_is_exact_scope_and_forbids_later_attempt_marker(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            root = temporary_root / "repo"
            state = temporary_root / "state"
            root.mkdir()
            state.mkdir()
            config_path = root / VALIDATOR.CONFIG_PATH
            manifest_path = root / VALIDATOR.MANIFEST_PATH
            config_path.parent.mkdir(parents=True)
            config_path.write_text("{}\n", encoding="utf-8")
            manifest_path.write_text("header\n", encoding="utf-8")
            launch_path = state / "launch.json"
            launch_path.write_text('{"status":"started"}\n', encoding="utf-8")

            def digest(path: Path) -> str:
                return hashlib.sha256(path.read_bytes()).hexdigest()

            subprocess.check_call(["git", "init", "-q", str(root)])
            subprocess.check_call(
                ["git", "-C", str(root), "config", "user.email", "r2@test.invalid"]
            )
            subprocess.check_call(
                ["git", "-C", str(root), "config", "user.name", "R2 Test"]
            )
            subprocess.check_call(["git", "-C", str(root), "add", "."])
            subprocess.check_call(
                ["git", "-C", str(root), "commit", "-q", "-m", "registered"]
            )
            head_before = subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
            ).strip()
            barrier_name = "imported-p0-before-041"
            python_path = "/registered/python"
            command = [
                python_path,
                "-s",
                str(root / "scripts/validate_s1_g1_thermodynamic_label_audit_r2.py"),
                str(root / VALIDATOR.MANIFEST_PATH),
                "--config",
                str(root / VALIDATOR.CONFIG_PATH),
                "--scientific-config",
                "/proc/self/fd/202",
                "--scientific-manifest",
                "/proc/self/fd/201",
                "--require-committed",
                "--require-pilot-gate",
            ]
            barrier_contract = {
                "required_keys_exact": list(
                    VALIDATOR.registration.BARRIER_FAILURE_REQUIRED_KEYS
                ),
                "schema_version": 1,
                "status": "barrier_failed",
                "retry_policy": "stop_after_exact_scope_commit_no_continue_or_retry",
                "allowed_post_failure_commit_paths_exact": list(
                    VALIDATOR.POST_TERMINAL_DOCUMENTATION_PATHS
                ),
            }
            config = {
                "execution": {
                    "barrier_failure_root": str(VALIDATOR.BARRIER_FAILURE_ROOT),
                    "barrier_failure_contract": barrier_contract,
                },
                "runtime": {"tools": {"python": {"path": python_path}}},
            }
            barrier = {
                "schema_version": 1,
                "protocol_revision": VALIDATOR.PROTOCOL_REVISION,
                "status": "barrier_failed",
                "created_utc": "2026-08-06T12:00:00.000001Z",
                "barrier_name": barrier_name,
                "experiment_id": None,
                "logical_experiment_id": None,
                "command_argv": command,
                "exit_code": 1,
                "config_path": str(VALIDATOR.CONFIG_PATH),
                "config_sha256": digest(config_path),
                "manifest_path": str(VALIDATOR.MANIFEST_PATH),
                "manifest_sha256": digest(manifest_path),
                "git_head_before_failure": head_before,
                "supervisor_state_directory": str(state),
                "supervisor_launch_path": str(launch_path),
                "supervisor_launch_sha256": digest(launch_path),
                "retry_policy": "stop_after_exact_scope_commit_no_continue_or_retry",
            }
            barrier_path = root / VALIDATOR.BARRIER_FAILURE_ROOT / f"{barrier_name}.json"
            barrier_path.parent.mkdir(parents=True)
            barrier_path.write_text(json.dumps(barrier) + "\n", encoding="utf-8")
            subprocess.check_call(
                [
                    "git",
                    "-C",
                    str(root),
                    "add",
                    str(VALIDATOR.BARRIER_FAILURE_ROOT / f"{barrier_name}.json"),
                ]
            )
            subprocess.check_call(
                ["git", "-C", str(root), "commit", "-q", "-m", "barrier"]
            )
            with mock.patch.object(
                VALIDATOR, "SUPERVISOR_STATE_DIRECTORY", str(state)
            ):
                records, errors = VALIDATOR.validate_barrier_failures(
                    root, config, require_committed=True
                )
                self.assertEqual(len(records), 1)
                self.assertEqual(errors, [])

                documentation = root / VALIDATOR.POST_TERMINAL_DOCUMENTATION_PATHS[0]
                documentation.parent.mkdir(parents=True, exist_ok=True)
                documentation.write_text("post-failure handoff\n", encoding="utf-8")
                subprocess.check_call(["git", "-C", str(root), "add", str(documentation)])
                subprocess.check_call(
                    ["git", "-C", str(root), "commit", "-q", "-m", "document barrier"]
                )
                _, errors = VALIDATOR.validate_barrier_failures(
                    root, config, require_committed=True
                )
                self.assertEqual(errors, [])

                subprocess.check_call(
                    ["git", "-C", str(root), "rm", "-q", str(barrier_path)]
                )
                subprocess.check_call(
                    ["git", "-C", str(root), "commit", "-q", "-m", "delete barrier"]
                )
                _, deletion_errors = VALIDATOR.validate_barrier_failures(
                    root, config, require_committed=True
                )
                self.assertTrue(
                    any("deleted from HEAD" in failure for failure in deletion_errors)
                )

                marker = (
                    root
                    / VALIDATOR.ATTEMPT_LEDGER_ROOT
                    / f"{VALIDATOR.R2_AUDIT_IDS[0]}.json"
                )
                marker.parent.mkdir(parents=True)
                marker.write_text("{}\n", encoding="utf-8")
                subprocess.check_call(
                    [
                        "git",
                        "-C",
                        str(root),
                        "add",
                        str(
                            VALIDATOR.ATTEMPT_LEDGER_ROOT
                            / f"{VALIDATOR.R2_AUDIT_IDS[0]}.json"
                        ),
                    ]
                )
                subprocess.check_call(
                    ["git", "-C", str(root), "commit", "-q", "-m", "illegal retry"]
                )
                _, errors = VALIDATOR.validate_barrier_failures(
                    root, config, require_committed=True
                )
            self.assertTrue(any("post-terminal" in failure for failure in errors))
            self.assertTrue(any("attempt marker followed" in failure for failure in errors))

    def test_deleted_supervisor_completion_remains_terminal_in_git_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = root / VALIDATOR.CONFIG_PATH
            manifest_path = root / VALIDATOR.MANIFEST_PATH
            completion_path = root / VALIDATOR.SUPERVISOR_COMPLETION_PATH
            for path, contents in (
                (config_path, "{}\n"),
                (manifest_path, "header\n"),
                (completion_path, "{}\n"),
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(contents, encoding="utf-8")
            subprocess.check_call(["git", "init", "-q", str(root)])
            subprocess.check_call(
                ["git", "-C", str(root), "config", "user.email", "r2@test.invalid"]
            )
            subprocess.check_call(
                ["git", "-C", str(root), "config", "user.name", "R2 Test"]
            )
            subprocess.check_call(["git", "-C", str(root), "add", "."])
            subprocess.check_call(
                ["git", "-C", str(root), "commit", "-q", "-m", "completion"]
            )
            subprocess.check_call(
                ["git", "-C", str(root), "rm", "-q", str(completion_path)]
            )
            subprocess.check_call(
                ["git", "-C", str(root), "commit", "-q", "-m", "delete completion"]
            )

            with (
                mock.patch.object(VALIDATOR, "_read_manifest", return_value=[]),
                mock.patch.object(VALIDATOR, "_validate_manifest_rows"),
                mock.patch.object(VALIDATOR, "_validate_config_contract"),
                mock.patch.object(VALIDATOR, "_validate_implementation"),
                mock.patch.object(VALIDATOR, "_validate_r1_bridge", return_value={}),
                mock.patch.object(VALIDATOR, "_tracked_head_failure", return_value=None),
                mock.patch.object(
                    VALIDATOR, "_validate_preregistration", return_value="a" * 40
                ),
                mock.patch.object(VALIDATOR, "_validate_execution_history"),
                mock.patch.object(
                    VALIDATOR, "validate_barrier_failures", return_value=([], [])
                ),
                mock.patch.object(
                    VALIDATOR,
                    "evaluate_imported_p0_gate",
                    return_value={"accepted": True},
                ),
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "historical supervisor completion was deleted from HEAD",
                ):
                    VALIDATOR.validate_registration(
                        root,
                        config_path,
                        manifest_path,
                        require_committed=True,
                    )

    def test_namespace_is_exact_ten_reused_plus_thirty_new(self) -> None:
        self.assertEqual(
            VALIDATOR.R1_REUSED_AUDIT_IDS,
            VALIDATOR.EXPECTED_REUSED_LOGICAL_IDS,
        )
        self.assertEqual(VALIDATOR.NEW_TO_LOGICAL, VALIDATOR.EXPECTED_NEW_TO_LOGICAL)
        self.assertEqual(len(VALIDATOR.R1_REUSED_AUDIT_IDS), 10)
        self.assertEqual(len(VALIDATOR.R2_AUDIT_IDS), 30)
        self.assertEqual(set(VALIDATOR.LOGICAL_TO_EFFECTIVE_ID), set(R1.RUN_IDS))
        self.assertEqual(len(set(VALIDATOR.LOGICAL_TO_EFFECTIVE_ID.values())), 40)
        self.assertEqual(VALIDATOR.EXECUTION_ORDER, VALIDATOR.R2_AUDIT_IDS)

    def test_barrier_spec_reconstructs_per_run_and_analysis_release_commands(self) -> None:
        root = Path("/frozen/project")
        python_path = "/registered/python3"
        config = {"runtime": {"tools": {"python": {"path": python_path}}}}
        experiment_id = "S1-20260806-041"
        logical_id = VALIDATOR.NEW_TO_LOGICAL[experiment_id]
        prefix = [
            python_path,
            "-s",
            str(root / "scripts/validate_s1_g1_thermodynamic_label_audit_r2.py"),
            str(root / VALIDATOR.MANIFEST_PATH),
            "--config",
            str(root / VALIDATOR.CONFIG_PATH),
            "--scientific-config",
            "/proc/self/fd/202",
            "--scientific-manifest",
            "/proc/self/fd/201",
            "--require-committed",
        ]
        for barrier_name, expected_identity, suffix in (
            ("imported-p0-before-041", (None, None), ["--require-pilot-gate"]),
            (
                "k-gate-after-042",
                ("S1-20260806-042", "S1-20260806-040"),
                ["--require-k-gate"],
            ),
            (
                "final-all-after-070",
                ("S1-20260806-070", "S1-20260806-033"),
                ["--require-all-runs"],
            ),
        ):
            effective, logical, command = VALIDATOR._barrier_spec(
                root, config, barrier_name
            )
            self.assertEqual((effective, logical), expected_identity)
            self.assertEqual(command, [*prefix, *suffix])
        for stem, option in (
            ("attempt-marker", "--check-attempt-marker"),
            ("accepted-run", "--check-run"),
            ("failure-archive", "--check-failure-archives"),
        ):
            effective, logical, command = VALIDATOR._barrier_spec(
                root, config, f"{stem}-041"
            )
            self.assertEqual((effective, logical), (experiment_id, logical_id))
            self.assertEqual(command, [*prefix, option, experiment_id])

        half_effective = VALIDATOR.effective_id("S1-20260806-007")
        effective, logical, command = VALIDATOR._barrier_spec(
            root,
            config,
            f"half-quarter-021-after-{half_effective.rsplit('-', 1)[1]}",
        )
        self.assertEqual((effective, logical), (half_effective, "S1-20260806-007"))
        self.assertEqual(
            command,
            [*prefix, "--require-half-quarter-pair", "S1-20260806-021"],
        )

        for stem, logical_id, arguments in (
            ("eos-al-standard-half", "S1-20260806-013", ["al", "standard", "half"]),
            ("eos-mg-standard-half", "S1-20260806-020", ["mg", "standard", "half"]),
            ("eos-al-half-quarter", "S1-20260806-026", ["al", "half", "quarter"]),
            ("eos-mg-half-quarter", "S1-20260806-033", ["mg", "half", "quarter"]),
        ):
            expected_effective = VALIDATOR.effective_id(logical_id)
            barrier_name = (
                f"{stem}-after-{expected_effective.rsplit('-', 1)[1]}"
            )
            effective, logical, command = VALIDATOR._barrier_spec(
                root, config, barrier_name
            )
            self.assertEqual((effective, logical), (expected_effective, logical_id))
            self.assertEqual(
                command, [*prefix, "--require-adjacent-eos", *arguments]
            )

        effective, logical, command = VALIDATOR._barrier_spec(
            root, config, "final-analysis"
        )
        self.assertEqual(
            (effective, logical),
            (VALIDATOR.R2_AUDIT_IDS[-1], VALIDATOR.NEW_TO_LOGICAL[VALIDATOR.R2_AUDIT_IDS[-1]]),
        )
        self.assertEqual(
            command,
            [
                python_path,
                "-s",
                str(root / "scripts/analyze_s1_g1_thermodynamic_label_audit_r2.py"),
                str(root / VALIDATOR.ANALYSIS_ROOT),
                "--config",
                str(root / VALIDATOR.CONFIG_PATH),
                "--manifest",
                str(root / VALIDATOR.MANIFEST_PATH),
                "--scientific-config",
                "/proc/self/fd/202",
                "--scientific-manifest",
                "/proc/self/fd/201",
            ],
        )
        _, _, status_command = VALIDATOR._barrier_spec(
            root, config, "final-analysis-status"
        )
        self.assertEqual(status_command, [*prefix, "--check-analysis-summary"])

    def test_fixed_resolver_does_not_use_new_id_arithmetic(self) -> None:
        self.assertEqual(
            VALIDATOR.effective_id("S1-20260806-034"), "S1-20260806-041"
        )
        self.assertEqual(
            VALIDATOR.effective_id("S1-20260806-040"), "S1-20260806-042"
        )
        self.assertEqual(
            VALIDATOR.effective_id("S1-20260806-001"), "S1-20260806-043"
        )
        self.assertEqual(
            VALIDATOR.logical_id("S1-20260806-063"), "S1-20260806-022"
        )
        self.assertEqual(
            VALIDATOR.logical_id("S1-20260806-024"), "S1-20260806-024"
        )
        with self.assertRaisesRegex(ValueError, "unknown effective"):
            VALIDATOR.logical_id("S1-20260806-071")

    def test_config_resolver_fails_closed_on_matrix_tampering(self) -> None:
        config = logical_config()
        self.assertEqual(
            VALIDATOR.logical_effective_id(config, "S1-20260806-022"),
            "S1-20260806-063",
        )
        config["logical_run_matrix"][21]["physical_experiment_id"] = "S1-20260806-064"
        with self.assertRaisesRegex(ValueError, "mapping differs"):
            VALIDATOR.logical_effective_id(config, "S1-20260806-022")

    def test_half_quarter_gate_uses_logical_partner_table(self) -> None:
        config = logical_config()
        rows = [
            {
                "experiment_id": "S1-20260806-063",
                "reference_experiment_id": "S1-20260806-008",
                "material": "al",
                "volume_ratio": "0.94",
            }
        ]
        pair_payload = {
            "field_metrics": {
                "d1": 0.0,
                "d2": 0.0,
                "dg": 0.0,
                "rms_g_ev": 0.0,
                "accepted": True,
            }
        }
        with mock.patch.object(
            VALIDATOR, "_logical_pair_payload", return_value=pair_payload
        ) as pair:
            result = VALIDATOR.evaluate_half_quarter_pair(
                PROJECT_ROOT,
                config,
                rows,
                "S1-20260806-063",
                require_committed=False,
            )
        self.assertEqual(result["half_logical_experiment_id"], "S1-20260806-008")
        self.assertEqual(result["quarter_logical_experiment_id"], "S1-20260806-022")
        self.assertEqual(result["half_experiment_id"], "S1-20260806-050")
        self.assertEqual(result["quarter_experiment_id"], "S1-20260806-063")
        self.assertTrue(result["accepted"])
        pair.assert_called_once_with(
            PROJECT_ROOT,
            rows,
            "S1-20260806-008",
            "S1-20260806-022",
            require_committed=False,
        )

    def test_k_gate_contains_five_imported_pairs_and_one_recovery_pair(self) -> None:
        config = logical_config()
        ratios = (0.90, 1.00, 1.10, 0.90, 1.00, 1.10)

        def pair_payload(
            _root: Path,
            _rows: list[dict[str, str]],
            common: str,
            extra: str,
            *,
            require_committed: bool,
        ) -> dict[str, object]:
            del require_committed
            index = VALIDATOR.K_LOGICAL_PAIRS.index((common, extra))
            return {
                "material": "al" if index < 3 else "mg",
                "volume_ratio": ratios[index],
                "coarse_logical_experiment_id": common,
                "reference_logical_experiment_id": extra,
                "coarse_experiment_id": VALIDATOR.effective_id(common),
                "reference_experiment_id": VALIDATOR.effective_id(extra),
                "absolute_energy_difference_mev_per_atom": 1.0,
                "absolute_pressure_difference_gpa_diagnostic": 0.0,
                "field_metrics": {"accepted": True},
            }

        result = {
            "zero_temp_extrapolated_energy_ev_per_atom": 0.0,
            "pressure_gpa": 0.0,
        }
        with (
            mock.patch.object(
                VALIDATOR, "_logical_pair_payload", side_effect=pair_payload
            ),
            mock.patch.object(VALIDATOR, "_logical_row_result", return_value=result),
        ):
            gate = VALIDATOR.evaluate_k_gate(
                PROJECT_ROOT, config, [], require_committed=False
            )
        self.assertTrue(gate["accepted"])
        self.assertEqual(gate["pair_count"], 6)
        recovery = gate["pairs"][-1]
        self.assertEqual(recovery["coarse_logical_experiment_id"], "S1-20260806-034")
        self.assertEqual(recovery["reference_logical_experiment_id"], "S1-20260806-040")
        self.assertEqual(recovery["coarse_experiment_id"], "S1-20260806-041")
        self.assertEqual(recovery["reference_experiment_id"], "S1-20260806-042")

    def test_accepted_status_binds_physical_and_logical_ids(self) -> None:
        expected = {
            "schema_version": 1,
            "protocol_revision": VALIDATOR.PROTOCOL_REVISION,
            "experiment_id": "S1-20260806-063",
            "logical_experiment_id": "S1-20260806-022",
            "authoritative_for_r2": True,
            "retry_policy": "new_protocol_revision_and_new_experiment_ids_only",
            "status": "accepted",
            "workflow_exit_code": 0,
            "parser_exit_code": 0,
            "core_validator_exit_code": 0,
        }
        self.assertEqual(
            VALIDATOR._status_payload("S1-20260806-063", accepted=True), expected
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / VALIDATOR.STATUS_NAME
            path.write_text(json.dumps(expected), encoding="utf-8")
            self.assertIsNone(
                VALIDATOR._status_failure_r2(
                    path, "S1-20260806-063", accepted=True
                )
            )
            tampered = deepcopy(expected)
            tampered.pop("logical_experiment_id")
            path.write_text(json.dumps(tampered), encoding="utf-8")
            self.assertIn(
                "logical_experiment_id",
                VALIDATOR._status_failure_r2(
                    path, "S1-20260806-063", accepted=True
                ),
            )

    def test_failure_policy_is_r2_namespaced_and_restores_r1_globals(self) -> None:
        original = {
            name: getattr(R1, name)
            for name in (
                "PROTOCOL_REVISION",
                "STATUS_NAME",
                "FAILURE_CLASS_NAME",
                "FAILURE_INVENTORY_NAME",
                "_status_failure",
            )
        }
        with VALIDATOR._r2_failure_policy():
            self.assertEqual(R1.PROTOCOL_REVISION, VALIDATOR.PROTOCOL_REVISION)
            self.assertEqual(R1.STATUS_NAME, VALIDATOR.STATUS_NAME)
            self.assertIs(R1._status_failure, VALIDATOR._status_failure_r2)
        for name, value in original.items():
            self.assertIs(getattr(R1, name), value) if callable(value) else self.assertEqual(
                getattr(R1, name), value
            )

    def test_manifest_validator_freezes_science_and_translates_only_partners(self) -> None:
        old_rows = VALIDATOR._r1_rows(PROJECT_ROOT)
        old_by_id = {row["experiment_id"]: row for row in old_rows}
        rows: list[dict[str, str]] = []
        for index, physical in enumerate(VALIDATOR.R2_AUDIT_IDS, 1):
            logical = VALIDATOR.NEW_TO_LOGICAL[physical]
            old = old_by_id[logical]
            row = dict(old)
            row.update(
                {
                    "execution_index": str(index),
                    "execution_phase": "P1" if physical in VALIDATOR.R2_PILOT_IDS else "P2",
                    "experiment_id": physical,
                    "input_directory": f"{VALIDATOR.INPUT_ROOT}/{physical}",
                    "reference_experiment_id": old["reference_experiment_id"],
                    "common_quarter_partner_id": old["common_quarter_partner_id"],
                    "suffix": VALIDATOR._expected_r2_suffix(old["suffix"]),
                    "input_sha256": "0" * 64,
                    "metadata_sha256": "1" * 64,
                }
            )
            rows.append(row)
        config = {
            "registered_experiment_ids": list(VALIDATOR.R2_AUDIT_IDS),
            "execution_order": list(VALIDATOR.EXECUTION_ORDER),
        }
        errors: list[str] = []
        with mock.patch.object(VALIDATOR, "_validate_input_derivation"):
            VALIDATOR._validate_manifest_rows(
                PROJECT_ROOT, rows, config, errors
            )
        self.assertEqual(errors, [])
        rows[0]["smearing_sigma_ry"] = "0.5"
        errors = []
        with mock.patch.object(VALIDATOR, "_validate_input_derivation"):
            VALIDATOR._validate_manifest_rows(
                PROJECT_ROOT, rows, config, errors
            )
        self.assertTrue(any("smearing_sigma_ry" in failure for failure in errors))

    def test_replay_effective_dispatches_reused_and_new_without_reinterpretation(self) -> None:
        config = logical_config()
        new_row = {"experiment_id": "S1-20260806-043"}
        with mock.patch.object(
            R1, "replay_evidence", return_value=({"source": "r1"}, [])
        ) as replay_r1:
            payload, failures = VALIDATOR.replay_effective_evidence(
                PROJECT_ROOT,
                config,
                [new_row],
                "S1-20260806-024",
                require_committed=True,
                require_replay_status=True,
            )
        self.assertEqual(payload, {"source": "r1"})
        self.assertEqual(failures, [])
        replay_r1.assert_called_once()
        with mock.patch.object(
            VALIDATOR, "replay_evidence", return_value=({"source": "r2"}, [])
        ) as replay_r2:
            scientific_config = Path("/proc/self/fd/202")
            scientific_manifest = Path("/proc/self/fd/201")
            payload, failures = VALIDATOR.replay_effective_evidence(
                PROJECT_ROOT,
                config,
                [new_row],
                "S1-20260806-001",
                require_committed=False,
                require_replay_status=False,
                scientific_config_path=scientific_config,
                scientific_manifest_path=scientific_manifest,
            )
        self.assertEqual(payload, {"source": "r2"})
        self.assertEqual(failures, [])
        replay_r2.assert_called_once_with(
            PROJECT_ROOT,
            config,
            new_row,
            require_committed=False,
            require_replay_status=False,
            scientific_config_path=scientific_config,
            scientific_manifest_path=scientific_manifest,
        )

    def test_replay_passes_sealed_scientific_paths_to_label_parser(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            experiment_id = "S1-20260806-043"
            run = root / "runs" / experiment_id
            run.mkdir(parents=True)
            (run / VALIDATOR.LABEL_NAME).write_text("{}\n", encoding="utf-8")
            scientific_config = Path("/proc/self/fd/202")
            scientific_manifest = Path("/proc/self/fd/201")
            with (
                mock.patch.object(R1, "_role", return_value="standard_replay"),
                mock.patch.object(
                    VALIDATOR,
                    "validate_attempt_marker",
                    return_value=({}, "a" * 40, []),
                ),
                mock.patch.object(
                    VALIDATOR.runtime_validation,
                    "validate_replay_run",
                    return_value=[],
                ),
                mock.patch.object(
                    VALIDATOR,
                    "parse_label_run",
                    side_effect=ValueError("stop after forwarding assertion"),
                ) as parse_labels,
            ):
                _, failures = VALIDATOR.replay_evidence(
                    root,
                    {},
                    {
                        "experiment_id": experiment_id,
                        "source_experiment_id": "S1-20260806-001",
                    },
                    require_committed=False,
                    require_replay_status=False,
                    scientific_config_path=scientific_config,
                    scientific_manifest_path=scientific_manifest,
                )
            parse_labels.assert_called_once_with(
                run,
                config_path=root / VALIDATOR.CONFIG_PATH,
                manifest_path=root / VALIDATOR.MANIFEST_PATH,
                scientific_config_path=scientific_config,
                scientific_manifest_path=scientific_manifest,
            )
            self.assertTrue(
                any("stop after forwarding assertion" in failure for failure in failures)
            )

    def test_analyzer_facing_public_signatures_are_stable(self) -> None:
        self.assertEqual(
            tuple(inspect.signature(VALIDATOR.validate_registration).parameters),
            (
                "project_root",
                "config_path",
                "manifest_path",
                "require_committed",
                "skip_terminal_evidence_validation",
            ),
        )
        self.assertEqual(
            tuple(inspect.signature(VALIDATOR.replay_effective_evidence).parameters),
            (
                "project_root",
                "config",
                "rows",
                "logical_experiment_id",
                "require_committed",
                "require_replay_status",
                "scientific_config_path",
                "scientific_manifest_path",
            ),
        )
        self.assertIn("config", inspect.signature(VALIDATOR.evaluate_k_gate).parameters)
        self.assertIn(
            "config", inspect.signature(VALIDATOR.evaluate_half_quarter_pair).parameters
        )
        self.assertIn(
            "config", inspect.signature(VALIDATOR.evaluate_adjacent_eos_gate).parameters
        )
        self.assertEqual(
            tuple(inspect.signature(VALIDATOR.validate_supervisor_completion).parameters),
            ("project_root", "config", "require_committed"),
        )
        self.assertEqual(
            tuple(inspect.signature(VALIDATOR.validate_barrier_failures).parameters),
            ("project_root", "config", "require_committed"),
        )


if __name__ == "__main__":
    unittest.main()
