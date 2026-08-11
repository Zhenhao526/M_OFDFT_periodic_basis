#!/usr/bin/env python3
"""Run follow-up R4 once, only after independently closed parent sources exist."""

from __future__ import annotations

import argparse
import copy
import fcntl
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import IO

from parse_s1_g1_three_layer_al_followup_r4 import parse_run
from replay_s1_g1_three_layer_r1_p0_followup_r3 import replay_r1_p0
from run_s1_g1_three_layer_r1 import runtime_environment
from s1_g1_three_layer_al_followup_r4_common import (
    CONFIG_PATH,
    MANIFEST_PATH,
    atomic_write,
    canonical_json_bytes,
    find_project_root,
    load_config,
    load_manifest,
    parse_cpu_list,
    read_json,
    require,
    require_clean_tree,
    require_tracked_matches_head,
    sha256_bytes,
    sha256_file,
    validate_pseudo,
)


REGISTERED_CODE = (
    "docs/S1_G1_THREE_LAYER_AL_DOMAIN_FOLLOWUP_R4_PROTOCOL.md",
    "config/S1_g1_three_layer_al_domain_followup_r4.json",
    "config/S1_g1_three_layer_al_domain_followup_r4_manifest.tsv",
    "config/S1_g1_three_layer_al_domain_followup_r3.json",
    "scripts/s1_g1_three_layer_common.py",
    "scripts/parse_s1_g1_three_layer_r1.py",
    "scripts/run_s1_g1_three_layer_r1.py",
    "scripts/s1_g1_three_layer_al_followup_r4_rank_wrapper.py",
    "scripts/s1_g1_three_layer_al_followup_r3_rank_wrapper.py",
    "scripts/s1_g1_three_layer_al_followup_r4_common.py",
    "scripts/generate_s1_g1_three_layer_al_followup_r4.py",
    "scripts/parse_s1_g1_three_layer_al_followup_r4.py",
    "scripts/replay_s1_g1_three_layer_al_followup_r4_parser_regression.py",
    "scripts/replay_s1_g1_three_layer_r1_p0_followup_r3.py",
    "scripts/run_s1_g1_three_layer_al_followup_r4.py",
    "scripts/run_s1_g1_three_layer_al_followup_r4_binding_smoke.py",
    "scripts/analyze_s1_g1_three_layer_al_followup_r4.py",
    "scripts/validate_s1_g1_three_layer_al_followup_r4.py",
    "scripts/s1_electron_number_common.py",
    "scripts/s1_g1_thermodynamic_label_common.py",
    "tests/test_s1_g1_three_layer_al_followup_r4.py",
)

SMOKE_EXECUTION_CODE = (
    "scripts/run_s1_g1_three_layer_al_followup_r4_binding_smoke.py",
    "scripts/run_s1_g1_three_layer_al_followup_r4.py",
    "scripts/s1_g1_three_layer_al_followup_r4_rank_wrapper.py",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def registered_paths(config: dict, rows: list[dict[str, str]]) -> list[Path]:
    paths = [Path(value) for value in REGISTERED_CODE]
    input_root = Path(config["input_root"])
    for row in rows:
        paths.extend(input_root / row["experiment_id"] / name for name in ("INPUT", "STRU", "KPT", "metadata.json"))
    return paths


def expected_preregistration_diff_paths(config: dict) -> list[str]:
    return sorted([
        "config/S1_g1_three_layer_al_domain_followup_r4.json",
        "docs/S1_G1_THREE_LAYER_AL_DOMAIN_FOLLOWUP_R4_PROTOCOL.md",
        *(f"{config['input_root']}/{experiment_id}/metadata.json" for experiment_id in config["formal_ids"]),
    ])


def preregistration_identity(project_root: Path, config: dict, head: str) -> dict:
    implementation = config["implementation_commit"]
    require(isinstance(implementation, str) and len(implementation) == 40, "implementation commit identity differs")
    parent_row = subprocess.run(
        ["git", "rev-list", "--parents", "-n", "1", head], cwd=project_root, check=True,
        text=True, stdout=subprocess.PIPE,
    ).stdout.split()
    require(parent_row == [head, implementation], "preregistration must have implementation as its unique direct parent")
    parent = parent_row[1]
    changed = sorted(subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", head],
        cwd=project_root, check=True, text=True, stdout=subprocess.PIPE,
    ).stdout.splitlines())
    require(changed == expected_preregistration_diff_paths(config), "preregistration changed paths outside the exact frozen diff")
    execution_code: list[dict] = []
    for relative in SMOKE_EXECUTION_CODE:
        path = project_root / relative
        require(path.is_file() and not path.is_symlink(), f"smoke execution code missing: {relative}")
        prereg_blob = subprocess.run(
            ["git", "rev-parse", f"{head}:{relative}"], cwd=project_root, check=True,
            text=True, stdout=subprocess.PIPE,
        ).stdout.strip()
        implementation_blob = subprocess.run(
            ["git", "rev-parse", f"{implementation}:{relative}"], cwd=project_root, check=True,
            text=True, stdout=subprocess.PIPE,
        ).stdout.strip()
        require(prereg_blob == implementation_blob, f"smoke execution code changed after implementation: {relative}")
        execution_code.append({
            "path": relative,
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
            "preregistration_git_blob": prereg_blob,
            "implementation_git_blob": implementation_blob,
        })
    return {
        "accepted": True,
        "preregistration_commit": head,
        "implementation_commit": implementation,
        "direct_parent_commit": parent,
        "exact_changed_paths": changed,
        "execution_code_identities": execution_code,
    }


def evidence_only_formalization_identity(project_root: Path, head: str, preregistration_commit: str, evidence_path: str) -> dict:
    parent_row = subprocess.run(
        ["git", "rev-list", "--parents", "-n", "1", head], cwd=project_root,
        check=True, text=True, stdout=subprocess.PIPE,
    ).stdout.split()
    require(parent_row == [head, preregistration_commit], "smoke formalization must have preregistration as its unique parent")
    changed = sorted(subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", head],
        cwd=project_root, check=True, text=True, stdout=subprocess.PIPE,
    ).stdout.splitlines())
    require(changed == [evidence_path], "smoke formalization changed paths outside the exact aggregate evidence")
    git_blob = subprocess.run(
        ["git", "rev-parse", f"{head}:{evidence_path}"], cwd=project_root,
        check=True, text=True, stdout=subprocess.PIPE,
    ).stdout.strip()
    return {
        "accepted": True,
        "formalization_commit": head,
        "preregistration_commit": preregistration_commit,
        "unique_parent_commit": parent_row[1],
        "exact_changed_paths": changed,
        "evidence_git_blob": git_blob,
    }


def _object(path: Path, label: str) -> dict:
    require(path.is_file() and not path.is_symlink(), f"{label} missing: {path}")
    payload = read_json(path)
    require(isinstance(payload, dict), f"{label} must be a JSON object")
    return payload


def verify_result_evidence(run_dir: Path, result: dict) -> list[dict]:
    identities = result.get("evidence_files")
    require(isinstance(identities, list) and identities, "source result evidence inventory missing")
    seen: set[str] = set()
    verified: list[dict] = []
    for identity in identities:
        require(isinstance(identity, dict), "source evidence identity must be an object")
        relative = identity.get("path")
        require(isinstance(relative, str) and relative and relative not in seen, "source evidence path invalid or duplicate")
        seen.add(relative)
        path = run_dir / relative
        require(path.is_file() and not path.is_symlink(), f"source result evidence missing: {path}")
        require(path.stat().st_size == identity.get("size_bytes"), f"source evidence size differs: {path}")
        digest = sha256_file(path)
        require(digest == identity.get("sha256"), f"source evidence SHA differs: {path}")
        verified.append({"path": relative, "sha256": digest, "size_bytes": path.stat().st_size})
    return verified


