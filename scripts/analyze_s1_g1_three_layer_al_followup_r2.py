#!/usr/bin/env python3
"""Build deterministic R2 strain and endpoint gates with source identities."""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from parse_s1_g1_three_layer_al_followup_r2 import parse_run as reparse_new_run
from run_s1_g1_three_layer_al_followup_r2 import (
    git_file_at_commit,
    verify_accepted_source,
    verify_continuation_phase_preflight,
    verify_pseudo_identity_closure,
)
from s1_g1_three_layer_al_followup_r2_common import (
    atomic_write,
    canonical_json_bytes,
    find_project_root,
    geometry_payload_bytes,
    load_config,
    load_manifest,
    read_json,
    require,
    sha256_bytes,
    sha256_file,
    verify_strain_geometry,
)


STRAIN_MAP = {
    "S1-20260810-335": "S1-20260810-204",
    "S1-20260810-336": "S1-20260810-205",
    "S1-20260810-337": "S1-20260810-206",
    "S1-20260810-338": "S1-20260810-207",
}
ANCHOR_IDS = ("S1-20260810-301", "S1-20260810-302", "S1-20260810-303")
RECOVERY_IDS = tuple(f"S1-20260810-{number:03d}" for number in range(301, 307))
CONTINUATION_IDS = ("S1-20260810-327", "S1-20260810-328")
CONTINUATION_PHASE_IDS = tuple(f"S1-20260810-{number:03d}" for number in range(327, 333))
GATE_FIELDS = ("gate", "point", "metric", "value", "limit", "inequality", "accepted")


def _read_object(path: Path) -> dict:
    require(path.is_file() and not path.is_symlink(), f"missing JSON evidence: {path}")
    payload = read_json(path)
    require(isinstance(payload, dict), f"JSON root must be object: {path}")
    return payload


def _hardlink_or_copy(source: str, destination: str) -> str:
    source_path = Path(source)
    require(source_path.is_file() and not source_path.is_symlink(), f"unsafe replay source: {source_path}")
    try:
        os.link(source_path, destination)
    except OSError:
        shutil.copyfile(source_path, destination)
    return destination


def reparse_with_external_pseudo(
    run_dir: Path,
    result: dict,
    config: dict,
    *,
    require_followup_orchestration: bool,
) -> tuple[dict, dict]:
    """Run the complete parser without committing a redistributability-unclear UPF body."""
    closure = verify_pseudo_identity_closure(run_dir, result, config, require_run_body=False)
    basename = closure["basename"]
    raw_pseudo = run_dir / basename
    if raw_pseudo.is_file():
        require(not raw_pseudo.is_symlink(), "raw pseudo body must not be a symlink")
        reparsed = reparse_new_run(
            run_dir, config, require_followup_orchestration=require_followup_orchestration
        )
    else:
        require(not raw_pseudo.exists(), "unsafe missing-pseudo placeholder")
        for path in run_dir.rglob("*"):
            require(not path.is_symlink(), f"snapshot contains symlink: {path}")
        cache_path = Path(config["external_pseudo_cache"]) / basename
        # verify_pseudo_identity_closure fully validated the cache SHA/header and
        # all recorded identities before any hard link or parser invocation.
        with tempfile.TemporaryDirectory(prefix="g1_al_followup_pseudo_replay_") as temporary:
            replay_dir = Path(temporary) / "run"
            shutil.copytree(run_dir, replay_dir, copy_function=_hardlink_or_copy)
            shutil.copyfile(cache_path, replay_dir / basename)
            reparsed = reparse_new_run(
                replay_dir, config, require_followup_orchestration=require_followup_orchestration
            )
    return reparsed, closure


