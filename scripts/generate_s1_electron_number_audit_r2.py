#!/usr/bin/env python3
"""Generate the incremental S1 G1 electron-number R2 preregistration.

R2 preserves every R1 implementation and evidence blob.  It preregisters only
the 19-run continuation (S1-130--148), with a narrowly versioned KMP transient
mapping contract and a new audit-launcher shim.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import subprocess
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from generate_s1_electron_number_audit import (
    CONFIG_PATH as R1_CONFIG_PATH,
    EXECUTABLE_IMPLEMENTATION_PATHS as R1_EXECUTABLE_IMPLEMENTATION_PATHS,
    IMPLEMENTATION_PATHS as R1_IMPLEMENTATION_PATHS,
    MANIFEST_PATH as R1_MANIFEST_PATH,
)
from s1_electron_number_common import (
    MANIFEST_FIELDS,
    expected_electrons,
    find_single_log,
    integrate_cube,
    parse_charge_grid,
    read_json,
    read_manifest,
    scientific_equivalence,
    sha256,
)
from s1_g1_kmp_runtime_contract import (
    KMP_PATTERN,
    validate_kmp_runtime_contract,
)
from validate_s1_electron_number_audit import (
    replay_evidence as validate_r1_replay_evidence,
    validate_registration as validate_r1_registration,
)
from validate_s1_mpi_prefix_equivalence import _failed_archive_chain_failures


PROTOCOL_REVISION = "S1-G1-ELECTRON-NUMBER-R2"
CONFIG_PATH = Path("config/S1_electron_number_audit_r2.json")
MANIFEST_PATH = Path("config/S1_electron_number_audit_r2_manifest.tsv")
PROTOCOL_PATH = Path("docs/S1_G1_ELECTRON_NUMBER_AUDIT_R2_PROTOCOL.md")
R2_AUDIT_IDS = tuple(f"S1-20260805-{value:03d}" for value in range(130, 149))
R1_REUSED_AUDIT_IDS = tuple(
    f"S1-20260805-{value:03d}" for value in range(119, 130)
)
PILOT_AUDIT_IDS = ("S1-20260805-130", "S1-20260805-135")
EXECUTION_ORDER = (
    *PILOT_AUDIT_IDS,
    *(f"S1-20260805-{value:03d}" for value in range(131, 135)),
    *(f"S1-20260805-{value:03d}" for value in range(136, 149)),
)
FAILURE_ARCHIVE_IDS = ("S1-20260805-127", "S1-20260805-130")
R2_AUDIT_LAUNCHER = Path("scripts/runtime_relocation_audit_launcher_g1_r2.py")

EXPECTED_R1_PREREGISTRATION_COMMIT = (
    "f3efec315b1074c34709f8040f978d72575b6f10"
)
EXPECTED_R1_CONFIG_SHA256 = (
    "b87e0ca7ce089c529849468e2437b96a019f096a9be327627d1954a4f20d3010"
)
EXPECTED_R1_MANIFEST_SHA256 = (
    "128ac010f20dd236068503cce2ddb704facd41ed9dd9fe040e15d218799fbd66"
)
EXPECTED_LIBOMP_SHA256 = (
    "3fe1a40e7676ecb914fde29f45a1083656b9eafea07c680970d8dc4bb8bd0e84"
)
EXPECTED_FAILURE_ARCHIVE_COMMITS = {
    "S1-20260805-127": {
        "failure_commit": "95b817c7918a4055d4f7e940d0a9f63bbdb27411",
        "archive_commit": "0da560bad66591dafe1480c02c69ce97df930612",
    },
    "S1-20260805-130": {
        "failure_commit": "a894c735d95c3fc8d74f3cdb7fb8b16d1fd2c075",
        "archive_commit": "8eb9231cd4500c4bb2d6a4d84aa822ba234374d8",
    },
}

R2_IMPLEMENTATION_PATHS = (
    str(PROTOCOL_PATH),
    "scripts/s1_g1_kmp_runtime_contract.py",
    str(R2_AUDIT_LAUNCHER),
    "scripts/generate_s1_electron_number_audit_r2.py",
    "scripts/validate_s1_electron_number_audit_r2.py",
    "scripts/analyze_s1_electron_number_audit_r2.py",
    "scripts/run_s1_electron_number_audit_r2.sh",
    "tests/unit/test_s1_g1_kmp_runtime_contract.py",
    "tests/unit/test_s1_electron_number_audit_r2.py",
)
IMPLEMENTATION_PATHS = tuple(
    dict.fromkeys((*R1_IMPLEMENTATION_PATHS, *R2_IMPLEMENTATION_PATHS))
)
EXECUTABLE_IMPLEMENTATION_PATHS = frozenset(
    (*R1_EXECUTABLE_IMPLEMENTATION_PATHS, "scripts/run_s1_electron_number_audit_r2.sh")
)

_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_ATTEMPT = re.compile(r"attempt-([0-9a-f]{12})\Z")


def _git(
    project_root: Path, *args: str, text: bool = True
) -> str | bytes:
    output = subprocess.check_output(
        ["git", "-C", str(project_root), *args], text=text
    )
    return output.strip() if text else output


def _relative(project_root: Path, path: Path) -> str:
    return path.resolve().relative_to(project_root.resolve()).as_posix()


def _require_project_root(project_root: Path) -> Path:
    project_root = project_root.resolve()
    git_root = Path(str(_git(project_root, "rev-parse", "--show-toplevel"))).resolve()
    if git_root != project_root:
        raise ValueError(f"project root differs from Git top level: {project_root}")
    return project_root


def _require_clean(project_root: Path) -> None:
    status = str(
        _git(
            project_root,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
    )
    if status:
        raise ValueError("R2 preregistration generation requires a clean worktree")


def _git_entry(
    project_root: Path, commit: str, relative: str
) -> tuple[str, str, str]:
    output = subprocess.check_output(
        ["git", "-C", str(project_root), "ls-tree", "-z", commit, "--", relative]
    )
    records = [record for record in output.split(b"\0") if record]
    if len(records) != 1:
        raise ValueError(f"expected one Git entry at {commit}:{relative}")
    metadata, raw_path = records[0].split(b"\t", 1)
    mode, object_type, object_id = metadata.decode("ascii").split()
    if raw_path.decode("utf-8") != relative:
        raise ValueError(f"Git entry path mismatch at {commit}:{relative}")
    return mode, object_type, object_id


def _git_object_id(
    project_root: Path, commit: str, relative: str, expected_type: str
) -> str:
    value = str(_git(project_root, "rev-parse", f"{commit}:{relative}"))
    if not _HEX40.fullmatch(value):
        raise ValueError(f"invalid Git object ID at {commit}:{relative}")
    object_type = str(_git(project_root, "cat-file", "-t", value))
    if object_type != expected_type:
        raise ValueError(
            f"Git object at {commit}:{relative} is {object_type}, expected {expected_type}"
        )
    return value


def _introduction_commit(project_root: Path, relative: str) -> str:
    commits = str(
        _git(
            project_root,
            "log",
            "--no-renames",
            "--format=%H",
            "--diff-filter=A",
            "--",
            relative,
        )
    ).splitlines()
    if len(commits) != 1 or not _HEX40.fullmatch(commits[0]):
        raise ValueError(f"expected one introduction commit for {relative}")
    return commits[0]


def _latest_introduction_commit(project_root: Path, relative: str) -> str:
    commits = str(
        _git(
            project_root,
            "log",
            "--no-renames",
            "--format=%H",
            "--diff-filter=A",
            "-n",
            "1",
            "--",
            relative,
        )
    ).splitlines()
    if len(commits) != 1 or not _HEX40.fullmatch(commits[0]):
        raise ValueError(f"expected a latest introduction commit for {relative}")
    return commits[0]


def _file_anchor(project_root: Path, path: Path) -> dict[str, str]:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"anchor is not a regular non-symbolic file: {path}")
    relative = _relative(project_root, path)
    mode, object_type, object_id = _git_entry(project_root, "HEAD", relative)
    if object_type != "blob" or mode not in {"100644", "100755"}:
        raise ValueError(f"anchor is not a regular Git blob: {relative}")
    return {
        "path": relative,
        "sha256": sha256(path),
        "blob_oid": object_id,
        "git_mode": mode,
    }


def _implementation_closure(
    project_root: Path,
) -> tuple[dict[str, str], dict[str, str]]:
    hashes: dict[str, str] = {}
    modes: dict[str, str] = {}
    for relative in IMPLEMENTATION_PATHS:
        path = project_root / relative
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"implementation path is missing or symbolic: {relative}")
        mode, object_type, _ = _git_entry(project_root, "HEAD", relative)
        if object_type != "blob" or mode not in {"100644", "100755"}:
            raise ValueError(f"implementation path is not a regular Git blob: {relative}")
        result = subprocess.run(
            ["git", "-C", str(project_root), "diff", "--quiet", "HEAD", "--", relative]
        )
        if result.returncode != 0:
            raise ValueError(f"implementation differs from HEAD: {relative}")
        if relative in EXECUTABLE_IMPLEMENTATION_PATHS and (
            mode != "100755" or not os.access(path, os.X_OK)
        ):
            raise ValueError(f"required implementation entry is not executable: {relative}")
        hashes[relative] = sha256(path)
        modes[relative] = mode
    return hashes, modes


def _contract_result_is_accepted(result: object, label: str) -> None:
    """Accept the contract helper's supported fail-closed return conventions."""

    if result is False:
        raise ValueError(f"KMP runtime contract rejected {label}")
    if isinstance(result, (list, tuple)) and result:
        raise ValueError(f"KMP runtime contract rejected {label}: {list(result)}")
    if isinstance(result, dict):
        if result.get("accepted") is not True:
            raise ValueError(f"KMP runtime contract did not accept {label}")
        status = result.get("status")
        if status not in (None, "accepted"):
            raise ValueError(f"KMP runtime contract rejected {label}: status={status}")
        failures = result.get("failures", result.get("errors", []))
        if failures:
            raise ValueError(f"KMP runtime contract rejected {label}: {failures}")