def verify_accepted_source(
    state_root: Path,
    experiment_id: str,
    session: dict,
    *,
    require_complete_orchestration: bool = False,
    expected_config_sha256: str | None = None,
    expected_manifest_sha256: str | None = None,
) -> dict:
    marker_path = state_root / "accepted" / f"{experiment_id}.json"
    run_dir = state_root / "runs" / experiment_id
    result_path = run_dir / "result.json"
    return_path = run_dir / "runner_return.json"
    marker = _object(marker_path, "accepted marker")
    result = _object(result_path, "source result")
    runner_return = _object(return_path, "source runner return")
    require(marker.get("status") == "accepted", f"source marker rejected: {experiment_id}")
    require(marker.get("experiment_id") == result.get("experiment_id") == runner_return.get("experiment_id") == experiment_id, "source ID binding differs")
    require(marker.get("protocol_revision") == result.get("protocol_revision") == session.get("protocol_revision"), "source protocol binding differs")
    require(marker.get("runner_commit") == session.get("runner_commit"), "source runner/session binding differs")
    require(result.get("status") == "accepted", f"source result rejected: {experiment_id}")
    require(runner_return.get("return_code") == 0, f"source runner return rejected: {experiment_id}")
    result_sha = sha256_file(result_path)
    require(marker.get("result_sha256") == result_sha, f"source marker/result SHA binding differs: {experiment_id}")
    evidence = verify_result_evidence(run_dir, result)
    identity = {
        "experiment_id": experiment_id,
        "accepted_marker_sha256": sha256_file(marker_path),
        "result_sha256": result_sha,
        "runner_return_sha256": sha256_file(return_path),
        "evidence": evidence,
        "evidence_count": len(evidence),
        "accepted": True,
    }
    if require_complete_orchestration:
        require(isinstance(expected_config_sha256, str) and len(expected_config_sha256) == 64, "expected config SHA missing")
        require(isinstance(expected_manifest_sha256, str) and len(expected_manifest_sha256) == 64, "expected manifest SHA missing")
        runner_commit = session.get("runner_commit")
        protocol = session.get("protocol_revision")
        require(session.get("config_sha256") == expected_config_sha256, "session/config SHA binding differs")
        require(session.get("manifest_sha256") == expected_manifest_sha256, "session/manifest SHA binding differs")
        attempt_path = state_root / "attempts" / f"{experiment_id}.json"
        attempt = _object(attempt_path, "attempt marker")
        metadata = _object(run_dir / "metadata.json", "source metadata")
        orchestration = result.get("orchestration_identity")
        require(isinstance(orchestration, dict), "result orchestration identity missing")
        for payload, label in ((attempt, "attempt"), (marker, "accepted marker"), (runner_return, "runner return"), (metadata, "metadata"), (orchestration, "result")):
            require(payload.get("experiment_id") == experiment_id, f"{label}/ID binding differs")
            require(payload.get("protocol_revision") == protocol, f"{label}/protocol binding differs")
            require(payload.get("runner_commit") == runner_commit, f"{label}/runner binding differs")
            require(payload.get("config_sha256") == expected_config_sha256, f"{label}/config binding differs")
            require(payload.get("manifest_sha256") == expected_manifest_sha256, f"{label}/manifest binding differs")
        require(attempt.get("status") == "formal_attempt_started", "attempt status differs")
        identity["attempt_marker_sha256"] = sha256_file(attempt_path)
        identity["metadata_sha256"] = sha256_file(run_dir / "metadata.json")
        identity["orchestration_identity"] = orchestration
    return identity


def verify_pseudo_identity_closure(run_dir: Path, result: dict, config: dict, *, require_run_body: bool) -> dict:
    """Cross-bind upstream identity, recorded headers, runtime count, and external cache.

    UPF bodies are deliberately excluded from committed evidence.  A committed
    replay is accepted only on the server where the immutable external cache
    still passes the full header/projector validator.
    """
    material = result.get("material")
    require(material in config["pseudodojo"]["materials"], "pseudo material contract missing")
    expected = config["pseudodojo"]["materials"][material]
    basename = expected["basename"]
    element_dir = {"al": "Al", "mg": "Mg"}[material]
    canonical_url = (
        f"https://raw.githubusercontent.com/{config['pseudodojo']['repository']}/"
        f"{config['pseudodojo']['commit']}/{element_dir}/{basename}"
    )
    require(expected["url"] == canonical_url, "pseudo upstream URL/repository/commit binding differs")
    require(isinstance(expected.get("git_blob_sha1"), str) and len(expected["git_blob_sha1"]) == 40, "pseudo Git blob identity missing")
    metadata = _object(run_dir / "metadata.json", "pseudo metadata")
    input_identity = metadata.get("pseudo")
    require(isinstance(input_identity, dict), "input pseudo identity missing")
    require(input_identity.get("basename") == basename, "input pseudo basename differs")
    require(input_identity.get("sha256") == expected["sha256"], "input pseudo SHA differs")
    require(input_identity.get("upstream_commit") == config["pseudodojo"]["commit"], "input pseudo upstream commit differs")
    require(input_identity.get("upstream_url") == expected["url"], "input pseudo upstream URL differs")
    recorded_path = run_dir / "pseudo_identity.json"
    recorded = _object(recorded_path, "recorded pseudo identity")
    runtime_recorded = metadata.get("pseudo_runtime_identity")
    require(isinstance(runtime_recorded, dict), "metadata runtime pseudo identity missing")
    require(recorded == runtime_recorded == result.get("pseudo_identity"), "recorded/result/runtime pseudo identity differs")
    cache_path = Path(config["external_pseudo_cache"]) / basename
    cache_identity = validate_pseudo(cache_path, material, config)
    require(recorded == cache_identity, "recorded pseudo identity differs from validated external cache")
    raw_path = run_dir / basename
    if require_run_body:
        require(raw_path.is_file() and not raw_path.is_symlink(), "raw run pseudo body missing")
    if raw_path.exists():
        require(not raw_path.is_symlink(), "raw run pseudo body must not be a symlink")
        require(validate_pseudo(raw_path, material, config) == cache_identity, "raw run pseudo differs from external cache")
    atom_count = int(result["atom_count"])
    expected_total = int(recorded["expanded_nonlocal_projectors_per_atom"]) * atom_count
    require(result.get("runtime_nonlocal_projectors_total") == expected_total, "log runtime projector count differs from pseudo identity")
    return {
        "material": material,
        "repository": config["pseudodojo"]["repository"],
        "upstream_commit": config["pseudodojo"]["commit"],
        "upstream_url": expected["url"],
        "git_blob_sha1": expected["git_blob_sha1"],
        "basename": basename,
        "sha256": expected["sha256"],
        "header_identity": cache_identity,
        "runtime_nonlocal_projectors_total": expected_total,
        "pseudo_identity_json_sha256": sha256_file(recorded_path),
        "external_cache_path": str(cache_path),
        "external_cache_verified_sha256": sha256_file(cache_path),
        "committed_body_policy": "deliberately_external_identity_only_due_to_repository_redistribution_policy",
        "accepted": True,
    }


def verify_continuation_phase_preflight(
    continuation_root: Path,
    continuation_session: dict,
    phase: dict,
    accepted_ids: list[str],
    continuation_spec: dict,
) -> dict:
    detached_path = continuation_root / "preflight/al_eos_detached_launch.json"
    phase_core_path = continuation_root / "preflight/al_eos_core_collision.json"
    detached_sha = sha256_file(detached_path)
    phase_core_sha = sha256_file(phase_core_path)
    require(phase.get("detached_launcher_proof_sha256") == detached_sha, "continuation detached-launch proof SHA differs")
    require(phase.get("phase_core_collision_preflight_sha256") == phase_core_sha, "continuation phase core-preflight SHA differs")
    require(continuation_session.get("initial_detached_launcher_proof_sha256") == detached_sha, "continuation session/detached proof differs")
    require(continuation_session.get("initial_core_collision_preflight_sha256") == phase_core_sha, "continuation session/core preflight differs")
    detached = _object(detached_path, "continuation detached-launch proof")
    require(detached.get("accepted") is True and detached.get("session_leader") is True, "continuation detached launch rejected")
    require(detached.get("sighup_ignored") is True, "continuation SIGHUP proof rejected")
    isatty = detached.get("isatty")
    require(isinstance(isatty, dict) and all(isatty.get(str(fd)) is False for fd in (0, 1, 2)), "continuation TTY detachment proof rejected")
    phase_core = _object(phase_core_path, "continuation phase core-collision preflight")
    require(phase_core.get("accepted") is True and phase_core.get("collisions") == [], "continuation phase core collision preflight rejected")
    require(phase_core.get("hostname") == continuation_spec["required_hostname"], "continuation phase core-preflight hostname differs")
    require(phase_core.get("physical_socket_id") == continuation_spec["runtime_physical_socket_id"], "continuation phase core-preflight socket differs")
    require(phase_core.get("physical_core_ids") == continuation_spec["runtime_physical_core_ids"], "continuation phase core-preflight physical cores differ")
    require(phase_core.get("target_sibling_logical_cpus") == continuation_spec["runtime_reserved_logical_cpu_ids"], "continuation phase core-preflight logical/SMT domain differs")
    result_map = phase.get("per_run_core_collision_preflight_sha256")
    require(isinstance(result_map, dict) and list(result_map) == accepted_ids, "continuation per-run core-preflight denominator differs")
    per_run: dict[str, dict] = {}
    for experiment_id in accepted_ids:
        path = continuation_root / "preflight" / f"{experiment_id}.json"
        digest = sha256_file(path)
        require(result_map.get(experiment_id) == digest, f"continuation per-run core-preflight SHA differs: {experiment_id}")
        payload = _object(path, "continuation per-run core-collision preflight")
        require(payload.get("accepted") is True and payload.get("collisions") == [], f"continuation per-run core collision rejected: {experiment_id}")
        require(payload.get("hostname") == continuation_spec["required_hostname"], f"continuation per-run core-preflight hostname differs: {experiment_id}")
        require(payload.get("physical_socket_id") == continuation_spec["runtime_physical_socket_id"], f"continuation per-run core-preflight socket differs: {experiment_id}")
        require(payload.get("physical_core_ids") == continuation_spec["runtime_physical_core_ids"], f"continuation per-run core-preflight physical cores differ: {experiment_id}")
        require(payload.get("target_sibling_logical_cpus") == continuation_spec["runtime_reserved_logical_cpu_ids"], f"continuation per-run core-preflight logical/SMT domain differs: {experiment_id}")
        per_run[experiment_id] = {"path": f"preflight/{experiment_id}.json", "sha256": digest}
    return {
        "detached": {"path": "preflight/al_eos_detached_launch.json", "sha256": detached_sha},
        "phase_core_collision": {"path": "preflight/al_eos_core_collision.json", "sha256": phase_core_sha},
        "per_run_core_collision": per_run,
        "accepted": True,
    }