def verify_new_result(state_root: Path, experiment_id: str, config: dict, session: dict) -> tuple[dict, dict]:
    run_dir = state_root / "runs" / experiment_id
    accepted_identity = verify_accepted_source(
        state_root,
        experiment_id,
        session,
        require_complete_orchestration=True,
        expected_config_sha256=session["config_sha256"],
        expected_manifest_sha256=session["manifest_sha256"],
    )
    result = _read_object(run_dir / "result.json")
    reparsed, pseudo_closure = reparse_with_external_pseudo(
        run_dir, result, config, require_followup_orchestration=True
    )
    require(canonical_json_bytes(reparsed) == (run_dir / "result.json").read_bytes(), f"new raw parser replay differs: {experiment_id}")
    require(result.get("status") == "accepted" and result.get("experiment_id") == experiment_id, f"new result rejected: {experiment_id}")
    require(result.get("protocol_revision") == config["protocol_revision"], "new result protocol differs")
    require(result.get("hard_gates") and all(result["hard_gates"].values()), f"per-point hard gate failed: {experiment_id}")
    require(result["runtime_nonlocal_projectors_total"] == 18, "runtime projector count differs")
    require(result["independent_electron_identity"]["accepted"] is True, "electron identity differs")
    require(result["band_occupation"]["accepted"] is True, "band gate differs")
    require(result["cube_geometry"]["accepted"] is True, "cube geometry differs")
    require(result["cube_geometry"]["origin_exactly_zero"] is True, "cube origin gate missing")
    require(result["cube_geometry"]["maximum_origin_absolute_error_bohr"] == 0.0, "cube origin differs")
    require(result["mechanics"]["accepted"] is True, "mechanics gate differs")
    for identity in result["evidence_files"]:
        path = run_dir / identity["path"]
        require(path.is_file() and not path.is_symlink(), f"missing new evidence: {path}")
        require(path.stat().st_size == identity["size_bytes"] and sha256_file(path) == identity["sha256"], f"new evidence identity differs: {path}")
    return result, {
        **accepted_identity,
        "pseudo_identity_closure": pseudo_closure,
        "independent_raw_replay_sha256": sha256_bytes(canonical_json_bytes(reparsed)),
        "all_hard_gates_accepted": all(reparsed["hard_gates"].values()),
    }


def verify_parent_result(run_dir: Path, experiment_id: str, config: dict) -> dict:
    result = _read_object(run_dir / "result.json")
    require(result.get("status") == "accepted" and result.get("experiment_id") == experiment_id, f"parent result rejected: {experiment_id}")
    require(result["runtime_nonlocal_projectors_total"] == 18, "parent projector count differs")
    require(result["pseudo_identity"]["sha256"] == config["pseudodojo"]["materials"]["al"]["sha256"], "parent pseudo differs")
    for identity in result["evidence_files"]:
        path = run_dir / identity["path"]
        require(path.is_file() and not path.is_symlink(), f"missing parent evidence: {path}")
        require(path.stat().st_size == identity["size_bytes"] and sha256_file(path) == identity["sha256"], f"parent evidence identity differs: {path}")
    require(_read_object(run_dir / "runner_return.json").get("return_code") == 0, "parent runner return differs")
    return result


def energy(result: dict) -> float:
    return float(result["thermodynamic_labels_ev_per_atom"]["E_ec"])


def local_reference_energy(project_root: Path, experiment_id: str) -> tuple[float, dict]:
    if experiment_id == "S1-20260807-043":
        path = project_root / "runs/S1-20260807-043/result.json"
        payload = _read_object(path)
        require(payload.get("converged") is True, "local anchor 043 not converged")
        value = float(payload["zero_temp_extrapolated_energy_ev_per_atom"])
    else:
        path = project_root / f"analysis/s1/g1_displacement_strain_reference_analysis_r2_20260810/raw/{experiment_id}/analysis_result.json"
        payload = _read_object(path)
        require(payload.get("status") == "accepted", f"local strain reference rejected: {experiment_id}")
        value = float(payload["thermodynamic"]["energy_labels_ev_per_atom"]["E_ec"])
    relative = path.relative_to(project_root).as_posix()
    blob = subprocess.run(["git", "rev-parse", f"HEAD:{relative}"], cwd=project_root, check=True, text=True, stdout=subprocess.PIPE).stdout.strip()
    return value, {"path": relative, "sha256": sha256_file(path), "git_blob": blob}


def verify_geometry_bindings(project_root: Path, config: dict, new_runs_root: Path, continuation_runs_root: Path) -> list[dict]:
    rows = load_manifest(project_root)
    output: list[dict] = []
    for row in rows:
        experiment_id = row["experiment_id"]
        generated = project_root / config["input_root"] / experiment_id / "STRU"
        executed = new_runs_root / experiment_id / "STRU"
        base = project_root / row["construction_base_path"]
        reference = project_root / row["registered_geometry_path"]
        require(sha256_file(base) == row["construction_base_stru_sha256"], "construction base SHA differs")
        require(sha256_file(reference) == row["registered_geometry_stru_sha256"], "registered geometry SHA differs")
        require(generated.read_bytes() == executed.read_bytes(), "executed STRU differs from preregistered input")
        require(sha256_bytes(geometry_payload_bytes(generated.read_bytes())) == row["geometry_payload_sha256"], "generated geometry payload differs")
        if row["phase"] == "strain":
            binding = config["geometry_bindings"][experiment_id]
            construction = verify_strain_geometry(base.read_bytes(), generated.read_bytes(), reference.read_bytes(), binding["deformation_gradient"])
            complete_reference = generated.read_bytes() == reference.read_bytes()
        else:
            require(generated.read_bytes() == reference.read_bytes(), "endpoint complete STRU differs from registered geometry")
            common = continuation_runs_root / row["accepted_common_id"] / "STRU"
            require(common.read_bytes() == generated.read_bytes(), "accepted continuation common STRU differs from endpoint geometry")
            construction = {"accepted": True, "accepted_common_id": row["accepted_common_id"], "accepted_common_complete_stru_byte_identical": True}
            complete_reference = True
        output.append({
            "experiment_id": experiment_id,
            "construction_base_id": row["construction_base_id"],
            "registered_geometry_id": row["registered_geometry_id"],
            "registered_geometry_stru_sha256": row["registered_geometry_stru_sha256"],
            "geometry_payload_sha256": row["geometry_payload_sha256"],
            "complete_registered_stru_byte_identical": complete_reference,
            "independent_construction_hard_gate": construction,
        })
    return output


