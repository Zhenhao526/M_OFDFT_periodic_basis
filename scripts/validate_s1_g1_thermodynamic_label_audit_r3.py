#!/usr/bin/env python3
"""Validate the fail-closed S1-G1 thermodynamic-label audit R3.

R3 is a clean rerun, not a reinterpretation of R1.  Historical R1/R2 runs stay
immutable but contribute zero to R3 acceptance.  Forty new experiment IDs
cover the same forty logical scientific slots.  Every scientific gate in
this module resolves a logical slot explicitly; no gate infers a partner from
an R3 experiment-number offset.
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Iterator

import generate_s1_g1_thermodynamic_label_audit_r3 as registration
import validate_s1_g1_thermodynamic_label_audit_r1 as r1
import validate_s1_mpi_prefix_equivalence as runtime_validation
from analyze_s1_eos import fit_bm3
from parse_s1_g1_thermodynamic_labels_r3 import (
    parse_run as parse_label_run,
    validate_registration_contract as validate_label_registration_contract,
)
from s1_electron_number_common import (
    expected_electrons,
    find_single_log,
    integrate_cube,
    parse_charge_grid,
    parse_stru,
    read_json,
    scientific_equivalence,
    sha256,
)
from s1_g1_kmp_runtime_contract import validate_kmp_runtime_contract
from s1_g1_thermodynamic_label_common import (
    MANIFEST_FIELDS,
    parse_abacus_cube,
    parse_input_text,
    parse_thermodynamic_log,
    read_manifest,
)


PROTOCOL_REVISION = registration.PROTOCOL_REVISION
CONFIG_PATH = registration.CONFIG_PATH
MANIFEST_PATH = registration.MANIFEST_PATH
INPUT_ROOT = registration.INPUT_ROOT
R3_AUDIT_IDS = tuple(registration.R3_AUDIT_IDS)
R1_REUSED_AUDIT_IDS = tuple(registration.R1_REUSED_AUDIT_IDS)
EXECUTION_ORDER = tuple(registration.EXECUTION_ORDER)
NEW_TO_LOGICAL = dict(registration.NEW_TO_LOGICAL)
LOGICAL_TO_EFFECTIVE_ID = dict(registration.LOGICAL_TO_EFFECTIVE_ID)
POST_TERMINAL_DOCUMENTATION_PATHS = tuple(
    registration.POST_TERMINAL_DOCUMENTATION_PATHS
)

R1_CONFIG_PATH = Path("config/S1_g1_thermodynamic_label_audit_r1.json")
R1_MANIFEST_PATH = Path("config/S1_g1_thermodynamic_label_audit_r1_manifest.tsv")
R1_INPUT_ROOT = Path("inputs/s1/g1_thermodynamic_label_audit_r1")
R1_PREREGISTRATION_COMMIT = "f71dd6b0fca238c386c0203b077ebf426e6b6926"
R1_CONFIG_SHA256 = "76873a782a21fb45cb96f318dee992ea5f9ac25625c066d691806fafd6450eba"
R1_MANIFEST_SHA256 = "7650fe3e3f528c8e12919156ae5f8475cfc8963bcefe14137a648e7cd2859d6c"
R1_FAILURE_ID = "S1-20260806-034"
R1_FAILURE_COMMIT = "df57f9b610d82d75835193f84b4bfbb4ffa5007b"
R1_ARCHIVE_COMMIT = "b0b7db592b3438322289dbd98cf66686c6f557a4"
R1_ARCHIVE_PATH = Path(
    "failed_runs/runtime_relocation/S1-20260806-034/attempt-df57f9b610d8"
)

EVIDENCE_NAME = "g1_thermodynamic_label_audit_r3.json"
STATUS_NAME = "thermodynamic_label_status_r3.json"
FAILURE_CLASS_NAME = "thermodynamic_label_failure_classification_r3.json"
FAILURE_INVENTORY_NAME = "thermodynamic_label_failure_artifact_inventory_r3.json"
LABEL_NAME = r1.LABEL_NAME
ANALYSIS_ROOT = Path("analysis/s1/g1_thermodynamic_label_audit_r3_20260807")
ANALYSIS_SUMMARY_PATH = ANALYSIS_ROOT / "summary.json"

EXPECTED_HISTORICAL_ACCEPTED_LOGICAL_IDS = (
    "S1-20260806-024",
    "S1-20260806-036",
    "S1-20260806-031",
    "S1-20260806-039",
    "S1-20260806-021",
    "S1-20260806-035",
    "S1-20260806-027",
    "S1-20260806-037",
    "S1-20260806-028",
    "S1-20260806-038",
)
EXPECTED_REUSED_LOGICAL_IDS: tuple[str, ...] = ()
_EXPECTED_LOGICAL_SUFFIXES = (
    34, 40, 24, 36, 31, 39, 21, 35, 27, 37, 28, 38,
    *range(1, 21), 22, 23, 25, 26, 29, 30, 32, 33,
)
EXPECTED_NEW_TO_LOGICAL = {
    f"S1-20260807-{new:03d}": f"S1-20260806-{logical:03d}"
    for new, logical in enumerate(_EXPECTED_LOGICAL_SUFFIXES, 1)
}

LOGICAL_IDS = tuple(r1.RUN_IDS)
STANDARD_LOGICAL_IDS = tuple(r1.STANDARD_REPLAY_IDS)
HALF_LOGICAL_IDS = tuple(r1.HALF_IDS)
COMMON_QUARTER_LOGICAL_IDS = tuple(r1.COMMON_QUARTER_IDS)
EXTRA_QUARTER_LOGICAL_IDS = tuple(r1.EXTRA_QUARTER_IDS)
HALF_QUARTER_LOGICAL_PAIRS = tuple(zip(HALF_LOGICAL_IDS, COMMON_QUARTER_LOGICAL_IDS))
K_LOGICAL_PAIRS = (
    ("S1-20260806-021", "S1-20260806-035"),
    ("S1-20260806-024", "S1-20260806-036"),
    ("S1-20260806-027", "S1-20260806-037"),
    ("S1-20260806-028", "S1-20260806-038"),
    ("S1-20260806-031", "S1-20260806-039"),
    ("S1-20260806-034", "S1-20260806-040"),
)
R3_PILOT_IDS = tuple(registration.PILOT_IDS)
R3_K_GATE_COMPLETION_LOGICAL_ID = EXPECTED_NEW_TO_LOGICAL[R3_PILOT_IDS[-1]]
ATTEMPT_LEDGER_ROOT = Path(
    "orchestration/s1/g1_thermodynamic_label_audit_r3_20260807/attempts"
)
SUPERVISOR_COMPLETION_PATH = Path(registration.SUPERVISOR_COMPLETION_PATH)
BARRIER_FAILURE_ROOT = Path(registration.BARRIER_FAILURE_ROOT)
SUPERVISOR_STATE_DIRECTORY = (
    "/home/shenwei01/.local/state/m_ofdft/"
    "g1_thermodynamic_label_audit_r3_20260807"
)
EOS_RATIOS = (0.90, 0.94, 0.97, 1.00, 1.03, 1.06, 1.10)
ANCHOR_RATIOS = (0.90, 1.00, 1.10)
BOHR_TO_ANGSTROM = r1.BOHR_TO_ANGSTROM

_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_ARCHIVE = re.compile(r"attempt-([0-9a-f]{12})\Z")
_UTC = re.compile(
    r"20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,9})?Z\Z"
)
_BOOT_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z"
)


def _assert_static_namespace() -> None:
    if R1_REUSED_AUDIT_IDS != EXPECTED_REUSED_LOGICAL_IDS:
        raise RuntimeError("R3 must not reuse any historical R1 run")
    if NEW_TO_LOGICAL != EXPECTED_NEW_TO_LOGICAL:
        raise RuntimeError("generator changed the frozen R3-to-logical mapping")
    if R3_AUDIT_IDS != tuple(EXPECTED_NEW_TO_LOGICAL):
        raise RuntimeError("R3 physical ID order differs from 001--040")
    expected_effective = {
        **{logical: physical for physical, logical in EXPECTED_NEW_TO_LOGICAL.items()},
    }
    if LOGICAL_TO_EFFECTIVE_ID != expected_effective or set(expected_effective) != set(LOGICAL_IDS):
        raise RuntimeError("logical/effective forty-slot resolver differs")
    if EXECUTION_ORDER != R3_AUDIT_IDS:
        raise RuntimeError("R3 execution order must be the exact 001--040 sequence")


_assert_static_namespace()


def _git(project_root: Path, *args: str, text: bool = True) -> str | bytes:
    output = subprocess.check_output(["git", "-C", str(project_root), *args], text=text)
    return output.strip() if text else output


def _canonical_supervisor_json(payload: object) -> bytes:
    return (
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")


def _stable_json_object(path: Path) -> tuple[bytes, dict[str, object], str]:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise ValueError("stable JSON read requires O_NOFOLLOW")
    descriptor = os.open(
        path,
        os.O_RDONLY
        | nofollow
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0),
    )
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"stable JSON path is not regular: {path}")
        blocks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            blocks.append(block)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    fields = (
        "st_dev",
        "st_ino",
        "st_mode",
        "st_nlink",
        "st_uid",
        "st_gid",
        "st_rdev",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    raw = b"".join(blocks)
    if any(getattr(before, key) != getattr(after, key) for key in fields) or len(
        raw
    ) != before.st_size:
        raise ValueError(f"stable JSON changed while being read: {path}")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"stable JSON root is not an object: {path}")
    return raw, value, hashlib.sha256(raw).hexdigest()


def _expected_registered_files(project_root: Path) -> dict[str, object]:
    return {
        "config_path": str(CONFIG_PATH),
        "config_sha256": sha256(project_root / CONFIG_PATH),
        "manifest_path": str(MANIFEST_PATH),
        "manifest_sha256": sha256(project_root / MANIFEST_PATH),
        "runner_path": "scripts/run_s1_g1_thermodynamic_label_audit_r3.sh",
        "runner_sha256": sha256(
            project_root / "scripts/run_s1_g1_thermodynamic_label_audit_r3.sh"
        ),
    }


def _expected_sealed_execution_inputs(
    project_root: Path, registered_files: dict[str, object]
) -> dict[str, object]:
    relative_paths = {
        "runner": Path("scripts/run_s1_g1_thermodynamic_label_audit_r3.sh"),
        "manifest": MANIFEST_PATH,
        "config": CONFIG_PATH,
    }
    return {
        "mode": registration.SEALED_EXECUTION_INPUT_MODE,
        "seal_mask": registration.SEALED_EXECUTION_INPUT_SEAL_MASK,
        "seal_names": list(registration.SEALED_EXECUTION_INPUT_SEAL_NAMES),
        "pass_fds": list(registration.SEALED_EXECUTION_INPUT_FDS.values()),
        "inputs": {
            name: {
                "fd": registration.SEALED_EXECUTION_INPUT_FDS[name],
                "proc_path": registration.SEALED_EXECUTION_INPUT_PROC_PATHS[name],
                "canonical_path": str(project_root / relative_paths[name]),
                "sha256": registered_files[f"{name}_sha256"],
            }
            for name in registration.SEALED_EXECUTION_INPUT_FDS
        },
    }


def _sealed_execution_inputs_sha256(record: dict[str, object]) -> str:
    return hashlib.sha256(_canonical_supervisor_json(record)).hexdigest()


def _read_fd_bytes(descriptor: int) -> bytes:
    size = os.fstat(descriptor).st_size
    blocks: list[bytes] = []
    offset = 0
    while offset < size:
        block = os.pread(descriptor, min(1024 * 1024, size - offset), offset)
        if not block:
            break
        blocks.append(block)
        offset += len(block)
    payload = b"".join(blocks)
    if len(payload) != size:
        raise ValueError("short read from live sealed execution input")
    return payload


def _validate_live_sealed_execution_inputs(
    supervisor_pid: int, record: dict[str, object]
) -> list[str]:
    errors: list[str] = []
    if not _positive_integer(supervisor_pid):
        return ["live sealed execution supervisor PID is invalid"]
    add_seals = getattr(fcntl, "F_ADD_SEALS", None)
    get_seals = getattr(fcntl, "F_GET_SEALS", None)
    mask = 0
    for name in registration.SEALED_EXECUTION_INPUT_SEAL_NAMES:
        value = getattr(fcntl, name, None)
        if type(value) is not int:
            errors.append(f"live sealed execution constant is unavailable: {name}")
            return errors
        mask |= value
    if type(add_seals) is not int or type(get_seals) is not int:
        return ["live sealed execution fcntl operations are unavailable"]
    if mask != registration.SEALED_EXECUTION_INPUT_SEAL_MASK:
        return ["live sealed execution kernel seal mask differs"]
    inputs = record.get("inputs")
    if not isinstance(inputs, dict):
        return ["live sealed execution input table is missing"]
    for name, fixed_fd in registration.SEALED_EXECUTION_INPUT_FDS.items():
        item = inputs.get(name)
        if not isinstance(item, dict):
            errors.append(f"live sealed execution {name} record is missing")
            continue
        path = Path(f"/proc/{supervisor_pid}/fd/{fixed_fd}")
        try:
            descriptor = os.open(
                path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            )
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise ValueError("FD is not regular")
                if fcntl.fcntl(descriptor, get_seals) != mask:
                    raise ValueError("seal mask differs")
                observed = hashlib.sha256(_read_fd_bytes(descriptor)).hexdigest()
            finally:
                os.close(descriptor)
            if observed != item.get("sha256"):
                errors.append(f"live sealed execution {name} SHA-256 differs")
        except (OSError, ValueError) as error:
            errors.append(f"live sealed execution {name} FD differs: {error}")
    return errors


def _relative(project_root: Path, path: Path) -> str:
    return path.resolve().relative_to(project_root.resolve()).as_posix()


def _is_ancestor(project_root: Path, ancestor: str, descendant: str) -> bool:
    return subprocess.run(
        ["git", "-C", str(project_root), "merge-base", "--is-ancestor", ancestor, descendant],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def _introduction_commits(project_root: Path, relative: str) -> list[str]:
    output = str(
        _git(
            project_root,
            "log",
            "--no-renames",
            "--format=%H",
            "--diff-filter=A",
            "--",
            relative,
        )
    )
    return output.splitlines() if output else []


def _introduction_commit(project_root: Path, relative: str) -> str:
    commits = _introduction_commits(project_root, relative)
    if len(commits) != 1:
        raise ValueError(f"expected one introduction commit for {relative}")
    return commits[0]


def _latest_introduction_commit(project_root: Path, relative: str) -> str:
    commits = _introduction_commits(project_root, relative)
    if not commits:
        raise ValueError(f"expected an introduction commit for {relative}")
    return commits[0]


def _commit_changed_paths(project_root: Path, commit: str) -> set[str]:
    raw = subprocess.check_output(
        [
            "git",
            "-C",
            str(project_root),
            "diff-tree",
            "--no-commit-id",
            "--no-renames",
            "--name-only",
            "-r",
            "-z",
            commit,
        ]
    )
    return {item.decode("utf-8") for item in raw.split(b"\0") if item}


def _introduced_paths_under(
    project_root: Path, relative_root: str
) -> list[tuple[str, str]]:
    """Return every path introduction under a namespace in current HEAD history."""

    commits = str(
        _git(
            project_root,
            "log",
            "--format=%H",
            "--diff-filter=A",
            "HEAD",
            "--",
            relative_root,
        )
    ).splitlines()
    introductions: list[tuple[str, str]] = []
    for commit in commits:
        raw = subprocess.check_output(
            [
                "git",
                "-C",
                str(project_root),
                "diff-tree",
                "--no-commit-id",
                "--no-renames",
                "--diff-filter=A",
                "--name-only",
                "-r",
                "-z",
                commit,
                "--",
                relative_root,
            ]
        )
        introductions.extend(
            (commit, item.decode("utf-8")) for item in raw.split(b"\0") if item
        )
    return introductions


def _post_terminal_history_failure(
    project_root: Path,
    terminal_commit: str,
    allowed_paths: set[str],
) -> str | None:
    """Allow only linear documentation bookkeeping after a terminal evidence commit."""

    head = str(_git(project_root, "rev-parse", "HEAD"))
    if not _is_ancestor(project_root, terminal_commit, head):
        return "terminal evidence commit is not an ancestor of HEAD"
    commits = str(
        _git(project_root, "rev-list", "--reverse", f"{terminal_commit}..{head}")
    ).splitlines()
    for commit in commits:
        parents = str(_git(project_root, "rev-list", "--parents", "-n", "1", commit)).split()
        changed = _commit_changed_paths(project_root, commit)
        if len(parents) != 2:
            return f"post-terminal commit is not linear: {commit}"
        if not changed or not changed.issubset(allowed_paths):
            return f"post-terminal commit changes non-documentation paths: {commit}"
    return None


def _blob_at(project_root: Path, revision: str, relative: str) -> bytes:
    return _git(project_root, "cat-file", "blob", f"{revision}:{relative}", text=False)


def _tree_oid(project_root: Path, revision: str, relative: str) -> str:
    oid = str(_git(project_root, "rev-parse", f"{revision}:{relative}"))
    if not _HEX40.fullmatch(oid):
        raise ValueError(f"invalid tree OID for {revision}:{relative}")
    if str(_git(project_root, "cat-file", "-t", oid)) != "tree":
        raise ValueError(f"Git object is not a tree: {revision}:{relative}")
    return oid


def _tracked_head_failure(project_root: Path, relative: str) -> str | None:
    path = project_root / relative
    if not path.is_file() or path.is_symlink():
        return f"not a regular non-symbolic file: {relative}"
    if subprocess.run(
        ["git", "-C", str(project_root), "ls-files", "--error-unmatch", "--", relative],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode:
        return f"not tracked: {relative}"
    if subprocess.run(
        ["git", "-C", str(project_root), "diff", "--quiet", "HEAD", "--", relative]
    ).returncode:
        return f"differs from HEAD: {relative}"
    return None


def _safe_project_path(project_root: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(f"{label} is not a nonempty path string")
    relative = Path(value)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError(f"{label} is not a canonical relative path")
    candidate = project_root / relative
    current = project_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} traverses a symbolic link")
    candidate.resolve().relative_to(project_root.resolve())
    return candidate


def _read_manifest(path: Path) -> list[dict[str, str]]:
    return read_manifest(path)


def effective_id(logical_experiment_id: str) -> str:
    try:
        return LOGICAL_TO_EFFECTIVE_ID[logical_experiment_id]
    except KeyError as error:
        raise ValueError(f"unknown logical R1 slot: {logical_experiment_id}") from error


def logical_effective_id(config: dict, logical_experiment_id: str) -> str:
    """Resolve one logical slot and verify the preregistered matrix agrees.

    The explicit ``config`` argument is intentional: analyzers cannot silently
    fall back to experiment-number arithmetic or to a different mapping.
    """

    resolved = effective_id(logical_experiment_id)
    matrix = config.get("logical_run_matrix")
    if not isinstance(matrix, list):
        raise ValueError("config lacks the forty-slot logical run matrix")
    matches = [
        item
        for item in matrix
        if isinstance(item, dict)
        and item.get("logical_experiment_id") == logical_experiment_id
    ]
    if len(matches) != 1 or matches[0].get("physical_experiment_id") != resolved:
        raise ValueError(f"config logical/effective mapping differs: {logical_experiment_id}")
    return resolved


def logical_id(experiment_id: str) -> str:
    if experiment_id in LOGICAL_TO_EFFECTIVE_ID and effective_id(experiment_id) == experiment_id:
        return experiment_id
    try:
        return NEW_TO_LOGICAL[experiment_id]
    except KeyError as error:
        raise ValueError(f"unknown effective R3 experiment ID: {experiment_id}") from error


def source_kind(logical_experiment_id: str) -> str:
    return "r1_reused" if logical_experiment_id in R1_REUSED_AUDIT_IDS else "r3_executed"


def _effective_reference(experiment_id: str) -> str:
    return LOGICAL_TO_EFFECTIVE_ID.get(experiment_id, experiment_id)


def _r1_rows(project_root: Path) -> list[dict[str, str]]:
    return _read_manifest(project_root / R1_MANIFEST_PATH)


def _r1_by_id(project_root: Path) -> dict[str, dict[str, str]]:
    return {row["experiment_id"]: row for row in _r1_rows(project_root)}


def _new_by_logical(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {NEW_TO_LOGICAL[row["experiment_id"]]: row for row in rows}


def row_for_logical(
    project_root: Path,
    rows: list[dict[str, str]],
    logical_experiment_id: str,
) -> dict[str, str]:
    if logical_experiment_id in R1_REUSED_AUDIT_IDS:
        return _r1_by_id(project_root)[logical_experiment_id]
    try:
        return _new_by_logical(rows)[logical_experiment_id]
    except KeyError as error:
        raise ValueError(f"R3 manifest lacks logical slot {logical_experiment_id}") from error


def _manifest_row_sha256(row: dict[str, str]) -> str:
    data = json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _expected_r1_bridge(project_root: Path) -> dict[str, object]:
    r1_config = read_json(project_root / R1_CONFIG_PATH)
    r1_rows = _r1_rows(project_root)
    return registration.build_r1_bridge(project_root, r1_config, r1_rows)


def _validate_r1_bridge(
    project_root: Path,
    config: dict,
    errors: list[str],
    *,
    require_committed: bool,
) -> dict[str, object]:
    details: dict[str, object] = {"reused": {}}
    try:
        if sha256(project_root / R1_CONFIG_PATH) != R1_CONFIG_SHA256:
            errors.append("frozen R1 config SHA-256 differs")
        if sha256(project_root / R1_MANIFEST_PATH) != R1_MANIFEST_SHA256:
            errors.append("frozen R1 manifest SHA-256 differs")
        if str(_git(project_root, "rev-parse", f"{R1_PREREGISTRATION_COMMIT}^{{commit}}")) != R1_PREREGISTRATION_COMMIT:
            errors.append("frozen R1 preregistration commit differs")
        expected = _expected_r1_bridge(project_root)
        if config.get("r1_bridge") != expected:
            errors.append("R1 bridge differs from the independently reconstructed frozen evidence")
        bridge = config.get("r1_bridge")
        if not isinstance(bridge, dict):
            raise ValueError("R1 bridge is not an object")
        if bridge.get("reused_logical_ids") != [] or bridge.get("reused_runs") != {}:
            errors.append("R1 bridge must exclude all historical runs from R3 reuse")
        if bridge.get("historical_accepted_logical_ids") != list(
            EXPECTED_HISTORICAL_ACCEPTED_LOGICAL_IDS
        ):
            errors.append("R1 historical accepted-run order differs")
        if bridge.get("historical_accepted_scientific_denominator_contribution") != 0:
            errors.append("R1 historical runs contribute to the R3 denominator")
        if bridge.get("historical_runtime_environment_replay_required") is not False:
            errors.append("R1 historical runtime replay exclusion differs")
        reused = bridge.get("historical_accepted_runs")
        if not isinstance(reused, dict) or set(reused) != set(
            EXPECTED_HISTORICAL_ACCEPTED_LOGICAL_IDS
        ):
            errors.append("R1 historical accepted-run anchor denominator differs")
            reused = {}

        introduction_commits: list[str] = []
        for experiment_id in EXPECTED_HISTORICAL_ACCEPTED_LOGICAL_IDS:
            anchor = reused.get(experiment_id)
            if not isinstance(anchor, dict):
                continue
            introduction = _latest_introduction_commit(
                project_root, f"runs/{experiment_id}/experiment_metadata.json"
            )
            introduction_commits.append(introduction)
            if anchor.get("introduction_commit") != introduction:
                errors.append(f"R1 historical introduction commit differs: {experiment_id}")
            if not _is_ancestor(project_root, R1_PREREGISTRATION_COMMIT, introduction):
                errors.append(f"R1 historical run predates R1 preregistration: {experiment_id}")
            if not _is_ancestor(project_root, introduction, R1_FAILURE_COMMIT) or introduction == R1_FAILURE_COMMIT:
                errors.append(f"R1 historical run is not before the frozen R1 failure: {experiment_id}")
            if anchor.get("tree_oid") != _tree_oid(project_root, "HEAD", f"runs/{experiment_id}"):
                errors.append(f"R1 historical run tree differs at HEAD: {experiment_id}")
            changed = _commit_changed_paths(project_root, introduction)
            prefix = f"runs/{experiment_id}/"
            if not changed or any(not path.startswith(prefix) for path in changed):
                errors.append(f"R1 historical run commit is not independently scoped: {experiment_id}")
            artifacts = anchor.get("artifacts")
            if not isinstance(artifacts, dict) or not artifacts:
                errors.append(f"R1 historical artifact anchor is missing: {experiment_id}")
            else:
                for artifact_name, artifact in artifacts.items():
                    if not isinstance(artifact_name, str) or not isinstance(artifact, dict):
                        errors.append(f"R1 historical artifact anchor is malformed: {experiment_id}")
                        continue
                    relative = artifact.get("path")
                    if not isinstance(relative, str):
                        errors.append(f"R1 historical artifact path is missing: {experiment_id}/{artifact_name}")
                        continue
                    failure = _tracked_head_failure(project_root, relative)
                    if failure:
                        errors.append(f"R1 historical {experiment_id}: {failure}")
                    elif artifact.get("sha256") != sha256(project_root / relative):
                        errors.append(f"R1 historical artifact SHA-256 differs: {experiment_id}/{artifact_name}")
        if len(set(introduction_commits)) != len(EXPECTED_HISTORICAL_ACCEPTED_LOGICAL_IDS):
            errors.append("R1 historical runs do not have ten distinct introduction commits")
        for earlier, later in zip(introduction_commits, introduction_commits[1:]):
            if not _is_ancestor(project_root, earlier, later) or earlier == later:
                errors.append("R1 historical-run commit ancestry differs from frozen execution order")

        if str(_git(project_root, "rev-parse", f"{R1_ARCHIVE_COMMIT}^")) != R1_FAILURE_COMMIT:
            errors.append("R1 archive commit is not adjacent to the frozen failure commit")
        if not (project_root / R1_ARCHIVE_PATH).is_dir() or (project_root / R1_ARCHIVE_PATH).is_symlink():
            errors.append("frozen R1 failed-attempt archive is missing or symbolic")
        else:
            failure_tree = _tree_oid(project_root, R1_FAILURE_COMMIT, f"runs/{R1_FAILURE_ID}")
            archive_tree = _tree_oid(project_root, R1_ARCHIVE_COMMIT, str(R1_ARCHIVE_PATH))
            head_tree = _tree_oid(project_root, "HEAD", str(R1_ARCHIVE_PATH))
            if not failure_tree == archive_tree == head_tree:
                errors.append("R1 failed-attempt tree changed during or after archival")
        if (project_root / "runs" / R1_FAILURE_ID).exists():
            errors.append("R1 failed ID 034 remains active")
        forbidden = (
            set(LOGICAL_IDS)
            - set(EXPECTED_HISTORICAL_ACCEPTED_LOGICAL_IDS)
            - {R1_FAILURE_ID}
        )
        for experiment_id in sorted(forbidden):
            if (project_root / "runs" / experiment_id).exists():
                errors.append(f"unexpected post-stop R1 active attempt: {experiment_id}")
            archive_root = project_root / "failed_runs/runtime_relocation" / experiment_id
            if archive_root.exists():
                errors.append(f"unexpected post-stop R1 failed archive: {experiment_id}")

        if require_committed:
            r1_config, r1_rows, _ = r1.validate_registration(
                project_root,
                project_root / R1_CONFIG_PATH,
                project_root / R1_MANIFEST_PATH,
                require_committed=True,
            )
            by_r1 = {row["experiment_id"]: row for row in r1_rows}
            archive_errors: list[str] = []
            events = r1._archive_events(project_root, R1_FAILURE_ID, archive_errors)
            if len(events) != 1 or events[0][0] != R1_ARCHIVE_PATH.name:
                archive_errors.append("R1-034 must have exactly the frozen no-retry archive")
            archive_errors.extend(
                r1.validate_failed_r1_run(
                    project_root,
                    by_r1[R1_FAILURE_ID],
                    require_committed=True,
                    directory=project_root / R1_ARCHIVE_PATH,
                )
            )
            errors.extend(f"R1 failed archive: {failure}" for failure in archive_errors)
            details["reused"] = {}
    except (
        FileNotFoundError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
    ) as error:
        errors.append(f"R1 bridge revalidation failed: {error}")
    return details


def _parse_kmesh(value: str) -> tuple[int, int, int]:
    fields = value.lower().split("x")
    if len(fields) != 3:
        raise ValueError("kmesh must contain three x-separated integers")
    mesh = tuple(int(field) for field in fields)
    if any(item <= 0 for item in mesh):
        raise ValueError("kmesh values must be positive")
    return mesh  # type: ignore[return-value]


def _expected_r3_suffix(r1_suffix: str) -> str:
    if "g1tlr1" not in r1_suffix:
        raise ValueError("R1 suffix lacks its frozen g1tlr1 token")
    return r1_suffix.replace("g1tlr1", "g1tlr3", 1)


def _validate_input_derivation(
    project_root: Path,
    row: dict[str, str],
    r1_row: dict[str, str],
    logical_experiment_id: str,
    errors: list[str],
) -> None:
    experiment_id = row["experiment_id"]
    prefix = f"{experiment_id}/{logical_experiment_id}:"
    try:
        directory = _safe_project_path(project_root, row["input_directory"], "R3 input directory")
        r1_directory = _safe_project_path(project_root, r1_row["input_directory"], "R1 input directory")
        if directory != project_root / INPUT_ROOT / experiment_id:
            errors.append(f"{prefix} input directory is not canonical")
        for basename, hash_key in (
            ("INPUT", "input_sha256"),
            ("STRU", "stru_sha256"),
            ("KPT", "kpt_sha256"),
            ("metadata.json", "metadata_sha256"),
        ):
            path = directory / basename
            digest = row[hash_key]
            if not path.is_file() or path.is_symlink():
                errors.append(f"{prefix} missing or symbolic {basename}")
            elif not _HEX64.fullmatch(digest) or sha256(path) != digest:
                errors.append(f"{prefix} {basename} SHA-256 differs")
        for basename in ("STRU", "KPT"):
            if (directory / basename).read_bytes() != (r1_directory / basename).read_bytes():
                errors.append(f"{prefix} {basename} differs from the frozen logical R1 input")
        before = parse_input_text((r1_directory / "INPUT").read_bytes())
        after = parse_input_text((directory / "INPUT").read_bytes())
        if set(before) != set(after):
            errors.append(f"{prefix} INPUT keyword set differs from the logical R1 input")
        for key in set(before) | set(after):
            expected = (
                (_expected_r3_suffix(r1_row["suffix"]),)
                if key == "suffix"
                else before.get(key)
            )
            if after.get(key) != expected:
                errors.append(f"{prefix} INPUT differs outside the registered R3 suffix: {key}")
        if row["suffix"] != _expected_r3_suffix(r1_row["suffix"]):
            errors.append(f"{prefix} manifest suffix differs from the exact R3 derivation")
        if _parse_kmesh(row["kmesh"]) != _parse_kmesh(r1_row["kmesh"]):
            errors.append(f"{prefix} physical k mesh changed")

        metadata = read_json(directory / "metadata.json")
        expected_metadata = {
            "protocol_revision": PROTOCOL_REVISION,
            "experiment_id": experiment_id,
            "material": row["material"],
            "volume_ratio": row["volume_ratio"],
            "smearing_level": row["smearing_level"],
            "smearing_sigma_ry": row["smearing_sigma_ry"],
            "run_role": row["run_role"],
            "source_experiment_id": row["source_experiment_id"],
            "reference_experiment_id": row["reference_experiment_id"],
            "common_quarter_partner_id": row["common_quarter_partner_id"],
            "pseudopotential": row["pseudopotential"],
            "expected_electrons": row["expected_electrons"],
            "atom_count": row["atom_count"],
            "cube_precision": row["cube_precision"],
            "density_basename": "chg.cube",
            "potential_basename": "pot.cube",
        }
        for key, expected in expected_metadata.items():
            if str(metadata.get(key, "")) != expected:
                errors.append(f"{prefix} metadata {key} differs")
        if metadata.get("kmesh") != list(_parse_kmesh(row["kmesh"])):
            errors.append(f"{prefix} metadata kmesh differs")
        r1_metadata = read_json(r1_directory / "metadata.json")
        expected_complete_metadata = deepcopy(r1_metadata)
        expected_complete_metadata.update(
            {
                "protocol_revision": PROTOCOL_REVISION,
                "experiment_id": experiment_id,
                "suffix": row["suffix"],
            }
        )
        if metadata != expected_complete_metadata:
            errors.append(
                f"{prefix} metadata changed beyond protocol revision, experiment ID, and suffix"
            )
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        errors.append(f"{prefix} input derivation validation failed: {error}")


def _validate_manifest_rows(
    project_root: Path,
    rows: list[dict[str, str]],
    config: dict,
    errors: list[str],
) -> None:
    ids = tuple(row.get("experiment_id", "") for row in rows)
    if ids != R3_AUDIT_IDS:
        errors.append("R3 manifest rows must be the exact 001--040 physical IDs in order")
    if tuple(row.get("execution_index", "") for row in rows) != tuple(
        str(index) for index in range(1, 41)
    ):
        errors.append("R3 manifest execution indices differ from the exact execution order")
    r1_by_id = _r1_by_id(project_root)
    for index, physical_id in enumerate(R3_AUDIT_IDS, 1):
        row = next((item for item in rows if item.get("experiment_id") == physical_id), None)
        if row is None:
            continue
        logical = NEW_TO_LOGICAL[physical_id]
        old = r1_by_id[logical]
        prefix = f"{physical_id}/{logical}:"
        if row.get("execution_phase") != ("P1" if physical_id in R3_PILOT_IDS else "P2"):
            errors.append(f"{prefix} execution phase differs")
        unchanged = set(MANIFEST_FIELDS) - {
            "execution_index",
            "execution_phase",
            "experiment_id",
            "input_directory",
            "reference_experiment_id",
            "common_quarter_partner_id",
            "suffix",
            "input_sha256",
            "metadata_sha256",
        }
        for field in unchanged:
            if row.get(field) != old.get(field):
                errors.append(f"{prefix} frozen scientific manifest field changed: {field}")
        if row.get("input_directory") != f"{INPUT_ROOT.as_posix()}/{physical_id}":
            errors.append(f"{prefix} input directory differs")
        if row.get("reference_experiment_id") != old["reference_experiment_id"]:
            errors.append(f"{prefix} logical scientific reference differs")
        if row.get("common_quarter_partner_id") != old["common_quarter_partner_id"]:
            errors.append(f"{prefix} logical common-quarter partner differs")
        if row.get("suffix") != _expected_r3_suffix(old["suffix"]):
            errors.append(f"{prefix} suffix differs")
        pseudo = project_root / "assets/pseudo" / row["pseudopotential"]
        if (
            Path(row["pseudopotential"]).name != row["pseudopotential"]
            or not pseudo.is_file()
            or pseudo.is_symlink()
            or not _HEX64.fullmatch(row["pseudopotential_sha256"])
            or sha256(pseudo) != row["pseudopotential_sha256"]
        ):
            errors.append(f"{prefix} pseudopotential identity differs")
        _validate_input_derivation(project_root, row, old, logical, errors)

    if config.get("registered_experiment_ids") != list(R3_AUDIT_IDS):
        errors.append("config registered physical IDs differ")
    if config.get("execution_order") != list(EXECUTION_ORDER):
        errors.append("config R3 execution order differs")


def _translated_energy_matrix(r1_config: dict) -> dict[str, object]:
    matrix = deepcopy(r1_config["energy_matrix"])
    points = matrix["points"]
    if not isinstance(points, list):
        raise ValueError("R1 energy matrix points are not a list")
    for point in points:
        if not isinstance(point, dict):
            raise ValueError("R1 energy matrix point is not an object")
        experiment_id = point.get("experiment_id")
        if isinstance(experiment_id, str) and experiment_id in LOGICAL_TO_EFFECTIVE_ID:
            point["logical_experiment_id"] = experiment_id
            point["experiment_id"] = effective_id(experiment_id)
            point["source_kind"] = source_kind(experiment_id)
    matrix["r1_reused_audit_run_count"] = 0
    matrix["r3_executed_audit_run_count"] = 40
    return matrix


def _translated_field_groups(r1_config: dict) -> dict[str, object]:
    groups = deepcopy(r1_config["field_label_groups"])
    by_smearing = groups.get("by_smearing")
    if not isinstance(by_smearing, dict):
        raise ValueError("R1 field groups are invalid")
    groups["by_smearing"] = {
        key: [effective_id(value) for value in values]
        for key, values in by_smearing.items()
    }
    extras = groups.get("extra_dense_quarter_ids")
    if not isinstance(extras, list):
        raise ValueError("R1 extra-dense group is invalid")
    groups["extra_dense_quarter_ids"] = [effective_id(value) for value in extras]
    return groups


def _validate_logical_matrices(
    project_root: Path,
    config: dict,
    rows: list[dict[str, str]],
    errors: list[str],
) -> None:
    r1_config = read_json(project_root / R1_CONFIG_PATH)
    r1_rows = _r1_rows(project_root)
    try:
        expected_logical, expected_new = registration.build_plan(
            project_root, r1_config, r1_rows
        )
        if config.get("logical_run_matrix") != expected_logical:
            errors.append("logical run matrix differs from the frozen forty-slot plan")
        if config.get("new_run_matrix") != expected_new:
            errors.append("new run matrix differs from the frozen forty-ID plan")
        logical_matrix = config.get("logical_run_matrix")
        if not isinstance(logical_matrix, list):
            return
        observed = {
            str(item.get("logical_experiment_id")): item
            for item in logical_matrix
            if isinstance(item, dict)
        }
        if tuple(observed) != LOGICAL_IDS:
            errors.append("logical run matrix order/denominator differs")
        for logical in LOGICAL_IDS:
            item = observed.get(logical)
            if not isinstance(item, dict):
                continue
            if (
                item.get("physical_experiment_id") != effective_id(logical)
                or item.get("effective_experiment_id") != effective_id(logical)
                or item.get("source_kind") != source_kind(logical)
                or item.get("evidence_origin") != source_kind(logical)
            ):
                errors.append(f"logical matrix resolver/source kind differs: {logical}")
    except (KeyError, TypeError, ValueError) as error:
        errors.append(f"cannot rebuild R3 logical/new matrices: {error}")


def _validate_config_contract(
    project_root: Path,
    config: dict,
    rows: list[dict[str, str]],
    errors: list[str],
) -> None:
    expected_keys = {
        "schema_version",
        "protocol_revision",
        "status",
        "preregistration_date",
        "generated_from_commit",
        "scope",
        "registered_experiment_ids",
        "execution_order",
        "logical_run_matrix",
        "new_run_matrix",
        "execution",
        "coverage",
        "energy_matrix",
        "field_label_groups",
        "numerical_axes",
        "output_contract",
        "thermodynamic_semantics",
        "acceptance",
        "formal_preregistration_commit_scope",
        "manifest",
        "input_root",
        "input_derivation",
        "r1_bridge",
        "r2_stopped_bridge",
        "base_evidence_commit",
        "upstream_evidence",
        "source_runs",
        "source_semantics",
        "runtime_source",
        "runtime",
        "runtime_audit",
        "kmp_contract",
        "rank_count",
        "implementation",
        "implementation_git_modes",
    }
    if set(config) != expected_keys:
        errors.append("R3 config top-level key set differs")
    if type(config.get("schema_version")) is not int or config.get("schema_version") != 2:
        errors.append("R3 config schema version differs")
    if config.get("protocol_revision") != PROTOCOL_REVISION:
        errors.append("R3 config protocol revision differs")
    if config.get("status") != "preregistered":
        errors.append("R3 config status must be preregistered")
    if config.get("preregistration_date") != "2026-08-07":
        errors.append("R3 preregistration date differs")
    if config.get("input_root") != str(INPUT_ROOT):
        errors.append("R3 input root differs")
    if config.get("scope") != "G1 third-smearing / dense-k thermodynamic-label R3 continuation only":
        errors.append("R3 scope statement differs")

    r1_config = read_json(project_root / R1_CONFIG_PATH)
    expected_execution = {
        "rank_count": 4,
        "pilot_ids": list(R3_PILOT_IDS),
        "k_gate_completion_ids": list(R3_PILOT_IDS),
        "phase_barriers_fail_closed": True,
        "stop_after_first_preserved_failure": True,
        "no_same_id_retry": True,
        "imported_r1_ids_non_executable": True,
        "detached_supervisor_required": True,
        "supervisor_hup_probe_required": True,
        "operator_go_gate_required": True,
        "single_writer_flock_required": True,
        "append_only_journal_required": True,
        "atomic_supervisor_evidence_publish_required": True,
        "runner_live_parent_binding_required": True,
        "exact_go_payload_required_before_runner": True,
        "go_validated_bytes_sha256_binding_required": True,
        "runner_exact_go_revalidation_required": True,
        "production_parser_registration_required_before_solver": True,
        "go_payload_required_keys_exact": list(
            registration.GO_PAYLOAD_REQUIRED_KEYS
        ),
        "runner_parent_binding_fields": [
            "state_directory",
            "supervisor_pid",
            "supervisor_start_time_ticks",
            "boot_id",
            "launch_sha256",
            "go_sha256",
        ],
        "ambient_environment": {
            "keys_exact": list(registration.FROZEN_AMBIENT_ENVIRONMENT_KEYS),
            "values_exact": dict(registration.FROZEN_AMBIENT_ENVIRONMENT_VALUES),
            "canonical_values_sha256": (
                registration.FROZEN_AMBIENT_ENVIRONMENT_SHA256
            ),
            "mutating_launcher_exact_match_required": True,
            "supervisor_umask_exact": "0022",
            "python_no_user_site_required": True,
            "validator_subprocess_explicit_environment_required": True,
            "supervisor_subprocess_explicit_environment_required": True,
            "runner_additional_binding_keys_exact": list(
                registration.RUNNER_BINDING_ENVIRONMENT_KEYS
            ),
            "runner_registered_bash_required": True,
        },
        "sealed_execution_inputs": {
            "mode": registration.SEALED_EXECUTION_INPUT_MODE,
            "fixed_fds_exact": dict(registration.SEALED_EXECUTION_INPUT_FDS),
            "proc_paths_exact": dict(
                registration.SEALED_EXECUTION_INPUT_PROC_PATHS
            ),
            "seal_mask_exact": registration.SEALED_EXECUTION_INPUT_SEAL_MASK,
            "seal_names_exact": list(
                registration.SEALED_EXECUTION_INPUT_SEAL_NAMES
            ),
            "popen_pass_fds_exact": list(
                registration.SEALED_EXECUTION_INPUT_FDS.values()
            ),
            "registered_bash_executes_runner_fd": True,
            "scientific_config_manifest_from_sealed_fds_required": True,
            "canonical_paths_provenance_only": True,
        },
        "attempt_ledger_root": str(ATTEMPT_LEDGER_ROOT),
        "supervisor_state_directory": SUPERVISOR_STATE_DIRECTORY,
        "detachment_attestation_path": str(
            registration.DETACHMENT_ATTESTATION_PATH
        ),
        "supervisor_completion_path": str(SUPERVISOR_COMPLETION_PATH),
        "barrier_failure_root": str(BARRIER_FAILURE_ROOT),
        "supervisor_completion_contract": {
            "scientific_analysis_status": "accepted",
            "overall_protocol_status_before_completion": "pending_supervisor_completion",
            "launcher_terminal_required_before_finalize": True,
            "finalize_writes_completion_evidence": True,
            "completion_commit_changed_path_exact": str(
                SUPERVISOR_COMPLETION_PATH
            ),
            "exact_scope_commit_required": True,
            "overall_acceptance_requires_committed_supervisor_completion": True,
            "final_acceptance_requires_validator_revalidation": True,
            "allowed_post_completion_commit_paths_exact": list(
                POST_TERMINAL_DOCUMENTATION_PATHS
            ),
            "required_keys_exact": list(
                registration.SUPERVISOR_COMPLETION_REQUIRED_KEYS
            ),
            "schema_version": 1,
            "status": "supervisor_completed",
            "runner_exit_code": 0,
            "analysis_audit_status": "accepted",
            "final_acceptance_policy": (
                "committed_completion_then_validator_revalidation"
            ),
        },
        "barrier_failure_contract": {
            "applies_to_every_barrier_command": True,
            "machine_readable_json_required": True,
            "failure_artifact_must_be_under_registered_root": True,
            "exact_scope_commit_required": True,
            "stop_immediately_after_failure_commit": True,
            "continue_or_retry_forbidden": True,
            "allowed_post_failure_commit_paths_exact": list(
                POST_TERMINAL_DOCUMENTATION_PATHS
            ),
            "required_keys_exact": list(registration.BARRIER_FAILURE_REQUIRED_KEYS),
            "schema_version": 1,
            "status": "barrier_failed",
            "exit_code_must_be_nonzero": True,
            "retry_policy": "stop_after_exact_scope_commit_no_continue_or_retry",
        },
        "attempt_marker": {
            "basename_template": "{experiment_id}.json",
            "external_basename_template": "{experiment_id}.json",
            "creation_contract": (
                "O_CREAT|O_EXCL_then_file_fsync_then_parent_directory_fsync_before_solver"
            ),
            "commit_scope": "exactly_one_attempt_ledger_marker",
            "run_introduction_parent_must_equal_marker_commit": True,
            "first_marker_commit_parent_must_equal_go_git_head": True,
            "subsequent_marker_commit_parent_must_equal_previous_accepted_run_introduction": True,
            "go_git_head_must_equal_detachment_introduction_commit": True,
            "validator_must_accept_marker_before_solver": True,
            "working_tree_clean_after_marker_commit": True,
            "same_id_retry_forbidden": True,
            "required_keys_exact": list(registration.ATTEMPT_MARKER_REQUIRED_KEYS),
            "schema_version": 1,
            "status": "formal_attempt_started",
            "retry_policy": "new_protocol_revision_and_new_experiment_ids_only",
        },
        "absolute_deadline_watchdog_seconds": r1_config["execution"][
            "absolute_deadline_watchdog_seconds"
        ],
    }
    if config.get("execution") != expected_execution:
        errors.append("R3 execution/no-retry/supervisor contract differs")
    observed_execution = config.get("execution")
    if isinstance(observed_execution, dict):
        for contract_name in (
            "supervisor_completion_contract",
            "barrier_failure_contract",
            "attempt_marker",
        ):
            observed_contract = observed_execution.get(contract_name)
            if not isinstance(observed_contract, dict) or type(
                observed_contract.get("schema_version")
            ) is not int:
                errors.append(f"R3 {contract_name} schema version type differs")
        completion_contract = observed_execution.get(
            "supervisor_completion_contract"
        )
        if (
            not isinstance(completion_contract, dict)
            or type(completion_contract.get("runner_exit_code")) is not int
            or completion_contract.get("runner_exit_code") != 0
        ):
            errors.append("R3 supervisor completion runner-exit type differs")
    expected_coverage = {
        "logical_run_count": 40,
        "r1_reused_accepted_count": 0,
        "r1_historical_accepted_excluded_count": 10,
        "r3_new_run_count": 40,
        "r1_failed_attempt_accepted_count": 0,
        "r2_rejected_archive_reused_count": 0,
        "selected_scalar_point_count": 42,
    }
    if config.get("coverage") != expected_coverage:
        errors.append("R3 all-new 40-run logical coverage accounting differs")
    expected_input_derivation = {
        "source_commit": R1_PREREGISTRATION_COMMIT,
        "source_root": str(R1_INPUT_ROOT),
        "source_root_tree_oid": registration.R1_INPUT_ROOT_TREE_OID,
        "derived_file_count": 160,
        "stru_and_kpt_byte_identical": True,
        "input_only_changed_key": "suffix",
        "metadata_changed_keys_exact": [
            "experiment_id",
            "protocol_revision",
            "suffix",
        ],
        "metadata_dataset_kind_preserved_as_r1_input_provenance": True,
    }
    if config.get("input_derivation") != expected_input_derivation:
        errors.append("R3 R1-input derivation contract differs")
    try:
        if config.get("r2_stopped_bridge") != registration.build_r2_stopped_bridge(
            project_root
        ):
            errors.append("R3 immutable R2 stopped bridge differs")
    except (FileNotFoundError, TypeError, ValueError, subprocess.CalledProcessError) as error:
        errors.append(f"R3 immutable R2 stopped bridge validation failed: {error}")
    for key in (
        "numerical_axes",
        "output_contract",
        "thermodynamic_semantics",
        "acceptance",
        "runtime",
        "runtime_audit",
        "kmp_contract",
        "rank_count",
    ):
        if config.get(key) != r1_config.get(key):
            errors.append(f"R3 frozen R1 scientific/runtime contract changed: {key}")
    for key in (
        "base_evidence_commit",
        "upstream_evidence",
        "source_runs",
        "source_semantics",
        "runtime_source",
    ):
        if config.get(key) != r1_config.get(key):
            errors.append(f"R3 frozen R1 upstream/source contract changed: {key}")
    try:
        if config.get("energy_matrix") != _translated_energy_matrix(r1_config):
            errors.append("R3 translated 42-point energy matrix differs")
        if config.get("field_label_groups") != _translated_field_groups(r1_config):
            errors.append("R3 translated field-label groups differ")
    except (KeyError, TypeError, ValueError) as error:
        errors.append(f"R3 translated scientific matrix validation failed: {error}")
    _validate_logical_matrices(project_root, config, rows, errors)

    manifest = config.get("manifest")
    if not isinstance(manifest, dict):
        errors.append("R3 manifest registration is missing")
    else:
        if manifest.get("path") != str(MANIFEST_PATH):
            errors.append("R3 manifest path differs")
        if manifest.get("sha256") != sha256(project_root / MANIFEST_PATH):
            errors.append("R3 manifest SHA-256 differs")
        if manifest.get("row_count") != 40:
            errors.append("R3 manifest row count differs")
        if manifest.get("fields") != list(MANIFEST_FIELDS):
            errors.append("R3 manifest field registration differs")
    expected_scope = {
        "include_exactly": [str(CONFIG_PATH), str(MANIFEST_PATH), str(INPUT_ROOT)],
        "implementation_must_be_in_parent_commit": True,
        "run_failure_archive_or_attempt_ledger_evidence_allowed": False,
    }
    if config.get("formal_preregistration_commit_scope") != expected_scope:
        errors.append("R3 formal preregistration commit-scope contract differs")


def _validate_implementation(
    project_root: Path,
    config: dict,
    errors: list[str],
    *,
    require_committed: bool,
) -> None:
    implementation = config.get("implementation")
    modes = config.get("implementation_git_modes")
    if not isinstance(implementation, dict) or not implementation:
        errors.append("R3 implementation closure is missing")
        return
    if not isinstance(modes, dict) or set(modes) != set(implementation):
        errors.append("R3 implementation Git-mode closure differs")
        return
    required = {
        "docs/S1_G1_THERMODYNAMIC_LABEL_AUDIT_R3_PROTOCOL.md",
        "scripts/generate_s1_g1_thermodynamic_label_audit_r3.py",
        "scripts/parse_s1_g1_thermodynamic_labels_r3.py",
        "scripts/validate_s1_g1_thermodynamic_label_audit_r3.py",
        "scripts/analyze_s1_g1_thermodynamic_label_audit_r3.py",
        "scripts/run_s1_g1_thermodynamic_label_audit_r3.sh",
        "scripts/launch_s1_g1_thermodynamic_label_audit_r3.py",
        "tests/unit/test_s1_g1_thermodynamic_label_audit_r3_validator.py",
    }
    if not required.issubset(implementation):
        errors.append("R3 implementation closure omits a required path")
    for relative, digest in implementation.items():
        try:
            path = _safe_project_path(project_root, relative, "implementation path")
            if not path.is_file() or path.is_symlink():
                errors.append(f"R3 implementation is missing or symbolic: {relative}")
                continue
            if not isinstance(digest, str) or sha256(path) != digest:
                errors.append(f"R3 implementation SHA-256 differs: {relative}")
            if require_committed:
                failure = _tracked_head_failure(project_root, relative)
                if failure:
                    errors.append(f"R3 implementation {failure}")
                    continue
                entries = str(_git(project_root, "ls-files", "--stage", "--", relative)).splitlines()
                if len(entries) != 1:
                    errors.append(f"R3 implementation is not tracked exactly once: {relative}")
                    continue
                mode = entries[0].split(maxsplit=1)[0]
                if mode not in {"100644", "100755"} or modes.get(relative) != mode:
                    errors.append(f"R3 implementation Git mode differs: {relative}")
        except (FileNotFoundError, TypeError, ValueError) as error:
            errors.append(f"R3 implementation validation failed {relative}: {error}")


def _validate_preregistration(
    project_root: Path,
    config: dict,
    rows: list[dict[str, str]],
    errors: list[str],
) -> str | None:
    try:
        prereg = _introduction_commit(project_root, str(CONFIG_PATH))
        if _introduction_commit(project_root, str(MANIFEST_PATH)) != prereg:
            errors.append("R3 config and manifest were not introduced together")
        expected_inputs = {
            f"{row['input_directory']}/{basename}"
            for row in rows
            for basename in ("INPUT", "STRU", "KPT", "metadata.json")
        }
        expected_scope = {str(CONFIG_PATH), str(MANIFEST_PATH), *expected_inputs}
        if _commit_changed_paths(project_root, prereg) != expected_scope:
            errors.append("R3 preregistration scope is not config+manifest+complete 40-run input tree")
        parent = str(_git(project_root, "rev-parse", f"{prereg}^"))
        if config.get("generated_from_commit") != parent:
            errors.append("R3 preregistration parent differs from generated_from_commit")
        for relative in (str(CONFIG_PATH), str(MANIFEST_PATH)):
            if _blob_at(project_root, prereg, relative) != (project_root / relative).read_bytes():
                errors.append(f"R3 preregistration blob differs: {relative}")
        implementation = config.get("implementation")
        if isinstance(implementation, dict):
            for relative in implementation:
                if _blob_at(project_root, parent, relative) != (project_root / relative).read_bytes():
                    errors.append(f"R3 implementation differs from preregistration parent: {relative}")
        for row in rows:
            experiment_id = row["experiment_id"]
            input_directory = row["input_directory"]
            if str(_git(project_root, "ls-tree", "-r", parent, "--", f"runs/{experiment_id}")):
                errors.append(f"R3 run existed before preregistration: {experiment_id}")
            if str(_git(project_root, "ls-tree", "-r", parent, "--", input_directory)):
                errors.append(f"R3 input existed before preregistration: {experiment_id}")
            if _tree_oid(project_root, prereg, input_directory) != _tree_oid(
                project_root, "HEAD", input_directory
            ):
                errors.append(f"R3 input changed after preregistration: {experiment_id}")
            for basename in ("INPUT", "STRU", "KPT", "metadata.json"):
                relative = f"{input_directory}/{basename}"
                if _introduction_commit(project_root, relative) != prereg:
                    errors.append(f"R3 input was not introduced by preregistration: {relative}")
                if _blob_at(project_root, prereg, relative) != (project_root / relative).read_bytes():
                    errors.append(f"R3 input differs from preregistration: {relative}")
        return prereg
    except (
        FileNotFoundError,
        KeyError,
        TypeError,
        ValueError,
        subprocess.CalledProcessError,
    ) as error:
        errors.append(f"R3 preregistration binding failed: {error}")
        return None


def _active_introduction(
    project_root: Path, experiment_id: str, errors: list[str]
) -> str | None:
    run = project_root / "runs" / experiment_id
    if not run.exists():
        return None
    if not run.is_dir() or run.is_symlink():
        errors.append(f"R3 active run is invalid: {experiment_id}")
        return None
    metadata = run / "experiment_metadata.json"
    status = run / STATUS_NAME
    marker = metadata if metadata.is_file() and not metadata.is_symlink() else status
    if not marker.is_file() or marker.is_symlink():
        errors.append(f"R3 active run lacks metadata/status marker: {experiment_id}")
        return None
    try:
        return _latest_introduction_commit(project_root, _relative(project_root, marker))
    except ValueError as error:
        errors.append(f"cannot bind R3 active run {experiment_id}: {error}")
        return None


def _archive_events(
    project_root: Path, experiment_id: str, errors: list[str]
) -> list[tuple[str, str]]:
    root = project_root / "failed_runs/runtime_relocation" / experiment_id
    if not root.exists():
        return []
    if not root.is_dir() or root.is_symlink():
        errors.append(f"R3 failed-archive root is invalid: {experiment_id}")
        return []
    events: list[tuple[str, str]] = []
    for attempt in sorted(root.iterdir(), key=lambda path: path.name):
        match = _ARCHIVE.fullmatch(attempt.name)
        if not attempt.is_dir() or attempt.is_symlink() or match is None:
            errors.append(f"R3 failed archive is non-canonical: {experiment_id}/{attempt.name}")
            continue
        try:
            failure_commit = str(_git(project_root, "rev-parse", f"{match.group(1)}^{{commit}}"))
            archive_relative = _relative(project_root, attempt)
            archive_commit = _introduction_commit(project_root, archive_relative)
            if str(_git(project_root, "rev-parse", f"{archive_commit}^")) != failure_commit:
                errors.append(f"R3 archive is not adjacent to failed commit: {experiment_id}")
            run_relative = f"runs/{experiment_id}"
            failure_tree = _tree_oid(project_root, failure_commit, run_relative)
            archive_tree = _tree_oid(project_root, archive_commit, archive_relative)
            head_tree = _tree_oid(project_root, "HEAD", archive_relative)
            if not failure_tree == archive_tree == head_tree:
                errors.append(f"R3 failed tree changed during or after archival: {experiment_id}")
            failure_paths = _commit_changed_paths(project_root, failure_commit)
            if not failure_paths or any(not path.startswith(f"{run_relative}/") for path in failure_paths):
                errors.append(f"R3 failed run commit is not independently scoped: {experiment_id}")
            archive_paths = _commit_changed_paths(project_root, archive_commit)
            if not archive_paths or any(
                not (
                    path.startswith(f"{run_relative}/")
                    or path.startswith(f"{archive_relative}/")
                )
                for path in archive_paths
            ):
                errors.append(f"R3 archive commit scope differs: {experiment_id}")
            if subprocess.run(
                ["git", "-C", str(project_root), "cat-file", "-e", f"{archive_commit}:{run_relative}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode == 0:
                errors.append(f"R3 failed run was copied instead of moved: {experiment_id}")
            events.append((attempt.name, failure_commit))
        except (subprocess.CalledProcessError, ValueError) as error:
            errors.append(f"cannot bind R3 failed archive {experiment_id}: {error}")
    return events


def validate_detachment_attestation(
    project_root: Path,
    config: dict,
    *,
    require_committed: bool,
    require_live_sealed_inputs: bool = False,
) -> tuple[dict[str, object], list[str]]:
    """Independently replay the one-shot SSH/PTY detachment and HUP gate."""

    errors: list[str] = []
    payload: dict[str, object] = {}
    execution = config.get("execution")
    if not isinstance(execution, dict):
        return payload, ["R3 execution contract is missing"]
    relative = execution.get("detachment_attestation_path")
    if relative != str(registration.DETACHMENT_ATTESTATION_PATH):
        return payload, ["detachment attestation path differs from registration"]
    path = project_root / str(relative)
    if not path.is_file() or path.is_symlink():
        return payload, ["detachment attestation is missing or symbolic"]
    try:
        expected_registered = _expected_registered_files(project_root)
        expected_sealed_inputs = _expected_sealed_execution_inputs(
            project_root, expected_registered
        )
        parsed = read_json(path)
        if not isinstance(parsed, dict):
            raise ValueError("detachment attestation root is not an object")
        payload = parsed
        expected_keys = {
            "schema_version",
            "protocol_revision",
            "status",
            "launch_path",
            "launch_sha256",
            "boot_id",
            "supervisor_process_before_hup",
            "supervisor_process_after_hup",
            "hup_event_count_before",
            "hup_event_count_after",
            "registered_files",
            "git_head",
            "attested_utc",
        }
        if set(payload) != expected_keys:
            errors.append("detachment attestation key set differs")
        if (
            type(payload.get("schema_version")) is not int
            or payload.get("schema_version") != 1
            or payload.get("protocol_revision") != PROTOCOL_REVISION
            or payload.get("status") != "accepted"
        ):
            errors.append("detachment attestation identity/status differs")
        if not isinstance(payload.get("attested_utc"), str) or not _UTC.fullmatch(
            str(payload["attested_utc"])
        ):
            errors.append("detachment attestation UTC timestamp differs")
        if not isinstance(payload.get("boot_id"), str) or not _BOOT_ID.fullmatch(
            str(payload["boot_id"])
        ):
            errors.append("detachment attestation boot ID is invalid")
        if not isinstance(payload.get("launch_sha256"), str) or not _HEX64.fullmatch(
            str(payload["launch_sha256"])
        ):
            errors.append("detachment launch SHA-256 is invalid")
        launch_path = Path(str(payload.get("launch_path", "")))
        expected_launch = Path(SUPERVISOR_STATE_DIRECTORY) / "launch.json"
        if launch_path != expected_launch:
            errors.append("detachment launch path differs")
        if not launch_path.is_file() or launch_path.is_symlink():
            errors.append("detachment launch record is missing or symbolic")
            launch: dict[str, object] = {}
        else:
            launch_payload = read_json(launch_path)
            launch = launch_payload if isinstance(launch_payload, dict) else {}
            if payload.get("launch_sha256") != sha256(launch_path):
                errors.append("detachment launch SHA-256 differs")
            launch_keys = {
                "schema_version",
                "protocol_revision",
                "status",
                "launch_method",
                "restart_policy",
                "project_root",
                "hostname",
                "working_directory",
                "umask",
                "environment",
                "state_directory",
                "lock_path",
                "log_path",
                "boot_id",
                "process",
                "git_head_at_launch",
                "registered_files",
                "sealed_execution_inputs",
                "launcher",
                "runner_argv",
                "started_utc",
            }
            if set(launch) != launch_keys:
                errors.append("detachment launch key set differs")
            if type(launch.get("schema_version")) is not int:
                errors.append("detachment launch schema_version differs")
            state = Path(SUPERVISOR_STATE_DIRECTORY)
            expected_launch_scalars = {
                "schema_version": 1,
                "protocol_revision": PROTOCOL_REVISION,
                "status": "waiting_for_detachment_attestation",
                "launch_method": "python_subprocess_start_new_session",
                "restart_policy": "never",
                "project_root": str(project_root),
                "hostname": os.uname().nodename,
                "working_directory": str(project_root),
                "umask": "0022",
                "state_directory": SUPERVISOR_STATE_DIRECTORY,
                "lock_path": str(state / "supervisor.lock"),
                "log_path": str(state / "supervisor.log"),
                "boot_id": payload.get("boot_id"),
                "git_head_at_launch": payload.get("git_head"),
                "registered_files": payload.get("registered_files"),
            }
            for key, expected in expected_launch_scalars.items():
                if launch.get(key) != expected:
                    errors.append(f"detachment launch {key} differs")
            if not isinstance(launch.get("started_utc"), str) or not _UTC.fullmatch(
                str(launch["started_utc"])
            ):
                errors.append("detachment launch UTC timestamp differs")
            expected_ambient_contract = {
                "keys_exact": list(registration.FROZEN_AMBIENT_ENVIRONMENT_KEYS),
                "values_exact": dict(
                    registration.FROZEN_AMBIENT_ENVIRONMENT_VALUES
                ),
                "canonical_values_sha256": (
                    registration.FROZEN_AMBIENT_ENVIRONMENT_SHA256
                ),
                "mutating_launcher_exact_match_required": True,
                "supervisor_umask_exact": "0022",
                "python_no_user_site_required": True,
                "validator_subprocess_explicit_environment_required": True,
                "supervisor_subprocess_explicit_environment_required": True,
                "runner_additional_binding_keys_exact": list(
                    registration.RUNNER_BINDING_ENVIRONMENT_KEYS
                ),
                "runner_registered_bash_required": True,
            }
            if execution.get("ambient_environment") != expected_ambient_contract:
                errors.append("detachment ambient-environment contract differs")
            expected_launch_environment = {
                key: expected_ambient_contract[key]
                for key in (
                    "keys_exact",
                    "values_exact",
                    "canonical_values_sha256",
                )
            }
            if launch.get("environment") != expected_launch_environment:
                errors.append("detachment launch ambient environment differs")
            if launch.get("registered_files") != expected_registered:
                errors.append("detachment launch registered files differ")
            if launch.get("sealed_execution_inputs") != expected_sealed_inputs:
                errors.append("detachment launch sealed execution inputs differ")
            runtime = config.get("runtime")
            tools = runtime.get("tools") if isinstance(runtime, dict) else None
            python_registration = (
                tools.get("python") if isinstance(tools, dict) else None
            )
            bash_registration = (
                tools.get("bash") if isinstance(tools, dict) else None
            )
            bash_path = (
                bash_registration.get("path")
                if isinstance(bash_registration, dict)
                else None
            )
            if not isinstance(bash_path, str) or not Path(bash_path).is_absolute():
                errors.append("detachment registered Bash path is invalid")
            else:
                expected_runner_argv = [
                    bash_path,
                    registration.SEALED_EXECUTION_INPUT_PROC_PATHS["runner"],
                    str(project_root),
                    str(project_root / MANIFEST_PATH),
                    str(project_root / CONFIG_PATH),
                    registration.SEALED_EXECUTION_INPUT_PROC_PATHS["manifest"],
                    registration.SEALED_EXECUTION_INPUT_PROC_PATHS["config"],
                ]
                if launch.get("runner_argv") != expected_runner_argv:
                    errors.append("detachment launch runner argv differs")
            launcher_path = (
                project_root
                / "scripts/launch_s1_g1_thermodynamic_label_audit_r3.py"
            )
            if not isinstance(python_registration, dict):
                errors.append("detachment registered Python identity is missing")
            elif not launcher_path.is_file() or launcher_path.is_symlink():
                errors.append("detachment registered launcher is missing or symbolic")
            else:
                expected_launcher = {
                    "path": str(launcher_path),
                    "sha256": sha256(launcher_path),
                    "python_path": python_registration.get("path"),
                    "python_realpath": python_registration.get("realpath"),
                    "python_sha256": python_registration.get("sha256"),
                }
                if launch.get("launcher") != expected_launcher:
                    errors.append("detachment launch tool identity differs")

        process_keys = {
            "pid",
            "ppid",
            "process_group_id",
            "session_id",
            "tty_nr",
            "start_time_ticks",
            "stdin",
            "stdout",
            "stderr",
        }
        before = payload.get("supervisor_process_before_hup")
        after = payload.get("supervisor_process_after_hup")
        expected_log = str(Path(SUPERVISOR_STATE_DIRECTORY) / "supervisor.log")
        for label, process in (("before", before), ("after", after)):
            if not isinstance(process, dict) or set(process) != process_keys:
                errors.append(f"detachment {label}-HUP process record differs")
                continue
            pid = process.get("pid")
            if (
                any(
                    not _positive_integer(process.get(field))
                    for field in (
                        "pid",
                        "ppid",
                        "process_group_id",
                        "session_id",
                        "start_time_ticks",
                    )
                )
                or type(process.get("tty_nr")) is not int
                or process.get("session_id") != pid
                or process.get("process_group_id") != pid
                or process.get("tty_nr") != 0
                or process.get("stdin") != "/dev/null"
                or process.get("stdout") != expected_log
                or process.get("stderr") != expected_log
            ):
                errors.append(f"detachment {label}-HUP process is not fully detached")
        if isinstance(before, dict) and isinstance(after, dict):
            stable_process_keys = process_keys - {"ppid"}
            if any(before.get(key) != after.get(key) for key in stable_process_keys):
                errors.append("detachment supervisor identity changed across HUP")
        launch_process = launch.get("process")
        if not isinstance(launch_process, dict):
            errors.append("detachment external launch process identity is missing")
        else:
            if set(launch_process) != process_keys:
                errors.append("detachment external launch process key set differs")
            if (
                any(
                    not _positive_integer(launch_process.get(field))
                    for field in (
                        "pid",
                        "ppid",
                        "process_group_id",
                        "session_id",
                        "start_time_ticks",
                    )
                )
                or type(launch_process.get("tty_nr")) is not int
                or launch_process.get("tty_nr") != 0
            ):
                errors.append("detachment external launch process integers differ")
            stable_process_keys = process_keys - {"ppid"}
            for label, process in (("before", before), ("after", after)):
                if not isinstance(process, dict):
                    continue
                if any(
                    process.get(key) != launch_process.get(key)
                    for key in stable_process_keys
                ):
                    errors.append(
                        f"detachment {label}-HUP identity does not match external "
                        "launch process"
                    )
            if require_live_sealed_inputs:
                errors.extend(
                    _validate_live_sealed_execution_inputs(
                        int(launch_process.get("pid", 0)), expected_sealed_inputs
                    )
                )
        hup_before = payload.get("hup_event_count_before")
        hup_after = payload.get("hup_event_count_after")
        if (
            not isinstance(hup_before, int)
            or isinstance(hup_before, bool)
            or hup_before < 0
            or not isinstance(hup_after, int)
            or isinstance(hup_after, bool)
            or hup_after != hup_before + 1
        ):
            errors.append("detachment HUP event count is not exactly one")

        journal_path = Path(SUPERVISOR_STATE_DIRECTORY) / "journal.jsonl"
        if not journal_path.is_file() or journal_path.is_symlink():
            errors.append("detachment supervisor journal is missing or symbolic")
        else:
            journal_bytes = journal_path.read_bytes()
            if not journal_bytes or not journal_bytes.endswith(b"\n"):
                errors.append("detachment supervisor journal is not newline terminated")
            journal: list[dict[str, object]] = []
            event_keys = {
                "waiting_for_go": {"event", "pid", "utc"},
                "waiting_heartbeat": {"event", "pid", "utc"},
                "sighup_received": {"event", "pid", "utc"},
                "go_accepted": {
                    "event",
                    "pid",
                    "utc",
                    "git_head",
                    "go_sha256",
                },
                "go_rejected": {"event", "pid", "utc", "reason"},
                "runner_started": {
                    "event",
                    "pid",
                    "utc",
                    "child_pid",
                    "child_start_time_ticks",
                },
                "runner_finished": {"event", "pid", "utc", "return_code"},
            }
            launch_pid = (
                launch_process.get("pid") if isinstance(launch_process, dict) else None
            )
            for index, line in enumerate(
                journal_bytes.decode("utf-8").splitlines(), 1
            ):
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError(
                        f"detachment supervisor journal event {index} is not an object"
                    )
                journal.append(event)
                name = event.get("event")
                if not isinstance(name, str) or name not in event_keys:
                    errors.append(
                        f"detachment supervisor journal event {index} name differs"
                    )
                    continue
                if set(event) != event_keys[name]:
                    errors.append(
                        f"detachment supervisor journal event {index} key set differs"
                    )
                if (
                    not _positive_integer(event.get("pid"))
                    or event.get("pid") != launch_pid
                    or not isinstance(event.get("utc"), str)
                    or not _UTC.fullmatch(str(event["utc"]))
                ):
                    errors.append(
                        f"detachment supervisor journal event {index} identity differs"
                    )
                if name == "go_accepted" and (
                    not isinstance(event.get("git_head"), str)
                    or not _HEX40.fullmatch(str(event["git_head"]))
                    or not isinstance(event.get("go_sha256"), str)
                    or not _HEX64.fullmatch(str(event["go_sha256"]))
                ):
                    errors.append(
                        f"detachment supervisor journal event {index} GO identity differs"
                    )
                if name == "go_rejected" and not isinstance(
                    event.get("reason"), str
                ):
                    errors.append(
                        f"detachment supervisor journal event {index} reason differs"
                    )
                if name == "runner_started" and (
                    not _positive_integer(event.get("child_pid"))
                    or not _positive_integer(event.get("child_start_time_ticks"))
                ):
                    errors.append(
                        f"detachment supervisor journal event {index} child identity differs"
                    )
                if name == "runner_finished" and (
                    not isinstance(event.get("return_code"), int)
                    or isinstance(event.get("return_code"), bool)
                ):
                    errors.append(
                        f"detachment supervisor journal event {index} return code differs"
                    )

            names = [event.get("event") for event in journal]
            waiting = [index for index, name in enumerate(names) if name == "waiting_for_go"]
            hups = [index for index, name in enumerate(names) if name == "sighup_received"]
            accepted = [index for index, name in enumerate(names) if name == "go_accepted"]
            rejected = [index for index, name in enumerate(names) if name == "go_rejected"]
            started = [index for index, name in enumerate(names) if name == "runner_started"]
            finished = [index for index, name in enumerate(names) if name == "runner_finished"]
            if waiting != [0]:
                errors.append("detachment supervisor journal must start with one waiting event")
            if (
                not isinstance(hup_after, int)
                or isinstance(hup_after, bool)
                or len(hups) != hup_after
            ):
                errors.append(
                    "detachment supervisor journal HUP event count differs from attestation"
                )
            terminal_go = sorted([*accepted, *rejected])
            if len(terminal_go) > 1:
                errors.append("detachment supervisor journal has multiple GO decisions")
            if terminal_go and (
                any(index >= terminal_go[0] for index in hups)
                or any(
                    index >= terminal_go[0]
                    for index, name in enumerate(names)
                    if name == "waiting_heartbeat"
                )
            ):
                errors.append("detachment supervisor journal HUP/wait ordering differs")
            if rejected and (accepted or started or finished):
                errors.append("detachment supervisor journal continued after rejected GO")
            if started and (
                len(started) != 1 or len(accepted) != 1 or accepted[0] >= started[0]
            ):
                errors.append("detachment supervisor journal runner-start ordering differs")
            if finished and (
                len(finished) != 1 or len(started) != 1 or started[0] >= finished[0]
            ):
                errors.append("detachment supervisor journal runner-finish ordering differs")

        if payload.get("registered_files") != expected_registered:
            errors.append("detachment registered config/manifest/runner files differ")

        if require_committed:
            failure = _tracked_head_failure(project_root, str(relative))
            if failure:
                errors.append(f"detachment attestation {failure}")
            introduction = _introduction_commit(project_root, str(relative))
            prereg = _introduction_commit(project_root, str(CONFIG_PATH))
            if _commit_changed_paths(project_root, introduction) != {str(relative)}:
                errors.append("detachment attestation commit scope is not exact")
            if str(_git(project_root, "rev-parse", f"{introduction}^")) != prereg:
                errors.append("detachment attestation commit is not adjacent to R3 preregistration")
            if payload.get("git_head") != prereg:
                errors.append("detachment attestation git_head differs from R3 preregistration")
            if _blob_at(project_root, introduction, str(relative)) != path.read_bytes():
                errors.append("detachment attestation differs from its introduction blob")
    except (
        FileNotFoundError,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
    ) as error:
        errors.append(f"detachment attestation validation failed: {error}")
    return payload, errors


def _external_object(path: Path, label: str) -> dict[str, object]:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"{label} is missing, symbolic, or not a regular file")
    value = read_json(path)
    if not isinstance(value, dict):
        raise ValueError(f"{label} root is not an object")
    return value


def _positive_integer(value: object) -> bool:
    return type(value) is int and value > 0


def validate_precompletion_analysis(
    project_root: Path,
    config: dict,
    *,
    require_committed: bool,
) -> tuple[dict[str, object], list[str]]:
    """Validate accepted science while retaining zero authority to advance G1."""

    errors: list[str] = []
    payload: dict[str, object] = {}
    path = project_root / ANALYSIS_SUMMARY_PATH
    try:
        parsed = _external_object(path, "R3 analysis summary")
        payload = parsed
        execution = config.get("execution")
        contract = (
            execution.get("supervisor_completion_contract")
            if isinstance(execution, dict)
            else None
        )
        if not isinstance(contract, dict):
            errors.append("R3 analysis supervisor-completion contract is missing")
        elif (
            payload.get("protocol_revision") != PROTOCOL_REVISION
            or payload.get("audit_status") != contract.get("scientific_analysis_status")
            or payload.get("overall_protocol_status")
            != contract.get("overall_protocol_status_before_completion")
            or payload.get("g1_status") != "pending (1/6)"
            or payload.get("authorized_scope") != "no_G1_advancement"
        ):
            errors.append("R3 analysis/pre-completion authority status differs")
        if payload.get("config_sha256") != sha256(project_root / CONFIG_PATH):
            errors.append("R3 analysis config SHA-256 differs")
        if payload.get("manifest_sha256") != sha256(project_root / MANIFEST_PATH):
            errors.append("R3 analysis manifest SHA-256 differs")
        if require_committed:
            failure = _tracked_head_failure(project_root, str(ANALYSIS_SUMMARY_PATH))
            if failure:
                errors.append(f"R3 analysis summary {failure}")
            introduction = _introduction_commit(project_root, str(ANALYSIS_SUMMARY_PATH))
            if str(_git(project_root, "rev-parse", "HEAD")) != introduction:
                errors.append("R3 pre-completion analysis is not HEAD")
            changes = _commit_changed_paths(project_root, introduction)
            prefix = ANALYSIS_ROOT.as_posix() + "/"
            if (
                ANALYSIS_SUMMARY_PATH.as_posix() not in changes
                or not changes
                or any(not item.startswith(prefix) for item in changes)
            ):
                errors.append("R3 pre-completion analysis commit scope differs")
            if _blob_at(
                project_root, introduction, ANALYSIS_SUMMARY_PATH.as_posix()
            ) != path.read_bytes():
                errors.append("R3 analysis summary differs from its introduction blob")
    except (
        FileNotFoundError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
    ) as error:
        errors.append(f"R3 pre-completion analysis validation failed: {error}")
    return payload, errors


def _analysis_tree_bytes(root: Path) -> dict[str, bytes]:
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"analysis root is missing, symbolic, or not a directory: {root}")
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise ValueError(f"symbolic analysis artifact is forbidden: {path}")
        if path.is_file():
            files[path.relative_to(root).as_posix()] = path.read_bytes()
        elif not path.is_dir():
            raise ValueError(f"non-regular analysis artifact is forbidden: {path}")
    return files


def recompute_final_analysis(
    project_root: Path,
    config_path: Path,
    manifest_path: Path,
) -> list[str]:
    """Regenerate every aggregate science artifact and compare it byte-for-byte."""

    errors: list[str] = []
    try:
        from analyze_s1_g1_thermodynamic_label_audit_r3 import analyze

        with tempfile.TemporaryDirectory(prefix="m-ofdft-g1-r3-final-replay-") as directory:
            output = Path(directory) / "analysis"
            summary = analyze(
                project_root,
                config_path,
                manifest_path,
                output,
                require_committed=True,
                skip_terminal_evidence_validation=True,
            )
            if summary.get("audit_status") != "accepted":
                errors.append("recomputed R3 aggregate science is not accepted")
            expected = _analysis_tree_bytes(project_root / ANALYSIS_ROOT)
            observed = _analysis_tree_bytes(output)
            if set(observed) != set(expected):
                errors.append("recomputed R3 analysis artifact set differs")
            for relative in sorted(set(observed).intersection(expected)):
                if observed[relative] != expected[relative]:
                    errors.append(f"recomputed R3 analysis artifact differs: {relative}")
    except (
        FileNotFoundError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
    ) as error:
        errors.append(f"R3 final aggregate-science replay failed: {error}")
    return errors


def validate_supervisor_completion(
    project_root: Path,
    config: dict,
    *,
    require_committed: bool,
) -> tuple[dict[str, object], list[str]]:
    """Replay the external supervisor receipt and its one-file Git import."""

    errors: list[str] = []
    payload: dict[str, object] = {}
    execution = config.get("execution")
    if not isinstance(execution, dict):
        return payload, ["R3 execution contract is missing"]
    relative = execution.get("supervisor_completion_path")
    if relative != str(SUPERVISOR_COMPLETION_PATH):
        return payload, ["supervisor completion path differs from registration"]
    contract = execution.get("supervisor_completion_contract")
    if not isinstance(contract, dict):
        return payload, ["supervisor completion contract is missing"]
    path = project_root / str(relative)
    if not path.is_file() or path.is_symlink():
        return payload, ["supervisor completion is missing or symbolic"]

    _, detachment_errors = validate_detachment_attestation(
        project_root, config, require_committed=require_committed
    )
    errors.extend(
        f"supervisor completion detachment revalidation: {failure}"
        for failure in detachment_errors
    )

    try:
        parsed = read_json(path)
        if not isinstance(parsed, dict):
            raise ValueError("supervisor completion root is not an object")
        payload = parsed
        registered_keys = contract.get("required_keys_exact")
        if (
            not isinstance(registered_keys, list)
            or registered_keys != list(registration.SUPERVISOR_COMPLETION_REQUIRED_KEYS)
            or set(payload) != set(registered_keys)
        ):
            errors.append("supervisor completion key set differs")

        expected_scalars = {
            "schema_version": contract.get("schema_version"),
            "protocol_revision": PROTOCOL_REVISION,
            "status": contract.get("status"),
            "config_path": str(CONFIG_PATH),
            "config_sha256": sha256(project_root / CONFIG_PATH),
            "manifest_path": str(MANIFEST_PATH),
            "manifest_sha256": sha256(project_root / MANIFEST_PATH),
            "supervisor_state_directory": SUPERVISOR_STATE_DIRECTORY,
            "runner_exit_code": contract.get("runner_exit_code"),
            "analysis_audit_status": contract.get("analysis_audit_status"),
            "final_acceptance_policy": contract.get("final_acceptance_policy"),
        }
        if type(payload.get("schema_version")) is not int:
            errors.append("supervisor completion schema_version differs")
        if (
            type(contract.get("runner_exit_code")) is not int
            or contract.get("runner_exit_code") != 0
            or type(payload.get("runner_exit_code")) is not int
            or payload.get("runner_exit_code") != 0
        ):
            errors.append("supervisor completion runner exit code type differs")
        for key, expected in expected_scalars.items():
            if payload.get(key) != expected:
                errors.append(f"supervisor completion {key} differs")
        if not isinstance(payload.get("created_utc"), str) or not _UTC.fullmatch(
            str(payload["created_utc"])
        ):
            errors.append("supervisor completion UTC timestamp differs")
        for key in ("supervisor_pid", "supervisor_start_time_ticks"):
            if not _positive_integer(payload.get(key)):
                errors.append(f"supervisor completion {key} is invalid")
        if not isinstance(payload.get("boot_id"), str) or not _BOOT_ID.fullmatch(
            str(payload["boot_id"])
        ):
            errors.append("supervisor completion boot ID is invalid")
        head_before = payload.get("git_head_before_completion")
        if not isinstance(head_before, str) or not _HEX40.fullmatch(head_before):
            errors.append("supervisor completion pre-completion Git HEAD is invalid")

        state = Path(SUPERVISOR_STATE_DIRECTORY)
        if not state.is_dir() or state.is_symlink():
            errors.append("supervisor completion state directory is missing or symbolic")
        launch_path = state / "launch.json"
        go_path = state / "go.json"
        terminal_path = state / "terminal.json"
        journal_path = state / "journal.jsonl"
        log_path = state / "supervisor.log"
        expected_paths = {
            "supervisor_launch_path": str(launch_path),
            "supervisor_terminal_path": str(terminal_path),
            "supervisor_journal_path": str(journal_path),
        }
        for key, expected in expected_paths.items():
            if payload.get(key) != expected:
                errors.append(f"supervisor completion {key} differs")

        launch = _external_object(launch_path, "supervisor launch record")
        go = _external_object(go_path, "supervisor GO record")
        terminal = _external_object(terminal_path, "supervisor terminal record")
        if not journal_path.is_file() or journal_path.is_symlink():
            raise ValueError("supervisor journal is missing or symbolic")
        if not log_path.is_file() or log_path.is_symlink():
            raise ValueError("supervisor log is missing or symbolic")
        expected_hashes = {
            "supervisor_launch_sha256": sha256(launch_path),
            "supervisor_terminal_sha256": sha256(terminal_path),
            "supervisor_journal_sha256": sha256(journal_path),
        }
        for key, expected in expected_hashes.items():
            if payload.get(key) != expected:
                errors.append(f"supervisor completion {key} differs")

        terminal_keys = {
            "schema_version",
            "protocol_revision",
            "status",
            "runner_return_code",
            "runner_pid",
            "runner_start_time_ticks",
            "launch_sha256",
            "go_sha256",
            "journal_sha256",
            "git_head_after_runner",
            "analysis_summary_path",
            "analysis_summary_sha256",
            "log_sha256",
            "finished_utc",
        }
        if set(terminal) != terminal_keys:
            errors.append("supervisor terminal key set differs")
        if (
            type(terminal.get("schema_version")) is not int
            or terminal.get("schema_version") != 1
            or terminal.get("protocol_revision") != PROTOCOL_REVISION
            or terminal.get("status") != "accepted"
            or type(terminal.get("runner_return_code")) is not int
            or terminal.get("runner_return_code") != 0
        ):
            errors.append("supervisor terminal identity/status differs")
        if not isinstance(terminal.get("finished_utc"), str) or not _UTC.fullmatch(
            str(terminal["finished_utc"])
        ):
            errors.append("supervisor terminal UTC timestamp differs")
        if (
            terminal.get("launch_sha256") != sha256(launch_path)
            or terminal.get("go_sha256") != sha256(go_path)
            or terminal.get("journal_sha256") != sha256(journal_path)
            or terminal.get("log_sha256") != sha256(log_path)
        ):
            errors.append("supervisor terminal external-evidence hashes differ")
        if (
            not _positive_integer(terminal.get("runner_pid"))
            or not _positive_integer(terminal.get("runner_start_time_ticks"))
            or terminal.get("runner_return_code") != payload.get("runner_exit_code")
        ):
            errors.append("supervisor completion terminal runner result is invalid")
        if terminal.get("git_head_after_runner") != head_before:
            errors.append("supervisor completion Git parent differs from terminal")

        launch_process = launch.get("process")
        registered_files = launch.get("registered_files")
        expected_registered = _expected_registered_files(project_root)
        expected_sealed_inputs = _expected_sealed_execution_inputs(
            project_root, expected_registered
        )
        if (
            launch.get("protocol_revision") != PROTOCOL_REVISION
            or launch.get("status") != "waiting_for_detachment_attestation"
            or launch.get("state_directory") != SUPERVISOR_STATE_DIRECTORY
            or launch.get("boot_id") != payload.get("boot_id")
            or registered_files != expected_registered
            or launch.get("sealed_execution_inputs") != expected_sealed_inputs
            or not isinstance(launch_process, dict)
        ):
            errors.append("supervisor completion launch identity/registration differs")
        process_integer_fields = (
            "pid",
            "ppid",
            "process_group_id",
            "session_id",
            "start_time_ticks",
        )
        if isinstance(launch_process, dict) and (
            any(
                not _positive_integer(launch_process.get(field))
                for field in process_integer_fields
            )
            or type(launch_process.get("tty_nr")) is not int
            or launch_process.get("tty_nr") != 0
        ):
            errors.append("supervisor completion launch process integers differ")

        if set(go) != set(registration.GO_PAYLOAD_REQUIRED_KEYS):
            errors.append("supervisor GO key set differs")
        attestation_relative = str(registration.DETACHMENT_ATTESTATION_PATH)
        attestation_path = project_root / attestation_relative
        if (
            type(go.get("schema_version")) is not int
            or go.get("schema_version") != 1
            or go.get("protocol_revision") != PROTOCOL_REVISION
            or go.get("status") != "go"
            or go.get("launch_sha256") != sha256(launch_path)
            or go.get("boot_id") != payload.get("boot_id")
            or go.get("attestation_path") != attestation_relative
            or go.get("attestation_sha256") != sha256(attestation_path)
            or go.get("registered_files") != expected_registered
            or go.get("sealed_execution_inputs_sha256")
            != _sealed_execution_inputs_sha256(expected_sealed_inputs)
        ):
            errors.append("supervisor completion GO identity/registration differs")
        if not isinstance(go.get("created_utc"), str) or not _UTC.fullmatch(
            str(go["created_utc"])
        ):
            errors.append("supervisor GO UTC timestamp differs")
        go_head = go.get("git_head")
        if not isinstance(go_head, str) or not _HEX40.fullmatch(go_head):
            errors.append("supervisor GO Git HEAD is invalid")
        if isinstance(launch_process, dict) and (
            not _positive_integer(go.get("supervisor_pid"))
            or not _positive_integer(go.get("supervisor_start_time_ticks"))
            or launch_process.get("pid")
            != go.get("supervisor_pid")
            or launch_process.get("pid") != payload.get("supervisor_pid")
            or launch_process.get("start_time_ticks")
            != go.get("supervisor_start_time_ticks")
            or launch_process.get("start_time_ticks")
            != payload.get("supervisor_start_time_ticks")
        ):
            errors.append("supervisor GO/completion identity differs from launch")

        journal_bytes = journal_path.read_bytes()
        if not journal_bytes.endswith(b"\n"):
            errors.append("supervisor journal is not newline terminated")
        journal: list[dict[str, object]] = []
        for index, line in enumerate(journal_bytes.decode("utf-8").splitlines(), 1):
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError(f"supervisor journal event {index} is not an object")
            journal.append(event)
            if (
                not isinstance(event.get("event"), str)
                or not _positive_integer(event.get("pid"))
                or not isinstance(event.get("utc"), str)
                or not _UTC.fullmatch(str(event["utc"]))
            ):
                errors.append(f"supervisor journal event {index} identity differs")
            if isinstance(launch_process, dict) and event.get("pid") != launch_process.get("pid"):
                errors.append(f"supervisor journal event {index} PID differs")
        started = [
            (index, event)
            for index, event in enumerate(journal)
            if event.get("event") == "runner_started"
        ]
        finished = [
            (index, event)
            for index, event in enumerate(journal)
            if event.get("event") == "runner_finished"
        ]
        accepted_go = [
            (index, event)
            for index, event in enumerate(journal)
            if event.get("event") == "go_accepted"
        ]
        if (
            len(accepted_go) != 1
            or accepted_go[0][1].get("git_head") != go_head
            or accepted_go[0][1].get("go_sha256") != sha256(go_path)
        ):
            errors.append("supervisor journal/GO identity or byte hash differs")
        if len(started) != 1 or len(finished) != 1:
            errors.append("supervisor journal runner event count differs")
        elif (
            len(accepted_go) != 1
            or accepted_go[0][0] >= started[0][0]
            or started[0][0] >= finished[0][0]
            or finished[0][0] != len(journal) - 1
            or started[0][1].get("child_pid") != terminal.get("runner_pid")
            or started[0][1].get("child_start_time_ticks")
            != terminal.get("runner_start_time_ticks")
            or not _positive_integer(started[0][1].get("child_pid"))
            or not _positive_integer(
                started[0][1].get("child_start_time_ticks")
            )
            or type(finished[0][1].get("return_code")) is not int
            or finished[0][1].get("return_code") != 0
        ):
            errors.append("supervisor journal terminal runner sequence differs")

        analysis_relative = Path(str(payload.get("analysis_path", "")))
        expected_analysis = ANALYSIS_SUMMARY_PATH
        if analysis_relative != expected_analysis:
            errors.append("supervisor completion analysis path differs")
        analysis_path = _safe_project_path(
            project_root, payload.get("analysis_path"), "supervisor completion analysis path"
        )
        analysis = _external_object(analysis_path, "R3 analysis summary")
        if payload.get("analysis_sha256") != sha256(analysis_path):
            errors.append("supervisor completion analysis SHA-256 differs")
        if (
            analysis.get("protocol_revision") != PROTOCOL_REVISION
            or analysis.get("audit_status") != contract.get("scientific_analysis_status")
            or payload.get("analysis_audit_status") != analysis.get("audit_status")
            or analysis.get("overall_protocol_status")
            != contract.get("overall_protocol_status_before_completion")
            or analysis.get("g1_status") != "pending (1/6)"
            or analysis.get("authorized_scope") != "no_G1_advancement"
        ):
            errors.append(
                "supervisor completion analysis/pre-completion authority status differs"
            )
        if (
            terminal.get("analysis_summary_path") != payload.get("analysis_path")
            or terminal.get("analysis_summary_sha256") != payload.get("analysis_sha256")
        ):
            errors.append("supervisor completion analysis differs from terminal")

        if require_committed:
            failure = _tracked_head_failure(project_root, str(relative))
            if failure:
                errors.append(f"supervisor completion {failure}")
            introduction = _introduction_commit(project_root, str(relative))
            if _commit_changed_paths(project_root, introduction) != {str(relative)}:
                errors.append("supervisor completion commit scope is not exact")
            parent = str(_git(project_root, "rev-parse", f"{introduction}^"))
            if parent != head_before:
                errors.append("supervisor completion commit parent differs")
            allowed = contract.get("allowed_post_completion_commit_paths_exact")
            if allowed != list(POST_TERMINAL_DOCUMENTATION_PATHS):
                errors.append("supervisor completion post-terminal allowlist differs")
            else:
                terminal_failure = _post_terminal_history_failure(
                    project_root, introduction, set(POST_TERMINAL_DOCUMENTATION_PATHS)
                )
                if terminal_failure:
                    errors.append(f"supervisor completion {terminal_failure}")
            if _blob_at(project_root, introduction, str(relative)) != path.read_bytes():
                errors.append("supervisor completion differs from its introduction blob")
            if _blob_at(project_root, parent, payload["analysis_path"]) != analysis_path.read_bytes():
                errors.append("accepted analysis summary was not frozen in completion parent")
            analysis_changes = _commit_changed_paths(project_root, parent)
            analysis_root = expected_analysis.parent.as_posix() + "/"
            if (
                expected_analysis.as_posix() not in analysis_changes
                or not analysis_changes
                or any(not item.startswith(analysis_root) for item in analysis_changes)
            ):
                errors.append("completion parent is not the exact R3 analysis commit")
            detachment_introduction = _introduction_commit(
                project_root, attestation_relative
            )
            if go_head != detachment_introduction:
                errors.append(
                    "supervisor GO Git HEAD is not the detachment introduction"
                )
            elif _blob_at(
                project_root, go_head, attestation_relative
            ) != attestation_path.read_bytes():
                errors.append(
                    "supervisor GO did not bind the committed detachment attestation"
                )
            first_marker_relative = (
                f"{ATTEMPT_LEDGER_ROOT.as_posix()}/{R3_AUDIT_IDS[0]}.json"
            )
            first_marker_introduction = _introduction_commit(
                project_root, first_marker_relative
            )
            first_marker_parent = str(
                _git(
                    project_root,
                    "rev-parse",
                    f"{first_marker_introduction}^",
                )
            )
            if first_marker_parent != go_head:
                errors.append(
                    "first R3 attempt marker parent is not the supervisor GO HEAD"
                )
    except (
        FileNotFoundError,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
    ) as error:
        errors.append(f"supervisor completion validation failed: {error}")
    return payload, errors


def _barrier_spec(
    project_root: Path,
    config: dict,
    barrier_name: str,
) -> tuple[object, object, list[str]]:
    python = config["runtime"]["tools"]["python"]["path"]
    prefix = [
        str(python),
        "-s",
        str(project_root / "scripts/validate_s1_g1_thermodynamic_label_audit_r3.py"),
        str(project_root / MANIFEST_PATH),
        "--config",
        str(project_root / CONFIG_PATH),
        "--scientific-config",
        registration.SEALED_EXECUTION_INPUT_PROC_PATHS["config"],
        "--scientific-manifest",
        registration.SEALED_EXECUTION_INPUT_PROC_PATHS["manifest"],
        "--require-committed",
    ]
    if barrier_name == "production-parser-registration-before-001":
        return None, None, prefix
    if barrier_name == "k-gate-after-012":
        return R3_PILOT_IDS[-1], R3_K_GATE_COMPLETION_LOGICAL_ID, [*prefix, "--require-k-gate"]
    if barrier_name == "final-all-after-040":
        return R3_AUDIT_IDS[-1], NEW_TO_LOGICAL[R3_AUDIT_IDS[-1]], [
            *prefix,
            "--require-all-runs",
        ]
    if barrier_name == "final-analysis":
        return R3_AUDIT_IDS[-1], NEW_TO_LOGICAL[R3_AUDIT_IDS[-1]], [
            str(python),
            "-s",
            str(project_root / "scripts/analyze_s1_g1_thermodynamic_label_audit_r3.py"),
            str(project_root / ANALYSIS_ROOT),
            "--config",
            str(project_root / CONFIG_PATH),
            "--manifest",
            str(project_root / MANIFEST_PATH),
            "--scientific-config",
            registration.SEALED_EXECUTION_INPUT_PROC_PATHS["config"],
            "--scientific-manifest",
            registration.SEALED_EXECUTION_INPUT_PROC_PATHS["manifest"],
        ]
    if barrier_name == "final-analysis-status":
        return R3_AUDIT_IDS[-1], NEW_TO_LOGICAL[R3_AUDIT_IDS[-1]], [
            *prefix,
            "--check-analysis-summary",
        ]

    per_run = re.fullmatch(r"(attempt-marker|accepted-run|failure-archive)-(\d{3})", barrier_name)
    if per_run:
        experiment_id = f"S1-20260807-{per_run.group(2)}"
        if experiment_id not in R3_AUDIT_IDS:
            raise ValueError(f"unknown per-run barrier: {barrier_name}")
        option = {
            "attempt-marker": "--check-attempt-marker",
            "accepted-run": "--check-run",
            "failure-archive": "--check-failure-archives",
        }[per_run.group(1)]
        return experiment_id, NEW_TO_LOGICAL[experiment_id], [
            *prefix,
            option,
            experiment_id,
        ]

    half_triggers = {
        "S1-20260806-021": "S1-20260806-007",
        "S1-20260806-024": "S1-20260806-010",
        "S1-20260806-027": "S1-20260806-013",
        "S1-20260806-028": "S1-20260806-014",
        "S1-20260806-031": "S1-20260806-017",
        "S1-20260806-034": "S1-20260806-020",
        **{
            logical: logical
            for logical in (
                "S1-20260806-022",
                "S1-20260806-023",
                "S1-20260806-025",
                "S1-20260806-026",
                "S1-20260806-029",
                "S1-20260806-030",
                "S1-20260806-032",
                "S1-20260806-033",
            )
        },
    }
    match = re.fullmatch(r"half-quarter-(\d{3})-after-(\d{3})", barrier_name)
    if match:
        quarter = f"S1-20260806-{match.group(1)}"
        after_logical = half_triggers.get(quarter)
        if after_logical is None:
            raise ValueError(f"unknown half/quarter barrier: {barrier_name}")
        after_effective = effective_id(after_logical)
        if after_effective.rsplit("-", 1)[1] != match.group(2):
            raise ValueError(f"half/quarter barrier suffix differs: {barrier_name}")
        return after_effective, after_logical, [
            *prefix,
            "--require-half-quarter-pair",
            quarter,
        ]

    eos = {
        "eos-al-standard-half": ("S1-20260806-013", ["al", "standard", "half"]),
        "eos-mg-standard-half": ("S1-20260806-020", ["mg", "standard", "half"]),
        "eos-al-half-quarter": ("S1-20260806-026", ["al", "half", "quarter"]),
        "eos-mg-half-quarter": ("S1-20260806-033", ["mg", "half", "quarter"]),
    }
    for stem, (after_logical, arguments) in eos.items():
        after_effective = effective_id(after_logical)
        expected_name = f"{stem}-after-{after_effective.rsplit('-', 1)[1]}"
        if barrier_name == expected_name:
            return after_effective, after_logical, [
                *prefix,
                "--require-adjacent-eos",
                *arguments,
            ]
    raise ValueError(f"unknown R3 barrier name: {barrier_name}")


def validate_barrier_failures(
    project_root: Path,
    config: dict,
    *,
    require_committed: bool,
) -> tuple[list[dict[str, object]], list[str]]:
    """Validate the single fail-closed barrier record, if one exists."""

    records: list[dict[str, object]] = []
    errors: list[str] = []
    execution = config.get("execution")
    if not isinstance(execution, dict):
        return records, ["R3 execution contract is missing"]
    relative_root = execution.get("barrier_failure_root")
    if relative_root != str(BARRIER_FAILURE_ROOT):
        return records, ["barrier failure root differs from registration"]
    contract = execution.get("barrier_failure_contract")
    if not isinstance(contract, dict):
        return records, ["barrier failure contract is missing"]
    root = project_root / str(relative_root)
    introductions: list[tuple[str, str]] = []
    if require_committed:
        introductions = _introduced_paths_under(project_root, str(relative_root))
        if len(introductions) > 1:
            errors.append("barrier failure history contains multiple terminal introductions")
        for introduction, relative in introductions:
            expected_parent = Path(str(relative_root))
            relative_path = Path(relative)
            if (
                relative_path.parent != expected_parent
                or relative_path.suffix != ".json"
            ):
                errors.append(f"non-canonical historical barrier path: {relative}")
                continue
            try:
                historical = json.loads(
                    _blob_at(project_root, introduction, relative).decode("utf-8")
                )
                if not isinstance(historical, dict):
                    raise ValueError("historical barrier root is not an object")
                if set(historical) != set(
                    registration.BARRIER_FAILURE_REQUIRED_KEYS
                ):
                    errors.append(f"{relative}: historical barrier key set differs")
                if _commit_changed_paths(project_root, introduction) != {relative}:
                    errors.append(f"{relative}: historical barrier commit scope is not exact")
                parent = str(_git(project_root, "rev-parse", f"{introduction}^"))
                if historical.get("git_head_before_failure") != parent:
                    errors.append(f"{relative}: historical barrier commit parent differs")
                terminal_failure = _post_terminal_history_failure(
                    project_root,
                    introduction,
                    set(POST_TERMINAL_DOCUMENTATION_PATHS),
                )
                if terminal_failure:
                    errors.append(f"{relative}: {terminal_failure}")
                barrier_name = historical.get("barrier_name")
                barrier_label = (
                    barrier_name if isinstance(barrier_name, str) else relative
                )
                for experiment_id in R3_AUDIT_IDS:
                    marker_relative = (
                        f"{ATTEMPT_LEDGER_ROOT.as_posix()}/{experiment_id}.json"
                    )
                    for marker_commit in _introduction_commits(
                        project_root, marker_relative
                    ):
                        if marker_commit != introduction and _is_ancestor(
                            project_root, introduction, marker_commit
                        ):
                            errors.append(
                                f"{barrier_label}: attempt marker followed barrier "
                                f"failure: {experiment_id}"
                            )
                if not root.exists() and not root.is_symlink():
                    records.append(historical)
            except (
                KeyError,
                TypeError,
                UnicodeDecodeError,
                ValueError,
                json.JSONDecodeError,
                subprocess.CalledProcessError,
            ) as error:
                errors.append(f"{relative}: historical barrier validation failed: {error}")
    if not root.exists() and not root.is_symlink():
        if introductions:
            errors.append("historical barrier failure was deleted from HEAD")
        return records, errors
    if not root.is_dir() or root.is_symlink():
        return records, ["barrier failure root is not a regular directory"]
    entries = sorted(root.iterdir(), key=lambda item: item.name)
    if len(entries) != 1:
        errors.append("barrier failure root must contain exactly one terminal record")
    if require_committed and len(introductions) == 1 and len(entries) == 1:
        current_relative = _relative(project_root, entries[0])
        if current_relative != introductions[0][1]:
            errors.append("current barrier failure path differs from terminal history")

    for path in entries:
        if not path.is_file() or path.is_symlink() or path.suffix != ".json":
            errors.append(f"non-canonical barrier failure artifact: {path.name}")
            continue
        try:
            parsed = read_json(path)
            if not isinstance(parsed, dict):
                raise ValueError("barrier failure root is not an object")
            payload: dict[str, object] = parsed
            records.append(payload)
            registered_keys = contract.get("required_keys_exact")
            if (
                not isinstance(registered_keys, list)
                or registered_keys != list(registration.BARRIER_FAILURE_REQUIRED_KEYS)
                or set(payload) != set(registered_keys)
            ):
                errors.append(f"{path.name}: barrier failure key set differs")
            barrier_name = payload.get("barrier_name")
            if not isinstance(barrier_name, str) or not re.fullmatch(
                r"[a-z0-9][a-z0-9-]*", barrier_name
            ):
                raise ValueError("barrier name is invalid")
            if path.name != f"{barrier_name}.json":
                errors.append(f"{path.name}: barrier filename differs from barrier name")
            expected_effective, expected_logical, expected_command = _barrier_spec(
                project_root, config, barrier_name
            )
            expected_scalars = {
                "schema_version": contract.get("schema_version"),
                "protocol_revision": PROTOCOL_REVISION,
                "status": contract.get("status"),
                "experiment_id": expected_effective,
                "logical_experiment_id": expected_logical,
                "command_argv": expected_command,
                "config_path": str(CONFIG_PATH),
                "config_sha256": sha256(project_root / CONFIG_PATH),
                "manifest_path": str(MANIFEST_PATH),
                "manifest_sha256": sha256(project_root / MANIFEST_PATH),
                "supervisor_state_directory": SUPERVISOR_STATE_DIRECTORY,
                "retry_policy": contract.get("retry_policy"),
            }
            if type(payload.get("schema_version")) is not int:
                errors.append(f"{path.name}: barrier failure schema_version differs")
            for key, expected in expected_scalars.items():
                if payload.get(key) != expected:
                    errors.append(f"{barrier_name}: barrier failure {key} differs")
            exit_code = payload.get("exit_code")
            if not isinstance(exit_code, int) or isinstance(exit_code, bool) or exit_code == 0:
                errors.append(f"{barrier_name}: barrier failure exit code is not nonzero")
            if not isinstance(payload.get("created_utc"), str) or not _UTC.fullmatch(
                str(payload["created_utc"])
            ):
                errors.append(f"{barrier_name}: barrier failure UTC timestamp differs")
            head_before = payload.get("git_head_before_failure")
            if not isinstance(head_before, str) or not _HEX40.fullmatch(head_before):
                errors.append(f"{barrier_name}: barrier failure Git parent is invalid")
            launch_path = Path(SUPERVISOR_STATE_DIRECTORY) / "launch.json"
            if payload.get("supervisor_launch_path") != str(launch_path):
                errors.append(f"{barrier_name}: barrier failure launch path differs")
            if not launch_path.is_file() or launch_path.is_symlink():
                errors.append(f"{barrier_name}: barrier launch is missing or symbolic")
            elif payload.get("supervisor_launch_sha256") != sha256(launch_path):
                errors.append(f"{barrier_name}: barrier launch SHA-256 differs")

            if require_committed:
                relative = _relative(project_root, path)
                failure = _tracked_head_failure(project_root, relative)
                if failure:
                    errors.append(f"{barrier_name}: barrier failure {failure}")
                introduction = _introduction_commit(project_root, relative)
                if _commit_changed_paths(project_root, introduction) != {relative}:
                    errors.append(f"{barrier_name}: barrier failure commit scope is not exact")
                parent = str(_git(project_root, "rev-parse", f"{introduction}^"))
                if parent != head_before:
                    errors.append(f"{barrier_name}: barrier failure commit parent differs")
                allowed = contract.get("allowed_post_failure_commit_paths_exact")
                if allowed != list(POST_TERMINAL_DOCUMENTATION_PATHS):
                    errors.append(
                        f"{barrier_name}: post-failure documentation allowlist differs"
                    )
                else:
                    terminal_failure = _post_terminal_history_failure(
                        project_root,
                        introduction,
                        set(POST_TERMINAL_DOCUMENTATION_PATHS),
                    )
                    if terminal_failure:
                        errors.append(f"{barrier_name}: {terminal_failure}")
                if _blob_at(project_root, introduction, relative) != path.read_bytes():
                    errors.append(
                        f"{barrier_name}: barrier failure differs from its introduction blob"
                    )
        except (
            FileNotFoundError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            subprocess.CalledProcessError,
        ) as error:
            errors.append(f"{path.name}: barrier failure validation failed: {error}")
    return records, errors


def validate_attempt_marker(
    project_root: Path,
    config: dict,
    experiment_id: str,
    *,
    require_committed: bool,
) -> tuple[dict[str, object], str | None, list[str]]:
    """Validate one single-use, Git-ordered formal-attempt marker."""

    errors: list[str] = []
    payload: dict[str, object] = {}
    commit: str | None = None
    if experiment_id not in R3_AUDIT_IDS:
        return payload, commit, [f"attempt marker ID is outside R3: {experiment_id}"]
    execution = config.get("execution")
    if not isinstance(execution, dict):
        return payload, commit, ["R3 execution contract is missing"]
    if execution.get("attempt_ledger_root") != str(ATTEMPT_LEDGER_ROOT):
        errors.append("attempt-ledger root differs from the frozen path")
    if execution.get("supervisor_state_directory") != SUPERVISOR_STATE_DIRECTORY:
        errors.append("supervisor state directory differs from the frozen path")
    _, detachment_errors = validate_detachment_attestation(
        project_root, config, require_committed=True
    )
    errors.extend(detachment_errors)
    marker_relative = f"{ATTEMPT_LEDGER_ROOT.as_posix()}/{experiment_id}.json"
    marker = project_root / marker_relative
    if not marker.is_file() or marker.is_symlink():
        return payload, commit, [*errors, f"missing or symbolic formal-attempt marker: {experiment_id}"]
    try:
        parsed = read_json(marker)
        if not isinstance(parsed, dict):
            raise ValueError("attempt marker root is not an object")
        payload = parsed
        if set(payload) != set(registration.ATTEMPT_MARKER_REQUIRED_KEYS):
            errors.append(f"{experiment_id}: attempt marker key set differs")
        expected_scalars = {
            "schema_version": 1,
            "protocol_revision": PROTOCOL_REVISION,
            "experiment_id": experiment_id,
            "logical_experiment_id": NEW_TO_LOGICAL[experiment_id],
            "status": "formal_attempt_started",
            "retry_policy": "new_protocol_revision_and_new_experiment_ids_only",
            "config_path": str(CONFIG_PATH),
            "config_sha256": sha256(project_root / CONFIG_PATH),
            "manifest_path": str(MANIFEST_PATH),
            "manifest_sha256": sha256(project_root / MANIFEST_PATH),
            "supervisor_state_directory": SUPERVISOR_STATE_DIRECTORY,
        }
        if type(payload.get("schema_version")) is not int:
            errors.append(f"{experiment_id}: attempt marker schema_version differs")
        for key, expected in expected_scalars.items():
            if payload.get(key) != expected:
                errors.append(f"{experiment_id}: attempt marker {key} differs")
        if not isinstance(payload.get("created_utc"), str) or not _UTC.fullmatch(
            str(payload["created_utc"])
        ):
            errors.append(f"{experiment_id}: attempt marker UTC timestamp differs")
        head_before = payload.get("git_head_before_attempt")
        if not isinstance(head_before, str) or not _HEX40.fullmatch(head_before):
            errors.append(f"{experiment_id}: pre-attempt Git HEAD is invalid")
        for key in ("supervisor_pid", "supervisor_start_time_ticks"):
            value = payload.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                errors.append(f"{experiment_id}: attempt marker {key} is invalid")
        if not isinstance(payload.get("boot_id"), str) or not _BOOT_ID.fullmatch(
            str(payload["boot_id"])
        ):
            errors.append(f"{experiment_id}: attempt marker boot ID is invalid")
        launch_value = payload.get("supervisor_launch_path")
        launch_digest = payload.get("supervisor_launch_sha256")
        launch: Path | None = None
        if not isinstance(launch_value, str):
            errors.append(f"{experiment_id}: supervisor launch path is invalid")
        else:
            launch = Path(launch_value)
            expected_launch = Path(SUPERVISOR_STATE_DIRECTORY) / "launch.json"
            if launch != expected_launch:
                errors.append(f"{experiment_id}: supervisor launch path differs")
            if not launch.is_file() or launch.is_symlink():
                errors.append(f"{experiment_id}: external supervisor launch record is missing or symbolic")
            elif not isinstance(launch_digest, str) or not _HEX64.fullmatch(launch_digest):
                errors.append(f"{experiment_id}: supervisor launch SHA-256 is invalid")
            elif sha256(launch) != launch_digest:
                errors.append(f"{experiment_id}: external supervisor launch SHA-256 differs")

        go_value = payload.get("supervisor_go_path")
        go_digest = payload.get("supervisor_go_sha256")
        go_git_head = payload.get("go_git_head")
        expected_go_path = Path(SUPERVISOR_STATE_DIRECTORY) / "go.json"
        go: dict[str, object] = {}
        if not isinstance(go_value, str) or Path(go_value) != expected_go_path:
            errors.append(f"{experiment_id}: supervisor GO path differs")
        elif not isinstance(go_digest, str) or not _HEX64.fullmatch(go_digest):
            errors.append(f"{experiment_id}: supervisor GO SHA-256 is invalid")
        elif not expected_go_path.is_file() or expected_go_path.is_symlink():
            errors.append(
                f"{experiment_id}: external supervisor GO record is missing or symbolic"
            )
        else:
            _, go, observed_go_digest = _stable_json_object(expected_go_path)
            if observed_go_digest != go_digest:
                errors.append(
                    f"{experiment_id}: external supervisor GO SHA-256 differs"
                )
            expected_registered = _expected_registered_files(project_root)
            expected_sealed = _expected_sealed_execution_inputs(
                project_root, expected_registered
            )
            if set(go) != set(registration.GO_PAYLOAD_REQUIRED_KEYS):
                errors.append(f"{experiment_id}: supervisor GO key set differs")
            attestation_path_value = execution.get("detachment_attestation_path")
            attestation_path = (
                project_root / str(attestation_path_value)
                if isinstance(attestation_path_value, str)
                else None
            )
            if (
                type(go.get("schema_version")) is not int
                or go.get("schema_version") != 1
                or go.get("protocol_revision") != PROTOCOL_REVISION
                or go.get("status") != "go"
                or go.get("launch_sha256") != launch_digest
                or go.get("boot_id") != payload.get("boot_id")
                or go.get("supervisor_pid") != payload.get("supervisor_pid")
                or go.get("supervisor_start_time_ticks")
                != payload.get("supervisor_start_time_ticks")
                or go.get("registered_files") != expected_registered
                or go.get("sealed_execution_inputs_sha256")
                != _sealed_execution_inputs_sha256(expected_sealed)
                or go.get("attestation_path") != attestation_path_value
                or attestation_path is None
                or not attestation_path.is_file()
                or attestation_path.is_symlink()
                or go.get("attestation_sha256") != sha256(attestation_path)
            ):
                errors.append(
                    f"{experiment_id}: supervisor GO identity/registration differs"
                )
            if not isinstance(go.get("created_utc"), str) or not _UTC.fullmatch(
                str(go.get("created_utc"))
            ):
                errors.append(f"{experiment_id}: supervisor GO UTC timestamp differs")
            if (
                not isinstance(go_git_head, str)
                or not _HEX40.fullmatch(go_git_head)
                or go.get("git_head") != go_git_head
            ):
                errors.append(f"{experiment_id}: marker/GO Git HEAD differs")
            if isinstance(attestation_path_value, str):
                attestation_introduction = _introduction_commit(
                    project_root, attestation_path_value
                )
                if go_git_head != attestation_introduction:
                    errors.append(
                        f"{experiment_id}: GO Git HEAD is not the detachment introduction"
                    )

        attestation_relative = execution.get("detachment_attestation_path")
        if not isinstance(attestation_relative, str):
            errors.append(f"{experiment_id}: detachment attestation path is missing")
            attestation: Path | None = None
            detachment: dict[str, object] = {}
        else:
            attestation = _safe_project_path(
                project_root, attestation_relative, "detachment attestation path"
            )
            if not attestation.is_file() or attestation.is_symlink():
                errors.append(f"{experiment_id}: detachment attestation is missing or symbolic")
                detachment = {}
            else:
                parsed_attestation = read_json(attestation)
                detachment = parsed_attestation if isinstance(parsed_attestation, dict) else {}
                if (
                    detachment.get("protocol_revision") != PROTOCOL_REVISION
                    or detachment.get("status") != "accepted"
                    or detachment.get("launch_path") != launch_value
                    or detachment.get("launch_sha256") != launch_digest
                    or detachment.get("boot_id") != payload.get("boot_id")
                ):
                    errors.append(f"{experiment_id}: committed detachment attestation does not bind launch")
                before = detachment.get("supervisor_process_before_hup")
                after = detachment.get("supervisor_process_after_hup")
                for label, process in (("before", before), ("after", after)):
                    if not isinstance(process, dict) or (
                        process.get("pid") != payload.get("supervisor_pid")
                        or process.get("start_time_ticks")
                        != payload.get("supervisor_start_time_ticks")
                    ):
                        errors.append(
                            f"{experiment_id}: detachment {label}-HUP process identity differs"
                        )

        if require_committed:
            failure = _tracked_head_failure(project_root, marker_relative)
            if failure:
                errors.append(f"{experiment_id}: attempt marker {failure}")
            commit = _introduction_commit(project_root, marker_relative)
            if _commit_changed_paths(project_root, commit) != {marker_relative}:
                errors.append(f"{experiment_id}: attempt marker commit scope is not exact")
            parent = str(_git(project_root, "rev-parse", f"{commit}^"))
            if payload.get("git_head_before_attempt") != parent:
                errors.append(f"{experiment_id}: attempt marker parent differs from its pre-attempt HEAD")
            if (
                experiment_id == EXECUTION_ORDER[0]
                and (
                    parent != payload.get("go_git_head")
                    or payload.get("git_head_before_attempt")
                    != payload.get("go_git_head")
                )
            ):
                errors.append(
                    f"{experiment_id}: first marker parent is not the GO/detachment introduction"
                )
            elif experiment_id != EXECUTION_ORDER[0]:
                predecessor = EXECUTION_ORDER[
                    EXECUTION_ORDER.index(experiment_id) - 1
                ]
                predecessor_introduction = _introduction_commit(
                    project_root, f"runs/{predecessor}"
                )
                if parent != predecessor_introduction:
                    errors.append(
                        f"{experiment_id}: marker parent is not the previous accepted run introduction"
                    )
            if _blob_at(project_root, commit, marker_relative) != marker.read_bytes():
                errors.append(f"{experiment_id}: attempt marker differs from its introduction blob")
            prereg = _introduction_commit(project_root, str(CONFIG_PATH))
            if commit == prereg or not _is_ancestor(project_root, prereg, commit):
                errors.append(f"{experiment_id}: attempt marker is not after R3 preregistration")
            if isinstance(attestation_relative, str) and attestation is not None:
                failure = _tracked_head_failure(project_root, attestation_relative)
                if failure:
                    errors.append(f"{experiment_id}: detachment attestation {failure}")
                else:
                    if _blob_at(project_root, parent, attestation_relative) != attestation.read_bytes():
                        errors.append(
                            f"{experiment_id}: detachment attestation was not frozen before attempt"
                        )
    except (
        FileNotFoundError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
    ) as error:
        errors.append(f"{experiment_id}: attempt marker validation failed: {error}")
    return payload, commit, errors


def _validate_execution_history(
    project_root: Path,
    prereg: str,
    config: dict,
    rows: list[dict[str, str]],
    errors: list[str],
) -> None:
    active: dict[str, str] = {}
    events: dict[str, list[tuple[str, str]]] = {experiment_id: [] for experiment_id in R3_AUDIT_IDS}
    marker_commits: dict[str, str] = {}
    ledger = project_root / ATTEMPT_LEDGER_ROOT
    if ledger.exists():
        if not ledger.is_dir() or ledger.is_symlink():
            errors.append("R3 attempt ledger is not a regular directory")
        else:
            observed = {path.name for path in ledger.iterdir()}
            allowed = {f"{experiment_id}.json" for experiment_id in R3_AUDIT_IDS}
            unexpected = observed - allowed
            if unexpected:
                errors.append(f"R3 attempt ledger contains unexpected entries: {sorted(unexpected)}")
    for experiment_id in R3_AUDIT_IDS:
        marker = project_root / ATTEMPT_LEDGER_ROOT / f"{experiment_id}.json"
        if marker.exists() or marker.is_symlink():
            _, marker_commit, marker_errors = validate_attempt_marker(
                project_root, config, experiment_id, require_committed=True
            )
            errors.extend(marker_errors)
            if marker_commit:
                marker_commits[experiment_id] = marker_commit
                events[experiment_id].append(("attempt", marker_commit))
        introduction = _active_introduction(project_root, experiment_id, errors)
        if introduction:
            active[experiment_id] = introduction
            events[experiment_id].append(("active", introduction))
            changed = _commit_changed_paths(project_root, introduction)
            if not changed or any(not path.startswith(f"runs/{experiment_id}/") for path in changed):
                errors.append(f"R3 active run commit is not independently scoped: {experiment_id}")
        archives = _archive_events(project_root, experiment_id, errors)
        events[experiment_id].extend(archives)
        run_commits = ([introduction] if introduction else []) + [commit for _, commit in archives]
        if run_commits and experiment_id not in marker_commits:
            errors.append(f"R3 run lacks its prior formal-attempt ledger commit: {experiment_id}")
        for run_commit in run_commits:
            if marker_commits.get(experiment_id) and str(
                _git(project_root, "rev-parse", f"{run_commit}^")
            ) != marker_commits[experiment_id]:
                errors.append(
                    f"R3 run introduction is not adjacent to its attempt marker: {experiment_id}"
                )
        if introduction and archives:
            errors.append(f"R3 forbids an active retry after archive: {experiment_id}")
        if len(archives) > 1:
            errors.append(f"R3 forbids multiple failed attempts for one ID: {experiment_id}")
        for label, commit in events[experiment_id]:
            if commit == prereg or not _is_ancestor(project_root, prereg, commit):
                errors.append(f"R3 execution event is not after preregistration: {experiment_id}/{label}")

    required_accepted_predecessors: set[str] = set()
    for index, experiment_id in enumerate(EXECUTION_ORDER):
        for label, commit in events[experiment_id]:
            for predecessor in EXECUTION_ORDER[:index]:
                required_accepted_predecessors.add(predecessor)
                predecessor_commit = active.get(predecessor)
                if predecessor_commit is None:
                    errors.append(
                        f"R3 execution order violation: {experiment_id}/{label} precedes accepted {predecessor}"
                    )
                    continue
                if predecessor_commit == commit or not _is_ancestor(project_root, predecessor_commit, commit):
                    errors.append(
                        f"R3 execution ancestry violation: {predecessor} is not before {experiment_id}/{label}"
                    )
    for predecessor in sorted(required_accepted_predecessors):
        if predecessor not in active:
            continue
        status_failure = _status_failure_r3(
            project_root / "runs" / predecessor / STATUS_NAME,
            predecessor,
            accepted=True,
        )
        if status_failure:
            errors.append(f"R3 predecessor {predecessor}: {status_failure}")
        for name in (STATUS_NAME, EVIDENCE_NAME):
            relative = f"runs/{predecessor}/{name}"
            failure = _tracked_head_failure(project_root, relative)
            if failure:
                errors.append(f"R3 predecessor {predecessor}: {failure}")
        evidence_path = project_root / "runs" / predecessor / EVIDENCE_NAME
        if evidence_path.is_file() and not evidence_path.is_symlink():
            try:
                evidence = read_json(evidence_path)
                if (
                    evidence.get("status") != "accepted"
                    or evidence.get("protocol_revision") != PROTOCOL_REVISION
                    or evidence.get("experiment_id") != predecessor
                    or evidence.get("logical_experiment_id") != NEW_TO_LOGICAL[predecessor]
                ):
                    errors.append(f"R3 predecessor accepted-evidence identity differs: {predecessor}")
            except (ValueError, json.JSONDecodeError) as error:
                errors.append(f"R3 predecessor evidence is invalid {predecessor}: {error}")
    if any(
        events[experiment_id]
        for experiment_id in EXECUTION_ORDER[len(R3_PILOT_IDS) :]
    ):
        try:
            gate = evaluate_k_gate(project_root, config, rows, require_committed=True)
            if gate.get("accepted") is not True:
                errors.append(
                    "R3 proceeded beyond the twelve-run P1 anchor set before the complete k gate passed"
                )
        except (FileNotFoundError, KeyError, TypeError, ValueError) as error:
            errors.append(f"R3 post-P1 k-gate barrier failed: {error}")


def validate_registration(
    project_root: Path,
    config_path: Path,
    manifest_path: Path,
    *,
    require_committed: bool,
    skip_terminal_evidence_validation: bool = False,
    scientific_config_path: Path | None = None,
    scientific_manifest_path: Path | None = None,
) -> tuple[dict, list[dict[str, str]], dict[str, object]]:
    project_root = project_root.resolve()
    errors: list[str] = []
    if config_path.resolve() != (project_root / CONFIG_PATH).resolve():
        errors.append("R3 config must use its canonical project path")
    if manifest_path.resolve() != (project_root / MANIFEST_PATH).resolve():
        errors.append("R3 manifest must use its canonical project path")
    if not config_path.is_file() or config_path.is_symlink():
        raise ValueError("R3 thermodynamic-label config is missing or symbolic")
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError("R3 thermodynamic-label manifest is missing or symbolic")
    try:
        validate_label_registration_contract(
            config_path=config_path,
            manifest_path=manifest_path,
            scientific_config_path=scientific_config_path,
            scientific_manifest_path=scientific_manifest_path,
        )
    except (FileNotFoundError, KeyError, TypeError, ValueError) as error:
        errors.append(f"production parser registration contract failed: {error}")
    config = read_json(config_path)
    rows = _read_manifest(manifest_path)
    _validate_manifest_rows(project_root, rows, config, errors)
    _validate_config_contract(project_root, config, rows, errors)
    _validate_implementation(project_root, config, errors, require_committed=require_committed)
    execution = config.get("execution")
    attestation_relative = (
        execution.get("detachment_attestation_path")
        if isinstance(execution, dict)
        else None
    )
    if (
        require_committed
        and isinstance(attestation_relative, str)
        and (project_root / attestation_relative).exists()
    ):
        _, detachment_errors = validate_detachment_attestation(
            project_root, config, require_committed=True
        )
        errors.extend(detachment_errors)
    bridge = _validate_r1_bridge(
        project_root, config, errors, require_committed=require_committed
    )
    prereg: str | None = None
    if require_committed:
        for relative in (str(CONFIG_PATH), str(MANIFEST_PATH)):
            failure = _tracked_head_failure(project_root, relative)
            if failure:
                errors.append(f"R3 preregistration {failure}")
        prereg = _validate_preregistration(project_root, config, rows, errors)
        if prereg:
            _validate_execution_history(project_root, prereg, config, rows, errors)
        barrier_root = project_root / BARRIER_FAILURE_ROOT
        barrier_introductions = _introduced_paths_under(
            project_root, BARRIER_FAILURE_ROOT.as_posix()
        )
        barrier_records, barrier_errors = validate_barrier_failures(
            project_root, config, require_committed=True
        )
        errors.extend(barrier_errors)
        if (
            barrier_records
            or barrier_introductions
            or barrier_root.exists()
            or barrier_root.is_symlink()
        ):
            errors.append(
                "R3 revision is terminal after a preserved barrier failure"
            )
        completion = project_root / SUPERVISOR_COMPLETION_PATH
        completion_introductions = _introduction_commits(
            project_root, SUPERVISOR_COMPLETION_PATH.as_posix()
        )
        if len(completion_introductions) > 1:
            errors.append("supervisor completion history contains multiple introductions")
        if completion_introductions and not (
            completion.exists() or completion.is_symlink()
        ):
            errors.append("historical supervisor completion was deleted from HEAD")
        if (
            not skip_terminal_evidence_validation
            and (completion.exists() or completion.is_symlink())
        ):
            _, completion_errors = validate_supervisor_completion(
                project_root, config, require_committed=True
            )
            errors.extend(completion_errors)
        if (
            (completion_introductions or completion.exists() or completion.is_symlink())
            and (
                barrier_introductions
                or barrier_root.exists()
                or barrier_root.is_symlink()
            )
        ):
            errors.append(
                "supervisor completion is forbidden after a terminal barrier failure"
            )
    if errors:
        raise ValueError(
            "S1 G1 thermodynamic-label R3 registration validation failed:\n- "
            + "\n- ".join(errors)
        )
    return config, rows, {"preregistration_commit": prereg, "r1_bridge": bridge}


def _runtime_row(row: dict[str, str]) -> dict[str, str]:
    return {
        **row,
        "replay_experiment_id": row["experiment_id"],
        "reference_experiment_id": row["source_experiment_id"],
        "solver": "ksdft",
    }


def _status_payload(experiment_id: str, *, accepted: bool) -> dict[str, object]:
    logical = logical_id(experiment_id)
    common: dict[str, object] = {
        "schema_version": 1,
        "protocol_revision": PROTOCOL_REVISION,
        "experiment_id": experiment_id,
        "logical_experiment_id": logical,
        "authoritative_for_r3": True,
        "retry_policy": "new_protocol_revision_and_new_experiment_ids_only",
    }
    if accepted:
        return {
            **common,
            "status": "accepted",
            "workflow_exit_code": 0,
            "parser_exit_code": 0,
            "core_validator_exit_code": 0,
        }
    return common


def _status_failure_r3(path: Path, experiment_id: str, *, accepted: bool) -> str | None:
    if not path.is_file() or path.is_symlink():
        return f"missing or symbolic {STATUS_NAME}"
    try:
        payload = read_json(path)
    except (ValueError, json.JSONDecodeError) as error:
        return f"invalid {STATUS_NAME}: {error}"
    common = _status_payload(experiment_id, accepted=False)
    for key, value in common.items():
        if payload.get(key) != value:
            return f"R3 authoritative status {key} differs"
    if accepted:
        return None if payload == _status_payload(experiment_id, accepted=True) else "accepted R3 status differs"
    allowed = {
        *common,
        "status",
        "workflow_exit_code",
        "parser_exit_code",
        "core_validator_exit_code",
        "failure_stage",
    }
    codes = [
        payload.get("workflow_exit_code"),
        payload.get("parser_exit_code"),
        payload.get("core_validator_exit_code"),
    ]
    stage = payload.get("failure_stage")
    if (
        set(payload) != allowed
        or payload.get("status") not in {"rejected", "indeterminate"}
        or not all(isinstance(value, int) and not isinstance(value, bool) for value in codes)
        or stage not in {"workflow", "thermodynamic_label_parser", "core_validator"}
    ):
        return "failed R3 authoritative status model differs"
    workflow, parser, core = codes
    coherent = (
        (stage == "workflow" and workflow != 0 and parser == 97 and core == 97)
        or (stage == "thermodynamic_label_parser" and workflow == 0 and parser != 0 and core == 97)
        or (stage == "core_validator" and workflow == 0 and parser == 0 and core != 0)
    )
    return None if coherent else "failed R3 status stage/exit tuple differs"


_R1_CLASSIFY_CORE = r1.classify_core_failures
_R1_CLASSIFY_NONCORE = r1.classify_noncore_failure


def classify_core_failures(experiment_id: str, failures: list[str]) -> dict[str, object]:
    payload = _R1_CLASSIFY_CORE(experiment_id, failures)
    payload["protocol_revision"] = PROTOCOL_REVISION
    payload["logical_experiment_id"] = logical_id(experiment_id)
    return payload


def classify_noncore_failure(
    run: Path,
    experiment_id: str,
    logical_experiment_id: str,
    stage: str,
    component_exit: int,
    diagnostic_path: Path | None = None,
) -> dict[str, object]:
    if logical_experiment_id != logical_id(experiment_id):
        raise ValueError("noncore failure physical/logical ID mapping differs")
    if stage == "workflow":
        payload = _R1_CLASSIFY_NONCORE(
            run, experiment_id, stage, component_exit, diagnostic_path
        )
        payload["protocol_revision"] = PROTOCOL_REVISION
        payload["logical_experiment_id"] = logical_experiment_id
        return payload
    if stage != "thermodynamic_label_parser":
        raise ValueError(f"unsupported non-core failure stage: {stage}")
    if (
        not isinstance(component_exit, int)
        or isinstance(component_exit, bool)
        or component_exit == 0
    ):
        raise ValueError("non-core failure component exit must be a nonzero integer")
    diagnostic = diagnostic_path or run / "thermodynamic_label_parser.stderr.txt"
    text = (
        diagnostic.read_text(encoding="utf-8", errors="replace")
        if diagnostic.is_file() and not diagnostic.is_symlink()
        else ""
    )
    reasons = [line for line in text.splitlines() if line.strip()]
    reasons.append(f"{stage}_exit_code={component_exit}")
    final_message = next(
        (line.strip().lower() for line in reversed(text.splitlines()) if line.strip()),
        "",
    )
    scientific_categories: list[str] = []
    if any(
        marker in final_message
        for marker in (
            "thermodynamic identity failed",
            "e_entropy(-ts) must be nonpositive",
            "expected f <= e_ec <= u",
        )
    ):
        scientific_categories.append("thermodynamic_identity")
    if any(
        marker in final_message
        for marker in (
            "density electron integration failed",
            "density electron relative error",
            "raw-log electron count differs",
        )
    ):
        scientific_categories.append("electron_number")
    if scientific_categories:
        status = "rejected"
        failure_class = "complete_numerical_or_runtime_contract_rejection"
        categories = sorted(set(scientific_categories))
    else:
        # A parser traceback, filename, or generic word such as
        # "thermodynamic" is never enough to claim a physical-identity failure.
        # Registration, input, provenance, malformed evidence, and unexpected
        # software exceptions remain indeterminate evidence-contract failures.
        status = "indeterminate"
        failure_class = "parser_contract_or_evidence_failure"
        categories = ["parser_contract_or_evidence"]
    payload = {
        "schema_version": 1,
        "protocol_revision": PROTOCOL_REVISION,
        "experiment_id": experiment_id,
        "failure_component": stage,
        "status": status,
        "failure_class": failure_class,
        "failure_categories": categories,
        "failure_reasons": reasons,
        "retry_policy": "new_protocol_revision_and_new_experiment_ids_only",
    }
    payload["protocol_revision"] = PROTOCOL_REVISION
    payload["logical_experiment_id"] = logical_experiment_id
    return payload


def _classify_noncore_for_r1(
    run: Path,
    experiment_id: str,
    stage: str,
    component_exit: int,
    diagnostic_path: Path | None = None,
) -> dict[str, object]:
    return classify_noncore_failure(
        run,
        experiment_id,
        logical_id(experiment_id),
        stage,
        component_exit,
        diagnostic_path,
    )


_R1_READ_JSON = r1.read_json


def _read_json_for_r3_failure(path: Path) -> dict:
    payload = _R1_READ_JSON(path)
    if path.name == FAILURE_INVENTORY_NAME and isinstance(payload, dict):
        payload = dict(payload)
        payload.pop("logical_experiment_id", None)
    return payload


@contextmanager
def _r3_failure_policy() -> Iterator[None]:
    names = {
        "PROTOCOL_REVISION": PROTOCOL_REVISION,
        "STATUS_NAME": STATUS_NAME,
        "FAILURE_CLASS_NAME": FAILURE_CLASS_NAME,
        "FAILURE_INVENTORY_NAME": FAILURE_INVENTORY_NAME,
        "_status_failure": _status_failure_r3,
        "classify_core_failures": classify_core_failures,
        "classify_noncore_failure": _classify_noncore_for_r1,
        "read_json": _read_json_for_r3_failure,
    }
    original = {name: getattr(r1, name) for name in names}
    try:
        for name, value in names.items():
            setattr(r1, name, value)
        yield
    finally:
        for name, value in original.items():
            setattr(r1, name, value)


def validate_failed_r3_run(
    project_root: Path,
    config: dict,
    row: dict[str, str],
    *,
    require_committed: bool,
    directory: Path | None = None,
) -> list[str]:
    _, marker_commit, marker_errors = validate_attempt_marker(
        project_root,
        config,
        row["experiment_id"],
        require_committed=True,
    )
    if marker_commit is None and not marker_errors:
        marker_errors.append(f"{row['experiment_id']}: formal-attempt marker is not committed")
    run = directory or project_root / "runs" / row["experiment_id"]
    inventory_path = run / FAILURE_INVENTORY_NAME
    if inventory_path.is_file() and not inventory_path.is_symlink():
        try:
            inventory = _R1_READ_JSON(inventory_path)
            if inventory.get("logical_experiment_id") != logical_id(row["experiment_id"]):
                marker_errors.append(
                    f"{row['experiment_id']}: failure inventory logical ID differs"
                )
        except (ValueError, json.JSONDecodeError) as error:
            marker_errors.append(f"{row['experiment_id']}: invalid R3 failure inventory: {error}")
    with _r3_failure_policy():
        failures = r1.validate_failed_r1_run(
            project_root,
            row,
            require_committed=require_committed,
            directory=directory,
        )
    return [*marker_errors, *failures]


def replay_evidence(
    project_root: Path,
    config: dict,
    row: dict[str, str],
    *,
    require_committed: bool,
    require_replay_status: bool,
    scientific_config_path: Path | None = None,
    scientific_manifest_path: Path | None = None,
) -> tuple[dict[str, object], list[str]]:
    experiment_id = row["experiment_id"]
    if (scientific_config_path is None) != (scientific_manifest_path is None):
        return {}, [
            f"{experiment_id}: scientific config and manifest must be supplied together"
        ]
    logical = logical_id(experiment_id)
    run = project_root / "runs" / experiment_id
    role = r1._role(row)
    errors: list[str] = []
    _, marker_commit, marker_errors = validate_attempt_marker(
        project_root, config, experiment_id, require_committed=True
    )
    errors.extend(marker_errors)
    if marker_commit is None and not marker_errors:
        errors.append(f"{experiment_id}: formal-attempt marker is not committed")
    with r1._runtime_science_policy(role == "standard_replay"):
        errors.extend(
            runtime_validation.validate_replay_run(
                project_root,
                config,
                _runtime_row(row),
                require_committed=require_committed,
                require_replay_status=False,
            )
        )
    payload: dict[str, object] = {}
    try:
        labels_path = run / LABEL_NAME
        if not labels_path.is_file() or labels_path.is_symlink():
            raise ValueError(f"missing or symbolic {LABEL_NAME}")
        labels = read_json(labels_path)
        reparsed = parse_label_run(
            run,
            config_path=project_root / CONFIG_PATH,
            manifest_path=project_root / MANIFEST_PATH,
            scientific_config_path=scientific_config_path,
            scientific_manifest_path=scientific_manifest_path,
        )
        if labels != reparsed:
            errors.append(f"{experiment_id}: thermodynamic labels differ from R3 recomputation")
        result = read_json(run / "result.json")
        metadata = read_json(run / "input_metadata.json")
        if result.get("converged") is not True:
            errors.append(f"{experiment_id}: run did not converge")
        log = find_single_log(run)
        text = log.read_text(encoding="utf-8", errors="strict")
        if "#SCF IS CONVERGED#" not in text or "#SCF IS NOT CONVERGED#" in text:
            errors.append(f"{experiment_id}: raw-log convergence markers failed")
        atom_count = int(metadata["atom_count"])
        errors.extend(
            f"{experiment_id}: {failure}"
            for failure in r1._thermodynamic_failures(labels, result, text, atom_count)
        )
        charge = r1._find_output(run, "chg.cube")
        potential = r1._find_output(run, "pot.cube")
        grid = parse_charge_grid(log)
        charge_payload = parse_abacus_cube(
            charge,
            quantity="thermal_density",
            units="electron/bohr^3",
            structure_path=run / "STRU",
            expected_grid=grid,
        )
        potential_payload = parse_abacus_cube(
            potential,
            quantity="local_effective_ks_potential",
            units="Ry",
            structure_path=run / "STRU",
            expected_grid=grid,
        )
        r1.validate_cube_geometry_against_stru(charge_payload, run / "STRU")
        r1.validate_cube_geometry_against_stru(potential_payload, run / "STRU")
        if charge_payload.geometry_signature != potential_payload.geometry_signature:
            errors.append(f"{experiment_id}: charge/potential cube geometry differs")
        expected, derivation = expected_electrons(run)
        integration = integrate_cube(charge, run / "STRU", expected, grid)
        if integration.get("accepted") is not True:
            errors.append(f"{experiment_id}: electron-number integration failed")
        contract = config["kmp_contract"]
        kmp = validate_kmp_runtime_contract(
            run,
            expected_libomp_path=contract["libomp"]["path"],
            expected_libomp_realpath=contract["libomp"]["realpath"],
            expected_libomp_sha256=contract["libomp"]["sha256"],
            require_registered_mapping_pattern=True,
        )
        if kmp.get("accepted") is not True:
            errors.append(f"{experiment_id}: KMP runtime contract rejected")
        equivalence: dict[str, object] | None = None
        if role == "standard_replay":
            source_result = read_json(project_root / row["source_result_path"])
            equivalence = scientific_equivalence(source_result, result)
            delta_eec = abs(
                float(result["zero_temp_extrapolated_energy_ev_per_atom"])
                - float(source_result["zero_temp_extrapolated_energy_ev_per_atom"])
            ) * 1000.0
            delta_f = abs(
                float(result["free_energy_ev_per_atom"])
                - float(source_result["free_energy_ev_per_atom"])
            ) * 1000.0
            delta_pressure = abs(float(result["pressure_gpa"]) - float(source_result["pressure_gpa"]))
            equivalence.update(
                {
                    "delta_entropy_corrected_energy_mev_per_atom": delta_eec,
                    "delta_free_energy_mev_per_atom": delta_f,
                    "delta_pressure_gpa": delta_pressure,
                    "accepted": delta_eec < 0.1 and delta_f < 0.1 and delta_pressure < 0.02,
                }
            )
            if equivalence["accepted"] is not True:
                errors.append(f"{experiment_id}: standard replay equivalence failed")
        payload = {
            "schema_version": 2,
            "protocol_revision": PROTOCOL_REVISION,
            "status": "accepted" if not errors else "rejected",
            "experiment_id": experiment_id,
            "logical_experiment_id": logical,
            "source_kind": "r3_executed",
            "source_experiment_id": row["source_experiment_id"],
            "run_role": role,
            "thermodynamic_labels_path": f"runs/{experiment_id}/{LABEL_NAME}",
            "thermodynamic_labels_sha256": sha256(labels_path),
            "charge_density": {
                "path": _relative(project_root, charge),
                "sha256": charge_payload.sha256,
                "dimensions": list(grid),
            },
            "local_effective_potential": {
                "path": _relative(project_root, potential),
                "sha256": potential_payload.sha256,
                "dimensions": list(grid),
                "unit": "Ry",
            },
            "expected_electron_derivation": derivation,
            "electron_number_integration": integration,
            "standard_replay_equivalence": equivalence,
            "kmp_runtime_contract": kmp,
            "provenance": {
                "config_path": str(CONFIG_PATH),
                "config_sha256": sha256(project_root / CONFIG_PATH),
                "manifest_path": str(MANIFEST_PATH),
                "manifest_sha256": sha256(project_root / MANIFEST_PATH),
                "preregistration_commit": _introduction_commit(project_root, str(CONFIG_PATH)),
                "r1_logical_input_tree_oid": _tree_oid(
                    project_root,
                    R1_PREREGISTRATION_COMMIT,
                    f"{R1_INPUT_ROOT.as_posix()}/{logical}",
                ),
                "replay_code_commit": read_json(run / "experiment_metadata.json")["code_commit"],
            },
        }
        if require_replay_status:
            status_error = _status_failure_r3(run / STATUS_NAME, experiment_id, accepted=True)
            if status_error:
                errors.append(f"{experiment_id}: {status_error}")
            elif require_committed:
                failure = _tracked_head_failure(project_root, f"runs/{experiment_id}/{STATUS_NAME}")
                if failure:
                    errors.append(f"{experiment_id}: {failure}")
        evidence = run / EVIDENCE_NAME
        if evidence.exists() or evidence.is_symlink():
            if not evidence.is_file() or evidence.is_symlink():
                errors.append(f"{experiment_id}: R3 evidence is not a regular file")
            elif read_json(evidence) != payload:
                errors.append(f"{experiment_id}: R3 evidence differs from recomputation")
            elif require_committed:
                failure = _tracked_head_failure(project_root, f"runs/{experiment_id}/{EVIDENCE_NAME}")
                if failure:
                    errors.append(f"{experiment_id}: {failure}")
        elif require_committed or require_replay_status:
            errors.append(f"{experiment_id}: missing {EVIDENCE_NAME}")
    except (
        FileNotFoundError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
    ) as error:
        errors.append(f"{experiment_id}: R3 run evidence validation failed: {error}")
    return payload, errors


def replay_effective_evidence(
    project_root: Path,
    config: dict,
    rows: list[dict[str, str]],
    logical_experiment_id: str,
    *,
    require_committed: bool,
    require_replay_status: bool,
    scientific_config_path: Path | None = None,
    scientific_manifest_path: Path | None = None,
) -> tuple[dict[str, object], list[str]]:
    if logical_experiment_id in R1_REUSED_AUDIT_IDS:
        r1_config = read_json(project_root / R1_CONFIG_PATH)
        row = _r1_by_id(project_root)[logical_experiment_id]
        return r1.replay_evidence(
            project_root,
            r1_config,
            row,
            require_committed=require_committed,
            require_replay_status=require_replay_status,
        )
    return replay_evidence(
        project_root,
        config,
        row_for_logical(project_root, rows, logical_experiment_id),
        require_committed=require_committed,
        require_replay_status=require_replay_status,
        scientific_config_path=scientific_config_path,
        scientific_manifest_path=scientific_manifest_path,
    )


# A descriptive compatibility alias for early R3 callers.  The analyzer-facing
# public API is ``replay_effective_evidence`` above.
replay_logical_evidence = replay_effective_evidence


def _write_evidence(path: Path, payload: dict[str, object]) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError(f"refusing to overwrite R3 evidence: {path}")
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _write_exclusive_json(path: Path, payload: dict[str, object]) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError(f"refusing to overwrite R3 failure evidence: {path}")
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _evidence_name_for_logical(logical_experiment_id: str) -> str:
    return r1.EVIDENCE_NAME if logical_experiment_id in R1_REUSED_AUDIT_IDS else EVIDENCE_NAME


def _ensure_logical_committed(project_root: Path, logical_experiment_id: str) -> None:
    physical = effective_id(logical_experiment_id)
    failure = _tracked_head_failure(
        project_root, f"runs/{physical}/{_evidence_name_for_logical(logical_experiment_id)}"
    )
    if failure:
        raise ValueError(f"logical gate {logical_experiment_id}/{physical}: {failure}")


def _logical_row_result(
    project_root: Path, rows: list[dict[str, str]], logical_experiment_id: str
) -> dict:
    del rows
    return read_json(project_root / "runs" / effective_id(logical_experiment_id) / "result.json")


def _logical_pair_payload(
    project_root: Path,
    rows: list[dict[str, str]],
    coarse_logical_id: str,
    reference_logical_id: str,
    *,
    require_committed: bool,
) -> dict[str, object]:
    if require_committed:
        _ensure_logical_committed(project_root, coarse_logical_id)
        _ensure_logical_committed(project_root, reference_logical_id)
    coarse_id = effective_id(coarse_logical_id)
    reference_id = effective_id(reference_logical_id)
    coarse = project_root / "runs" / coarse_id
    reference = project_root / "runs" / reference_id
    expected, _ = expected_electrons(reference)
    fields = r1.field_metrics(
        r1._find_output(coarse, "chg.cube"),
        r1._find_output(reference, "chg.cube"),
        r1._find_output(coarse, "pot.cube"),
        r1._find_output(reference, "pot.cube"),
        structure_path=reference / "STRU",
        expected_electron_count=expected,
    )
    coarse_result = _logical_row_result(project_root, rows, coarse_logical_id)
    reference_result = _logical_row_result(project_root, rows, reference_logical_id)
    absolute_energy = abs(
        float(reference_result["zero_temp_extrapolated_energy_ev_per_atom"])
        - float(coarse_result["zero_temp_extrapolated_energy_ev_per_atom"])
    ) * 1000.0
    pressure = abs(float(reference_result["pressure_gpa"]) - float(coarse_result["pressure_gpa"]))
    logical_row = row_for_logical(project_root, rows, coarse_logical_id)
    return {
        "material": logical_row["material"],
        "volume_ratio": float(logical_row["volume_ratio"]),
        "coarse_logical_experiment_id": coarse_logical_id,
        "reference_logical_experiment_id": reference_logical_id,
        "coarse_experiment_id": coarse_id,
        "reference_experiment_id": reference_id,
        "absolute_energy_difference_mev_per_atom": absolute_energy,
        "absolute_pressure_difference_gpa_diagnostic": pressure,
        "field_metrics": fields,
    }


def evaluate_pilot_gate(
    project_root: Path,
    config: dict,
    rows: list[dict[str, str]],
    *,
    require_committed: bool,
) -> dict[str, object]:
    # P1 is the complete twelve-run, six-pair dense-k anchor set. Historical
    # R1 candidates are provenance only and must never satisfy this gate.
    result = evaluate_k_gate(
        project_root, config, rows, require_committed=require_committed
    )
    return {
        **result,
        "pilot_ids": list(R3_PILOT_IDS),
        "gate_semantics": "all_new_r3_complete_dense_k_anchor_set",
    }


def evaluate_half_quarter_pair(
    project_root: Path,
    config: dict,
    rows: list[dict[str, str]],
    quarter_experiment_id: str,
    *,
    require_committed: bool,
) -> dict[str, object]:
    quarter_logical = (
        logical_id(quarter_experiment_id)
        if quarter_experiment_id in R3_AUDIT_IDS
        else quarter_experiment_id
    )
    pairs = dict((quarter, half) for half, quarter in HALF_QUARTER_LOGICAL_PAIRS)
    if quarter_logical not in pairs:
        raise ValueError("half-quarter gate requires a logical common-quarter slot 021--034")
    half_logical = pairs[quarter_logical]
    logical_effective_id(config, half_logical)
    logical_effective_id(config, quarter_logical)
    quarter_row = row_for_logical(project_root, rows, quarter_logical)
    expected_reference = half_logical
    if quarter_row["reference_experiment_id"] != expected_reference:
        raise ValueError("manifest half-quarter scientific partner differs from logical resolver")
    payload = _logical_pair_payload(
        project_root,
        rows,
        half_logical,
        quarter_logical,
        require_committed=require_committed,
    )
    return {
        "half_logical_experiment_id": half_logical,
        "quarter_logical_experiment_id": quarter_logical,
        "half_experiment_id": effective_id(half_logical),
        "quarter_experiment_id": effective_id(quarter_logical),
        "material": quarter_row["material"],
        "volume_ratio": float(quarter_row["volume_ratio"]),
        "field_metrics": payload["field_metrics"],
        "accepted": payload["field_metrics"]["accepted"] is True,  # type: ignore[index]
    }


def _eos_point(project_root: Path, experiment_id: str, ratio: float) -> dict[str, float]:
    run = project_root / "runs" / experiment_id
    structure = parse_stru(run / "STRU")
    atom_count = sum(structure.species_counts.values())
    thermo = parse_thermodynamic_log(
        find_single_log(run).read_text(encoding="utf-8", errors="strict"),
        expected_atom_count=atom_count,
    )
    labels = thermo["energy_labels_ev_per_atom"]
    if not isinstance(labels, dict):
        raise ValueError(f"{experiment_id}: missing per-atom energy labels")
    return {
        "volume_ratio": ratio,
        "volume_per_atom_angstrom3": structure.volume_bohr3 * BOHR_TO_ANGSTROM**3 / atom_count,
        "e_ec_ev_per_atom": float(labels["E_ec"]),
    }


def _eos_fit_gate(points: list[dict[str, float]]) -> tuple[dict, list[str]]:
    failures: list[str] = []
    ordered = sorted(points, key=lambda point: point["volume_per_atom_angstrom3"])
    if len(ordered) != 7 or {point["volume_ratio"] for point in ordered} != set(EOS_RATIOS):
        return {}, ["series_does_not_contain_seven_frozen_ratios"]
    fit = fit_bm3(
        [point["volume_per_atom_angstrom3"] for point in ordered],
        [point["e_ec_ev_per_atom"] for point in ordered],
    )
    if not ordered[0]["volume_per_atom_angstrom3"] < float(fit["v0_angstrom3_per_atom"]) < ordered[-1]["volume_per_atom_angstrom3"]:
        failures.append("fitted_v0_not_strictly_inside_sampled_interval")
    if not float(fit["b0_gpa"]) > 0.0:
        failures.append("bulk_modulus_not_strictly_positive")
    if not float(fit["max_abs_residual_mev_per_atom"]) < 1.0:
        failures.append("maximum_fit_residual_not_strictly_below_1_mev")
    return fit, failures


def _logical_series(material: str, level: str) -> tuple[str, ...]:
    mapping = {
        ("al", "half"): tuple(f"S1-20260806-{number:03d}" for number in range(7, 14)),
        ("mg", "half"): tuple(f"S1-20260806-{number:03d}" for number in range(14, 21)),
        ("al", "quarter"): tuple(f"S1-20260806-{number:03d}" for number in range(21, 28)),
        ("mg", "quarter"): tuple(f"S1-20260806-{number:03d}" for number in range(28, 35)),
    }
    try:
        return mapping[(material, level)]
    except KeyError as error:
        raise ValueError(f"unsupported logical EOS series: {material}/{level}") from error


def _standard_series(material: str) -> tuple[str, ...]:
    first = 85 if material == "al" else 106
    return tuple(f"S1-20260805-{first + index:03d}" for index in range(7))


def evaluate_adjacent_eos_gate(
    project_root: Path,
    config: dict,
    rows: list[dict[str, str]],
    material: str,
    coarse_level: str,
    fine_level: str,
    *,
    require_committed: bool,
) -> dict[str, object]:
    if material not in {"al", "mg"} or (coarse_level, fine_level) not in {
        ("standard", "half"),
        ("half", "quarter"),
    }:
        raise ValueError("unsupported adjacent EOS gate")
    logical_by_level = {
        "half": _logical_series(material, "half"),
        "quarter": _logical_series(material, "quarter"),
    }
    for level in {coarse_level, fine_level} - {"standard"}:
        for logical in logical_by_level[level]:
            logical_effective_id(config, logical)
    ids_by_level = {
        "standard": _standard_series(material),
        "half": tuple(effective_id(value) for value in logical_by_level["half"]),
        "quarter": tuple(effective_id(value) for value in logical_by_level["quarter"]),
    }
    for level in {coarse_level, fine_level} - {"standard"}:
        for logical in logical_by_level[level]:
            if require_committed:
                _ensure_logical_committed(project_root, logical)
            if row_for_logical(project_root, rows, logical)["material"] != material:
                raise ValueError(f"logical EOS material differs: {logical}")
    series: dict[str, dict[str, object]] = {}
    for level in (coarse_level, fine_level):
        points = [
            _eos_point(project_root, experiment_id, ratio)
            for experiment_id, ratio in zip(ids_by_level[level], EOS_RATIOS)
        ]
        fit, failures = _eos_fit_gate(points)
        series[level] = {"points": points, "fit": fit, "fit_failures": failures}
    coarse = series[coarse_level]
    fine = series[fine_level]
    if coarse["fit_failures"] or fine["fit_failures"]:
        return {
            "material": material,
            "coarse_level": coarse_level,
            "fine_level": fine_level,
            "series": series,
            "accepted": False,
        }
    coarse_points = {float(item["volume_ratio"]): item for item in coarse["points"]}  # type: ignore[union-attr]
    fine_points = {float(item["volume_ratio"]): item for item in fine["points"]}  # type: ignore[union-attr]
    coarse_v100 = float(coarse_points[1.0]["e_ec_ev_per_atom"])
    fine_v100 = float(fine_points[1.0]["e_ec_ev_per_atom"])
    anchored = []
    for ratio in EOS_RATIOS:
        difference = abs(
            (float(fine_points[ratio]["e_ec_ev_per_atom"]) - fine_v100)
            - (float(coarse_points[ratio]["e_ec_ev_per_atom"]) - coarse_v100)
        ) * 1000.0
        anchored.append({"volume_ratio": ratio, "difference_mev_per_atom": difference})
    maximum = max(float(item["difference_mev_per_atom"]) for item in anchored)
    coarse_v0 = float(coarse["fit"]["v0_angstrom3_per_atom"])  # type: ignore[index]
    fine_v0 = float(fine["fit"]["v0_angstrom3_per_atom"])  # type: ignore[index]
    volume = abs(fine_v0 - coarse_v0) / coarse_v0 * 100.0
    return {
        "material": material,
        "coarse_level": coarse_level,
        "fine_level": fine_level,
        "series": series,
        "anchored_rows": anchored,
        "max_anchored_energy_difference_mev_per_atom": maximum,
        "equilibrium_volume_difference_percent": volume,
        "energy_accepted": maximum < 2.0,
        "volume_accepted": volume < 0.2,
        "accepted": maximum < 2.0 and volume < 0.2,
    }


def evaluate_k_gate(
    project_root: Path,
    config: dict,
    rows: list[dict[str, str]],
    *,
    require_committed: bool,
) -> dict[str, object]:
    for logical in (value for pair in K_LOGICAL_PAIRS for value in pair):
        logical_effective_id(config, logical)
    pairs = [
        _logical_pair_payload(
            project_root, rows, common, extra, require_committed=require_committed
        )
        for common, extra in K_LOGICAL_PAIRS
    ]
    material_payloads: dict[str, dict[str, object]] = {}
    for material in ("al", "mg"):
        selected = [item for item in pairs if item["material"] == material]
        if len(selected) != 3 or {float(item["volume_ratio"]) for item in selected} != set(ANCHOR_RATIOS):
            raise ValueError(f"{material}: k gate must contain three frozen logical anchors")
        at_v100 = next(item for item in selected if item["volume_ratio"] == 1.0)
        common_v100 = _logical_row_result(
            project_root, rows, str(at_v100["coarse_logical_experiment_id"])
        )
        extra_v100 = _logical_row_result(
            project_root, rows, str(at_v100["reference_logical_experiment_id"])
        )
        anchored: list[dict[str, float]] = []
        for item in selected:
            common = _logical_row_result(
                project_root, rows, str(item["coarse_logical_experiment_id"])
            )
            extra = _logical_row_result(
                project_root, rows, str(item["reference_logical_experiment_id"])
            )
            difference = abs(
                (
                    float(extra["zero_temp_extrapolated_energy_ev_per_atom"])
                    - float(extra_v100["zero_temp_extrapolated_energy_ev_per_atom"])
                )
                - (
                    float(common["zero_temp_extrapolated_energy_ev_per_atom"])
                    - float(common_v100["zero_temp_extrapolated_energy_ev_per_atom"])
                )
            ) * 1000.0
            anchored.append({"volume_ratio": float(item["volume_ratio"]), "difference": difference})
        maximum = max(item["difference"] for item in anchored)
        field_passed = all(item["field_metrics"]["accepted"] is True for item in selected)  # type: ignore[index]
        v100 = float(at_v100["absolute_energy_difference_mev_per_atom"])
        material_payloads[material] = {
            "v100_absolute_energy_difference_mev_per_atom": v100,
            "max_anchored_energy_difference_mev_per_atom": maximum,
            "anchored_rows": anchored,
            "field_passed": field_passed,
            "accepted": v100 < 2.0 and maximum < 2.0 and field_passed,
        }
    return {
        "pair_count": len(pairs),
        "pairs": pairs,
        "materials": material_payloads,
        "accepted": len(pairs) == 6
        and len(material_payloads) == 2
        and all(item["accepted"] is True for item in material_payloads.values()),
    }


def _validate_gate_runs(
    project_root: Path,
    config: dict,
    rows: list[dict[str, str]],
    logical_ids: tuple[str, ...],
    *,
    scientific_config_path: Path | None = None,
    scientific_manifest_path: Path | None = None,
) -> None:
    for logical in logical_ids:
        _, failures = replay_logical_evidence(
            project_root,
            config,
            rows,
            logical,
            require_committed=True,
            require_replay_status=True,
            scientific_config_path=scientific_config_path,
            scientific_manifest_path=scientific_manifest_path,
        )
        if failures:
            raise ValueError(
                f"logical gate run validation failed for {logical}/{effective_id(logical)}:\n- "
                + "\n- ".join(failures)
            )


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", nargs="?", type=Path, default=project_root / MANIFEST_PATH)
    parser.add_argument("--config", type=Path, default=project_root / CONFIG_PATH)
    parser.add_argument("--scientific-config", type=Path)
    parser.add_argument("--scientific-manifest", type=Path)
    parser.add_argument("--require-committed", action="store_true")
    parser.add_argument("--check-run-core")
    parser.add_argument("--check-run")
    parser.add_argument("--check-failure-run")
    parser.add_argument("--check-failure-archives")
    parser.add_argument("--check-attempt-marker")
    parser.add_argument("--check-detachment-attestation", action="store_true")
    parser.add_argument(
        "--check-detachment-attestation-record", action="store_true"
    )
    parser.add_argument("--check-analysis-summary", action="store_true")
    parser.add_argument("--require-supervisor-completion", action="store_true")
    parser.add_argument("--write-run-evidence")
    parser.add_argument("--write-core-failure-evidence", action="store_true")
    parser.add_argument("--require-half-quarter-pair")
    parser.add_argument("--require-adjacent-eos", nargs=3, metavar=("MATERIAL", "COARSE", "FINE"))
    parser.add_argument("--require-pilot-gate", action="store_true")
    parser.add_argument("--require-k-gate", action="store_true")
    parser.add_argument("--require-all-runs", action="store_true")
    args = parser.parse_args()
    per_run = [
        value
        for value in (
            args.check_run_core,
            args.check_run,
            args.check_failure_run,
            args.check_failure_archives,
            args.write_run_evidence,
        )
        if value
    ]
    if len(per_run) > 1:
        parser.error("select at most one per-run mode")
    if args.check_attempt_marker and per_run:
        parser.error("--check-attempt-marker cannot be combined with a per-run evidence mode")
    if args.write_core_failure_evidence and args.check_run_core is None:
        parser.error("--write-core-failure-evidence requires --check-run-core")
    if (
        args.check_detachment_attestation
        and args.check_detachment_attestation_record
    ):
        parser.error("select only one detachment-attestation validation mode")
    if (args.scientific_config is None) != (args.scientific_manifest is None):
        parser.error(
            "--scientific-config and --scientific-manifest must be supplied together"
        )
    require_registration_commit = (
        args.require_committed or args.require_supervisor_completion
    )
    config, rows, details = validate_registration(
        project_root,
        args.config.resolve(),
        args.manifest.resolve(),
        require_committed=require_registration_commit,
        skip_terminal_evidence_validation=args.require_supervisor_completion,
        scientific_config_path=args.scientific_config,
        scientific_manifest_path=args.scientific_manifest,
    )
    by_id = {row["experiment_id"]: row for row in rows}
    checked: list[str] = []
    supervisor_completion: dict[str, object] | None = None
    if args.check_detachment_attestation or args.check_detachment_attestation_record:
        _, failures = validate_detachment_attestation(
            project_root,
            config,
            require_committed=args.require_committed,
            require_live_sealed_inputs=args.check_detachment_attestation,
        )
        if failures:
            raise ValueError(
                "R3 detachment-attestation validation failed:\n- "
                + "\n- ".join(failures)
            )
    if args.check_attempt_marker:
        experiment_id = args.check_attempt_marker
        _, marker_commit, failures = validate_attempt_marker(
            project_root,
            config,
            experiment_id,
            require_committed=args.require_committed,
        )
        if failures:
            raise ValueError("R3 attempt-marker validation failed:\n- " + "\n- ".join(failures))
        if args.require_committed and marker_commit is None:
            raise ValueError(f"R3 attempt marker is not committed: {experiment_id}")
        checked.append(experiment_id)
    if args.check_analysis_summary:
        _, failures = validate_precompletion_analysis(
            project_root,
            config,
            require_committed=args.require_committed,
        )
        if failures:
            raise ValueError(
                "R3 pre-completion analysis validation failed:\n- "
                + "\n- ".join(failures)
            )
    if args.require_supervisor_completion:
        supervisor_completion, failures = validate_supervisor_completion(
            project_root,
            config,
            require_committed=True,
        )
        if failures:
            raise ValueError(
                "R3 supervisor-completion validation failed:\n- "
                + "\n- ".join(failures)
            )
        replay_failures = recompute_final_analysis(
            project_root,
            args.config.resolve(),
            args.manifest.resolve(),
        )
        if replay_failures:
            raise ValueError(
                "R3 final aggregate-science replay failed:\n- "
                + "\n- ".join(replay_failures)
            )
    if per_run:
        experiment_id = per_run[0]
        if experiment_id not in by_id:
            raise ValueError(f"requested run is outside the R3 manifest: {experiment_id}")
        row = by_id[experiment_id]
        if args.check_failure_archives:
            failures: list[str] = []
            events = _archive_events(project_root, experiment_id, failures)
            if _active_introduction(project_root, experiment_id, failures) is not None:
                failures.append(f"{experiment_id}: active run remains after archive")
            if len(events) != 1:
                failures.append(f"{experiment_id}: expected exactly one no-retry archive")
            if len(events) == 1:
                failures.extend(
                    validate_failed_r3_run(
                        project_root,
                        config,
                        row,
                        require_committed=args.require_committed,
                        directory=(
                            project_root
                            / "failed_runs/runtime_relocation"
                            / experiment_id
                            / events[0][0]
                        ),
                    )
                )
            if failures:
                raise ValueError("R3 failed-archive validation failed:\n- " + "\n- ".join(failures))
        elif args.check_failure_run:
            failures = validate_failed_r3_run(
                project_root, config, row, require_committed=args.require_committed
            )
            if failures:
                raise ValueError("R3 failed-run validation failed:\n- " + "\n- ".join(failures))
        else:
            payload, failures = replay_evidence(
                project_root,
                config,
                row,
                require_committed=args.require_committed,
                require_replay_status=args.check_run is not None,
                scientific_config_path=args.scientific_config,
                scientific_manifest_path=args.scientific_manifest,
            )
            if failures:
                if args.check_run_core and args.write_core_failure_evidence:
                    _write_exclusive_json(
                        project_root / "runs" / experiment_id / FAILURE_CLASS_NAME,
                        classify_core_failures(experiment_id, failures),
                    )
                raise ValueError("R3 run validation failed:\n- " + "\n- ".join(failures))
            evidence = project_root / "runs" / experiment_id / EVIDENCE_NAME
            if args.write_run_evidence:
                _write_evidence(evidence, payload)
            elif args.check_run_core:
                pass
            elif not evidence.is_file() or evidence.is_symlink():
                raise ValueError(f"missing {EVIDENCE_NAME} for {experiment_id}")
        checked.append(experiment_id)

    half_quarter: dict[str, object] | None = None
    if args.require_half_quarter_pair:
        requested = args.require_half_quarter_pair
        logical = logical_id(requested) if requested in R3_AUDIT_IDS else requested
        quarter_to_half = dict((quarter, half) for half, quarter in HALF_QUARTER_LOGICAL_PAIRS)
        if logical not in quarter_to_half:
            raise ValueError("--require-half-quarter-pair requires logical slot 021--034 or its effective ID")
        _validate_gate_runs(
            project_root,
            config,
            rows,
            (quarter_to_half[logical], logical),
            scientific_config_path=args.scientific_config,
            scientific_manifest_path=args.scientific_manifest,
        )
        half_quarter = evaluate_half_quarter_pair(
            project_root, config, rows, logical, require_committed=True
        )
        if half_quarter["accepted"] is not True:
            raise ValueError("R3 half-quarter field gate rejected: " + json.dumps(half_quarter, sort_keys=True))

    adjacent: dict[str, object] | None = None
    if args.require_adjacent_eos:
        material, coarse, fine = args.require_adjacent_eos
        logicals: tuple[str, ...] = ()
        for level in {coarse, fine} - {"standard"}:
            logicals += _logical_series(material, level)
        _validate_gate_runs(
            project_root,
            config,
            rows,
            logicals,
            scientific_config_path=args.scientific_config,
            scientific_manifest_path=args.scientific_manifest,
        )
        adjacent = evaluate_adjacent_eos_gate(
            project_root, config, rows, material, coarse, fine, require_committed=True
        )
        if adjacent["accepted"] is not True:
            raise ValueError("R3 adjacent EOS gate rejected: " + json.dumps(adjacent, sort_keys=True))

    pilot: dict[str, object] | None = None
    if args.require_pilot_gate:
        pilot = evaluate_pilot_gate(project_root, config, rows, require_committed=True)
        if pilot["accepted"] is not True:
            raise ValueError("R3 recovery pilot gate rejected: " + json.dumps(pilot, sort_keys=True))

    k_gate: dict[str, object] | None = None
    if args.require_k_gate:
        logicals = tuple(value for pair in K_LOGICAL_PAIRS for value in pair)
        _validate_gate_runs(
            project_root,
            config,
            rows,
            logicals,
            scientific_config_path=args.scientific_config,
            scientific_manifest_path=args.scientific_manifest,
        )
        k_gate = evaluate_k_gate(project_root, config, rows, require_committed=True)
        if k_gate["accepted"] is not True:
            raise ValueError("R3 complete low-smearing k gate rejected: " + json.dumps(k_gate, sort_keys=True))

    if args.require_all_runs:
        for logical in LOGICAL_IDS:
            _, failures = replay_logical_evidence(
                project_root,
                config,
                rows,
                logical,
                require_committed=True,
                require_replay_status=True,
                scientific_config_path=args.scientific_config,
                scientific_manifest_path=args.scientific_manifest,
            )
            if failures:
                raise ValueError(
                    f"R3 all-run validation failed for {logical}/{effective_id(logical)}:\n- "
                    + "\n- ".join(failures)
                )
            checked.append(effective_id(logical))

    output = {
        "protocol_revision": PROTOCOL_REVISION,
        "config_sha256": sha256(args.config),
        "manifest_sha256": sha256(args.manifest),
        "preregistration_commit": details["preregistration_commit"],
        "r1_reused_count": 0,
        "r1_historical_excluded_count": 10,
        "r3_new_count": 40,
        "logical_denominator": 40,
        "checked_run_ids": checked,
        "half_quarter_pair": half_quarter,
        "adjacent_eos": adjacent,
        "pilot_gate": pilot,
        "k_gate": k_gate,
        "supervisor_completion": supervisor_completion,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