def replay_continuation_al_raw(run_dir: Path, stored: dict, config: dict, continuation_spec: dict) -> dict:
    replay_config = copy.deepcopy(config)
    replay_config["protocol_revision"] = continuation_spec["protocol_revision"]
    replay_config["runtime"]["required_hostname"] = continuation_spec["required_hostname"]
    replay_config["runtime"]["physical_core_ids"] = continuation_spec["runtime_physical_core_ids"]
    replay_config["runtime"]["rank_count"] = continuation_spec["runtime_rank_count"]
    reparsed = parse_run(run_dir, replay_config, require_followup_orchestration=False)
    require(reparsed.get("status") == "accepted" and all(reparsed.get("hard_gates", {}).values()), "independent continuation raw replay rejected")
    for key in (
        "experiment_id", "atom_count", "expected_electrons", "runtime_nonlocal_projectors_total",
        "pseudo_identity", "thermodynamic_labels_ev_per_cell", "thermodynamic_labels_ev_per_atom",
        "pressure_kbar", "pressure_gpa",
    ):
        require(reparsed.get(key) == stored.get(key), f"continuation stored/independent replay differs: {key}")
    return {
        "parser": "parse_s1_g1_three_layer_al_followup_r4.parse_run",
        "reparsed_sha256": sha256_bytes(canonical_json_bytes(reparsed)),
        "cube_origin_exactly_zero": reparsed["cube_geometry"]["origin_exactly_zero"],
        "stress_pressure_accepted": reparsed["mechanics"]["accepted"],
        "all_hard_gates_accepted": all(reparsed["hard_gates"].values()),
        "accepted": True,
    }


def _inventory_map(payload: object) -> dict[str, dict]:
    require(isinstance(payload, list), "recovery accepted inventory must be a list")
    output: dict[str, dict] = {}
    for row in payload:
        require(isinstance(row, dict) and isinstance(row.get("experiment_id"), str), "recovery inventory row invalid")
        experiment_id = row["experiment_id"]
        require(experiment_id not in output, "duplicate recovery inventory ID")
        output[experiment_id] = row
    return output


