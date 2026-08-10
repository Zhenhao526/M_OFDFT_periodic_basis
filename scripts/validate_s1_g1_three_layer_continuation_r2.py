#!/usr/bin/env python3
"""Validate preregistration, committed recovery, or final replay."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from analyze_s1_g1_three_layer_continuation_r2 import build_final_analysis, write_analysis
from recover_s1_g1_three_layer_continuation_r2 import build_recovery, verify_existing
from run_s1_g1_three_layer_continuation_r2 import PREREGISTRATION_PATH, REGISTERED_CODE, registered_paths
from s1_g1_three_layer_continuation_r2_common import (
    find_project_root,
    git,
    load_config,
    load_manifest,
    read_json,
    require,
    require_clean_tree,
    require_tracked_matches_head,
    sha256_file,
)


def require_git_success(project_root: Path, *arguments: str) -> None:
    completed = subprocess.run(["git", *arguments], cwd=project_root, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    require(completed.returncode == 0, f"git command failed: {' '.join(arguments)}")


def generated_input_inventory(project_root: Path, config: dict, rows: list[dict[str, str]]) -> dict:
    paths = []
    for row in rows:
        root = project_root / config["input_root"] / row["experiment_id"]
        paths.extend(root / name for name in ("INPUT", "STRU", "KPT", "metadata.json"))
    identities = []
    for path in sorted(paths):
        relative = path.relative_to(project_root).as_posix()
        identities.append({"path": relative, "sha256": sha256_file(path), "size_bytes": path.stat().st_size})
    digest = hashlib.sha256("".join(f"{item['sha256']}  {item['path']}\n" for item in identities).encode("utf-8")).hexdigest()
    return {"file_count": len(identities), "sha256sum_list_digest": digest, "files": identities}


def validate_preregistered(project_root: Path, config: dict, rows: list[dict[str, str]]) -> dict:
    head = require_clean_tree(project_root)
    scope = config["scientific_scope"]
    require(scope["source_r1_operational_phase_accepted"] is False, "R1 phase overclaim")
    require(scope["second_independent_ks_closed"] is False and scope["qe_d026_scope_closed"] is False, "second-KS overclaim")
    ids = [row["experiment_id"] for row in rows]
    require(ids == [f"S1-20260810-{value:03d}" for value in range(327, 335)], "continuation ID denominator differs")
    require(not set(ids) & set(config["source_r1"]["accepted_ids"] + config["source_r1"]["permanently_unexecuted_ids"]), "new/source ID overlap")
    require_tracked_matches_head(project_root, registered_paths(project_root, config, rows))
    for row in rows:
        metadata = read_json(project_root / config["input_root"] / row["experiment_id"] / "metadata.json")
        require(isinstance(metadata, dict) and metadata.get("experiment_id") == row["experiment_id"], "input metadata ID differs")
        require(metadata["pseudo"]["sha256"] == row["pseudo_sha256"], "input PP binding differs")
        require(metadata["thermodynamic_semantics"]["local_only_kinetic_decomposition_claim"] is False, "local-only claim present")
        require(metadata["input_identity"]["config_sha256"] == sha256_file(project_root / "config/S1_g1_three_layer_continuation_r2.json"), "generated input/config binding differs")
    lock = read_json(project_root / PREREGISTRATION_PATH)
    require(isinstance(lock, dict) and lock.get("status") == "frozen_preregistration", "preregistration lock rejected")
    require(lock.get("protocol_revision") == config["protocol_revision"], "preregistration protocol differs")
    require(lock.get("formal_new_ids") == ids, "preregistration ID denominator differs")
    require(lock.get("config_sha256") == sha256_file(project_root / "config/S1_g1_three_layer_continuation_r2.json"), "preregistration config SHA differs")
    require(lock.get("manifest_sha256") == sha256_file(project_root / "config/S1_g1_three_layer_continuation_r2_manifest.tsv"), "preregistration manifest SHA differs")
    inventory = generated_input_inventory(project_root, config, rows)
    require(lock.get("input_file_count") == inventory["file_count"], "preregistration input count differs")
    require(lock.get("input_inventory_sha256") == inventory["sha256sum_list_digest"], "preregistration input inventory differs")
    implementation = lock.get("implementation_commit")
    require(isinstance(implementation, str) and len(implementation) == 40, "preregistration implementation commit missing")
    require_git_success(project_root, "merge-base", "--is-ancestor", implementation, head)
    expected_post_implementation = {PREREGISTRATION_PATH.as_posix()} | {
        f"{config['input_root']}/{experiment_id}/metadata.json" for experiment_id in ids
    }
    actual_post_implementation = set(git(project_root, "diff", "--name-only", f"{implementation}..{head}").splitlines())
    require(actual_post_implementation == expected_post_implementation, "post-implementation preregistration delta differs")
    base = config["implementation_base_commit"]
    require_git_success(project_root, "merge-base", "--is-ancestor", base, head)
    require(head != base, "formal solver requires a post-base preregistration")
    return {"status": "accepted", "mode": "preregistered", "head": head, "registered_file_count": len(registered_paths(project_root, config, rows)), "formal_new_id_count": len(ids)}


def validate_recovery_committed(project_root: Path, config: dict) -> dict:
    head = require_clean_tree(project_root)
    recomputed = build_recovery(project_root, config)
    payload = verify_existing(project_root, config, recomputed)
    relative = Path(config["versioned_recovery_barrier"])
    require_tracked_matches_head(project_root, [relative])
    prereg = payload["continuation_prereg_commit"]
    require_git_success(project_root, "merge-base", "--is-ancestor", prereg, head)
    changed = git(project_root, "diff", "--name-only", f"{prereg}..{head}").splitlines()
    require(changed == [relative.as_posix()], "recovery commit changed files beyond versioned barrier")
    return {**payload, "mode": "recovery_committed", "head": head, "versioned_barrier": relative.as_posix()}


def validate_analysis(project_root: Path, config: dict, rows: list[dict[str, str]], require_committed: bool) -> dict:
    analysis = project_root / config["analysis_root"]
    require(analysis.is_dir() and not analysis.is_symlink(), "analysis root missing")
    summary = read_json(analysis / "summary.json")
    require(isinstance(summary, dict) and summary.get("status") == "accepted", "analysis not accepted")
    require(summary["source_r1_run_count"] == 6 and summary["source_r1_new_run_count"] == 0, "source denominator differs")
    require(summary["formal_continuation_run_count"] == 8 and summary["formal_al_hard_continuation_count"] == 6 and summary["formal_mg_diagnostic_continuation_count"] == 2, "continuation denominator differs")
    require(summary["source_r1_operational_disposition"]["phase_accepted"] is False, "R1 operational overclaim")
    require(summary["p0_recovery"]["overall_hard_domain_uses_al_only"] is True, "Al hard-domain scope differs")
    require(summary["mg_compatibility_diagnostic"]["affects_overall"] is False, "Mg affected overall")
    require(summary["mg_compatibility_diagnostic"]["coverage"] == "mandatory_three_point_three_curve", "Mg three-curve coverage differs")
    limits = summary["scope_limits"]
    require(limits["second_independent_KS_engine_closed"] is False and limits["D_026_QE_scope_closed"] is False and limits["G1_six_of_six_closed_by_this_scope"] is False, "scope overclaim")
    pseudo_names = {value["basename"] for value in config["pseudodojo"]["materials"].values()}
    require(not any(path.name in pseudo_names for path in analysis.rglob("*")), "UPF bytes entered analysis")
    for experiment_id in config["source_r1"]["accepted_ids"]:
        raw = analysis / "raw" / "source_r1" / experiment_id
        require((raw / "result.json").is_file() and (raw / "attempt.json").is_file() and (raw / "accepted.json").is_file(), "source raw denominator differs")
    for experiment_id in [row["experiment_id"] for row in rows]:
        raw = analysis / "raw" / "continuation" / experiment_id
        require((raw / "result.json").is_file() and (raw / "attempt.json").is_file() and (raw / "accepted.json").is_file(), "continuation raw denominator differs")
    session = read_json(analysis / "orchestration" / "session.json")
    require(isinstance(session, dict) and session.get("runner_commit") == summary["runner_commit"], "runner commit binding differs")
    require(session.get("recovery_barrier_sha256") == summary["recovery_barrier_sha256"], "recovery barrier/session binding differs")
    phase_ids = {
        "al_eos": [f"S1-20260810-{value:03d}" for value in range(327, 333)],
        "mg_required": [f"S1-20260810-{value:03d}" for value in range(333, 335)],
    }
    for phase, expected_ids in phase_ids.items():
        marker = read_json(analysis / "orchestration" / "phases" / f"{phase}.json")
        require(isinstance(marker, dict) and marker.get("status") == "accepted", f"{phase} marker rejected")
        require(marker.get("phase") == phase and marker.get("protocol_revision") == config["protocol_revision"], f"{phase} marker identity differs")
        require(marker.get("accepted_ids") == expected_ids and marker.get("accepted_count") == len(expected_ids), f"{phase} marker denominator differs")
        require(marker.get("runner_commit") == session["runner_commit"] and marker.get("recovery_barrier_sha256") == summary["recovery_barrier_sha256"], f"{phase} marker provenance differs")
        require(marker.get("session_sha256") == sha256_file(analysis / "orchestration" / "session.json"), f"{phase} session SHA differs")
        require(marker.get("config_sha256") == sha256_file(project_root / "config/S1_g1_three_layer_continuation_r2.json"), f"{phase} config SHA differs")
        require(marker.get("manifest_sha256") == sha256_file(project_root / "config/S1_g1_three_layer_continuation_r2_manifest.tsv"), f"{phase} manifest SHA differs")
        result_map = marker.get("accepted_result_sha256")
        require(isinstance(result_map, dict) and list(result_map) == expected_ids, f"{phase} result SHA denominator differs")
        for experiment_id in expected_ids:
            require(result_map[experiment_id] == sha256_file(analysis / "raw" / "continuation" / experiment_id / "result.json"), f"{phase} result SHA differs")
        require(set(marker.get("per_run_core_collision_preflight_sha256", {})) == set(expected_ids), f"{phase} preflight denominator differs")
    require_git_success(project_root, "merge-base", "--is-ancestor", session["runner_commit"], "HEAD")
    require_tracked_matches_head(project_root, [Path(value) for value in REGISTERED_CODE] + [Path(config["versioned_recovery_barrier"])])
    with tempfile.TemporaryDirectory(prefix="g1_three_layer_continuation_r2_replay_") as temporary:
        output = Path(temporary) / "analysis"
        replay_summary, replay_points, _ = build_final_analysis(project_root, config, rows, (analysis / "raw").resolve())
        write_analysis(output, replay_summary, replay_points)
        for name in ("summary.json", "points.tsv", "README.md"):
            require((analysis / name).read_bytes() == (output / name).read_bytes(), f"analysis replay differs: {name}")
    head = git(project_root, "rev-parse", "HEAD")
    if require_committed:
        require_clean_tree(project_root)
        files = sorted(path for path in analysis.rglob("*") if path.is_file())
        require(files, "analysis tree empty")
        require_tracked_matches_head(project_root, [path.relative_to(project_root) for path in files])
        changed = git(project_root, "diff", "--name-only", f"{session['runner_commit']}..HEAD").splitlines()
        prefix = Path(config["analysis_root"]).as_posix() + "/"
        require(changed and all(path.startswith(prefix) for path in changed), "post-run commit changed files outside analysis")
        require(head != session["runner_commit"], "evidence must be committed after runner")
    return {"status": "accepted", "mode": "final_require_committed" if require_committed else "final", "head": head, "runner_commit": session["runner_commit"], "scope_status": summary["scope_status"], "summary_sha256": sha256_file(analysis / "summary.json")}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--preregistered-only", action="store_true")
    parser.add_argument("--recovery-committed", action="store_true")
    parser.add_argument("--require-committed", action="store_true")
    args = parser.parse_args()
    require(sum((args.preregistered_only, args.recovery_committed, args.require_committed)) <= 1, "validator modes differ")
    project_root = find_project_root(args.project_root)
    config = load_config(project_root)
    rows = load_manifest(project_root)
    if args.preregistered_only:
        payload = validate_preregistered(project_root, config, rows)
    elif args.recovery_committed:
        payload = validate_recovery_committed(project_root, config)
    else:
        payload = validate_analysis(project_root, config, rows, args.require_committed)
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