def evaluate_gates(new: dict[str, dict], parent: dict[str, dict], local: dict[str, float], config: dict) -> tuple[dict, list[dict]]:
    strain_limit = float(config["acceptance"]["strain_anchored_difference_mev_per_atom_max"])
    strain_rows: list[dict] = []
    gate_rows: list[dict] = []
    for new_id, local_id in STRAIN_MAP.items():
        nl_response = energy(new[new_id]) - energy(parent["S1-20260810-301"])
        local_response = local[local_id] - local["S1-20260807-043"]
        delta = (nl_response - local_response) * 1000.0
        accepted = abs(delta) <= strain_limit
        strain_rows.append({
            "experiment_id": new_id, "local_reference_id": local_id,
            "nl_anchor_id": "S1-20260810-301", "local_anchor_id": "S1-20260807-043",
            "nl_strain_response_mev_per_atom": nl_response * 1000.0,
            "local_strain_response_mev_per_atom": local_response * 1000.0,
            "anchored_scheme_and_construction_difference_mev_per_atom": delta,
            "absolute_difference_mev_per_atom": abs(delta), "maximum_allowed_mev_per_atom": strain_limit,
            "inequality": "less_than_or_equal", "accepted": accepted,
        })
        gate_rows.append({"gate": "strain", "point": new_id, "metric": "absolute_scheme_and_construction_difference_mev_per_atom", "value": abs(delta), "limit": strain_limit, "inequality": "<=", "accepted": accepted})
    k_limit = float(config["acceptance"]["endpoint_anchored_k_difference_mev_per_atom_strictly_less_than"])
    cutoff_limit = float(config["acceptance"]["endpoint_anchored_cutoff_difference_mev_per_atom_strictly_less_than"])
    pressure_limit = float(config["acceptance"]["endpoint_cutoff_pressure_difference_gpa_strictly_less_than"])
    endpoint_specs = {
        "v090": ("S1-20260810-327", "S1-20260810-339", "S1-20260810-340"),
        "v110": ("S1-20260810-328", "S1-20260810-341", "S1-20260810-342"),
    }
    endpoint_rows: list[dict] = []
    for label, (common_id, extra_id, high_id) in endpoint_specs.items():
        common_shape = energy(parent[common_id]) - energy(parent["S1-20260810-301"])
        extra_shape = energy(new[extra_id]) - energy(parent["S1-20260810-302"])
        high_shape = energy(new[high_id]) - energy(parent["S1-20260810-303"])
        k_delta = abs(extra_shape - common_shape) * 1000.0
        cutoff_delta = abs(high_shape - extra_shape) * 1000.0
        pressure_delta = abs(float(new[high_id]["pressure_gpa"]) - float(new[extra_id]["pressure_gpa"]))
        accepted = k_delta < k_limit and cutoff_delta < cutoff_limit and pressure_delta < pressure_limit
        endpoint_rows.append({
            "endpoint": label, "common_id": common_id, "normal_extra_k_id": extra_id,
            "high_extra_k_id": high_id, "v100_anchor_ids": list(ANCHOR_IDS),
            "anchored_k_difference_mev_per_atom": k_delta,
            "anchored_cutoff_difference_mev_per_atom": cutoff_delta,
            "cutoff_pressure_difference_gpa": pressure_delta, "accepted": accepted,
        })
        for metric, value, limit in (("anchored_k_difference_mev_per_atom", k_delta, k_limit), ("anchored_cutoff_difference_mev_per_atom", cutoff_delta, cutoff_limit), ("cutoff_pressure_difference_gpa", pressure_delta, pressure_limit)):
            gate_rows.append({"gate": "endpoint", "point": label, "metric": metric, "value": value, "limit": limit, "inequality": "<", "accepted": value < limit})
    accepted = all(row["accepted"] for row in strain_rows + endpoint_rows)
    return {
        "status": "accepted" if accepted else "rejected",
        "strain": {"status": "accepted" if all(row["accepted"] for row in strain_rows) else "rejected", "point_count": 4, "rows": strain_rows},
        "endpoints": {"status": "accepted" if all(row["accepted"] for row in endpoint_rows) else "rejected", "endpoint_count": 2, "rows": endpoint_rows},
    }, gate_rows


