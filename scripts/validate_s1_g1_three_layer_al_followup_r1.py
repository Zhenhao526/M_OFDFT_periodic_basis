#!/usr/bin/env python3
"""Validate the Al follow-up preregistration or deterministically replay evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from analyze_s1_g1_three_layer_al_followup_r1 import build_analysis, write_analysis
from generate_s1_g1_three_layer_al_followup_r1 import generate
from run_s1_g1_three_layer_al_followup_r1 import REGISTERED_CODE, registered_paths
from s1_g1_three_layer_al_followup_common import (
    find_project_root,
    git,
    load_config,
    load_manifest,
    protocol_implementation_commit,
    read_json,
    require,
    require_clean_tree,
    require_tracked_matches_head,
    sha256_file,
)


def require_ancestor(project_root: Path, ancestor: str, descendant: str) -> None:
    completed = subprocess.run(["git", "merge-base", "--is-ancestor", ancestor, descendant], cwd=project_root, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    require(completed.returncode == 0, f"not an ancestor: {ancestor} -> {descendant}")


def validate_preregistered(project_root: Path, config: dict, rows: list[dict[str, str]]) -> dict:
    head = require_clean_tree(project_root)
    require(config["external_state_root"] == "/home/shenwei01/.local/state/m_ofdft/s1_g1_three_layer_al_domain_followup_r1_20260810", "external state path differs")
    require(config["runtime"]["physical_core_ids"] == [40, 41, 42, 43], "frozen core IDs differ")
    require(config["runtime"]["map_by"] == "pe-list=40,41,42,43:ordered", "ordered binding differs")
    require(config["scientific_scope"]["second_independent_ks_closed"] is False, "second KS overclaim")
    require(config["scientific_scope"]["g4_force_or_stress_closed"] is False, "G4 overclaim")
    require(config["formal_ids"] == [row["experiment_id"] for row in rows], "config/manifest IDs differ")
    implementation = protocol_implementation_commit(project_root)
    require(implementation == config["implementation_commit"], "protocol/config implementation commit differs")
    require_ancestor(project_root, implementation, head)
    require_ancestor(project_root, config["parent_three_layer_r1"]["runner_commit"], head)
    require(head != implementation, "formal preregistration must follow implementation commit")
    require_tracked_matches_head(project_root, registered_paths(config, rows))
    generate(project_root, mode="check")
    for row in rows:
        metadata = read_json(project_root / config["input_root"] / row["experiment_id"] / "metadata.json")
        require(isinstance(metadata, dict) and metadata.get("experiment_id") == row["experiment_id"], "input metadata ID differs")
        require(metadata["geometry_binding"]["geometry_payload_sha256"] == row["geometry_payload_sha256"], "input geometry binding differs")
        require(metadata["pseudo"]["expanded_nonlocal_projectors_per_atom"] == 18, "input projector contract differs")
    return {"status": "accepted", "mode": "preregistered", "head": head, "implementation_commit": implementation, "registered_run_count": 8, "registered_file_count": len(registered_paths(config, rows))}


def validate_final(project_root: Path, config: dict, rows: list[dict[str, str]], require_committed: bool) -> dict:
    analysis_root = project_root / config["analysis_root"]
    summary = read_json(analysis_root / "summary.json")
    require(isinstance(summary, dict) and summary.get("status") == "accepted", "analysis is not accepted")
    require(summary.get("scope_status") == "accepted_al_domain_followup", "scope status differs")
    require(summary.get("registered_run_count") == summary.get("accepted_run_count") == 8, "run denominator differs")
    require(summary.get("failed_missing_skipped_retried_count") == 0, "failure/retry count differs")
    require(summary.get("all_per_point_hard_gates_accepted") is True, "per-point hard gates differ")
    require(summary["galileo_gates"]["strain"]["status"] == "accepted", "strain gates rejected")
    require(summary["galileo_gates"]["endpoints"]["status"] == "accepted", "endpoint gates rejected")
    require(summary["semantic_limits"]["second_independent_KS_engine_closed"] is False, "second KS overclaim")
    require(summary["semantic_limits"]["G4_force_or_stress_closed"] is False, "G4 overclaim")
    require(not any(path.name == "Al_std.upf" for path in analysis_root.rglob("*")), "UPF body entered evidence tree")
    for experiment_id in config["formal_ids"]:
        require((analysis_root / "raw/new" / experiment_id / "result.json").is_file(), f"missing new raw result: {experiment_id}")
        attempt = read_json(analysis_root / "orchestration/attempts" / f"{experiment_id}.json")
        accepted = read_json(analysis_root / "orchestration/accepted" / f"{experiment_id}.json")
        require(isinstance(attempt, dict) and attempt.get("status") == "formal_attempt_started", "attempt marker differs")
        require(isinstance(accepted, dict) and accepted.get("status") == "accepted", "accepted marker differs")
        require(attempt.get("experiment_id") == accepted.get("experiment_id") == experiment_id, "marker ID differs")
    session = read_json(analysis_root / "orchestration/session.json")
    terminal = read_json(analysis_root / "orchestration/terminal.json")
    require(isinstance(session, dict) and isinstance(terminal, dict), "orchestration evidence differs")
    runner_commit = session["runner_commit"]
    require(terminal.get("status") == "accepted" and terminal.get("accepted_count") == 8 and terminal.get("runner_return_code") == 0, "terminal differs")
    require(terminal.get("runner_commit") == runner_commit == summary.get("runner_commit"), "runner commit binding differs")
    require_ancestor(project_root, runner_commit, "HEAD")
    require_tracked_matches_head(project_root, [Path(value) for value in REGISTERED_CODE])
    with tempfile.TemporaryDirectory(prefix="g1_al_followup_replay_") as temporary:
        replay_root = Path(temporary) / "analysis"
        replay_summary, replay_rows = build_analysis(project_root, config, analysis_root / "raw/new", analysis_root / "raw/parent")
        write_analysis(replay_root, replay_summary, replay_rows)
        for name in ("summary.json", "gates.tsv", "README.md"):
            require((analysis_root / name).read_bytes() == (replay_root / name).read_bytes(), f"deterministic replay differs: {name}")
    head = git(project_root, "rev-parse", "HEAD")
    if require_committed:
        require_clean_tree(project_root)
        evidence_files = sorted(path.relative_to(project_root) for path in analysis_root.rglob("*") if path.is_file())
        require(evidence_files, "analysis tree is empty")
        require_tracked_matches_head(project_root, evidence_files)
        changed = git(project_root, "diff", "--name-only", f"{runner_commit}..HEAD").splitlines()
        prefix = Path(config["analysis_root"]).as_posix() + "/"
        require(changed and all(path.startswith(prefix) for path in changed), "post-run commit changed paths outside analysis root")
        require(head != runner_commit, "evidence commit must follow runner commit")
    return {"status": "accepted", "mode": "final_require_committed" if require_committed else "final", "head": head, "runner_commit": runner_commit, "accepted_run_count": 8, "summary_sha256": sha256_file(analysis_root / "summary.json")}


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
        require(not args.require_committed, "validator modes are mutually exclusive")
        payload = validate_preregistered(project_root, config, rows)
    else:
        payload = validate_final(project_root, config, rows, args.require_committed)
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