def _validate_kmp(
    run_directory: Path,
    libomp_path: Path,
    libomp_realpath: Path,
    libomp_sha256: str,
) -> None:
    result = validate_kmp_runtime_contract(
        run_directory,
        str(libomp_path),
        str(libomp_realpath),
        libomp_sha256,
        require_registered_mapping_pattern=False,
    )
    _contract_result_is_accepted(result, str(run_directory))
    expected = {
        "registered_mapping_pattern_required": False,
        "kmp_pattern": KMP_PATTERN,
        "rank_count": 4,
        "lifecycle_count": 4,
        "successful_syscall_count": 12,
        "libomp_path": str(libomp_path),
        "libomp_realpath": str(libomp_realpath),
        "libomp_sha256": libomp_sha256,
    }
    if not isinstance(result, dict) or any(
        result.get(key) != value for key, value in expected.items()
    ):
        raise ValueError(f"KMP bridge summary differs for {run_directory}")


def _reused_run_anchor(
    project_root: Path,
    row: dict[str, str],
    libomp_path: Path,
    libomp_realpath: Path,
    libomp_sha256: str,
) -> dict[str, object]:
    audit_id = row["audit_experiment_id"]
    run_relative = f"runs/{audit_id}"
    run_directory = project_root / run_relative
    evidence = run_directory / "electron_number_audit.json"
    density = project_root / row["density_path"]
    introduction = _latest_introduction_commit(
        project_root, f"{run_relative}/experiment_metadata.json"
    )
    tree_oid = _git_object_id(project_root, "HEAD", run_relative, "tree")
    if _git_object_id(project_root, introduction, run_relative, "tree") != tree_oid:
        raise ValueError(f"accepted R1 run tree changed after introduction: {audit_id}")
    _validate_kmp(
        run_directory, libomp_path, libomp_realpath, libomp_sha256
    )
    return {
        "introduction_commit": introduction,
        "tree_oid": tree_oid,
        "evidence_path": _relative(project_root, evidence),
        "evidence_sha256": sha256(evidence),
        "density_path": _relative(project_root, density),
        "density_sha256": sha256(density),
    }