def verify_snapshot_evidence_inventory(run_dir: Path, identities: object, pseudo_closure: dict) -> list[dict]:
    require(isinstance(identities, list) and identities, "snapshot evidence inventory missing")
    verified: list[dict] = []
    seen: set[str] = set()
    for identity in identities:
        require(isinstance(identity, dict), "snapshot evidence identity must be an object")
        relative = identity.get("path")
        require(isinstance(relative, str) and relative and relative not in seen, "snapshot evidence path invalid or duplicate")
        relative_path = Path(relative)
        require(not relative_path.is_absolute() and ".." not in relative_path.parts, "snapshot evidence path escapes run")
        seen.add(relative)
        if relative_path.name.lower().endswith(".upf"):
            require(relative_path.name == pseudo_closure["basename"], "external pseudo evidence basename differs")
            require(identity.get("sha256") == pseudo_closure["sha256"], "external pseudo evidence SHA differs")
            raw_pseudo = run_dir / relative_path
            if raw_pseudo.exists():
                require(raw_pseudo.is_file() and not raw_pseudo.is_symlink(), "unsafe raw pseudo evidence")
                require(sha256_file(raw_pseudo) == identity.get("sha256"), "raw pseudo evidence SHA differs")
                size = raw_pseudo.stat().st_size
            else:
                size = Path(pseudo_closure["external_cache_path"]).stat().st_size
            require(identity.get("size_bytes") == size, "external pseudo evidence size differs")
        else:
            path = run_dir / relative_path
            require(path.is_file() and not path.is_symlink(), f"snapshot evidence missing: {path}")
            require(path.stat().st_size == identity.get("size_bytes"), f"snapshot evidence size differs: {path}")
            require(sha256_file(path) == identity.get("sha256"), f"snapshot evidence SHA differs: {path}")
        verified.append({"path": relative, "sha256": identity["sha256"], "size_bytes": identity["size_bytes"]})
    return verified


def continuation_replay_identity(run_dir: Path, result: dict, config: dict, continuation_spec: dict) -> tuple[dict, dict]:
    replay_config = json.loads(json.dumps(config))
    replay_config["protocol_revision"] = continuation_spec["protocol_revision"]
    replay_config["runtime"]["required_hostname"] = continuation_spec["required_hostname"]
    replay_config["runtime"]["physical_core_ids"] = continuation_spec["runtime_physical_core_ids"]
    replay_config["runtime"]["rank_count"] = continuation_spec["runtime_rank_count"]
    reparsed, pseudo_closure = reparse_with_external_pseudo(
        run_dir, result, replay_config, require_followup_orchestration=False
    )
    require(reparsed.get("status") == "accepted" and all(reparsed.get("hard_gates", {}).values()), "committed continuation raw replay rejected")
    for key in (
        "experiment_id", "atom_count", "expected_electrons", "runtime_nonlocal_projectors_total",
        "pseudo_identity", "thermodynamic_labels_ev_per_cell", "thermodynamic_labels_ev_per_atom",
        "pressure_kbar", "pressure_gpa",
    ):
        require(reparsed.get(key) == result.get(key), f"committed continuation stored/raw replay differs: {key}")
    return {
        "parser": "parse_s1_g1_three_layer_al_followup_r2.parse_run",
        "reparsed_sha256": sha256_bytes(canonical_json_bytes(reparsed)),
        "cube_origin_exactly_zero": reparsed["cube_geometry"]["origin_exactly_zero"],
        "stress_pressure_accepted": reparsed["mechanics"]["accepted"],
        "all_hard_gates_accepted": all(reparsed["hard_gates"].values()),
        "accepted": True,
    }, pseudo_closure


