#!/usr/bin/env python3
"""Validate follow-up R2 preregistration or replay committed evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from analyze_s1_g1_three_layer_al_followup_r2 import build_analysis, write_analysis
from generate_s1_g1_three_layer_al_followup_r2 import generate
from run_s1_g1_three_layer_al_followup_r2 import REGISTERED_CODE, registered_paths
from s1_g1_three_layer_al_followup_r2_common import (
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


def require_commit_object(project_root: Path, commit: str) -> None:
    require(isinstance(commit, str) and len(commit) == 40, "invalid frozen commit identity")
    completed = subprocess.run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=project_root, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    require(completed.returncode == 0, f"frozen source commit object is unavailable: {commit}")


def validate_preregistered(project_root: Path, config: dict, rows: list[dict[str, str]]) -> dict:
    head = require_clean_tree(project_root)
    require(config["external_state_root"] == "/home/shenwei01/.local/state/m_ofdft/s1_g1_three_layer_al_domain_followup_r2_20260810", "external state path differs")
    require(not Path(config["external_state_root"]).exists(), "fresh R2 external state must not exist at preregistration")
    require(config["runtime"]["physical_core_ids"] == [40, 41, 42, 43], "frozen core IDs differ")
    require(config["runtime"]["reserved_logical_cpu_ids"] == [40, 41, 42, 43, 116, 117, 118, 119], "frozen logical/SMT reservation domain differs")
    require(config["runtime"]["map_by"] == "pe-list=40,41,42,43:ordered", "ordered binding differs")
    require(config["runtime"]["core_lock_root"] == "/home/shenwei01/.local/state/m_ofdft/core_locks", "core lock root differs")
    require(config["pseudodojo"].get("license_note") and "outside this repository" in config["pseudodojo"]["license_note"], "external UPF policy missing")
    require(set(config["pseudodojo"]["materials"]) == {"al", "mg"}, "Al/Mg recovery pseudo identity denominator differs")
    provenance = config["runtime_provenance"]
    require(provenance["new_ks_nl_binary_sha256"] == config["runtime"]["binary_sha256"], "new KS-NL runtime provenance differs")
    require(provenance["legacy_ks_l_relocated_binary_sha256"] == "438c74b9ada4c8df15ffbb66da6755907dfd2a3812ecf868fafd4d7dc4db62e1", "legacy relocated runtime identity differs")
    require_commit_object(project_root, provenance["storage_exact_bridge_commit"])
    require("implementation_not_byte_identical" in provenance["bridge_scope"], "runtime identity overclaim")
    scope = config["scientific_scope"]
    require("same_engine_lpp_scheme_bias_upper_bound" not in scope, "deprecated isolated scheme-bias overclaim remains")
    require(scope["interpretation"] == "same-engine NLPP-versus-local scheme plus construction discrepancy suitability bound", "scientific interpretation differs")
    require(scope["isolated_lpp_scheme_bias_claim"] is False, "isolated LPP scheme-bias overclaim")
    require(scope["second_independent_ks_closed"] is False and scope["g4_force_or_stress_closed"] is False, "scope overclaim")
    require(config["formal_ids"] == [row["experiment_id"] for row in rows], "config/manifest IDs differ")
    require(config["source_states"]["r1_p0"]["required_accepted_ids"] == [f"S1-20260810-{number:03d}" for number in range(301, 307)], "R1 recovery denominator differs")
    continuation = config["source_states"]["continuation_r2"]
    require(continuation["required_accepted_ids"] == ["S1-20260810-327", "S1-20260810-328"], "continuation endpoint IDs differ")
    require(continuation["required_recovery_ids"] == [f"S1-20260810-{number:03d}" for number in range(301, 307)], "continuation recovery ID set differs")
    require(continuation["endpoint_phase_marker_relative_path"] == "phases/al_eos.json" and continuation["endpoint_phase"] == "al_eos", "continuation phase closure path differs")
    require(continuation["endpoint_phase_accepted_ids"] == [f"S1-20260810-{number:03d}" for number in range(327, 333)], "continuation Al EOS phase denominator differs")
    require(continuation["versioned_recovery_barrier_path"] == "orchestration/s1/g1_three_layer_continuation_r2_20260810/r1_p0_recovery.json", "versioned recovery path differs")
    implementation = protocol_implementation_commit(project_root)
    require(implementation == config["implementation_commit"], "protocol/config implementation commit differs")
    require_ancestor(project_root, implementation, head)
    require_ancestor(project_root, config["r1_followup_closure_commit"], head)
    require_ancestor(project_root, config["inherited_three_layer_preregistration_commit"], head)
    require_ancestor(project_root, continuation["implementation_commit"], continuation["preregistration_commit"])
    # The continuation is an independent branch/state source; its exact commit
    # must exist locally but need not be merged into the follow-up branch.
    require_commit_object(project_root, continuation["preregistration_commit"])
    require(head != implementation, "formal preregistration must follow implementation commit")
    closure = read_json(project_root / "analysis/s1/g1_three_layer_al_domain_followup_r1_20260810/superseded_before_execution.json")
    require(isinstance(closure, dict) and closure.get("status") == "superseded_before_execution", "R1 follow-up closure missing")
    require(closure["execution_counts"]["solver_starts"] == 0, "R1 follow-up solver count differs")
    require_tracked_matches_head(project_root, registered_paths(config, rows))
    generate(project_root, mode="check")
    for row in rows:
        metadata = read_json(project_root / config["input_root"] / row["experiment_id"] / "metadata.json")
        require(isinstance(metadata, dict) and metadata.get("experiment_id") == row["experiment_id"], "input metadata ID differs")
        gate = metadata["geometry_binding"]["construction_hard_gate"]
        require(gate.get("accepted") is True, "input construction hard gate differs")
        if row["phase"] == "strain":
            require(gate.get("determinant_f") is not None and abs(gate["determinant_f"] - 1.0) < 1e-12, "strain det(F) differs")
            require(gate.get("direct_coordinate_bytes_unchanged") is True, "strain Direct coordinates differ")
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
    require(summary["semantic_limits"]["isolated_lpp_scheme_bias_claim"] is False, "scheme-bias overclaim")
    require(summary["semantic_limits"]["second_independent_KS_engine_closed"] is False, "second KS overclaim")
    require(summary["semantic_limits"]["G4_force_or_stress_closed"] is False, "G4 overclaim")
    require(summary["runtime_provenance"] == config["runtime_provenance"], "runtime provenance summary differs")
    pseudo_policy = summary.get("pseudo_body_evidence_policy")
    require(isinstance(pseudo_policy, dict) and pseudo_policy.get("status") == "accepted_identity_closure_external_body", "external pseudo identity closure missing")
    require(pseudo_policy.get("upf_body_committed") is False and pseudo_policy.get("server_external_cache_fully_validated") is True, "pseudo body/cache semantics differ")
    require(not any(path.name.lower().endswith(".upf") for path in analysis_root.rglob("*")), "UPF body entered evidence tree")
    orchestration = summary.get("followup_orchestration_identities")
    require(isinstance(orchestration, dict) and list(orchestration) == config["formal_ids"], "new-run orchestration denominator differs")
    require(all(row.get("accepted") is True and row.get("attempt_marker_sha256") for row in orchestration.values()), "new-run marker/result/session chain differs")
    snapshot = analysis_root / "state_snapshot"
    new = snapshot / "followup_r2"
    r1 = snapshot / "r1_p0"
    continuation = snapshot / "continuation_r2"
    for experiment_id in config["formal_ids"]:
        require((new / "runs" / experiment_id / "result.json").is_file(), f"missing new raw result: {experiment_id}")
        attempt = read_json(new / "attempts" / f"{experiment_id}.json")
        accepted = read_json(new / "accepted" / f"{experiment_id}.json")
        require(isinstance(attempt, dict) and attempt.get("status") == "formal_attempt_started", "attempt marker differs")
        require(isinstance(accepted, dict) and accepted.get("status") == "accepted", "accepted marker differs")
        require(attempt.get("experiment_id") == accepted.get("experiment_id") == experiment_id, "marker ID differs")
        case_preflight = attempt.get("case_live_preflight")
        require(isinstance(case_preflight, dict) and case_preflight.get("accepted") is True and not case_preflight.get("abacus_collisions"), "per-case sibling-aware collision gate differs")
        require(case_preflight.get("reserved_logical_cpus") == config["runtime"]["reserved_logical_cpu_ids"], "per-case SMT sibling proof differs")
    session = read_json(new / "session.json")
    terminal = read_json(new / "terminal.json")
    require(isinstance(session, dict) and isinstance(terminal, dict), "orchestration evidence differs")
    runner_commit = session["runner_commit"]
    require(terminal.get("status") == "accepted" and terminal.get("accepted_count") == 8 and terminal.get("runner_return_code") == 0, "terminal differs")
    require(terminal.get("runner_commit") == runner_commit == summary.get("runner_commit"), "runner commit binding differs")
    require(terminal.get("session_sha256") == sha256_file(new / "session.json"), "terminal/session SHA differs")
    require(terminal.get("config_sha256") == session.get("config_sha256") and terminal.get("manifest_sha256") == session.get("manifest_sha256"), "terminal/session registered-file identity differs")
    require(session["core_reservation_ack"]["accepted"] is True, "core reservation ACK absent")
    require(len(session["core_locks"]) == 8 and [row["logical_cpu_id"] for row in session["core_locks"]] == config["runtime"]["reserved_logical_cpu_ids"], "core-lock domain differs")
    require(all(row["acquired"] for row in session["core_locks"]), "core-lock proof differs")
    require(session["live_preflight"]["accepted"] is True, "live preflight differs")
    require(session["live_preflight"].get("reserved_logical_cpus") == config["runtime"]["reserved_logical_cpu_ids"] and not session["live_preflight"].get("abacus_collisions"), "session sibling-aware collision proof differs")
    require(session["detached_runtime_proof"]["accepted"] is True and session["detached_runtime_proof"]["session_leader"] is True, "detached runtime proof differs")
    require_ancestor(project_root, runner_commit, "HEAD")
    require_tracked_matches_head(project_root, [Path(value) for value in REGISTERED_CODE])
    with tempfile.TemporaryDirectory(prefix="g1_al_followup_r2_replay_") as temporary:
        replay_root = Path(temporary) / "analysis"
        replay_summary, replay_rows = build_analysis(project_root, config, new, r1, continuation)
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