def _archive_artifact_anchors(
    project_root: Path, attempt: Path
) -> dict[str, dict[str, str]]:
    candidates = (
        "failure.json",
        "replay_status.json",
        "run_status.json",
        "result.json",
        "mpi_runtime_audit/audit.json",
        "mpi_runtime_audit/objects.tsv",
    )
    anchors = {
        relative: _file_anchor(project_root, attempt / relative)
        for relative in candidates
        if (attempt / relative).is_file()
    }
    cubes = sorted(attempt.glob("OUT.*/chg.cube"))
    if len(cubes) > 1:
        raise ValueError(f"failed archive contains multiple density cubes: {attempt}")
    if cubes:
        anchors["density_cube"] = _file_anchor(project_root, cubes[0])
    return anchors


def _validate_archived_130_science(
    project_root: Path, attempt: Path, row: dict[str, str]
) -> None:
    source = project_root / "runs" / row["source_experiment_id"]
    expected, _ = expected_electrons(attempt)
    source_grid = parse_charge_grid(find_single_log(source))
    replay_grid = parse_charge_grid(find_single_log(attempt))
    if replay_grid != source_grid:
        raise ValueError("S1-130 archived charge grid differs from its source")
    cubes = sorted(attempt.glob("OUT.*/chg.cube"))
    if len(cubes) != 1:
        raise ValueError("S1-130 archive must contain exactly one density cube")
    integration = integrate_cube(cubes[0], attempt / "STRU", expected, replay_grid)
    equivalence = scientific_equivalence(
        read_json(source / "result.json"), read_json(attempt / "result.json")
    )
    if integration.get("accepted") is not True:
        raise ValueError("S1-130 archived independent electron integral is rejected")
    if equivalence.get("accepted") is not True:
        raise ValueError("S1-130 archived scientific equivalence is rejected")