def verify_snapshot_source_identity(project_root: Path, config: dict, new_state: Path, r1_state: Path, continuation_state: Path) -> dict:
    session = _read_object(new_state / "session.json")
    frozen = session.get("parent_source_identity")
    require(isinstance(frozen, dict) and frozen.get("ready") is True, "follow-up session lacks frozen parent source identity")
    r1_session = _read_object(r1_state / "session.json")
    continuation_session = _read_object(continuation_state / "session.json")
    continuation_spec = config["source_states"]["continuation_r2"]
    barrier_path = continuation_state / continuation_spec["recovery_barrier_relative_path"]
    phase_path = continuation_state / continuation_spec["endpoint_phase_marker_relative_path"]
    barrier_payload = _read_object(barrier_path)
    phase_payload = _read_object(phase_path)
    continuation_runner = continuation_session["runner_commit"]
    require(sha256_file(barrier_path) == continuation_spec["recovery_barrier_sha256"], "snapshot barrier differs from frozen SHA")
    formalized_bytes, formalized_blob = git_file_at_commit(
        project_root,
        continuation_spec["recovery_formalization_commit"],
        continuation_spec["versioned_recovery_barrier_path"],
    )
    require(formalized_bytes == barrier_path.read_bytes(), "snapshot barrier differs from frozen formalization commit")
    versioned_bytes, versioned_blob = git_file_at_commit(project_root, continuation_runner, continuation_spec["versioned_recovery_barrier_path"])
    require(versioned_bytes == barrier_path.read_bytes(), "snapshot barrier differs from continuation runner Git blob")
    require(versioned_blob == formalized_blob, "snapshot recovery barrier Git blob changed after formalization")

    barrier_rows = barrier_payload.get("per_run_recovery")
    require(isinstance(barrier_rows, list), "snapshot recovery inventory missing")
    barrier_inventory = {
        row["experiment_id"]: row
        for row in barrier_rows
        if isinstance(row, dict) and isinstance(row.get("experiment_id"), str)
    }
    require(list(barrier_inventory) == list(RECOVERY_IDS), "snapshot recovery inventory denominator differs")
    recovered: dict[str, dict] = {}
    for experiment_id in RECOVERY_IDS:
        identity = verify_accepted_source(r1_state, experiment_id, r1_session)
        inventory = barrier_inventory[experiment_id]
        attempt_path = r1_state / "attempts" / f"{experiment_id}.json"
        require(inventory.get("attempt_sha256") == sha256_file(attempt_path), f"snapshot recovery attempt SHA differs: {experiment_id}")
        require(inventory.get("accepted_sha256") == identity["accepted_marker_sha256"], f"snapshot recovery marker SHA differs: {experiment_id}")
        require(inventory.get("accepted_result_sha256") == inventory.get("result_sha256") == identity["result_sha256"], f"snapshot recovery result SHA differs: {experiment_id}")
        require(inventory.get("runner_return_sha256") == identity["runner_return_sha256"] and inventory.get("runner_return_code") == 0, f"snapshot recovery return SHA differs: {experiment_id}")
        require(inventory.get("r1_parser_byte_exact_replay") is True, f"snapshot R1 parser replay proof differs: {experiment_id}")
        enhanced = inventory.get("enhanced_raw_gates")
        require(isinstance(enhanced, dict) and enhanced.get("accepted") is True, f"snapshot enhanced recovery gate rejected: {experiment_id}")
        result = _read_object(r1_state / "runs" / experiment_id / "result.json")
        pseudo_closure = verify_pseudo_identity_closure(
            r1_state / "runs" / experiment_id, result, config, require_run_body=False
        )
        identity["enhanced_raw_evidence"] = verify_snapshot_evidence_inventory(
            r1_state / "runs" / experiment_id, enhanced.get("evidence_files"), pseudo_closure
        )
        identity["pseudo_identity_closure"] = pseudo_closure
        recovered[experiment_id] = identity

    phase_sources: dict[str, dict] = {}
    for experiment_id in CONTINUATION_PHASE_IDS:
        identity = verify_accepted_source(continuation_state, experiment_id, continuation_session)
        result = _read_object(continuation_state / "runs" / experiment_id / "result.json")
        replay, pseudo_closure = continuation_replay_identity(
            continuation_state / "runs" / experiment_id, result, config, continuation_spec
        )
        identity["independent_raw_replay"] = replay
        identity["pseudo_identity_closure"] = pseudo_closure
        phase_sources[experiment_id] = identity
    expected_phase_results = {experiment_id: phase_sources[experiment_id]["result_sha256"] for experiment_id in CONTINUATION_PHASE_IDS}
    require(phase_payload.get("accepted_result_sha256") == expected_phase_results, "snapshot phase result-SHA map differs")
    require(phase_payload.get("session_sha256") == sha256_file(continuation_state / "session.json"), "snapshot phase/session SHA differs")
    require(phase_payload.get("config_sha256") == continuation_session.get("config_sha256") == barrier_payload.get("config_sha256"), "snapshot phase/config SHA differs")
    require(phase_payload.get("manifest_sha256") == continuation_session.get("manifest_sha256") == barrier_payload.get("manifest_sha256"), "snapshot phase/manifest SHA differs")
    require(phase_payload.get("recovery_barrier_sha256") == sha256_file(barrier_path), "snapshot phase/barrier SHA differs")
    phase_preflight = verify_continuation_phase_preflight(
        continuation_state, continuation_session, phase_payload, list(CONTINUATION_PHASE_IDS), continuation_spec
    )
    observed = {
        "ready": True,
        "r1_session_sha256": sha256_file(r1_state / "session.json"),
        "r1_recovery_barrier_sha256": sha256_file(barrier_path),
        "r1_recovery_versioned_git_blob": versioned_blob,
        "r1_recovery_formalization_commit": continuation_spec["recovery_formalization_commit"],
        "r1_recovery_formalized_git_blob": formalized_blob,
        "continuation_runner_commit": continuation_runner,
        "continuation_session_sha256": sha256_file(continuation_state / "session.json"),
        "continuation_endpoint_phase_sha256": sha256_file(phase_path),
        "continuation_endpoint_phase_accepted_result_sha256": expected_phase_results,
        "continuation_phase_preflight_identity": phase_preflight,
        "r1_recovered_sources": recovered,
        "continuation_al_eos_sources": phase_sources,
    }
    require(observed == frozen, "parent source snapshot differs from runner-frozen identity")
    return observed


