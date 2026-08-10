#!/usr/bin/env python3
"""Validate preregistration or replay committed R1 analysis byte-for-byte."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from analyze_s1_g1_three_layer_r1 import build_final_analysis, write_analysis
from run_s1_g1_three_layer_r1 import REGISTERED_CODE, registered_paths
from s1_g1_three_layer_common import (
    find_project_root,
    git,
    load_config,
    load_manifest,
    read_json,
    require,
    require_clean_tree,
    require_tracked_matches_head,
    sha256_bytes,
    sha256_file,
)


def require_git_success(project_root: Path, *args: str) -> None:
    completed = subprocess.run(["git", *args], cwd=project_root, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    require(completed.returncode == 0, f"git command failed: {' '.join(args)}")


def validate_preregistered(project_root: Path, config: dict, rows: list[dict[str, str]]) -> dict:
    head = require_clean_tree(project_root)
    require(config["scientific_scope"]["second_independent_ks_closed"] is False, "second KS overclaim")
    require(config["scientific_scope"]["qe_d026_scope_closed"] is False, "QE D-026 overclaim")
    ids = [row["experiment_id"] for row in rows]
    require(not any(experiment_id.endswith(f"-{number:03d}") for experiment_id in ids for number in range(201, 216)), "old ID overlap")
    require_tracked_matches_head(project_root, registered_paths(project_root, config, rows))
    for row in rows:
        input_dir = project_root / config["input_root"] / row["experiment_id"]
        metadata = read_json(input_dir / "metadata.json")
        require(isinstance(metadata, dict), "input metadata must be object")
        require(metadata.get("experiment_id") == row["experiment_id"], "input metadata ID differs")
        require(metadata["pseudo"]["sha256"] == row["pseudo_sha256"], "input metadata PP differs")
        require(metadata["thermodynamic_semantics"]["local_only_kinetic_decomposition_claim"] is False, "local-only claim")
    base = config["base_commit"]
    require_git_success(project_root, "merge-base", "--is-ancestor", base, head)
    require(head != base, "formal solver requires a preregistration commit after base")
    return {"status": "accepted", "mode": "preregistered", "head": head, "registered_file_count": len(registered_paths(project_root, config, rows))}


def validate_analysis_tree(project_root: Path, config: dict, rows: list[dict[str, str]], require_committed: bool) -> dict:
    analysis_root = project_root / config["analysis_root"]
    require(analysis_root.is_dir() and not analysis_root.is_symlink(), "analysis root missing")
    summary = read_json(analysis_root / "summary.json")
    require(isinstance(summary, dict) and summary.get("status") == "accepted", "analysis not accepted")
    require(summary["scope_limits"]["second_independent_KS_engine_closed"] is False, "second KS overclaim")
    require(summary["scope_limits"]["D_026_QE_scope_closed"] is False, "D-026 overclaim")
    require(summary["scope_limits"]["Mg_LPP_hard_comparison"] is False, "Mg hard-gate overclaim")
    require(summary["formal_required_run_count"] == 14, "required denominator differs")
    require(summary["accepted_new_run_count"] in {14, 18}, "accepted count differs")
    pseudo_basenames = {pseudo["basename"] for pseudo in config["pseudodojo"]["materials"].values()}
    require(not any(path.name in pseudo_basenames for path in analysis_root.rglob("*")), "UPF bytes entered evidence tree")
    required_ids = config["execution_phases"]["p0"] + config["execution_phases"]["al_eos"] + config["execution_phases"]["mg_required"]
    for experiment_id in required_ids:
        raw = analysis_root / "raw" / experiment_id
        require((raw / "result.json").is_file(), f"missing raw required result: {experiment_id}")
        attempt = read_json(raw / "attempt.json")
        require(isinstance(attempt, dict) and attempt.get("experiment_id") == experiment_id, "attempt identity differs")
        require(attempt.get("retry_policy") == "same_id_forbidden_new_revision_and_new_ids_only", "retry policy differs")
    session = read_json(analysis_root / "orchestration" / "session.json")
    require(isinstance(session, dict), "session evidence differs")
    runner_commit = session["runner_commit"]
    require(runner_commit == summary["runner_commit"], "runner commit binding differs")
    require_git_success(project_root, "merge-base", "--is-ancestor", runner_commit, "HEAD")
    require_tracked_matches_head(project_root, [Path(value) for value in REGISTERED_CODE])
    with tempfile.TemporaryDirectory(prefix="g1_three_layer_replay_") as temporary:
        replay_root = Path(temporary) / "analysis"
        replay_summary, replay_points, _ = build_final_analysis(
            project_root, config, rows, (analysis_root / "raw").resolve()
        )
        write_analysis(replay_root, replay_summary, replay_points)
        for name in ("summary.json", "points.tsv", "README.md"):
            committed = analysis_root / name
            replayed = replay_root / name
            require(committed.read_bytes() == replayed.read_bytes(), f"analysis replay differs: {name}")
    head = git(project_root, "rev-parse", "HEAD")
    if require_committed:
        require_clean_tree(project_root)
        all_files = sorted(path for path in analysis_root.rglob("*") if path.is_file())
        require(all_files, "empty analysis tree")
        require_tracked_matches_head(project_root, [path.relative_to(project_root) for path in all_files])
        changed = git(project_root, "diff", "--name-only", f"{runner_commit}..HEAD").splitlines()
        prefix = Path(config["analysis_root"]).as_posix() + "/"
        require(changed and all(path.startswith(prefix) for path in changed), "post-run commit changed files outside analysis root")
        require(head != runner_commit, "evidence must be committed after runner commit")
    return {
        "status": "accepted",
        "mode": "final_require_committed" if require_committed else "final",
        "head": head,
        "runner_commit": runner_commit,
        "accepted_new_run_count": summary["accepted_new_run_count"],
        "scope_status": summary["scope_status"],
        "summary_sha256": sha256_file(analysis_root / "summary.json"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--preregistered-only", action="store_true")
    parser.add_argument("--require-committed", action="store_true")
    args = parser.parse_args()
    project_root = find_project_root(args.project_root)
    config = load_config(project_root)
    rows = load_manifest(project_root)
    if args.preregistered_only:
        require(not args.require_committed, "modes are mutually exclusive")
        payload = validate_preregistered(project_root, config, rows)
    else:
        payload = validate_analysis_tree(project_root, config, rows, args.require_committed)
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