def _failure_archive_anchors(
    project_root: Path,
    experiment_id: str,
    libomp_path: Path,
    libomp_realpath: Path,
    libomp_sha256: str,
) -> list[dict[str, object]]:
    failures = _failed_archive_chain_failures(project_root, experiment_id)
    if failures:
        raise ValueError(
            f"R1 failed-archive validation failed for {experiment_id}:\n- "
            + "\n- ".join(failures)
        )
    archive_root = (
        project_root / "failed_runs" / "runtime_relocation" / experiment_id
    )
    if not archive_root.is_dir() or archive_root.is_symlink():
        raise ValueError(f"missing or symbolic failed-archive root: {archive_root}")
    attempts = sorted(path for path in archive_root.iterdir() if path.is_dir())
    if not attempts or any(path.is_symlink() for path in attempts):
        raise ValueError(f"invalid failed-attempt set for {experiment_id}")

    anchors: list[dict[str, object]] = []
    for attempt in attempts:
        match = _ATTEMPT.fullmatch(attempt.name)
        if match is None:
            raise ValueError(f"non-canonical failed-attempt name: {attempt}")
        failure_commit = str(_git(project_root, "rev-parse", match.group(1)))
        if not _HEX40.fullmatch(failure_commit) or not failure_commit.startswith(
            match.group(1)
        ):
            raise ValueError(f"cannot resolve failed-attempt commit: {attempt}")
        attempt_relative = _relative(project_root, attempt)
        archive_commit = _latest_introduction_commit(
            project_root, f"{attempt_relative}/failure.json"
        )
        if str(_git(project_root, "rev-parse", f"{archive_commit}^")) != failure_commit:
            raise ValueError(f"failed archive is not adjacent to failure: {attempt}")
        failure_run_relative = f"runs/{experiment_id}"
        failure_tree_oid = _git_object_id(
            project_root, failure_commit, failure_run_relative, "tree"
        )
        archive_tree_oid = _git_object_id(
            project_root, "HEAD", attempt_relative, "tree"
        )
        if failure_tree_oid != archive_tree_oid:
            raise ValueError(f"failed/archive tree OID mismatch: {attempt}")
        if (
            _git_object_id(project_root, archive_commit, attempt_relative, "tree")
            != archive_tree_oid
        ):
            raise ValueError(f"failed archive changed after archive commit: {attempt}")
        anchor: dict[str, object] = {
            "experiment_id": experiment_id,
            "attempt_path": attempt_relative,
            "failure_commit": failure_commit,
            "archive_commit": archive_commit,
            "failure_tree_oid": failure_tree_oid,
            "archive_tree_oid": archive_tree_oid,
            "artifacts": _archive_artifact_anchors(project_root, attempt),
        }
        if experiment_id == "S1-20260805-130":
            audit = read_json(attempt / "mpi_runtime_audit" / "audit.json")
            result = read_json(attempt / "result.json")
            if audit.get("status") != "rejected" or audit.get("failure_reasons") != [
                "unexpected_mapped_object_count:1"
            ]:
                raise ValueError("S1-130 archived audit does not isolate the KMP root cause")
            if result.get("converged") is not True:
                raise ValueError("S1-130 archived solver result is not converged")
            _validate_kmp(
                attempt, libomp_path, libomp_realpath, libomp_sha256
            )
            anchor["root_cause"] = {
                "solver_converged": True,
                "runtime_audit_status": "rejected",
                "failure_reasons": ["unexpected_mapped_object_count:1"],
                "kmp_contract_validated_from_raw_trace": True,
                "posthoc_acceptance_forbidden": True,
            }
        anchors.append(anchor)

    expected = EXPECTED_FAILURE_ARCHIVE_COMMITS[experiment_id]
    matching = [
        entry
        for entry in anchors
        if entry["failure_commit"] == expected["failure_commit"]
        and entry["archive_commit"] == expected["archive_commit"]
    ]
    if len(anchors) != 1 or len(matching) != 1:
        raise ValueError(f"failed-archive anchor set changed for {experiment_id}")
    return anchors