def build_analysis(project_root: Path, config: dict, new_state: Path, r1_state: Path, continuation_state: Path) -> tuple[dict, list[dict]]:
    source_identity = verify_snapshot_source_identity(project_root, config, new_state, r1_state, continuation_state)
    new_runs = new_state / "runs"
    r1_runs = r1_state / "runs"
    continuation_runs = continuation_state / "runs"
    session = _read_object(new_state / "session.json")
    terminal = _read_object(new_state / "terminal.json")
    config_sha = sha256_file(project_root / "config/S1_g1_three_layer_al_domain_followup_r2.json")
    manifest_sha = sha256_file(project_root / "config/S1_g1_three_layer_al_domain_followup_r2_manifest.tsv")
    require(session.get("protocol_revision") == config["protocol_revision"], "follow-up session protocol differs")
    require(session.get("config_sha256") == config_sha and session.get("manifest_sha256") == manifest_sha, "follow-up session registered-file identity differs")
    runner_commit = session.get("runner_commit")
    require(isinstance(runner_commit, str) and len(runner_commit) == 40, "follow-up runner commit differs")
    new: dict[str, dict] = {}
    new_identities: dict[str, dict] = {}
    for experiment_id in config["formal_ids"]:
        result, identity = verify_new_result(new_state, experiment_id, config, session)
        new[experiment_id] = result
        new_identities[experiment_id] = identity
    expected_result_map = {experiment_id: new_identities[experiment_id]["result_sha256"] for experiment_id in config["formal_ids"]}
    require(terminal.get("status") == "accepted" and terminal.get("protocol_revision") == config["protocol_revision"], "follow-up terminal rejected")
    require(terminal.get("runner_commit") == runner_commit, "follow-up terminal/runner differs")
    require(terminal.get("session_sha256") == sha256_file(new_state / "session.json"), "follow-up terminal/session SHA differs")
    require(terminal.get("config_sha256") == config_sha and terminal.get("manifest_sha256") == manifest_sha, "follow-up terminal registered-file identity differs")
    require(terminal.get("accepted_ids") == config["formal_ids"] and terminal.get("accepted_count") == 8, "follow-up terminal denominator differs")
    require(terminal.get("accepted_result_sha256") == expected_result_map, "follow-up terminal result-SHA map differs")
    require(terminal.get("failed_count") == terminal.get("retried_count") == 0 and terminal.get("runner_return_code") == 0, "follow-up terminal failure/retry differs")
    parent = {experiment_id: verify_parent_result(r1_runs / experiment_id, experiment_id, config) for experiment_id in ANCHOR_IDS}
    parent.update({experiment_id: verify_parent_result(continuation_runs / experiment_id, experiment_id, config) for experiment_id in CONTINUATION_IDS})
    local: dict[str, float] = {}
    identities: list[dict] = []
    for experiment_id in ("S1-20260807-043", *STRAIN_MAP.values()):
        local[experiment_id], identity = local_reference_energy(project_root, experiment_id)
        identities.append(identity)
    gates, gate_rows = evaluate_gates(new, parent, local, config)
    geometry = verify_geometry_bindings(project_root, config, new_runs, continuation_runs)
    status = "accepted" if gates["status"] == "accepted" else "rejected"
    summary = {
        "schema_version": 1, "protocol_revision": config["protocol_revision"], "status": status,
        "scope_status": "accepted_al_domain_followup" if status == "accepted" else "rejected_al_domain_followup",
        "registered_run_count": 8, "accepted_run_count": 8, "failed_missing_skipped_retried_count": 0,
        "runner_commit": runner_commit, "all_per_point_hard_gates_accepted": all(all(result["hard_gates"].values()) for result in new.values()),
        "followup_orchestration_identities": new_identities,
        "geometry_bindings": geometry, "galileo_gates": gates,
        "parent_reference_ids": [*ANCHOR_IDS, *CONTINUATION_IDS], "parent_source_identity": source_identity,
        "legacy_local_reference_identities": identities,
        "runtime_provenance": config["runtime_provenance"],
        "pseudo_body_evidence_policy": {
            "status": "accepted_identity_closure_external_body",
            "upf_body_committed": False,
            "server_external_cache_fully_validated": True,
            "identity_bindings": ["config_repository_commit_url_git_blob_sha_header", "pseudo_identity_json", "metadata_runtime_identity", "result_pseudo_identity", "log_runtime_projector_total"],
        },
        "scientific_interpretation": "same-engine NLPP-versus-local scheme plus construction discrepancy suitability bound; not an isolated LPP scheme-bias bound",
        "semantic_limits": {
            "second_independent_KS_engine_closed": False, "D_026_QE_scope_closed": False,
            "G4_force_or_stress_closed": False, "absolute_cross_PP_energy_gate_applied": False,
            "isolated_lpp_scheme_bias_claim": False, "zero_temperature_exact_claim": False,
        },
    }
    return summary, gate_rows