def git_file_at_commit(project_root: Path, commit: str, relative: str) -> tuple[bytes, str]:
    completed = subprocess.run(
        ["git", "show", f"{commit}:{relative}"], cwd=project_root, check=False,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    require(completed.returncode == 0, f"source Git file unavailable: {commit}:{relative}")
    blob = subprocess.run(
        ["git", "rev-parse", f"{commit}:{relative}"], cwd=project_root, check=True,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout.strip()
    return completed.stdout, blob


def require_git_ancestor(project_root: Path, ancestor: str, descendant: str) -> None:
    require(len(ancestor) == len(descendant) == 40, "source commit identity differs")
    completed = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant], cwd=project_root,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    require(completed.returncode == 0, f"source preregistration is not ancestor: {ancestor} -> {descendant}")


def committed_json(project_root: Path, commit: str, relative: str) -> tuple[dict, str, bytes]:
    data, blob = git_file_at_commit(project_root, commit, relative)
    payload = json.loads(data)
    require(isinstance(payload, dict), f"committed JSON root differs: {relative}")
    return payload, blob, data


def verify_continuation_source_orchestration(
    state_root: Path,
    experiment_id: str,
    session: dict,
    project_root: Path,
    continuation_spec: dict,
    continuation_config: dict,
) -> dict:
    runner_commit = session["runner_commit"]
    attempt_path = state_root / "attempts" / f"{experiment_id}.json"
    run_dir = state_root / "runs" / experiment_id
    attempt = _object(attempt_path, "continuation attempt marker")
    metadata = _object(run_dir / "metadata.json", "continuation metadata")
    input_metadata = _object(run_dir / "input_metadata.json", "continuation input metadata")
    require(attempt.get("status") == "formal_attempt_started" and attempt.get("experiment_id") == experiment_id, "continuation attempt identity differs")
    require(attempt.get("protocol_revision") == session["protocol_revision"], "continuation attempt protocol differs")
    require(attempt.get("runner_commit") == runner_commit, "continuation attempt runner differs")
    require(attempt.get("config_sha256") == session["config_sha256"], "continuation attempt config differs")
    require(attempt.get("manifest_sha256") == session["manifest_sha256"], "continuation attempt manifest differs")
    require(metadata.get("experiment_id") == experiment_id and metadata.get("protocol_revision") == session["protocol_revision"], "continuation metadata identity differs")
    require(metadata.get("runner_commit") == runner_commit, "continuation metadata runner differs")
    registered_identity = input_metadata.get("input_identity")
    require(isinstance(registered_identity, dict), "continuation input registered identity missing")
    require(registered_identity.get("config_sha256") == session["config_sha256"], "continuation input/config SHA binding differs")
    require(registered_identity.get("manifest_sha256") == session["manifest_sha256"], "continuation input/manifest SHA binding differs")
    for key, value in input_metadata.items():
        require(metadata.get(key) == value, f"continuation metadata/input metadata differs: {experiment_id}/{key}")
    preflight_path = state_root / "preflight" / f"{experiment_id}.json"
    require(attempt.get("core_collision_preflight_sha256") == sha256_file(preflight_path), "continuation attempt/core preflight binding differs")
    input_identities: list[dict] = []
    input_root = continuation_config["input_root"]
    for name in ("INPUT", "STRU", "KPT", "metadata.json"):
        relative = f"{input_root}/{experiment_id}/{name}"
        committed, blob = git_file_at_commit(project_root, runner_commit, relative)
        actual = run_dir / ("input_metadata.json" if name == "metadata.json" else name)
        require(actual.read_bytes() == committed, f"continuation executed/committed input differs: {experiment_id}/{name}")
        if name != "metadata.json":
            require(registered_identity.get(f"{name}_sha256") == sha256_bytes(committed), f"continuation registered input SHA differs: {experiment_id}/{name}")
        input_identities.append({"path": relative, "git_blob": blob, "sha256": sha256_bytes(committed), "size_bytes": len(committed)})
    return {
        "attempt_marker_sha256": sha256_file(attempt_path),
        "metadata_sha256": sha256_file(run_dir / "metadata.json"),
        "input_metadata_sha256": sha256_file(run_dir / "input_metadata.json"),
        "config_sha256": session["config_sha256"],
        "manifest_sha256": session["manifest_sha256"],
        "committed_input_identities": input_identities,
        "core_collision_preflight_sha256": sha256_file(preflight_path),
        "accepted": True,
    }


def verify_parent_sources(config: dict, project_root: Path | None = None, rows: list[dict[str, str]] | None = None) -> dict:
    sources = config["source_states"]
    old_spec = sources["r1_p0"]
    old_root = Path(old_spec["external_state_root"])
    old_session_path = old_root / "session.json"
    old_session = _object(old_session_path, "R1 P0 session")
    require(old_session.get("protocol_revision") == old_spec["protocol_revision"], "R1 P0 protocol differs")
    require(old_session.get("runner_commit") == old_spec["runner_commit"], "R1 P0 runner commit differs")
    old_session_sha = sha256_file(old_session_path)
    require(old_session_sha == old_spec["session_sha256"], "R1 P0 session SHA differs")

    continuation_spec = sources["continuation_r2"]
    continuation_root = Path(continuation_spec["external_state_root"])
    continuation_session_path = continuation_root / "session.json"
    continuation_session = _object(continuation_session_path, "continuation session")
    require(continuation_session.get("protocol_revision") == continuation_spec["protocol_revision"], "continuation protocol differs")
    continuation_runner = continuation_session.get("runner_commit")
    require(isinstance(continuation_runner, str) and len(continuation_runner) == 40, "continuation runner commit differs")
    continuation_config: dict | None = None
    r1_config: dict | None = None
    if project_root is not None:
        require_git_ancestor(project_root, continuation_spec["preregistration_commit"], continuation_runner)
        require_git_ancestor(project_root, continuation_spec["preregistration_commit"], continuation_spec["recovery_formalization_commit"])
        require_git_ancestor(project_root, continuation_spec["recovery_formalization_commit"], continuation_runner)
        continuation_config, _, continuation_config_bytes = committed_json(
            project_root, continuation_runner, continuation_spec["config_path"]
        )
        require(sha256_bytes(continuation_config_bytes) == continuation_session.get("config_sha256"), "continuation committed config/session SHA differs")
    require(continuation_session.get("recovery_prereg_commit") == continuation_spec["preregistration_commit"], "continuation recovery prereg binding differs")
    require(continuation_session.get("recovery_source_runner_commit") == old_spec["runner_commit"], "continuation recovery source runner differs")

    barrier_path = continuation_root / continuation_spec["recovery_barrier_relative_path"]
    barrier = _object(barrier_path, "independent R1 P0 recovery barrier")
    require(barrier.get("status") == "accepted", "independent R1 P0 recovery rejected")
    require(barrier.get("protocol_revision") == continuation_spec["protocol_revision"], "recovery barrier protocol differs")
    barrier_sha = sha256_file(barrier_path)
    require(barrier_sha == continuation_spec["recovery_barrier_sha256"], "frozen continuation recovery barrier SHA differs")
    require(continuation_session.get("recovery_barrier_sha256") == barrier_sha, "continuation session/recovery barrier SHA differs")
    require(barrier.get("continuation_prereg_commit") == continuation_spec["preregistration_commit"], "recovery barrier prereg binding differs")
    require(barrier.get("source_state_root") == str(old_root), "recovery source state differs")
    require(barrier.get("source_session_sha256") == old_session_sha, "recovery source session SHA differs")
    require(barrier.get("source_runner_commit") == old_spec["runner_commit"], "recovery source runner differs")
    require(barrier.get("source_operational_status") == old_spec["operational_status"], "R1 operational closure semantics differ")
    require(barrier.get("source_operational_phase_accepted") is False, "R1 operational phase overclaim")
    require(barrier.get("scientific_p0_recovery_status") == "accepted", "independent scientific P0 recovery gate rejected")
    require(barrier.get("accepted_source_ids") == continuation_spec["required_recovery_ids"], "recovery source ID set/order differs")
    require(barrier.get("accepted_source_count") == len(continuation_spec["required_recovery_ids"]), "recovery source count differs")
    require(barrier.get("new_run_count") == 0, "recovery evidence was miscounted as new runs")
    require(barrier.get("permanently_unexecuted_source_ids") == old_spec["permanently_unexecuted_ids"], "R1 unexecuted denominator differs")
    require(barrier.get("source_snapshot") == old_spec["source_snapshot"], "R1 source snapshot differs")
    require(isinstance(barrier.get("p0_metrics"), dict) and barrier["p0_metrics"].get("status") == "accepted", "recovered P0 metrics rejected")
    require(barrier.get("scope", {}).get("r1_phase_marker_reconstructed") is False, "R1 phase marker reconstruction overclaim")
    barrier_inventory = _inventory_map(barrier.get("per_run_recovery"))

    if project_root is not None:
        formalized_bytes, formalized_blob = git_file_at_commit(
            project_root,
            continuation_spec["recovery_formalization_commit"],
            continuation_spec["versioned_recovery_barrier_path"],
        )
        require(formalized_bytes == barrier_path.read_bytes(), "external recovery barrier differs from frozen formalization commit")
        versioned_bytes, versioned_blob = git_file_at_commit(project_root, continuation_runner, continuation_spec["versioned_recovery_barrier_path"])
        require(versioned_bytes == barrier_path.read_bytes(), "external/versioned recovery barrier differs")
        require(versioned_blob == formalized_blob, "recovery barrier Git blob changed after formalization")
        for relative, key in ((continuation_spec["config_path"], "config_sha256"), (continuation_spec["manifest_path"], "manifest_sha256")):
            committed, _ = git_file_at_commit(project_root, continuation_runner, relative)
            digest = sha256_bytes(committed)
            require(digest == barrier.get(key) == continuation_session.get(key), f"continuation {key} binding differs")
        source_git_identities = barrier.get("source_git_identities")
        require(isinstance(source_git_identities, list) and [item.get("path") for item in source_git_identities if isinstance(item, dict)] == old_spec["source_git_paths"], "recovery source Git identity denominator differs")
        for identity in source_git_identities:
            require(isinstance(identity, dict), "recovery source Git identity differs")
            committed, blob = git_file_at_commit(project_root, identity.get("commit", ""), identity.get("path", ""))
            require(blob == identity.get("git_blob_oid"), "recovery source Git blob differs")
            require(sha256_bytes(committed) == identity.get("sha256") and len(committed) == identity.get("size_bytes"), "recovery source Git bytes differ")
        r1_config_path = old_spec["source_git_paths"][0]
        r1_config, _, r1_config_bytes = committed_json(project_root, old_spec["runner_commit"], r1_config_path)
        r1_config_identity = next(identity for identity in source_git_identities if identity["path"] == r1_config_path)
        require(sha256_bytes(r1_config_bytes) == r1_config_identity["sha256"], "R1 committed config/recovery identity differs")
    else:
        versioned_blob = "not_checked_without_project_root"
        formalized_blob = "not_checked_without_project_root"

    recovered: dict[str, dict] = {}
    for experiment_id in old_spec["required_accepted_ids"]:
        identity = verify_accepted_source(old_root, experiment_id, old_session)
        inventory = barrier_inventory.get(experiment_id)
        require(inventory is not None, f"recovery inventory lacks {experiment_id}")
        require(inventory.get("status") == "accepted_source_evidence", f"recovery status differs: {experiment_id}")
        require(inventory.get("accepted_sha256") == identity["accepted_marker_sha256"], f"recovery/source accepted marker differs: {experiment_id}")
        require(inventory.get("accepted_result_sha256") == inventory.get("result_sha256") == identity["result_sha256"], f"recovery/source result differs: {experiment_id}")
        require(inventory.get("runner_return_sha256") == identity["runner_return_sha256"] and inventory.get("runner_return_code") == 0, f"recovery/source runner return differs: {experiment_id}")
        require(inventory.get("attempt_sha256") == sha256_file(old_root / "attempts" / f"{experiment_id}.json"), f"recovery/source attempt differs: {experiment_id}")
        require(inventory.get("r1_parser_byte_exact_replay") is True, f"R1 parser replay differs: {experiment_id}")
        enhanced = inventory.get("enhanced_raw_gates")
        require(isinstance(enhanced, dict) and enhanced.get("accepted") is True, f"enhanced recovery gates rejected: {experiment_id}")
        require(enhanced.get("affinity", {}).get("accepted") is True, f"recovery affinity rejected: {experiment_id}")
        require(enhanced.get("cube_geometry", {}).get("accepted") is True, f"recovery cube geometry rejected: {experiment_id}")
        require(enhanced.get("stress_trace_gate", {}).get("accepted") is True, f"recovery stress/pressure rejected: {experiment_id}")
        require(enhanced.get("eig_occupations", {}).get("accepted") is True, f"recovery eig occupation rejected: {experiment_id}")
        enhanced_evidence = verify_result_evidence(old_root / "runs" / experiment_id, enhanced)
        result = _object(old_root / "runs" / experiment_id / "result.json", "R1 recovered result")
        identity["enhanced_raw_evidence"] = enhanced_evidence
        identity["pseudo_identity_closure"] = verify_pseudo_identity_closure(
            old_root / "runs" / experiment_id, result, config, require_run_body=True
        )
        if project_root is not None:
            require(r1_config is not None and continuation_config is not None, "committed R1 replay configs unavailable")
            base_reparsed, enhanced_reparsed, replay_identity = replay_r1_p0(
                old_root / "runs" / experiment_id, r1_config, continuation_config
            )
            require(canonical_json_bytes(base_reparsed) == (old_root / "runs" / experiment_id / "result.json").read_bytes(), f"R1 base committed replay differs: {experiment_id}")
            require(canonical_json_bytes(enhanced_reparsed) == canonical_json_bytes(enhanced), f"R1 enhanced committed replay differs: {experiment_id}")
            identity["committed_raw_replay"] = replay_identity
        else:
            identity["committed_raw_replay"] = {"accepted": True, "not_checked_without_project_root": True}
        if experiment_id in {"S1-20260810-301", "S1-20260810-302", "S1-20260810-303"}:
            require(result.get("runtime_nonlocal_projectors_total") == 18, "R1 Al anchor projector count differs")
            require(result.get("pseudo_identity", {}).get("sha256") == config["pseudodojo"]["materials"]["al"]["sha256"], "R1 Al anchor pseudo differs")
        recovered[experiment_id] = identity

    phase_path = continuation_root / continuation_spec["endpoint_phase_marker_relative_path"]
    phase = _object(phase_path, "continuation endpoint phase closure")
    require(phase.get("status") == "accepted", "continuation endpoint phase rejected")
    require(phase.get("protocol_revision") == continuation_spec["protocol_revision"], "continuation phase protocol differs")
    require(phase.get("runner_commit") == continuation_session.get("runner_commit"), "continuation phase runner/session differs")
    require(phase.get("phase") == continuation_spec["endpoint_phase"], "continuation endpoint phase name differs")
    require(phase.get("accepted_ids") == continuation_spec["endpoint_phase_accepted_ids"], "continuation endpoint phase ID set/order differs")
    require(phase.get("accepted_count") == len(continuation_spec["endpoint_phase_accepted_ids"]), "continuation endpoint phase count differs")
    require(phase.get("session_sha256") == sha256_file(continuation_session_path), "continuation phase/session SHA binding differs")
    require(phase.get("config_sha256") == continuation_session.get("config_sha256") == barrier.get("config_sha256"), "continuation phase/config SHA binding differs")
    require(phase.get("manifest_sha256") == continuation_session.get("manifest_sha256") == barrier.get("manifest_sha256"), "continuation phase/manifest SHA binding differs")
    require(phase.get("recovery_barrier_sha256") == continuation_session.get("recovery_barrier_sha256") == barrier_sha, "continuation phase/recovery barrier binding differs")
    phase_sources = {experiment_id: verify_accepted_source(continuation_root, experiment_id, continuation_session) for experiment_id in continuation_spec["endpoint_phase_accepted_ids"]}
    expected_phase_results = {experiment_id: phase_sources[experiment_id]["result_sha256"] for experiment_id in continuation_spec["endpoint_phase_accepted_ids"]}
    require(phase.get("accepted_result_sha256") == expected_phase_results, "continuation phase accepted-result SHA map differs")
    phase_preflight = verify_continuation_phase_preflight(
        continuation_root,
        continuation_session,
        phase,
        continuation_spec["endpoint_phase_accepted_ids"],
        continuation_spec,
    )
    for experiment_id in continuation_spec["endpoint_phase_accepted_ids"]:
        result = _object(continuation_root / "runs" / experiment_id / "result.json", "continuation Al EOS result")
        require(result.get("runtime_nonlocal_projectors_total") == 18, "continuation Al EOS projector count differs")
        require(result.get("pseudo_identity", {}).get("sha256") == config["pseudodojo"]["materials"]["al"]["sha256"], "continuation Al EOS pseudo differs")
        phase_sources[experiment_id]["independent_raw_replay"] = replay_continuation_al_raw(
            continuation_root / "runs" / experiment_id, result, config, continuation_spec
        )
        phase_sources[experiment_id]["pseudo_identity_closure"] = verify_pseudo_identity_closure(
            continuation_root / "runs" / experiment_id, result, config, require_run_body=True
        )
        if project_root is not None:
            require(continuation_config is not None, "committed continuation config unavailable")
            phase_sources[experiment_id]["orchestration_identity"] = verify_continuation_source_orchestration(
                continuation_root,
                experiment_id,
                continuation_session,
                project_root,
                continuation_spec,
                continuation_config,
            )
        else:
            phase_sources[experiment_id]["orchestration_identity"] = {"accepted": True, "not_checked_without_project_root": True}
    if project_root is not None or rows is not None:
        require(project_root is not None and rows is not None, "project root and manifest rows must be supplied together")
        checked: set[str] = set()
        for row in rows:
            common_id = row["accepted_common_id"]
            if not common_id or common_id in checked:
                continue
            checked.add(common_id)
            registered = project_root / row["registered_geometry_path"]
            actual = continuation_root / "runs" / common_id / "STRU"
            require(sha256_file(registered) == row["registered_geometry_stru_sha256"], "registered endpoint geometry SHA differs")
            require(actual.read_bytes() == registered.read_bytes(), f"accepted continuation geometry differs: {common_id}")
    return {
        "ready": True,
        "r1_session_sha256": old_session_sha,
        "r1_recovery_barrier_sha256": barrier_sha,
        "r1_recovery_versioned_git_blob": versioned_blob,
        "r1_recovery_formalization_commit": continuation_spec["recovery_formalization_commit"],
        "r1_recovery_formalized_git_blob": formalized_blob,
        "continuation_runner_commit": continuation_runner,
        "continuation_session_sha256": sha256_file(continuation_session_path),
        "continuation_endpoint_phase_sha256": sha256_file(phase_path),
        "continuation_endpoint_phase_accepted_result_sha256": expected_phase_results,
        "continuation_phase_preflight_identity": phase_preflight,
        "r1_recovered_sources": recovered,
        "continuation_al_eos_sources": phase_sources,
    }


def validate_core_reservation_ack(path: Path, config: dict) -> dict:
    ack = _object(path, "core reservation ACK")
    runtime = config["runtime"]
    require(ack.get("schema_version") == 1, "core reservation ACK schema differs")
    require(ack.get("status") == "exclusive_core_reservation_acknowledged", "core reservation ACK rejected")
    require(ack.get("protocol_revision") == config["protocol_revision"], "core reservation ACK protocol differs")
    require(ack.get("hostname") == runtime["required_hostname"], "core reservation ACK host differs")
    require(ack.get("physical_package_id") == runtime["required_physical_package_id"], "core reservation ACK package differs")
    require(ack.get("primary_os_logical_cpu_ids_by_rank") == runtime["primary_os_logical_cpu_ids_by_rank"], "core reservation ACK primary OS CPUs differ")
    require(ack.get("sysfs_core_id_by_rank") == runtime["sysfs_core_id_by_rank"], "core reservation ACK sysfs core_id map differs")
    require(ack.get("thread_siblings_by_rank") == runtime["thread_siblings_by_rank"], "core reservation ACK sibling map differs")
    require(ack.get("reserved_os_logical_cpu_ids") == runtime["reserved_os_logical_cpu_ids"], "core reservation ACK OS logical/SMT domain differs")
    require(ack.get("conflicting_workflows_checked") is True, "core collision coordination not acknowledged")
    require(ack.get("single_runner_exclusive_use") is True, "exclusive core use not acknowledged")
    require(isinstance(ack.get("acknowledged_by"), str) and ack["acknowledged_by"].strip(), "core reservation acknowledger missing")
    return {"path": str(path.resolve()), "sha256": sha256_file(path), "payload": ack, "accepted": True}


def acquire_core_locks(config: dict) -> tuple[list[IO[bytes]], list[dict]]:
    runtime = config["runtime"]
    lock_root = Path(runtime["core_lock_root"])
    lock_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    handles: list[IO[bytes]] = []
    identities: list[dict] = []
    try:
        for cpu in runtime["reserved_os_logical_cpu_ids"]:
            path = lock_root / f"{runtime['required_hostname']}_logical_cpu_{cpu}.lock"
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags, 0o600)
            handle = os.fdopen(descriptor, "a+b")
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                handle.close()
                raise ValueError(f"exclusive core lock unavailable: {path}") from error
            handles.append(handle)
            stat = os.fstat(handle.fileno())
            identities.append({
                "path": str(path), "logical_cpu_id": cpu, "advisory_lock": "exclusive_nonblocking",
                "device": stat.st_dev, "inode": stat.st_ino, "owner_pid": os.getpid(), "acquired": True,
            })
    except Exception:
        for handle in handles:
            handle.close()
        raise
    return handles, identities


