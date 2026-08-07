#!/usr/bin/env python3
"""Generate the frozen S1-G1 thermodynamic-label R3 preregistration.

R3 is a clean rerun of the immutable R1 scientific matrix after R2 terminated
at a pre-scientific parser-registration barrier.  No R1 or R2 run is reused in
the R3 acceptance denominator: all forty logical slots receive all-new
experiment IDs.  Every R3 input is derived mechanically from the corresponding
blob at the R1 preregistration commit.

The default is read-only.  ``--write`` additionally requires a completely
clean repository and creates only the canonical config, manifest, and 40-run
input tree.  It never creates a run or an attempt-ledger entry.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Iterable

from s1_g1_thermodynamic_label_common import (
    AUDIT_IDS as R1_AUDIT_IDS,
    MANIFEST_FIELDS,
    canonical_json_bytes,
    parse_input_text,
    require,
    sha256_bytes,
    sha256_regular_file,
)


PROTOCOL_REVISION = "S1-G1-THERMODYNAMIC-LABEL-AUDIT-R3"
CONFIG_PATH = Path("config/S1_g1_thermodynamic_label_audit_r3.json")
MANIFEST_PATH = Path("config/S1_g1_thermodynamic_label_audit_r3_manifest.tsv")
INPUT_ROOT = Path("inputs/s1/g1_thermodynamic_label_audit_r3")
PROTOCOL_PATH = Path("docs/S1_G1_THERMODYNAMIC_LABEL_AUDIT_R3_PROTOCOL.md")

R1_CONFIG_PATH = Path("config/S1_g1_thermodynamic_label_audit_r1.json")
R1_MANIFEST_PATH = Path("config/S1_g1_thermodynamic_label_audit_r1_manifest.tsv")
R1_INPUT_ROOT = Path("inputs/s1/g1_thermodynamic_label_audit_r1")
R1_PREREGISTRATION_COMMIT = "f71dd6b0fca238c386c0203b077ebf426e6b6926"
R1_CONFIG_SHA256 = "76873a782a21fb45cb96f318dee992ea5f9ac25625c066d691806fafd6450eba"
R1_MANIFEST_SHA256 = "7650fe3e3f528c8e12919156ae5f8475cfc8963bcefe14137a648e7cd2859d6c"
R1_INPUT_ROOT_TREE_OID = "39ec363adf9cea8fbd7593669f8e268b326e496c"

R1_FAILURE_ID = "S1-20260806-034"
R1_FAILURE_COMMIT = "df57f9b610d82d75835193f84b4bfbb4ffa5007b"
R1_ARCHIVE_COMMIT = "b0b7db592b3438322289dbd98cf66686c6f557a4"
R1_FAILURE_TREE_OID = "c0b7d1cbdfea594c130f086b29f898102d441383"
R1_ARCHIVE_PATH = Path(
    "failed_runs/runtime_relocation/S1-20260806-034/attempt-df57f9b610d8"
)

R2_IMPLEMENTATION_COMMIT = "d73e2ba6da531d2d00cb9b92a406341e31487e53"
R2_PREREGISTRATION_COMMIT = "329a2005e79d1181e7e2da0770e84d0cc3d43880"
R2_DETACHMENT_COMMIT = "99deacd4910eaeb08bcf850887c54935cc3c676e"
R2_FIRST_MARKER_COMMIT = "314ac53777fd55fbb4c2d252a4c243b9a1e1895f"
R2_FAILED_RUN_COMMIT = "ff26667f881e067f11ae0be088ea3659fea8a61a"
R2_ARCHIVE_COMMIT = "f91a3006cd99fd3d11845da2fc8e9c88f662d951"
R2_STOPPED_HANDOFF_COMMIT = "c0f2bb5eb23c68e73ceb8a00ced7c614c148d68a"
R2_CONFIG_PATH = Path("config/S1_g1_thermodynamic_label_audit_r2.json")
R2_MANIFEST_PATH = Path("config/S1_g1_thermodynamic_label_audit_r2_manifest.tsv")
R2_INPUT_ROOT = Path("inputs/s1/g1_thermodynamic_label_audit_r2")
R2_CONFIG_BLOB_OID = "71d7d485ace95be49cc00786c588f3b23efe7c6c"
R2_MANIFEST_BLOB_OID = "c65519559a4b782cd9e60009d249a6d544a095d0"
R2_INPUT_ROOT_TREE_OID = "8c1e077499cd57148db22750cb61640980a15cfa"
R2_CONFIG_SHA256 = "c38ffa3f7014b41015ceb25e6d67eaf8643b3e47d126d84c39c342bbf26ab658"
R2_MANIFEST_SHA256 = "3c5ff41cb2642fccf043dec92a006712719cfaa793480f8cb41f735171f56af1"
R2_FAILED_ID = "S1-20260806-041"
R2_FAILED_LOGICAL_ID = "S1-20260806-034"
R2_FAILED_ARCHIVE_PATH = Path(
    "failed_runs/runtime_relocation/S1-20260806-041/attempt-ff26667f881e"
)
R2_FAILED_TREE_OID = "ce89b513a01964dbd34e24d6f570700bf478210a"
R2_ATTEMPT_LEDGER_ROOT = Path(
    "orchestration/s1/g1_thermodynamic_label_audit_r2_20260806/attempts"
)
R2_DETACHMENT_PATH = Path(
    "orchestration/s1/g1_thermodynamic_label_audit_r2_20260806/detachment.json"
)
R2_DETACHMENT_BLOB_OID = "a250ab98a1143aed8dfbd5e662c57ceb87a6dc1c"
R2_DETACHMENT_SHA256 = "2eb92cfe9ff3a5efe636995f1e46695d6852ce517e112e08d50be16873c06cbc"
R2_FIRST_MARKER_PATH = R2_ATTEMPT_LEDGER_ROOT / f"{R2_FAILED_ID}.json"
R2_FIRST_MARKER_BLOB_OID = "923a0da3e58dea0227314c60f7ed44f5e6780f3d"
R2_FIRST_MARKER_SHA256 = "392b77a417ce2eb2e5070c3b26c205b7cbd4a45ed325095cd243566a4d8946b1"
R2_ANALYSIS_ROOT = Path("analysis/s1/g1_thermodynamic_label_audit_r2_20260806")
R2_COMPLETION_PATH = Path(
    "orchestration/s1/g1_thermodynamic_label_audit_r2_20260806/"
    "supervisor_completion.json"
)

R3_AUDIT_IDS = tuple(f"S1-20260807-{value:03d}" for value in range(1, 41))
R1_HISTORICAL_ACCEPTED_IDS = (
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
R1_REUSED_AUDIT_IDS: tuple[str, ...] = ()
PILOT_IDS = R3_AUDIT_IDS[:12]
EXECUTION_ORDER = R3_AUDIT_IDS

_LOGICAL_ID_SUFFIXES = (
    34,
    40,
    24,
    36,
    31,
    39,
    21,
    35,
    27,
    37,
    28,
    38,
    *range(1, 21),
    22,
    23,
    25,
    26,
    29,
    30,
    32,
    33,
)
LOGICAL_IDS_FOR_R3 = tuple(
    f"S1-20260806-{value:03d}" for value in _LOGICAL_ID_SUFFIXES
)
require(
    len(R3_AUDIT_IDS) == len(LOGICAL_IDS_FOR_R3),
    "R3/logical ID mapping lengths differ",
)
NEW_TO_LOGICAL = dict(zip(R3_AUDIT_IDS, LOGICAL_IDS_FOR_R3))
LOGICAL_TO_NEW_ID = {logical: new for new, logical in NEW_TO_LOGICAL.items()}
LOGICAL_TO_EFFECTIVE_ID = {
    logical: LOGICAL_TO_NEW_ID[logical]
    for logical in R1_AUDIT_IDS
}

# R3 keeps the exact R1 TSV schema so the scientific parser can reuse the same
# row contract.  Logical-slot and bridge provenance live in the JSON config.
R3_MANIFEST_FIELDS = MANIFEST_FIELDS

SUPERVISOR_STATE_DIRECTORY = (
    "/home/shenwei01/.local/state/m_ofdft/"
    "g1_thermodynamic_label_audit_r3_20260807"
)
ATTEMPT_LEDGER_ROOT = Path(
    "orchestration/s1/g1_thermodynamic_label_audit_r3_20260807/attempts"
)
DETACHMENT_ATTESTATION_PATH = Path(
    "orchestration/s1/g1_thermodynamic_label_audit_r3_20260807/detachment.json"
)
SUPERVISOR_COMPLETION_PATH = Path(
    "orchestration/s1/g1_thermodynamic_label_audit_r3_20260807/"
    "supervisor_completion.json"
)
BARRIER_FAILURE_ROOT = Path(
    "orchestration/s1/g1_thermodynamic_label_audit_r3_20260807/barrier_failures"
)
POST_TERMINAL_DOCUMENTATION_PATHS = ("docs/M_OFDFT_项目进度与交接.md",)
FROZEN_AMBIENT_ENVIRONMENT_KEYS = (
    "HOME",
    "LC_ALL",
    "LOGNAME",
    "PATH",
    "PYTHONHASHSEED",
    "PYTHONIOENCODING",
    "PYTHONNOUSERSITE",
    "PYTHONUTF8",
    "TZ",
    "USER",
)
FROZEN_AMBIENT_ENVIRONMENT_VALUES = {
    "HOME": "/home/shenwei01",
    "LC_ALL": "C",
    "LOGNAME": "shenwei01",
    "PATH": "/usr/bin:/bin",
    "PYTHONHASHSEED": "0",
    "PYTHONIOENCODING": "UTF-8",
    "PYTHONNOUSERSITE": "1",
    "PYTHONUTF8": "1",
    "TZ": "UTC",
    "USER": "shenwei01",
}
FROZEN_AMBIENT_ENVIRONMENT_SHA256 = sha256_bytes(
    canonical_json_bytes(FROZEN_AMBIENT_ENVIRONMENT_VALUES)
)
RUNNER_BINDING_ENVIRONMENT_KEYS = (
    "M_OFDFT_G1_R3_SUPERVISOR_STATE_DIRECTORY",
    "M_OFDFT_G1_R3_SUPERVISOR_PID",
    "M_OFDFT_G1_R3_SUPERVISOR_START_TIME_TICKS",
    "M_OFDFT_G1_R3_BOOT_ID",
    "M_OFDFT_G1_R3_LAUNCH_SHA256",
    "M_OFDFT_G1_R3_GO_SHA256",
)
SEALED_EXECUTION_INPUT_MODE = "linux_memfd_sealed_v1"
SEALED_EXECUTION_INPUT_FDS = {
    "runner": 200,
    "manifest": 201,
    "config": 202,
}
SEALED_EXECUTION_INPUT_PROC_PATHS = {
    name: f"/proc/self/fd/{descriptor}"
    for name, descriptor in SEALED_EXECUTION_INPUT_FDS.items()
}
SEALED_EXECUTION_INPUT_SEAL_NAMES = (
    "F_SEAL_SEAL",
    "F_SEAL_SHRINK",
    "F_SEAL_GROW",
    "F_SEAL_WRITE",
)
SEALED_EXECUTION_INPUT_SEAL_MASK = 15
GO_PAYLOAD_REQUIRED_KEYS = (
    "schema_version",
    "protocol_revision",
    "status",
    "launch_sha256",
    "boot_id",
    "supervisor_pid",
    "supervisor_start_time_ticks",
    "attestation_path",
    "attestation_sha256",
    "git_head",
    "registered_files",
    "sealed_execution_inputs_sha256",
    "created_utc",
)
SUPERVISOR_COMPLETION_REQUIRED_KEYS = (
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
)
BARRIER_FAILURE_REQUIRED_KEYS = (
    "schema_version",
    "protocol_revision",
    "status",
    "created_utc",
    "barrier_name",
    "experiment_id",
    "logical_experiment_id",
    "command_argv",
    "exit_code",
    "config_path",
    "config_sha256",
    "manifest_path",
    "manifest_sha256",
    "git_head_before_failure",
    "supervisor_state_directory",
    "supervisor_launch_path",
    "supervisor_launch_sha256",
    "retry_policy",
)
ATTEMPT_MARKER_REQUIRED_KEYS = (
    "schema_version",
    "protocol_revision",
    "experiment_id",
    "logical_experiment_id",
    "status",
    "retry_policy",
    "created_utc",
    "config_path",
    "config_sha256",
    "manifest_path",
    "manifest_sha256",
    "git_head_before_attempt",
    "supervisor_state_directory",
    "supervisor_launch_path",
    "supervisor_launch_sha256",
    "supervisor_pid",
    "supervisor_start_time_ticks",
    "boot_id",
    "supervisor_go_path",
    "supervisor_go_sha256",
    "go_git_head",
)

EXPECTED_REUSED_RUNS = {
    "S1-20260806-024": {
        "introduction_commit": "e90362784963a2f27cff6a79dfcaad3da1e7a90c",
        "tree_oid": "91954f7b039214f492795e41cdebff5d199a9e3a",
    },
    "S1-20260806-036": {
        "introduction_commit": "911141ce8bafc60d203dd682da5e48c4ffa4d342",
        "tree_oid": "6a9c3d040bcfb587c397b2ae604aab0a078b1a75",
    },
    "S1-20260806-031": {
        "introduction_commit": "a95b439da6afac4f92ec285393167d4787068d3a",
        "tree_oid": "115d879d54840d1a8767b96f80d69ec58db7eccf",
    },
    "S1-20260806-039": {
        "introduction_commit": "59c60f49c27e65de16966198b0f065ac3427adba",
        "tree_oid": "07f8cae39a9d5e45ce469771b7f5d1c58f797c4b",
    },
    "S1-20260806-021": {
        "introduction_commit": "0a5b5c712f4fbe08fe3f236b0773c00049dc8bb7",
        "tree_oid": "7ab190b15eeee0b3125e354717ae18b051c99aea",
    },
    "S1-20260806-035": {
        "introduction_commit": "9b36427da40c38951e1d2337831ae4f1c4b87989",
        "tree_oid": "e72ac5ad2219a793b6523caaf2fad3ece581e5ab",
    },
    "S1-20260806-027": {
        "introduction_commit": "f4f0952ec60cee666f921bac66f13f2f639ad704",
        "tree_oid": "c62f3090a0a8168e24b446f68efbd795295a7576",
    },
    "S1-20260806-037": {
        "introduction_commit": "8d683982fb650da2a8faddf970c1ed958305f071",
        "tree_oid": "50af53692721a04f047834ce0e87afc48e8c87a3",
    },
    "S1-20260806-028": {
        "introduction_commit": "fa39a13590a8233cd59e53d93eecae67e1e3d0b2",
        "tree_oid": "0756f9b604681f908fb6493e4882b3d8835710e9",
    },
    "S1-20260806-038": {
        "introduction_commit": "9096ca317916b496564b9543c84fbe567f3e1d69",
        "tree_oid": "3249fdc52df32f97813722114adc6c6e8d677eef",
    },
}

R3_IMPLEMENTATION_PATHS = (
    str(PROTOCOL_PATH),
    "scripts/parse_s1_g1_thermodynamic_labels_r3.py",
    "scripts/generate_s1_g1_thermodynamic_label_audit_r3.py",
    "scripts/validate_s1_g1_thermodynamic_label_audit_r3.py",
    "scripts/analyze_s1_g1_thermodynamic_label_audit_r3.py",
    "scripts/run_s1_g1_thermodynamic_label_audit_r3.sh",
    "scripts/launch_s1_g1_thermodynamic_label_audit_r3.py",
    "tests/unit/test_s1_g1_thermodynamic_label_audit_r3_generator.py",
    "tests/unit/test_s1_g1_thermodynamic_label_audit_r3_parser.py",
    "tests/unit/test_s1_g1_thermodynamic_label_audit_r3_validator.py",
    "tests/unit/test_s1_g1_thermodynamic_label_audit_r3_launcher.py",
    "tests/unit/test_s1_g1_thermodynamic_label_audit_r3_runner.py",
    "tests/unit/test_s1_g1_thermodynamic_label_audit_r3_analysis.py",
)
R3_EXECUTABLE_IMPLEMENTATION_PATHS = frozenset(
    {"scripts/run_s1_g1_thermodynamic_label_audit_r3.sh"}
)

_HEX40 = re.compile(r"[0-9a-f]{40}\Z")


def _git(project_root: Path, *arguments: str, text: bool = True) -> str | bytes:
    output = subprocess.check_output(
        ["git", "-C", str(project_root), *arguments], text=text
    )
    return output.strip() if text else output


def _project_root(path: Path) -> Path:
    root = path.resolve()
    git_root = Path(str(_git(root, "rev-parse", "--show-toplevel"))).resolve()
    require(root == git_root, "project root differs from Git top level")
    return root


def _is_ancestor(project_root: Path, ancestor: str, descendant: str = "HEAD") -> bool:
    return (
        subprocess.run(
            [
                "git",
                "-C",
                str(project_root),
                "merge-base",
                "--is-ancestor",
                ancestor,
                descendant,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )


def _git_entry(
    project_root: Path, commit: str, relative: str
) -> tuple[str, str, str]:
    raw = subprocess.check_output(
        ["git", "-C", str(project_root), "ls-tree", "-z", commit, "--", relative]
    )
    records = [record for record in raw.split(b"\0") if record]
    require(len(records) == 1, f"expected one Git entry: {commit}:{relative}")
    metadata, observed_path = records[0].split(b"\t", 1)
    mode, object_type, object_id = metadata.decode("ascii").split()
    require(observed_path.decode("utf-8") == relative, "Git entry path differs")
    return mode, object_type, object_id


def _git_object_id(
    project_root: Path, commit: str, relative: str, expected_type: str
) -> str:
    object_id = str(_git(project_root, "rev-parse", f"{commit}:{relative}"))
    require(_HEX40.fullmatch(object_id) is not None, f"invalid Git OID: {commit}:{relative}")
    require(
        str(_git(project_root, "cat-file", "-t", object_id)) == expected_type,
        f"Git object type differs: {commit}:{relative}",
    )
    return object_id


def _git_blob(project_root: Path, commit: str, relative: str) -> bytes:
    mode, object_type, _ = _git_entry(project_root, commit, relative)
    require(
        object_type == "blob" and mode in {"100644", "100755"},
        f"not a regular Git blob: {commit}:{relative}",
    )
    return bytes(_git(project_root, "show", f"{commit}:{relative}", text=False))


def _file_anchor_at_commit(
    project_root: Path, commit: str, relative: str
) -> dict[str, str]:
    mode, object_type, object_id = _git_entry(project_root, commit, relative)
    require(
        object_type == "blob" and mode in {"100644", "100755"},
        f"anchor is not a regular Git blob: {commit}:{relative}",
    )
    return {
        "path": relative,
        "sha256": sha256_bytes(_git_blob(project_root, commit, relative)),
        "blob_oid": object_id,
        "git_mode": mode,
    }


def _changed_paths(project_root: Path, commit: str) -> tuple[str, ...]:
    output = str(
        _git(
            project_root,
            "-c",
            "core.quotePath=false",
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            commit,
        )
    )
    return tuple(path for path in output.splitlines() if path)


def build_r2_stopped_bridge(project_root: Path) -> dict[str, object]:
    """Bind the immutable, rejected R2 predecessor without reusing its science."""

    project_root = _project_root(project_root)
    adjacent_chain = (
        (R2_IMPLEMENTATION_COMMIT, R2_PREREGISTRATION_COMMIT),
        (R2_PREREGISTRATION_COMMIT, R2_DETACHMENT_COMMIT),
        (R2_DETACHMENT_COMMIT, R2_FIRST_MARKER_COMMIT),
        (R2_FIRST_MARKER_COMMIT, R2_FAILED_RUN_COMMIT),
        (R2_FAILED_RUN_COMMIT, R2_ARCHIVE_COMMIT),
        (R2_ARCHIVE_COMMIT, R2_STOPPED_HANDOFF_COMMIT),
    )
    for parent, child in adjacent_chain:
        require(
            str(_git(project_root, "rev-parse", f"{child}^")) == parent,
            f"R2 stopped commit chain is not adjacent: {parent}->{child}",
        )
    require(
        _is_ancestor(project_root, R2_STOPPED_HANDOFF_COMMIT, "HEAD"),
        "R2 stopped handoff is not an ancestor of HEAD",
    )
    require(
        _git_object_id(
            project_root, R2_PREREGISTRATION_COMMIT, R2_CONFIG_PATH.as_posix(), "blob"
        )
        == R2_CONFIG_BLOB_OID
        == _git_object_id(project_root, "HEAD", R2_CONFIG_PATH.as_posix(), "blob"),
        "R2 config blob changed",
    )
    require(
        _git_object_id(
            project_root, R2_PREREGISTRATION_COMMIT, R2_MANIFEST_PATH.as_posix(), "blob"
        )
        == R2_MANIFEST_BLOB_OID
        == _git_object_id(project_root, "HEAD", R2_MANIFEST_PATH.as_posix(), "blob"),
        "R2 manifest blob changed",
    )
    require(
        _git_object_id(
            project_root, R2_PREREGISTRATION_COMMIT, R2_INPUT_ROOT.as_posix(), "tree"
        )
        == R2_INPUT_ROOT_TREE_OID
        == _git_object_id(project_root, "HEAD", R2_INPUT_ROOT.as_posix(), "tree"),
        "R2 input tree changed",
    )
    require(
        sha256_bytes(
            _git_blob(project_root, R2_PREREGISTRATION_COMMIT, R2_CONFIG_PATH.as_posix())
        )
        == R2_CONFIG_SHA256,
        "R2 config SHA-256 changed",
    )
    require(
        sha256_bytes(
            _git_blob(project_root, R2_PREREGISTRATION_COMMIT, R2_MANIFEST_PATH.as_posix())
        )
        == R2_MANIFEST_SHA256,
        "R2 manifest SHA-256 changed",
    )
    for path, introduction, expected_oid, expected_sha256, label in (
        (
            R2_DETACHMENT_PATH,
            R2_DETACHMENT_COMMIT,
            R2_DETACHMENT_BLOB_OID,
            R2_DETACHMENT_SHA256,
            "detachment",
        ),
        (
            R2_FIRST_MARKER_PATH,
            R2_FIRST_MARKER_COMMIT,
            R2_FIRST_MARKER_BLOB_OID,
            R2_FIRST_MARKER_SHA256,
            "first marker",
        ),
    ):
        relative = path.as_posix()
        require(
            _changed_paths(project_root, introduction) == (relative,),
            f"R2 {label} commit scope differs",
        )
        require(
            _git_object_id(project_root, introduction, relative, "blob")
            == expected_oid
            == _git_object_id(project_root, "HEAD", relative, "blob"),
            f"R2 {label} blob changed",
        )
        require(
            sha256_bytes(_git_blob(project_root, introduction, relative))
            == expected_sha256,
            f"R2 {label} SHA-256 changed",
        )
    require(
        _git_object_id(
            project_root,
            R2_FAILED_RUN_COMMIT,
            f"runs/{R2_FAILED_ID}",
            "tree",
        )
        == R2_FAILED_TREE_OID
        == _git_object_id(
            project_root,
            R2_ARCHIVE_COMMIT,
            R2_FAILED_ARCHIVE_PATH.as_posix(),
            "tree",
        )
        == _git_object_id(
            project_root, "HEAD", R2_FAILED_ARCHIVE_PATH.as_posix(), "tree"
        ),
        "R2 failed/archive tree changed",
    )
    attempted = tuple(
        str(_git(project_root, "ls-tree", "-r", "--name-only", "HEAD", "--", R2_ATTEMPT_LEDGER_ROOT.as_posix())).splitlines()
    )
    expected_marker = R2_FIRST_MARKER_PATH.as_posix()
    require(attempted == (expected_marker,), "R2 attempt-ledger history differs")
    require(
        not str(_git(project_root, "ls-tree", "-r", "--name-only", "HEAD", "--", f"runs/{R2_FAILED_ID}")),
        "R2 failed run remains active",
    )
    require(
        not str(_git(project_root, "ls-tree", "-r", "--name-only", "HEAD", "--", R2_ANALYSIS_ROOT.as_posix()))
        and not str(_git(project_root, "ls-tree", "-r", "--name-only", "HEAD", "--", R2_COMPLETION_PATH.as_posix())),
        "R2 analysis/completion unexpectedly exists",
    )
    require(
        _changed_paths(project_root, R2_STOPPED_HANDOFF_COMMIT)
        == ("docs/M_OFDFT_项目进度与交接.md",),
        "R2 stopped handoff commit scope differs",
    )
    frozen_r2_paths = (
        R2_CONFIG_PATH.as_posix(),
        R2_MANIFEST_PATH.as_posix(),
        R2_INPUT_ROOT.as_posix(),
        R2_ATTEMPT_LEDGER_ROOT.parent.as_posix(),
        R2_ANALYSIS_ROOT.as_posix(),
        *(f"runs/S1-20260806-{value:03d}" for value in range(41, 71)),
        *(
            f"failed_runs/runtime_relocation/S1-20260806-{value:03d}"
            for value in range(41, 71)
        ),
    )
    require(
        not str(
            _git(
                project_root,
                "diff",
                "--name-only",
                f"{R2_STOPPED_HANDOFF_COMMIT}..HEAD",
                "--",
                *frozen_r2_paths,
            )
        ),
        "R2 execution namespace changed after the stopped handoff",
    )
    classification = _file_anchor_at_commit(
        project_root,
        R2_ARCHIVE_COMMIT,
        f"{R2_FAILED_ARCHIVE_PATH.as_posix()}/thermodynamic_label_failure_classification_r2.json",
    )
    status = _file_anchor_at_commit(
        project_root,
        R2_ARCHIVE_COMMIT,
        f"{R2_FAILED_ARCHIVE_PATH.as_posix()}/thermodynamic_label_status_r2.json",
    )
    return {
        "protocol_revision": "S1-G1-THERMODYNAMIC-LABEL-AUDIT-R2",
        "terminal_status": "stopped",
        "implementation_commit": R2_IMPLEMENTATION_COMMIT,
        "preregistration_commit": R2_PREREGISTRATION_COMMIT,
        "detachment_commit": R2_DETACHMENT_COMMIT,
        "detachment_attestation": {
            "path": R2_DETACHMENT_PATH.as_posix(),
            "blob_oid": R2_DETACHMENT_BLOB_OID,
            "sha256": R2_DETACHMENT_SHA256,
        },
        "first_marker_commit": R2_FIRST_MARKER_COMMIT,
        "first_attempt_marker": {
            "path": R2_FIRST_MARKER_PATH.as_posix(),
            "blob_oid": R2_FIRST_MARKER_BLOB_OID,
            "sha256": R2_FIRST_MARKER_SHA256,
        },
        "failed_run_commit": R2_FAILED_RUN_COMMIT,
        "archive_commit": R2_ARCHIVE_COMMIT,
        "stopped_handoff_commit": R2_STOPPED_HANDOFF_COMMIT,
        "config": {
            "path": R2_CONFIG_PATH.as_posix(),
            "blob_oid": R2_CONFIG_BLOB_OID,
            "sha256": R2_CONFIG_SHA256,
        },
        "manifest": {
            "path": R2_MANIFEST_PATH.as_posix(),
            "blob_oid": R2_MANIFEST_BLOB_OID,
            "sha256": R2_MANIFEST_SHA256,
        },
        "input_root": {
            "path": R2_INPUT_ROOT.as_posix(),
            "tree_oid": R2_INPUT_ROOT_TREE_OID,
        },
        "failed_experiment_id": R2_FAILED_ID,
        "failed_logical_experiment_id": R2_FAILED_LOGICAL_ID,
        "failed_archive_path": R2_FAILED_ARCHIVE_PATH.as_posix(),
        "failed_run_and_archive_tree_oid": R2_FAILED_TREE_OID,
        "attempted_ids_exact": [R2_FAILED_ID],
        "registered_but_unattempted_ids_exact": [
            f"S1-20260806-{value:03d}" for value in range(42, 71)
        ],
        "analysis_present": False,
        "supervisor_completion_present": False,
        "workflow_exit_code": 0,
        "parser_exit_code": 1,
        "runner_exit_code": 97,
        "failure_mechanism": "production_parser_registration_contract",
        "legacy_machine_failure_category": "thermodynamic_identity",
        "failure_classification": classification,
        "authoritative_status": status,
        "accepted_scientific_denominator_contribution": 0,
        "r3_reuse_forbidden": True,
    }


def _require_clean(project_root: Path) -> None:
    status = str(
        _git(project_root, "status", "--porcelain=v1", "--untracked-files=all")
    )
    require(status == "", "formal R3 preregistration requires a clean worktree")


def _read_manifest_bytes(data: bytes) -> list[dict[str, str]]:
    text = data.decode("utf-8", errors="strict")
    with io.StringIO(text, newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        require(
            tuple(reader.fieldnames or ()) == MANIFEST_FIELDS,
            "R1 manifest header differs from the frozen schema",
        )
        rows = list(reader)
    require(
        len(rows) == 40
        and tuple(row["experiment_id"] for row in rows) == R1_AUDIT_IDS,
        "R1 manifest logical ID matrix differs",
    )
    require(
        all(None not in row and all(value is not None for value in row.values()) for row in rows),
        "R1 manifest contains a malformed row",
    )
    return rows


def load_r1_registration(
    project_root: Path,
) -> tuple[dict[str, object], list[dict[str, str]]]:
    """Load and verify the exact R1 preregistration blobs."""

    project_root = _project_root(project_root)
    require(
        str(_git(project_root, "rev-parse", R1_PREREGISTRATION_COMMIT))
        == R1_PREREGISTRATION_COMMIT,
        "R1 preregistration commit differs",
    )
    require(
        _is_ancestor(project_root, R1_PREREGISTRATION_COMMIT),
        "R1 preregistration is not an ancestor of HEAD",
    )
    config_bytes = _git_blob(
        project_root, R1_PREREGISTRATION_COMMIT, R1_CONFIG_PATH.as_posix()
    )
    manifest_bytes = _git_blob(
        project_root, R1_PREREGISTRATION_COMMIT, R1_MANIFEST_PATH.as_posix()
    )
    require(sha256_bytes(config_bytes) == R1_CONFIG_SHA256, "R1 config SHA-256 differs")
    require(
        sha256_bytes(manifest_bytes) == R1_MANIFEST_SHA256,
        "R1 manifest SHA-256 differs",
    )
    require(
        _git_object_id(
            project_root,
            R1_PREREGISTRATION_COMMIT,
            R1_INPUT_ROOT.as_posix(),
            "tree",
        )
        == R1_INPUT_ROOT_TREE_OID,
        "R1 registered input-root tree differs",
    )
    expected_preregistration_paths = {
        R1_CONFIG_PATH.as_posix(),
        R1_MANIFEST_PATH.as_posix(),
        *(
            f"{R1_INPUT_ROOT.as_posix()}/{experiment_id}/{basename}"
            for experiment_id in R1_AUDIT_IDS
            for basename in ("INPUT", "STRU", "KPT", "metadata.json")
        ),
    }
    require(
        set(_changed_paths(project_root, R1_PREREGISTRATION_COMMIT))
        == expected_preregistration_paths,
        "R1 preregistration commit scope differs",
    )
    for relative, frozen in (
        (R1_CONFIG_PATH, config_bytes),
        (R1_MANIFEST_PATH, manifest_bytes),
    ):
        path = project_root / relative
        require(path.is_file() and not path.is_symlink(), f"missing R1 file: {relative}")
        require(path.read_bytes() == frozen, f"working R1 file differs: {relative}")
        require(
            _git_blob(project_root, "HEAD", relative.as_posix()) == frozen,
            f"HEAD R1 file differs: {relative}",
        )

    config = json.loads(config_bytes)
    require(isinstance(config, dict), "R1 config is not an object")
    require(
        config.get("protocol_revision") == "S1-G1-THERMODYNAMIC-LABEL-AUDIT-R1"
        and config.get("status") == "preregistered",
        "R1 config identity differs",
    )
    require(
        config.get("manifest", {}).get("sha256") == R1_MANIFEST_SHA256,
        "R1 config does not bind its manifest",
    )
    rows = _read_manifest_bytes(manifest_bytes)
    return config, rows


def _tree_file_anchors(
    project_root: Path, commit: str, tree_relative: str
) -> dict[str, dict[str, str]]:
    raw = subprocess.check_output(
        [
            "git",
            "-C",
            str(project_root),
            "ls-tree",
            "-r",
            "-z",
            commit,
            "--",
            tree_relative,
        ]
    )
    anchors: dict[str, dict[str, str]] = {}
    prefix = tree_relative.rstrip("/") + "/"
    for record in sorted((item for item in raw.split(b"\0") if item), key=lambda x: x.split(b"\t", 1)[1]):
        metadata, raw_path = record.split(b"\t", 1)
        mode, object_type, object_id = metadata.decode("ascii").split()
        full_path = raw_path.decode("utf-8")
        require(full_path.startswith(prefix), "tree artifact path escaped its root")
        relative = full_path[len(prefix) :]
        require(
            object_type == "blob" and mode in {"100644", "100755"},
            f"tree artifact is not a regular blob: {full_path}",
        )
        data = bytes(_git(project_root, "show", f"{commit}:{full_path}", text=False))
        anchors[relative] = {
            "sha256": sha256_bytes(data),
            "blob_oid": object_id,
            "git_mode": mode,
        }
    return anchors


def _accepted_run_anchor(
    project_root: Path,
    experiment_id: str,
    row: dict[str, str],
) -> dict[str, object]:
    expected = EXPECTED_REUSED_RUNS[experiment_id]
    introduction = expected["introduction_commit"]
    run_relative = f"runs/{experiment_id}"
    tree_oid = _git_object_id(project_root, introduction, run_relative, "tree")
    require(tree_oid == expected["tree_oid"], f"R1 accepted tree differs: {experiment_id}")
    require(
        _git_object_id(project_root, "HEAD", run_relative, "tree") == tree_oid,
        f"R1 accepted tree changed after introduction: {experiment_id}",
    )
    require(
        _is_ancestor(project_root, R1_PREREGISTRATION_COMMIT, introduction)
        and _is_ancestor(project_root, introduction),
        f"R1 accepted introduction ancestry differs: {experiment_id}",
    )
    changed = _changed_paths(project_root, introduction)
    prefix = run_relative + "/"
    require(
        changed and all(path.startswith(prefix) for path in changed),
        f"R1 accepted introduction is not exact-run scoped: {experiment_id}",
    )

    evidence_name = "g1_thermodynamic_label_audit_r1.json"
    status_name = "thermodynamic_label_status.json"
    evidence = json.loads(
        _git_blob(project_root, introduction, f"{run_relative}/{evidence_name}")
    )
    status = json.loads(
        _git_blob(project_root, introduction, f"{run_relative}/{status_name}")
    )
    result = json.loads(
        _git_blob(project_root, introduction, f"{run_relative}/result.json")
    )
    runtime_audit = json.loads(
        _git_blob(
            project_root,
            introduction,
            f"{run_relative}/mpi_runtime_audit/audit.json",
        )
    )
    require(
        evidence.get("status") == "accepted"
        and evidence.get("experiment_id") == experiment_id,
        f"R1 accepted evidence identity differs: {experiment_id}",
    )
    require(
        status.get("status") == "accepted"
        and status.get("authoritative_for_r1") is True,
        f"R1 authoritative status differs: {experiment_id}",
    )
    require(result.get("converged") is True, f"R1 solver result is not converged: {experiment_id}")
    require(
        runtime_audit.get("status") == "accepted"
        and runtime_audit.get("failure_reasons") == [],
        f"R1 runtime audit is not accepted: {experiment_id}",
    )

    artifact_names = (
        evidence_name,
        status_name,
        "thermodynamic_labels.json",
        "result.json",
        "mpi_runtime_audit/audit.json",
        "mpi_runtime_audit/counterpart_audit.json",
        f"OUT.{row['suffix']}/chg.cube",
        f"OUT.{row['suffix']}/pot.cube",
    )
    artifacts = {
        name: _file_anchor_at_commit(project_root, introduction, f"{run_relative}/{name}")
        for name in artifact_names
    }
    r1_input_relative = f"{R1_INPUT_ROOT.as_posix()}/{experiment_id}"
    return {
        "introduction_commit": introduction,
        "tree_oid": tree_oid,
        "manifest_row_sha256": sha256_bytes(canonical_json_bytes(row)),
        "input_tree_oid": _git_object_id(
            project_root,
            R1_PREREGISTRATION_COMMIT,
            r1_input_relative,
            "tree",
        ),
        "artifacts": artifacts,
    }


def _failure_anchor(project_root: Path) -> dict[str, object]:
    require(
        str(_git(project_root, "rev-parse", f"{R1_ARCHIVE_COMMIT}^"))
        == R1_FAILURE_COMMIT,
        "R1 archive commit is not adjacent to the failed-run commit",
    )
    failure_tree = _git_object_id(
        project_root, R1_FAILURE_COMMIT, f"runs/{R1_FAILURE_ID}", "tree"
    )
    archive_tree = _git_object_id(
        project_root, R1_ARCHIVE_COMMIT, R1_ARCHIVE_PATH.as_posix(), "tree"
    )
    head_tree = _git_object_id(
        project_root, "HEAD", R1_ARCHIVE_PATH.as_posix(), "tree"
    )
    require(
        failure_tree == archive_tree == head_tree == R1_FAILURE_TREE_OID,
        "R1 failed/archive tree identity differs",
    )
    require(
        subprocess.run(
            [
                "git",
                "-C",
                str(project_root),
                "cat-file",
                "-e",
                f"HEAD:runs/{R1_FAILURE_ID}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        != 0,
        "R1 failed run remains active after archive",
    )
    artifacts = _tree_file_anchors(
        project_root, R1_ARCHIVE_COMMIT, R1_ARCHIVE_PATH.as_posix()
    )
    require(len(artifacts) == 71, "R1 failed archive artifact count differs")
    classification = json.loads(
        _git_blob(
            project_root,
            R1_ARCHIVE_COMMIT,
            f"{R1_ARCHIVE_PATH.as_posix()}/thermodynamic_label_failure_classification.json",
        )
    )
    require(
        classification.get("status") == "indeterminate"
        and classification.get("failure_class")
        == "workflow_or_runtime_capability_failure"
        and classification.get("retry_policy")
        == "new_protocol_revision_and_new_experiment_ids_only",
        "R1-034 failure classification differs",
    )
    return {
        "experiment_id": R1_FAILURE_ID,
        "failure_commit": R1_FAILURE_COMMIT,
        "archive_commit": R1_ARCHIVE_COMMIT,
        "archive_path": R1_ARCHIVE_PATH.as_posix(),
        "failure_tree_oid": failure_tree,
        "archive_tree_oid": archive_tree,
        "classification": {
            "status": "indeterminate",
            "failure_class": "workflow_or_runtime_capability_failure",
            "retry_policy": "new_protocol_revision_and_new_experiment_ids_only",
            "accepted_scientific_denominator_contribution": 0,
        },
        "artifacts": artifacts,
    }


def build_r1_bridge(
    project_root: Path,
    r1_config: dict[str, object] | None = None,
    r1_rows: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    """Rebuild the complete R1-to-R3 cryptographic bridge."""

    project_root = _project_root(project_root)
    if r1_config is None or r1_rows is None:
        r1_config, r1_rows = load_r1_registration(project_root)
    rows_by_id = {row["experiment_id"]: row for row in r1_rows}
    require(set(rows_by_id) == set(R1_AUDIT_IDS), "R1 row map differs")
    historical_runs = {
        experiment_id: _accepted_run_anchor(
            project_root, experiment_id, rows_by_id[experiment_id]
        )
        for experiment_id in R1_HISTORICAL_ACCEPTED_IDS
    }
    introductions = [
        str(historical_runs[experiment_id]["introduction_commit"])
        for experiment_id in R1_HISTORICAL_ACCEPTED_IDS
    ]
    require(
        all(
            earlier != later and _is_ancestor(project_root, earlier, later)
            for earlier, later in zip(introductions, introductions[1:])
        ),
        "R1 historical accepted-run introduction ancestry differs",
    )
    require(
        _is_ancestor(project_root, introductions[-1], R1_FAILURE_COMMIT)
        and _is_ancestor(project_root, R1_ARCHIVE_COMMIT),
        "R1 failed/archive ancestry differs from historical runs or HEAD",
    )
    return {
        "protocol_revision": r1_config["protocol_revision"],
        "preregistration_commit": R1_PREREGISTRATION_COMMIT,
        "config": _file_anchor_at_commit(
            project_root, R1_PREREGISTRATION_COMMIT, R1_CONFIG_PATH.as_posix()
        ),
        "manifest": {
            **_file_anchor_at_commit(
                project_root,
                R1_PREREGISTRATION_COMMIT,
                R1_MANIFEST_PATH.as_posix(),
            ),
            "row_count": 40,
        },
        "input_root": {
            "path": R1_INPUT_ROOT.as_posix(),
            "tree_oid": R1_INPUT_ROOT_TREE_OID,
            "file_count": 160,
        },
        "historical_accepted_logical_ids": list(R1_HISTORICAL_ACCEPTED_IDS),
        "historical_accepted_runs": historical_runs,
        "reused_logical_ids": [],
        "reused_runs": {},
        "historical_accepted_scientific_denominator_contribution": 0,
        "historical_runtime_environment_replay_required": False,
        "failed_run": _failure_anchor(project_root),
        "r1_active_run_for_failed_id_forbidden": True,
        "r1_failed_attempt_contributes_to_acceptance": False,
    }


def _r1_input_tree_oid(project_root: Path, logical_id: str) -> str:
    return _git_object_id(
        project_root,
        R1_PREREGISTRATION_COMMIT,
        f"{R1_INPUT_ROOT.as_posix()}/{logical_id}",
        "tree",
    )


def _effective_reference(value: str) -> str:
    return LOGICAL_TO_EFFECTIVE_ID.get(value, value)


def build_plan(
    project_root: Path,
    r1_config: dict[str, object] | None = None,
    r1_rows: list[dict[str, str]] | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Build the 40-slot logical matrix and the 40-row R3 run matrix."""

    project_root = _project_root(project_root)
    if r1_config is None or r1_rows is None:
        r1_config, r1_rows = load_r1_registration(project_root)
    r1_run_matrix = r1_config.get("run_matrix")
    require(isinstance(r1_run_matrix, list) and len(r1_run_matrix) == 40, "R1 run matrix differs")
    config_by_id = {
        str(row.get("experiment_id")): row
        for row in r1_run_matrix
        if isinstance(row, dict)
    }
    manifest_by_id = {row["experiment_id"]: row for row in r1_rows}
    require(
        set(config_by_id) == set(manifest_by_id) == set(R1_AUDIT_IDS),
        "R1 config/manifest logical IDs differ",
    )

    new_index = {experiment_id: index for index, experiment_id in enumerate(EXECUTION_ORDER, 1)}
    logical_matrix: list[dict[str, object]] = []
    for logical_id in R1_AUDIT_IDS:
        physical_id = LOGICAL_TO_EFFECTIVE_ID[logical_id]
        r1_row = config_by_id[logical_id]
        logical_matrix.append(
            {
                "logical_experiment_id": logical_id,
                "physical_experiment_id": physical_id,
                "effective_experiment_id": physical_id,
                "source_kind": "r3_executed",
                "evidence_origin": "r3_executed",
                "r1_execution_index": r1_row["execution_index"],
                "r1_execution_phase": r1_row["execution_phase"],
                "r3_execution_index": new_index[physical_id],
                "r3_execution_phase": "P1" if physical_id in PILOT_IDS else "P2",
                "input_directory": f"{INPUT_ROOT.as_posix()}/{physical_id}",
                "r1_input_directory": f"{R1_INPUT_ROOT.as_posix()}/{logical_id}",
                "r1_input_tree_oid": _r1_input_tree_oid(project_root, logical_id),
                "r1_manifest_row_sha256": sha256_bytes(
                    canonical_json_bytes(manifest_by_id[logical_id])
                ),
                "logical_reference_experiment_id": r1_row["reference_experiment_id"],
                "effective_reference_experiment_id": _effective_reference(
                    str(r1_row["reference_experiment_id"])
                ),
                "logical_common_quarter_partner_id": r1_row["common_quarter_partner_id"],
                "effective_common_quarter_partner_id": _effective_reference(
                    str(r1_row["common_quarter_partner_id"])
                ),
                **{
                    key: deepcopy(value)
                    for key, value in r1_row.items()
                    if key
                    not in {
                        "experiment_id",
                        "execution_index",
                        "execution_phase",
                        "reference_experiment_id",
                        "common_quarter_partner_id",
                    }
                },
            }
        )

    new_matrix: list[dict[str, object]] = []
    for execution_index, new_id in enumerate(EXECUTION_ORDER, 1):
        logical_id = NEW_TO_LOGICAL[new_id]
        r1_row = config_by_id[logical_id]
        old_suffix = str(r1_row["suffix"])
        require(old_suffix.startswith("g1tlr1_"), f"R1 suffix differs: {logical_id}")
        suffix = "g1tlr3_" + old_suffix.removeprefix("g1tlr1_")
        new_matrix.append(
            {
                "execution_index": execution_index,
                "execution_phase": "P1" if new_id in PILOT_IDS else "P2",
                "experiment_id": new_id,
                "logical_experiment_id": logical_id,
                "input_directory": f"{INPUT_ROOT.as_posix()}/{new_id}",
                "r1_input_directory": f"{R1_INPUT_ROOT.as_posix()}/{logical_id}",
                "r1_input_tree_oid": _r1_input_tree_oid(project_root, logical_id),
                "r1_manifest_row_sha256": sha256_bytes(
                    canonical_json_bytes(manifest_by_id[logical_id])
                ),
                "reference_experiment_id": r1_row["reference_experiment_id"],
                "effective_reference_experiment_id": _effective_reference(
                    str(r1_row["reference_experiment_id"])
                ),
                "common_quarter_partner_id": r1_row["common_quarter_partner_id"],
                "effective_common_quarter_partner_id": _effective_reference(
                    str(r1_row["common_quarter_partner_id"])
                ),
                "suffix": suffix,
                **{
                    key: deepcopy(value)
                    for key, value in r1_row.items()
                    if key
                    not in {
                        "experiment_id",
                        "execution_index",
                        "execution_phase",
                        "reference_experiment_id",
                        "common_quarter_partner_id",
                        "suffix",
                    }
                },
            }
        )

    require(len(logical_matrix) == 40 and len(new_matrix) == 40, "R3 matrix count differs")
    require(
        {row["physical_experiment_id"] for row in logical_matrix}
        == set(R3_AUDIT_IDS),
        "R3 physical ID partition differs",
    )
    return logical_matrix, new_matrix


