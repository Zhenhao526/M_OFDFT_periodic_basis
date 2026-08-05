#!/usr/bin/env python3
"""Freeze the six S1-R8 runtime-relocation replay points after references exist."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

from analyze_s1_non_equilibrium import _archive_failures
from s1_mpi_prefix_equivalence_common import (
    CANONICAL_CONFIG_PATH,
    CANONICAL_MANIFEST_PATH,
    DEFAULT_R8_SUMMARY_PATH,
    DEFAULT_RUNTIME_SMOKE_SUMMARY_PATH,
    FIXED_PAIRS,
    PROTOCOL_REVISION,
    R8_CONFIG_PATH,
    R8_MANIFEST_PATH,
    REGISTERED_DEVICE_MAPPING_PATTERNS,
    REQUIRED_SOURCE_FILES,
    SYSTEM_MAPPING_EXACT_PATHS,
    SYSTEM_MAPPING_ROOTS,
    TRANSIENT_MAPPING_PATTERNS,
    atomic_write,
    git_clean,
    is_within,
    path_from_project,
    read_r8_manifest,
    relative_or_absolute,
    render_tsv,
    reparse_run,
    registered_old_prefix_failed_probes,
    require_tracked_at_head,
    sha256,
)
from s1_runtime_relocation_smoke import validate_smoke
from validate_s1_non_equilibrium_manifest import validate as validate_r8_manifest
from s1_runtime_relocation_elf import (
    file_identity,
    relocation_equivalence_evidence,
    versioned_tool_identity,
)


def _head(project_root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(project_root), "rev-parse", "HEAD"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError(completed.stderr.strip() or "cannot resolve repository HEAD")
    return completed.stdout.strip()


def _git_blob(project_root: Path, commit: str, relative_path: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(project_root), "cat-file", "blob", f"{commit}:{relative_path}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"cannot read {relative_path} at R8 analysis commit: {detail}")
    return completed.stdout


def validate_r8_summary_provenance(
    project_root: Path,
    r8_config_path: Path,
    r8_manifest_path: Path,
    r8_summary: dict,
    r8_manifest_validation: dict,
) -> dict:
    expected_counts = {
        "expected_calculations": 42,
        "selected_calculations": 42,
        "accepted_comparisons": 6,
    }
    for key, expected in expected_counts.items():
        if r8_summary.get(key) != expected:
            raise ValueError(f"S1-R8 summary {key} must equal {expected}")
    provenance = r8_summary.get("analysis_provenance")
    if not isinstance(provenance, dict):
        raise ValueError("S1-R8 summary lacks analysis_provenance")
    commit = str(provenance.get("analyzer_code_commit", ""))
    if not commit or not all(character in "0123456789abcdef" for character in commit):
        raise ValueError("S1-R8 summary has an invalid analyzer commit")
    registered_files = {
        "r8_analyzer_sha256": "scripts/analyze_s1_non_equilibrium.py",
        "eos_analyzer_sha256": "scripts/analyze_s1_eos.py",
        "result_parser_sha256": "scripts/parse_s1_single.py",
    }
    payload = {"analysis_commit": commit}
    for key, relative in registered_files.items():
        blob_digest = hashlib.sha256(_git_blob(project_root, commit, relative)).hexdigest()
        current_path = project_root / relative
        if sha256(current_path) != blob_digest:
            raise ValueError(f"current {relative} differs from R8 analysis commit {commit}")
        payload[key] = blob_digest
    expected_provenance = {
        "analyzer_script_sha256": payload["r8_analyzer_sha256"],
        "result_parser_script_sha256": payload["result_parser_sha256"],
        "config_sha256": sha256(r8_config_path),
        "manifest_sha256": sha256(r8_manifest_path),
        "preregistration_commit": r8_manifest_validation["preregistration_commit"],
    }
    r8_config = json.loads(r8_config_path.read_text(encoding="utf-8"))
    core = r8_config["core_reference"]
    core_summary = path_from_project(project_root, core["summary_path"])
    if sha256(core_summary) != core["summary_sha256"]:
        raise ValueError("S1-R8 core summary differs from its registered SHA-256")
    expected_provenance["core_summary_sha256"] = core["summary_sha256"]
    for key, expected in expected_provenance.items():
        if provenance.get(key) != expected:
            raise ValueError(f"S1-R8 analysis provenance {key} mismatch")
    return payload


def build_frozen_payload(
    project_root: Path,
    recovery_prefix: Path,
    old_prefix: Path,
    abacus: Path,
    mpirun: Path,
    launcher: Path | None,
    r8_config_path: Path,
    r8_manifest_path: Path,
    r8_summary_path: Path,
    output_manifest_path: Path,
    *,
    readelf: Path = Path("/usr/bin/readelf"),
    chrpath: Path = Path("/usr/bin/chrpath"),
    strace: Path = Path("/usr/bin/strace"),
    unshare: Path = Path("/usr/bin/unshare"),
    mount: Path = Path("/usr/bin/mount"),
    bash: Path = Path("/bin/bash"),
    python: Path = Path("/usr/bin/python3"),
    reference_mpirun: Path | None = None,
    reference_launcher: Path | None = None,
    require_clean_worktree: bool = True,
    smoke_summary_path: Path | None = None,
) -> tuple[dict, list[dict[str, object]], list[Path]]:
    project_root = project_root.resolve()
    if require_clean_worktree and not git_clean(project_root):
        raise ValueError("refusing to freeze runtime-relocation replay from a dirty worktree")
    if os.getuid() == 0:
        raise ValueError("runtime relocation must be frozen by a non-root host user")

    recovery_prefix = recovery_prefix.expanduser()
    if not recovery_prefix.is_absolute():
        raise ValueError("recovery prefix must be an absolute path")
    recovery_prefix = recovery_prefix.resolve(strict=True)
    if not recovery_prefix.is_dir():
        raise ValueError(f"recovery prefix is not a directory: {recovery_prefix}")
    recovery_root = recovery_prefix.parent.resolve()
    old_prefix = old_prefix.expanduser()
    if not old_prefix.is_absolute():
        raise ValueError("old prefix must be an absolute path")
    old_prefix = old_prefix.resolve(strict=False)
    if old_prefix == recovery_prefix:
        raise ValueError("old prefix must be an absolute path different from recovery prefix")
    old_root = old_prefix.parent
    replay_abacus = file_identity(abacus, "relocated replay ABACUS", require_elf=True)
    replay_mpirun = file_identity(mpirun, "replay mpirun", require_elf=True)
    reference_mpirun_identity = file_identity(
        reference_mpirun if reference_mpirun is not None else mpirun,
        "reference mpirun invocation",
        require_elf=True,
    )
    replay_launcher = file_identity(
        launcher if launcher is not None else recovery_prefix / "bin" / "prterun",
        "replay final MPI launcher",
        require_elf=True,
    )
    reference_launcher_identity = file_identity(
        reference_launcher
        if reference_launcher is not None
        else old_prefix / "bin" / "prterun",
        "reference final MPI launcher",
        require_elf=True,
    )
    if not is_within(Path(replay_mpirun["realpath"]), recovery_prefix):
        raise ValueError("mpirun must resolve inside the recovery prefix")
    if not is_within(Path(replay_launcher["realpath"]), recovery_prefix):
        raise ValueError("final MPI launcher must resolve inside the recovery prefix")
    if not is_within(Path(replay_abacus["realpath"]), recovery_root):
        raise ValueError("ABACUS must resolve inside the recovery runtime root")
    if not is_within(Path(reference_launcher_identity["realpath"]), old_prefix):
        raise ValueError("reference final launcher must resolve inside the old prefix")
    if reference_launcher_identity["sha256"] != replay_launcher["sha256"]:
        raise ValueError("reference and replay launchers are not byte-identical")

    runtime_tools = {
        "strace": versioned_tool_identity(strace, "strace"),
        "unshare": versioned_tool_identity(unshare, "unshare"),
        "mount": versioned_tool_identity(mount, "mount"),
        "bash": versioned_tool_identity(bash, "bash"),
        "python": versioned_tool_identity(python, "python"),
    }
    wrapper_paths = {
        "namespace_launcher": project_root
        / "scripts"
        / "runtime_relocation_namespace_launcher.py",
        "namespace_payload": project_root
        / "scripts"
        / "runtime_relocation_namespace_payload.sh",
        "audit_launcher": project_root
        / "scripts"
        / "runtime_relocation_audit_launcher.py",
        "rank_wrapper": project_root
        / "scripts"
        / "runtime_relocation_rank_wrapper.py",
    }
    wrappers = {}
    for name, path in wrapper_paths.items():
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"missing or symbolic-link runtime wrapper: {path}")
        wrappers[name] = {"path": str(path), "sha256": sha256(path)}

    for path, label in (
        (r8_config_path, "S1-R8 config"),
        (r8_manifest_path, "S1-R8 manifest"),
        (r8_summary_path, "accepted S1-R8 summary"),
    ):
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"missing or symbolic-link {label}: {path}")

    try:
        r8_manifest_validation = validate_r8_manifest(
            project_root, r8_config_path, r8_manifest_path
        )
    except ValueError as error:
        raise ValueError(f"S1-R8 preregistered manifest validation failed: {error}") from error

    r8_summary = json.loads(r8_summary_path.read_text(encoding="utf-8"))
    if r8_summary.get("s1_r8_status") != "accepted":
        raise ValueError("S1-R8 summary must be accepted before MPI replay can be frozen")
    r8_series = r8_summary.get("series", {})
    expected_series = {f"{material}/{series}" for _, _, material, series in FIXED_PAIRS}
    if not expected_series.issubset(r8_series):
        missing = sorted(expected_series - set(r8_series))
        raise ValueError(f"S1-R8 summary is missing registered series: {missing}")
    if any(r8_series[key].get("status") != "accepted" for key in expected_series):
        raise ValueError("all six S1-R8 source series must be accepted")
    r8_algorithm_provenance = validate_r8_summary_provenance(
        project_root,
        r8_config_path,
        r8_manifest_path,
        r8_summary,
        r8_manifest_validation,
    )

    r8_rows = read_r8_manifest(r8_manifest_path)
    tracked_paths = [r8_config_path, r8_manifest_path, r8_summary_path]
    staged_rows: list[dict[str, object]] = []
    mappings = []
    reference_abacus_identities: list[dict] = []
    reference_mpirun_identities: list[dict] = []
    for replay_id, reference_id, material, series_id in FIXED_PAIRS:
        source_row = r8_rows.get(reference_id)
        if source_row is None:
            raise ValueError(f"S1-R8 manifest is missing reference {reference_id}")
        if source_row.get("material") != material or source_row.get("series_id") != series_id:
            raise ValueError(f"{reference_id}: material/series differs from fixed mapping")
        if round(float(source_row.get("volume_ratio", "nan")), 12) != 1.0:
            raise ValueError(f"{reference_id}: fixed MPI replay source is not V/V0=1.0")

        input_directory_value = str(source_row["input_directory"])
        input_directory = path_from_project(project_root, input_directory_value)
        source_paths = {name: input_directory / name for name in REQUIRED_SOURCE_FILES}
        for name, path in source_paths.items():
            if not path.is_file() or path.is_symlink():
                raise ValueError(f"{reference_id}: missing or symbolic-link source {name}")
        metadata = json.loads(source_paths["metadata.json"].read_text(encoding="utf-8"))
        if sha256(source_paths["metadata.json"]) != source_row.get(
            "input_metadata_sha256"
        ):
            raise ValueError(f"{reference_id}: R8 row input_metadata_sha256 mismatch")
        if metadata.get("material") != material or metadata.get("series_id") != series_id:
            raise ValueError(f"{reference_id}: source metadata does not match fixed mapping")
        pseudopotential = metadata.get("pseudopotential")
        if not isinstance(pseudopotential, str) or Path(pseudopotential).name != pseudopotential:
            raise ValueError(f"{reference_id}: invalid pseudopotential basename")
        pseudo_path = project_root / "assets" / "pseudo" / pseudopotential
        if not pseudo_path.is_file() or pseudo_path.is_symlink():
            raise ValueError(f"{reference_id}: missing or symbolic-link pseudopotential")
        if sha256(pseudo_path) != metadata.get("pseudopotential_sha256"):
            raise ValueError(f"{reference_id}: pseudopotential hash differs from metadata")

        reference_run = project_root / "runs" / reference_id
        try:
            reference_metadata, reference_log, reference_result = reparse_run(reference_run)
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError(f"{reference_id}: reference result is incomplete: {error}") from error
        if not reference_result.get("converged"):
            raise ValueError(f"{reference_id}: reference result did not converge")
        if (reference_run / "input_metadata.json").read_bytes() != source_paths[
            "metadata.json"
        ].read_bytes():
            raise ValueError(f"{reference_id}: run metadata differs byte-for-byte from source")
        reference_experiment_metadata_path = reference_run / "experiment_metadata.json"
        reference_result_path = reference_run / "result.json"
        if not reference_experiment_metadata_path.is_file():
            raise ValueError(f"{reference_id}: missing experiment_metadata.json")
        reference_experiment_metadata = json.loads(
            reference_experiment_metadata_path.read_text(encoding="utf-8")
        )
        archive_failures = _archive_failures(
            project_root,
            source_row,
            reference_run,
            reference_metadata,
            reference_result,
        )
        if archive_failures:
            raise ValueError(
                f"{reference_id}: reference run archive differs from frozen input: "
                + ",".join(archive_failures)
            )
        if reference_experiment_metadata.get("mpi_ranks") != 4:
            raise ValueError(f"{reference_id}: reference run did not use four MPI ranks")
        try:
            reference_abacus_identity = file_identity(
                Path(reference_experiment_metadata["abacus_path"]),
                f"{reference_id} reference ABACUS",
                require_elf=True,
            )
        except KeyError as error:
            raise ValueError(
                f"{reference_id}: reference runtime metadata is missing {error}"
            ) from error
        if reference_experiment_metadata.get("abacus_sha256") != (
            reference_abacus_identity["sha256"]
        ):
            raise ValueError(f"{reference_id}: reference ABACUS SHA-256 mismatch")
        metadata_mpirun_fields = {
            key: reference_experiment_metadata.get(key)
            for key in ("mpirun_path", "mpirun_sha256")
        }
        if any(value is not None for value in metadata_mpirun_fields.values()):
            if not all(value is not None for value in metadata_mpirun_fields.values()):
                raise ValueError(f"{reference_id}: partial reference mpirun metadata")
            observed_mpirun = file_identity(
                Path(str(metadata_mpirun_fields["mpirun_path"])),
                f"{reference_id} recorded reference mpirun",
                require_elf=True,
            )
            if (
                observed_mpirun != reference_mpirun_identity
                or metadata_mpirun_fields["mpirun_sha256"]
                != reference_mpirun_identity["sha256"]
            ):
                raise ValueError(f"{reference_id}: reference mpirun identity mismatch")
        reference_abacus_identities.append(reference_abacus_identity)
        reference_mpirun_identities.append(reference_mpirun_identity)

        tracked_paths.extend(
            [
                *source_paths.values(),
                pseudo_path,
                reference_run / "input_metadata.json",
                reference_result_path,
                reference_log,
                reference_experiment_metadata_path,
            ]
        )
        staged_rows.append(
            {
                "replay_experiment_id": replay_id,
                "reference_experiment_id": reference_id,
                "input_directory": input_directory_value,
                "material": material,
                "series_id": series_id,
                "solver": str(metadata["solver"]),
                "input_sha256": sha256(source_paths["INPUT"]),
                "stru_sha256": sha256(source_paths["STRU"]),
                "kpt_sha256": sha256(source_paths["KPT"]),
                "metadata_sha256": sha256(source_paths["metadata.json"]),
                "pseudopotential": pseudopotential,
                "pseudopotential_sha256": sha256(pseudo_path),
                "reference_result_path": relative_or_absolute(
                    project_root, reference_result_path
                ),
                "reference_result_sha256": sha256(reference_result_path),
                "reference_log_path": relative_or_absolute(project_root, reference_log),
                "reference_log_sha256": sha256(reference_log),
                "reference_experiment_metadata_path": relative_or_absolute(
                    project_root, reference_experiment_metadata_path
                ),
                "reference_experiment_metadata_sha256": sha256(
                    reference_experiment_metadata_path
                ),
                "reference_abacus_path": reference_abacus_identity["path"],
                "reference_abacus_realpath": reference_abacus_identity["realpath"],
                "reference_abacus_sha256": reference_abacus_identity["sha256"],
                "reference_mpirun_path": reference_mpirun_identity["path"],
                "reference_mpirun_realpath": reference_mpirun_identity["realpath"],
                "reference_mpirun_sha256": reference_mpirun_identity["sha256"],
            }
        )
        mappings.append(
            {
                "replay_experiment_id": replay_id,
                "reference_experiment_id": reference_id,
                "material": material,
                "series_id": series_id,
                "input_directory": input_directory_value,
            }
        )

    reference_abacus_values = {
        json.dumps(identity, sort_keys=True) for identity in reference_abacus_identities
    }
    reference_mpirun_values = {
        json.dumps(identity, sort_keys=True) for identity in reference_mpirun_identities
    }
    if len(reference_abacus_values) != 1:
        raise ValueError("six reference points do not share one ABACUS identity")
    if len(reference_mpirun_values) != 1:
        raise ValueError("six reference points do not share one mpirun identity")
    reference_abacus = reference_abacus_identities[0]
    frozen_reference_mpirun = reference_mpirun_identities[0]
    if frozen_reference_mpirun["sha256"] != replay_mpirun["sha256"]:
        raise ValueError("reference and replay mpirun bytes are not identical")
    elf_relocation = relocation_equivalence_evidence(
        Path(reference_abacus["path"]),
        Path(replay_abacus["path"]),
        old_prefix,
        readelf,
        chrpath,
    )

    tracked_failures = require_tracked_at_head(project_root, tracked_paths)
    if tracked_failures:
        raise ValueError(
            "reference/source artifacts are not immutable at HEAD:\n- "
            + "\n- ".join(tracked_failures)
        )

    prefix_environment = {
        "OPAL_PREFIX": str(recovery_prefix),
        "PRTE_PREFIX": str(recovery_prefix),
        "PMIX_PREFIX": str(recovery_prefix),
        "UCX_MODULE_DIR": str(recovery_prefix),
    }
    config = {
        "schema_version": 2,
        "status": "runtime_relocation_equivalence_frozen",
        "protocol_revision": PROTOCOL_REVISION,
        "generated_from_commit": _head(project_root),
        "experiment_id_block": {
            "date": "20260805",
            "start_sequence": 113,
            "end_sequence": 118,
        },
        "rank_count": 4,
        "source": {
            "r8_config_path": relative_or_absolute(project_root, r8_config_path),
            "r8_config_sha256": sha256(r8_config_path),
            "r8_manifest_path": relative_or_absolute(project_root, r8_manifest_path),
            "r8_manifest_sha256": sha256(r8_manifest_path),
            "r8_summary_path": relative_or_absolute(project_root, r8_summary_path),
            "r8_summary_sha256": sha256(r8_summary_path),
            "r8_status": "accepted",
            "r8_series_status": {
                key: str(r8_series[key]["status"]) for key in sorted(expected_series)
            },
            "r8_manifest_validation": r8_manifest_validation,
            "r8_algorithm_provenance": r8_algorithm_provenance,
        },
        "runtime": {
            "recovery_root": str(recovery_root),
            "recovery_prefix": str(recovery_prefix),
            "old_root": str(old_root),
            "old_prefix": str(old_prefix),
            "reference": {
                "abacus": reference_abacus,
                "mpirun": frozen_reference_mpirun,
                "launcher": reference_launcher_identity,
                "r8_launcher_observation": {
                    "claim": "original_42_used_old_prefix_prte",
                    "evidence_scope": (
                        "operator_remote_proc_observation_not_archived_per_reference_run"
                    ),
                    "launcher_realpath_is_in_old_prefix": True,
                    "launcher_bytes_equal_replay_launcher": True,
                    "mpirun_claim": "original_42_invoked_registered_recovery_mpirun",
                    "mpirun_metadata_scope": (
                        "explicit_freeze_operator_observation_legacy_run_metadata_omits_mpirun"
                    ),
                },
            },
            "replay": {
                "abacus": replay_abacus,
                "mpirun": replay_mpirun,
                "launcher": replay_launcher,
            },
            "prefix_environment": prefix_environment,
            "tools": runtime_tools,
            "wrappers": wrappers,
            "elf_relocation": elf_relocation,
            "mpi_argv_prefix": [
                "--allow-run-as-root",
                "--bind-to",
                "core",
                "-np",
                "4",
            ],
            "namespace": {
                "unshare_argv_prefix": [
                    runtime_tools["unshare"]["path"],
                    "--user",
                    "--map-root-user",
                    "--kill-child=KILL",
                    "--mount",
                    "--pid",
                    "--fork",
                    "--mount-proc",
                    "--propagation",
                    "private",
                    runtime_tools["bash"]["path"],
                    wrappers["namespace_payload"]["path"],
                ],
                "mount_target": str(old_root),
                "mount_type": "tmpfs",
                "mount_source": "tmpfs",
                "mount_options": ["size=1m", "nosuid", "nodev", "noexec"],
                "host_uid_remains_unprivileged": True,
                "host_uid": os.getuid(),
                "host_gid": os.getgid(),
                "namespace_effective_uid": 0,
                "pid_namespace_required": True,
                "namespace_init_pid": 1,
                "host_proc_pid_namespace_empty_after_exit_required": True,
                "external_old_root_must_survive": True,
                "total_wall_timeout_seconds": 7260,
                "timeout_requires_zero_residual_processes": True,
            },
        },
        "runtime_audit": {
            "launcher_count": 1,
            "rank_count": 4,
            "runtime_wall_timeout_seconds": 7200,
            "absolute_deadline_watchdog_seconds": 7200,
            "known_pid_terminal_proof_required": True,
            "mapping_observation_scope": "final_prterun_and_four_abacus_ranks",
            "mpirun_and_support_daemon_maps_out_of_scope": True,
            "rank_handshake_required": True,
            "initial_maps_required_for_every_target": True,
            "old_prefix_mapped_object_count_max": 0,
            "unexpected_mapped_object_count_max": 0,
            "all_captured_regular_mapped_objects_must_be_hashed": True,
            "recovery_component_counterpart_byte_equality_required": True,
            "counterpart_missing_count_max": 0,
            "counterpart_byte_mismatch_count_max": 0,
            "counterpart_exclusions": {
                "relocated_abacus_elf_gate": {
                    "reference": reference_abacus,
                    "replay": replay_abacus,
                    "byte_equality_required": False,
                },
                "mpirun_identity_gate": {
                    "reference": frozen_reference_mpirun,
                    "replay": replay_mpirun,
                    "byte_equality_required": True,
                },
                "launcher_identity_gate": {
                    "reference": reference_launcher_identity,
                    "replay": replay_launcher,
                    "byte_equality_required": True,
                },
            },
            "successful_exec_multiset": {
                replay_mpirun["realpath"]: 1,
                replay_launcher["realpath"]: 1,
                runtime_tools["python"]["realpath"]: 4,
                replay_abacus["realpath"]: 4,
            },
            "ambiguous_exec_result_count_max": 0,
            "file_trace_required": True,
            "strace_before_after_identity_required": True,
            "old_prefix_successful_access_count_max": 0,
            "old_prefix_exec_success_count_max": 0,
            "unknown_old_prefix_failed_probe_count_max": 0,
            "registered_probe_count_mismatch_count_max": 0,
            "registered_old_prefix_failed_probe_count": 22,
            "registered_old_prefix_failed_probes": list(
                registered_old_prefix_failed_probes(old_prefix)
            ),
            "clean_environment_required": True,
            "controlled_home_policy": "per_run_marker_only_no_user_mpi_config",
            "required_path": f"{recovery_prefix}/bin:/usr/bin:/bin",
            "required_cmake_prefix_path": str(recovery_prefix),
            "required_mklroot": str(recovery_prefix),
            "required_ld_library_path": str(recovery_prefix / "lib"),
            "ld_preload_must_be_unset": True,
            "transient_mapping_patterns": list(TRANSIENT_MAPPING_PATTERNS),
            "system_mapping_roots": list(SYSTEM_MAPPING_ROOTS),
            "system_mapping_exact_paths": list(SYSTEM_MAPPING_EXACT_PATHS),
            "registered_device_mapping_patterns": list(
                REGISTERED_DEVICE_MAPPING_PATTERNS
            ),
            "namespace_evidence_required": True,
        },
        "acceptance": {
            "max_absolute_energy_difference_mev_per_atom": 0.1,
            "max_absolute_pressure_difference_gpa": 0.02,
            "threshold_comparison": "strict_less_than",
            "storage_equivalence_tiers_diagnostic_only": [
                "storage_exact",
                "storage_resolution_equal",
            ],
            "scientific_runtime_and_r8_gates_passed_action": (
                "close_runtime_relocation_equivalence_and_keep_s1_r8_conclusion"
            ),
            "r8_v100_replacement_must_preserve_conclusion": True,
            "full_42_rerun_triggers": [
                "elf_difference_outside_registered_runpath_slot",
                "elf_needed_build_id_or_load_layout_changed",
                "scientific_energy_or_pressure_gate_failed",
                "r8_series_or_fit_hard_gate_changed",
                "mapped_component_byte_equivalence_unprovable",
            ],
            "six_point_retry_after_fix_triggers": [
                "pure_namespace_launcher_or_runtime_audit_failure"
            ],
        },
        "manifest_path": relative_or_absolute(project_root, output_manifest_path),
        "mappings": mappings,
    }
    if smoke_summary_path is not None:
        smoke_row = next(
            row
            for row in staged_rows
            if row["reference_experiment_id"] == "S1-20260805-074"
        )
        smoke_validation = validate_smoke(
            project_root,
            config,
            smoke_row,
            smoke_summary_path.resolve(),
            require_committed=True,
        )
        smoke_tracked_paths = smoke_validation.pop("tracked_paths")
        tracked_paths.extend(smoke_tracked_paths)
        smoke_tracking_failures = require_tracked_at_head(
            project_root, smoke_tracked_paths
        )
        if smoke_tracking_failures:
            raise ValueError(
                "managed 074 smoke evidence is not immutable at HEAD:\n- "
                + "\n- ".join(smoke_tracking_failures)
            )
        config["source"]["runtime_relocation_smoke"] = smoke_validation
    return config, staged_rows, tracked_paths


def generate(
    project_root: Path,
    recovery_prefix: Path,
    old_prefix: Path,
    abacus: Path,
    mpirun: Path,
    config_path: Path,
    manifest_path: Path,
    r8_config_path: Path,
    r8_manifest_path: Path,
    r8_summary_path: Path,
    *,
    launcher: Path | None = None,
    readelf: Path = Path("/usr/bin/readelf"),
    chrpath: Path = Path("/usr/bin/chrpath"),
    strace: Path = Path("/usr/bin/strace"),
    unshare: Path = Path("/usr/bin/unshare"),
    mount: Path = Path("/usr/bin/mount"),
    bash: Path = Path("/bin/bash"),
    python: Path = Path("/usr/bin/python3"),
    reference_mpirun: Path | None = None,
    reference_launcher: Path | None = None,
    require_clean_worktree: bool = True,
    smoke_summary_path: Path | None = None,
) -> dict:
    if smoke_summary_path is None:
        raise ValueError("formal generation requires an accepted --smoke-summary")
    for output in (config_path, manifest_path):
        if output.exists() or output.is_symlink():
            raise ValueError(f"refusing to overwrite frozen output: {output}")
    config, rows, _ = build_frozen_payload(
        project_root,
        recovery_prefix,
        old_prefix,
        abacus,
        mpirun,
        launcher,
        r8_config_path,
        r8_manifest_path,
        r8_summary_path,
        manifest_path,
        readelf=readelf,
        chrpath=chrpath,
        strace=strace,
        unshare=unshare,
        mount=mount,
        bash=bash,
        python=python,
        reference_mpirun=reference_mpirun,
        reference_launcher=reference_launcher,
        require_clean_worktree=require_clean_worktree,
        smoke_summary_path=smoke_summary_path,
    )
    config_text = json.dumps(config, indent=2, sort_keys=True) + "\n"
    config_digest = hashlib.sha256(config_text.encode("utf-8")).hexdigest()
    for row in rows:
        row["config_sha256"] = config_digest
    manifest_text = render_tsv(rows)
    atomic_write(config_path, config_text)
    atomic_write(manifest_path, manifest_text)
    return {
        "config_path": str(config_path),
        "config_sha256": config_digest,
        "manifest_path": str(manifest_path),
        "manifest_sha256": sha256(manifest_path),
        "experiment_count": len(rows),
        "first_experiment_id": rows[0]["replay_experiment_id"],
        "last_experiment_id": rows[-1]["replay_experiment_id"],
    }


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description=(
            "Freeze S1-R8 runtime-relocation replay only after all reference artifacts exist."
        )
    )
    parser.add_argument("--recovery-prefix", type=Path, required=True)
    parser.add_argument("--old-prefix", type=Path, required=True)
    parser.add_argument(
        "--abacus",
        type=Path,
        required=True,
        help="relocated replay ABACUS (reference ABACUS is read from run metadata)",
    )
    parser.add_argument("--mpirun", type=Path, required=True)
    parser.add_argument(
        "--launcher",
        type=Path,
        help="final launcher ELF; defaults to <recovery-prefix>/bin/prterun",
    )
    parser.add_argument("--reference-launcher", type=Path)
    parser.add_argument(
        "--reference-mpirun",
        type=Path,
        help="R8 mpirun invocation; defaults to --mpirun for legacy run metadata",
    )
    parser.add_argument("--readelf", type=Path, default=Path("/usr/bin/readelf"))
    parser.add_argument("--chrpath", type=Path, default=Path("/usr/bin/chrpath"))
    parser.add_argument("--strace", type=Path, default=Path("/usr/bin/strace"))
    parser.add_argument("--unshare", type=Path, default=Path("/usr/bin/unshare"))
    parser.add_argument("--mount", type=Path, default=Path("/usr/bin/mount"))
    parser.add_argument("--bash", type=Path, default=Path("/bin/bash"))
    parser.add_argument("--python", type=Path, default=Path("/usr/bin/python3"))
    parser.add_argument(
        "--config",
        type=Path,
        default=project_root / CANONICAL_CONFIG_PATH,
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=project_root / CANONICAL_MANIFEST_PATH,
    )
    parser.add_argument(
        "--r8-config", type=Path, default=project_root / R8_CONFIG_PATH
    )
    parser.add_argument(
        "--r8-manifest", type=Path, default=project_root / R8_MANIFEST_PATH
    )
    parser.add_argument(
        "--r8-summary", type=Path, default=project_root / DEFAULT_R8_SUMMARY_PATH
    )
    parser.add_argument(
        "--smoke-summary",
        type=Path,
        required=True,
        help=(
            "committed accepted managed 074 smoke summary; formal 113--118 "
            "preregistration is refused without it"
        ),
    )
    args = parser.parse_args()
    payload = generate(
        project_root,
        args.recovery_prefix,
        args.old_prefix,
        args.abacus,
        args.mpirun,
        args.config.resolve(),
        args.manifest.resolve(),
        args.r8_config.resolve(),
        args.r8_manifest.resolve(),
        args.r8_summary.resolve(),
        launcher=args.launcher,
        readelf=args.readelf,
        chrpath=args.chrpath,
        strace=args.strace,
        unshare=args.unshare,
        mount=args.mount,
        bash=args.bash,
        python=args.python,
        reference_mpirun=args.reference_mpirun,
        reference_launcher=args.reference_launcher,
        smoke_summary_path=args.smoke_summary.resolve(),
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
