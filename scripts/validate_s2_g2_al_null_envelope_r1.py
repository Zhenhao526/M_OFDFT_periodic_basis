#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import analyze_s2_g2_al_null_envelope_r1 as analysis
import s2_g2_al_localized_common_r1 as common

CONFIG_REL = Path("config/S2_g2_al_null_envelope_r1.json")
PATHS = sorted(str(x) for x in (
    CONFIG_REL, Path("docs/S2_G2_AL_NULL_ENVELOPE_R1_PROTOCOL.md"),
    Path("scripts/analyze_s2_g2_al_null_envelope_r1.py"), Path("scripts/validate_s2_g2_al_null_envelope_r1.py"),
    Path("tests/test_s2_g2_al_null_envelope_r1.py"),
))


def git(root: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    common.require(p.returncode == 0, f"git {' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout.strip()


def parents(root: Path, commit: str) -> list[str]: return git(root, "rev-list", "--parents", "-n", "1", commit).split()[1:]


def diff_rows(root: Path, left: str, right: str) -> list[tuple[str, str]]:
    rows = []
    for line in git(root, "diff", "--name-status", left, right).splitlines():
        fields = line.split("\t"); common.require(len(fields) == 2, "rename/copy differs"); rows.append((fields[0], fields[1]))
    return rows


def clean(root: Path) -> None: common.require(git(root, "status", "--porcelain") == "", "worktree is dirty")


def implementation_identity(root: Path, commit: str) -> dict:
    common.require(parents(root, commit) == [analysis.BASE_COMMIT], "implementation parent differs")
    common.require(diff_rows(root, analysis.BASE_COMMIT, commit) == [("A", path) for path in PATHS], "implementation delta differs")
    config = json.loads(git(root, "show", f"{commit}:{CONFIG_REL}")); analysis.validate_config(config)
    common.require(config["status"] == "implementation_pending_preregistration" and config["implementation_commit"] == "__FREEZE_IMPLEMENTATION_COMMIT__", "implementation identity differs")
    return config


def preregistration_identity(root: Path, commit: str) -> tuple[str, dict]:
    parent = parents(root, commit); common.require(len(parent) == 1, "preregistration parent count differs")
    implementation = parent[0]; base = implementation_identity(root, implementation)
    common.require(diff_rows(root, implementation, commit) == [("M", str(CONFIG_REL))], "preregistration delta differs")
    config = json.loads(git(root, "show", f"{commit}:{CONFIG_REL}"))
    common.require(config["status"] == "preregistered_no_execution" and config["implementation_commit"] == implementation, "preregistration identity differs")
    normalized = json.loads(json.dumps(config)); normalized["status"] = "implementation_pending_preregistration"; normalized["implementation_commit"] = "__FREEZE_IMPLEMENTATION_COMMIT__"
    common.require(common.canonical_json(normalized) == common.canonical_json(base), "preregistration changes content")
    return implementation, config


def preexecution(root: Path, config: dict) -> None:
    analysis.validate_sources(root, config)
    common.require(not Path(config["execution"]["external_state_root"]).exists(), "external state exists")
    common.require(not (root / config["execution"]["analysis_root"]).exists(), "analysis root exists")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    group = parser.add_mutually_exclusive_group(required=True); group.add_argument("--implementation-only", action="store_true"); group.add_argument("--preregistered-only", action="store_true"); group.add_argument("--require-committed", action="store_true")
    args = parser.parse_args(); root = args.project_root.resolve(); clean(root); head = git(root, "rev-parse", "HEAD")
    if args.implementation_only:
        config = implementation_identity(root, head); preexecution(root, config); result = {"status": "accepted_implementation", "head": head}
    elif args.preregistered_only:
        _, config = preregistration_identity(root, head); preexecution(root, config); result = {"status": "accepted_preregistered", "head": head}
    else:
        parent = parents(root, head); common.require(len(parent) == 1, "evidence parent count differs"); prereg = parent[0]; _, config = preregistration_identity(root, prereg)
        expected = sorted(f"{config['execution']['analysis_root']}/{name}" for name in config["output_files"])
        common.require(diff_rows(root, prereg, head) == [("A", path) for path in expected], "evidence delta differs")
        summary, outputs = analysis.build_analysis(root, config)
        for name, data in outputs.items():
            actual = root / config["execution"]["analysis_root"] / name
            common.require(actual.is_file() and not actual.is_symlink() and actual.read_bytes() == data, f"committed output differs: {name}")
        result = {"status": "accepted_committed_evidence", "minimum_required_fixed_envelope_dimension": summary["minimum_required_fixed_envelope_dimension"], "decision": summary["decision"], "new_solver_run_count": 0}
    print(json.dumps(result, sort_keys=True)); return 0


if __name__ == "__main__": raise SystemExit(main())