def _derive_input_blob(
    source: bytes, old_suffix: str, new_suffix: str
) -> bytes:
    text = source.decode("utf-8", errors="strict")
    old_line = f"suffix {old_suffix}"
    new_line = f"suffix {new_suffix}"
    require(text.splitlines().count(old_line) == 1, "R1 INPUT suffix line is not unique")
    derived = text.replace(old_line, new_line, 1).encode("utf-8")
    old_parsed = parse_input_text(source)
    new_parsed = parse_input_text(derived)
    require(old_parsed.get("suffix") == (old_suffix,), "R1 parsed suffix differs")
    require(new_parsed.get("suffix") == (new_suffix,), "R3 parsed suffix differs")
    old_without_suffix = {key: value for key, value in old_parsed.items() if key != "suffix"}
    new_without_suffix = {key: value for key, value in new_parsed.items() if key != "suffix"}
    require(old_without_suffix == new_without_suffix, "R3 INPUT changed beyond suffix")
    return derived


def _derive_metadata_blob(
    source: bytes, logical_id: str, new_id: str, new_suffix: str
) -> bytes:
    payload = json.loads(source)
    require(isinstance(payload, dict), "R1 input metadata is not an object")
    old_suffix = payload.get("suffix")
    require(
        payload.get("protocol_revision") == "S1-G1-THERMODYNAMIC-LABEL-AUDIT-R1"
        and payload.get("experiment_id") == logical_id
        and isinstance(old_suffix, str)
        and old_suffix.startswith("g1tlr1_"),
        f"R1 metadata identity differs: {logical_id}",
    )
    derived = deepcopy(payload)
    derived["protocol_revision"] = PROTOCOL_REVISION
    derived["experiment_id"] = new_id
    derived["suffix"] = new_suffix
    changed = {key for key in set(payload) | set(derived) if payload.get(key) != derived.get(key)}
    require(
        changed == {"protocol_revision", "experiment_id", "suffix"},
        f"R3 metadata changed unexpected keys: {sorted(changed)}",
    )
    return canonical_json_bytes(derived)


