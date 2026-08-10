#!/usr/bin/env python3
"""Create and replay the immutable R3/343 parser-regression fixture."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

from analyze_s1_g1_three_layer_al_followup_r4 import analyze_r3_parser_regression
from parse_s1_g1_three_layer_al_followup_r4 import parse_run
from s1_g1_three_layer_al_followup_r4_common import (
    atomic_write,
    canonical_json_bytes,
    find_project_root,
    load_config,
    read_json,
    require,
    sha256_bytes,
    sha256_file,
)


def inventory(root: Path) -> list[dict]:
    require(root.is_dir() and not root.is_symlink(), f"fixture inventory root missing: {root}")
    rows: list[dict] = []
    for path in sorted(root.rglob("*"), key=lambda value: value.relative_to(root).as_posix()):
        require(not path.is_symlink(), f"symlink forbidden in parser fixture: {path}")
        if path.is_file():
            rows.append({
                "path": path.relative_to(root).as_posix(),
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            })
    return rows


def snapshot(rows: list[dict]) -> dict:
    digest_rows = "".join(f"{row['sha256']}  {row['path']}\n" for row in rows).encode()
    return {
        "file_count": len(rows),
        "regular_file_bytes": sum(int(row["size_bytes"]) for row in rows),
        "relative_sha256sum_list_digest": sha256_bytes(digest_rows),
    }


def prepare_fixture(project_root: Path, config: dict) -> dict:
    spec = config["r3_parser_regression_fixture"]
    source = Path(spec["source_state_root"])
    destination = Path(spec["external_root"])
    require(not destination.exists() and not destination.is_symlink(), "parser fixture root already exists; replacement/retry forbidden")
    source_rows = inventory(source)
    require(snapshot(source_rows) == spec["source_snapshot"], "R3 source-state snapshot differs before fixture copy")
    raw = destination / spec["raw_state_relative_path"]
    raw.mkdir(parents=True, mode=0o700)
    for identity in source_rows:
        source_path = source / identity["path"]
        target = raw / identity["path"]
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        require(not target.exists(), f"parser fixture destination exists: {target}")
        shutil.copyfile(source_path, target)
        require(target.stat().st_nlink == 1, f"parser fixture must be an independent copy: {target}")
        require(sha256_file(target) == identity["sha256"] and target.stat().st_size == identity["size_bytes"], f"parser fixture copy differs: {target}")
    copied_rows = inventory(raw)
    require(copied_rows == source_rows, "parser fixture/source inventory differs")
    manifest = {
        "schema_version": 1,
        "protocol_revision": config["protocol_revision"],
        "status": "accepted_read_only_byte_exact_independent_copy",
        "source_protocol_revision": spec["source_protocol_revision"],
        "source_runner_commit": spec["source_runner_commit"],
        "source_state_root": str(source),
        "source_run_id": spec["source_run_id"],
        "raw_state_relative_path": spec["raw_state_relative_path"],
        "source_snapshot": snapshot(source_rows),
        "files": source_rows,
        "copy_contract": {
            "all_regular_files_copied": True,
            "symlinks_forbidden": True,
            "hardlinks_to_source_forbidden": True,
            "timestamps_excluded_from_identity": True,
            "raw_tree_read_only_after_manifest": True,
        },
    }
    manifest_path = destination / spec["manifest_relative_path"]
    atomic_write(manifest_path, canonical_json_bytes(manifest), exclusive=True)
    for path in sorted(raw.rglob("*"), reverse=True):
        path.chmod(0o444 if path.is_file() else 0o555)
    raw.chmod(0o555)
    manifest_path.chmod(0o444)
    destination.chmod(0o555)
    return {
        "status": "accepted_fixture_created_once",
        "root": str(destination),
        "manifest_sha256": sha256_file(manifest_path),
        "source_snapshot": snapshot(source_rows),
    }


def require_read_only_tree(root: Path) -> None:
    for path in (root, *root.rglob("*")):
        require(not path.is_symlink(), f"symlink forbidden in read-only fixture: {path}")
        require(stat.S_IMODE(path.stat().st_mode) & 0o222 == 0, f"fixture path is writable: {path}")


def _negative_metadata_replay(
    source_run: Path,
    source_config: dict,
    source_wrapper: Path,
    mutation: str,
) -> dict:
    with tempfile.TemporaryDirectory(prefix=f"g1_r4_parser_negative_{mutation}_") as temporary:
        target = Path(temporary) / "run"
        shutil.copytree(source_run, target, copy_function=os.link)
        for directory in (target, *(path for path in target.rglob("*") if path.is_dir())):
            directory.chmod(0o700)
        metadata_path = target / "metadata.json"
        original = read_json(metadata_path)
        require(isinstance(original, dict), "negative fixture metadata differs")
        metadata_path.unlink()
        if mutation == "legacy_requirement_present":
            changed = {**original, "requirement": original["role"]}
        elif mutation == "role_missing":
            changed = {key: value for key, value in original.items() if key != "role"}
        elif mutation == "phase_role_mismatch":
            changed = {**original, "phase": "endpoint"}
        else:
            raise ValueError(f"unknown negative mutation: {mutation}")
        metadata_path.write_bytes(canonical_json_bytes(changed))
        try:
            parse_run(target, source_config, rank_wrapper_path=source_wrapper)
        except ValueError as error:
            return {"mutation": mutation, "status": "rejected_as_required", "error": str(error), "accepted": True}
        raise ValueError(f"negative metadata regression unexpectedly passed: {mutation}")


def validate_fixture(project_root: Path, config: dict, *, require_frozen_manifest: bool = True) -> dict:
    spec = config["r3_parser_regression_fixture"]
    source = Path(spec["source_state_root"])
    root = Path(spec["external_root"])
    raw = root / spec["raw_state_relative_path"]
    manifest_path = root / spec["manifest_relative_path"]
    require_read_only_tree(root)
    source_rows = inventory(source)
    fixture_rows = inventory(raw)
    require(source_rows == fixture_rows, "R3 source/fixture byte inventory differs")
    observed_snapshot = snapshot(fixture_rows)
    require(observed_snapshot == spec["source_snapshot"], "R3 fixture snapshot differs")
    manifest = read_json(manifest_path)
    require(isinstance(manifest, dict) and manifest.get("status") == "accepted_read_only_byte_exact_independent_copy", "R3 fixture manifest rejected")
    require(manifest_path.read_bytes() == canonical_json_bytes(manifest), "R3 fixture manifest is not canonical JSON")
    require(manifest.get("files") == fixture_rows and manifest.get("source_snapshot") == observed_snapshot, "R3 fixture manifest inventory differs")
    if require_frozen_manifest:
        require(sha256_file(manifest_path) == spec["manifest_sha256"], "R3 fixture manifest SHA differs")
    source_config_path = project_root / spec["source_config_path"]
    source_wrapper = project_root / spec["source_rank_wrapper_path"]
    require(sha256_file(source_config_path) == spec["source_config_sha256"], "R3 source config SHA differs")
    require(sha256_file(source_wrapper) == spec["source_rank_wrapper_sha256"], "R3 source rank-wrapper SHA differs")
    for relative, expected_sha in (
        (spec["source_config_path"], spec["source_config_sha256"]),
        (spec["source_rank_wrapper_path"], spec["source_rank_wrapper_sha256"]),
    ):
        committed = subprocess.run(
            ["git", "show", f"{spec['source_runner_commit']}:{relative}"],
            cwd=project_root, check=True, stdout=subprocess.PIPE,
        ).stdout
        require(sha256_bytes(committed) == expected_sha, f"R3 committed parser-fixture dependency differs: {relative}")
        require(committed == (project_root / relative).read_bytes(), f"R3 working parser-fixture dependency differs: {relative}")
    source_config = read_json(source_config_path)
    require(isinstance(source_config, dict) and source_config.get("protocol_revision") == spec["source_protocol_revision"], "R3 source config differs")
    source_run = raw / "runs" / spec["source_run_id"]
    raw_metadata = read_json(source_run / "metadata.json")
    require(isinstance(raw_metadata, dict) and "requirement" not in raw_metadata and raw_metadata.get("role") == "al_tetragonal_plus", "R3 role-only raw metadata differs")
    require(raw_metadata.get("config_sha256") == spec["source_config_sha256"], "R3 raw metadata/config identity differs")
    result = parse_run(source_run, source_config, rank_wrapper_path=source_wrapper)
    analysis = analyze_r3_parser_regression(result, config)
    negative = [
        _negative_metadata_replay(source_run, source_config, source_wrapper, mutation)
        for mutation in ("legacy_requirement_present", "role_missing", "phase_role_mismatch")
    ]
    return {
        "status": "accepted_complete_r3_raw_parse_and_analysis_regression",
        "manifest_sha256": sha256_file(manifest_path),
        "source_snapshot": observed_snapshot,
        "raw_metadata_schema": "role_only_requirement_absent",
        "parse_result_sha256": sha256_bytes(canonical_json_bytes(result)),
        "analysis": analysis,
        "negative_regressions": negative,
        "accepted": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--allow-unfrozen-manifest", action="store_true")
    args = parser.parse_args()
    require(args.prepare != args.validate, "choose exactly one of --prepare or --validate")
    project_root = find_project_root(args.project_root)
    config = load_config(project_root)
    payload = prepare_fixture(project_root, config) if args.prepare else validate_fixture(
        project_root, config, require_frozen_manifest=not args.allow_unfrozen_manifest
    )
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