def _manifest_text(rows: list[dict[str, str]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output, fieldnames=MANIFEST_FIELDS, delimiter="\t", lineterminator="\n"
    )
    writer.writeheader()
    for row in rows:
        if set(row) != set(MANIFEST_FIELDS):
            raise ValueError("R1 manifest row key set differs from the frozen schema")
        writer.writerow({field: row[field] for field in MANIFEST_FIELDS})
    return output.getvalue()


def _write_new_text(path: Path, text: str) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError(f"refusing to overwrite preregistration artifact: {path}")
    with path.open("x", encoding="utf-8", newline="") as handle:
        handle.write(text)


def prepare(project_root: Path) -> dict[str, object]:
    project_root = _require_project_root(project_root)
    _require_clean(project_root)
    config_path = project_root / CONFIG_PATH
    manifest_path = project_root / MANIFEST_PATH
    for output in (config_path, manifest_path):
        if output.exists() or output.is_symlink():
            raise ValueError(f"R2 preregistration output already exists: {output}")

    for audit_id in R2_AUDIT_IDS:
        run_directory = project_root / "runs" / audit_id
        if run_directory.exists() or run_directory.is_symlink():
            raise ValueError(
                f"R2 run prefix exists before preregistration: runs/{audit_id}"
            )

    r1_config_path = project_root / R1_CONFIG_PATH
    r1_manifest_path = project_root / R1_MANIFEST_PATH
    if sha256(r1_config_path) != EXPECTED_R1_CONFIG_SHA256:
        raise ValueError("R1 config SHA-256 differs from the frozen bridge anchor")
    if sha256(r1_manifest_path) != EXPECTED_R1_MANIFEST_SHA256:
        raise ValueError("R1 manifest SHA-256 differs from the frozen bridge anchor")
    r1_preregistration_commit = _introduction_commit(
        project_root, str(R1_CONFIG_PATH)
    )
    if r1_preregistration_commit != EXPECTED_R1_PREREGISTRATION_COMMIT:
        raise ValueError("R1 preregistration commit differs from the frozen bridge anchor")
    if _introduction_commit(project_root, str(R1_MANIFEST_PATH)) != r1_preregistration_commit:
        raise ValueError("R1 config and manifest were not introduced together")

    r1_config, r1_rows, _ = validate_r1_registration(
        project_root,
        r1_config_path,
        r1_manifest_path,
        require_committed=True,
    )
    if read_manifest(r1_manifest_path) != r1_rows:
        raise ValueError("R1 manifest changed during validation")
    rows_by_audit = {
        row["audit_experiment_id"]: row
        for row in r1_rows
        if row["audit_experiment_id"]
    }
    if set(R1_REUSED_AUDIT_IDS + R2_AUDIT_IDS) != set(rows_by_audit):
        raise ValueError("R1 OF replay ID set differs from S1-119--148")

    runtime_base = deepcopy(r1_config["runtime"])
    runtime_audit_base = deepcopy(r1_config["runtime_audit"])
    registered_root = Path(runtime_base["wrappers"]["namespace_launcher"]["path"])
    if registered_root.parent.parent.resolve() != project_root:
        raise ValueError("formal R2 generator must run in the registered node01 repository")
    libomp_path = Path(runtime_base["recovery_prefix"]) / "lib" / "libomp.so"
    if not libomp_path.is_file() or libomp_path.is_symlink():
        raise ValueError(f"frozen recovery libomp is missing or symbolic: {libomp_path}")
    libomp_realpath = libomp_path.resolve(strict=True)
    libomp_digest = sha256(libomp_realpath)
    if (
        not _HEX64.fullmatch(libomp_digest)
        or libomp_digest != EXPECTED_LIBOMP_SHA256
    ):
        raise ValueError("recovery libomp SHA-256 differs from the frozen R2 identity")

    reused_runs: dict[str, dict[str, object]] = {}
    for audit_id in R1_REUSED_AUDIT_IDS:
        row = rows_by_audit[audit_id]
        payload, errors = validate_r1_replay_evidence(
            project_root,
            r1_config,
            row,
            require_committed=True,
            require_replay_status=True,
        )
        if errors or payload.get("status") != "accepted":
            raise ValueError(
                f"R1 reused run failed strict validation for {audit_id}:\n- "
                + "\n- ".join(errors or [f"status={payload.get('status')}"])
            )
        reused_runs[audit_id] = _reused_run_anchor(
            project_root,
            row,
            libomp_path,
            libomp_realpath,
            libomp_digest,
        )

    failure_archives: dict[str, dict[str, object]] = {}
    for experiment_id in FAILURE_ARCHIVE_IDS:
        anchors = _failure_archive_anchors(
            project_root,
            experiment_id,
            libomp_path,
            libomp_realpath,
            libomp_digest,
        )
        if len(anchors) != 1:
            raise ValueError(f"expected one frozen failed archive for {experiment_id}")
        anchor = anchors[0]
        if experiment_id == "S1-20260805-130":
            _validate_archived_130_science(
                project_root,
                project_root / str(anchor["attempt_path"]),
                rows_by_audit[experiment_id],
            )
        failure_archives[experiment_id] = {
            "failed_attempt_commit": anchor["failure_commit"],
            "archive_commit": anchor["archive_commit"],
            "archive_path": anchor["attempt_path"],
            "tree_oid": anchor["archive_tree_oid"],
        }

    r2_rows = [rows_by_audit[audit_id].copy() for audit_id in R2_AUDIT_IDS]
    if len(r2_rows) != 19 or any(
        row != rows_by_audit[row["audit_experiment_id"]] for row in r2_rows
    ):
        raise ValueError("R2 continuation rows are not exact R1 field copies")

    implementation, implementation_git_modes = _implementation_closure(project_root)
    generated_from_commit = str(_git(project_root, "rev-parse", "HEAD"))
    if not _HEX40.fullmatch(generated_from_commit):
        raise ValueError("invalid implementation commit")

    r2_launcher = project_root / R2_AUDIT_LAUNCHER
    runtime = deepcopy(runtime_base)
    base_launcher = deepcopy(runtime_base["wrappers"]["audit_launcher"])
    if set(base_launcher) != {"path", "sha256"}:
        raise ValueError("R1 audit-launcher identity schema changed")
    runtime["wrappers"]["audit_launcher"] = {
        "path": str(r2_launcher.resolve()),
        "sha256": sha256(r2_launcher),
    }
    runtime_audit = deepcopy(runtime_audit_base)
    patterns = list(runtime_audit["transient_mapping_patterns"])
    if KMP_PATTERN in patterns:
        raise ValueError("R1 transient pattern set already contains the R2 KMP rule")
    runtime_audit["transient_mapping_patterns"] = [*patterns, KMP_PATTERN]

    manifest_text = _manifest_text(r2_rows)
    manifest_digest = hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()
    config = {
        "schema_version": 2,
        "protocol_revision": PROTOCOL_REVISION,
        "status": "preregistered_pending_execution",
        "rank_count": 4,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_from_commit": generated_from_commit,
        "manifest_path": str(MANIFEST_PATH),
        "manifest_sha256": manifest_digest,
        "r1_bridge": {
            "config_path": str(R1_CONFIG_PATH),
            "config_sha256": EXPECTED_R1_CONFIG_SHA256,
            "manifest_path": str(R1_MANIFEST_PATH),
            "manifest_sha256": EXPECTED_R1_MANIFEST_SHA256,
            "preregistration_commit": r1_preregistration_commit,
            "reused_audit_ids": list(R1_REUSED_AUDIT_IDS),
            "reused_runs": reused_runs,
            "failure_archives": failure_archives,
        },
        "scope": {
            "coverage_denominator": 90,
            "ks_existing_density_count": 60,
            "ofdft_total_count": 30,
            "r1_reused_ofdft_count": 11,
            "r2_executed_ofdft_count": 19,
        },
        "execution": {
            "r2_audit_ids": list(R2_AUDIT_IDS),
            "pilot_audit_ids": list(PILOT_AUDIT_IDS),
            "execution_order": list(EXECUTION_ORDER),
            "remaining_forbidden_until_pilots_accepted": True,
        },
        "density": deepcopy(r1_config["density"]),
        "acceptance": deepcopy(r1_config["acceptance"]),
        "implementation": implementation,
        "implementation_git_modes": implementation_git_modes,
        "runtime_base": {
            "config_path": str(R1_CONFIG_PATH),
            "config_sha256": EXPECTED_R1_CONFIG_SHA256,
        },
        "runtime": runtime,
        "runtime_audit": runtime_audit,
        "kmp_contract": {
            "pattern": KMP_PATTERN,
            "expected_uid": 0,
            "rank_count_per_run": 4,
            "lifecycle_count_per_run": 4,
            "successful_syscall_count_per_run": 12,
            "libomp": {
                "path": str(libomp_path),
                "realpath": str(libomp_realpath),
                "sha256": libomp_digest,
            },
        },
    }

    # These comparisons make the claimed minimal runtime patch executable, not
    # merely documentary.
    reconstructed_runtime = deepcopy(runtime)
    reconstructed_runtime["wrappers"]["audit_launcher"] = base_launcher
    if reconstructed_runtime != runtime_base:
        raise ValueError("R2 runtime differs from R1 beyond the launcher identity")
    reconstructed_audit = deepcopy(runtime_audit)
    reconstructed_patterns = reconstructed_audit["transient_mapping_patterns"]
    if reconstructed_patterns[-1:] != [KMP_PATTERN]:
        raise ValueError("R2 KMP pattern is not the sole appended pattern")
    reconstructed_audit["transient_mapping_patterns"] = reconstructed_patterns[:-1]
    if reconstructed_audit != runtime_audit_base:
        raise ValueError("R2 runtime audit differs from R1 beyond the KMP pattern")

    _write_new_text(manifest_path, manifest_text)
    if sha256(manifest_path) != manifest_digest:
        raise ValueError("written R2 manifest SHA-256 mismatch")
    _write_new_text(
        config_path,
        json.dumps(config, indent=2, sort_keys=True) + "\n",
    )
    return {
        "protocol_revision": PROTOCOL_REVISION,
        "config_path": str(CONFIG_PATH),
        "config_sha256": sha256(config_path),
        "manifest_path": str(MANIFEST_PATH),
        "manifest_sha256": sha256(manifest_path),
        "r1_reused_count": len(reused_runs),
        "r2_execution_count": len(r2_rows),
        "failure_archive_count": len(failure_archives),
        "generated_from_commit": generated_from_commit,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args()
    payload = prepare(args.project_root)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