def _live_topology(logical_cpu: int) -> dict:
    root = Path(f"/sys/devices/system/cpu/cpu{logical_cpu}/topology")
    return {
        "os_logical_cpu_id": logical_cpu,
        "physical_package_id": int((root / "physical_package_id").read_text().strip()),
        "sysfs_core_id": int((root / "core_id").read_text().strip()),
        "thread_siblings": sorted(parse_cpu_list((root / "thread_siblings_list").read_text())),
    }


def live_preflight(config: dict) -> dict:
    runtime = config["runtime"]
    hostname = socket.gethostname()
    require(hostname == runtime["required_hostname"], "formal run must execute on node01")
    primary = runtime["primary_os_logical_cpu_ids_by_rank"]
    core_ids = runtime["sysfs_core_id_by_rank"]
    sibling_rows = runtime["thread_siblings_by_rank"]
    require(primary == [30, 31, 32, 33], "frozen primary OS CPU list differs")
    require(core_ids == [30, 31, 32, 33], "frozen /sys core_id list differs")
    require(sibling_rows == [[30, 106], [31, 107], [32, 108], [33, 109]], "frozen sibling map differs")
    require(runtime["required_physical_package_id"] == 0, "frozen physical package differs")
    reserved_logical = set(runtime["reserved_os_logical_cpu_ids"])
    require(sorted(reserved_logical) == [30, 31, 32, 33, 106, 107, 108, 109], "frozen OS logical reservation differs")
    online = parse_cpu_list(Path("/sys/devices/system/cpu/online").read_text())
    require(reserved_logical <= online, "one or more frozen OS CPUs are offline")
    require(set(primary) <= os.sched_getaffinity(0), "runner affinity excludes frozen primary CPUs")
    topology_by_rank: list[list[dict]] = []
    for rank, siblings in enumerate(sibling_rows):
        topology = [_live_topology(cpu) for cpu in siblings]
        require(all(row["physical_package_id"] == runtime["required_physical_package_id"] for row in topology), f"rank {rank} live package differs")
        require(all(row["sysfs_core_id"] == core_ids[rank] for row in topology), f"rank {rank} live sysfs core_id differs")
        require(all(row["thread_siblings"] == siblings for row in topology), f"rank {rank} live siblings differ")
        topology_by_rank.append(topology)
    collisions: list[dict] = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            comm = (proc / "comm").read_text().strip()
            cmdline = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
            lowered_comm = comm.lower()
            lowered_cmdline = cmdline.lower()
            is_solver = "abacus" in lowered_comm
            is_bound_rank_wrapper = "three_layer" in lowered_cmdline and "rank_wrapper.py" in lowered_cmdline and "--evidence-dir" in lowered_cmdline
            if not (is_solver or is_bound_rank_wrapper):
                continue
            status = (proc / "status").read_text()
            allowed_row = next(line for line in status.splitlines() if line.startswith("Cpus_allowed_list:"))
            allowed = parse_cpu_list(allowed_row.split(":", 1)[1])
        except (FileNotFoundError, PermissionError, StopIteration, ValueError):
            continue
        overlap = sorted(reserved_logical & allowed)
        if overlap:
            collisions.append({"pid": int(proc.name), "comm": comm, "kind": "solver" if is_solver else "bound_rank_wrapper", "allowed": sorted(allowed), "overlap": overlap})
    require(not collisions, f"live solver/rank-wrapper collision on reserved OS CPUs: {collisions}")
    require(sha256_file(Path(runtime["binary"])) == runtime["binary_sha256"], "binary SHA differs")
    return {
        "hostname": hostname, "online_os_logical_cpus": sorted(online),
        "required_physical_package_id": runtime["required_physical_package_id"],
        "primary_os_logical_cpu_ids_by_rank": primary,
        "sysfs_core_id_by_rank": core_ids,
        "thread_siblings_by_rank": sibling_rows,
        "reserved_os_logical_cpus": sorted(reserved_logical),
        "topology_by_rank": topology_by_rank,
        "collision_scan_scope": "live ABACUS solver comm plus three-layer rank-wrapper command lines; complete reserved OS logical/SMT domain",
        "abacus_collisions": collisions, "accepted": True,
    }