def _manifest_bytes(rows: Iterable[dict[str, object]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=MANIFEST_FIELDS,
        delimiter="\t",
        lineterminator="\n",
    )
    writer.writeheader()
    count = 0
    for row in rows:
        require(set(row) == set(MANIFEST_FIELDS), "R3 manifest row key set differs")
        writer.writerow({field: row[field] for field in MANIFEST_FIELDS})
        count += 1
    require(count == len(R3_AUDIT_IDS), "R3 manifest row count differs")
    return buffer.getvalue().encode("utf-8")


def derive_registration_artifacts(
    project_root: Path,
    new_matrix: list[dict[str, object]],
    r1_rows: list[dict[str, str]],
) -> tuple[dict[str, bytes], list[dict[str, object]]]:
    """Derive all 160 input blobs and the exact 40-row manifest payload."""

    project_root = _project_root(project_root)
    r1_by_id = {row["experiment_id"]: row for row in r1_rows}
    artifacts: dict[str, bytes] = {}
    rows: list[dict[str, object]] = []
    for plan in new_matrix:
        new_id = str(plan["experiment_id"])
        logical_id = str(plan["logical_experiment_id"])
        r1_row = r1_by_id[logical_id]
        r1_directory = f"{R1_INPUT_ROOT.as_posix()}/{logical_id}"
        source_blobs = {
            basename: _git_blob(
                project_root,
                R1_PREREGISTRATION_COMMIT,
                f"{r1_directory}/{basename}",
            )
            for basename in ("INPUT", "STRU", "KPT", "metadata.json")
        }
        require(
            sha256_bytes(source_blobs["INPUT"]) == r1_row["input_sha256"]
            and sha256_bytes(source_blobs["STRU"]) == r1_row["stru_sha256"]
            and sha256_bytes(source_blobs["KPT"]) == r1_row["kpt_sha256"]
            and sha256_bytes(source_blobs["metadata.json"]) == r1_row["metadata_sha256"],
            f"R1 input blob hash differs: {logical_id}",
        )
        new_suffix = str(plan["suffix"])
        input_blob = _derive_input_blob(
            source_blobs["INPUT"], r1_row["suffix"], new_suffix
        )
        metadata_blob = _derive_metadata_blob(
            source_blobs["metadata.json"], logical_id, new_id, new_suffix
        )
        derived = {
            "INPUT": input_blob,
            "STRU": source_blobs["STRU"],
            "KPT": source_blobs["KPT"],
            "metadata.json": metadata_blob,
        }
        output_directory = f"{INPUT_ROOT.as_posix()}/{new_id}"
        for basename, data in derived.items():
            artifacts[f"{output_directory}/{basename}"] = data

        row: dict[str, object] = dict(r1_row)
        row.update(
            {
                "execution_index": plan["execution_index"],
                "execution_phase": plan["execution_phase"],
                "experiment_id": new_id,
                "input_directory": output_directory,
                # Partner/reference columns intentionally retain the logical
                # R1 slot IDs; the config records their effective IDs.
                "reference_experiment_id": plan["reference_experiment_id"],
                "common_quarter_partner_id": plan["common_quarter_partner_id"],
                "suffix": new_suffix,
                "input_sha256": sha256_bytes(input_blob),
                "stru_sha256": sha256_bytes(derived["STRU"]),
                "kpt_sha256": sha256_bytes(derived["KPT"]),
                "metadata_sha256": sha256_bytes(metadata_blob),
            }
        )
        require(set(row) == set(MANIFEST_FIELDS), f"R3 manifest schema differs: {new_id}")
        rows.append(row)

    require(tuple(row["experiment_id"] for row in rows) == EXECUTION_ORDER, "R3 manifest order differs")
    require(len(artifacts) == 4 * len(R3_AUDIT_IDS), "R3 input artifact count differs")
    return artifacts, rows


def _implementation_closure(
    project_root: Path,
    r1_config: dict[str, object],
    *,
    formal: bool,
) -> tuple[dict[str, str], dict[str, str]]:
    inherited_hashes = r1_config.get("implementation")
    inherited_modes = r1_config.get("implementation_git_modes")
    require(isinstance(inherited_hashes, dict), "R1 implementation closure is missing")
    require(isinstance(inherited_modes, dict), "R1 implementation modes are missing")
    implementation: dict[str, str] = {}
    modes: dict[str, str] = {}
    for relative in tuple(dict.fromkeys((*inherited_hashes.keys(), *R3_IMPLEMENTATION_PATHS))):
        path = project_root / relative
        require(path.is_file() and not path.is_symlink(), f"missing implementation: {relative}")
        digest = sha256_regular_file(path)
        if relative in inherited_hashes:
            require(digest == inherited_hashes[relative], f"R1 implementation changed: {relative}")
        if formal:
            mode, object_type, _ = _git_entry(project_root, "HEAD", relative)
            require(
                object_type == "blob" and mode in {"100644", "100755"},
                f"implementation is not a regular HEAD blob: {relative}",
            )
            require(
                _git_blob(project_root, "HEAD", relative) == path.read_bytes(),
                f"implementation differs from HEAD: {relative}",
            )
        else:
            mode = "100755" if os.access(path, os.X_OK) else "100644"
        if relative in inherited_modes:
            require(mode == inherited_modes[relative], f"R1 implementation mode changed: {relative}")
        if relative in R3_EXECUTABLE_IMPLEMENTATION_PATHS:
            require(
                mode == "100755" and os.access(path, os.X_OK),
                f"R3 executable implementation mode differs: {relative}",
            )
        implementation[relative] = digest
        modes[relative] = mode
    return implementation, modes


def _map_energy_matrix(
    r1_energy_matrix: dict[str, object],
) -> dict[str, object]:
    result = deepcopy(r1_energy_matrix)
    points = result.get("points")
    require(isinstance(points, list) and len(points) == 42, "R1 energy matrix differs")
    for point in points:
        require(isinstance(point, dict), "R1 energy point differs")
        logical_id = str(point.get("experiment_id"))
        if logical_id in LOGICAL_TO_EFFECTIVE_ID:
            point["logical_experiment_id"] = logical_id
            point["experiment_id"] = LOGICAL_TO_EFFECTIVE_ID[logical_id]
            point["source_kind"] = (
                "r1_reused" if logical_id in R1_REUSED_AUDIT_IDS else "r3_executed"
            )
    result["r1_reused_audit_run_count"] = len(R1_REUSED_AUDIT_IDS)
    result["r3_executed_audit_run_count"] = len(R3_AUDIT_IDS)
    return result


def _map_field_groups(r1_groups: dict[str, object]) -> dict[str, object]:
    result = deepcopy(r1_groups)
    by_smearing = result.get("by_smearing")
    require(isinstance(by_smearing, dict), "R1 field groups differ")
    for level, values in by_smearing.items():
        require(isinstance(values, list), f"R1 field group differs: {level}")
        by_smearing[level] = [LOGICAL_TO_EFFECTIVE_ID[value] for value in values]
    extra = result.get("extra_dense_quarter_ids")
    require(isinstance(extra, list), "R1 extra-dense field group differs")
    result["extra_dense_quarter_ids"] = [LOGICAL_TO_EFFECTIVE_ID[value] for value in extra]
    return result


def _require_preregistration_targets_absent(
    project_root: Path,
    output_config: Path,
    output_manifest: Path,
    input_root: Path,
) -> None:
    """Enforce the single-use namespace only for the mutating write path."""

    for output in (
        project_root / output_config,
        project_root / output_manifest,
        project_root / input_root,
    ):
        require(
            not output.exists() and not output.is_symlink(),
            f"refusing to overwrite R3 output: {output}",
        )
    for experiment_id in R3_AUDIT_IDS:
        for relative in (
            Path("runs") / experiment_id,
            Path("failed_runs/runtime_relocation") / experiment_id,
            ATTEMPT_LEDGER_ROOT / f"{experiment_id}.json",
        ):
            target = project_root / relative
            require(
                not target.exists() and not target.is_symlink(),
                f"R3 execution/attempt path exists before preregistration: {relative}",
            )
    for relative in (
        ATTEMPT_LEDGER_ROOT,
        DETACHMENT_ATTESTATION_PATH,
        SUPERVISOR_COMPLETION_PATH,
        BARRIER_FAILURE_ROOT,
    ):
        target = project_root / relative
        require(
            not target.exists() and not target.is_symlink(),
            f"R3 orchestration path exists before preregistration: {relative}",
        )


def prepare(
    project_root: Path,
    *,
    output_config: Path = CONFIG_PATH,
    output_manifest: Path = MANIFEST_PATH,
    input_root: Path = INPUT_ROOT,
    write: bool = False,
) -> dict[str, object]:
    project_root = _project_root(project_root)
    output_config = Path(output_config)
    output_manifest = Path(output_manifest)
    input_root = Path(input_root)
    if write:
        require(output_config == CONFIG_PATH, "formal R3 config path must be canonical")
        require(output_manifest == MANIFEST_PATH, "formal R3 manifest path must be canonical")
        require(input_root == INPUT_ROOT, "formal R3 input root must be canonical")
        _require_clean(project_root)
        _require_preregistration_targets_absent(
            project_root, output_config, output_manifest, input_root
        )

    r1_config, r1_rows = load_r1_registration(project_root)
    bridge = build_r1_bridge(project_root, r1_config, r1_rows)
    r2_stopped_bridge = build_r2_stopped_bridge(project_root)
    logical_matrix, new_matrix = build_plan(project_root, r1_config, r1_rows)
    artifacts, manifest_rows = derive_registration_artifacts(
        project_root, new_matrix, r1_rows
    )
    encoded_manifest = _manifest_bytes(manifest_rows)
    implementation, modes = _implementation_closure(
        project_root, r1_config, formal=write
    )
    generated_from_commit = str(_git(project_root, "rev-parse", "HEAD"))
    require(_HEX40.fullmatch(generated_from_commit) is not None, "invalid implementation commit")

    config: dict[str, object] = {
        "schema_version": 2,
        "protocol_revision": PROTOCOL_REVISION,
        "status": "preregistered",
        "preregistration_date": "2026-08-07",
        "generated_from_commit": generated_from_commit,
        "scope": "G1 third-smearing / dense-k thermodynamic-label R3 continuation only",
        "registered_experiment_ids": list(R3_AUDIT_IDS),
        "execution_order": list(EXECUTION_ORDER),
        "logical_run_matrix": logical_matrix,
        "new_run_matrix": new_matrix,
        "execution": {
            "rank_count": 4,
            "pilot_ids": list(PILOT_IDS),
            "k_gate_completion_ids": list(PILOT_IDS),
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
            "go_payload_required_keys_exact": list(GO_PAYLOAD_REQUIRED_KEYS),
            "runner_parent_binding_fields": [
                "state_directory",
                "supervisor_pid",
                "supervisor_start_time_ticks",
                "boot_id",
                "launch_sha256",
                "go_sha256",
            ],
            "ambient_environment": {
                "keys_exact": list(FROZEN_AMBIENT_ENVIRONMENT_KEYS),
                "values_exact": dict(FROZEN_AMBIENT_ENVIRONMENT_VALUES),
                "canonical_values_sha256": FROZEN_AMBIENT_ENVIRONMENT_SHA256,
                "mutating_launcher_exact_match_required": True,
                "supervisor_umask_exact": "0022",
                "python_no_user_site_required": True,
                "validator_subprocess_explicit_environment_required": True,
                "supervisor_subprocess_explicit_environment_required": True,
                "runner_additional_binding_keys_exact": list(
                    RUNNER_BINDING_ENVIRONMENT_KEYS
                ),
                "runner_registered_bash_required": True,
            },
            "sealed_execution_inputs": {
                "mode": SEALED_EXECUTION_INPUT_MODE,
                "fixed_fds_exact": dict(SEALED_EXECUTION_INPUT_FDS),
                "proc_paths_exact": dict(SEALED_EXECUTION_INPUT_PROC_PATHS),
                "seal_mask_exact": SEALED_EXECUTION_INPUT_SEAL_MASK,
                "seal_names_exact": list(SEALED_EXECUTION_INPUT_SEAL_NAMES),
                "popen_pass_fds_exact": list(
                    SEALED_EXECUTION_INPUT_FDS.values()
                ),
                "registered_bash_executes_runner_fd": True,
                "scientific_config_manifest_from_sealed_fds_required": True,
                "canonical_paths_provenance_only": True,
            },
            "supervisor_state_directory": SUPERVISOR_STATE_DIRECTORY,
            "attempt_ledger_root": ATTEMPT_LEDGER_ROOT.as_posix(),
            "detachment_attestation_path": DETACHMENT_ATTESTATION_PATH.as_posix(),
            "supervisor_completion_path": SUPERVISOR_COMPLETION_PATH.as_posix(),
            "barrier_failure_root": BARRIER_FAILURE_ROOT.as_posix(),
            "supervisor_completion_contract": {
                "scientific_analysis_status": "accepted",
                "overall_protocol_status_before_completion": "pending_supervisor_completion",
                "launcher_terminal_required_before_finalize": True,
                "finalize_writes_completion_evidence": True,
                "completion_commit_changed_path_exact": SUPERVISOR_COMPLETION_PATH.as_posix(),
                "exact_scope_commit_required": True,
                "overall_acceptance_requires_committed_supervisor_completion": True,
                "final_acceptance_requires_validator_revalidation": True,
                "allowed_post_completion_commit_paths_exact": list(
                    POST_TERMINAL_DOCUMENTATION_PATHS
                ),
                "required_keys_exact": list(SUPERVISOR_COMPLETION_REQUIRED_KEYS),
                "schema_version": 1,
                "status": "supervisor_completed",
                "runner_exit_code": 0,
                "analysis_audit_status": "accepted",
                "final_acceptance_policy": "committed_completion_then_validator_revalidation",
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
                "required_keys_exact": list(BARRIER_FAILURE_REQUIRED_KEYS),
                "schema_version": 1,
                "status": "barrier_failed",
                "exit_code_must_be_nonzero": True,
                "retry_policy": "stop_after_exact_scope_commit_no_continue_or_retry",
            },
            "attempt_marker": {
                "basename_template": "{experiment_id}.json",
                "external_basename_template": "{experiment_id}.json",
                "creation_contract": "O_CREAT|O_EXCL_then_file_fsync_then_parent_directory_fsync_before_solver",
                "commit_scope": "exactly_one_attempt_ledger_marker",
                "run_introduction_parent_must_equal_marker_commit": True,
                "first_marker_commit_parent_must_equal_go_git_head": True,
                "subsequent_marker_commit_parent_must_equal_previous_accepted_run_introduction": True,
                "go_git_head_must_equal_detachment_introduction_commit": True,
                "validator_must_accept_marker_before_solver": True,
                "working_tree_clean_after_marker_commit": True,
                "same_id_retry_forbidden": True,
                "required_keys_exact": list(ATTEMPT_MARKER_REQUIRED_KEYS),
                "schema_version": 1,
                "status": "formal_attempt_started",
                "retry_policy": "new_protocol_revision_and_new_experiment_ids_only",
            },
            "absolute_deadline_watchdog_seconds": r1_config["execution"][
                "absolute_deadline_watchdog_seconds"
            ],
        },
        "coverage": {
            "logical_run_count": 40,
            "r1_reused_accepted_count": 0,
            "r1_historical_accepted_excluded_count": 10,
            "r3_new_run_count": 40,
            "r1_failed_attempt_accepted_count": 0,
            "r2_rejected_archive_reused_count": 0,
            "selected_scalar_point_count": 42,
        },
        "energy_matrix": _map_energy_matrix(r1_config["energy_matrix"]),
        "field_label_groups": _map_field_groups(r1_config["field_label_groups"]),
        "numerical_axes": deepcopy(r1_config["numerical_axes"]),
        "output_contract": deepcopy(r1_config["output_contract"]),
        "thermodynamic_semantics": deepcopy(r1_config["thermodynamic_semantics"]),
        "acceptance": deepcopy(r1_config["acceptance"]),
        "manifest": {
            "path": output_manifest.as_posix(),
            "sha256": sha256_bytes(encoded_manifest),
            "row_count": 40,
            "fields": list(MANIFEST_FIELDS),
        },
        "input_root": input_root.as_posix(),
        "input_derivation": {
            "source_commit": R1_PREREGISTRATION_COMMIT,
            "source_root": R1_INPUT_ROOT.as_posix(),
            "source_root_tree_oid": R1_INPUT_ROOT_TREE_OID,
            "derived_file_count": 160,
            "stru_and_kpt_byte_identical": True,
            "input_only_changed_key": "suffix",
            "metadata_changed_keys_exact": [
                "experiment_id",
                "protocol_revision",
                "suffix",
            ],
            "metadata_dataset_kind_preserved_as_r1_input_provenance": True,
        },
        "r1_bridge": bridge,
        "r2_stopped_bridge": r2_stopped_bridge,
        "base_evidence_commit": r1_config["base_evidence_commit"],
        "upstream_evidence": deepcopy(r1_config["upstream_evidence"]),
        "source_runs": deepcopy(r1_config["source_runs"]),
        "source_semantics": deepcopy(r1_config["source_semantics"]),
        "runtime_source": deepcopy(r1_config["runtime_source"]),
        "runtime": deepcopy(r1_config["runtime"]),
        "runtime_audit": deepcopy(r1_config["runtime_audit"]),
        "kmp_contract": deepcopy(r1_config["kmp_contract"]),
        "rank_count": r1_config["rank_count"],
        "implementation": implementation,
        "implementation_git_modes": modes,
        "formal_preregistration_commit_scope": {
            "include_exactly": [
                output_config.as_posix(),
                output_manifest.as_posix(),
                input_root.as_posix(),
            ],
            "implementation_must_be_in_parent_commit": True,
            "run_failure_archive_or_attempt_ledger_evidence_allowed": False,
        },
    }
    encoded_config = canonical_json_bytes(config)
    prepared: dict[str, object] = {
        "config": config,
        "config_bytes": encoded_config,
        "manifest_rows": manifest_rows,
        "manifest_bytes": encoded_manifest,
        "artifacts": artifacts,
        "output_config": output_config.as_posix(),
        "output_manifest": output_manifest.as_posix(),
        "input_root": input_root.as_posix(),
    }
    if write:
        _write(project_root, prepared)
    return prepared


def _write(project_root: Path, prepared: dict[str, object]) -> None:
    config_path = project_root / str(prepared["output_config"])
    manifest_path = project_root / str(prepared["output_manifest"])
    input_root = project_root / str(prepared["input_root"])
    config_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    input_root.mkdir(parents=True, exist_ok=False)
    artifacts = prepared["artifacts"]
    require(isinstance(artifacts, dict), "internal artifact map differs")
    for relative, data in sorted(artifacts.items()):
        path = project_root / str(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        require(isinstance(data, bytes), "internal artifact is not bytes")
        with path.open("xb") as handle:
            handle.write(data)
    with manifest_path.open("xb") as handle:
        handle.write(prepared["manifest_bytes"])
    with config_path.open("xb") as handle:
        handle.write(prepared["config_bytes"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--output-manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--input-root", type=Path, default=INPUT_ROOT)
    parser.add_argument("--write", action="store_true")
    arguments = parser.parse_args()
    prepared = prepare(
        arguments.project_root,
        output_config=arguments.output_config,
        output_manifest=arguments.output_manifest,
        input_root=arguments.input_root,
        write=arguments.write,
    )
    summary = {
        "status": "written" if arguments.write else "dry_run_validated",
        "config_path": prepared["output_config"],
        "config_sha256": sha256_bytes(prepared["config_bytes"]),
        "manifest_path": prepared["output_manifest"],
        "manifest_sha256": sha256_bytes(prepared["manifest_bytes"]),
        "r1_reused_count": len(R1_REUSED_AUDIT_IDS),
        "r3_execution_count": len(R3_AUDIT_IDS),
        "input_file_count": len(prepared["artifacts"]),
    }
    print(canonical_json_bytes(summary).decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
