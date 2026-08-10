#!/usr/bin/env python3
"""Execute one registered R1 phase with one-attempt IDs and external state."""

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

from parse_s1_g1_three_layer_r1 import parse_run
from s1_g1_three_layer_common import (
    CONFIG_PATH,
    MANIFEST_PATH,
    atomic_write,
    canonical_json_bytes,
    file_identity,
    find_project_root,
    load_config,
    load_manifest,
    phase_rows,
    read_json,
    require,
    require_clean_tree,
    require_tracked_matches_head,
    sha256_file,
    validate_pseudo,
)


REGISTERED_CODE = (
    "docs/S1_G1_THREE_LAYER_R1_PROTOCOL.md",
    "config/S1_g1_three_layer_r1.json",
    "config/S1_g1_three_layer_r1_manifest.tsv",
    "scripts/s1_g1_three_layer_common.py",
    "scripts/generate_s1_g1_three_layer_r1.py",
    "scripts/fetch_s1_g1_three_layer_pseudos.py",
    "scripts/s1_g1_three_layer_rank_wrapper.py",
    "scripts/parse_s1_g1_three_layer_r1.py",
    "scripts/run_s1_g1_three_layer_r1.py",
    "scripts/analyze_s1_g1_three_layer_r1.py",
    "scripts/validate_s1_g1_three_layer_r1.py",
    "tests/test_s1_g1_three_layer_r1.py",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def registered_paths(project_root: Path, config: dict, rows: list[dict[str, str]]) -> list[Path]:
    paths = [Path(value) for value in REGISTERED_CODE]
    input_root = Path(config["input_root"])
    for row in rows:
        paths.extend(input_root / row["experiment_id"] / name for name in ("INPUT", "STRU", "KPT", "metadata.json"))
    return paths


def runtime_environment(config: dict) -> dict[str, str]:
    prefix = config["runtime"]["prefix"]
    env = {
        "HOME": "/home/shenwei01",
        "USER": "shenwei01",
        "LOGNAME": "shenwei01",
        "PATH": f"{prefix}/bin:/usr/bin:/bin",
        "LD_LIBRARY_PATH": f"{prefix}/lib",
        "OPAL_PREFIX": prefix,
        "PRTE_PREFIX": prefix,
        "PMIX_PREFIX": prefix,
        "OMP_NUM_THREADS": "1",
        "OMP_PROC_BIND": "true",
        "OMP_PLACES": "cores",
        "MKL_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "LC_ALL": "C",
        "LANG": "C",
        "TZ": "UTC",
    }
    return env


def validate_phase_barrier(state_root: Path, phase: str) -> None:
    if phase == "p0":
        require(not state_root.exists(), f"fresh P0 state root already exists: {state_root}")
        return
    barrier = state_root / "barriers" / "p0_gate.json"
    payload = read_json(barrier)
    require(isinstance(payload, dict) and payload.get("status") == "accepted", "P0 gate not accepted")
    require(payload.get("protocol_revision") == "S1-G1-THREE-LAYER-20260810-R1", "P0 protocol differs")


def initialize_or_validate_session(
    state_root: Path,
    project_root: Path,
    config: dict,
    head: str,
    phase: str,
) -> dict:
    session_path = state_root / "session.json"
    if phase == "p0":
        state_root.mkdir(parents=True, mode=0o700)
        payload = {
            "schema_version": 1,
            "protocol_revision": config["protocol_revision"],
            "status": "active",
            "created_utc": utc_now(),
            "hostname": socket.gethostname(),
            "runner_commit": head,
            "branch": subprocess.run(
                ["git", "branch", "--show-current"], cwd=project_root, check=True, text=True, stdout=subprocess.PIPE
            ).stdout.strip(),
            "project_root": str(project_root),
            "config_sha256": sha256_file(project_root / CONFIG_PATH),
            "manifest_sha256": sha256_file(project_root / MANIFEST_PATH),
            "retry_policy": "same_id_forbidden_new_revision_and_new_ids_only",
        }
        atomic_write(session_path, canonical_json_bytes(payload), exclusive=True)
        return payload
    payload = read_json(session_path)
    require(isinstance(payload, dict), "session must be object")
    require(payload.get("runner_commit") == head, "runner commit changed between phases")
    require(payload.get("config_sha256") == sha256_file(project_root / CONFIG_PATH), "config changed")
    require(payload.get("manifest_sha256") == sha256_file(project_root / MANIFEST_PATH), "manifest changed")
    require(payload.get("hostname") == socket.gethostname(), "session hostname changed")
    return payload


def write_failure(run_dir: Path, experiment_id: str, stage: str, message: str, return_code: int | None) -> None:
    payload = {
        "schema_version": 1,
        "protocol_revision": "S1-G1-THREE-LAYER-20260810-R1",
        "status": "preserved_failure",
        "experiment_id": experiment_id,
        "stage": stage,
        "message": message,
        "runner_return_code": return_code,
        "created_utc": utc_now(),
        "retry_policy": "do_not_retry_this_id",
    }
    path = run_dir / "failure.json"
    if not path.exists():
        atomic_write(path, canonical_json_bytes(payload), exclusive=True)


def run_one(
    project_root: Path,
    state_root: Path,
    cache: Path,
    row: dict[str, str],
    config: dict,
    head: str,
) -> dict:
    experiment_id = row["experiment_id"]
    attempt_path = state_root / "attempts" / f"{experiment_id}.json"
    run_dir = state_root / "runs" / experiment_id
    require(not attempt_path.exists(), f"attempt marker already exists; retry forbidden: {experiment_id}")
    require(not run_dir.exists(), f"run directory already exists; retry forbidden: {experiment_id}")
    attempt = {
        "schema_version": 1,
        "protocol_revision": config["protocol_revision"],
        "status": "formal_attempt_started",
        "experiment_id": experiment_id,
        "phase": row["phase"],
        "requirement": row["requirement"],
        "created_utc": utc_now(),
        "runner_commit": head,
        "config_sha256": sha256_file(project_root / CONFIG_PATH),
        "manifest_sha256": sha256_file(project_root / MANIFEST_PATH),
        "retry_policy": "same_id_forbidden_new_revision_and_new_ids_only",
    }
    atomic_write(attempt_path, canonical_json_bytes(attempt), exclusive=True)
    run_dir.mkdir(parents=True)
    input_dir = project_root / config["input_root"] / experiment_id
    for name in ("INPUT", "STRU", "KPT"):
        shutil.copyfile(input_dir / name, run_dir / name)
    shutil.copyfile(input_dir / "metadata.json", run_dir / "input_metadata.json")
    pseudo_source = cache / row["pseudo_basename"]
    pseudo_identity = validate_pseudo(pseudo_source, row["material"], config)
    shutil.copyfile(pseudo_source, run_dir / row["pseudo_basename"])
    require(sha256_file(run_dir / row["pseudo_basename"]) == row["pseudo_sha256"], "copied PP SHA differs")
    input_metadata = read_json(input_dir / "metadata.json")
    require(isinstance(input_metadata, dict), "input metadata must be object")
    metadata = {
        **input_metadata,
        "runner_commit": head,
        "hostname": socket.gethostname(),
        "started_utc": utc_now(),
        "runtime": {
            "binary": config["runtime"]["binary"],
            "binary_sha256": config["runtime"]["binary_sha256"],
            "mpi": config["runtime"]["mpi"],
            "rank_count": config["runtime"]["rank_count"],
            "physical_core_ids": config["runtime"]["physical_core_ids"],
            "map_by": config["runtime"]["map_by"],
        },
        "pseudo_runtime_identity": pseudo_identity,
    }
    atomic_write(run_dir / "metadata.json", canonical_json_bytes(metadata), exclusive=True)
    atomic_write(run_dir / "pseudo_identity.json", canonical_json_bytes(pseudo_identity), exclusive=True)
    affinity_dir = run_dir / "affinity"
    affinity_dir.mkdir()
    wrapper = project_root / "scripts/s1_g1_three_layer_rank_wrapper.py"
    command = [
        config["runtime"]["mpi"],
        "--map-by",
        config["runtime"]["map_by"],
        "--bind-to",
        "core",
        "--report-bindings",
        "-np",
        str(config["runtime"]["rank_count"]),
        "/usr/bin/python3",
        str(wrapper),
        "--binary",
        config["runtime"]["binary"],
        "--evidence-dir",
        str(affinity_dir),
        "--expected-cores",
        ",".join(str(value) for value in config["runtime"]["physical_core_ids"]),
    ]
    start = time.monotonic()
    return_code: int | None = None
    stage = "solver"
    try:
        with (run_dir / "run.stdout").open("wb") as stdout, (run_dir / "run.stderr").open("wb") as stderr:
            completed = subprocess.run(
                command,
                cwd=run_dir,
                env=runtime_environment(config),
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                timeout=int(config["runtime"]["per_run_timeout_seconds"]),
                check=False,
            )
        return_code = completed.returncode
        duration = time.monotonic() - start
        return_payload = {
            "schema_version": 1,
            "experiment_id": experiment_id,
            "command": command,
            "return_code": return_code,
            "duration_seconds": duration,
            "finished_utc": utc_now(),
        }
        atomic_write(run_dir / "runner_return.json", canonical_json_bytes(return_payload), exclusive=True)
        require(return_code == 0, f"solver returned {return_code}")
        stage = "parser"
        result = parse_run(run_dir, config)
        atomic_write(run_dir / "result.json", canonical_json_bytes(result), exclusive=True)
        accepted = {
            "schema_version": 1,
            "protocol_revision": config["protocol_revision"],
            "status": "accepted",
            "experiment_id": experiment_id,
            "runner_commit": head,
            "duration_seconds": duration,
            "result_sha256": sha256_file(run_dir / "result.json"),
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True, choices=("p0", "al_eos", "mg_required", "mg_optional"))
    parser.add_argument("--project-root", type=Path)
    args = parser.parse_args()
    project_root = find_project_root(args.project_root)
    config = load_config(project_root)
    rows = load_manifest(project_root)
    head = require_clean_tree(project_root)
    require(socket.gethostname() == config["runtime"]["required_hostname"], "formal run must execute on node01")
    require(sha256_file(Path(config["runtime"]["binary"])) == config["runtime"]["binary_sha256"], "binary SHA differs")
    require_tracked_matches_head(project_root, registered_paths(project_root, config, rows))
    state_root = Path(config["external_state_root"])
    validate_phase_barrier(state_root, args.phase)
    session = initialize_or_validate_session(state_root, project_root, config, head, args.phase)
    cache = Path(config["external_pseudo_cache"])
    for material, pseudo in config["pseudodojo"]["materials"].items():
        validate_pseudo(cache / pseudo["basename"], material, config)
    selected = phase_rows(rows, args.phase)
    accepted: list[dict] = []
    for row in selected:
        print(f"START {row['experiment_id']} phase={args.phase}", flush=True)
        accepted_row = run_one(project_root, state_root, cache, row, config, head)
        accepted.append(accepted_row)
        print(
            f"ACCEPTED {row['experiment_id']} duration_seconds={accepted_row['duration_seconds']:.3f}",
            flush=True,
        )
    phase_payload = {
        "schema_version": 1,
        "protocol_revision": config["protocol_revision"],
        "status": "accepted",
        "phase": args.phase,
        "runner_commit": session["runner_commit"],
        "accepted_ids": [row["experiment_id"] for row in accepted],
        "accepted_count": len(accepted),
        "created_utc": utc_now(),
    }
    atomic_write(state_root / "phases" / f"{args.phase}.json", canonical_json_bytes(phase_payload), exclusive=True)
    print(json.dumps(phase_payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"FATAL: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        raise
