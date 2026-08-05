#!/usr/bin/env python3
"""Strict byte/provenance validation for S1-R8 runtime relocation."""

from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import re
import stat
import subprocess
from datetime import datetime
from pathlib import Path

from analyze_s1_non_equilibrium import _archive_failures, _checksum_failures
from s1_mpi_prefix_equivalence_common import (
    CANONICAL_CONFIG_PATH,
    CANONICAL_MANIFEST_PATH,
    FIXED_PAIRS,
    PROTOCOL_REVISION,
    REGISTERED_DEVICE_MAPPING_PATTERNS,
    REQUIRED_SOURCE_FILES,
    SYSTEM_MAPPING_EXACT_PATHS,
    SYSTEM_MAPPING_ROOTS,
    TRANSIENT_MAPPING_PATTERNS,
    equivalence_tier,
    is_within,
    normalized_run_input,
    path_from_project,
    raw_observables,
    read_r8_manifest,
    read_tsv,
    reparse_run,
    registered_old_prefix_failed_probes,
    require_tracked_at_head,
    sha256,
)
from runtime_relocation_audit_launcher import (
    classify_mapping,
    parse_execve_records,
    parse_strace_records,
)
from s1_runtime_relocation_elf import (
    file_identity,
    relocation_equivalence_evidence,
    versioned_tool_identity,
)
from s1_runtime_relocation_smoke import validate_smoke


TOP_LEVEL_KEYS = {
    "schema_version",
    "status",
    "protocol_revision",
    "generated_from_commit",
    "experiment_id_block",
    "rank_count",
    "source",
    "runtime",
    "runtime_audit",
    "acceptance",
    "manifest_path",
    "mappings",
}
RUNTIME_KEYS = {
    "recovery_root",
    "recovery_prefix",
    "old_root",
    "old_prefix",
    "reference",
    "replay",
    "prefix_environment",
    "tools",
    "wrappers",
    "elf_relocation",
    "mpi_argv_prefix",
    "namespace",
}
AUDIT_KEYS = {
    "launcher_count",
    "rank_count",
    "runtime_wall_timeout_seconds",
    "absolute_deadline_watchdog_seconds",
    "known_pid_terminal_proof_required",
    "strace_fixed_arguments",
    "tracee_termination_contract",
    "mapping_observation_scope",
    "mpirun_and_support_daemon_maps_out_of_scope",
    "rank_handshake_required",
    "initial_maps_required_for_every_target",
    "old_prefix_mapped_object_count_max",
    "unexpected_mapped_object_count_max",
    "all_captured_regular_mapped_objects_must_be_hashed",
    "recovery_component_counterpart_byte_equality_required",
    "counterpart_missing_count_max",
    "counterpart_byte_mismatch_count_max",
    "counterpart_exclusions",
    "successful_exec_multiset",
    "ambiguous_exec_result_count_max",
    "file_trace_required",
    "strace_before_after_identity_required",
    "old_prefix_successful_access_count_max",
    "old_prefix_exec_success_count_max",
    "unknown_old_prefix_failed_probe_count_max",
    "registered_probe_count_mismatch_count_max",
    "registered_old_prefix_failed_probe_count",
    "registered_old_prefix_failed_probes",
    "clean_environment_required",
    "controlled_home_policy",
    "controlled_home_readonly_bind_mount_required",
    "required_path",
    "required_cmake_prefix_path",
    "required_mklroot",
    "required_ld_library_path",
    "required_cuda_cache_disable",
    "ld_preload_must_be_unset",
    "transient_mapping_patterns",
    "system_mapping_roots",
    "system_mapping_exact_paths",
    "registered_device_mapping_patterns",
    "namespace_evidence_required",
}
SOURCE_KEYS = {
    "r8_config_path",
    "r8_config_sha256",
    "r8_manifest_path",
    "r8_manifest_sha256",
    "r8_summary_path",
    "r8_summary_sha256",
    "r8_status",
    "r8_series_status",
    "r8_manifest_validation",
    "r8_algorithm_provenance",
    "runtime_relocation_smoke",
}
R8_VALIDATION_KEYS = {
    "experiment_count",
    "first_experiment_id",
    "last_experiment_id",
    "config_sha256",
    "manifest_sha256",
    "preregistration_commit",
}
R8_ALGORITHM_KEYS = {
    "analysis_commit",
    "r8_analyzer_sha256",
    "eos_analyzer_sha256",
    "result_parser_sha256",
}
OBJECT_HEADER = (
    "pid",
    "role",
    "rank",
    "mapped_path",
    "loaded_realpath",
    "loaded_sha256",
    "classification",
)
COUNTERPART_HEADER = (
    "recovery_path",
    "recovery_realpath",
    "recovery_relative_path",
    "recovery_sha256",
    "old_counterpart_path",
    "old_counterpart_realpath",
    "old_counterpart_sha256",
    "byte_equal",
    "verification_rule",
)
FROZEN_IMPLEMENTATION_PATHS = (
    "environment/activate.sh",
    "scripts/generate_s1_mpi_prefix_equivalence.py",
    "scripts/generate_s1_runtime_relocation_equivalence.py",
    "scripts/validate_s1_mpi_prefix_equivalence.py",
    "scripts/validate_s1_runtime_relocation_equivalence.py",
    "scripts/run_s1_mpi_prefix_equivalence.sh",
    "scripts/run_s1_runtime_relocation_equivalence.sh",
    "scripts/mpi_prefix_audit_launcher.py",
    "scripts/runtime_relocation_audit_launcher.py",
    "scripts/runtime_relocation_namespace_launcher.py",
    "scripts/runtime_relocation_namespace_payload.sh",
    "scripts/runtime_relocation_rank_wrapper.py",
    "scripts/write_s1_runtime_relocation_status.py",
    "scripts/s1_runtime_relocation_smoke.py",
    "scripts/run_s1_runtime_relocation_smoke.py",
    "scripts/s1_runtime_relocation_elf.py",
    "scripts/analyze_s1_mpi_prefix_equivalence.py",
    "scripts/analyze_s1_runtime_relocation_equivalence.py",
    "scripts/s1_mpi_prefix_equivalence_common.py",
    "scripts/s1_runtime_relocation_equivalence_common.py",
    "scripts/run_s1_single.sh",
    "scripts/parse_s1_single.py",
    "scripts/analyze_s1_non_equilibrium.py",
    "scripts/analyze_s1_eos.py",
)


def _same_path(left: Path, right: Path) -> bool:
    return left.resolve(strict=False) == right.resolve(strict=False)


def _strace_command_contract_matches(
    strace_command: object,
    strace_path: Path,
    trace_prefix: Path,
    real_command: object,
) -> bool:
    """Require the exact 5.16-compatible command and external-containment mode."""

    fixed = [
        "-ff",
        "-qq",
        "-I",
        "1",
        "-s",
        "4096",
        "-e",
        "trace=file,process",
    ]
    return (
        isinstance(strace_command, list)
        and isinstance(real_command, list)
        and len(strace_command) == 11 + len(real_command)
        and _same_path(Path(str(strace_command[0])), strace_path)
        and strace_command[1:9] == fixed
        and strace_command[9] == "-o"
        and _same_path(Path(str(strace_command[10])), trace_prefix)
        and strace_command[11:] == real_command
        and "--kill-on-exit" not in strace_command
    )


def _known_pid_terminal_contract_failures(
    terminal_process: object,
    trace_pids: set[int],
    launcher_pid: object,
    rank_pids: dict[int, int],
    process_pids: set[int],
    cleanup: object,
) -> list[str]:
    """Cross-bind every independent PID source to the terminal proof."""

    failures: list[str] = []
    if not isinstance(terminal_process, dict):
        return ["terminal process evidence is not an object"]
    expected_terminal_keys = {
        "known_pid_count",
        "known_pids",
        "process_group",
        "process_group_members_after",
        "all_known_pids_gone",
    }
    if set(terminal_process) != expected_terminal_keys:
        failures.append("terminal process evidence has an invalid schema")
    rows = terminal_process.get("known_pids")
    if not isinstance(rows, list):
        return ["known PID rows are not a list"]
    allowed_sources = {
        "strace_root",
        "rank_handshake",
        "launcher_discovery",
        "descendant_scan",
        "process_group_scan",
        "strace_trace_file",
        "terminal_process_group_scan",
    }
    expected_row_keys = {
        "pid",
        "sources",
        "observed_start_time_ticks",
        "terminal_start_time_ticks",
        "terminal_state",
    }
    known: dict[int, dict] = {}
    row_order: list[int] = []
    for row in rows:
        if not isinstance(row, dict):
            failures.append("known PID row is not an object")
            continue
        if set(row) != expected_row_keys:
            failures.append("known PID row has an invalid schema")
        try:
            pid = int(row["pid"])
        except (KeyError, TypeError, ValueError):
            failures.append("known PID row has an invalid PID")
            continue
        if (
            not isinstance(row.get("pid"), int)
            or isinstance(row.get("pid"), bool)
            or pid <= 0
            or pid in known
        ):
            failures.append(f"known PID row is duplicate/invalid: {pid}")
        row_order.append(pid)
        known[pid] = row
        sources = row.get("sources")
        if not isinstance(sources, list) or not sources:
            failures.append(f"known PID {pid} source list is incomplete")
        elif any(not isinstance(source, str) or not source for source in sources):
            failures.append(f"known PID {pid} source list is invalid")
        elif sources != sorted(set(sources)) or not set(sources) <= allowed_sources:
            failures.append(f"known PID {pid} source list is invalid")
        observed = row.get("observed_start_time_ticks")
        terminal = row.get("terminal_start_time_ticks")
        observed_valid = observed is None or (
            isinstance(observed, int)
            and not isinstance(observed, bool)
            and observed > 0
        )
        state = row.get("terminal_state")
        if not observed_valid:
            failures.append(f"known PID {pid} observed start time is invalid")
        if state == "gone":
            state_valid = terminal is None
        elif state == "pid_reused_original_gone":
            state_valid = (
                isinstance(observed, int)
                and not isinstance(observed, bool)
                and observed > 0
                and isinstance(terminal, int)
                and not isinstance(terminal, bool)
                and terminal > 0
                and terminal != observed
            )
        else:
            state_valid = False
        if not state_valid:
            failures.append(f"known PID {pid} terminal row is incomplete")
    if row_order != sorted(set(row_order)):
        failures.append("known PID rows are not strictly PID-sorted and unique")
    known_pid_count = terminal_process.get("known_pid_count")
    if (
        not isinstance(known_pid_count, int)
        or isinstance(known_pid_count, bool)
        or known_pid_count != len(rows)
    ):
        failures.append("known PID count differs from rows")
    if terminal_process.get("all_known_pids_gone") is not True:
        failures.append("known PID aggregate is not terminal")
    if terminal_process.get("process_group_members_after") != []:
        failures.append("terminal process group still has members")

    cleanup_pids: set[int] = set()
    if isinstance(cleanup, dict):
        for key in ("members_before_cleanup", "tracee_pids_before_cleanup"):
            values = cleanup.get(key, [])
            if isinstance(values, list):
                try:
                    cleanup_pids.update(int(value) for value in values)
                except (TypeError, ValueError):
                    failures.append(f"cleanup PID list is invalid: {key}")
            else:
                failures.append(f"cleanup PID list is invalid: {key}")
    else:
        failures.append("process cleanup evidence is not an object")

    required = set(trace_pids) | set(rank_pids.values()) | process_pids | cleanup_pids
    if isinstance(launcher_pid, int):
        required.add(launcher_pid)
    missing = sorted(required - set(known))
    if missing:
        failures.append(f"cross-channel PIDs missing from terminal proof: {missing}")
    for pid in trace_pids:
        if pid in known and "strace_trace_file" not in known[pid].get("sources", []):
            failures.append(f"trace PID {pid} lacks strace source binding")
    for pid in rank_pids.values():
        if pid in known and "rank_handshake" not in known[pid].get("sources", []):
            failures.append(f"rank PID {pid} lacks handshake source binding")
    if isinstance(launcher_pid, int) and launcher_pid in known:
        if "launcher_discovery" not in known[launcher_pid].get("sources", []):
            failures.append(f"launcher PID {launcher_pid} lacks discovery source binding")
    target_pids = set(rank_pids.values()) | process_pids
    if isinstance(launcher_pid, int):
        target_pids.add(launcher_pid)
    untraced_targets = sorted(target_pids - trace_pids)
    if untraced_targets:
        failures.append(
            f"launcher/rank/process PIDs lack raw trace files: {untraced_targets}"
        )
    process_group = terminal_process.get("process_group")
    if (
        not isinstance(process_group, int)
        or isinstance(process_group, bool)
        or process_group <= 0
    ):
        failures.append("strace process-group root is invalid")
    elif (
        process_group not in known
        or "strace_root" not in known[process_group].get("sources", [])
    ):
        failures.append("strace process-group root lacks terminal source binding")
    source_pids = {
        source: {
            pid
            for pid, row in known.items()
            if isinstance(row.get("sources"), list)
            and source in row.get("sources", [])
        }
        for source in (
            "strace_root",
            "launcher_discovery",
            "rank_handshake",
            "strace_trace_file",
        )
    }
    expected_source_pids = {
        "strace_root": (
            {process_group}
            if isinstance(process_group, int)
            and not isinstance(process_group, bool)
            and process_group > 0
            else set()
        ),
        "launcher_discovery": (
            {launcher_pid}
            if isinstance(launcher_pid, int)
            and not isinstance(launcher_pid, bool)
            and launcher_pid > 0
            else set()
        ),
        "rank_handshake": set(rank_pids.values()),
        "strace_trace_file": set(trace_pids),
    }
    for source, expected_pids in expected_source_pids.items():
        if source_pids[source] != expected_pids:
            failures.append(
                f"known PID source binding differs for {source}: "
                f"{sorted(source_pids[source])} != {sorted(expected_pids)}"
            )
    return failures


def _runtime_pid_role_contract_failures(
    launcher_pid: object,
    rank_pids: dict[int, int],
    launcher_rows: list[dict],
    rank_rows: list[dict],
    expected_rank_count: int,
) -> list[str]:
    """Require handshake and mapped-process evidence to identify identical PIDs."""

    failures: list[str] = []
    valid_launcher_pid = (
        isinstance(launcher_pid, int)
        and not isinstance(launcher_pid, bool)
        and launcher_pid > 0
    )
    if not valid_launcher_pid:
        failures.append("launcher handshake PID is invalid")

    expected_ranks = set(range(expected_rank_count))
    if set(rank_pids) != expected_ranks:
        failures.append("rank handshake PID map does not cover the frozen ranks")
    elif any(
        not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0
        for pid in rank_pids.values()
    ):
        failures.append("rank handshake PID map contains an invalid PID")
    elif len(set(rank_pids.values())) != expected_rank_count:
        failures.append("rank handshake PID map contains duplicate PIDs")
    elif valid_launcher_pid and launcher_pid in rank_pids.values():
        failures.append("launcher and rank handshake PIDs overlap")

    if len(launcher_rows) != 1:
        failures.append("mapped-process evidence does not contain one launcher")
    else:
        launcher_row = launcher_rows[0]
        row_pid = launcher_row.get("pid")
        if (
            not isinstance(row_pid, int)
            or isinstance(row_pid, bool)
            or launcher_row.get("rank") is not None
            or not valid_launcher_pid
            or row_pid != launcher_pid
        ):
            failures.append("mapped launcher PID differs from launcher handshake")

    mapped_rank_pids: dict[int, int] = {}
    mapped_rank_rows_valid = len(rank_rows) == expected_rank_count
    for row in rank_rows:
        rank = row.get("rank")
        pid = row.get("pid")
        if (
            not isinstance(rank, int)
            or isinstance(rank, bool)
            or not isinstance(pid, int)
            or isinstance(pid, bool)
            or pid <= 0
            or rank in mapped_rank_pids
        ):
            mapped_rank_rows_valid = False
            continue
        mapped_rank_pids[rank] = pid
    if not mapped_rank_rows_valid or mapped_rank_pids != rank_pids:
        failures.append("mapped rank PID map differs from rank handshakes")
    return failures


