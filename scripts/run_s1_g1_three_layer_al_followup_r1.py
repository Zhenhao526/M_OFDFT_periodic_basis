#!/usr/bin/env python3
"""Run the eight Al follow-up IDs once, after the frozen parent references exist."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from parse_s1_g1_three_layer_al_followup_r1 import parse_run
from run_s1_g1_three_layer_r1 import runtime_environment
from s1_g1_three_layer_al_followup_common import (
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
    sha256_file,
    validate_pseudo,
)


REGISTERED_CODE = (
    "docs/S1_G1_THREE_LAYER_AL_DOMAIN_FOLLOWUP_R1_PROTOCOL.md",
    "config/S1_g1_three_layer_al_domain_followup_r1.json",
    "config/S1_g1_three_layer_al_domain_followup_r1_manifest.tsv",
    "scripts/s1_g1_three_layer_common.py",
    "scripts/generate_s1_g1_three_layer_r1.py",
    "scripts/parse_s1_g1_three_layer_r1.py",
    "scripts/run_s1_g1_three_layer_r1.py",
    "scripts/s1_g1_three_layer_rank_wrapper.py",
    "scripts/s1_g1_three_layer_al_followup_common.py",
    "scripts/generate_s1_g1_three_layer_al_followup_r1.py",
    "scripts/parse_s1_g1_three_layer_al_followup_r1.py",
    "scripts/run_s1_g1_three_layer_al_followup_r1.py",
    "scripts/analyze_s1_g1_three_layer_al_followup_r1.py",
    "scripts/validate_s1_g1_three_layer_al_followup_r1.py",
    "scripts/s1_electron_number_common.py",
    "scripts/s1_g1_thermodynamic_label_common.py",
    "tests/test_s1_g1_three_layer_al_followup_r1.py",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def registered_paths(config: dict, rows: list[dict[str, str]]) -> list[Path]:
    paths = [Path(value) for value in REGISTERED_CODE]
    input_root = Path(config["input_root"])
    for row in rows:
        paths.extend(input_root / row["experiment_id"] / name for name in ("INPUT", "STRU", "KPT", "metadata.json"))
    return paths


def parent_reference_status(config: dict) -> dict:
    spec = config["parent_three_layer_r1"]
    root = Path(spec["external_state_root"])
    session_path = root / "session.json"
    require(session_path.is_file() and not session_path.is_symlink(), "parent three-layer session missing")
    session = read_json(session_path)
    require(isinstance(session, dict), "parent session must be object")
    require(session.get("protocol_revision") == spec["protocol_revision"], "parent protocol differs")
    require(session.get("runner_commit") == spec["runner_commit"], "parent runner commit differs")
    missing: list[str] = []
    accepted: list[str] = []
    for experiment_id in spec["required_accepted_ids"]:
        marker_path = root / "accepted" / f"{experiment_id}.json"
        result_path = root / "runs" / experiment_id / "result.json"
        if not marker_path.is_file() or not result_path.is_file():
            missing.append(experiment_id)
            continue
        marker = read_json(marker_path)
        result = read_json(result_path)
        require(isinstance(marker, dict) and marker.get("status") == "accepted", f"parent marker rejected: {experiment_id}")
        require(isinstance(result, dict) and result.get("status") == "accepted", f"parent result rejected: {experiment_id}")
        require(marker.get("experiment_id") == result.get("experiment_id") == experiment_id, "parent ID differs")
        accepted.append(experiment_id)
    return {"ready": not missing, "accepted_ids": accepted, "missing_ids": missing, "session_sha256": sha256_file(session_path)}


def live_preflight(config: dict) -> dict:
    runtime = config["runtime"]
    hostname = socket.gethostname()
    require(hostname == runtime["required_hostname"], "formal run must execute on node01")
    targets = set(int(value) for value in runtime["physical_core_ids"])
    require(targets == {40, 41, 42, 43}, "follow-up core set differs")
    online = parse_cpu_list(Path("/sys/devices/system/cpu/online").read_text())
    require(targets <= online, "one or more frozen CPUs are offline")
    require(targets <= os.sched_getaffinity(0), "runner affinity excludes frozen CPUs")
    collisions: list[dict] = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            comm = (proc / "comm").read_text().strip()
            if not comm.lower().startswith("abacus"):
                continue
            status = (proc / "status").read_text()
            allowed_row = next(line for line in status.splitlines() if line.startswith("Cpus_allowed_list:"))
            allowed = parse_cpu_list(allowed_row.split(":", 1)[1])
        except (FileNotFoundError, PermissionError, StopIteration, ValueError):
            continue
        overlap = sorted(targets & allowed)
        if overlap:
            collisions.append({"pid": int(proc.name), "comm": comm, "allowed": sorted(allowed), "overlap": overlap})
    require(not collisions, f"live ABACUS affinity collision on cores 40-43: {collisions}")
    require(sha256_file(Path(runtime["binary"])) == runtime["binary_sha256"], "binary SHA differs")
    return {"hostname": hostname, "online_cpus": sorted(online), "target_physical_cores": sorted(targets), "abacus_collisions": collisions, "accepted": True}


def initialize_state(state_root: Path, project_root: Path, config: dict, head: str, parent: dict, preflight: dict) -> dict:
    require(not state_root.exists(), f"fresh external state already exists: {state_root}")
    state_root.mkdir(parents=True, mode=0o700)
    payload = {
        "schema_version": 1,
        "protocol_revision": config["protocol_revision"],
        "status": "active",
        "created_utc": utc_now(),
        "runner_commit": head,
        "branch": subprocess.run(["git", "branch", "--show-current"], cwd=project_root, check=True, text=True, stdout=subprocess.PIPE).stdout.strip(),
        "project_root": str(project_root),
        "config_sha256": sha256_file(project_root / CONFIG_PATH),
        "manifest_sha256": sha256_file(project_root / MANIFEST_PATH),
        "parent_reference_preflight": parent,
        "live_preflight": preflight,
        "retry_policy": "same_id_forbidden_new_revision_and_new_ids_only",
    }
    atomic_write(state_root / "session.json", canonical_json_bytes(payload), exclusive=True)
    return payload


def write_failure(run_dir: Path, experiment_id: str, stage: str, message: str, return_code: int | None) -> None:
    path = run_dir / "failure.json"
    if path.exists():
        return
    atomic_write(path, canonical_json_bytes({
        "schema_version": 1,
        "protocol_revision": "S1-G1-THREE-LAYER-AL-DOMAIN-FOLLOWUP-20260810-R1",
        "status": "preserved_failure_no_retry",
        "experiment_id": experiment_id,
        "stage": stage,
        "message": message,
        "runner_return_code": return_code,
        "created_utc": utc_now(),
        "retry_policy": "do_not_retry_this_id",
    }), exclusive=True)


def run_one(project_root: Path, state_root: Path, cache: Path, row: dict[str, str], config: dict, head: str) -> dict:
    experiment_id = row["experiment_id"]
    attempt_path = state_root / "attempts" / f"{experiment_id}.json"
    run_dir = state_root / "runs" / experiment_id
    require(not attempt_path.exists() and not run_dir.exists(), f"retry forbidden: {experiment_id}")
    atomic_write(attempt_path, canonical_json_bytes({
        "schema_version": 1,
        "protocol_revision": config["protocol_revision"],
        "status": "formal_attempt_started",
        "experiment_id": experiment_id,
        "created_utc": utc_now(),
        "runner_commit": head,
        "config_sha256": sha256_file(project_root / CONFIG_PATH),
        "manifest_sha256": sha256_file(project_root / MANIFEST_PATH),
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
    input_metadata = read_json(input_dir / "metadata.json")
    require(isinstance(input_metadata, dict), "input metadata must be object")
    metadata = {
        **input_metadata,
        "runner_commit": head,
        "hostname": socket.gethostname(),
        "started_utc": utc_now(),
        "runtime": {key: config["runtime"][key] for key in ("binary", "binary_sha256", "mpi", "rank_count", "physical_core_ids", "map_by")},
        "pseudo_runtime_identity": pseudo_identity,
    }
    atomic_write(run_dir / "metadata.json", canonical_json_bytes(metadata), exclusive=True)
    atomic_write(run_dir / "pseudo_identity.json", canonical_json_bytes(pseudo_identity), exclusive=True)
    affinity_dir = run_dir / "affinity"
    affinity_dir.mkdir()
    command = [
        config["runtime"]["mpi"], "--map-by", config["runtime"]["map_by"], "--bind-to", "core", "--report-bindings",
        "-np", str(config["runtime"]["rank_count"]), "/usr/bin/python3", str(project_root / "scripts/s1_g1_three_layer_rank_wrapper.py"),
        "--binary", config["runtime"]["binary"], "--evidence-dir", str(affinity_dir),
        "--expected-cores", ",".join(str(value) for value in config["runtime"]["physical_core_ids"]),
    ]
    start = time.monotonic()
    return_code: int | None = None
    stage = "solver"
    try:
        with (run_dir / "run.stdout").open("wb") as stdout, (run_dir / "run.stderr").open("wb") as stderr:
            completed = subprocess.run(command, cwd=run_dir, env=runtime_environment(config), stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, timeout=int(config["runtime"]["per_run_timeout_seconds"]), check=False)
        return_code = completed.returncode
        duration = time.monotonic() - start
        atomic_write(run_dir / "runner_return.json", canonical_json_bytes({"schema_version": 1, "experiment_id": experiment_id, "command": command, "return_code": return_code, "duration_seconds": duration, "finished_utc": utc_now()}), exclusive=True)
        require(return_code == 0, f"solver returned {return_code}")
        stage = "parser"
        result = parse_run(run_dir, config)
        atomic_write(run_dir / "result.json", canonical_json_bytes(result), exclusive=True)
        accepted = {"schema_version": 1, "protocol_revision": config["protocol_revision"], "status": "accepted", "experiment_id": experiment_id, "runner_commit": head, "duration_seconds": duration, "result_sha256": sha256_file(run_dir / "result.json"), "created_utc": utc_now()}
        atomic_write(state_root / "accepted" / f"{experiment_id}.json", canonical_json_bytes(accepted), exclusive=True)
        return accepted
    except subprocess.TimeoutExpired as error:
        write_failure(run_dir, experiment_id, "solver_timeout", str(error), None)
        raise
    except Exception as error:
        write_failure(run_dir, experiment_id, stage, str(error), return_code)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    project_root = find_project_root(args.project_root)
    config = load_config(project_root)
    rows = load_manifest(project_root)
    head = require_clean_tree(project_root)
    require_tracked_matches_head(project_root, registered_paths(config, rows))
    parent = parent_reference_status(config)
    state_root = Path(config["external_state_root"])
    if args.dry_run:
        print(json.dumps({"status": "accepted_dry_run", "solver_started": False, "formal_id_count": len(rows), "state_exists": state_root.exists(), "parent_reference": parent, "target_cores": config["runtime"]["physical_core_ids"]}, sort_keys=True))
        return 0
    require(parent["ready"], f"parent references not yet accepted: {parent['missing_ids']}")
    preflight = live_preflight(config)
    initialize_state(state_root, project_root, config, head, parent, preflight)
    cache = Path(config["external_pseudo_cache"])
    validate_pseudo(cache / config["pseudodojo"]["materials"]["al"]["basename"], "al", config)
    accepted: list[dict] = []
    for row in rows:
        print(f"START {row['experiment_id']}", flush=True)
        marker = run_one(project_root, state_root, cache, row, config, head)
        accepted.append(marker)
        print(f"ACCEPTED {row['experiment_id']} duration_seconds={marker['duration_seconds']:.3f}", flush=True)
    terminal = {"schema_version": 1, "protocol_revision": config["protocol_revision"], "status": "accepted", "runner_commit": head, "accepted_ids": [row["experiment_id"] for row in accepted], "accepted_count": len(accepted), "failed_count": 0, "retried_count": 0, "runner_return_code": 0, "created_utc": utc_now()}
    atomic_write(state_root / "terminal.json", canonical_json_bytes(terminal), exclusive=True)
    print(json.dumps(terminal, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"FATAL: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        raise
