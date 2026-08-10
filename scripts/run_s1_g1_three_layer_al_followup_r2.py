#!/usr/bin/env python3
"""Run follow-up R2 once, only after independently closed parent sources exist."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import IO

from parse_s1_g1_three_layer_al_followup_r2 import parse_run
from run_s1_g1_three_layer_r1 import runtime_environment
from s1_g1_three_layer_al_followup_r2_common import (
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
    "docs/S1_G1_THREE_LAYER_AL_DOMAIN_FOLLOWUP_R2_PROTOCOL.md",
    "config/S1_g1_three_layer_al_domain_followup_r2.json",
    "config/S1_g1_three_layer_al_domain_followup_r2_manifest.tsv",
    "scripts/s1_g1_three_layer_common.py",
    "scripts/parse_s1_g1_three_layer_r1.py",
    "scripts/run_s1_g1_three_layer_r1.py",
    "scripts/s1_g1_three_layer_rank_wrapper.py",
    "scripts/s1_g1_three_layer_al_followup_r2_common.py",
    "scripts/generate_s1_g1_three_layer_al_followup_r2.py",
    "scripts/parse_s1_g1_three_layer_al_followup_r2.py",
    "scripts/run_s1_g1_three_layer_al_followup_r2.py",
    "scripts/analyze_s1_g1_three_layer_al_followup_r2.py",
    "scripts/validate_s1_g1_three_layer_al_followup_r2.py",
    "scripts/s1_electron_number_common.py",
    "scripts/s1_g1_thermodynamic_label_common.py",
    "tests/test_s1_g1_three_layer_al_followup_r2.py",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def registered_paths(config: dict, rows: list[dict[str, str]]) -> list[Path]:
    paths = [Path(value) for value in REGISTERED_CODE]
    input_root = Path(config["input_root"])
    for row in rows:
        paths.extend(input_root / row["experiment_id"] / name for name in ("INPUT", "STRU", "KPT", "metadata.json"))
    return paths


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


def verify_accepted_source(state_root: Path, experiment_id: str, session: dict) -> dict:
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
    return {
        "experiment_id": experiment_id,
        "accepted_marker_sha256": sha256_file(marker_path),
        "result_sha256": result_sha,
        "runner_return_sha256": sha256_file(return_path),
        "evidence": evidence,
        "evidence_count": len(evidence),
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
    require(continuation_session.get("runner_commit") == continuation_spec["preregistration_commit"], "continuation preregistration/runner binding differs")

    barrier_path = continuation_root / continuation_spec["recovery_barrier_relative_path"]
    barrier = _object(barrier_path, "independent R1 P0 recovery barrier")
    require(barrier.get("status") == "accepted", "independent R1 P0 recovery rejected")
    require(barrier.get("protocol_revision") == continuation_spec["protocol_revision"], "recovery barrier protocol differs")
    require(barrier.get("runner_commit") == continuation_session.get("runner_commit"), "recovery barrier runner/session binding differs")
    require(barrier.get("source_r1_state_root") == str(old_root), "recovery source state differs")
    require(barrier.get("source_r1_session_sha256") == old_session_sha, "recovery source session SHA differs")
    require(barrier.get("source_r1_runner_commit") == old_spec["runner_commit"], "recovery source runner differs")
    require(barrier.get("r1_operational_closure") == "incomplete_missing_phase_marker", "R1 operational closure semantics differ")
    require(barrier.get("source_ids") == continuation_spec["required_recovery_ids"], "recovery source ID set/order differs")
    require(barrier.get("source_ids_no_retry_no_reuse") is True, "recovery no-retry/no-reuse acknowledgement missing")
    scientific = barrier.get("scientific_p0_statuses")
    require(isinstance(scientific, dict) and scientific and all(value == "accepted" for value in scientific.values()), "independent scientific P0 recovery gate rejected")
    barrier_inventory = _inventory_map(barrier.get("accepted_inventory"))

    recovered: dict[str, dict] = {}
    for experiment_id in old_spec["required_accepted_ids"]:
        identity = verify_accepted_source(old_root, experiment_id, old_session)
        inventory = barrier_inventory.get(experiment_id)
        require(inventory is not None, f"recovery inventory lacks {experiment_id}")
        for key in ("accepted_marker_sha256", "result_sha256", "runner_return_sha256"):
            require(inventory.get(key) == identity[key], f"recovery/source {key} differs: {experiment_id}")
        if experiment_id in {"S1-20260810-301", "S1-20260810-302", "S1-20260810-303"}:
            result = _object(old_root / "runs" / experiment_id / "result.json", "R1 Al anchor result")
            require(result.get("runtime_nonlocal_projectors_total") == 18, "R1 Al anchor projector count differs")
            require(result.get("pseudo_identity", {}).get("sha256") == config["pseudodojo"]["materials"]["al"]["sha256"], "R1 Al anchor pseudo differs")
        recovered[experiment_id] = identity

    phase_path = continuation_root / continuation_spec["endpoint_phase_marker_relative_path"]
    phase = _object(phase_path, "continuation endpoint phase closure")
    require(phase.get("status") == "accepted", "continuation endpoint phase rejected")
    require(phase.get("protocol_revision") == continuation_spec["protocol_revision"], "continuation phase protocol differs")
    require(phase.get("runner_commit") == continuation_session.get("runner_commit"), "continuation phase runner/session differs")
    require(phase.get("phase") == continuation_spec["endpoint_phase"], "continuation endpoint phase name differs")
    require(phase.get("accepted_ids") == continuation_spec["required_accepted_ids"], "continuation endpoint phase ID set/order differs")
    require(phase.get("accepted_count") == len(continuation_spec["required_accepted_ids"]), "continuation endpoint phase count differs")
    endpoints = {experiment_id: verify_accepted_source(continuation_root, experiment_id, continuation_session) for experiment_id in continuation_spec["required_accepted_ids"]}
    for experiment_id in continuation_spec["required_accepted_ids"]:
        result = _object(continuation_root / "runs" / experiment_id / "result.json", "continuation Al endpoint result")
        require(result.get("runtime_nonlocal_projectors_total") == 18, "continuation Al endpoint projector count differs")
        require(result.get("pseudo_identity", {}).get("sha256") == config["pseudodojo"]["materials"]["al"]["sha256"], "continuation Al endpoint pseudo differs")
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
    if "accepted_result_sha256" in phase:
        require(phase["accepted_result_sha256"] == {key: value["result_sha256"] for key, value in endpoints.items()}, "continuation phase result identity differs")
    return {
        "ready": True,
        "r1_session_sha256": old_session_sha,
        "r1_recovery_barrier_sha256": sha256_file(barrier_path),
        "continuation_session_sha256": sha256_file(continuation_session_path),
        "continuation_endpoint_phase_sha256": sha256_file(phase_path),
        "r1_recovered_sources": recovered,
        "endpoint_common_sources": endpoints,
    }


def validate_core_reservation_ack(path: Path, config: dict) -> dict:
    ack = _object(path, "core reservation ACK")
    runtime = config["runtime"]
    require(ack.get("schema_version") == 1, "core reservation ACK schema differs")
    require(ack.get("status") == "exclusive_core_reservation_acknowledged", "core reservation ACK rejected")
    require(ack.get("protocol_revision") == config["protocol_revision"], "core reservation ACK protocol differs")
    require(ack.get("hostname") == runtime["required_hostname"], "core reservation ACK host differs")
    require(ack.get("physical_core_ids") == runtime["physical_core_ids"], "core reservation ACK cores differ")
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
        for core in runtime["physical_core_ids"]:
            path = lock_root / f"{runtime['required_hostname']}_physical_core_{core}.lock"
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
                "path": str(path), "physical_core_id": core, "advisory_lock": "exclusive_nonblocking",
                "device": stat.st_dev, "inode": stat.st_ino, "owner_pid": os.getpid(), "acquired": True,
            })
    except Exception:
        for handle in handles:
            handle.close()
        raise
    return handles, identities


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
            cmdline = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
            if "abacus" not in (comm + " " + cmdline).lower():
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


def initialize_state(state_root: Path, project_root: Path, config: dict, head: str, parents: dict, ack: dict, locks: list[dict], preflight: dict) -> dict:
    require(not state_root.exists(), f"fresh external state already exists: {state_root}")
    state_root.mkdir(parents=True, mode=0o700)
    payload = {
        "schema_version": 1, "protocol_revision": config["protocol_revision"], "status": "active",
        "created_utc": utc_now(), "runner_commit": head,
        "branch": subprocess.run(["git", "branch", "--show-current"], cwd=project_root, check=True, text=True, stdout=subprocess.PIPE).stdout.strip(),
        "project_root": str(project_root), "config_sha256": sha256_file(project_root / CONFIG_PATH),
        "manifest_sha256": sha256_file(project_root / MANIFEST_PATH), "parent_source_identity": parents,
        "core_reservation_ack": ack, "core_locks": locks, "live_preflight": preflight,
        "retry_policy": "same_id_forbidden_new_revision_and_new_ids_only",
    }
    atomic_write(state_root / "session.json", canonical_json_bytes(payload), exclusive=True)
    return payload


def write_failure(run_dir: Path, experiment_id: str, stage: str, message: str, return_code: int | None) -> None:
    path = run_dir / "failure.json"
    if path.exists():
        return
    atomic_write(path, canonical_json_bytes({
        "schema_version": 1, "protocol_revision": "S1-G1-THREE-LAYER-AL-DOMAIN-FOLLOWUP-20260810-R2",
        "status": "preserved_failure_no_retry", "experiment_id": experiment_id, "stage": stage,
        "message": message, "runner_return_code": return_code, "created_utc": utc_now(),
        "retry_policy": "do_not_retry_this_id",
    }), exclusive=True)


def run_one(project_root: Path, state_root: Path, cache: Path, row: dict[str, str], config: dict, head: str) -> dict:
    experiment_id = row["experiment_id"]
    attempt_path = state_root / "attempts" / f"{experiment_id}.json"
    run_dir = state_root / "runs" / experiment_id
    require(not attempt_path.exists() and not run_dir.exists(), f"retry forbidden: {experiment_id}")
    atomic_write(attempt_path, canonical_json_bytes({
        "schema_version": 1, "protocol_revision": config["protocol_revision"], "status": "formal_attempt_started",
        "experiment_id": experiment_id, "created_utc": utc_now(), "runner_commit": head,
        "config_sha256": sha256_file(project_root / CONFIG_PATH), "manifest_sha256": sha256_file(project_root / MANIFEST_PATH),
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
        **input_metadata, "runner_commit": head, "hostname": socket.gethostname(), "started_utc": utc_now(),
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
    parser.add_argument("--core-reservation-ack", type=Path)
    args = parser.parse_args()
    project_root = find_project_root(args.project_root)
    config = load_config(project_root)
    rows = load_manifest(project_root)
    head = require_clean_tree(project_root)
    require_tracked_matches_head(project_root, registered_paths(config, rows))
    state_root = Path(config["external_state_root"])
    if args.dry_run:
        parent_error: str | None = None
        try:
            parent = verify_parent_sources(config, project_root, rows)
        except Exception as error:
            parent = {"ready": False}
            parent_error = f"{type(error).__name__}: {error}"
        print(json.dumps({"status": "accepted_dry_run", "solver_started": False, "formal_id_count": len(rows), "state_exists": state_root.exists(), "parent_source": parent, "parent_not_ready_reason": parent_error, "reservation_ack_supplied": args.core_reservation_ack is not None, "target_cores": config["runtime"]["physical_core_ids"]}, sort_keys=True))
        return 0
    require(args.core_reservation_ack is not None, "formal run requires --core-reservation-ack")
    parents = verify_parent_sources(config, project_root, rows)
    ack = validate_core_reservation_ack(args.core_reservation_ack, config)
    handles, locks = acquire_core_locks(config)
    try:
        preflight = live_preflight(config)
        initialize_state(state_root, project_root, config, head, parents, ack, locks, preflight)
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
    finally:
        for handle in handles:
            handle.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"FATAL: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        raise
