#!/usr/bin/env python3
"""Run one detached, no-ABACUS four-rank topology smoke before formal state exists."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from run_s1_g1_three_layer_al_followup_r3 import (
    acquire_core_locks,
    binding_command,
    detached_runtime_proof,
    live_preflight,
    registered_paths,
)
from s1_g1_three_layer_al_followup_r3_common import (
    CONFIG_PATH,
    MANIFEST_PATH,
    atomic_write,
    canonical_json_bytes,
    find_project_root,
    load_config,
    load_manifest,
    read_json,
    require,
    require_clean_tree,
    require_tracked_matches_head,
    sha256_file,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_rank_payloads(root: Path, config: dict) -> list[dict]:
    rows: list[dict] = []
    runtime = config["runtime"]
    for rank in range(runtime["rank_count"]):
        path = root / "ranks" / f"rank_{rank:03d}.json"
        payload = read_json(path)
        require(isinstance(payload, dict) and payload.get("accepted") is True, f"rank {rank} smoke rejected")
        require(payload.get("mode") == "smoke" and payload.get("rank") == rank and payload.get("local_rank") == rank, f"rank {rank} identity differs")
        require(payload.get("hostname") == runtime["required_hostname"], f"rank {rank} host differs")
        require(payload.get("os_logical_cpu_affinity") == runtime["thread_siblings_by_rank"][rank], f"rank {rank} OS affinity differs")
        require(payload.get("primary_os_logical_cpu_id") == runtime["primary_os_logical_cpu_ids_by_rank"][rank], f"rank {rank} primary CPU differs")
        require(payload.get("physical_package_id") == runtime["required_physical_package_id"], f"rank {rank} package differs")
        require(payload.get("core_id") == runtime["core_id_by_rank"][rank], f"rank {rank} core_id differs")
        require(payload.get("thread_siblings") == runtime["thread_siblings_by_rank"][rank], f"rank {rank} siblings differ")
        rows.append({"path": str(path.relative_to(root)), "sha256": sha256_file(path), "size_bytes": path.stat().st_size, "payload": payload})
    require(len(list((root / "ranks").glob("rank_*.json"))) == runtime["rank_count"], "smoke rank denominator differs")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path)
    args = parser.parse_args()
    project_root = find_project_root(args.project_root)
    config = load_config(project_root)
    rows = load_manifest(project_root)
    head = require_clean_tree(project_root)
    require(head != config["implementation_commit"], "binding smoke must run from a preregistration child of implementation")
    require_tracked_matches_head(project_root, registered_paths(config, rows))
    state_root = Path(config["external_state_root"])
    require(not state_root.exists(), "formal state exists before binding smoke")
    output_root = Path(config["binding_smoke"]["external_root"])
    require(not output_root.exists() and not output_root.is_symlink(), "binding smoke root already exists; retry forbidden")
    output_root.mkdir(parents=True, mode=0o700)
    start = time.monotonic()
    handles = []
    try:
        detached = detached_runtime_proof()
        handles, locks = acquire_core_locks(config)
        preflight = live_preflight(config)
        command = binding_command(project_root, config, output_root / "ranks", mode="smoke")
        stdout_path = output_root / "run.stdout"
        stderr_path = output_root / "run.stderr"
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            completed = subprocess.run(command, cwd=project_root, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, check=False, timeout=120)
        require(completed.returncode == 0, f"binding smoke returned {completed.returncode}")
        rank_evidence = validate_rank_payloads(output_root, config)
        aggregate = {
            "schema_version": 1,
            "protocol_revision": config["protocol_revision"],
            "status": "accepted",
            "preregistration_commit": head,
            "implementation_commit": config["implementation_commit"],
            "config_sha256": sha256_file(project_root / CONFIG_PATH),
            "manifest_sha256": sha256_file(project_root / MANIFEST_PATH),
            "formal_state_absent": not state_root.exists(),
            "formal_attempt_count": 0,
            "abacus_exec_count": 0,
            "runner_return_code": completed.returncode,
            "rank_count": len(rank_evidence),
            "failed_rank_count": 0,
            "command": command,
            "detached_runtime_proof": detached,
            "live_preflight": preflight,
            "core_locks": locks,
            "rank_evidence": rank_evidence,
            "stdout_sha256": sha256_file(stdout_path),
            "stderr_sha256": sha256_file(stderr_path),
            "duration_seconds": time.monotonic() - start,
            "created_utc": utc_now(),
        }
        atomic_write(output_root / "aggregate.json", canonical_json_bytes(aggregate), exclusive=True)
        print(json.dumps({"status": "accepted", "aggregate_sha256": sha256_file(output_root / "aggregate.json"), "rank_count": 4, "abacus_exec_count": 0, "formal_state_exists": state_root.exists()}, sort_keys=True))
        return 0
    except Exception as error:
        if output_root.exists() and not (output_root / "failure.json").exists():
            atomic_write(output_root / "failure.json", canonical_json_bytes({"schema_version": 1, "protocol_revision": config["protocol_revision"], "status": "preserved_failure_no_retry", "message": f"{type(error).__name__}: {error}", "created_utc": utc_now()}), exclusive=True)
        raise
    finally:
        for handle in handles:
            handle.close()


if __name__ == "__main__":
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"FATAL: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        raise