def _trace_file_pid_contract(
    trace_paths: list[Path],
) -> tuple[dict[int, Path], list[str]]:
    """Parse only canonical, regular ``trace.<positive-int>`` evidence files."""

    trace_files: dict[int, Path] = {}
    failures: list[str] = []
    for path in trace_paths:
        suffix = path.name.removeprefix("trace.")
        try:
            pid = int(suffix)
        except ValueError:
            pid = -1
        if (
            not path.name.startswith("trace.")
            or pid <= 0
            or suffix != str(pid)
            or not path.is_file()
            or path.is_symlink()
        ):
            failures.append(f"invalid raw trace artifact: {path.name}")
            continue
        if pid in trace_files:
            failures.append(f"duplicate raw trace PID: {pid}")
            continue
        trace_files[pid] = path
    if not trace_files:
        failures.append("raw trace PID set is empty")
    return trace_files, failures


def _trace_directory_contract(
    trace_directory: Path,
) -> tuple[dict[int, Path], list[str]]:
    """Enumerate every direct child so a renamed trace cannot evade validation."""

    if (
        not trace_directory.is_dir()
        or trace_directory.is_symlink()
    ):
        return {}, ["raw trace directory is missing, symbolic, or not a directory"]
    try:
        entries = sorted(trace_directory.iterdir())
    except OSError as error:
        return {}, [f"cannot enumerate raw trace directory: {error}"]
    return _trace_file_pid_contract(entries)


def _accessible_host_scan_contract_matches(scan: object) -> bool:
    """Validate the exact auxiliary host PID-namespace scan schema."""

    expected_keys = {
        "schema_version",
        "pid_namespace_inode",
        "accessible_matching_members",
        "accessible_matching_member_count",
        "inaccessible_pid_count",
        "inaccessible_pid_samples",
        "fatal_errors",
        "no_accessible_matching_members",
        "scan_passed",
    }
    if not isinstance(scan, dict) or set(scan) != expected_keys:
        return False
    count = scan.get("inaccessible_pid_count")
    samples = scan.get("inaccessible_pid_samples")
    if (
        scan.get("schema_version") != 1
        or scan.get("accessible_matching_members") != []
        or scan.get("accessible_matching_member_count") != 0
        or scan.get("fatal_errors") != []
        or scan.get("no_accessible_matching_members") is not True
        or scan.get("scan_passed") is not True
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
        or not isinstance(samples, list)
        or len(samples) != min(count, 20)
    ):
        return False
    sample_pids: list[int] = []
    for row in samples:
        if (
            not isinstance(row, dict)
            or set(row) != {"host_pid", "error"}
            or not isinstance(row.get("host_pid"), int)
            or isinstance(row.get("host_pid"), bool)
            or row.get("host_pid", 0) <= 0
            or not isinstance(row.get("error"), str)
            or not row.get("error")
        ):
            return False
        sample_pids.append(row["host_pid"])
    return sample_pids == sorted(set(sample_pids))


def _rank_incoming_environment_contract_matches(
    evidence: object,
    recovery_prefix: str,
    recovery_library: str,
) -> bool:
    """Accept only PRRTE's known unset/duplicate transformations before normalization."""

    expected_keys = {
        "schema_version",
        "prefix_environment",
        "ld_library_path_raw",
        "ld_library_path_entries",
        "cuda_cache_disable",
        "normalization_action",
    }
    if not isinstance(evidence, dict) or set(evidence) != expected_keys:
        return False
    prefixes = evidence.get("prefix_environment")
    expected_prefix_keys = {
        "OPAL_PREFIX",
        "PRTE_PREFIX",
        "PMIX_PREFIX",
        "UCX_MODULE_DIR",
    }
    if not isinstance(prefixes, dict) or set(prefixes) != expected_prefix_keys:
        return False
    if any(
        prefixes.get(key) != recovery_prefix
        for key in ("OPAL_PREFIX", "PMIX_PREFIX", "UCX_MODULE_DIR")
    ) or prefixes.get("PRTE_PREFIX") not in (None, "", recovery_prefix):
        return False
    raw = evidence.get("ld_library_path_raw")
    entries = evidence.get("ld_library_path_entries")
    return (
        evidence.get("schema_version") == 1
        and isinstance(raw, str)
        and bool(raw)
        and isinstance(entries, list)
        and bool(entries)
        and entries == raw.split(":")
        and all(entry == recovery_library for entry in entries)
        and evidence.get("cuda_cache_disable") == "1"
        and evidence.get("normalization_action")
        == "restore_four_recovery_prefixes_and_collapse_identical_library_entries"
    )


def _controlled_home_namespace_contract_matches(
    state: object,
    expected_home: str,
    raw_mountinfo_lines: list[str],
    *,
    require_readonly_mount: bool,
    expected_lstat: object = None,
) -> bool:
    """Bind the marker-only HOME policy to raw private-namespace mountinfo."""

    if not isinstance(state, dict):
        return False
    lstat = state.get("controlled_home_lstat")
    home_lines = state.get("controlled_home_mountinfo_lines")
    recomputed = [
        raw
        for raw in raw_mountinfo_lines
        if len(raw.split()) >= 5 and raw.split()[4] == expected_home
    ]
    base_ok = (
        state.get("controlled_home") == expected_home
        and state.get("controlled_home_exists") is True
        and isinstance(lstat, dict)
        and lstat.get("is_symlink") is False
        and state.get("controlled_home_entries") == ["CONTROLLED_HOME.txt"]
        and state.get("controlled_home_marker_sha256")
        == "eff12e20044f5411f511d7f32f76fd51dc0cd96b30863381f8e4916d5dd48ab0"
        and isinstance(home_lines, list)
        and home_lines == recomputed
    )
    if not base_ok:
        return False
    if not require_readonly_mount:
        return home_lines == [] and lstat.get("mode", 0) & 0o777 == 0o500
    if expected_lstat is not None and lstat != expected_lstat:
        return False
    if len(home_lines) != 1:
        return False
    fields = home_lines[0].split()
    options = set(fields[5].split(",")) if len(fields) > 5 else set()
    return {"ro", "nosuid", "nodev", "noexec"}.issubset(options)


def _pid1_kernel_reap_contract_matches(
    proof: object,
    namespace_inode: object,
    expected_host_command: list[str],
    expected_unshare_argv: list[str],
    expected_payload_argv: list[str],
) -> bool:
    """Bind the kernel-reap claim to the raw PID-namespace evidence."""

    expected = {
        "schema_version": 1,
        "authority": "linux_pid_namespace_init_exit_and_unshare_parent_wait",
        "expected_unshare_argv": expected_unshare_argv,
        "expected_payload_argv": expected_payload_argv,
        "observed_command": expected_host_command,
        "command_contract_satisfied": True,
        "process_wait_completed_normally": True,
        "process_wait_exit_code": 0,
        "process_wait_exit_zero": True,
        "state_before_mount": {
            "namespace_init_pid": 1,
            "pid_namespace_inode": namespace_inode,
        },
        "state_after_run": {
            "namespace_init_pid": 1,
            "pid_namespace_inode": namespace_inode,
        },
        "payload_status": {
            "status": "accepted",
            "audit_launcher_exit_code": 0,
            "pid_namespace_inode": namespace_inode,
        },
        "pid1_is_one": True,
        "pid_namespace_inode": namespace_inode,
        "pid_namespace_inode_consistent": True,
        "payload_accepted_and_exit_zero": True,
        "evidence_errors": [],
        "all_namespace_members_reaped": True,
    }
    return proof == expected


def _expect_keys(payload: dict, expected: set[str], label: str, errors: list[str]) -> None:
    actual = set(payload)
    if actual != expected:
        errors.append(
            f"{label} keys differ: missing={sorted(expected - actual)} "
            f"unexpected={sorted(actual - expected)}"
        )


def _check_hash(path: Path, expected: str, label: str, errors: list[str]) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", str(expected)):
        errors.append(f"{label}: invalid registered SHA-256")
    elif not path.is_file() or path.is_symlink():
        errors.append(f"{label}: missing or symbolic-link file {path}")
    elif sha256(path) != expected:
        errors.append(f"{label}: SHA-256 mismatch")


def _check_identity(
    identity: object,
    label: str,
    errors: list[str],
    *,
    require_elf: bool = True,
) -> dict:
    if not isinstance(identity, dict):
        errors.append(f"{label}: identity must be an object")
        return {}
    if set(identity) != {"path", "realpath", "sha256"}:
        errors.append(f"{label}: identity keys differ")
        return identity
    try:
        actual = file_identity(Path(str(identity["path"])), label, require_elf=require_elf)
    except ValueError as error:
        errors.append(str(error))
        return identity
    if actual != identity:
        errors.append(f"{label}: path/realpath/SHA-256 identity mismatch")
    return identity


def _check_versioned_tool(identity: object, label: str, errors: list[str]) -> dict:
    if not isinstance(identity, dict):
        errors.append(f"{label}: tool identity must be an object")
        return {}
    expected_keys = {
        "path",
        "realpath",
        "sha256",
        "version_arguments",
        "version_first_line",
        "version_output_sha256",
    }
    if set(identity) != expected_keys or identity.get("version_arguments") != ["--version"]:
        errors.append(f"{label}: versioned tool identity fields differ")
        return identity
    try:
        actual = versioned_tool_identity(Path(str(identity["path"])), label)
    except ValueError as error:
        errors.append(str(error))
        return identity
    if actual != identity:
        errors.append(f"{label}: frozen tool identity/version mismatch")
    return identity


def _lstat_identity(path: Path) -> dict | None:
    try:
        value = path.lstat()
    except FileNotFoundError:
        return None
    return {
        "device": value.st_dev,
        "gid": value.st_gid,
        "inode": value.st_ino,
        "mode": value.st_mode,
        "mode_type": stat.S_IFMT(value.st_mode),
        "mtime_ns": value.st_mtime_ns,
        "size": value.st_size,
        "uid": value.st_uid,
        "is_symlink": path.is_symlink(),
        "realpath": str(path.resolve(strict=False)),
    }


def _integer_fields(value: object) -> list[int]:
    try:
        return [int(item) for item in str(value).split()]
    except ValueError:
        return []


def _timing_evidence_failures(payload: dict, timeout: float, label: str) -> list[str]:
    failures: list[str] = []
    numeric = {}
    for key in ("started_epoch_seconds", "ended_epoch_seconds", "elapsed_seconds"):
        value = payload.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            failures.append(f"{label} {key} is not numeric")
        else:
            numeric[key] = float(value)
    for key in ("started_at_utc", "ended_at_utc"):
        value = payload.get(key)
        try:
            parsed = datetime.fromisoformat(str(value))
            if parsed.tzinfo is None:
                raise ValueError("timezone missing")
        except ValueError:
            failures.append(f"{label} {key} is not a timezone-aware ISO timestamp")
    if len(numeric) == 3:
        wall_elapsed = numeric["ended_epoch_seconds"] - numeric["started_epoch_seconds"]
        monotonic_elapsed = numeric["elapsed_seconds"]
        if wall_elapsed < 0 or monotonic_elapsed < 0:
            failures.append(f"{label} elapsed time is negative")
        if abs(wall_elapsed - monotonic_elapsed) > max(1.0, monotonic_elapsed * 0.01):
            failures.append(f"{label} wall/monotonic elapsed evidence is inconsistent")
        if monotonic_elapsed > timeout:
            failures.append(f"{label} elapsed time exceeds absolute deadline")
    return failures


def _git_bytes(project_root: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(project_root), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"git {' '.join(arguments)} failed: {detail}")
    return completed.stdout


def _git_tree_entries(
    project_root: Path, commit: str, prefix: str
) -> dict[str, tuple[str, str, str]]:
    """Return the complete tracked leaf collection below *prefix* at *commit*.

    Git does not track empty directories.  Every tracked leaf is nevertheless
    represented by its relative path, mode (which distinguishes regular files,
    executables, symlinks, and gitlinks), object type, and object id.  Comparing
    this mapping therefore rejects missing/extra paths and type changes as well
    as byte changes.
    """

    normalized = prefix.rstrip("/")
    output = _git_bytes(
        project_root,
        "ls-tree",
        "-r",
        "-z",
        commit,
        "--",
        normalized,
    )
    entries: dict[str, tuple[str, str, str]] = {}
    for raw in output.split(b"\0"):
        if not raw:
            continue
        try:
            metadata, raw_path = raw.split(b"\t", 1)
            mode, object_type, object_id = metadata.decode("ascii").split(" ", 2)
            path = raw_path.decode("utf-8", errors="surrogateescape")
        except ValueError as error:
            raise ValueError(f"cannot parse git tree entry below {normalized}") from error
        expected_prefix = normalized + "/"
        if not path.startswith(expected_prefix):
            raise ValueError(f"git tree path escaped prefix {normalized}: {path}")
        suffix = path[len(expected_prefix) :]
        if not suffix or suffix in entries:
            raise ValueError(f"duplicate/empty git tree suffix below {normalized}: {suffix}")
        entries[suffix] = (mode, object_type, object_id)
    return entries


def _preregistration_commit(
    project_root: Path, config_path: Path, manifest_path: Path
) -> str:
    relative_paths = []
    for path in (config_path, manifest_path):
        try:
            relative_paths.append(path.relative_to(project_root).as_posix())
        except ValueError as error:
            raise ValueError(f"formal frozen file is outside project: {path}") from error
    commits = []
    for relative in relative_paths:
        output = _git_bytes(
            project_root,
            "log",
            "--diff-filter=A",
            "--format=%H",
            "--",
            relative,
        ).decode("ascii")
        values = [line for line in output.splitlines() if line]
        if len(values) != 1:
            raise ValueError(
                f"expected exactly one preregistration addition commit for {relative}, "
                f"found {len(values)}"
            )
        commits.append(values[0])
    if len(set(commits)) != 1:
        raise ValueError("config and manifest were not added in one preregistration commit")
    commit = commits[0]
    for relative, path in zip(relative_paths, (config_path, manifest_path)):
        frozen = _git_bytes(project_root, "cat-file", "blob", f"{commit}:{relative}")
        if frozen != path.read_bytes():
            raise ValueError(
                f"{relative} differs byte-for-byte from preregistration commit {commit}"
            )
    return commit