def binding_command(project_root: Path, config: dict, evidence_dir: Path, *, mode: str) -> list[str]:
    require(mode in {"smoke", "solver"}, "invalid binding-command mode")
    command = [
        config["runtime"]["mpi"], "--map-by", config["runtime"]["map_by"], "--bind-to", "core", "--report-bindings",
        "-np", str(config["runtime"]["rank_count"]), "/usr/bin/python3",
        str(project_root / "scripts/s1_g1_three_layer_al_followup_r4_rank_wrapper.py"),
        "--mode", mode, "--config", str(project_root / CONFIG_PATH),
        "--config-sha256", sha256_file(project_root / CONFIG_PATH), "--evidence-dir", str(evidence_dir),
    ]
    if mode == "solver":
        command.extend(["--binary", config["runtime"]["binary"]])
    return command


def verify_binding_smoke(config: dict, project_root: Path, head: str) -> dict:
    spec = config["binding_smoke"]
    external_root = Path(spec["external_root"])
    external = external_root / spec["external_aggregate_relative_path"]
    versioned = project_root / spec["versioned_aggregate_path"]
    aggregate = _object(external, "external binding-smoke aggregate")
    require(versioned.is_file() and not versioned.is_symlink(), "versioned binding-smoke aggregate missing")
    require(external.read_bytes() == versioned.read_bytes(), "external/versioned binding-smoke bytes differ")
    require(aggregate.get("status") == "accepted" and aggregate.get("protocol_revision") == config["protocol_revision"], "binding smoke not accepted")
    require(aggregate.get("implementation_commit") == config["implementation_commit"], "binding-smoke implementation differs")
    require(aggregate.get("config_sha256") == sha256_file(project_root / CONFIG_PATH), "binding-smoke config SHA differs")
    require(aggregate.get("manifest_sha256") == sha256_file(project_root / MANIFEST_PATH), "binding-smoke manifest SHA differs")
    require(aggregate.get("formal_state_absent") is True and aggregate.get("formal_attempt_count") == 0, "binding smoke contaminated formal state")
    require(aggregate.get("runner_return_code") == 0 and aggregate.get("rank_count") == 4 and aggregate.get("failed_rank_count") == 0 and aggregate.get("abacus_exec_count") == 0, "binding-smoke denominator differs")
    prereg = aggregate.get("preregistration_commit")
    require(isinstance(prereg, str) and len(prereg) == 40, "binding-smoke preregistration commit missing")
    formalization = evidence_only_formalization_identity(project_root, head, prereg, spec["versioned_aggregate_path"])
    require(aggregate.get("preregistration_identity") == preregistration_identity(project_root, config, prereg), "binding-smoke preregistration/code identity differs")
    committed = subprocess.run(["git", "show", f"{head}:{spec['versioned_aggregate_path']}"], cwd=project_root, check=True, stdout=subprocess.PIPE).stdout
    require(committed == external.read_bytes(), "committed binding-smoke bytes differ")
    require(aggregate.get("command") == binding_command(project_root, config, external_root / "ranks", mode="smoke"), "binding-smoke command differs from formal mapper/wrapper")
    rank_rows = aggregate.get("rank_evidence")
    require(isinstance(rank_rows, list) and len(rank_rows) == 4, "binding-smoke rank evidence denominator differs")
    for rank, identity in enumerate(rank_rows):
        path = external_root / identity["path"]
        require(path.is_file() and not path.is_symlink(), f"binding-smoke rank {rank} raw missing")
        require(path.stat().st_size == identity["size_bytes"] and sha256_file(path) == identity["sha256"], f"binding-smoke rank {rank} raw identity differs")
        require(read_json(path) == identity["payload"], f"binding-smoke rank {rank} payload differs")
        payload = identity["payload"]
        require(payload.get("os_logical_cpu_affinity") == config["runtime"]["thread_siblings_by_rank"][rank], f"binding-smoke rank {rank} OS affinity differs")
        require(payload.get("physical_package_id") == config["runtime"]["required_physical_package_id"], f"binding-smoke rank {rank} package differs")
        require(payload.get("sysfs_core_id") == config["runtime"]["sysfs_core_id_by_rank"][rank], f"binding-smoke rank {rank} sysfs core_id differs")
        require("physical_core_ids" not in payload and "expected_physical_core_id" not in payload, f"binding-smoke rank {rank} contains an ambiguous physical-core label")
        require(payload.get("config_sha256") == sha256_file(project_root / CONFIG_PATH), f"binding-smoke rank {rank} config SHA differs")
        require(payload.get("rank_wrapper_sha256") == sha256_file(project_root / "scripts/s1_g1_three_layer_al_followup_r4_rank_wrapper.py"), f"binding-smoke rank {rank} wrapper SHA differs")
    for name in ("stdout", "stderr"):
        path = external_root / f"run.{name}"
        require(sha256_file(path) == aggregate[f"{name}_sha256"], f"binding-smoke {name} SHA differs")
    detached = aggregate.get("detached_runtime_proof", {})
    require(detached.get("accepted") is True and detached.get("session_leader") is True and detached.get("sighup_disposition") == "ignored" and not any(detached.get("stdio_isatty", {}).values()), "binding-smoke detached proof differs")
    return {"accepted": True, "aggregate_sha256": sha256_file(external), "preregistration_commit": prereg, "formalization_commit": head, "formalization_identity": formalization, "rank_count": 4, "abacus_exec_count": 0}