def gate_tsv_bytes(rows: list[dict]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=GATE_FIELDS, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode()


def readme_bytes(summary: dict) -> bytes:
    return ("\n".join([
        "# S1/G1 Al-domain 三层补充 R2", "",
        f"状态：`{summary['scope_status']}`；8/8 新点，失败/缺失/跳过/重试为 0。", "",
        f"四个 301/043 锚定 strain 门：`{summary['galileo_gates']['strain']['status']}`；两个 327/328 端点的锚定 k/cutoff/pressure 门：`{summary['galileo_gates']['endpoints']['status']}`。", "",
        "strain 几何由 043 按 A'=A F^T 独立重建并核 det(F)=1、Direct 不变；所有 cube 原点显式等于零。", "",
        "解释边界：结果约束同引擎 NLPP/local 的 scheme+construction discrepancy 与适用性，不将其冒充孤立 LPP scheme bias；也不关闭第二 KS/QE 或 G4。",
        "",
        "运行身份：新 KS-NL 使用旧 CPU binary 2d68a57c…；它与 legacy relocated 438c74b9… 并非字节同一实现，跨存储身份仅由既有 6/6 storage-exact bridge 支持科学等价。",
    ]) + "\n").encode()


def write_analysis(output_root: Path, summary: dict, gate_rows: list[dict]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    atomic_write(output_root / "summary.json", canonical_json_bytes(summary))
    atomic_write(output_root / "gates.tsv", gate_tsv_bytes(gate_rows))
    atomic_write(output_root / "README.md", readme_bytes(summary))


def copy_exact(source: Path, destination: Path) -> None:
    require(source.is_file() and not source.is_symlink(), f"not a regular source: {source}")
    require(not destination.exists(), f"evidence destination exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    require(sha256_file(source) == sha256_file(destination), "evidence copy SHA differs")


def copy_run_snapshot(
    source_state: Path,
    destination_state: Path,
    experiment_id: str,
    *,
    include_attempt: bool,
    additional_evidence: object = None,
) -> None:
    source_run = source_state / "runs" / experiment_id
    result = _read_object(source_run / "result.json")
    inventories = [result["evidence_files"]]
    if additional_evidence is not None:
        require(isinstance(additional_evidence, list), "additional snapshot evidence must be a list")
        inventories.append(additional_evidence)
    names: set[str] = set()
    identities: dict[str, tuple[str, int]] = {}
    for inventory in inventories:
        for identity in inventory:
            require(isinstance(identity, dict), "snapshot source evidence identity differs")
            name = identity.get("path")
            require(isinstance(name, str) and name, "snapshot source evidence path missing")
            relative = Path(name)
            require(not relative.is_absolute() and ".." not in relative.parts, "snapshot source evidence path escapes run")
            expected = (identity.get("sha256"), identity.get("size_bytes"))
            require(name not in identities or identities[name] == expected, "conflicting snapshot source evidence identity")
            identities[name] = expected
            source = source_run / relative
            require(source.is_file() and not source.is_symlink(), f"snapshot source evidence missing: {source}")
            require(sha256_file(source) == expected[0] and source.stat().st_size == expected[1], f"snapshot source evidence identity differs: {source}")
            names.add(name)
    names.update({"result.json", "runner_return.json"})
    for optional in ("input_metadata.json", "pseudo_identity.json"):
        if (source_run / optional).is_file():
            names.add(optional)
    for name in sorted(names):
        if Path(name).name.lower().endswith(".upf"):
            continue
        copy_exact(source_run / name, destination_state / "runs" / experiment_id / name)
    copy_exact(source_state / "accepted" / f"{experiment_id}.json", destination_state / "accepted" / f"{experiment_id}.json")
    if include_attempt:
        copy_exact(source_state / "attempts" / f"{experiment_id}.json", destination_state / "attempts" / f"{experiment_id}.json")


def collect(project_root: Path, config: dict) -> tuple[Path, Path, Path, Path, dict, list[dict]]:
    source_new = Path(config["external_state_root"])
    terminal = _read_object(source_new / "terminal.json")
    require(terminal.get("status") == "accepted" and terminal.get("accepted_count") == 8 and terminal.get("runner_return_code") == 0, "follow-up terminal not accepted")
    source_r1 = Path(config["source_states"]["r1_p0"]["external_state_root"])
    source_cont = Path(config["source_states"]["continuation_r2"]["external_state_root"])
    external_summary, external_rows = build_analysis(project_root, config, source_new, source_r1, source_cont)
    analysis_root = project_root / config["analysis_root"]
    require(not analysis_root.exists(), "analysis root already exists")
    snapshot = analysis_root / "state_snapshot"
    new = snapshot / "followup_r2"
    r1 = snapshot / "r1_p0"
    cont = snapshot / "continuation_r2"
    for experiment_id in config["formal_ids"]:
        copy_run_snapshot(source_new, new, experiment_id, include_attempt=True)
    continuation_spec = config["source_states"]["continuation_r2"]
    barrier_payload = _read_object(source_cont / continuation_spec["recovery_barrier_relative_path"])
    recovery_inventory = {
        row["experiment_id"]: row
        for row in barrier_payload.get("per_run_recovery", [])
        if isinstance(row, dict) and isinstance(row.get("experiment_id"), str)
    }
    require(list(recovery_inventory) == list(RECOVERY_IDS), "collect recovery denominator differs")
    for experiment_id in RECOVERY_IDS:
        enhanced = recovery_inventory[experiment_id].get("enhanced_raw_gates")
        require(isinstance(enhanced, dict), f"collect enhanced recovery evidence missing: {experiment_id}")
        copy_run_snapshot(
            source_r1,
            r1,
            experiment_id,
            include_attempt=True,
            additional_evidence=enhanced.get("evidence_files"),
        )
    for experiment_id in CONTINUATION_PHASE_IDS:
        copy_run_snapshot(source_cont, cont, experiment_id, include_attempt=True)
    copy_exact(source_new / "session.json", new / "session.json")
    copy_exact(source_new / "terminal.json", new / "terminal.json")
    copy_exact(source_r1 / "session.json", r1 / "session.json")
    copy_exact(source_cont / "session.json", cont / "session.json")
    for relative in (continuation_spec["recovery_barrier_relative_path"], continuation_spec["endpoint_phase_marker_relative_path"]):
        copy_exact(source_cont / relative, cont / relative)
    for relative in (
        "preflight/al_eos_detached_launch.json",
        "preflight/al_eos_core_collision.json",
        *(f"preflight/{experiment_id}.json" for experiment_id in CONTINUATION_PHASE_IDS),
    ):
        copy_exact(source_cont / relative, cont / relative)
    return analysis_root, new, r1, cont, external_summary, external_rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--collect", action="store_true")
    parser.add_argument("--new-state-root", type=Path)
    parser.add_argument("--r1-state-root", type=Path)
    parser.add_argument("--continuation-state-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args()
    project_root = find_project_root(args.project_root)
    config = load_config(project_root)
    if args.collect:
        analysis_root, new, r1, cont, external_summary, external_rows = collect(project_root, config)
    else:
        analysis_root = args.output_root or project_root / config["analysis_root"]
        new = args.new_state_root or Path(config["external_state_root"])
        r1 = args.r1_state_root or Path(config["source_states"]["r1_p0"]["external_state_root"])
        cont = args.continuation_state_root or Path(config["source_states"]["continuation_r2"]["external_state_root"])
    summary, rows = build_analysis(project_root, config, new.resolve(), r1.resolve(), cont.resolve())
    if args.collect:
        require(canonical_json_bytes(summary) == canonical_json_bytes(external_summary), "external/snapshot summary replay differs")
        require(gate_tsv_bytes(rows) == gate_tsv_bytes(external_rows), "external/snapshot gate replay differs")
    write_analysis(analysis_root.resolve(), summary, rows)
    print(json.dumps({"status": summary["status"], "scope_status": summary["scope_status"], "output_root": str(analysis_root)}, sort_keys=True))
    return 0 if summary["status"] == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
