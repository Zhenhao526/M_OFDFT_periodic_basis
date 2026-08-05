#!/usr/bin/env python3
"""Validate the incremental R2 closure of the S1 G1 electron-number audit.

R2 deliberately does not reinterpret the eleven accepted R1 replays.  It
validates those runs with the frozen R1 validator and applies the amended
runtime policy only to the nineteen runs that are introduced after the R2
preregistration commit.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
from copy import deepcopy
from pathlib import Path

from generate_s1_electron_number_audit_r2 import (
    EXECUTABLE_IMPLEMENTATION_PATHS,
    IMPLEMENTATION_PATHS,
)
from s1_electron_number_common import (
    CUBE_PRECISION,
    MANIFEST_FIELDS,
    expected_electrons,
    find_single_log,
    integrate_cube,
    parse_charge_grid,
    read_json,
    scientific_equivalence,
    sha256,
)
from s1_g1_kmp_runtime_contract import validate_kmp_runtime_contract
from validate_s1_electron_number_audit import (
    replay_evidence as replay_evidence_r1,
    validate_registration as validate_registration_r1,
)
from validate_s1_mpi_prefix_equivalence import (
    _failed_archive_chain_failures,
    validate_failed_replay_run,
    validate_replay_run,
)


PROTOCOL_REVISION = "S1-G1-ELECTRON-NUMBER-R2"
CONFIG_PATH = Path("config/S1_electron_number_audit_r2.json")
MANIFEST_PATH = Path("config/S1_electron_number_audit_r2_manifest.tsv")
R1_CONFIG_PATH = Path("config/S1_electron_number_audit.json")
R1_MANIFEST_PATH = Path("config/S1_electron_number_audit_manifest.tsv")
R1_PREREGISTRATION_COMMIT = "f3efec315b1074c34709f8040f978d72575b6f10"
R1_CONFIG_SHA256 = "b87e0ca7ce089c529849468e2437b96a019f096a9be327627d1954a4f20d3010"
R1_MANIFEST_SHA256 = "128ac010f20dd236068503cce2ddb704facd41ed9dd9fe040e15d218799fbd66"
R1_FAILURE_COMMITS = {
    "S1-20260805-127": "95b817c7918a4055d4f7e940d0a9f63bbdb27411",
    "S1-20260805-130": "a894c735d95c3fc8d74f3cdb7fb8b16d1fd2c075",
}
KMP_PATTERN = r"^/dev/shm/__KMP_REGISTERED_LIB_[1-9][0-9]*_0$"
R1_REUSED_IDS = tuple(f"S1-20260805-{value:03d}" for value in range(119, 130))
R2_IDS = tuple(f"S1-20260805-{value:03d}" for value in range(130, 149))
R2_PILOTS = ("S1-20260805-130", "S1-20260805-135")
R2_EXECUTION_ORDER = (
    *R2_PILOTS,
    *(f"S1-20260805-{value:03d}" for value in range(131, 135)),
    *(f"S1-20260805-{value:03d}" for value in range(136, 149)),
)
EVIDENCE_NAME = "electron_number_audit_r2.json"


def _git(project_root: Path, *args: str, text: bool = True) -> str | bytes:
    output = subprocess.check_output(
        ["git", "-C", str(project_root), *args], text=text
    )
    return output.strip() if text else output


def _relative(project_root: Path, path: Path) -> str:
    return str(path.resolve().relative_to(project_root.resolve()))


def _read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if tuple(reader.fieldnames or ()) != MANIFEST_FIELDS:
            raise ValueError(f"manifest header mismatch: {path}")
        return list(reader)


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


def _tree_oid(project_root: Path, revision: str, relative: str) -> str:
    output = str(_git(project_root, "rev-parse", f"{revision}:{relative}"))
    if len(output) != 40:
        raise ValueError(f"invalid tree object for {revision}:{relative}")
    return output


def _blob_at(project_root: Path, revision: str, relative: str) -> bytes:
    return _git(project_root, "cat-file", "blob", f"{revision}:{relative}", text=False)


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


def _is_ancestor(project_root: Path, ancestor: str, descendant: str) -> bool:
    return subprocess.run(
        ["git", "-C", str(project_root), "merge-base", "--is-ancestor", ancestor, descendant],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def _selected_r1_rows(project_root: Path) -> list[dict[str, str]]:
    rows = _read_manifest(project_root / R1_MANIFEST_PATH)
    by_audit = {row["audit_experiment_id"]: row for row in rows if row["audit_experiment_id"]}
    return [dict(by_audit[experiment_id]) for experiment_id in R2_IDS]


def _reused_run_anchor(project_root: Path, experiment_id: str) -> dict[str, object]:
    run_relative = f"runs/{experiment_id}"
    evidence_relative = f"{run_relative}/electron_number_audit.json"
    evidence = read_json(project_root / evidence_relative)
    density_relative = str(evidence["integration"]["density_path"])
    introduction = _latest_introduction_commit(
        project_root, f"{run_relative}/experiment_metadata.json"
    )
    return {
        "introduction_commit": introduction,
        "tree_oid": _tree_oid(project_root, "HEAD", run_relative),
        "evidence_path": evidence_relative,
        "evidence_sha256": sha256(project_root / evidence_relative),
        "density_path": density_relative,
        "density_sha256": sha256(project_root / density_relative),
    }


def _archive_anchor(project_root: Path, experiment_id: str) -> dict[str, object]:
    frozen_failure = R1_FAILURE_COMMITS[experiment_id]
    archive = (
        project_root
        / "failed_runs/runtime_relocation"
        / experiment_id
        / f"attempt-{frozen_failure[:12]}"
    )
    if not archive.is_dir() or archive.is_symlink():
        raise ValueError(f"missing frozen failed-attempt archive for {experiment_id}")
    relative = _relative(project_root, archive)
    failed_prefix = archive.name.removeprefix("attempt-")
    failed_commit = str(_git(project_root, "rev-parse", f"{failed_prefix}^{{commit}}"))
    if failed_commit != frozen_failure or not failed_commit.startswith(failed_prefix):
        raise ValueError(f"failed archive commit prefix mismatch for {experiment_id}")
    archive_commit = _introduction_commit(project_root, relative)
    if str(_git(project_root, "rev-parse", f"{archive_commit}^")) != failed_commit:
        raise ValueError(f"archive is not adjacent to failed commit for {experiment_id}")
    return {
        "failed_attempt_commit": failed_commit,
        "archive_commit": archive_commit,
        "archive_path": relative,
        "tree_oid": _tree_oid(project_root, "HEAD", relative),
    }


def _expected_r1_bridge(project_root: Path) -> dict[str, object]:
    return {
        "config_path": str(R1_CONFIG_PATH),
        "config_sha256": R1_CONFIG_SHA256,
        "manifest_path": str(R1_MANIFEST_PATH),
        "manifest_sha256": R1_MANIFEST_SHA256,
        "preregistration_commit": R1_PREREGISTRATION_COMMIT,
        "reused_audit_ids": list(R1_REUSED_IDS),
        "reused_runs": {
            experiment_id: _reused_run_anchor(project_root, experiment_id)
            for experiment_id in R1_REUSED_IDS
        },
        "failure_archives": {
            experiment_id: _archive_anchor(project_root, experiment_id)
            for experiment_id in ("S1-20260805-127", "S1-20260805-130")
        },
    }


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
        errors.append("implementation closure is missing")
        return
    if not isinstance(modes, dict) or set(modes) != set(implementation):
        errors.append("implementation Git-mode closure differs")
        return
    if set(implementation) != set(IMPLEMENTATION_PATHS):
        errors.append("implementation closure path set mismatch")
    for relative, expected_sha in implementation.items():
        failure = _tracked_head_failure(project_root, relative)
        if failure:
            errors.append(f"implementation {failure}")
            continue
        path = project_root / relative
        if sha256(path) != expected_sha:
            errors.append(f"implementation SHA-256 differs: {relative}")
        entries = str(_git(project_root, "ls-files", "--stage", "--", relative)).splitlines()
        if len(entries) != 1:
            errors.append(f"implementation is not tracked exactly once: {relative}")
            continue
        mode = entries[0].split(maxsplit=1)[0]
        if mode not in {"100644", "100755"} or modes.get(relative) != mode:
            errors.append(f"implementation Git mode differs: {relative}")
        if relative in EXECUTABLE_IMPLEMENTATION_PATHS and mode != "100755":
            errors.append(f"required implementation entry is not executable: {relative}")
    if not require_committed:
        return
    try:
        prereg = _introduction_commit(project_root, str(CONFIG_PATH))
        parent = str(_git(project_root, "rev-parse", f"{prereg}^"))
        for relative in implementation:
            if _blob_at(project_root, parent, relative) != (project_root / relative).read_bytes():
                errors.append(f"implementation differs from preregistration parent: {relative}")
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError) as error:
        errors.append(f"implementation preregistration binding failed: {error}")


def _validate_preregistration(
    project_root: Path, config: dict, errors: list[str]
) -> None:
    try:
        prereg = _introduction_commit(project_root, str(CONFIG_PATH))
        if _introduction_commit(project_root, str(MANIFEST_PATH)) != prereg:
            errors.append("config and manifest were not introduced together")
        if _commit_changed_paths(project_root, prereg) != {
            str(CONFIG_PATH),
            str(MANIFEST_PATH),
        }:
            errors.append("R2 preregistration commit scope is not exactly config+manifest")
        parent = str(_git(project_root, "rev-parse", f"{prereg}^"))
        if parent != config.get("generated_from_commit"):
            errors.append("R2 preregistration parent differs from generated_from_commit")
        if _blob_at(project_root, prereg, str(CONFIG_PATH)) != (project_root / CONFIG_PATH).read_bytes():
            errors.append("R2 config differs from preregistration blob")
        if _blob_at(project_root, prereg, str(MANIFEST_PATH)) != (project_root / MANIFEST_PATH).read_bytes():
            errors.append("R2 manifest differs from preregistration blob")
        for experiment_id in R2_IDS:
            if str(_git(project_root, "ls-tree", "-r", parent, "--", f"runs/{experiment_id}")):
                errors.append(f"R2 run existed before preregistration: {experiment_id}")
        for row in _selected_r1_rows(project_root):
            relative = row["input_directory"]
            if _tree_oid(project_root, "HEAD", relative) != _tree_oid(
                project_root, R1_PREREGISTRATION_COMMIT, relative
            ):
                errors.append(f"R1 derived input changed after R1 preregistration: {relative}")
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError) as error:
        errors.append(f"R2 preregistration binding failed: {error}")


def _validate_execution_order(project_root: Path, prereg: str, errors: list[str]) -> None:
    introductions: dict[str, str] = {}
    for experiment_id in R2_IDS:
        run_directory = project_root / "runs" / experiment_id
        metadata = project_root / "runs" / experiment_id / "experiment_metadata.json"
        if run_directory.exists() and (
            not run_directory.is_dir()
            or run_directory.is_symlink()
            or not metadata.is_file()
            or metadata.is_symlink()
        ):
            errors.append(
                f"R2 active run lacks a regular experiment metadata file: {experiment_id}"
            )
            continue
        if metadata.is_file():
            try:
                commit = _latest_introduction_commit(
                    project_root, f"runs/{experiment_id}/experiment_metadata.json"
                )
                introductions[experiment_id] = commit
                if not _is_ancestor(project_root, prereg, commit) or commit == prereg:
                    errors.append(f"R2 run is not introduced after preregistration: {experiment_id}")
            except (subprocess.CalledProcessError, ValueError) as error:
                errors.append(f"cannot bind R2 run introduction for {experiment_id}: {error}")
    for index, experiment_id in enumerate(R2_EXECUTION_ORDER):
        if experiment_id not in introductions:
            continue
        for predecessor in R2_EXECUTION_ORDER[:index]:
            if predecessor not in introductions:
                errors.append(
                    f"R2 execution order violation: {experiment_id} exists before {predecessor}"
                )
            elif not _is_ancestor(
                project_root, introductions[predecessor], introductions[experiment_id]
            ):
                errors.append(
                    f"R2 execution order ancestry violation: {predecessor} -> {experiment_id}"
                )


def validate_registration(
    project_root: Path,
    config_path: Path,
    manifest_path: Path,
    *,
    require_committed: bool,
) -> tuple[dict, list[dict[str, str]], dict[str, object]]:
    project_root = project_root.resolve()
    config_path = config_path.resolve()
    manifest_path = manifest_path.resolve()
    config = read_json(config_path)
    rows = _read_manifest(manifest_path)
    errors: list[str] = []

    if config_path != (project_root / CONFIG_PATH).resolve():
        errors.append("R2 config must use its canonical project path")
    if manifest_path != (project_root / MANIFEST_PATH).resolve():
        errors.append("R2 manifest must use its canonical project path")
    expected_top_level = {
        "schema_version",
        "protocol_revision",
        "status",
        "rank_count",
        "generated_at",
        "generated_from_commit",
        "manifest_path",
        "manifest_sha256",
        "r1_bridge",
        "scope",
        "execution",
        "density",
        "acceptance",
        "implementation",
        "implementation_git_modes",
        "runtime_base",
        "runtime",
        "runtime_audit",
        "kmp_contract",
    }
    if set(config) != expected_top_level:
        errors.append("R2 config top-level key set mismatch")
    if config.get("schema_version") != 2 or config.get("protocol_revision") != PROTOCOL_REVISION:
        errors.append("R2 config schema/protocol mismatch")
    if config.get("status") != "preregistered_pending_execution" or config.get("rank_count") != 4:
        errors.append("R2 config status/rank count mismatch")
    if config.get("manifest_path") != str(MANIFEST_PATH):
        errors.append("R2 config manifest path mismatch")
    if config.get("manifest_sha256") != sha256(manifest_path):
        errors.append("R2 config manifest SHA-256 mismatch")

    expected_rows = _selected_r1_rows(project_root)
    if rows != expected_rows:
        errors.append("R2 manifest is not the exact ordered R1 subset for S1-130--148")
    if len(rows) != 19 or tuple(row["audit_experiment_id"] for row in rows) != R2_IDS:
        errors.append("R2 manifest experiment IDs differ from S1-130--148")
    if sha256(project_root / R1_CONFIG_PATH) != R1_CONFIG_SHA256:
        errors.append("R1 config differs from its frozen SHA-256")
    if sha256(project_root / R1_MANIFEST_PATH) != R1_MANIFEST_SHA256:
        errors.append("R1 manifest differs from its frozen SHA-256")

    try:
        if config.get("r1_bridge") != _expected_r1_bridge(project_root):
            errors.append("R1 reuse/archive bridge differs from current frozen evidence")
    except (FileNotFoundError, KeyError, subprocess.CalledProcessError, ValueError) as error:
        errors.append(f"cannot reconstruct R1 bridge: {error}")
    if config.get("scope") != {
        "coverage_denominator": 90,
        "ks_existing_density_count": 60,
        "ofdft_total_count": 30,
        "r1_reused_ofdft_count": 11,
        "r2_executed_ofdft_count": 19,
    }:
        errors.append("R2 scope accounting mismatch")
    if config.get("execution") != {
        "r2_audit_ids": list(R2_IDS),
        "pilot_audit_ids": list(R2_PILOTS),
        "execution_order": list(R2_EXECUTION_ORDER),
        "remaining_forbidden_until_pilots_accepted": True,
    }:
        errors.append("R2 execution/pilot registration mismatch")

    r1_config = read_json(project_root / R1_CONFIG_PATH)
    expected_runtime_base = {
        "config_path": str(R1_CONFIG_PATH),
        "config_sha256": R1_CONFIG_SHA256,
    }
    if config.get("runtime_base") != expected_runtime_base:
        errors.append("R2 runtime base registration mismatch")
    expected_runtime = deepcopy(r1_config["runtime"])
    expected_runtime["wrappers"]["audit_launcher"] = {
        "path": str((project_root / "scripts/runtime_relocation_audit_launcher_g1_r2.py").resolve()),
        "sha256": sha256(project_root / "scripts/runtime_relocation_audit_launcher_g1_r2.py"),
    }
    if config.get("runtime") != expected_runtime:
        errors.append("R2 runtime differs beyond the registered audit-launcher shim")
    expected_runtime_audit = deepcopy(r1_config["runtime_audit"])
    expected_runtime_audit["transient_mapping_patterns"] = [
        *expected_runtime_audit["transient_mapping_patterns"],
        KMP_PATTERN,
    ]
    if config.get("runtime_audit") != expected_runtime_audit:
        errors.append("R2 runtime audit differs beyond the exact KMP transient pattern")
    contract = config.get("kmp_contract", {})
    expected_contract_scalars = {
        "pattern": KMP_PATTERN,
        "expected_uid": 0,
        "rank_count_per_run": 4,
        "lifecycle_count_per_run": 4,
        "successful_syscall_count_per_run": 12,
    }
    if not isinstance(contract, dict) or any(
        contract.get(key) != value for key, value in expected_contract_scalars.items()
    ):
        errors.append("R2 KMP contract scalar registration mismatch")
    libomp = contract.get("libomp", {}) if isinstance(contract, dict) else {}
    if set(libomp) != {"path", "realpath", "sha256"}:
        errors.append("R2 KMP libomp identity fields mismatch")
    elif libomp.get("sha256") != "3fe1a40e7676ecb914fde29f45a1083656b9eafea07c680970d8dc4bb8bd0e84":
        errors.append("R2 KMP libomp SHA-256 mismatch")

    if config.get("density") != r1_config.get("density"):
        errors.append("R2 density integration algorithm differs from R1")
    if config.get("acceptance") != r1_config.get("acceptance"):
        errors.append("R2 scientific acceptance thresholds differ from R1")
    _validate_implementation(
        project_root, config, errors, require_committed=require_committed
    )

    r1_details: dict[str, object] = {}
    if require_committed:
        for relative in (str(CONFIG_PATH), str(MANIFEST_PATH)):
            failure = _tracked_head_failure(project_root, relative)
            if failure:
                errors.append(f"R2 preregistration {failure}")
        _validate_preregistration(project_root, config, errors)
        try:
            prereg = _introduction_commit(project_root, str(CONFIG_PATH))
            _validate_execution_order(project_root, prereg, errors)
            active_ids = {
                experiment_id
                for experiment_id in R2_IDS
                if (project_root / "runs" / experiment_id).is_dir()
                and not (project_root / "runs" / experiment_id).is_symlink()
            }
            required_pilots: tuple[str, ...] = ()
            if R2_PILOTS[1] in active_ids:
                required_pilots = (R2_PILOTS[0],)
            if active_ids - set(R2_PILOTS):
                required_pilots = R2_PILOTS
            rows_by_audit = {row["audit_experiment_id"]: row for row in rows}
            for pilot_id in required_pilots:
                _, pilot_errors = replay_evidence(
                    project_root,
                    config,
                    rows_by_audit[pilot_id],
                    require_committed=True,
                    require_replay_status=True,
                )
                errors.extend(
                    f"R2 pilot gate {pilot_id}: {failure}"
                    for failure in pilot_errors
                )
        except ValueError as error:
            errors.append(str(error))
        try:
            _, r1_rows, _ = validate_registration_r1(
                project_root,
                project_root / R1_CONFIG_PATH,
                project_root / R1_MANIFEST_PATH,
                require_committed=True,
            )
            r1_by_audit = {
                row["audit_experiment_id"]: row
                for row in r1_rows
                if row["audit_experiment_id"]
            }
            for experiment_id in R1_REUSED_IDS:
                payload, run_errors = replay_evidence_r1(
                    project_root,
                    r1_config,
                    r1_by_audit[experiment_id],
                    require_committed=True,
                    require_replay_status=True,
                )
                if run_errors:
                    errors.extend(
                        f"R1 reuse {experiment_id}: {failure}" for failure in run_errors
                    )
                else:
                    try:
                        libomp = config["kmp_contract"]["libomp"]
                        kmp = validate_kmp_runtime_contract(
                            project_root / "runs" / experiment_id,
                            expected_libomp_path=libomp["path"],
                            expected_libomp_realpath=libomp["realpath"],
                            expected_libomp_sha256=libomp["sha256"],
                            require_registered_mapping_pattern=False,
                        )
                        r1_details[experiment_id] = {
                            "electron_number_evidence": payload,
                            "kmp_runtime_contract": kmp,
                        }
                    except (KeyError, TypeError, ValueError) as error:
                        errors.append(
                            f"R1 reuse {experiment_id}: KMP bridge validation failed: {error}"
                        )
            for experiment_id in ("S1-20260805-127", "S1-20260805-130"):
                errors.extend(
                    f"R1 archive {experiment_id}: {failure}"
                    for failure in _failed_archive_chain_failures(project_root, experiment_id)
                )
            archive_130 = Path(
                str(config["r1_bridge"]["failure_archives"]["S1-20260805-130"]["archive_path"])
            )
            libomp = config["kmp_contract"]["libomp"]
            validate_kmp_runtime_contract(
                project_root / archive_130,
                expected_libomp_path=libomp["path"],
                expected_libomp_realpath=libomp["realpath"],
                expected_libomp_sha256=libomp["sha256"],
                require_registered_mapping_pattern=False,
            )
        except (KeyError, subprocess.CalledProcessError, ValueError) as error:
            errors.append(f"R1 bridge revalidation failed: {error}")

    if errors:
        raise ValueError(
            "S1 electron-number R2 registration validation failed:\n- "
            + "\n- ".join(errors)
        )
    return config, rows, {"r1_reused": r1_details}


def replay_evidence(
    project_root: Path,
    config: dict,
    row: dict[str, str],
    *,
    require_committed: bool,
    require_replay_status: bool,
) -> tuple[dict[str, object], list[str]]:
    audit_id = row["audit_experiment_id"]
    source_id = row["source_experiment_id"]
    runtime_row = {**row, "replay_experiment_id": audit_id}
    errors = validate_replay_run(
        project_root,
        config,
        runtime_row,
        require_committed=require_committed,
        require_replay_status=require_replay_status,
    )
    run = project_root / "runs" / audit_id
    source = project_root / "runs" / source_id
    payload: dict[str, object] = {}
    try:
        contract = config["kmp_contract"]
        kmp = validate_kmp_runtime_contract(
            run,
            expected_libomp_path=contract["libomp"]["path"],
            expected_libomp_realpath=contract["libomp"]["realpath"],
            expected_libomp_sha256=contract["libomp"]["sha256"],
        )
        if kmp.get("accepted") is not True:
            errors.append(f"{audit_id}: KMP runtime contract rejected")
        expected, derivation = expected_electrons(run)
        if format(expected, ".17g") != row["expected_electrons"]:
            errors.append(f"{audit_id}: expected electron count differs from manifest")
        source_grid = parse_charge_grid(find_single_log(source))
        replay_grid = parse_charge_grid(find_single_log(run))
        if source_grid != replay_grid:
            errors.append(f"{audit_id}: replay FFT grid differs from source")
        cube = project_root / row["density_path"]
        integration = integrate_cube(cube, run / "STRU", expected, replay_grid)
        integration["density_path"] = row["density_path"]
        equivalence = scientific_equivalence(
            read_json(source / "result.json"), read_json(run / "result.json")
        )
        if integration.get("accepted") is not True:
            errors.append(f"{audit_id}: certified electron relative error failed")
        if equivalence.get("accepted") is not True:
            errors.append(f"{audit_id}: source/replay scientific equivalence failed")
        payload = {
            "schema_version": 2,
            "protocol_revision": PROTOCOL_REVISION,
            "status": (
                "accepted"
                if integration.get("accepted") is True
                and equivalence.get("accepted") is True
                and kmp.get("accepted") is True
                else "rejected"
            ),
            "source_experiment_id": source_id,
            "audit_experiment_id": audit_id,
            "expected_electron_derivation": derivation,
            "integration": integration,
            "scientific_equivalence": equivalence,
            "kmp_runtime_contract": kmp,
            "provenance": {
                "config_path": str(CONFIG_PATH),
                "config_sha256": sha256(project_root / CONFIG_PATH),
                "manifest_path": str(MANIFEST_PATH),
                "manifest_sha256": sha256(project_root / MANIFEST_PATH),
                "preregistration_commit": _introduction_commit(
                    project_root, str(CONFIG_PATH)
                ),
                "integrator_path": "scripts/s1_electron_number_common.py",
                "integrator_sha256": config["implementation"][
                    "scripts/s1_electron_number_common.py"
                ],
                "validator_path": "scripts/validate_s1_electron_number_audit_r2.py",
                "validator_sha256": config["implementation"][
                    "scripts/validate_s1_electron_number_audit_r2.py"
                ],
                "source_run_introduction_commit": _latest_introduction_commit(
                    project_root, f"runs/{source_id}/input_metadata.json"
                ),
                "source_result_sha256": sha256(source / "result.json"),
                "replay_code_commit": read_json(run / "experiment_metadata.json")[
                    "code_commit"
                ],
            },
        }
        evidence_path = run / EVIDENCE_NAME
        if evidence_path.exists():
            if not evidence_path.is_file() or evidence_path.is_symlink():
                errors.append(f"{audit_id}: R2 evidence is not a regular file")
            elif read_json(evidence_path) != payload:
                errors.append(f"{audit_id}: R2 evidence differs from recomputation")
            elif require_committed:
                relative = _relative(project_root, evidence_path)
                failure = _tracked_head_failure(project_root, relative)
                if failure:
                    errors.append(f"{audit_id}: {failure}")
        elif require_committed or require_replay_status:
            errors.append(f"{audit_id}: missing {EVIDENCE_NAME}")
    except (
        FileNotFoundError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
    ) as error:
        errors.append(f"{audit_id}: R2 replay evidence validation failed: {error}")
    return payload, errors


def _write_evidence(path: Path, payload: dict[str, object]) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError(f"refusing to overwrite R2 evidence: {path}")
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", nargs="?", type=Path, default=project_root / MANIFEST_PATH)
    parser.add_argument("--config", type=Path, default=project_root / CONFIG_PATH)
    parser.add_argument("--require-committed", action="store_true")
    parser.add_argument("--check-run-core")
    parser.add_argument("--check-run")
    parser.add_argument("--check-failure-run")
    parser.add_argument("--check-failure-archives")
    parser.add_argument("--write-run-evidence")
    parser.add_argument("--require-all-runs", action="store_true")
    args = parser.parse_args()
    selected = [
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
    if len(selected) > 1:
        parser.error("select at most one per-run mode")
    config, rows, details = validate_registration(
        project_root,
        args.config.resolve(),
        args.manifest.resolve(),
        require_committed=args.require_committed,
    )
    by_audit = {row["audit_experiment_id"]: row for row in rows}
    checked: list[str] = []
    if selected:
        audit_id = selected[0]
        if audit_id not in by_audit:
            raise ValueError(f"requested R2 run is outside manifest: {audit_id}")
        row = by_audit[audit_id]
        if args.check_failure_archives:
            errors = _failed_archive_chain_failures(project_root, audit_id)
            if errors:
                raise ValueError("R2 failed-archive validation failed:\n- " + "\n- ".join(errors))
        elif args.check_failure_run:
            errors = validate_failed_replay_run(
                project_root,
                config,
                {**row, "replay_experiment_id": audit_id},
                require_committed=args.require_committed,
            )
            if errors:
                raise ValueError("R2 failed-run validation failed:\n- " + "\n- ".join(errors))
        else:
            payload, errors = replay_evidence(
                project_root,
                config,
                row,
                require_committed=args.require_committed,
                require_replay_status=args.check_run is not None,
            )
            if errors:
                raise ValueError("R2 run validation failed:\n- " + "\n- ".join(errors))
            evidence = project_root / "runs" / audit_id / EVIDENCE_NAME
            if args.write_run_evidence:
                _write_evidence(evidence, payload)
            elif not evidence.is_file() or evidence.is_symlink():
                raise ValueError(f"missing R2 evidence for {audit_id}")
        checked.append(audit_id)
    if args.require_all_runs:
        for audit_id in R2_IDS:
            _, errors = replay_evidence(
                project_root,
                config,
                by_audit[audit_id],
                require_committed=True,
                require_replay_status=True,
            )
            if errors:
                raise ValueError(
                    f"R2 all-run validation failed for {audit_id}:\n- "
                    + "\n- ".join(errors)
                )
            checked.append(audit_id)
    output = {
        "protocol_revision": PROTOCOL_REVISION,
        "coverage_target_count": 90,
        "ks_existing_density_count": 60,
        "r1_reused_ofdft_count": len(details["r1_reused"]),
        "r2_executed_ofdft_count": len(checked) if args.require_all_runs else 0,
        "config_sha256": sha256(args.config),
        "manifest_sha256": sha256(args.manifest),
        "checked_run_ids": checked,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
