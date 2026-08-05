from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import analyze_s1_mpi_prefix_equivalence as ANALYZER  # noqa: E402
import generate_s1_mpi_prefix_equivalence as GENERATOR  # noqa: E402
import mpi_prefix_audit_launcher as AUDIT  # noqa: E402
import s1_mpi_prefix_equivalence_common as COMMON  # noqa: E402
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

    def test_strace_allows_only_two_classid_enoent_probes(self) -> None:
        old = Path("/old/prefix")
        lines = [
            'newfstatat(AT_FDCWD, "/old/prefix/classid", 0x1, 0) = -1 ENOENT (No such file)',
            'openat(AT_FDCWD, "/old/prefix/classid", O_RDONLY) = -1 ENOENT (No such file)',
        ]
        payload = AUDIT.parse_strace_lines(lines, old, old / "classid", "ENOENT")
        self.assertEqual(payload["old_prefix_access_attempt_count"], 2)
        self.assertEqual(payload["allowed_failed_probe_count"], 2)
        self.assertEqual(payload["old_prefix_successful_access_count"], 0)
        self.assertEqual(payload["other_old_prefix_attempt_count"], 0)

        rejected = AUDIT.parse_strace_lines(
            lines
            + [
                'openat(AT_FDCWD, "/old/prefix/lib/libmpi.so", O_RDONLY) = 3',
                'stat("/old/prefix/other", 0x1) = -1 ENOENT (No such file)',
            ],
            old,
            old / "classid",
            "ENOENT",
        )
        self.assertEqual(rejected["old_prefix_successful_access_count"], 1)
        self.assertEqual(rejected["other_old_prefix_attempt_count"], 2)
        self.assertEqual(
            AUDIT.parse_execve_paths(
                [
                    'execve("/recovery/bin/mpirun", ["mpirun"], 0x1) = 0',
                    'execve("/recovery/bin/prterun", ["prterun"], 0x1) = 0',
                ]
            ),
            ["/recovery/bin/mpirun", "/recovery/bin/prterun"],
        )

    def test_transient_mpi_maps_are_narrowly_classified(self) -> None:
        roots = [Path(value) for value in ("/usr", "/lib", "/dev", "/proc", "/sys")]
        for value in (
            "/SYSV00000000",
            "/dev/shm/sm_segment.123",
            "/dev/shm/ucx_shm_posix_abc-123",
            "/tmp/ompi.1234/1/pmix-gds-shmem2-jobdata/session-1",
            "/tmp/ompi.2555634/1/pmix-gds-shmem2.node01-prterun-node01-2555634@1.jobdata.2555634",
            "/tmp/ompi.2555634/1/pmix-gds-shmem2.node01-prterun-node01-2555634@1.session.2555634",
            "/tmp/ompi.1234/1/rank.0/shared_mem_cuda_pool",
        ):
            path = Path(value)
            self.assertEqual(
                AUDIT.classify_mapping(
                    path,
                    path,
                    Path("/old/prefix"),
                    Path("/recovery"),
                    roots,
                ),
                "transient_system",
                value,
            )
        arbitrary = Path("/tmp/arbitrary/libmpi.so")
        self.assertEqual(
            AUDIT.classify_mapping(
                arbitrary,
                arbitrary,
                Path("/old/prefix"),
                Path("/recovery"),
                roots,
            ),
            "unexpected",
        )

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

    def test_runtime_evidence_is_reparsed_instead_of_trusting_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            audit_directory = run / "mpi_runtime_audit"
            trace_directory = audit_directory / "strace"
            trace_directory.mkdir(parents=True)
            executable = Path("/usr/bin/true").resolve()
            executable_digest = hashlib.sha256(executable.read_bytes()).hexdigest()
            processes = [
                {
                    "pid": 100,
                    "role": "launcher",
                    "rank": None,
                    "executable_realpath": str(executable),
                    "executable_sha256": executable_digest,
                    "mapped_object_count": 1,
                },
                *[
                    {
                        "pid": 101 + rank,
                        "role": "rank",
                        "rank": rank,
                        "executable_realpath": str(executable),
                        "executable_sha256": executable_digest,
                        "mapped_object_count": 1,
                    }
                    for rank in range(4)
                ],
            ]
            object_lines = ["\t".join(VALIDATOR.OBJECT_HEADER)]
            for process in processes:
                object_lines.append(
                    "\t".join(
                        (
                            str(process["pid"]),
                            str(process["role"]),
                            "" if process["rank"] is None else str(process["rank"]),
                            str(executable),
                            str(executable),
                            executable_digest,
                            "recovery_runtime",
                        )
                    )
                )
            (audit_directory / "objects.tsv").write_text("\n".join(object_lines) + "\n")
            lines = [
                f'execve("{executable}", ["true"], 0x1) = 0',
                'stat("/old/prefix/classid", 0x1) = -1 ENOENT (No such file)',
                'openat(AT_FDCWD, "/old/prefix/classid", O_RDONLY) = -1 ENOENT (No such file)',
            ]
            trace = trace_directory / "trace.100"
            trace.write_text("\n".join(lines) + "\n")
            access = AUDIT.parse_strace_lines(
                lines, Path("/old/prefix"), Path("/old/prefix/classid"), "ENOENT"
            )
            audit = {
                "processes": processes,
                "mapped_object_count": 5,
                "old_prefix_mapped_object_count": 0,
                "unexpected_mapped_object_count": 0,
                "transient_system_mapped_object_count": 0,
                "command": [str(executable)],
                "observed_execve_realpaths": [str(executable)],
                "mpirun_invocation_execve_observed": True,
                "launcher_execve_observed": True,
                **access,
            }
            runtime = {
                "old_prefix": "/old/prefix",
                "recovery_root": "/usr",
                "mpirun_path": str(executable),
                "launcher_path": str(executable),
                "abacus_path": str(executable),
            }
            audit_spec = {
                "rank_count": 4,
                "system_mapping_roots": ["/usr", "/lib", "/lib64", "/dev", "/proc", "/sys"],
                "allowed_failed_probe_path": "/old/prefix/classid",
                "allowed_failed_probe_errno": "ENOENT",
            }
            errors: list[str] = []
            VALIDATOR._validate_runtime_audit_evidence(
                run, runtime, audit_spec, audit, errors, "fixture:"
            )
            self.assertEqual(errors, [])
            trace.write_text(
                "\n".join(lines + ['openat(AT_FDCWD, "/old/prefix/libmpi.so", O_RDONLY) = 3'])
                + "\n"
            )
            errors = []
            VALIDATOR._validate_runtime_audit_evidence(
                run, runtime, audit_spec, audit, errors, "fixture:"
            )
            self.assertTrue(any("differs from raw strace" in error for error in errors))

    def test_generator_and_validator_freeze_complete_synthetic_references(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            fixture = self._make_repository(repository)
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
                )
            self.assertEqual(payload["experiment_count"], 6)
            frozen_config = json.loads(config_path.read_text())
            self.assertEqual(Path(frozen_config["runtime"]["mpirun_path"]).name, "mpirun")
            self.assertEqual(
                Path(frozen_config["runtime"]["launcher_path"]).name, "prterun"
            )
            self.assertNotEqual(
                frozen_config["runtime"]["mpirun_path"],
                frozen_config["runtime"]["launcher_path"],
            )
            validation = VALIDATOR.validate(repository, config_path, manifest_path)
            self.assertEqual(validation["first_experiment_id"], "S1-20260805-113")
            self._git(repository, "add", "config")
            self._git(repository, "commit", "-m", "preregister MPI replay")
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
            with self.assertRaisesRegex(ValueError, "INPUT: SHA-256 mismatch"):
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
                )
            self.assertFalse(config_path.exists())
            self.assertFalse(manifest_path.exists())

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
        runner = (SCRIPTS / "run_s1_mpi_prefix_equivalence.sh").read_text()
        for assignment in (
            'OPAL_PREFIX="$recovery_prefix"',
            'PRTE_PREFIX="$recovery_prefix"',
            'PMIX_PREFIX="$recovery_prefix"',
            "M_OFDFT_MPI_AUDIT_STRACE_MODE=require",
            "M_OFDFT_EXPECTED_LAUNCHER=",
            "env -i",
        ):
            self.assertIn(assignment, runner)
        self.assertIn('exec 9<"$manifest"', runner)
        self.assertIn('<&9', runner)
        self.assertIn('</dev/null', runner)
        subprocess.run(
            [
                "/bin/bash",
                "-n",
                str(SCRIPTS / "run_s1_single.sh"),
                str(SCRIPTS / "run_s1_mpi_prefix_equivalence.sh"),
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
        for path in (r8_config, r8_manifest, r8_summary, abacus, mpirun, launcher):
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
        abacus.chmod(0o755)
        mpirun.chmod(0o755)
        launcher.chmod(0o755)
        abacus_digest = hashlib.sha256(abacus.read_bytes()).hexdigest()

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
                    {"abacus_sha256": abacus_digest, "mpi_ranks": 4}, indent=2
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
