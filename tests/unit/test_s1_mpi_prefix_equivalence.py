from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import analyze_s1_mpi_prefix_equivalence as ANALYZER  # noqa: E402
import generate_s1_mpi_prefix_equivalence as GENERATOR  # noqa: E402
import runtime_relocation_audit_launcher as AUDIT  # noqa: E402
import runtime_relocation_namespace_launcher as NAMESPACE  # noqa: E402
import run_s1_runtime_relocation_smoke as SMOKE_RUNNER  # noqa: E402
import s1_mpi_prefix_equivalence_common as COMMON  # noqa: E402
import s1_runtime_relocation_smoke as SMOKE  # noqa: E402
import s1_runtime_relocation_elf as ELF  # noqa: E402
import validate_s1_mpi_prefix_equivalence as VALIDATOR  # noqa: E402
from parse_s1_single import parse_log  # noqa: E402


class S1MpiPrefixEquivalenceTest(unittest.TestCase):
    def test_fixed_mapping_is_exact_and_official_freeze_is_not_fabricated(self) -> None:
        self.assertEqual(
            [(replay[-3:], reference[-3:]) for replay, reference, _, _ in COMMON.FIXED_PAIRS],
            [("113", "074"), ("114", "081"), ("115", "088"), ("116", "095"), ("117", "102"), ("118", "109")],
        )
        config_exists = (PROJECT_ROOT / COMMON.CANONICAL_CONFIG_PATH).exists()
        manifest_exists = (PROJECT_ROOT / COMMON.CANONICAL_MANIFEST_PATH).exists()
        self.assertEqual(config_exists, manifest_exists)
        if manifest_exists:
            self.assertEqual(
                [row["replay_experiment_id"] for row in COMMON.read_tsv(
                    PROJECT_ROOT / COMMON.CANONICAL_MANIFEST_PATH
                )],
                [pair[0] for pair in COMMON.FIXED_PAIRS],
            )

    def test_raw_observable_tiers_and_strict_boundaries(self) -> None:
        exact = COMMON.raw_observables(
            "!FINAL_ETOT_IS -1.00000 eV\n#TOTAL-PRESSURE# x: 1.0000 kbar\n",
            "ofdft",
            1,
        )
        self.assertEqual(COMMON.equivalence_tier(exact, exact)["tier"], "storage_exact")

        resolution = COMMON.raw_observables(
            "!FINAL_ETOT_IS -1.00001 eV\n#TOTAL-PRESSURE# x: 1.0001 kbar\n",
            "ofdft",
            1,
        )
        self.assertEqual(
            COMMON.equivalence_tier(exact, resolution)["tier"],
            "storage_resolution_equal",
        )

        scientific_only = COMMON.raw_observables(
            "!FINAL_ETOT_IS -1.00005 eV\n#TOTAL-PRESSURE# x: 1.0100 kbar\n",
            "ofdft",
            1,
        )
        tier = COMMON.equivalence_tier(exact, scientific_only)
        self.assertEqual(tier["tier"], "scientific_tolerance_only")
        self.assertTrue(tier["scientific_tolerance_passed"])

        boundary_reference = COMMON.raw_observables(
            "!FINAL_ETOT_IS -1.0000 eV\n#TOTAL-PRESSURE# x: 1.000 kbar\n",
            "ofdft",
            1,
        )
        boundary_replay = COMMON.raw_observables(
            "!FINAL_ETOT_IS -1.0001 eV\n#TOTAL-PRESSURE# x: 1.000 kbar\n",
            "ofdft",
            1,
        )
        boundary = COMMON.equivalence_tier(boundary_reference, boundary_replay)
        self.assertEqual(boundary["delta_energy_mev_per_atom"], COMMON.Decimal("0.1"))
        self.assertFalse(boundary["scientific_tolerance_passed"])
        self.assertEqual(boundary["tier"], "not_equivalent")

        ks = COMMON.raw_observables(
            "E_KS(sigma->0) -2.0 -1.234500\n#TOTAL-PRESSURE# x: -2.0 kbar\n",
            "ksdft",
            1,
        )
        self.assertEqual(ks["energy_token"], "-1.234500")

    def test_strace_requires_exact_22_role_rank_probe_matrix(self) -> None:
        old = Path("/old/prefix")
        records = [
            {
                "pid": 100,
                "role": "launcher",
                "rank": None,
                "line": 'stat("/old/prefix/classid", 0x1) = -1 ENOENT (No such file)',
            },
            {
                "pid": 100,
                "role": "launcher",
                "rank": None,
                "line": 'openat(AT_FDCWD, "/old/prefix/classid", O_RDONLY|O_CLOEXEC) = -1 ENOENT (No such file)',
            },
        ]
        for rank in range(4):
            records.extend(
                {
                    "pid": 200 + rank,
                    "role": "rank",
                    "rank": rank,
                    "line": line,
                }
                for line in (
                    'stat("/old/prefix/classid", 0x1) = -1 ENOENT (No such file)',
                    'openat(AT_FDCWD, "/old/prefix/classid", O_RDONLY|O_CLOEXEC) = -1 ENOENT (No such file)',
                    'openat(AT_FDCWD, "/old/prefix/ucx.conf", O_RDONLY) = -1 ENOENT (No such file)',
                    'openat(AT_FDCWD, "/old/prefix", O_RDONLY) = -1 ENOENT (No such file)',
                    'openat(AT_FDCWD, "/old/prefix", O_RDONLY|O_NONBLOCK|O_CLOEXEC|O_DIRECTORY) = -1 ENOENT (No such file)',
                )
            )
        policies = COMMON.registered_old_prefix_failed_probes(old)
        payload = AUDIT.parse_strace_records(records, old, policies, 4)
        self.assertEqual(payload["old_prefix_access_attempt_count"], 22)
        self.assertEqual(payload["registered_old_prefix_failed_probe_count"], 22)
        self.assertEqual(payload["old_prefix_successful_access_count"], 0)
        self.assertEqual(payload["old_prefix_exec_success_count"], 0)
        self.assertEqual(payload["unknown_old_prefix_failed_probe_count"], 0)
        self.assertEqual(payload["registered_probe_count_mismatch_count"], 0)

        rejected = AUDIT.parse_strace_records(
            records
            + [
                {
                    "pid": 200,
                    "role": "rank",
                    "rank": 0,
                    "line": 'openat(AT_FDCWD, "/old/prefix/lib/libmpi.so", O_RDONLY) = 3',
                },
                {
                    "pid": 200,
                    "role": "rank",
                    "rank": 0,
                    "line": 'stat("/old/prefix/other", 0x1) = -1 ENOENT (No such file)',
                },
                {
                    "pid": 200,
                    "role": "rank",
                    "rank": 0,
                    "line": 'execve("/old/prefix/bin/prterun", ["prterun"], 0x1) = 0',
                },
            ],
            old,
            policies,
            4,
        )
        self.assertEqual(rejected["old_prefix_successful_access_count"], 2)
        self.assertEqual(rejected["old_prefix_exec_success_count"], 1)
        self.assertEqual(rejected["unknown_old_prefix_failed_probe_count"], 1)
        self.assertEqual(
            AUDIT.parse_execve_records(
                [
                    {
                        "pid": 1,
                        "role": "support",
                        "rank": None,
                        "line": 'execve("/recovery/bin/mpirun", ["mpirun"], 0x1) = 0',
                    },
                ]
            ),
            [
                {
                    "pid": 1,
                    "role": "support",
                    "rank": None,
                    "path": "/recovery/bin/mpirun",
                    "result": "0",
                    "errno": None,
                    "successful": True,
                }
            ],
        )

    def test_transient_mpi_maps_are_narrowly_classified(self) -> None:
        for value in (
            "/SYSV00000000",
            "/dev/shm/sm_segment.123",
            "/dev/shm/ucx_shm_posix_abc-123",
            "/tmp/ompi.1234/1/pmix-gds-shmem2-jobdata/session-1",
            "/tmp/ompi.2555634/1/pmix-gds-shmem2.node01-prterun-node01-2555634@1.jobdata.2555634",
            "/tmp/ompi.2555634/1/pmix-gds-shmem2.node01-prterun-node01-2555634@1.session.2555634",
            "/tmp/ompi.1234/1/rank.0/shared_mem_cuda_pool",
            "/tmp/ompi.1234/hwloc.sm",
        ):
            path = Path(value)
            self.assertEqual(
                AUDIT.classify_mapping(
                    path,
                    path,
                    Path("/old/prefix"),
                    Path("/recovery"),
                ),
                "transient_system",
                value,
            )
        for value in (
            "/tmp/arbitrary/libmpi.so",
            "/dev/random",
            "/proc/self/maps",
            "/sys/kernel/notes",
            "/etc/passwd",
        ):
            arbitrary = Path(value)
            self.assertEqual(
                AUDIT.classify_mapping(
                    arbitrary,
                    arbitrary,
                    Path("/old/prefix"),
                    Path("/recovery"),
                ),
                "unexpected",
                value,
            )
        self.assertEqual(
            AUDIT.classify_mapping(
                Path("/etc/ld.so.cache"),
                Path("/etc/ld.so.cache"),
                Path("/old/prefix"),
                Path("/recovery"),
            ),
            "system",
        )
        self.assertEqual(
            AUDIT.classify_mapping(
                Path("/dev/infiniband/uverbs0"),
                Path("/dev/infiniband/uverbs0"),
                Path("/old/prefix"),
                Path("/recovery"),
            ),
            "registered_device",
        )

    def test_recovery_mapped_components_require_old_counterpart_byte_equality(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recovery = root / "recovery"
            old = root / "old"
            recovery_library = recovery / "lib/libcomponent.so"
            old_library = old / "lib/libcomponent.so"
            recovery_library.parent.mkdir(parents=True)
            old_library.parent.mkdir(parents=True)
            recovery_library.write_bytes(b"identical component\n")
            old_library.write_bytes(recovery_library.read_bytes())
            identities = {}
            for key, relative, content in (
                ("REPLAY_ABACUS", "recovery/bin/abacus", b"relocated"),
                ("REFERENCE_ABACUS", "old/bin/abacus", b"original"),
                ("REPLAY_MPIRUN", "recovery/bin/mpirun", b"mpirun"),
                ("REFERENCE_MPIRUN", "old/bin/mpirun", b"mpirun"),
                ("REPLAY_LAUNCHER", "recovery/bin/prterun", b"launcher"),
                ("REFERENCE_LAUNCHER", "old/bin/prterun", b"launcher"),
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                path.chmod(0o755)
                identities[key] = path

            def audit_directory(name: str) -> Path:
                directory = root / name
                directory.mkdir()
                digest = hashlib.sha256(recovery_library.read_bytes()).hexdigest()
                (directory / "objects.tsv").write_text(
                    "\t".join(VALIDATOR.OBJECT_HEADER)
                    + "\n"
                    + "\t".join(
                        (
                            "1",
                            "rank",
                            "0",
                            str(recovery_library),
                            str(recovery_library.resolve()),
                            digest,
                            "recovery_runtime",
                        )
                    )
                    + "\n"
                )
                return directory

            environment = {
                "M_OFDFT_RECOVERY_ROOT": str(recovery),
                "M_OFDFT_OLD_ROOT": str(old),
            }
            for key, path in identities.items():
                environment[f"M_OFDFT_{key}_PATH"] = str(path)
                environment[f"M_OFDFT_{key}_REALPATH"] = str(path.resolve())
                environment[f"M_OFDFT_{key}_SHA256"] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
            with mock.patch.dict(os.environ, environment, clear=False):
                accepted = NAMESPACE._verify_recovery_counterparts(
                    audit_directory("accepted-audit"), time.monotonic() + 10
                )
                self.assertEqual(accepted["status"], "accepted")
                old_library.write_bytes(b"different component\n")
                rejected = NAMESPACE._verify_recovery_counterparts(
                    audit_directory("rejected-audit"), time.monotonic() + 10
                )
                self.assertEqual(rejected["status"], "rejected")
                self.assertEqual(rejected["counterpart_byte_mismatch_count"], 1)

    def test_descendant_scan_is_confined_to_audit_tree_and_all_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            proc = Path(temporary)
            children_by_task = {
                (100, 100): "101 102\n",
                (100, 105): "103\n",
                (101, 101): "104\n",
                (102, 102): "\n",
                (103, 103): "\n",
                (104, 104): "\n",
                (999, 999): "1000\n",
            }
            for (pid, task), children in children_by_task.items():
                task_directory = proc / str(pid) / "task" / str(task)
                task_directory.mkdir(parents=True)
                (task_directory / "children").write_text(children, encoding="ascii")

            self.assertEqual(
                AUDIT._descendants(100, proc),
                {100, 101, 102, 103, 104},
            )
            self.assertNotIn(999, AUDIT._descendants(100, proc))

    def test_deadline_and_terminal_pid_evidence_are_fail_closed(self) -> None:
        timing = {
            "started_at_utc": "2026-08-05T00:00:00+00:00",
            "ended_at_utc": "2026-08-05T02:00:01+00:00",
            "started_epoch_seconds": 0.0,
            "ended_epoch_seconds": 7201.0,
            "elapsed_seconds": 7201.0,
        }
        self.assertTrue(
            any(
                "exceeds absolute deadline" in failure
                for failure in VALIDATOR._timing_evidence_failures(
                    timing, 7200, "runtime audit"
                )
            )
        )

        known = {
            123: {
                "pid": 123,
                "sources": {"strace_trace_file", "rank_handshake"},
                "observed_start_time_ticks": 9,
            }
        }
        with mock.patch.object(AUDIT, "_proc_start_time_ticks", return_value=None), mock.patch.object(
            AUDIT, "_process_group_members", return_value=[]
        ):
            gone = AUDIT._prove_known_pids_gone(known, time.monotonic() + 1, 100)
        self.assertTrue(gone["all_known_pids_gone"])
        self.assertEqual(gone["known_pids"][0]["terminal_state"], "gone")

        known[123]["observed_start_time_ticks"] = None
        with mock.patch.object(AUDIT, "_proc_start_time_ticks", return_value=9), mock.patch.object(
            AUDIT, "_process_group_members", return_value=[]
        ):
            unproven = AUDIT._prove_known_pids_gone(known, time.monotonic(), 100)
        self.assertFalse(unproven["all_known_pids_gone"])

        with mock.patch.object(NAMESPACE, "_pid_namespace_members", return_value=([], [])):
            namespace_gone = NAMESPACE._prove_pid_namespace_empty(
                456, time.monotonic() + 1
            )
        self.assertTrue(namespace_gone["all_namespace_members_gone"])
        with mock.patch.object(
            NAMESPACE,
            "_pid_namespace_members",
            return_value=([], ["cannot_stat_pid_namespace:7"]),
        ):
            namespace_unknown = NAMESPACE._prove_pid_namespace_empty(
                456, time.monotonic() + 1
            )
        self.assertFalse(namespace_unknown["all_namespace_members_gone"])

    def test_version_probe_timeout_is_part_of_absolute_deadline(self) -> None:
        identity = {
            "path": "/synthetic/tool",
            "realpath": "/synthetic/tool",
            "sha256": "0" * 64,
        }
        with mock.patch.object(ELF, "file_identity", return_value=identity), mock.patch.object(
            ELF.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(["/synthetic/tool", "--version"], 1),
        ), self.assertRaisesRegex(TimeoutError, "tool --version"):
            ELF.versioned_tool_identity(
                Path("/synthetic/tool"), "tool", deadline=time.monotonic() + 1
            )

    def test_rank_wrapper_waits_for_explicit_release_before_exec(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            handshake = Path(temporary) / "handshake"
            (handshake / "release").mkdir(parents=True)
            (handshake / "release/rank-0").write_text("release\n")
            target = Path("/usr/bin/true").resolve()
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "runtime_relocation_rank_wrapper.py"),
                    str(target),
                ],
                env={
                    **os.environ,
                    "OMPI_COMM_WORLD_RANK": "0",
                    "PMIX_RANK": "0",
                    "M_OFDFT_RANK_HANDSHAKE_DIR": str(handshake),
                    "M_OFDFT_MPI_AUDIT_EXPECTED_RANKS": "4",
                    "M_OFDFT_EXPECTED_ABACUS": str(target),
                    "OPAL_PREFIX": "/recovery",
                    "PRTE_PREFIX": "/recovery",
                    "PMIX_PREFIX": "/recovery",
                    "UCX_MODULE_DIR": "/recovery",
                },
                check=False,
                text=True,
                capture_output=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            ready = json.loads((handshake / "ready/rank-0.json").read_text())
            self.assertEqual(ready["wrapper_state"], "ready_before_exec")
            self.assertEqual(ready["rank"], 0)
            self.assertEqual(ready["target_abacus_realpath"], str(target))

    def test_elf_gate_allows_only_registered_runpath_slot_bytes(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp", prefix="elf-") as temporary:
            root = Path(temporary)
            # A 60-byte old RUNPATH whose bytes are deliberately disjoint from
            # "$ORIGIN/../conda_prefix/lib" makes the frozen 60-byte diff exact
            # on every host, independent of the temporary-directory spelling.
            old_prefix = Path("/" + "Z" * 55)
            resolved_old_prefix = old_prefix.resolve(strict=False)
            self.assertEqual(len(str(resolved_old_prefix / "lib")), 60)
            reference = root / "reference-abacus"
            replay = root / "replay-abacus"
            original = bytearray(ELF.EXPECTED_FILE_SIZE)
            relocated = bytearray(original)
            dynstr_end = ELF.EXPECTED_DYNSTR_OFFSET + ELF.EXPECTED_DYNSTR_SIZE
            old_value = (str(resolved_old_prefix / "lib")).encode() + b"\0"
            new_value = ELF.EXPECTED_RELOCATED_RUNPATH.encode() + b"\0"
            path_start = dynstr_end - len(old_value)
            original[path_start:dynstr_end] = old_value
            relocated[path_start : path_start + len(new_value)] = new_value
            reference.write_bytes(original)
            replay.write_bytes(relocated)
            reference.chmod(0o755)
            replay.chmod(0o755)

            identities = {
                str(reference): {
                    "path": str(reference),
                    "realpath": str(reference),
                    "sha256": ELF.REFERENCE_ABACUS_SHA256,
                },
                str(replay): {
                    "path": str(replay),
                    "realpath": str(replay),
                    "sha256": ELF.RELOCATED_ABACUS_SHA256,
                },
            }
            common_elf = {
                "file_size": ELF.EXPECTED_FILE_SIZE,
                "build_id": ELF.EXPECTED_BUILD_ID,
                "needed": ["libmpi.so.40", "libc.so.6"],
                "rpath": [],
                "load_segments": [{"offset": "0x0", "flags": "R"}],
                "dynstr": {
                    "file_offset": ELF.EXPECTED_DYNSTR_OFFSET,
                    "size": ELF.EXPECTED_DYNSTR_SIZE,
                },
                "readelf_output_sha256": {name: "0" * 64 for name in ELF.READELF_COMMANDS},
                "normalized_dynamic_sha256": "1" * 64,
            }

            def fake_identity(path: Path, _label: str, *, require_elf: bool = False) -> dict:
                return identities[str(path)]

            def fake_readelf(binary: dict, _tool: dict) -> tuple[dict, dict[str, bytes]]:
                payload = dict(common_elf)
                payload["runpath"] = [
                    str(resolved_old_prefix / "lib")
                    if binary["path"] == str(reference)
                    else ELF.EXPECTED_RELOCATED_RUNPATH
                ]
                outputs = {name: b"same\n" for name in ELF.READELF_COMMANDS}
                return payload, outputs

            with mock.patch.object(ELF, "file_identity", side_effect=fake_identity), mock.patch.object(
                ELF,
                "versioned_tool_identity",
                return_value={"path": "/tool", "realpath": "/tool", "sha256": "2" * 64},
            ), mock.patch.object(ELF, "readelf_evidence", side_effect=fake_readelf):
                evidence = ELF.relocation_equivalence_evidence(
                    reference,
                    replay,
                    old_prefix,
                    Path("/usr/bin/readelf"),
                    Path("/usr/bin/chrpath"),
                )
                self.assertEqual(
                    evidence["comparison"]["byte_difference_count"],
                    ELF.EXPECTED_DIFFERENCE_COUNT,
                )
                self.assertTrue(
                    evidence["comparison"]["outside_runpath_slot_byte_identical"]
                )
                with replay.open("r+b") as handle:
                    handle.seek(100)
                    handle.write(b"X")
                with self.assertRaisesRegex(ValueError, "outside the RUNPATH"):
                    ELF.relocation_equivalence_evidence(
                        reference,
                        replay,
                        old_prefix,
                        Path("/usr/bin/readelf"),
                        Path("/usr/bin/chrpath"),
                    )

    def test_runtime_access_summary_cannot_hide_raw_trace_tampering(self) -> None:
        old = Path("/old/prefix")
        policies = COMMON.registered_old_prefix_failed_probes(old)
        clean_records = [
            {
                "pid": 100,
                "role": "launcher",
                "rank": None,
                "line": 'stat("/old/prefix/classid", 0x1) = -1 ENOENT (No such file)',
            }
        ]
        frozen_summary = AUDIT.parse_strace_records(clean_records, old, policies, 4)
        tampered_records = clean_records + [
            {
                "pid": 200,
                "role": "rank",
                "rank": 0,
                "line": 'openat(AT_FDCWD, "/old/prefix/libmpi.so", O_RDONLY) = 3',
            }
        ]
        reparsed = AUDIT.parse_strace_records(tampered_records, old, policies, 4)
        self.assertNotEqual(reparsed, frozen_summary)
        self.assertEqual(reparsed["old_prefix_successful_access_count"], 1)

    def test_failure_status_model_accepts_parser_only_and_core_only_failures(self) -> None:
        accepted_run = {
            "schema_version": 2,
            "status": "accepted",
            "runtime_relocation_mode": True,
            "setup_completed": True,
            "failure_stage": None,
            "workflow_exit_code": 0,
            "invocation_exit_code": 0,
            "launcher_exit_code": 0,
            "parser_exit_code": 0,
            "result_json_present": True,
            "result_converged": True,
            "runtime_audit_json_present": True,
            "runtime_audit_status": "accepted",
            "namespace_host_status": "accepted",
            "counterpart_audit_status": "accepted",
        }

        def statuses(run_status: dict, core_exit: int) -> tuple[dict, dict]:
            replay = {
                "schema_version": 2,
                "status": "rejected",
                "workflow_exit_code": run_status["workflow_exit_code"],
                "invocation_exit_code": run_status["invocation_exit_code"],
                "launcher_exit_code": run_status["launcher_exit_code"],
                "parser_exit_code": run_status["parser_exit_code"],
                "core_validation_exit_code": core_exit,
                "run_status": run_status,
                "runtime_audit_status": run_status["runtime_audit_status"],
                "runtime_audit_failure_reasons": [],
                "safe_retry_policy": "archive_committed_failure_then_retry_same_registered_id",
            }
            failure = {
                "schema_version": 2,
                "status": "failed_attempt_preserved",
                "workflow_exit_code": run_status["workflow_exit_code"],
                "invocation_exit_code": run_status["invocation_exit_code"],
                "launcher_exit_code": run_status["launcher_exit_code"],
                "parser_exit_code": run_status["parser_exit_code"],
                "core_validation_exit_code": core_exit,
                "setup_completed": run_status["setup_completed"],
                "failure_stage": run_status["failure_stage"],
                "runtime_audit_failure_reasons": [],
                "retry_requires_committed_archive": True,
            }
            return replay, failure

        replay, failure = statuses(accepted_run, 1)
        self.assertEqual(
            VALIDATOR._failure_status_model_errors(accepted_run, replay, failure), []
        )

        parser_failed = dict(accepted_run)
        parser_failed.update(
            {
                "status": "rejected",
                "failure_stage": "runtime_invocation_or_parser",
                "workflow_exit_code": 7,
                "parser_exit_code": 7,
                "result_json_present": False,
                "result_converged": None,
            }
        )
        replay, failure = statuses(parser_failed, 1)
        self.assertEqual(
            VALIDATOR._failure_status_model_errors(parser_failed, replay, failure), []
        )

    def test_same_id_retry_commit_chain_uses_latest_introduction(self) -> None:
        experiment_id = "S1-20260805-113"
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            self._git(repository, "init")
            self._git(repository, "config", "user.name", "Unit Test")
            self._git(repository, "config", "user.email", "unit@example.invalid")
            (repository / "base.txt").write_text("base\n")
            self._git(repository, "add", ".")
            self._git(repository, "commit", "-m", "base")
            base = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repository, text=True
            ).strip()
            first_run = repository / "runs" / experiment_id
            first_run.mkdir(parents=True)
            (first_run / "experiment_metadata.json").write_text(
                json.dumps({"code_commit": base}) + "\n"
            )
            (first_run / "failure.json").write_text("{}\n")
            self._git(repository, "add", "runs")
            self._git(repository, "commit", "-m", "failed attempt")
            failure_commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repository, text=True
            ).strip()
            archive = (
                repository
                / "failed_runs/runtime_relocation"
                / experiment_id
                / f"attempt-{failure_commit[:12]}"
            )
            archive.parent.mkdir(parents=True)
            self._git(repository, "mv", str(first_run), str(archive))
            self._git(repository, "commit", "-m", "archive failed attempt")
            archive_commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repository, text=True
            ).strip()
            retry = repository / "runs" / experiment_id
            retry.mkdir(parents=True)
            (retry / "experiment_metadata.json").write_text(
                json.dumps({"code_commit": archive_commit}) + "\n"
            )
            (retry / "accepted.json").write_text("{}\n")
            self._git(repository, "add", "runs")
            self._git(repository, "commit", "-m", "accepted retry")
            self.assertIsNone(
                VALIDATOR._run_commit_chain_failure(
                    repository, experiment_id, archive_commit
                )
            )
            self.assertEqual(
                VALIDATOR._failed_archive_chain_failures(repository, experiment_id), []
            )

    def test_failed_archive_requires_exact_complete_git_tree(self) -> None:
        experiment_id = "S1-20260805-113"

        def make_archive(root: Path, mutation: str) -> tuple[Path, Path]:
            self._git(root, "init")
            self._git(root, "config", "user.name", "Unit Test")
            self._git(root, "config", "user.email", "unit@example.invalid")
            (root / "base.txt").write_text("base\n")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "base")
            base = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True
            ).strip()
            run = root / "runs" / experiment_id
            run.mkdir(parents=True)
            (run / "experiment_metadata.json").write_text(
                json.dumps({"code_commit": base}) + "\n"
            )
            (run / "failure.json").write_text("{}\n")
            executable = run / "nested/tool"
            executable.parent.mkdir()
            executable.write_text("tool\n")
            executable.chmod(0o755)
            self._git(root, "add", "runs")
            self._git(root, "commit", "-m", "failed attempt")
            failure_commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True
            ).strip()
            archive = (
                root
                / "failed_runs/runtime_relocation"
                / experiment_id
                / f"attempt-{failure_commit[:12]}"
            )
            archive.parent.mkdir(parents=True)
            self._git(root, "mv", str(run), str(archive))
            if mutation == "delete":
                (archive / "failure.json").unlink()
                self._git(root, "add", "-u")
            elif mutation == "add":
                (archive / "injected.txt").write_text("injected\n")
                self._git(root, "add", str(archive / "injected.txt"))
            elif mutation == "mode":
                (archive / "nested/tool").chmod(0o644)
                self._git(root, "add", str(archive / "nested/tool"))
            self._git(root, "commit", "-m", f"archive with {mutation}")
            return archive, root

        for mutation in ("delete", "add", "mode"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                repository = Path(temporary)
                make_archive(repository, mutation)
                failures = VALIDATOR._failed_archive_chain_failures(
                    repository, experiment_id
                )
                self.assertTrue(
                    any("archive tree differs from failed run" in item for item in failures),
                    failures,
                )

        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            archive, _ = make_archive(repository, "none")
            (archive / "untracked-extra.txt").write_text("untracked\n")
            failures = VALIDATOR._failed_archive_chain_failures(
                repository, experiment_id
            )
            self.assertTrue(
                any("archive worktree differs from HEAD" in item for item in failures),
                failures,
            )

    def test_failed_managed_smoke_is_archived_before_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            self._git(repository, "init")
            self._git(repository, "config", "user.name", "Unit Test")
            self._git(repository, "config", "user.email", "unit@example.invalid")
            (repository / "base.txt").write_text("base\n")
            self._git(repository, "add", ".")
            self._git(repository, "commit", "-m", "base")
            smoke_root = repository / COMMON.RUNTIME_SMOKE_ROOT
            run = smoke_root / "run"
            run.mkdir(parents=True)
            (run / "replay_status.json").write_text(
                json.dumps({"status": "rejected"}) + "\n"
            )
            (run / "failure.json").write_text("{}\n")
            self._git(repository, "add", ".")
            self._git(repository, "commit", "-m", "failed managed smoke")
            failure_commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repository, text=True
            ).strip()
            SMOKE_RUNNER._archive_existing_failed_smoke(repository, smoke_root)
            archive = (
                repository
                / "failed_runs/runtime_relocation_smoke"
                / f"attempt-{failure_commit[:12]}"
            )
            self.assertFalse(smoke_root.exists())
            self.assertTrue((archive / "run/failure.json").is_file())
            self.assertEqual(
                subprocess.check_output(
                    ["git", "status", "--porcelain"], cwd=repository, text=True
                ),
                "",
            )

    def test_smoke_evidence_manifest_covers_complete_regular_file_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = root / "run"
            (run / "nested").mkdir(parents=True)
            (run / "a.txt").write_text("a\n")
            executable = run / "nested/tool"
            executable.write_text("tool\n")
            executable.chmod(0o755)
            manifest = root / "evidence.tsv"
            rows = SMOKE.write_evidence_manifest(run, manifest)
            self.assertEqual(rows, SMOKE._evidence_rows(run))
            (run / "injected.txt").write_text("injected\n")
            self.assertNotEqual(rows, SMOKE._evidence_rows(run))
            if hasattr(os, "symlink"):
                (run / "link").symlink_to("a.txt")
                with self.assertRaisesRegex(ValueError, "regular files only"):
                    SMOKE._evidence_rows(run)

    def test_managed_smoke_status_gates_require_complete_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary) / "run"
            namespace = run / "mpi_runtime_audit" / "namespace"
            namespace.mkdir(parents=True)
            run_status = {
                "status": "accepted",
                "setup_completed": True,
                "failure_stage": None,
            }
            payloads = {
                run / "run_status.json": run_status,
                run / "replay_status.json": {
                    "status": "accepted",
                    "core_validation_exit_code": 0,
                    "run_status": run_status,
                },
                run / "mpi_runtime_audit" / "audit.json": {"status": "accepted"},
                namespace / "host_status.json": {"status": "accepted"},
                run / "mpi_runtime_audit" / "counterpart_audit.json": {
                    "status": "accepted"
                },
            }
            for path, payload in payloads.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload), encoding="utf-8")

            gates = SMOKE._status_gates(run)
            self.assertEqual(
                set(gates),
                {
                    "run_status",
                    "replay_status",
                    "runtime_audit",
                    "namespace_host",
                    "counterpart_audit",
                },
            )
            (run / "failure.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "status gates"):
                SMOKE._status_gates(run)

    def test_smoke_implementation_closure_is_bound_to_execution_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            script = repository / "scripts/entry.py"
            script.parent.mkdir(parents=True)
            script.write_text("print('frozen')\n")
            self._git(repository, "init")
            self._git(repository, "config", "user.name", "Unit Test")
            self._git(repository, "config", "user.email", "unit@example.invalid")
            self._git(repository, "add", ".")
            self._git(repository, "commit", "-m", "freeze smoke implementation")
            commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repository, text=True
            ).strip()
            with mock.patch.object(
                SMOKE, "SMOKE_IMPLEMENTATION_PATHS", ("scripts/entry.py",)
            ):
                closure = SMOKE._implementation_closure(repository, commit)
                self.assertEqual(closure[0]["path"], "scripts/entry.py")
                script.write_text("print('tampered')\n")
                with self.assertRaisesRegex(ValueError, "differs from code_commit"):
                    SMOKE._implementation_closure(repository, commit)

    def test_generator_and_validator_freeze_complete_synthetic_references(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            fixture = self._make_repository(repository)
            smoke_payload = self._smoke_validation_payload(fixture)
            config_path = repository / COMMON.CANONICAL_CONFIG_PATH
            manifest_path = repository / COMMON.CANONICAL_MANIFEST_PATH
            with mock.patch.object(
                GENERATOR,
                "validate_r8_manifest",
                return_value=self._r8_validation_payload(fixture),
            ), mock.patch.object(
                GENERATOR,
                "validate_r8_summary_provenance",
                return_value=self._r8_algorithm_payload(repository),
            ), mock.patch.object(
                GENERATOR,
                "versioned_tool_identity",
                side_effect=self._fake_tool_identity,
            ), mock.patch.object(
                GENERATOR,
                "relocation_equivalence_evidence",
                side_effect=self._fake_elf_evidence,
            ), mock.patch.object(
                GENERATOR,
                "validate_smoke",
                return_value=dict(smoke_payload),
            ), mock.patch.object(
                VALIDATOR,
                "versioned_tool_identity",
                side_effect=self._fake_tool_identity,
            ), mock.patch.object(
                VALIDATOR,
                "relocation_equivalence_evidence",
                side_effect=self._fake_elf_evidence,
            ):
                payload = GENERATOR.generate(
                    repository,
                    fixture["recovery_prefix"],
                    repository / "retired/conda_prefix",
                    fixture["abacus"],
                    fixture["mpirun"],
                    config_path,
                    manifest_path,
                    fixture["r8_config"],
                    fixture["r8_manifest"],
                    fixture["r8_summary"],
                    reference_mpirun=fixture["reference_mpirun"],
                    smoke_summary_path=fixture["r8_summary"],
                )
            self.assertEqual(payload["experiment_count"], 6)
            frozen_config = json.loads(config_path.read_text())
            replay_runtime = frozen_config["runtime"]["replay"]
            reference_runtime = frozen_config["runtime"]["reference"]
            self.assertEqual(Path(replay_runtime["mpirun"]["path"]).name, "mpirun")
            self.assertEqual(
                Path(replay_runtime["launcher"]["path"]).name, "prterun"
            )
            self.assertNotEqual(
                replay_runtime["mpirun"]["path"],
                replay_runtime["launcher"]["path"],
            )
            self.assertNotEqual(
                reference_runtime["abacus"]["path"], replay_runtime["abacus"]["path"]
            )
            self.assertEqual(
                frozen_config["runtime"]["prefix_environment"]["UCX_MODULE_DIR"],
                str(fixture["recovery_prefix"].resolve()),
            )
            with mock.patch.object(
                VALIDATOR,
                "versioned_tool_identity",
                side_effect=self._fake_tool_identity,
            ), mock.patch.object(
                VALIDATOR,
                "relocation_equivalence_evidence",
                side_effect=self._fake_elf_evidence,
            ), mock.patch.object(
                VALIDATOR,
                "validate_smoke",
                return_value=dict(smoke_payload),
            ):
                validation = VALIDATOR.validate(repository, config_path, manifest_path)
            self.assertEqual(validation["first_experiment_id"], "S1-20260805-113")
            self._git(repository, "add", "config")
            self._git(repository, "commit", "-m", "preregister MPI replay")
            with mock.patch.object(
                VALIDATOR,
                "versioned_tool_identity",
                side_effect=self._fake_tool_identity,
            ), mock.patch.object(
                VALIDATOR,
                "relocation_equivalence_evidence",
                side_effect=self._fake_elf_evidence,
            ), mock.patch.object(
                VALIDATOR,
                "validate_smoke",
                return_value=dict(smoke_payload),
            ):
                committed_validation = VALIDATOR.validate(
                    repository,
                    config_path,
                    manifest_path,
                    require_committed=True,
                )
            self.assertIsNotNone(committed_validation["preregistration_commit"])
            rows = COMMON.read_tsv(manifest_path)
            r8_rows = COMMON.read_r8_manifest(fixture["r8_manifest"])
            for row in rows:
                self.assertEqual(
                    row["input_directory"],
                    r8_rows[row["reference_experiment_id"]]["input_directory"],
                )
            source_input = repository / rows[0]["input_directory"] / "INPUT"
            source_input.write_text(source_input.read_text() + "tamper\n")
            with mock.patch.object(
                VALIDATOR,
                "versioned_tool_identity",
                side_effect=self._fake_tool_identity,
            ), mock.patch.object(
                VALIDATOR,
                "relocation_equivalence_evidence",
                side_effect=self._fake_elf_evidence,
            ), mock.patch.object(
                VALIDATOR,
                "validate_smoke",
                return_value=dict(smoke_payload),
            ), self.assertRaisesRegex(ValueError, "INPUT: SHA-256 mismatch"):
                VALIDATOR.validate(repository, config_path, manifest_path)

    def test_generator_writes_nothing_when_a_reference_result_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            fixture = self._make_repository(repository)
            (repository / "runs/S1-20260805-074/result.json").unlink()
            config_path = repository / COMMON.CANONICAL_CONFIG_PATH
            manifest_path = repository / COMMON.CANONICAL_MANIFEST_PATH
            with mock.patch.object(
                GENERATOR,
                "validate_r8_manifest",
                return_value=self._r8_validation_payload(fixture),
            ), mock.patch.object(
                GENERATOR,
                "validate_r8_summary_provenance",
                return_value=self._r8_algorithm_payload(repository),
            ), mock.patch.object(
                GENERATOR,
                "versioned_tool_identity",
                side_effect=self._fake_tool_identity,
            ), self.assertRaisesRegex(ValueError, "reference result is incomplete"):
                GENERATOR.generate(
                    repository,
                    fixture["recovery_prefix"],
                    repository / "retired/conda_prefix",
                    fixture["abacus"],
                    fixture["mpirun"],
                    config_path,
                    manifest_path,
                    fixture["r8_config"],
                    fixture["r8_manifest"],
                    fixture["r8_summary"],
                    require_clean_worktree=False,
                    smoke_summary_path=fixture["r8_summary"],
                )
            self.assertFalse(config_path.exists())
            self.assertFalse(manifest_path.exists())

    def test_formal_generator_refuses_missing_managed_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            fixture = self._make_repository(repository)
            with self.assertRaisesRegex(ValueError, "requires an accepted --smoke-summary"):
                GENERATOR.generate(
                    repository,
                    fixture["recovery_prefix"],
                    repository / "retired/conda_prefix",
                    fixture["abacus"],
                    fixture["mpirun"],
                    repository / COMMON.CANONICAL_CONFIG_PATH,
                    repository / COMMON.CANONICAL_MANIFEST_PATH,
                    fixture["r8_config"],
                    fixture["r8_manifest"],
                    fixture["r8_summary"],
                )

    def test_relative_old_prefix_is_rejected_before_freezing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            fixture = self._make_repository(repository)
            config_path = repository / COMMON.CANONICAL_CONFIG_PATH
            manifest_path = repository / COMMON.CANONICAL_MANIFEST_PATH
            with self.assertRaisesRegex(ValueError, "old prefix must be an absolute path"):
                GENERATOR.generate(
                    repository,
                    fixture["recovery_prefix"],
                    Path("relative/retired-prefix"),
                    fixture["abacus"],
                    fixture["mpirun"],
                    config_path,
                    manifest_path,
                    fixture["r8_config"],
                    fixture["r8_manifest"],
                    fixture["r8_summary"],
                    smoke_summary_path=fixture["r8_summary"],
                )
            self.assertFalse(config_path.exists())
            self.assertFalse(manifest_path.exists())

    def test_replacement_requires_same_r8_conclusion(self) -> None:
        r8_config = {
            "acceptance": {
                "relative_energy_reference_volume_ratio": 1.0,
                "cutoff_max_relative_energy_difference_mev_per_atom": 1.0,
                "cutoff_max_pressure_difference_gpa": 0.02,
            },
            "materials": {
                "al": {
                    "ofdft_next_cutoff": {
                        "comparison_axis": "cutoff",
                        "baseline_series_id": "ofdft",
                    }
                }
            },
        }
        r8_summary = {
            "s1_r8_status": "accepted",
            "series": {
                "al/ofdft_next_cutoff": {
                    "status": "accepted",
                    "points": [
                        {
                            "volume_ratio": 1.0,
                            "experiment_id": "S1-20260805-074",
                            "energy_ev_per_atom": -1.0,
                            "pressure_gpa": 0.0,
                        }
                    ],
                }
            },
        }
        core = {"series": {"al/ofdft": {"points": []}}}
        with mock.patch.object(
            ANALYZER,
            "compare_series",
            return_value={"status": "accepted"},
        ), mock.patch.object(ANALYZER, "_fit_quality", return_value=({}, [])):
            result = ANALYZER.replacement_conclusion(
                r8_config,
                r8_summary,
                core,
                "al",
                "ofdft_next_cutoff",
                "S1-20260805-113",
                -1.0,
                0.0,
            )
        self.assertTrue(result["conclusion_unchanged"])
        self.assertEqual(result["modified_r8_status"], "accepted")

    def test_shell_runner_enforces_frozen_prefixes_and_independent_fd(self) -> None:
        runner_path = SCRIPTS / "run_s1_runtime_relocation_equivalence.sh"
        runner = runner_path.read_text()
        for assignment in (
            'OPAL_PREFIX="$recovery_prefix"',
            'PRTE_PREFIX="$recovery_prefix"',
            'PMIX_PREFIX="$recovery_prefix"',
            'UCX_MODULE_DIR="$recovery_prefix"',
            'M_OFDFT_STRACE_TOOL="${tool_path[strace]}"',
            "M_OFDFT_EXPECTED_LAUNCHER=",
            "env -i",
        ):
            self.assertIn(assignment, runner)
        self.assertIn('exec 9<"$manifest"', runner)
        self.assertIn('<&9', runner)
        self.assertIn('9<&- </dev/null', runner)
        self.assertIn("write_replay_status", runner)
        self.assertIn("--check-failure-run", runner)
        self.assertIn("assert_clean_and_commit_scope", runner)
        self.assertIn("archive_failed_attempt", runner)
        legacy = (SCRIPTS / "run_s1_mpi_prefix_equivalence.sh").read_text()
        self.assertIn("run_s1_runtime_relocation_equivalence.sh", legacy)
        subprocess.run(
            [
                "/bin/bash",
                "-n",
                str(SCRIPTS / "run_s1_single.sh"),
                str(SCRIPTS / "run_s1_mpi_prefix_equivalence.sh"),
                str(runner_path),
                str(SCRIPTS / "runtime_relocation_namespace_payload.sh"),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    def _make_repository(self, repository: Path) -> dict[str, Path]:
        r8_config = repository / COMMON.R8_CONFIG_PATH
        r8_manifest = repository / COMMON.R8_MANIFEST_PATH
        r8_summary = repository / COMMON.DEFAULT_R8_SUMMARY_PATH
        runtime = repository / "runtime"
        recovery_prefix = runtime / "conda_prefix"
        abacus = runtime / "source/abacus_pw_para"
        mpirun = recovery_prefix / "bin/mpirun"
        launcher = recovery_prefix / "bin/prterun"
        retired_root = repository / "retired"
        reference_abacus = retired_root / "source/abacus_pw_para"
        reference_mpirun = retired_root / "conda_prefix/bin/mpirun"
        reference_launcher = retired_root / "conda_prefix/bin/prterun"
        for path in (
            r8_config,
            r8_manifest,
            r8_summary,
            abacus,
            mpirun,
            launcher,
            reference_abacus,
            reference_mpirun,
            reference_launcher,
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
        r8_config.write_text("{}\n")
        for relative in VALIDATOR.FROZEN_IMPLEMENTATION_PATHS:
            target = repository / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((PROJECT_ROOT / relative).read_bytes())
        executable_fixture = b"\x7fELF synthetic executable fixture\n"
        abacus.write_bytes(executable_fixture)
        mpirun.write_bytes(executable_fixture)
        launcher.write_bytes(executable_fixture)
        reference_abacus.write_bytes(executable_fixture + b"reference\n")
        reference_mpirun.write_bytes(executable_fixture)
        reference_launcher.write_bytes(executable_fixture)
        for path in (
            abacus,
            mpirun,
            launcher,
            reference_abacus,
            reference_mpirun,
            reference_launcher,
        ):
            path.chmod(0o755)
        reference_abacus_digest = hashlib.sha256(reference_abacus.read_bytes()).hexdigest()
        reference_mpirun_digest = hashlib.sha256(reference_mpirun.read_bytes()).hexdigest()

        manifest_header = (
            "experiment_id\tinput_directory\tmaterial\tseries_id\tcomparison_axis\t"
            "volume_ratio\treference_experiment_id\tinput_metadata_sha256"
        )
        manifest_lines = [manifest_header]
        series_payload = {}
        pseudo_by_material: dict[str, tuple[str, str]] = {}
        for replay, reference, material, series in COMMON.FIXED_PAIRS:
            pseudo = f"{material}.psp"
            pseudo_path = repository / "assets/pseudo" / pseudo
            pseudo_path.parent.mkdir(parents=True, exist_ok=True)
            if not pseudo_path.exists():
                pseudo_path.write_text(f"pseudo {material}\n")
            pseudo_digest = hashlib.sha256(pseudo_path.read_bytes()).hexdigest()
            pseudo_by_material[material] = (pseudo, pseudo_digest)
            solver = "ofdft" if series.startswith("ofdft") else "ksdft"
            input_directory_value = f"inputs/s1/non_equilibrium_convergence/{material}/v100/{series}"
            input_directory = repository / input_directory_value
            input_directory.mkdir(parents=True)
            metadata = {
                "atom_count": 1,
                "expected_electrons": 1,
                "material": material,
                "series_id": series,
                "solver": solver,
                "pseudopotential": pseudo,
                "pseudopotential_sha256": pseudo_digest,
            }
            (input_directory / "INPUT").write_text(
                "INPUT_PARAMETERS\npseudo_dir ../../assets/pseudo\n"
            )
            (input_directory / "STRU").write_text(f"structure {material}\n")
            (input_directory / "KPT").write_text("K_POINTS\n0\nGamma\n1 1 1 0 0 0\n")
            metadata_text = json.dumps(metadata, indent=2, sort_keys=True) + "\n"
            (input_directory / "metadata.json").write_text(metadata_text)
            metadata_digest = hashlib.sha256(metadata_text.encode()).hexdigest()
            manifest_lines.append(
                "\t".join(
                    (
                        reference,
                        input_directory_value,
                        material,
                        series,
                        "cutoff" if "cutoff" in series else "kmesh",
                        "1.0",
                        "synthetic-core",
                        metadata_digest,
                    )
                )
            )

            run = repository / "runs" / reference
            output = run / f"OUT.{reference}"
            output.mkdir(parents=True)
            (run / "input_metadata.json").write_text(metadata_text)
            (run / "INPUT").write_bytes(
                COMMON.normalized_run_input((input_directory / "INPUT").read_bytes())
            )
            (run / "STRU").write_bytes((input_directory / "STRU").read_bytes())
            (run / "KPT").write_bytes((input_directory / "KPT").read_bytes())
            (run / pseudo).write_bytes(pseudo_path.read_bytes())
            if solver == "ksdft":
                log = (
                    "Autoset the number of electrons = 1\n"
                    "#SCF IS CONVERGED#\n"
                    "!FINAL_ETOT_IS -1.000000 eV\n"
                    "E_KS(sigma->0) -1.000000 -1.000000\n"
                    "E_entropy(-TS) -0.000100 -0.000100\n"
                    "#TOTAL-PRESSURE# synthetic: 1.000000 kbar\n"
                )
            else:
                log = (
                    "Autoset the number of electrons = 1\n"
                    "#SCF IS CONVERGED#\n"
                    "!FINAL_ETOT_IS -1.000000 eV\n"
                    "#TOTAL-PRESSURE# synthetic: 1.000000 kbar\n"
                )
            log_path = output / "running_scf.log"
            log_path.write_text(log)
            result = parse_log(log, 1.0, 1, solver)
            (run / "result.json").write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n"
            )
            (run / "experiment_metadata.json").write_text(
                json.dumps(
                    {
                        "abacus_path": str(reference_abacus),
                        "abacus_sha256": reference_abacus_digest,
                        "mpirun_path": str(reference_mpirun),
                        "mpirun_sha256": reference_mpirun_digest,
                        "mpi_ranks": 4,
                    },
                    indent=2,
                )
                + "\n"
            )
            checksum_names = ("INPUT", "STRU", "KPT", pseudo)
            (run / "INPUT_SHA256SUMS").write_text(
                "".join(
                    f"{hashlib.sha256((run / name).read_bytes()).hexdigest()}  {name}\n"
                    for name in checksum_names
                )
            )
            series_payload[f"{material}/{series}"] = {
                "status": "accepted",
                "points": [],
            }
        r8_manifest.write_text("\n".join(manifest_lines) + "\n")
        algorithm = self._r8_algorithm_payload(repository)
        r8_summary.write_text(
            json.dumps(
                {
                    "s1_r8_status": "accepted",
                    "expected_calculations": 42,
                    "selected_calculations": 42,
                    "accepted_comparisons": 6,
                    "analysis_provenance": {
                        "analyzer_code_commit": algorithm["analysis_commit"],
                        "analyzer_script_sha256": algorithm["r8_analyzer_sha256"],
                        "result_parser_script_sha256": algorithm["result_parser_sha256"],
                    },
                    "series": series_payload,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        self._git(repository, "init")
        self._git(repository, "config", "user.name", "Unit Test")
        self._git(repository, "config", "user.email", "unit@example.invalid")
        self._git(repository, "add", ".")
        self._git(repository, "commit", "-m", "synthetic S1-R8 references")
        return {
            "r8_config": r8_config,
            "r8_manifest": r8_manifest,
            "r8_summary": r8_summary,
            "recovery_prefix": recovery_prefix,
            "abacus": abacus,
            "mpirun": mpirun,
            "reference_abacus": reference_abacus,
            "reference_mpirun": reference_mpirun,
            "reference_launcher": reference_launcher,
        }

    @staticmethod
    def _fake_tool_identity(path: Path, _label: str, **_kwargs) -> dict:
        path = Path(path)
        token = hashlib.sha256(str(path).encode()).hexdigest()
        return {
            "path": str(path),
            "realpath": str(path),
            "sha256": token,
            "version_arguments": ["--version"],
            "version_first_line": f"synthetic {path.name} 1.0",
            "version_output_sha256": hashlib.sha256(
                f"synthetic {path.name} 1.0\n".encode()
            ).hexdigest(),
        }

    @staticmethod
    def _fake_elf_evidence(
        reference: Path,
        replay: Path,
        old_prefix: Path,
        readelf: Path,
        chrpath: Path,
    ) -> dict:
        reference = Path(reference)
        replay = Path(replay)
        return {
            "schema_version": 1,
            "reference_binary": {
                "path": str(reference),
                "realpath": str(reference.resolve()),
                "sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
            },
            "replay_binary": {
                "path": str(replay),
                "realpath": str(replay.resolve()),
                "sha256": hashlib.sha256(replay.read_bytes()).hexdigest(),
            },
            "old_prefix": str(old_prefix),
            "readelf_tool": S1MpiPrefixEquivalenceTest._fake_tool_identity(
                readelf, "readelf"
            ),
            "chrpath_tool": S1MpiPrefixEquivalenceTest._fake_tool_identity(
                chrpath, "chrpath"
            ),
            "comparison": {"outside_runpath_slot_byte_identical": True},
        }

    @staticmethod
    def _r8_validation_payload(fixture: dict[str, Path]) -> dict:
        return {
            "experiment_count": 42,
            "first_experiment_id": "S1-20260805-071",
            "last_experiment_id": "S1-20260805-112",
            "config_sha256": hashlib.sha256(fixture["r8_config"].read_bytes()).hexdigest(),
            "manifest_sha256": hashlib.sha256(
                fixture["r8_manifest"].read_bytes()
            ).hexdigest(),
            "preregistration_commit": "0" * 40,
        }

    @staticmethod
    def _smoke_validation_payload(fixture: dict[str, Path]) -> dict:
        return {
            "status": "accepted",
            "smoke_id": COMMON.RUNTIME_SMOKE_ID,
            "reference_experiment_id": COMMON.RUNTIME_SMOKE_REFERENCE_ID,
            "summary_path": fixture["r8_summary"].relative_to(
                fixture["r8_summary"].parents[3]
            ).as_posix(),
            "summary_sha256": hashlib.sha256(
                fixture["r8_summary"].read_bytes()
            ).hexdigest(),
            "run_directory": COMMON.RUNTIME_SMOKE_RUN_DIRECTORY.as_posix(),
            "evidence_manifest_path": COMMON.RUNTIME_SMOKE_EVIDENCE_MANIFEST.as_posix(),
            "evidence_manifest_sha256": "1" * 64,
            "evidence_file_count": 1,
            "code_commit": "0" * 40,
            "smoke_commit": "3" * 40,
            "runtime_registration_sha256": "2" * 64,
            "runtime_identities": {},
            "status_gates": {},
            "scientific_equivalence": {"status": "accepted"},
            "tracked_paths": [fixture["r8_summary"]],
        }

    @staticmethod
    def _r8_algorithm_payload(repository: Path) -> dict:
        return {
            "analysis_commit": "0" * 40,
            "r8_analyzer_sha256": hashlib.sha256(
                (repository / "scripts/analyze_s1_non_equilibrium.py").read_bytes()
            ).hexdigest(),
            "eos_analyzer_sha256": hashlib.sha256(
                (repository / "scripts/analyze_s1_eos.py").read_bytes()
            ).hexdigest(),
            "result_parser_sha256": hashlib.sha256(
                (repository / "scripts/parse_s1_single.py").read_bytes()
            ).hexdigest(),
        }

    @staticmethod
    def _git(repository: Path, *arguments: str) -> None:
        subprocess.run(
            ["git", *arguments],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"},
        )


if __name__ == "__main__":
    unittest.main()