def verify_r2_operational_failure_closure(config: dict, project_root: Path, head: str) -> dict:
    relative = Path(config["r2_operational_failure_closure_path"])
    path = project_root / relative
    closure = _object(path, "R2 operational failure closure")
    require(sha256_file(path) == config["r2_operational_failure_closure_sha256"], "R2 closure SHA differs")
    require(closure.get("operational_status") == "failed_binding_contract_preserved_no_retry", "R2 closure status differs")
    require(closure.get("unique_attempt_ids") == ["S1-20260810-335"] and closure.get("unique_attempt_count") == 1, "R2 attempt denominator differs")
    require(closure.get("accepted_count") == 0 and closure.get("abacus_exec_count") == 0 and closure.get("scientific_contribution_count") == 0, "R2 zero-contribution closure differs")
    require(closure.get("unattempted_ids") == [f"S1-20260810-{number}" for number in range(336, 343)], "R2 unattempted IDs differ")
    require(closure.get("preregistration_and_runner_commit") == config["r2_preregistration_and_runner_commit"], "R2 runner closure differs")
    require_git_ancestor(project_root, config["r2_preregistration_and_runner_commit"], config["r2_operational_failure_closure_commit"])
    require_git_ancestor(project_root, config["r2_operational_failure_closure_commit"], head)
    committed = subprocess.run(["git", "show", f"{config['r2_operational_failure_closure_commit']}:{relative.as_posix()}"], cwd=project_root, check=True, stdout=subprocess.PIPE).stdout
    require(committed == path.read_bytes(), "R2 closure differs from closure commit")
    state_root = Path(closure["external_paths"]["state_root"])
    files = sorted(path for path in state_root.rglob("*") if path.is_file() and not path.is_symlink())
    digest_rows = "".join(f"{sha256_file(path)}  {path.relative_to(state_root).as_posix()}\n" for path in files).encode()
    snapshot = closure["state_snapshot"]
    require(len(files) == snapshot["file_count"] and sum(path.stat().st_size for path in files) == snapshot["regular_file_bytes"], "R2 state snapshot denominator differs")
    require(sha256_bytes(digest_rows) == snapshot["relative_sha256sum_list_digest"], "R2 state snapshot digest differs")
    require(not (state_root / "terminal.json").exists() and not (state_root / "accepted").exists(), "R2 terminal/accepted overclaim")
    return {"accepted": True, "closure_commit": config["r2_operational_failure_closure_commit"], "closure_sha256": sha256_file(path), "attempt_ids": ["S1-20260810-335"], "accepted_count": 0, "abacus_exec_count": 0}


def verify_r3_operational_failure_closure(config: dict, project_root: Path, head: str) -> dict:
    relative = Path(config["r3_operational_failure_closure_path"])
    path = project_root / relative
    closure = _object(path, "R3 operational failure closure")
    require(sha256_file(path) == config["r3_operational_failure_closure_sha256"], "R3 closure SHA differs")
    require(closure.get("operational_status") == "failed_parser_schema_preserved_no_retry", "R3 closure status differs")
    require(closure.get("runner_commit") == config["r3_preregistration_and_runner_commit"], "R3 runner closure differs")
    require(closure.get("unique_attempt_ids") == ["S1-20260810-343"] and closure.get("unique_attempt_count") == 1, "R3 attempt denominator differs")
    require(closure.get("solver_start_count") == closure.get("solver_return_zero_count") == closure.get("parser_failure_count") == 1, "R3 solver/parser denominator differs")
    require(closure.get("accepted_count") == 0 and closure.get("scientific_contribution_count") == 0, "R3 zero-contribution closure differs")
    require(closure.get("unattempted_ids") == [f"S1-20260810-{number}" for number in range(344, 351)], "R3 unattempted IDs differ")
    require(closure.get("terminal_marker_present") is False, "R3 terminal overclaim")
    require_git_ancestor(project_root, config["r3_preregistration_and_runner_commit"], config["r3_operational_failure_closure_commit"])
    require_git_ancestor(project_root, config["r3_operational_failure_closure_commit"], head)
    committed = subprocess.run(
        ["git", "show", f"{config['r3_operational_failure_closure_commit']}:{relative.as_posix()}"],
        cwd=project_root, check=True, stdout=subprocess.PIPE,
    ).stdout
    require(committed == path.read_bytes(), "R3 closure differs from closure commit")
    state_root = Path(closure["external_state_root"])
    files = sorted(value for value in state_root.rglob("*") if value.is_file() and not value.is_symlink())
    digest_rows = "".join(f"{sha256_file(value)}  {value.relative_to(state_root).as_posix()}\n" for value in files).encode()
    snapshot = closure["state_snapshot"]
    require(len(files) == snapshot["file_count"] and sum(value.stat().st_size for value in files) == snapshot["regular_file_bytes"], "R3 state snapshot denominator differs")
    require(sha256_bytes(digest_rows) == snapshot["relative_sha256sum_list_digest"], "R3 state snapshot digest differs")
    require(not (state_root / "terminal.json").exists() and not (state_root / "accepted").exists(), "R3 terminal/accepted overclaim")
    require(sorted(value.stem for value in (state_root / "attempts").glob("*.json")) == ["S1-20260810-343"], "R3 attempt tree differs")
    require(sorted(value.name for value in (state_root / "runs").iterdir()) == ["S1-20260810-343"], "R3 run tree differs")
    raw_metadata = _object(state_root / "runs/S1-20260810-343/metadata.json", "R3 raw metadata")
    require("requirement" not in raw_metadata and raw_metadata.get("role") == "al_tetragonal_plus", "R3 raw metadata failure premise differs")
    raw = closure["executed_raw_identity"]
    require(sha256_file(state_root / "runs/S1-20260810-343/metadata.json") == raw["metadata_sha256"], "R3 raw metadata SHA differs")
    return {
        "accepted": True,
        "closure_commit": config["r3_operational_failure_closure_commit"],
        "closure_sha256": sha256_file(path),
        "attempt_ids": ["S1-20260810-343"],
        "accepted_count": 0,
        "scientific_contribution_count": 0,
    }


def detached_runtime_proof() -> dict:
    pid = os.getpid()
    sid = os.getsid(0)
    require(signal.getsignal(signal.SIGHUP) == signal.SIG_IGN, "formal runner must ignore SIGHUP")
    require(sid == pid, "formal runner must be launched as a detached session leader (setsid)")
    tty = {str(fd): os.isatty(fd) for fd in (0, 1, 2)}
    require(not any(tty.values()), "formal runner stdio must be detached from a terminal")
    return {
        "pid": pid, "parent_pid": os.getppid(), "session_id": sid, "process_group_id": os.getpgrp(),
        "sighup_disposition": "ignored", "stdio_isatty": tty,
        "session_leader": True, "accepted": True,
    }


def initialize_state(state_root: Path, project_root: Path, config: dict, head: str, parents: dict, smoke: dict, ack: dict, locks: list[dict], preflight: dict, detached: dict) -> dict:
    require(not state_root.exists(), f"fresh external state already exists: {state_root}")
    state_root.mkdir(parents=True, mode=0o700)
    payload = {
        "schema_version": 1, "protocol_revision": config["protocol_revision"], "status": "active",
        "created_utc": utc_now(), "runner_commit": head,
        "branch": subprocess.run(["git", "branch", "--show-current"], cwd=project_root, check=True, text=True, stdout=subprocess.PIPE).stdout.strip(),
        "project_root": str(project_root), "config_sha256": sha256_file(project_root / CONFIG_PATH),
        "manifest_sha256": sha256_file(project_root / MANIFEST_PATH), "parent_source_identity": parents,
        "binding_smoke_identity": smoke,
        "core_reservation_ack": ack, "core_locks": locks, "live_preflight": preflight,
        "detached_runtime_proof": detached,
        "retry_policy": "same_id_forbidden_new_revision_and_new_ids_only",
    }
    atomic_write(state_root / "session.json", canonical_json_bytes(payload), exclusive=True)
    return payload


def write_failure(run_dir: Path, experiment_id: str, stage: str, message: str, return_code: int | None) -> None:
    path = run_dir / "failure.json"
    if path.exists():
        return
    atomic_write(path, canonical_json_bytes({
        "schema_version": 1, "protocol_revision": "S1-G1-THREE-LAYER-AL-DOMAIN-FOLLOWUP-20260810-R4",
        "status": "preserved_failure_no_retry", "experiment_id": experiment_id, "stage": stage,
        "message": message, "runner_return_code": return_code, "created_utc": utc_now(),
        "retry_policy": "do_not_retry_this_id",
    }), exclusive=True)