def _run_commit_chain_failure(
    project_root: Path, experiment_id: str, code_commit: object
) -> str | None:
    if not isinstance(code_commit, str) or not re.fullmatch(r"[0-9a-f]{40,64}", code_commit):
        return f"{experiment_id}: invalid code_commit provenance"
    relative = f"runs/{experiment_id}/experiment_metadata.json"
    output = _git_bytes(
        project_root,
        "log",
        "--no-renames",
        "--diff-filter=A",
        "--format=%H",
        "--",
        relative,
    ).decode("ascii")
    additions = [line for line in output.splitlines() if line]
    if not additions:
        return f"{experiment_id}: current run has no addition commit"
    current_addition = additions[0]
    current_blob = _git_bytes(
        project_root, "cat-file", "blob", f"HEAD:{relative}"
    )
    addition_blob = _git_bytes(
        project_root, "cat-file", "blob", f"{current_addition}:{relative}"
    )
    if current_blob != addition_blob:
        return f"{experiment_id}: current metadata differs from latest run introduction"
    parent = _git_bytes(project_root, "rev-parse", f"{current_addition}^").decode("ascii").strip()
    if parent != code_commit:
        return (
            f"{experiment_id}: experiment code_commit {code_commit} is not the run "
            f"commit parent {parent}"
        )
    ancestor = subprocess.run(
        ["git", "-C", str(project_root), "merge-base", "--is-ancestor", code_commit, "HEAD"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if ancestor.returncode != 0:
        return f"{experiment_id}: code_commit is not an ancestor of HEAD"
    return None


def _failed_archive_chain_failures(project_root: Path, experiment_id: str) -> list[str]:
    """Validate every preserved failed attempt independently of a same-ID retry."""

    failures: list[str] = []
    archive_root = project_root / "failed_runs" / "runtime_relocation" / experiment_id
    if not archive_root.exists():
        return failures
    for attempt in sorted(archive_root.glob("attempt-*")):
        prefix = f"{experiment_id}:{attempt.name}:"
        if not attempt.is_dir() or attempt.is_symlink():
            failures.append(f"{prefix} invalid archive directory")
            continue
        match = re.fullmatch(r"attempt-([0-9a-f]{12})", attempt.name)
        if match is None:
            failures.append(f"{prefix} invalid failure-commit suffix")
            continue
        try:
            failure_commit = _git_bytes(
                project_root, "rev-parse", f"{match.group(1)}^{{commit}}"
            ).decode("ascii").strip()
            metadata_path = attempt / "experiment_metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            code_commit = metadata.get("code_commit")
            failure_parent = _git_bytes(
                project_root, "rev-parse", f"{failure_commit}^"
            ).decode("ascii").strip()
            if failure_parent != code_commit:
                failures.append(f"{prefix} failed-run commit parent/code_commit mismatch")
            archived_relative = metadata_path.relative_to(project_root).as_posix()
            additions = _git_bytes(
                project_root,
                "log",
                "--no-renames",
                "--diff-filter=A",
                "--format=%H",
                "--",
                archived_relative,
            ).decode("ascii").splitlines()
            if not additions:
                failures.append(f"{prefix} archive has no introduction commit")
                continue
            archive_commit = additions[0]
            archive_parent = _git_bytes(
                project_root, "rev-parse", f"{archive_commit}^"
            ).decode("ascii").strip()
            if archive_parent != failure_commit:
                failures.append(f"{prefix} archive commit does not immediately follow failure")
            failure_entries = _git_tree_entries(
                project_root, failure_commit, f"runs/{experiment_id}"
            )
            archived_entries = _git_tree_entries(
                project_root, archive_commit, attempt.relative_to(project_root).as_posix()
            )
            head_entries = _git_tree_entries(
                project_root, "HEAD", attempt.relative_to(project_root).as_posix()
            )
            archive_worktree_changes = _git_bytes(
                project_root,
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--",
                attempt.relative_to(project_root).as_posix(),
            )
            if not failure_entries:
                failures.append(f"{prefix} failed-run commit has an empty tracked tree")
            if archived_entries != failure_entries:
                missing = sorted(set(failure_entries) - set(archived_entries))
                added = sorted(set(archived_entries) - set(failure_entries))
                changed = sorted(
                    suffix
                    for suffix in set(failure_entries) & set(archived_entries)
                    if failure_entries[suffix] != archived_entries[suffix]
                )
                failures.append(
                    f"{prefix} archive tree differs from failed run: "
                    f"missing={missing} added={added} mode_type_or_blob_changed={changed}"
                )
            if head_entries != archived_entries:
                failures.append(f"{prefix} current archive tree differs from archive commit")
            if archive_worktree_changes:
                failures.append(f"{prefix} current archive worktree differs from HEAD")
        except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError) as error:
            failures.append(f"{prefix} archive chain validation failed: {error}")
    return failures


def _validate_runtime_audit_evidence(
    run_directory: Path,
    runtime: dict,
    audit_spec: dict,
    audit: dict,
    errors: list[str],
    prefix: str,
) -> list[Path]:
    objects_path = run_directory / "mpi_runtime_audit" / "objects.tsv"
    trace_directory = run_directory / "mpi_runtime_audit" / "strace"
    evidence_paths = [objects_path]
    try:
        with objects_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            if tuple(reader.fieldnames or ()) != OBJECT_HEADER:
                errors.append(f"{prefix} invalid runtime object TSV header")
                object_rows = []
            else:
                object_rows = list(reader)
    except FileNotFoundError:
        errors.append(f"{prefix} missing runtime object TSV")
        object_rows = []

    processes = audit.get("processes")
    if not isinstance(processes, list):
        errors.append(f"{prefix} runtime audit processes must be a list")
        processes = []
    process_by_pid: dict[int, dict] = {}
    valid_processes = []
    for process in processes:
        if not isinstance(process, dict):
            errors.append(f"{prefix} runtime process record must be an object")
            continue
        valid_processes.append(process)
        try:
            pid = int(process["pid"])
            role = str(process["role"])
            rank = process.get("rank")
            rank = None if rank is None else int(rank)
            executable = Path(process["executable_realpath"])
            executable_hash = str(process["executable_sha256"])
        except (KeyError, TypeError, ValueError) as error:
            errors.append(f"{prefix} invalid runtime process record: {error}")
            continue
        if pid in process_by_pid:
            errors.append(f"{prefix} duplicate runtime process PID {pid}")
        process_by_pid[pid] = process
        if role not in {"launcher", "rank", "support"}:
            errors.append(f"{prefix} invalid runtime process role {role}")
        _check_hash(executable, executable_hash, f"{prefix} process {pid} executable", errors)
        if role == "launcher" and not _same_path(
            executable, Path(runtime["launcher_path"])
        ):
            errors.append(f"{prefix} launcher executable is not frozen final launcher")
        if role == "rank" and not _same_path(executable, Path(runtime["abacus_path"])):
            errors.append(f"{prefix} rank {rank} executable is not frozen ABACUS")
    launcher_rows = [row for row in valid_processes if row.get("role") == "launcher"]
    rank_rows = [row for row in valid_processes if row.get("role") == "rank"]
    try:
        rank_values = sorted(int(row["rank"]) for row in rank_rows)
    except (KeyError, TypeError, ValueError):
        rank_values = []
    if len(launcher_rows) != 1:
        errors.append(f"{prefix} raw process evidence does not contain one launcher")
    if len(rank_rows) != audit_spec["rank_count"] or rank_values != list(
        range(audit_spec["rank_count"])
    ):
        errors.append(f"{prefix} raw process evidence does not contain exactly four ranks")
    for process in [*launcher_rows, *rank_rows]:
        if int(process.get("mapped_object_count", 0)) <= 0:
            errors.append(
                f"{prefix} target process {process.get('pid')} has no captured maps"
            )

    seen_objects: set[tuple[int, str]] = set()
    recomputed_counts = {"old_prefix": 0, "unexpected": 0, "transient_system": 0}
    object_counts_by_pid: dict[int, int] = {}
    for row in object_rows:
        try:
            pid = int(row["pid"])
            mapped = Path(row["mapped_path"])
            loaded = Path(row["loaded_realpath"])
        except (KeyError, TypeError, ValueError) as error:
            errors.append(f"{prefix} invalid mapped-object record: {error}")
            continue
        key = (pid, str(mapped))
        if key in seen_objects:
            errors.append(f"{prefix} duplicate mapped-object record {key}")
        seen_objects.add(key)
        object_counts_by_pid[pid] = object_counts_by_pid.get(pid, 0) + 1
        process = process_by_pid.get(pid)
        if process is None:
            errors.append(f"{prefix} mapped object refers to unknown PID {pid}")
        else:
            expected_rank = "" if process.get("rank") is None else str(process.get("rank"))
            if row.get("role") != str(process.get("role")) or row.get("rank", "") != expected_rank:
                errors.append(f"{prefix} mapped object PID {pid} role/rank mismatch")
        expected_loaded = mapped.resolve(strict=False)
        if loaded != expected_loaded:
            errors.append(f"{prefix} mapped object realpath mismatch: {mapped}")
        classification = classify_mapping(
            mapped,
            loaded,
            Path(runtime["old_prefix"]),
            Path(runtime["recovery_root"]),
            [Path(value) for value in audit_spec["system_mapping_roots"]],
        )
        if row.get("classification") != classification:
            errors.append(f"{prefix} mapped object classification mismatch: {mapped}")
        if classification in recomputed_counts:
            recomputed_counts[classification] += 1
        if classification == "recovery_runtime" and not row.get("loaded_sha256"):
            errors.append(f"{prefix} recovery mapping lacks SHA-256: {loaded}")
        if loaded.is_file() and classification != "transient_system":
            registered_hash = row.get("loaded_sha256", "")
            if not re.fullmatch(r"[0-9a-f]{64}", registered_hash):
                errors.append(f"{prefix} mapped regular file lacks SHA-256: {loaded}")
            elif sha256(loaded) != registered_hash:
                errors.append(f"{prefix} mapped object SHA-256 mismatch: {loaded}")
    for pid, process in process_by_pid.items():
        if process.get("mapped_object_count") != object_counts_by_pid.get(pid, 0):
            errors.append(f"{prefix} process {pid} mapped-object count differs from TSV")
    for process in [*launcher_rows, *rank_rows]:
        executable = Path(str(process.get("executable_realpath", ""))).resolve(
            strict=False
        )
        if not any(
            row.get("pid") == str(process.get("pid"))
            and Path(row.get("loaded_realpath", "")).resolve(strict=False) == executable
            for row in object_rows
        ):
            errors.append(
                f"{prefix} target process {process.get('pid')} executable map was not captured"
            )
    if len(object_rows) != audit.get("mapped_object_count"):
        errors.append(f"{prefix} mapped object count differs from TSV")
    if recomputed_counts["old_prefix"] != audit.get("old_prefix_mapped_object_count"):
        errors.append(f"{prefix} old-prefix mapping count differs from TSV")
    if recomputed_counts["unexpected"] != audit.get("unexpected_mapped_object_count"):
        errors.append(f"{prefix} unexpected mapping count differs from TSV")
    if recomputed_counts["transient_system"] != audit.get(
        "transient_system_mapped_object_count"
    ):
        errors.append(f"{prefix} transient-system mapping count differs from TSV")

    traces = sorted(trace_directory.glob("trace*"))
    evidence_paths.extend(path for path in traces if path.is_file())
    trace_lines: list[str] = []
    for path in traces:
        trace_lines.extend(
            path.read_text(encoding="utf-8", errors="replace").splitlines()
        )
    reparsed = parse_strace_lines(
        trace_lines,
        Path(runtime["old_prefix"]),
        Path(audit_spec["allowed_failed_probe_path"]),
        audit_spec["allowed_failed_probe_errno"],
    )
    for key, value in reparsed.items():
        if audit.get(key) != value:
            errors.append(f"{prefix} runtime audit {key} differs from raw strace")
    execve_paths = parse_execve_paths(trace_lines)
    if audit.get("observed_execve_realpaths") != execve_paths:
        errors.append(f"{prefix} runtime audit execve paths differ from raw strace")
    invocation_observed = str(Path(runtime["mpirun_path"]).resolve()) in execve_paths
    launcher_observed = str(Path(runtime["launcher_path"]).resolve()) in execve_paths
    if audit.get("mpirun_invocation_execve_observed") != invocation_observed:
        errors.append(f"{prefix} mpirun invocation execve claim differs from raw strace")
    if audit.get("launcher_execve_observed") != launcher_observed:
        errors.append(f"{prefix} launcher execve claim differs from raw strace")
    command = audit.get("command")
    if not isinstance(command, list) or not command or not _same_path(
        Path(str(command[0])), Path(runtime["mpirun_path"])
    ):
        errors.append(f"{prefix} runtime command did not invoke frozen mpirun")
    return evidence_paths


def _validate_runtime_relocation_audit_evidence(
    run_directory: Path,
    runtime: dict,
    audit_spec: dict,
    audit: dict,
    errors: list[str],
    prefix: str,
) -> list[Path]:
    audit_directory = run_directory / "mpi_runtime_audit"
    objects_path = audit_directory / "objects.tsv"
    trace_directory = audit_directory / "strace"
    namespace_directory = audit_directory / "namespace"
    evidence_paths = [objects_path]
    try:
        with objects_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            if tuple(reader.fieldnames or ()) != OBJECT_HEADER:
                errors.append(f"{prefix} invalid runtime object TSV header")
                object_rows = []
            else:
                object_rows = list(reader)
    except FileNotFoundError:
        errors.append(f"{prefix} missing runtime object TSV")
        object_rows = []

    replay = runtime["replay"]
    expected_executables = {
        "launcher": replay["launcher"],
        "rank": replay["abacus"],
    }
    processes = audit.get("processes")
    if not isinstance(processes, list):
        errors.append(f"{prefix} runtime audit processes must be a list")
        processes = []
    process_by_pid: dict[int, dict] = {}
    launcher_rows = []
    rank_rows = []
    for process in processes:
        if not isinstance(process, dict):
            errors.append(f"{prefix} runtime process record must be an object")
            continue
        try:
            pid = int(process["pid"])
            role = str(process["role"])
            rank = process.get("rank")
            rank = None if rank is None else int(rank)
        except (KeyError, TypeError, ValueError) as error:
            errors.append(f"{prefix} invalid runtime process record: {error}")
            continue
        if pid in process_by_pid:
            errors.append(f"{prefix} duplicate runtime process PID {pid}")
        process_by_pid[pid] = process
        if role not in expected_executables:
            errors.append(f"{prefix} unexpected target process role {role}")
            continue
        expected = expected_executables[role]
        if not _same_path(
            Path(str(process.get("executable_realpath", ""))),
            Path(expected["realpath"]),
        ):
            errors.append(f"{prefix} {role} executable realpath mismatch")
        if process.get("executable_sha256") != expected["sha256"]:
            errors.append(f"{prefix} {role} executable SHA-256 mismatch")
        if not process.get("initial_map_capture_observed"):
            errors.append(f"{prefix} {role} PID {pid} lacks deterministic initial maps")
        if int(process.get("mapped_object_count", 0)) <= 0:
            errors.append(f"{prefix} {role} PID {pid} has no captured mappings")
        if role == "launcher":
            launcher_rows.append(process)
        else:
            rank_rows.append(process)
    try:
        rank_values = sorted(int(row["rank"]) for row in rank_rows)
    except (KeyError, TypeError, ValueError):
        rank_values = []
    if len(launcher_rows) != 1:
        errors.append(f"{prefix} raw process evidence does not contain one launcher")
    if rank_values != list(range(audit_spec["rank_count"])):
        errors.append(f"{prefix} raw process evidence does not contain ranks 0..3")

    seen_objects: set[tuple[int, str]] = set()
    counts = {
        "old_prefix": 0,
        "unexpected": 0,
        "transient_system": 0,
        "registered_device": 0,
    }
    counts_by_pid: dict[int, int] = {}
    for row in object_rows:
        try:
            pid = int(row["pid"])
            mapped = Path(row["mapped_path"])
            loaded = Path(row["loaded_realpath"])
        except (KeyError, TypeError, ValueError) as error:
            errors.append(f"{prefix} invalid mapped object record: {error}")
            continue
        key = (pid, str(mapped))
        if key in seen_objects:
            errors.append(f"{prefix} duplicate mapped object record {key}")
        seen_objects.add(key)
        counts_by_pid[pid] = counts_by_pid.get(pid, 0) + 1
        process = process_by_pid.get(pid)
        if process is None:
            errors.append(f"{prefix} mapped object refers to unknown PID {pid}")
        else:
            expected_rank = "" if process.get("rank") is None else str(process.get("rank"))
            if row.get("role") != process.get("role") or row.get("rank", "") != expected_rank:
                errors.append(f"{prefix} mapped object PID {pid} role/rank mismatch")
        expected_loaded = mapped.resolve(strict=False)
        if loaded != expected_loaded:
            errors.append(f"{prefix} mapped object realpath mismatch: {mapped}")
        classification = classify_mapping(
            mapped,
            loaded,
            Path(runtime["old_prefix"]),
            Path(runtime["recovery_root"]),
            tuple(audit_spec["system_mapping_roots"]),
            tuple(audit_spec["system_mapping_exact_paths"]),
            tuple(audit_spec["registered_device_mapping_patterns"]),
            tuple(audit_spec["transient_mapping_patterns"]),
        )
        if row.get("classification") != classification:
            errors.append(f"{prefix} mapped object classification mismatch: {mapped}")
        if classification in counts:
            counts[classification] += 1
        if loaded.is_file() and classification != "transient_system":
            registered_hash = row.get("loaded_sha256", "")
            if not re.fullmatch(r"[0-9a-f]{64}", registered_hash):
                errors.append(f"{prefix} mapped regular file lacks SHA-256: {loaded}")
            elif sha256(loaded) != registered_hash:
                errors.append(f"{prefix} mapped regular file SHA-256 mismatch: {loaded}")
    for pid, process in process_by_pid.items():
        if process.get("mapped_object_count") != counts_by_pid.get(pid, 0):
            errors.append(f"{prefix} process {pid} mapped-object count differs from TSV")
    audit_count_fields = {
        "mapped_object_count": len(object_rows),
        "old_prefix_mapped_object_count": counts["old_prefix"],
        "unexpected_mapped_object_count": counts["unexpected"],
        "transient_system_mapped_object_count": counts["transient_system"],
        "registered_device_mapped_object_count": counts["registered_device"],
    }
    for key, expected in audit_count_fields.items():
        if audit.get(key) != expected:
            errors.append(f"{prefix} runtime audit {key} differs from object TSV")

    counterpart_path = audit_directory / "counterparts.tsv"
    counterpart_audit_path = audit_directory / "counterpart_audit.json"
    evidence_paths.extend([counterpart_path, counterpart_audit_path])
    try:
        with counterpart_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            if tuple(reader.fieldnames or ()) != COUNTERPART_HEADER:
                errors.append(f"{prefix} invalid counterpart TSV header")
                counterpart_rows = []
            else:
                counterpart_rows = list(reader)
    except FileNotFoundError:
        errors.append(f"{prefix} missing recovery counterpart TSV")
        counterpart_rows = []
    try:
        counterpart_audit = json.loads(counterpart_audit_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as error:
        errors.append(f"{prefix} invalid recovery counterpart audit: {error}")
        counterpart_audit = {}
    recovery_root = Path(runtime["recovery_root"]).resolve(strict=True)
    old_root = Path(runtime["old_root"]).resolve(strict=True)
    expected_recovery_paths = {
        str(Path(os.path.abspath(row["mapped_path"])))
        for row in object_rows
        if row.get("classification") == "recovery_runtime"
    }
    seen_recovery_paths: set[str] = set()
    missing_counterparts = 0
    mismatched_counterparts = 0
    exclusions = audit_spec["counterpart_exclusions"]
    exclusion_by_replay = {
        str(Path(spec["replay"]["realpath"]).resolve(strict=True)): (rule, spec)
        for rule, spec in exclusions.items()
    }
    for row in counterpart_rows:
        recovery_value = row.get("recovery_path", "")
        if recovery_value in seen_recovery_paths:
            errors.append(f"{prefix} duplicate counterpart row: {recovery_value}")
        seen_recovery_paths.add(recovery_value)
        recovery_path = Path(recovery_value)
        recovery_realpath = Path(row.get("recovery_realpath", ""))
        try:
            resolved_recovery = recovery_realpath.resolve(strict=True)
            try:
                relative = Path(os.path.abspath(recovery_path)).relative_to(recovery_root)
            except ValueError:
                relative = resolved_recovery.relative_to(recovery_root)
        except (FileNotFoundError, ValueError) as error:
            errors.append(f"{prefix} invalid recovery counterpart path {recovery_value}: {error}")
            continue
        recovery_sha = sha256(resolved_recovery)
        if row.get("recovery_realpath") != str(resolved_recovery):
            errors.append(f"{prefix} recovery counterpart realpath mismatch")
        if row.get("recovery_relative_path") != relative.as_posix():
            errors.append(f"{prefix} recovery counterpart relative path mismatch")
        if row.get("recovery_sha256") != recovery_sha:
            errors.append(f"{prefix} recovery counterpart SHA-256 mismatch")
        exclusion = exclusion_by_replay.get(str(resolved_recovery))
        if exclusion is not None:
            rule, spec = exclusion
            expected_old_path = Path(spec["reference"]["path"])
            expected_old_realpath = Path(spec["reference"]["realpath"]).resolve(
                strict=True
            )
        else:
            rule = "relative_path_counterpart_byte_equality"
            spec = None
            expected_old_path = old_root / relative
            try:
                expected_old_realpath = expected_old_path.resolve(strict=True)
            except FileNotFoundError:
                expected_old_realpath = expected_old_path.resolve(strict=False)
        if row.get("verification_rule") != rule:
            errors.append(f"{prefix} recovery counterpart verification rule mismatch")
        if row.get("old_counterpart_path") != str(expected_old_path):
            errors.append(f"{prefix} old counterpart relative-path mapping mismatch")
        if row.get("old_counterpart_realpath") != str(expected_old_realpath):
            errors.append(f"{prefix} old counterpart realpath mismatch")
        if not expected_old_realpath.is_file():
            old_sha = ""
            byte_equal = False
            missing_counterparts += 1
        else:
            old_sha = sha256(expected_old_realpath)
            byte_equal = old_sha == recovery_sha
            if not byte_equal and not (
                spec is not None and not spec["byte_equality_required"]
            ):
                mismatched_counterparts += 1
        if row.get("old_counterpart_sha256") != old_sha:
            errors.append(f"{prefix} old counterpart SHA-256 mismatch")
        if row.get("byte_equal") != ("true" if byte_equal else "false"):
            errors.append(f"{prefix} counterpart byte-equality claim mismatch")
    if seen_recovery_paths != expected_recovery_paths:
        errors.append(f"{prefix} counterpart rows do not cover all captured recovery maps")
    expected_counterpart_audit = {
        "schema_version": 1,
        "status": "accepted",
        "failure_reasons": [],
        "captured_recovery_component_count": len(counterpart_rows),
        "counterpart_missing_count": missing_counterparts,
        "counterpart_byte_mismatch_count": mismatched_counterparts,
        "exclusion_rules": exclusions,
    }
    if counterpart_audit != expected_counterpart_audit:
        errors.append(f"{prefix} recovery counterpart audit summary mismatch")
    if missing_counterparts or mismatched_counterparts:
        errors.append(f"{prefix} mapped_component_byte_equivalence_unprovable")

    launcher_pid = audit.get("launcher_pid")
    rank_pids_value = audit.get("rank_pids")
    expected_rank_pid_keys = {
        str(rank) for rank in range(audit_spec["rank_count"])
    }
    if (
        not isinstance(rank_pids_value, dict)
        or set(rank_pids_value) != expected_rank_pid_keys
        or any(
            not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0
            for pid in rank_pids_value.values()
        )
    ):
        rank_pids = {}
        errors.append(f"{prefix} invalid rank PID evidence")
    else:
        rank_pids = {int(rank): pid for rank, pid in rank_pids_value.items()}
    handshake_directory = audit_directory / "rank_handshake"
    ready_paths = sorted((handshake_directory / "ready").glob("*"))
    release_paths = sorted((handshake_directory / "release").glob("*"))
    failure_paths = sorted((handshake_directory / "failure").glob("*"))
    abort_path = handshake_directory / "abort"
    evidence_paths.extend(
        path
        for path in (*ready_paths, *release_paths, *failure_paths, abort_path)
        if path.is_file()
    )
    expected_ready_names = [f"rank-{rank}.json" for rank in range(audit_spec["rank_count"])]
    expected_release_names = [f"rank-{rank}" for rank in range(audit_spec["rank_count"])]
    if [path.name for path in ready_paths] != expected_ready_names:
        errors.append(f"{prefix} rank handshake ready-file set differs")
    if [path.name for path in release_paths] != expected_release_names:
        errors.append(f"{prefix} rank handshake release-file set differs")
    if failure_paths or abort_path.exists() or abort_path.is_symlink():
        errors.append(f"{prefix} accepted rank handshake has failure/abort evidence")
    expected_prefix_environment = runtime["prefix_environment"]
    expected_runtime_environment = {
        "PATH": audit_spec["required_path"],
        "LD_LIBRARY_PATH": audit_spec["required_ld_library_path"],
        "LD_PRELOAD": None,
        "CMAKE_PREFIX_PATH": audit_spec["required_cmake_prefix_path"],
        "MKLROOT": audit_spec["required_mklroot"],
        "HOME": str(run_directory / "runtime_home"),
        "OMP_NUM_THREADS": "1",
        "CUDA_CACHE_DISABLE": audit_spec["required_cuda_cache_disable"],
    }
    for rank, path in enumerate(ready_paths):
        try:
            ready = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            errors.append(f"{prefix} invalid rank ready JSON {path.name}: {error}")
            continue
        incoming_environment = ready.get("incoming_environment_normalization")
        if not _rank_incoming_environment_contract_matches(
            incoming_environment,
            runtime["recovery_prefix"],
            audit_spec["required_ld_library_path"],
        ):
            errors.append(f"{prefix} rank {rank} incoming environment is invalid")
        expected_ready = {
            "schema_version": 1,
            "pid": rank_pids.get(rank),
            "rank": rank,
            "expected_ranks": audit_spec["rank_count"],
            "target_abacus_realpath": replay["abacus"]["realpath"],
            "incoming_environment_normalization": incoming_environment,
            "prefix_environment": expected_prefix_environment,
            "runtime_environment": expected_runtime_environment,
            "wrapper_state": "ready_before_exec",
        }
        if ready != expected_ready:
            errors.append(f"{prefix} rank {rank} ready evidence mismatch")
    for path in release_paths:
        if path.is_symlink() or path.read_bytes() != b"release\n":
            errors.append(f"{prefix} rank release evidence mismatch: {path.name}")
    expected_terminal = {
        "ready_files": expected_ready_names,
        "release_files": expected_release_names,
        "failure_files": [],
        "abort_exists": False,
    }
    if audit.get("rank_handshake_terminal_state") != expected_terminal:
        errors.append(f"{prefix} rank handshake terminal summary mismatch")
    trace_files, trace_path_failures = _trace_directory_contract(trace_directory)
    traces = [trace_files[pid] for pid in sorted(trace_files)]
    trace_pids = set(trace_files)
    evidence_paths.extend(traces)
    errors.extend(f"{prefix} {failure}" for failure in trace_path_failures)
    trace_records = []
    pid_roles = {pid: ("rank", rank) for rank, pid in rank_pids.items()}
    if isinstance(launcher_pid, int):
        pid_roles[launcher_pid] = ("launcher", None)
    for path in traces:
        try:
            pid = int(path.name.rsplit(".", 1)[1])
        except (IndexError, ValueError):
            pid = -1
        role, rank = pid_roles.get(pid, ("support", None))
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            trace_records.append({"pid": pid, "role": role, "rank": rank, "line": line})
    policies = tuple(audit_spec["registered_old_prefix_failed_probes"])
    reparsed = parse_strace_records(
        trace_records,
        Path(runtime["old_prefix"]),
        policies,
        audit_spec["rank_count"],
    )
    for key, value in reparsed.items():
        if audit.get(key) != value:
            errors.append(f"{prefix} runtime audit {key} differs from raw strace")
    execve_records = parse_execve_records(trace_records)
    if audit.get("observed_execve_records") != execve_records:
        errors.append(f"{prefix} execve evidence differs from raw strace")
    successful_execs = [row["path"] for row in execve_records if row.get("successful")]
    expected_execs = {
        replay["mpirun"]["realpath"]: 1,
        replay["launcher"]["realpath"]: 1,
        runtime["tools"]["python"]["realpath"]: audit_spec["rank_count"],
        replay["abacus"]["realpath"]: audit_spec["rank_count"],
    }
    observed_execs = dict(sorted(collections.Counter(successful_execs).items()))
    if observed_execs != dict(sorted(expected_execs.items())):
        errors.append(f"{prefix} successful exec multiset differs from frozen chain")
    if audit.get("successful_exec_multiset") != observed_execs:
        errors.append(f"{prefix} successful exec multiset summary differs from strace")
    ambiguous_execs = sum(
        row.get("result") not in ("0", "-1") for row in execve_records
    )
    if audit.get("ambiguous_exec_result_count") != ambiguous_execs or ambiguous_execs:
        errors.append(f"{prefix} ambiguous/truncated exec evidence is not zero")
    command = audit.get("command")
    if (
        not isinstance(command, list)
        or not command
        or not _same_path(Path(str(command[0])), Path(replay["mpirun"]["path"]))
        or command.count("--allow-run-as-root") != 1
    ):
        errors.append(f"{prefix} frozen MPI command/namespace root flag mismatch")

    tools = runtime["tools"]
    if audit.get("strace_identity_before") != tools["strace"]:
        errors.append(f"{prefix} strace preflight identity mismatch")
    if audit.get("strace_identity_after") != tools["strace"]:
        errors.append(f"{prefix} strace postflight identity mismatch")
    if audit.get("runtime_environment") != expected_runtime_environment:
        errors.append(f"{prefix} final runtime environment differs from registration")
    audit_cleanup = audit.get("process_group_cleanup", {})
    terminal_process = audit.get("terminal_process_evidence", {})
    if (
        audit.get("runtime_wall_timeout_seconds") != 7200
        or audit.get("absolute_deadline_watchdog_seconds") != 7200
        or audit.get("timeout_triggered") is not False
        or audit_cleanup.get("all_group_members_gone") is not True
        or audit_cleanup.get("members_after_cleanup") != []
        or audit_cleanup.get("tracee_pids_after_cleanup") != []
    ):
        errors.append(f"{prefix} runtime audit timeout/process cleanup evidence mismatch")
    errors.extend(
        f"{prefix} {failure}"
        for failure in _timing_evidence_failures(audit, 7200, "runtime audit")
    )
    role_failures = _runtime_pid_role_contract_failures(
        launcher_pid,
        rank_pids,
        launcher_rows,
        rank_rows,
        audit_spec["rank_count"],
    )
    errors.extend(f"{prefix} {failure}" for failure in role_failures)
    terminal_failures = _known_pid_terminal_contract_failures(
        terminal_process,
        trace_pids,
        launcher_pid,
        rank_pids,
        set(process_by_pid),
        audit_cleanup,
    )
    errors.extend(f"{prefix} {failure}" for failure in terminal_failures)
    strace_command = audit.get("strace_command", [])
    if not _strace_command_contract_matches(
        strace_command,
        Path(str(tools["strace"]["path"])),
        run_directory / "mpi_runtime_audit" / "strace" / "trace",
        command,
    ):
        errors.append(
            f"{prefix} strace 5.16 interruptible/external-containment contract mismatch"
        )
    controlled_home = run_directory / "runtime_home"
    controlled_marker = controlled_home / "CONTROLLED_HOME.txt"
    if (
        not controlled_home.is_dir()
        or controlled_home.is_symlink()
        or sorted(path.name for path in controlled_home.iterdir())
        != [controlled_marker.name]
        or controlled_marker.read_text(encoding="utf-8", errors="replace")
        != "Controlled empty HOME for S1 runtime-relocation replay.\n"
    ):
        errors.append(f"{prefix} controlled HOME contains unregistered user config")
    else:
        evidence_paths.append(controlled_marker)
    namespace_paths = {
        name: namespace_directory / name
        for name in (
            "host_preflight.json",
            "host_status.json",
            "payload_status.json",
            "state.before_mount.json",
            "state.after_mount.json",
            "state.after_run.json",
            "mountinfo.before_mount",
            "mountinfo.after_mount",
            "mountinfo.after_run",
        )
    }
    evidence_paths.extend(namespace_paths.values())
    payloads = {}
    for name, path in namespace_paths.items():
        if not path.is_file() or path.is_symlink():
            errors.append(f"{prefix} missing namespace evidence {name}")
            continue
        if name.endswith(".json"):
            try:
                payloads[name] = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as error:
                errors.append(f"{prefix} invalid namespace evidence {name}: {error}")
    host_status = payloads.get("host_status.json", {})
    host_preflight = payloads.get("host_preflight.json", {})
    if host_status.get("status") != "accepted" or host_status.get("tools_before") != (
        host_status.get("tools_after")
    ):
        errors.append(f"{prefix} namespace host before/after tool gate failed")
    cleanup = host_status.get("process_group_cleanup", {})
    pid1_kernel_reap_proof = host_status.get("pid1_kernel_reap_proof", {})
    accessible_namespace_scan = host_status.get(
        "accessible_host_pid_namespace_scan", {}
    )
    if (
        host_status.get("total_wall_timeout_seconds") != 7260
        or host_status.get("absolute_deadline_watchdog_seconds") != 7260
        or host_status.get("timeout_triggered") is not False
        or cleanup.get("all_group_members_gone") is not True
        or cleanup.get("members_after_cleanup") != []
        or cleanup.get("tracee_pids_after_cleanup") != []
    ):
        errors.append(f"{prefix} namespace timeout/process cleanup evidence mismatch")
    errors.extend(
        f"{prefix} {failure}"
        for failure in _timing_evidence_failures(host_status, 7260, "namespace host")
    )
    if (
        not isinstance(pid1_kernel_reap_proof, dict)
        or pid1_kernel_reap_proof.get("all_namespace_members_reaped") is not True
        or pid1_kernel_reap_proof.get("evidence_errors") != []
    ):
        errors.append(f"{prefix} PID-1 kernel reap proof is incomplete")
    if not _accessible_host_scan_contract_matches(accessible_namespace_scan):
        errors.append(f"{prefix} accessible host PID-namespace scan is invalid")
    if host_status.get("counterpart_audit") != counterpart_audit:
        errors.append(f"{prefix} host counterpart audit handoff mismatch")
    if host_status.get("tools_before") != {
        key: tools[key] for key in ("unshare", "bash", "mount", "python")
    }:
        errors.append(f"{prefix} namespace host tools differ from frozen config")
    host_before = host_status.get("host_old_runtime_before")
    host_after = host_status.get("host_old_runtime_after")
    if (
        host_before != host_after
        or host_preflight.get("host_old_runtime_before") != host_before
        or not isinstance(host_before, dict)
        or host_before.get("host_uid") != runtime["namespace"]["host_uid"]
        or host_before.get("host_gid") != runtime["namespace"]["host_gid"]
        or host_before.get("old_root_mountinfo_lines") != []
        or host_before.get("old_root_lstat") != _lstat_identity(Path(runtime["old_root"]))
        or host_before.get("old_prefix_lstat")
        != _lstat_identity(Path(runtime["old_prefix"]))
    ):
        errors.append(f"{prefix} host old-runtime isolation/non-propagation evidence mismatch")
    if payloads.get("payload_status.json", {}).get("status") != "accepted":
        errors.append(f"{prefix} namespace payload did not accept")
    before_state = payloads.get("state.before_mount.json", {})
    namespace_inode = before_state.get("pid_namespace_inode")
    expected_controlled_home = str(run_directory / "runtime_home")
    raw_before_lines = namespace_paths["mountinfo.before_mount"].read_text(
        encoding="utf-8", errors="replace"
    ).splitlines() if namespace_paths["mountinfo.before_mount"].is_file() else []
    recomputed_before_old_lines = [
        raw
        for raw in raw_before_lines
        if len(raw.split()) >= 5 and raw.split()[4] == runtime["old_root"]
    ]
    expected_uid_map = [0, runtime["namespace"]["host_uid"], 1]
    expected_gid_map = [0, runtime["namespace"]["host_gid"], 1]
    uid_map = _integer_fields(before_state.get("uid_map", ""))
    gid_map = _integer_fields(before_state.get("gid_map", ""))
    if (
        before_state.get("effective_uid") != 0
        or not before_state.get("old_root_exists")
        or not before_state.get("old_prefix_exists")
        or before_state.get("old_root_mountinfo_lines") != []
        or before_state.get("old_root_mountinfo_lines") != recomputed_before_old_lines
        or not _controlled_home_namespace_contract_matches(
            before_state,
            expected_controlled_home,
            raw_before_lines,
            require_readonly_mount=False,
        )
        or before_state.get("shared_mount_lines") != []
        or before_state.get("namespace_init_pid") != 1
        or not isinstance(namespace_inode, int)
        or namespace_inode <= 0
        or before_state.get("nspid", [])[-1:] != [before_state.get("pid")]
        or uid_map != expected_uid_map
        or gid_map != expected_gid_map
    ):
        errors.append(f"{prefix} namespace pre-mount state mismatch")
    for phase in ("after_mount", "after_run"):
        state = payloads.get(f"state.{phase}.json", {})
        raw_mountinfo_path = namespace_paths[f"mountinfo.{phase}"]
        raw_lines = raw_mountinfo_path.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines() if raw_mountinfo_path.is_file() else []
        recomputed_old_lines = [
            raw
            for raw in raw_lines
            if len(raw.split()) >= 5 and raw.split()[4] == runtime["old_root"]
        ]
        lines = state.get("old_root_mountinfo_lines", [])
        line = lines[0] if isinstance(lines, list) and len(lines) == 1 else ""
        fields = line.split()
        separator = fields.index("-") if "-" in fields else -1
        options = set(fields[5].split(",")) if len(fields) > 5 else set()
        mount_ok = (
            separator >= 0
            and fields[separator + 1 : separator + 3] == ["tmpfs", "tmpfs"]
            and {"nosuid", "nodev", "noexec"}.issubset(options)
        )
        if (
            not state.get("old_root_exists")
            or state.get("old_prefix_exists")
            or not mount_ok
            or not _controlled_home_namespace_contract_matches(
                state,
                expected_controlled_home,
                raw_lines,
                require_readonly_mount=True,
                expected_lstat=before_state.get("controlled_home_lstat"),
            )
            or state.get("shared_mount_lines") != []
            or state.get("namespace_init_pid") != 1
            or state.get("pid_namespace_inode") != namespace_inode
            or state.get("nspid", [])[-1:] != [state.get("pid")]
            or lines != recomputed_old_lines
            or _integer_fields(state.get("uid_map", "")) != expected_uid_map
            or _integer_fields(state.get("gid_map", "")) != expected_gid_map
        ):
            errors.append(f"{prefix} namespace {phase} isolation evidence mismatch")
    if payloads.get("state.after_mount.json", {}).get(
        "controlled_home_mountinfo_lines"
    ) != payloads.get("state.after_run.json", {}).get(
        "controlled_home_mountinfo_lines"
    ):
        errors.append(f"{prefix} controlled HOME read-only mount changed during run")
    if not Path(runtime["old_root"]).is_dir() or not Path(runtime["old_prefix"]).is_dir():
        errors.append(f"{prefix} host old runtime did not survive private namespace")
    payload_status = payloads.get("payload_status.json", {})
    if payload_status.get("pid_namespace_inode") != namespace_inode:
        errors.append(f"{prefix} payload status PID namespace identity mismatch")
    expected_host_command = [
        *runtime["namespace"]["unshare_argv_prefix"],
        "--bind-to",
        "core",
        "-np",
        str(audit_spec["rank_count"]),
        replay["abacus"]["path"],
    ]
    if (
        host_preflight.get("command") != expected_host_command
        or host_status.get("command") != expected_host_command
        or host_preflight.get("stdin") != "/dev/null"
        or host_status.get("stdin") != "/dev/null"
    ):
        errors.append(f"{prefix} executed unshare namespace command differs from registration")
    expected_unshare_argv = runtime["namespace"]["unshare_argv_prefix"][:10]
    expected_payload_argv = [
        *runtime["namespace"]["unshare_argv_prefix"][10:],
        "--bind-to",
        "core",
        "-np",
        str(audit_spec["rank_count"]),
        replay["abacus"]["path"],
    ]
    if not _pid1_kernel_reap_contract_matches(
        pid1_kernel_reap_proof,
        namespace_inode,
        expected_host_command,
        expected_unshare_argv,
        expected_payload_argv,
    ):
        errors.append(f"{prefix} PID-1 kernel reap proof differs from raw namespace evidence")
    if accessible_namespace_scan.get("pid_namespace_inode") != namespace_inode:
        errors.append(f"{prefix} accessible host scan used a different PID namespace")
    return evidence_paths


def validate_replay_run(
    project_root: Path,
    config: dict,
    row: dict[str, str],
    *,
    require_committed: bool,
    require_replay_status: bool = True,
) -> list[str]:
    experiment_id = row["replay_experiment_id"]
    prefix = f"{experiment_id}:"
    errors: list[str] = []
    run_directory = project_root / "runs" / experiment_id
    source_directory = path_from_project(project_root, row["input_directory"])
    if not run_directory.is_dir() or run_directory.is_symlink():
        return [f"{prefix} missing replay run directory"]
    required = {
        "INPUT": run_directory / "INPUT",
        "STRU": run_directory / "STRU",
        "KPT": run_directory / "KPT",
        "input_metadata.json": run_directory / "input_metadata.json",
        "experiment_metadata.json": run_directory / "experiment_metadata.json",
        "result.json": run_directory / "result.json",
        "INPUT_SHA256SUMS": run_directory / "INPUT_SHA256SUMS",
        row["pseudopotential"]: run_directory / row["pseudopotential"],
        "mpi_runtime_audit/audit.json": run_directory
        / "mpi_runtime_audit"
        / "audit.json",
        "mpi_runtime_audit/objects.tsv": run_directory
        / "mpi_runtime_audit"
        / "objects.tsv",
        "run_status.json": run_directory / "run_status.json",
    }
    if require_replay_status:
        required["replay_status.json"] = run_directory / "replay_status.json"
    for name, path in required.items():
        if not path.is_file() or path.is_symlink():
            errors.append(f"{prefix} missing or symbolic-link run artifact {name}")

    try:
        source_input = (source_directory / "INPUT").read_bytes()
        if required["INPUT"].read_bytes() != normalized_run_input(source_input):
            errors.append(f"{prefix} archived INPUT differs beyond pseudo_dir normalization")
        for name in ("STRU", "KPT"):
            if required[name].read_bytes() != (source_directory / name).read_bytes():
                errors.append(f"{prefix} archived {name} differs byte-for-byte from source")
        if required["input_metadata.json"].read_bytes() != (
            source_directory / "metadata.json"
        ).read_bytes():
            errors.append(f"{prefix} archived metadata differs byte-for-byte from source")
        if required[row["pseudopotential"]].read_bytes() != (
            project_root / "assets" / "pseudo" / row["pseudopotential"]
        ).read_bytes():
            errors.append(f"{prefix} archived pseudopotential differs from frozen asset")
    except (FileNotFoundError, ValueError) as error:
        errors.append(f"{prefix} replay input comparison failed: {error}")

    try:
        metadata, log_path, result = reparse_run(run_directory)
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        errors.append(f"{prefix} raw-log reparse failed: {error}")
        log_path = None
        result = {}
        metadata = {}
    if not result.get("converged"):
        errors.append(f"{prefix} replay did not converge")
    if metadata.get("solver") != row["solver"]:
        errors.append(f"{prefix} replay solver differs from manifest")
    errors.extend(
        f"{prefix} {failure}"
        for failure in _checksum_failures(run_directory, row["pseudopotential"])
    )

    # The per-point runner invokes this validator before it accepts and commits a
    # replay.  Keep the scientific hard gate here instead of deferring it to the
    # final six-point reporting analyzer.
    try:
        reference_run = project_root / "runs" / row["reference_experiment_id"]
        reference_metadata, reference_log, _ = reparse_run(reference_run)
        reference_raw = raw_observables(
            reference_log.read_text(encoding="utf-8", errors="replace"),
            str(reference_metadata["solver"]),
            int(reference_metadata["atom_count"]),
        )
        if log_path is None:
            raise ValueError("replay raw log is unavailable")
        replay_raw = raw_observables(
            log_path.read_text(encoding="utf-8", errors="replace"),
            str(metadata["solver"]),
            int(metadata["atom_count"]),
        )
        equivalence = equivalence_tier(reference_raw, replay_raw)
        if not equivalence["scientific_tolerance_passed"]:
            errors.append(
                f"{prefix} strict scientific equivalence failed: "
                f"dE={equivalence['delta_energy_mev_per_atom']} meV/atom "
                f"dP={equivalence['delta_pressure_gpa']} GPa"
            )
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        errors.append(f"{prefix} scientific equivalence reparse failed: {error}")

    runtime = config["runtime"]
    replay_runtime = runtime["replay"]
    wrappers = runtime["wrappers"]
    tools = runtime["tools"]
    audit_spec = config["runtime_audit"]
    namespace_launcher = Path(wrappers["namespace_launcher"]["path"])
    try:
        experiment_metadata = json.loads(
            required["experiment_metadata.json"].read_text(encoding="utf-8")
        )
    except (FileNotFoundError, json.JSONDecodeError) as error:
        errors.append(f"{prefix} invalid experiment metadata: {error}")
        experiment_metadata = {}
    expected_metadata = {
        "experiment_id": experiment_id,
        "mpi_ranks": 4,
        "abacus_path": replay_runtime["abacus"]["path"],
        "abacus_realpath": replay_runtime["abacus"]["realpath"],
        "abacus_sha256": replay_runtime["abacus"]["sha256"],
        "mpirun_path": replay_runtime["mpirun"]["path"],
        "mpirun_realpath": replay_runtime["mpirun"]["realpath"],
        "mpirun_sha256": replay_runtime["mpirun"]["sha256"],
        "mpirun_invocation_path": str(namespace_launcher),
        "mpirun_invocation_sha256": wrappers["namespace_launcher"]["sha256"],
        "mpirun_invocation_interpreter_path": tools["python"]["path"],
        "mpirun_invocation_interpreter_realpath": tools["python"]["realpath"],
        "mpirun_invocation_interpreter_sha256": tools["python"]["sha256"],
        "runtime_relocation_mode": True,
        "runtime_environment": {
            "PATH": audit_spec["required_path"],
            "LD_LIBRARY_PATH": audit_spec["required_ld_library_path"],
            "LD_PRELOAD": None,
            "CMAKE_PREFIX_PATH": audit_spec["required_cmake_prefix_path"],
            "MKLROOT": audit_spec["required_mklroot"],
            "HOME": str(run_directory / "runtime_home"),
            "OMP_NUM_THREADS": "1",
            "CUDA_CACHE_DISABLE": audit_spec["required_cuda_cache_disable"],
        },
        **runtime["prefix_environment"],
        "worktree_dirty": False,
    }
    for key, expected in expected_metadata.items():
        if experiment_metadata.get(key) != expected:
            errors.append(f"{prefix} experiment metadata {key} mismatch")

    audit_path = required["mpi_runtime_audit/audit.json"]
    try:
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as error:
        errors.append(f"{prefix} invalid MPI runtime audit: {error}")
        audit = {}
    audit_expected = {
        "status": "accepted",
        "runtime_wall_timeout_seconds": audit_spec[
            "runtime_wall_timeout_seconds"
        ],
        "runtime_environment": expected_metadata["runtime_environment"],
        "old_prefix": runtime["old_prefix"],
        "recovery_prefix": runtime["recovery_prefix"],
        "rank_handshake_status": "accepted",
        "old_prefix_mapped_object_count": 0,
        "unexpected_mapped_object_count": 0,
        "unhashed_regular_mapped_object_count": 0,
        "ld_library_path": audit_spec["required_ld_library_path"],
        "ld_preload": None,
        "old_prefix_access_attempt_count": audit_spec[
            "registered_old_prefix_failed_probe_count"
        ],
        "old_prefix_successful_access_count": 0,
        "old_prefix_exec_success_count": 0,
        "registered_old_prefix_failed_probe_count": audit_spec[
            "registered_old_prefix_failed_probe_count"
        ],
        "unknown_old_prefix_failed_probe_count": 0,
        "registered_probe_count_mismatch_count": 0,
        "registered_old_prefix_failed_probes": audit_spec[
            "registered_old_prefix_failed_probes"
        ],
        "successful_exec_multiset": audit_spec["successful_exec_multiset"],
        "ambiguous_exec_result_count": 0,
        "prefix_environment": runtime["prefix_environment"],
    }
    for key, expected in audit_expected.items():
        if audit.get(key) != expected:
            errors.append(f"{prefix} runtime audit {key} mismatch")

    try:
        run_status = json.loads(required["run_status.json"].read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as error:
        errors.append(f"{prefix} invalid run_status.json: {error}")
        run_status = {}
    expected_run_status = {
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
    if run_status != expected_run_status:
        errors.append(f"{prefix} run_status.json is not an accepted execution record")
    if require_replay_status:
        try:
            replay_status = json.loads(
                required["replay_status.json"].read_text(encoding="utf-8")
            )
        except (FileNotFoundError, json.JSONDecodeError) as error:
            errors.append(f"{prefix} invalid replay_status.json: {error}")
            replay_status = {}
        expected_replay_status = {
            "schema_version": 2,
            "status": "accepted",
            "workflow_exit_code": 0,
            "invocation_exit_code": 0,
            "launcher_exit_code": 0,
            "parser_exit_code": 0,
            "core_validation_exit_code": 0,
            "run_status": run_status,
            "runtime_audit_status": "accepted",
            "runtime_audit_failure_reasons": [],
            "safe_retry_policy": (
                "archive_committed_failure_then_retry_same_registered_id"
            ),
        }
        if replay_status != expected_replay_status:
            errors.append(f"{prefix} replay_status.json is not an accepted validation record")
        if (run_directory / "failure.json").exists():
            errors.append(f"{prefix} accepted replay contains failure.json")
    evidence_paths = _validate_runtime_relocation_audit_evidence(
        run_directory,
        runtime,
        audit_spec,
        audit,
        errors,
        prefix,
    )

    tracked = [path for path in required.values() if path.is_file()]
    if log_path is not None:
        tracked.append(log_path)
    trace_directory = run_directory / "mpi_runtime_audit" / "strace"
    if audit_spec["file_trace_required"]:
        traces = sorted(trace_directory.glob("trace*"))
        if not traces:
            errors.append(f"{prefix} required strace files are missing")
    tracked.extend(path for path in evidence_paths if path.is_file())
    if require_committed:
        errors.extend(
            f"{prefix} {failure}"
            for failure in require_tracked_at_head(project_root, tracked)
        )
        try:
            commit_failure = _run_commit_chain_failure(
                project_root, experiment_id, experiment_metadata.get("code_commit")
            )
        except ValueError as error:
            commit_failure = f"{experiment_id}: cannot validate run commit chain: {error}"
        if commit_failure:
            errors.append(commit_failure)
        errors.extend(_failed_archive_chain_failures(project_root, experiment_id))
    return errors


def _failure_status_model_errors(
    run_status: dict, replay_status: dict, failure_status: dict
) -> list[str]:
    errors: list[str] = []
    workflow_exit = replay_status.get("workflow_exit_code")
    invocation_exit = replay_status.get("invocation_exit_code")
    launcher_exit = replay_status.get("launcher_exit_code")
    parser_exit = replay_status.get("parser_exit_code")
    core_validation_exit = replay_status.get("core_validation_exit_code")
    exit_values = (
        workflow_exit,
        invocation_exit,
        launcher_exit,
        parser_exit,
        core_validation_exit,
    )
    if (
        replay_status.get("schema_version") != 2
        or replay_status.get("status") != "rejected"
        or not all(isinstance(value, int) for value in exit_values)
        or replay_status.get("run_status") != run_status
        or replay_status.get("safe_retry_policy")
        != "archive_committed_failure_then_retry_same_registered_id"
    ):
        errors.append("replay_status.json is not a coherent rejected attempt")
    mirrored_exit_fields = {
        "workflow_exit_code": workflow_exit,
        "invocation_exit_code": invocation_exit,
        "launcher_exit_code": launcher_exit,
        "parser_exit_code": parser_exit,
    }
    failure_stage = run_status.get("failure_stage")
    failure_stage_valid = (
        run_status.get("status") == "accepted" and failure_stage is None
    ) or (
        run_status.get("status") == "rejected"
        and isinstance(failure_stage, str)
        and bool(failure_stage)
    )
    if (
        run_status.get("schema_version") != 2
        or run_status.get("runtime_relocation_mode") is not True
        or not isinstance(run_status.get("setup_completed"), bool)
        or not failure_stage_valid
        or any(run_status.get(key) != value for key, value in mirrored_exit_fields.items())
    ):
        errors.append("run_status.json is not a coherent runtime-relocation execution")
    if isinstance(invocation_exit, int) and isinstance(parser_exit, int):
        derived_workflow = invocation_exit if invocation_exit != 0 else parser_exit
        if workflow_exit != derived_workflow:
            errors.append("workflow exit does not derive from invocation/parser")
    run_acceptance_fields = (
        invocation_exit == 0
        and parser_exit == 0
        and run_status.get("result_json_present") is True
        and run_status.get("result_converged") is True
        and run_status.get("runtime_audit_json_present") is True
        and run_status.get("runtime_audit_status") == "accepted"
        and run_status.get("namespace_host_status") == "accepted"
        and run_status.get("counterpart_audit_status") == "accepted"
    )
    expected_run_status_value = "accepted" if run_acceptance_fields else "rejected"
    if run_status.get("status") != expected_run_status_value:
        errors.append("run status does not derive from component statuses")
    if (
        workflow_exit == 0
        and core_validation_exit == 0
        and expected_run_status_value == "accepted"
    ):
        errors.append("rejected replay has no rejecting component")
    expected_failure = {
        "schema_version": 2,
        "status": "failed_attempt_preserved",
        **mirrored_exit_fields,
        "core_validation_exit_code": core_validation_exit,
        "setup_completed": run_status.get("setup_completed"),
        "failure_stage": run_status.get("failure_stage"),
        "runtime_audit_failure_reasons": replay_status.get(
            "runtime_audit_failure_reasons", []
        ),
        "retry_requires_committed_archive": True,
    }
    if failure_status != expected_failure:
        errors.append("failure.json differs from replay_status.json")
    return errors


def validate_failed_replay_run(
    project_root: Path,
    config: dict,
    row: dict[str, str],
    *,
    require_committed: bool,
) -> list[str]:
    experiment_id = row["replay_experiment_id"]
    prefix = f"{experiment_id}:"
    errors: list[str] = []
    run_directory = project_root / "runs" / experiment_id
    if not run_directory.is_dir() or run_directory.is_symlink():
        return [f"{prefix} missing failed-attempt directory"]
    source_directory = path_from_project(project_root, row["input_directory"])
    required = {
        "experiment_metadata.json": run_directory / "experiment_metadata.json",
        "run_status.json": run_directory / "run_status.json",
        "replay_status.json": run_directory / "replay_status.json",
        "failure.json": run_directory / "failure.json",
    }
    for name, path in required.items():
        if not path.is_file() or path.is_symlink():
            errors.append(f"{prefix} missing or symbolic-link failure artifact {name}")
    try:
        run_status = json.loads(required["run_status.json"].read_text(encoding="utf-8"))
        replay_status = json.loads(
            required["replay_status.json"].read_text(encoding="utf-8")
        )
        failure_status = json.loads(required["failure.json"].read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as error:
        errors.append(f"{prefix} invalid failure status JSON: {error}")
        run_status = {}
        replay_status = {}
        failure_status = {}
    workflow_exit = replay_status.get("workflow_exit_code")
    invocation_exit = replay_status.get("invocation_exit_code")
    launcher_exit = replay_status.get("launcher_exit_code")
    parser_exit = replay_status.get("parser_exit_code")
    core_validation_exit = replay_status.get("core_validation_exit_code")
    errors.extend(
        f"{prefix} {failure}"
        for failure in _failure_status_model_errors(
            run_status, replay_status, failure_status
        )
    )
    setup_paths = {
        "INPUT": run_directory / "INPUT",
        "STRU": run_directory / "STRU",
        "KPT": run_directory / "KPT",
        "input_metadata.json": run_directory / "input_metadata.json",
        "INPUT_SHA256SUMS": run_directory / "INPUT_SHA256SUMS",
        row["pseudopotential"]: run_directory / row["pseudopotential"],
    }
    setup_completed = run_status.get("setup_completed") is True
    if setup_completed:
        for name, path in setup_paths.items():
            if not path.is_file() or path.is_symlink():
                errors.append(f"{prefix} missing completed-setup artifact {name}")
    try:
        comparisons = {
            "INPUT": normalized_run_input((source_directory / "INPUT").read_bytes()),
            "STRU": (source_directory / "STRU").read_bytes(),
            "KPT": (source_directory / "KPT").read_bytes(),
            "input_metadata.json": (source_directory / "metadata.json").read_bytes(),
            row["pseudopotential"]: (
                project_root / "assets" / "pseudo" / row["pseudopotential"]
            ).read_bytes(),
        }
        for name, expected in comparisons.items():
            path = setup_paths[name]
            if path.exists() or path.is_symlink():
                if not path.is_file() or path.is_symlink() or path.read_bytes() != expected:
                    errors.append(f"{prefix} failed-attempt {name} differs from source")
        if setup_completed:
            errors.extend(
                f"{prefix} {failure}"
                for failure in _checksum_failures(run_directory, row["pseudopotential"])
            )
    except (FileNotFoundError, ValueError) as error:
        errors.append(f"{prefix} failed-attempt input comparison failed: {error}")
    if not setup_completed and (
        (run_directory / "result.json").exists()
        or (run_directory / "mpi_runtime_audit").exists()
    ):
        errors.append(f"{prefix} pre-setup failure contains execution artifacts")

    logs = sorted(run_directory.glob("OUT.*/running_scf.log"))
    if len(logs) > 1:
        errors.append(f"{prefix} failed attempt has multiple raw SCF logs")
    result_path = run_directory / "result.json"
    if logs and parser_exit == 0:
        try:
            _, parsed_log, _ = reparse_run(run_directory)
            if parsed_log != logs[0]:
                errors.append(f"{prefix} failed-attempt raw log path mismatch")
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            errors.append(f"{prefix} failed-attempt parser claimed success but reparse failed: {error}")
    if run_status.get("result_json_present") != result_path.is_file():
        errors.append(f"{prefix} run status result-presence claim mismatch")

    audit_path = run_directory / "mpi_runtime_audit" / "audit.json"
    audit = {}
    if audit_path.is_file() and not audit_path.is_symlink():
        try:
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            errors.append(f"{prefix} invalid rejected audit JSON: {error}")
    if replay_status.get("runtime_audit_status") != audit.get("status"):
        errors.append(f"{prefix} rejected audit status claim mismatch")
    if replay_status.get("runtime_audit_failure_reasons", []) != audit.get(
        "failure_reasons", []
    ):
        errors.append(f"{prefix} rejected audit failure reasons mismatch")
    if audit and launcher_exit != audit.get("launcher_exit_code"):
        errors.append(f"{prefix} launcher exit differs from raw runtime audit")
    host_status_path = (
        run_directory / "mpi_runtime_audit" / "namespace" / "host_status.json"
    )
    counterpart_path = run_directory / "mpi_runtime_audit" / "counterpart_audit.json"
    try:
        host_status = (
            json.loads(host_status_path.read_text(encoding="utf-8"))
            if host_status_path.is_file()
            else {}
        )
        counterpart_status = (
            json.loads(counterpart_path.read_text(encoding="utf-8"))
            if counterpart_path.is_file()
            else {}
        )
    except json.JSONDecodeError as error:
        errors.append(f"{prefix} invalid rejected namespace/counterpart status: {error}")
        host_status = {}
        counterpart_status = {}
    if run_status.get("namespace_host_status") != host_status.get("status"):
        errors.append(f"{prefix} namespace host status claim mismatch")
    if run_status.get("counterpart_audit_status") != counterpart_status.get("status"):
        errors.append(f"{prefix} counterpart status claim mismatch")
    trace_directory = run_directory / "mpi_runtime_audit" / "strace"
    trace_paths = sorted(trace_directory.glob("trace.*"))
    if audit and trace_paths:
        try:
            launcher_pid = audit.get("launcher_pid")
            rank_pids = {
                int(rank): int(pid) for rank, pid in audit.get("rank_pids", {}).items()
            }
            pid_roles = {pid: ("rank", rank) for rank, pid in rank_pids.items()}
            if isinstance(launcher_pid, int):
                pid_roles[launcher_pid] = ("launcher", None)
            records = []
            for path in trace_paths:
                pid = int(path.name.rsplit(".", 1)[1])
                role, rank = pid_roles.get(pid, ("support", None))
                records.extend(
                    {"pid": pid, "role": role, "rank": rank, "line": line}
                    for line in path.read_text(
                        encoding="utf-8", errors="replace"
                    ).splitlines()
                )
            reparsed = parse_strace_records(
                records,
                Path(config["runtime"]["old_prefix"]),
                tuple(config["runtime_audit"]["registered_old_prefix_failed_probes"]),
                config["rank_count"],
            )
            for key, value in reparsed.items():
                if audit.get(key) != value:
                    errors.append(f"{prefix} rejected audit {key} differs from raw strace")
            if audit.get("observed_execve_records") != parse_execve_records(records):
                errors.append(f"{prefix} rejected audit exec evidence differs from strace")
        except (KeyError, TypeError, ValueError) as error:
            errors.append(f"{prefix} cannot reparse rejected strace evidence: {error}")

    try:
        experiment_metadata = json.loads(
            required["experiment_metadata.json"].read_text(encoding="utf-8")
        )
    except (FileNotFoundError, json.JSONDecodeError):
        experiment_metadata = {}
    all_artifacts = [path for path in run_directory.rglob("*") if path.is_file()]
    if any(path.is_symlink() for path in run_directory.rglob("*")):
        errors.append(f"{prefix} failed-attempt tree contains a symbolic link")
    if require_committed:
        errors.extend(
            f"{prefix} {failure}"
            for failure in require_tracked_at_head(project_root, all_artifacts)
        )
        try:
            commit_failure = _run_commit_chain_failure(
                project_root, experiment_id, experiment_metadata.get("code_commit")
            )
        except ValueError as error:
            commit_failure = f"{experiment_id}: cannot validate failure commit chain: {error}"
        if commit_failure:
            errors.append(commit_failure)
        errors.extend(_failed_archive_chain_failures(project_root, experiment_id))
    return errors


def validate(
    project_root: Path,
    config_path: Path,
    manifest_path: Path,
    *,
    require_committed: bool = False,
    check_run_ids: tuple[str, ...] = (),
    check_core_ids: tuple[str, ...] = (),
    check_failure_ids: tuple[str, ...] = (),
) -> dict:
    project_root = project_root.resolve()
    config_path = config_path.resolve()
    manifest_path = manifest_path.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    rows = read_tsv(manifest_path)
    errors: list[str] = []
    _expect_keys(config, TOP_LEVEL_KEYS, "config", errors)
    if config.get("schema_version") != 2:
        errors.append("schema_version must equal 2")
    if config.get("status") != "runtime_relocation_equivalence_frozen":
        errors.append("config is not a formally frozen runtime relocation protocol")
    if config.get("protocol_revision") != PROTOCOL_REVISION:
        errors.append("protocol revision mismatch")
    if os.getuid() == 0:
        errors.append("runtime relocation validation must run as a non-root host user")
    if not re.fullmatch(r"[0-9a-f]{40}", str(config.get("generated_from_commit", ""))):
        errors.append("generated_from_commit must be a Git SHA-1")
    if config.get("rank_count") != 4:
        errors.append("rank_count must equal 4")
    if config.get("experiment_id_block") != {
        "date": "20260805",
        "start_sequence": 113,
        "end_sequence": 118,
    }:
        errors.append("experiment ID block is not exactly 113-118")

    runtime = config.get("runtime", {})
    audit_spec = config.get("runtime_audit", {})
    if isinstance(runtime, dict):
        _expect_keys(runtime, RUNTIME_KEYS, "runtime", errors)
    else:
        errors.append("runtime must be an object")
        runtime = {}
    if isinstance(audit_spec, dict):
        _expect_keys(audit_spec, AUDIT_KEYS, "runtime_audit", errors)
    else:
        errors.append("runtime_audit must be an object")
        audit_spec = {}

    recovery_prefix = Path(str(runtime.get("recovery_prefix", "")))
    recovery_root = Path(str(runtime.get("recovery_root", "")))
    old_root = Path(str(runtime.get("old_root", "")))
    old_prefix = Path(str(runtime.get("old_prefix", "")))
    if not recovery_prefix.is_absolute() or not recovery_root.is_absolute():
        errors.append("recovery root and prefix must be absolute")
    if (
        not old_root.is_absolute()
        or not old_prefix.is_absolute()
        or old_prefix.parent != old_root
        or _same_path(old_prefix, recovery_prefix)
    ):
        errors.append("old prefix must be a distinct absolute path")
    prefixes = runtime.get("prefix_environment")
    if prefixes != {
        "OPAL_PREFIX": str(recovery_prefix),
        "PRTE_PREFIX": str(recovery_prefix),
        "PMIX_PREFIX": str(recovery_prefix),
        "UCX_MODULE_DIR": str(recovery_prefix),
    }:
        errors.append("all four MPI/UCX prefix variables must equal recovery_prefix")
    reference_runtime = runtime.get("reference", {})
    replay_runtime = runtime.get("replay", {})
    if set(reference_runtime) != {"abacus", "mpirun", "launcher", "r8_launcher_observation"}:
        errors.append("reference runtime identity fields differ")
    if set(replay_runtime) != {"abacus", "mpirun", "launcher"}:
        errors.append("replay runtime identity fields differ")
    reference_abacus = _check_identity(
        reference_runtime.get("abacus"), "reference ABACUS", errors
    )
    reference_mpirun = _check_identity(
        reference_runtime.get("mpirun"), "reference mpirun", errors
    )
    reference_launcher = _check_identity(
        reference_runtime.get("launcher"), "reference final launcher", errors
    )
    replay_abacus = _check_identity(replay_runtime.get("abacus"), "replay ABACUS", errors)
    replay_mpirun = _check_identity(replay_runtime.get("mpirun"), "replay mpirun", errors)
    replay_launcher = _check_identity(
        replay_runtime.get("launcher"), "replay final launcher", errors
    )
    if not is_within(Path(str(replay_abacus.get("realpath", ""))), recovery_root):
        errors.append("ABACUS resolves outside recovery_root")
    if not is_within(Path(str(replay_mpirun.get("realpath", ""))), recovery_prefix):
        errors.append("mpirun resolves outside recovery_prefix")
    if not is_within(Path(str(replay_launcher.get("realpath", ""))), recovery_prefix):
        errors.append("final MPI launcher resolves outside recovery_prefix")
    if not is_within(Path(str(reference_launcher.get("realpath", ""))), old_prefix):
        errors.append("reference final launcher does not resolve inside old prefix")
    if reference_mpirun.get("sha256") != replay_mpirun.get("sha256"):
        errors.append("reference/replay mpirun bytes differ")
    if reference_launcher.get("sha256") != replay_launcher.get("sha256"):
        errors.append("reference/replay final launcher bytes differ")
    expected_launcher_observation = {
        "claim": "original_42_used_old_prefix_prte",
        "evidence_scope": "operator_remote_proc_observation_not_archived_per_reference_run",
        "launcher_realpath_is_in_old_prefix": True,
        "launcher_bytes_equal_replay_launcher": True,
        "mpirun_claim": "original_42_invoked_registered_recovery_mpirun",
        "mpirun_metadata_scope": (
            "explicit_freeze_operator_observation_legacy_run_metadata_omits_mpirun"
        ),
    }
    if reference_runtime.get("r8_launcher_observation") != expected_launcher_observation:
        errors.append("R8 old-launcher observation/limitation differs")

    tools = runtime.get("tools", {})
    if set(tools) != {"strace", "unshare", "mount", "bash", "python"}:
        errors.append("runtime tool set differs")
    for name in ("strace", "unshare", "mount", "bash", "python"):
        _check_versioned_tool(tools.get(name), name, errors)
    wrappers = runtime.get("wrappers", {})
    expected_wrapper_paths = {
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
    if set(wrappers) != set(expected_wrapper_paths):
        errors.append("runtime wrapper set differs")
    for name, expected_path in expected_wrapper_paths.items():
        identity = wrappers.get(name, {})
        if identity.get("path") != str(expected_path):
            errors.append(f"runtime wrapper path mismatch: {name}")
        _check_hash(expected_path, str(identity.get("sha256", "")), name, errors)
    try:
        current_elf = relocation_equivalence_evidence(
            Path(reference_abacus["path"]),
            Path(replay_abacus["path"]),
            old_prefix,
            Path(runtime["elf_relocation"]["readelf_tool"]["path"]),
            Path(runtime["elf_relocation"]["chrpath_tool"]["path"]),
        )
        if current_elf != runtime.get("elf_relocation"):
            errors.append("current byte-level ELF evidence differs from registration")
    except (KeyError, TypeError, ValueError) as error:
        errors.append(f"runtime relocation ELF validation failed: {error}")
    expected_mpi_argv = ["--allow-run-as-root", "--bind-to", "core", "-np", "4"]
    if runtime.get("mpi_argv_prefix") != expected_mpi_argv:
        errors.append("frozen MPI argv must include the user-namespace root flag")
    expected_namespace = {
        "unshare_argv_prefix": [
            tools.get("unshare", {}).get("path"),
            "--user",
            "--map-root-user",
            "--kill-child=KILL",
            "--mount",
            "--pid",
            "--fork",
            "--mount-proc",
            "--propagation",
            "private",
            tools.get("bash", {}).get("path"),
            wrappers.get("namespace_payload", {}).get("path"),
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
        "pid1_kernel_reap_proof_required": True,
        "accessible_host_pid_namespace_negative_scan_required": True,
        "host_proc_pid_namespace_scan_is_auxiliary": True,
        "external_old_root_must_survive": True,
        "total_wall_timeout_seconds": 7260,
        "timeout_requires_zero_residual_processes": True,
    }
    if runtime.get("namespace") != expected_namespace:
        errors.append("namespace isolation contract differs from registration")
    expected_audit = {
        "launcher_count": 1,
        "rank_count": 4,
        "runtime_wall_timeout_seconds": 7200,
        "absolute_deadline_watchdog_seconds": 7200,
        "known_pid_terminal_proof_required": True,
        "strace_fixed_arguments": [
            "-ff",
            "-qq",
            "-I",
            "1",
            "-s",
            "4096",
            "-e",
            "trace=file,process",
        ],
        "tracee_termination_contract": [
            "runtime_process_group_zero_residual",
            "known_pid_cross_channel_terminal_proof",
            "pid_namespace_init_exit_kernel_sigkill",
            "accessible_host_inode_negative_scan",
        ],
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
                "reference": reference_mpirun,
                "replay": replay_mpirun,
                "byte_equality_required": True,
            },
            "launcher_identity_gate": {
                "reference": reference_launcher,
                "replay": replay_launcher,
                "byte_equality_required": True,
            },
        },
        "successful_exec_multiset": {
            replay_mpirun["realpath"]: 1,
            replay_launcher["realpath"]: 1,
            tools["python"]["realpath"]: 4,
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
        "controlled_home_readonly_bind_mount_required": True,
        "required_path": f"{recovery_prefix}/bin:/usr/bin:/bin",
        "required_cmake_prefix_path": str(recovery_prefix),
        "required_mklroot": str(recovery_prefix),
        "required_ld_library_path": str(recovery_prefix / "lib"),
        "required_cuda_cache_disable": "1",
        "ld_preload_must_be_unset": True,
        "transient_mapping_patterns": list(TRANSIENT_MAPPING_PATTERNS),
        "system_mapping_roots": list(SYSTEM_MAPPING_ROOTS),
        "system_mapping_exact_paths": list(SYSTEM_MAPPING_EXACT_PATHS),
        "registered_device_mapping_patterns": list(
            REGISTERED_DEVICE_MAPPING_PATTERNS
        ),
        "namespace_evidence_required": True,
    }
    if audit_spec != expected_audit:
        errors.append("runtime_audit fields differ from the registered hard gates")

    acceptance = config.get("acceptance")
    expected_acceptance = {
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
    }
    if acceptance != expected_acceptance:
        errors.append("acceptance fields differ from the registered strict protocol")

    source = config.get("source", {})
    if isinstance(source, dict):
        _expect_keys(source, SOURCE_KEYS, "source", errors)
    else:
        errors.append("source must be an object")
        source = {}
    for label in ("r8_config", "r8_manifest", "r8_summary"):
        path_key = f"{label}_path"
        hash_key = f"{label}_sha256"
        if path_key not in source or hash_key not in source:
            errors.append(f"source is missing {label} path/hash")
            continue
        _check_hash(
            path_from_project(project_root, source[path_key]),
            str(source[hash_key]),
            label,
            errors,
        )
    if source.get("r8_status") != "accepted":
        errors.append("registered S1-R8 status is not accepted")
    r8_manifest_validation = source.get("r8_manifest_validation")
    if not isinstance(r8_manifest_validation, dict):
        errors.append("source r8_manifest_validation must be an object")
    else:
        _expect_keys(
            r8_manifest_validation,
            R8_VALIDATION_KEYS,
            "source r8_manifest_validation",
            errors,
        )
        expected_r8_validation = {
            "experiment_count": 42,
            "first_experiment_id": "S1-20260805-071",
            "last_experiment_id": "S1-20260805-112",
            "config_sha256": source.get("r8_config_sha256"),
            "manifest_sha256": source.get("r8_manifest_sha256"),
        }
        for key, expected in expected_r8_validation.items():
            if r8_manifest_validation.get(key) != expected:
                errors.append(f"source R8 validation {key} mismatch")
        if not re.fullmatch(
            r"[0-9a-f]{40,64}",
            str(r8_manifest_validation.get("preregistration_commit", "")),
        ):
            errors.append("source R8 preregistration commit is invalid")
    r8_algorithm = source.get("r8_algorithm_provenance")
    if not isinstance(r8_algorithm, dict):
        errors.append("source r8_algorithm_provenance must be an object")
    else:
        _expect_keys(
            r8_algorithm,
            R8_ALGORITHM_KEYS,
            "source r8_algorithm_provenance",
            errors,
        )
        algorithm_files = {
            "r8_analyzer_sha256": project_root
            / "scripts"
            / "analyze_s1_non_equilibrium.py",
            "eos_analyzer_sha256": project_root / "scripts" / "analyze_s1_eos.py",
            "result_parser_sha256": project_root / "scripts" / "parse_s1_single.py",
        }
        for key, path in algorithm_files.items():
            _check_hash(path, str(r8_algorithm.get(key, "")), key, errors)
        if not re.fullmatch(
            r"[0-9a-f]{40,64}", str(r8_algorithm.get("analysis_commit", ""))
        ):
            errors.append("source R8 analysis commit is invalid")
    try:
        r8_summary_path = path_from_project(project_root, source["r8_summary_path"])
        r8_summary = json.loads(r8_summary_path.read_text(encoding="utf-8"))
        if r8_summary.get("s1_r8_status") != "accepted":
            errors.append("current frozen S1-R8 summary is not accepted")
        for key, expected in (
            ("expected_calculations", 42),
            ("selected_calculations", 42),
            ("accepted_comparisons", 6),
        ):
            if r8_summary.get(key) != expected:
                errors.append(f"current frozen S1-R8 summary {key} mismatch")
        r8_summary_provenance = r8_summary.get("analysis_provenance", {})
        if isinstance(r8_algorithm, dict):
            expected_algorithm_provenance = {
                "analyzer_code_commit": r8_algorithm.get("analysis_commit"),
                "analyzer_script_sha256": r8_algorithm.get("r8_analyzer_sha256"),
                "result_parser_script_sha256": r8_algorithm.get("result_parser_sha256"),
            }
            for key, expected in expected_algorithm_provenance.items():
                if r8_summary_provenance.get(key) != expected:
                    errors.append(f"S1-R8 summary algorithm provenance {key} mismatch")
        current_series_status = {
            key: value.get("status")
            for key, value in r8_summary.get("series", {}).items()
            if key in source.get("r8_series_status", {})
        }
        if current_series_status != source.get("r8_series_status"):
            errors.append("S1-R8 series conclusions differ from registration")
    except (KeyError, FileNotFoundError, json.JSONDecodeError) as error:
        errors.append(f"cannot read frozen S1-R8 summary: {error}")

    if not _same_path(
        path_from_project(project_root, str(config.get("manifest_path", ""))),
        manifest_path,
    ):
        errors.append("config manifest_path does not identify the supplied manifest")
    config_digest = sha256(config_path)
    if len(rows) != len(FIXED_PAIRS):
        errors.append(f"manifest must contain exactly {len(FIXED_PAIRS)} rows")
    if [row["replay_experiment_id"] for row in rows] != [pair[0] for pair in FIXED_PAIRS]:
        errors.append("manifest rows must be ordered exactly from S1-20260805-113 to 118")
    if config.get("mappings") != [
        {
            "replay_experiment_id": replay,
            "reference_experiment_id": reference,
            "material": material,
            "series_id": series,
            "input_directory": next(
                (
                    row["input_directory"]
                    for row in rows
                    if row["replay_experiment_id"] == replay
                ),
                None,
            ),
        }
        for replay, reference, material, series in FIXED_PAIRS
    ]:
        errors.append("config mappings differ from exact 113->074 ... 118->109 mapping")

    r8_manifest_path = path_from_project(project_root, str(source.get("r8_manifest_path", "")))
    try:
        r8_by_id = read_r8_manifest(r8_manifest_path)
    except (FileNotFoundError, KeyError, ValueError) as error:
        errors.append(f"cannot read S1-R8 manifest: {error}")
        r8_by_id = {}
    expected_by_replay = {pair[0]: pair for pair in FIXED_PAIRS}
    seen: set[str] = set()
    frozen_paths = [config_path, manifest_path]
    for index, row in enumerate(rows):
        replay_id = row["replay_experiment_id"]
        pair = expected_by_replay.get(replay_id)
        if pair is None:
            errors.append(f"row {index + 1}: replay ID is outside fixed mapping")
            continue
        if replay_id in seen:
            errors.append(f"duplicate replay ID: {replay_id}")
        seen.add(replay_id)
        expected_replay, reference_id, material, series_id = pair
        if row["reference_experiment_id"] != reference_id:
            errors.append(f"{replay_id}: reference mapping mismatch")
        if row["material"] != material or row["series_id"] != series_id:
            errors.append(f"{replay_id}: material/series mapping mismatch")
        if row["config_sha256"] != config_digest:
            errors.append(f"{replay_id}: config SHA-256 mismatch")
        for name in ("abacus", "mpirun"):
            row_identity = {
                "path": row[f"reference_{name}_path"],
                "realpath": row[f"reference_{name}_realpath"],
                "sha256": row[f"reference_{name}_sha256"],
            }
            if row_identity != runtime.get("reference", {}).get(name):
                errors.append(f"{replay_id}: reference {name} identity differs from config")
        r8_row = r8_by_id.get(reference_id)
        if r8_row is None:
            errors.append(f"{replay_id}: reference missing from S1-R8 manifest")
        else:
            if row["input_directory"] != r8_row.get("input_directory"):
                errors.append(f"{replay_id}: input_directory is not the exact R8 value")
            if r8_row.get("material") != material or r8_row.get("series_id") != series_id:
                errors.append(f"{replay_id}: R8 reference material/series mismatch")
            try:
                if round(float(r8_row.get("volume_ratio", "nan")), 12) != 1.0:
                    errors.append(f"{replay_id}: R8 reference is not V/V0=1.0")
            except ValueError:
                errors.append(f"{replay_id}: invalid R8 volume ratio")

        input_directory = path_from_project(project_root, row["input_directory"])
        registered_hashes = {
            "INPUT": row["input_sha256"],
            "STRU": row["stru_sha256"],
            "KPT": row["kpt_sha256"],
            "metadata.json": row["metadata_sha256"],
        }
        for name in REQUIRED_SOURCE_FILES:
            source_path = input_directory / name
            _check_hash(source_path, registered_hashes[name], f"{replay_id}:{name}", errors)
            frozen_paths.append(source_path)
        try:
            metadata = json.loads(
                (input_directory / "metadata.json").read_text(encoding="utf-8")
            )
            if metadata.get("solver") != row["solver"]:
                errors.append(f"{replay_id}: solver differs from source metadata")
            if metadata.get("pseudopotential") != row["pseudopotential"]:
                errors.append(f"{replay_id}: pseudopotential basename mismatch")
            if metadata.get("material") != material or metadata.get("series_id") != series_id:
                errors.append(f"{replay_id}: source metadata mapping mismatch")
        except (FileNotFoundError, json.JSONDecodeError) as error:
            errors.append(f"{replay_id}: invalid source metadata: {error}")
        pseudo_path = project_root / "assets" / "pseudo" / row["pseudopotential"]
        _check_hash(
            pseudo_path,
            row["pseudopotential_sha256"],
            f"{replay_id}:pseudopotential",
            errors,
        )
        frozen_paths.append(pseudo_path)
        for path_key, hash_key, label in (
            ("reference_result_path", "reference_result_sha256", "reference result"),
            ("reference_log_path", "reference_log_sha256", "reference log"),
            (
                "reference_experiment_metadata_path",
                "reference_experiment_metadata_sha256",
                "reference experiment metadata",
            ),
        ):
            path = path_from_project(project_root, row[path_key])
            _check_hash(path, row[hash_key], f"{replay_id}:{label}", errors)
            frozen_paths.append(path)
        reference_run = project_root / "runs" / reference_id
        canonical_reference_paths = {
            "reference_result_path": reference_run / "result.json",
            "reference_experiment_metadata_path": reference_run
            / "experiment_metadata.json",
        }
        for key, expected_path in canonical_reference_paths.items():
            if not _same_path(path_from_project(project_root, row[key]), expected_path):
                errors.append(f"{replay_id}: {key} is not the canonical reference artifact")
        try:
            reference_metadata, reference_log, reference_result = reparse_run(reference_run)
            if not reference_result.get("converged"):
                errors.append(f"{replay_id}: reference result did not converge")
            if not _same_path(
                reference_log,
                path_from_project(project_root, row["reference_log_path"]),
            ):
                errors.append(f"{replay_id}: registered reference log path mismatch")
            if (reference_run / "input_metadata.json").read_bytes() != (
                input_directory / "metadata.json"
            ).read_bytes():
                errors.append(f"{replay_id}: reference/source metadata bytes differ")
            if reference_metadata.get("solver") != row["solver"]:
                errors.append(f"{replay_id}: reference solver mismatch")
            reference_experiment_metadata = json.loads(
                (reference_run / "experiment_metadata.json").read_text(encoding="utf-8")
            )
            if r8_row is not None:
                reference_archive_failures = _archive_failures(
                    project_root,
                    r8_row,
                    reference_run,
                    reference_metadata,
                    reference_result,
                )
                errors.extend(
                    f"{replay_id}: reference archive {failure}"
                    for failure in reference_archive_failures
                )
            if reference_experiment_metadata.get("mpi_ranks") != 4:
                errors.append(f"{replay_id}: reference run did not use four MPI ranks")
            if reference_experiment_metadata.get("abacus_path") != row[
                "reference_abacus_path"
            ] or reference_experiment_metadata.get("abacus_sha256") != row[
                "reference_abacus_sha256"
            ]:
                errors.append(f"{replay_id}: reference ABACUS SHA-256 mismatch")
            recorded_mpirun = {
                key: reference_experiment_metadata.get(key)
                for key in ("mpirun_path", "mpirun_sha256")
            }
            if any(value is not None for value in recorded_mpirun.values()) and (
                recorded_mpirun["mpirun_path"] != row["reference_mpirun_path"]
                or recorded_mpirun["mpirun_sha256"]
                != row["reference_mpirun_sha256"]
            ):
                errors.append(f"{replay_id}: recorded reference mpirun identity mismatch")
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            errors.append(f"{replay_id}: reference raw-log validation failed: {error}")

    registered_smoke = source.get("runtime_relocation_smoke")
    smoke_row = next(
        (row for row in rows if row.get("reference_experiment_id") == "S1-20260805-074"),
        None,
    )
    if not isinstance(registered_smoke, dict) or smoke_row is None:
        errors.append("registered managed 074 smoke evidence is missing")
    else:
        try:
            smoke_summary_path = path_from_project(
                project_root, str(registered_smoke["summary_path"])
            )
            current_smoke = validate_smoke(
                project_root,
                config,
                smoke_row,
                smoke_summary_path,
                require_committed=True,
            )
            smoke_tracked_paths = current_smoke.pop("tracked_paths")
            frozen_paths.extend(smoke_tracked_paths)
            if current_smoke != registered_smoke:
                errors.append("managed 074 smoke evidence differs from registration")
        except (KeyError, FileNotFoundError, TypeError, ValueError, json.JSONDecodeError) as error:
            errors.append(f"managed 074 smoke validation failed: {error}")

    if seen != set(expected_by_replay):
        errors.append(f"manifest replay IDs differ: {sorted(seen)}")
    preregistration_commit = None
    if require_committed:
        errors.extend(require_tracked_at_head(project_root, frozen_paths))
        try:
            preregistration_commit = _preregistration_commit(
                project_root, config_path, manifest_path
            )
            preregistration_parent = _git_bytes(
                project_root, "rev-parse", f"{preregistration_commit}^"
            ).decode("ascii").strip()
            if preregistration_parent != config.get("generated_from_commit"):
                errors.append(
                    "preregistration commit parent differs from generated_from_commit"
                )
            generated_commit = str(config.get("generated_from_commit"))
            for relative in FROZEN_IMPLEMENTATION_PATHS:
                frozen_blob = _git_bytes(
                    project_root, "cat-file", "blob", f"{generated_commit}:{relative}"
                )
                current_path = project_root / relative
                if not current_path.is_file() or current_path.read_bytes() != frozen_blob:
                    errors.append(
                        f"{relative} differs from frozen implementation at {generated_commit}"
                    )
        except ValueError as error:
            errors.append(str(error))
    selected_run_ids = check_run_ids or ()
    selected_core_ids = check_core_ids or ()
    selected_failure_ids = check_failure_ids or ()
    if selected_run_ids or selected_core_ids or selected_failure_ids:
        rows_by_id = {row["replay_experiment_id"]: row for row in rows}
        for experiment_id in (*selected_run_ids, *selected_core_ids, *selected_failure_ids):
            if sum(
                experiment_id in values
                for values in (
                    selected_run_ids,
                    selected_core_ids,
                    selected_failure_ids,
                )
            ) != 1:
                errors.append(f"run check mode is ambiguous for {experiment_id}")
                continue
            row = rows_by_id.get(experiment_id)
            if row is None:
                errors.append(f"requested run check is outside manifest: {experiment_id}")
                continue
            if experiment_id in selected_failure_ids:
                errors.extend(
                    validate_failed_replay_run(
                        project_root,
                        config,
                        row,
                        require_committed=require_committed,
                    )
                )
            else:
                errors.extend(
                    validate_replay_run(
                        project_root,
                        config,
                        row,
                        require_committed=require_committed,
                        require_replay_status=experiment_id in selected_run_ids,
                    )
                )
    if errors:
        raise ValueError("S1 MPI-prefix equivalence validation failed:\n- " + "\n- ".join(errors))
    return {
        "protocol_revision": PROTOCOL_REVISION,
        "experiment_count": len(rows),
        "first_experiment_id": rows[0]["replay_experiment_id"],
        "last_experiment_id": rows[-1]["replay_experiment_id"],
        "config_sha256": config_digest,
        "manifest_sha256": sha256(manifest_path),
        "preregistration_commit": preregistration_commit,
        "checked_run_ids": list(selected_run_ids),
        "checked_core_ids": list(selected_core_ids),
        "checked_failure_ids": list(selected_failure_ids),
    }


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "manifest",
        nargs="?",
        type=Path,
        default=project_root / CANONICAL_MANIFEST_PATH,
    )
    parser.add_argument(
        "--config", type=Path, default=project_root / CANONICAL_CONFIG_PATH
    )
    parser.add_argument("--require-committed", action="store_true")
    parser.add_argument("--check-run", action="append", default=[])
    parser.add_argument("--check-run-core", action="append", default=[])
    parser.add_argument("--check-failure-run", action="append", default=[])
    args = parser.parse_args()
    payload = validate(
        project_root,
        args.config.resolve(),
        args.manifest.resolve(),
        require_committed=args.require_committed,
        check_run_ids=tuple(args.check_run),
        check_core_ids=tuple(args.check_run_core),
        check_failure_ids=tuple(args.check_failure_run),
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
