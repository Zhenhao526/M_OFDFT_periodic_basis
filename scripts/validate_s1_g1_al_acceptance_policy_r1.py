#!/usr/bin/env python3
"""Fail-closed topology and evidence validator for the G1 acceptance policy R1."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import analyze_s1_g1_al_acceptance_policy_r1 as policy


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout.strip()


def parent_row(root: Path, commit: str) -> list[str]:
    return git(root, "rev-list", "--parents", "-n", "1", commit).split()


def diff_rows(root: Path, old: str, new: str) -> list[tuple[str, str]]:
    rows = []
    for line in git(root, "diff", "--name-status", old, new).splitlines():
        if line:
            status, path = line.split("\t", 1)
            rows.append((status, path))
    return rows


def object_at(root: Path, commit: str, path: str) -> dict:
    text = git(root, "show", f"{commit}:{path}")
    value = json.loads(text)
    require(isinstance(value, dict), f"committed JSON root differs: {commit}:{path}")
    return value


def require_clean(root: Path) -> None:
    require(git(root, "status", "--porcelain") == "", "worktree/index is not clean")


def require_output_absent(root: Path, cfg: dict, commit: str) -> None:
    require(not (root / cfg["analysis_root"]).exists(), "analysis output existed before evidence commit")
    require(git(root, "ls-tree", "-r", "--name-only", commit, "--", cfg["analysis_root"]) == "", "analysis output was already committed")


def validate_registered_chain(root: Path, cfg: dict, preregistration: str) -> str:
    implementation = cfg["registration"]["implementation_commit"]
    require(parent_row(root, preregistration) == [preregistration, implementation], "evidence parent is not the registered preregistration child")
    base = cfg["registration"]["integration_base_commit"]
    require(parent_row(root, implementation) == [implementation, base], "registered implementation/base topology differs")
    expected_implementation = sorted(cfg["topology"]["implementation_added_paths"])
    require(diff_rows(root, base, implementation) == [("A", path) for path in expected_implementation], "registered implementation diff/path set differs")
    require(diff_rows(root, implementation, preregistration) == [("M", policy.CONFIG_PATH.as_posix())], "evidence parent is not config-only preregistration")
    prereg_cfg = object_at(root, preregistration, policy.CONFIG_PATH.as_posix())
    require(prereg_cfg == cfg, "working/final config differs from preregistration bytes")
    old_cfg = object_at(root, implementation, policy.CONFIG_PATH.as_posix())
    normalized = json.loads(json.dumps(prereg_cfg))
    normalized["status"] = "implementation_pending_preregistration"
    normalized["registration"]["implementation_commit"] = "__FREEZE_IMPLEMENTATION_COMMIT__"
    require(normalized == old_cfg, "evidence parent changed non-registration content")
    return implementation


def validate_implementation(root: Path) -> dict:
    require_clean(root)
    head = git(root, "rev-parse", "HEAD")
    cfg = policy.read_json(root / policy.CONFIG_PATH)
    base = cfg["registration"]["integration_base_commit"]
    require(parent_row(root, head) == [head, base], "implementation must be the unique direct child of integration base")
    expected = sorted(cfg["topology"]["implementation_added_paths"])
    rows = diff_rows(root, base, head)
    require(rows == [("A", path) for path in expected], "implementation diff/path set differs")
    require(cfg["status"] == "implementation_pending_preregistration", "implementation config status differs")
    require(cfg["registration"]["implementation_commit"] == "__FREEZE_IMPLEMENTATION_COMMIT__", "implementation placeholder changed early")
    require(cfg["registration"]["preregistration_commit"] == "__FREEZE_PREREGISTRATION_COMMIT__", "preregistration placeholder changed early")
    require_output_absent(root, cfg, head)
    summary, gates = policy.build_analysis(root, cfg)
    require(summary["status"] == "accepted_g1_6_of_6" and len(gates) == 17, "implementation scientific replay differs")
    return {"mode": "implementation-only", "status": "accepted", "head": head, "gate_rows": len(gates)}


def validate_preregistered(root: Path) -> dict:
    require_clean(root)
    head = git(root, "rev-parse", "HEAD")
    cfg = policy.read_json(root / policy.CONFIG_PATH)
    implementation = cfg["registration"]["implementation_commit"]
    require(parent_row(root, head) == [head, implementation], "preregistration must be the unique child of implementation")
    require(cfg["registration"]["preregistration_commit"] == "__FREEZE_PREREGISTRATION_COMMIT__", "preregistration placeholder must remain non-self-referential")
    require(cfg["status"] == "preregistered", "preregistered config status differs")
    require(diff_rows(root, implementation, head) == [("M", policy.CONFIG_PATH.as_posix())], "preregistration diff must be config-only")
    old = object_at(root, implementation, policy.CONFIG_PATH.as_posix())
    normalized = json.loads(json.dumps(cfg))
    normalized["status"] = "implementation_pending_preregistration"
    normalized["registration"]["implementation_commit"] = "__FREEZE_IMPLEMENTATION_COMMIT__"
    require(normalized == old, "preregistration changed non-registration content")
    require_output_absent(root, cfg, head)
    summary, gates = policy.build_analysis(root, cfg)
    require(summary["status"] == "accepted_g1_6_of_6" and len(gates) == 17, "preregistered scientific replay differs")
    return {"mode": "preregistered-only", "status": "accepted", "head": head, "gate_rows": len(gates)}


def validate_committed(root: Path) -> dict:
    require_clean(root)
    head = git(root, "rev-parse", "HEAD")
    cfg = policy.read_json(root / policy.CONFIG_PATH)
    row = parent_row(root, head)
    require(len(row) == 2, "evidence commit must have one parent")
    preregistration = row[1]
    validate_registered_chain(root, cfg, preregistration)
    require(parent_row(root, head) == [head, preregistration], "evidence commit must be the unique child of preregistration")
    expected_files = sorted(cfg["topology"]["final_output_files"])
    rows = diff_rows(root, preregistration, head)
    require(rows == [("A", path) for path in expected_files], "evidence diff/file set differs")
    output_root = root / cfg["analysis_root"]
    require(output_root.is_dir() and not output_root.is_symlink(), "analysis output root missing")
    actual_files = sorted(path.relative_to(root).as_posix() for path in output_root.rglob("*") if path.is_file())
    require(actual_files == expected_files, "analysis output denominator differs")
    require(all(not (root / path).is_symlink() for path in actual_files), "analysis output contains symlink")
    rendered = policy.rendered_outputs(root, cfg)
    require(sorted(rendered) == sorted(policy.OUTPUT_NAMES), "rendered output denominator differs")
    for name, content in rendered.items():
        require((output_root / name).read_bytes() == content, f"committed output replay differs: {name}")
    summary = policy.read_json(output_root / "summary.json")
    require(summary["status"] == "accepted_g1_6_of_6", "committed disposition differs")
    require(summary["evidence_valid"] is True and summary["scientific_gate_accepted"] is True, "committed evidence/science status differs")
    require(summary["g1"] == {"status": "accepted", "accepted_subitems": 6, "required_subitems": 6}, "committed G1 denominator differs")
    require(summary["new_solver_run_count"] == 0, "policy revision ran a solver")
    policy.verify_scope(summary["scope_decision"])
    return {"mode": "require-committed", "status": "accepted", "head": head, "disposition": summary["status"], "g1": "6/6", "new_solver_run_count": 0}


def main() -> int:
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--implementation-only", action="store_true")
    modes.add_argument("--preregistered-only", action="store_true")
    modes.add_argument("--require-committed", action="store_true")
    args = parser.parse_args()
    root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel"))
    if args.implementation_only:
        result = validate_implementation(root)
    elif args.preregistered_only:
        result = validate_preregistered(root)
    else:
        result = validate_committed(root)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