def run_one(project_root: Path, state_root: Path, cache: Path, row: dict[str, str], config: dict, head: str, case_preflight: dict) -> dict:
    experiment_id = row["experiment_id"]
    config_sha = sha256_file(project_root / CONFIG_PATH)
    manifest_sha = sha256_file(project_root / MANIFEST_PATH)
    attempt_path = state_root / "attempts" / f"{experiment_id}.json"
    run_dir = state_root / "runs" / experiment_id
    require(not attempt_path.exists() and not run_dir.exists(), f"retry forbidden: {experiment_id}")
    atomic_write(attempt_path, canonical_json_bytes({
        "schema_version": 1, "protocol_revision": config["protocol_revision"], "status": "formal_attempt_started",
        "experiment_id": experiment_id, "created_utc": utc_now(), "runner_commit": head,
        "config_sha256": config_sha, "manifest_sha256": manifest_sha,
        "case_live_preflight": case_preflight,
        "retry_policy": "same_id_forbidden_new_revision_and_new_ids_only",
    }), exclusive=True)
    run_dir.mkdir(parents=True)
    input_dir = project_root / config["input_root"] / experiment_id
    for name in ("INPUT", "STRU", "KPT"):
        shutil.copyfile(input_dir / name, run_dir / name)
    shutil.copyfile(input_dir / "metadata.json", run_dir / "input_metadata.json")
    pseudo_source = cache / row["pseudo_basename"]
    pseudo_identity = validate_pseudo(pseudo_source, "al", config)
    shutil.copyfile(pseudo_source, run_dir / row["pseudo_basename"])
    input_metadata = _object(input_dir / "metadata.json", "input metadata")
    metadata = {
        **input_metadata, "protocol_revision": config["protocol_revision"], "runner_commit": head,
        "config_sha256": config_sha, "manifest_sha256": manifest_sha,
        "orchestration_identity": {
            "protocol_revision": config["protocol_revision"], "experiment_id": experiment_id,
            "runner_commit": head, "config_sha256": config_sha, "manifest_sha256": manifest_sha,
        },
        "hostname": socket.gethostname(), "started_utc": utc_now(),
        "case_live_preflight": case_preflight,
        "runtime": {key: config["runtime"][key] for key in ("binary", "binary_sha256", "mpi", "rank_count", "required_physical_package_id", "primary_os_logical_cpu_ids_by_rank", "sysfs_core_id_by_rank", "thread_siblings_by_rank", "reserved_os_logical_cpu_ids", "map_by")},
        "pseudo_runtime_identity": pseudo_identity,
    }
    atomic_write(run_dir / "metadata.json", canonical_json_bytes(metadata), exclusive=True)
    atomic_write(run_dir / "pseudo_identity.json", canonical_json_bytes(pseudo_identity), exclusive=True)
    affinity_dir = run_dir / "affinity"
    affinity_dir.mkdir()
    command = binding_command(project_root, config, affinity_dir, mode="solver")
    start = time.monotonic()
    return_code: int | None = None
    stage = "solver"
    try:
        with (run_dir / "run.stdout").open("wb") as stdout, (run_dir / "run.stderr").open("wb") as stderr:
            completed = subprocess.run(command, cwd=run_dir, env=runtime_environment(config), stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, timeout=int(config["runtime"]["per_run_timeout_seconds"]), check=False)
        return_code = completed.returncode
        duration = time.monotonic() - start
        atomic_write(run_dir / "runner_return.json", canonical_json_bytes({
            "schema_version": 1, "protocol_revision": config["protocol_revision"],
            "experiment_id": experiment_id, "runner_commit": head,
            "config_sha256": config_sha, "manifest_sha256": manifest_sha,
            "command": command, "return_code": return_code,
            "duration_seconds": duration, "finished_utc": utc_now(),
        }), exclusive=True)
        require(return_code == 0, f"solver returned {return_code}")
        stage = "parser"
        result = parse_run(run_dir, config)
        atomic_write(run_dir / "result.json", canonical_json_bytes(result), exclusive=True)
        accepted = {
            "schema_version": 1, "protocol_revision": config["protocol_revision"], "status": "accepted",
            "experiment_id": experiment_id, "runner_commit": head,
            "config_sha256": config_sha, "manifest_sha256": manifest_sha,
            "duration_seconds": duration, "result_sha256": sha256_file(run_dir / "result.json"),
            "created_utc": utc_now(),
        }
        atomic_write(state_root / "accepted" / f"{experiment_id}.json", canonical_json_bytes(accepted), exclusive=True)
        return accepted
    except subprocess.TimeoutExpired as error:
        write_failure(run_dir, experiment_id, "solver_timeout", str(error), None)
        raise
    except Exception as error:
        write_failure(run_dir, experiment_id, stage, str(error), return_code)
        raise


def main() -> int:
    # Imported after this module is initialized because the regression analyzer
    # reuses the runner's committed-source verifiers.
    from replay_s1_g1_three_layer_al_followup_r4_parser_regression import validate_fixture

    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--core-reservation-ack", type=Path)
    args = parser.parse_args()
    project_root = find_project_root(args.project_root)
    config = load_config(project_root)
    rows = load_manifest(project_root)
    head = require_clean_tree(project_root)
    require_tracked_matches_head(project_root, registered_paths(config, rows))
    r2_closure = verify_r2_operational_failure_closure(config, project_root, head)
    r3_closure = verify_r3_operational_failure_closure(config, project_root, head)
    parser_regression = validate_fixture(project_root, config)
    state_root = Path(config["external_state_root"])
    if args.dry_run:
        parent_error: str | None = None
        try:
            parent = verify_parent_sources(config, project_root, rows)
        except Exception as error:
            parent = {"ready": False}
            parent_error = f"{type(error).__name__}: {error}"
        smoke_error: str | None = None
        try:
            smoke = verify_binding_smoke(config, project_root, head)
        except Exception as error:
            smoke = {"accepted": False}
            smoke_error = f"{type(error).__name__}: {error}"
        print(json.dumps({"status": "accepted_dry_run", "solver_started": False, "formal_id_count": len(rows), "state_exists": state_root.exists(), "r2_operational_failure_closure": r2_closure, "r3_operational_failure_closure": r3_closure, "r3_parser_regression": parser_regression, "parent_source": parent, "parent_not_ready_reason": parent_error, "binding_smoke": smoke, "binding_smoke_not_ready_reason": smoke_error, "reservation_ack_supplied": args.core_reservation_ack is not None, "primary_os_logical_cpu_ids_by_rank": config["runtime"]["primary_os_logical_cpu_ids_by_rank"]}, sort_keys=True))
        return 0
    require(args.core_reservation_ack is not None, "formal run requires --core-reservation-ack")
    parents = verify_parent_sources(config, project_root, rows)
    parents["r2_operational_failure_closure"] = r2_closure
    parents["r3_operational_failure_closure"] = r3_closure
    parents["r3_parser_regression"] = parser_regression
    smoke = verify_binding_smoke(config, project_root, head)
    ack = validate_core_reservation_ack(args.core_reservation_ack, config)
    detached = detached_runtime_proof()
    handles, locks = acquire_core_locks(config)
    try:
        preflight = live_preflight(config)
        initialize_state(state_root, project_root, config, head, parents, smoke, ack, locks, preflight, detached)
        cache = Path(config["external_pseudo_cache"])
        validate_pseudo(cache / config["pseudodojo"]["materials"]["al"]["basename"], "al", config)
        accepted: list[dict] = []
        for row in rows:
            case_preflight = live_preflight(config)
            print(f"START {row['experiment_id']}", flush=True)
            marker = run_one(project_root, state_root, cache, row, config, head, case_preflight)
            accepted.append(marker)
            print(f"ACCEPTED {row['experiment_id']} duration_seconds={marker['duration_seconds']:.3f}", flush=True)
        accepted_ids = [marker["experiment_id"] for marker in accepted]
        require(accepted_ids == config["formal_ids"], "terminal accepted denominator differs")
        terminal = {
            "schema_version": 1, "protocol_revision": config["protocol_revision"], "status": "accepted",
            "runner_commit": head, "session_sha256": sha256_file(state_root / "session.json"),
            "config_sha256": sha256_file(project_root / CONFIG_PATH),
            "manifest_sha256": sha256_file(project_root / MANIFEST_PATH),
            "attempted_ids": accepted_ids, "attempted_count": len(accepted_ids),
            "accepted_ids": accepted_ids,
            "accepted_result_sha256": {marker["experiment_id"]: marker["result_sha256"] for marker in accepted},
            "attempt_marker_sha256": {
                experiment_id: sha256_file(state_root / "attempts" / f"{experiment_id}.json")
                for experiment_id in accepted_ids
            },
            "accepted_marker_sha256": {
                experiment_id: sha256_file(state_root / "accepted" / f"{experiment_id}.json")
                for experiment_id in accepted_ids
            },
            "runner_return_sha256": {
                experiment_id: sha256_file(state_root / "runs" / experiment_id / "runner_return.json")
                for experiment_id in accepted_ids
            },
            "result_sha256": {
                experiment_id: sha256_file(state_root / "runs" / experiment_id / "result.json")
                for experiment_id in accepted_ids
            },
            "metadata_sha256": {
                experiment_id: sha256_file(state_root / "runs" / experiment_id / "metadata.json")
                for experiment_id in accepted_ids
            },
            "accepted_count": len(accepted), "failed_count": 0, "retried_count": 0,
            "runner_return_code": 0, "created_utc": utc_now(),
        }
        atomic_write(state_root / "terminal.json", canonical_json_bytes(terminal), exclusive=True)
        print(json.dumps(terminal, sort_keys=True))
    finally:
        for handle in handles:
            handle.close()
    return 0


if __name__ == "__main__":
    try:
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        raise SystemExit(main())
    except Exception as error:
        print(f"FATAL: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        raise
