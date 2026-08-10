#!/usr/bin/env python3
"""Run frozen R2 collect once and freeze its expected scientific-rejection invocation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from analyze_s1_g1_three_layer_analysis_r3 import load_config, source_inventory
from s1_g1_three_layer_continuation_r2_common import atomic_write, canonical_json_bytes, read_json, require, sha256_file


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def git(root: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=root, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return completed.stdout.strip()


def capture(capture_root: Path, r2_root: Path) -> dict:
    config = load_config(capture_root)
    capture_spec = config["capture"]
    capture_head = git(capture_root, "rev-parse", "HEAD")
    require(not git(capture_root, "status", "--porcelain=v1"), "capture worktree must be clean")
    require(git(r2_root, "rev-parse", "HEAD") == capture_spec["r2_runner_commit"], "R2 runner HEAD differs")
    require(not git(r2_root, "status", "--porcelain=v1"), "R2 worktree must be clean before collect")
    analyzer = r2_root / capture_spec["analyzer_path"]
    r2_config = r2_root / capture_spec["r2_config_path"]
    require(sha256_file(analyzer) == capture_spec["analyzer_sha256"], "R2 analyzer SHA differs")
    require(sha256_file(r2_config) == capture_spec["r2_config_sha256"], "R2 config SHA differs")
    r2_payload = read_json(r2_config)
    require(isinstance(r2_payload, dict), "R2 config must be object")
    analysis = r2_root / r2_payload["analysis_root"]
    require(not analysis.exists(), "R2 analysis root must not pre-exist")
    terminal_path = Path(r2_payload["external_state_root"]) / "terminal.json"
    terminal = read_json(terminal_path)
    require(isinstance(terminal, dict) and terminal.get("status") == "accepted", "capture requires accepted R2 terminal")
    require(terminal.get("runner_commit") == capture_spec["r2_runner_commit"], "R2 terminal runner differs")
    require(terminal.get("attempted_count") == terminal.get("accepted_count") == 8, "R2 terminal denominator differs")
    require(terminal.get("failed_count") == terminal.get("retried_count") == terminal.get("runner_return_code") == 0, "R2 terminal failure/retry/RC differs")
    argv = [capture_spec["python"], capture_spec["analyzer_path"], "--project-root", ".", "--collect"]
    env = {**os.environ, "PYTHONPATH": str(r2_root / "scripts")}
    started_utc = utc_now()
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="g1_three_layer_r2_capture_") as temporary:
        stdout_path = Path(temporary) / "analysis.stdout"
        stderr_path = Path(temporary) / "analysis.stderr"
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            completed = subprocess.run(argv, cwd=r2_root, env=env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, check=False)
        finished_utc = utc_now()
        duration = time.monotonic() - start
        require(completed.returncode == int(capture_spec["expected_exit_code"]) == 2, "R2 analyzer exit semantics differ")
        require(analysis.is_dir() and not analysis.is_symlink(), "R2 analyzer did not create analysis root")
        summary = read_json(analysis / "summary.json")
        require(isinstance(summary, dict) and summary.get("status") == capture_spec["expected_summary_status"] == "rejected", "R2 summary disposition differs")
        analyzer_inventory = source_inventory(analysis)
        orchestration = analysis / "orchestration"
        stdout_bytes = stdout_path.read_bytes()
        stderr_bytes = stderr_path.read_bytes()
        stdout_identity = {"sha256": hashlib.sha256(stdout_bytes).hexdigest(), "size_bytes": len(stdout_bytes)}
        stderr_identity = {"sha256": hashlib.sha256(stderr_bytes).hexdigest(), "size_bytes": len(stderr_bytes)}
        atomic_write(orchestration / "analysis.stdout", stdout_bytes, exclusive=True)
        atomic_write(orchestration / "analysis.stderr", stderr_bytes, exclusive=True)
        invocation = {
            "schema_version": 1,
            "protocol_revision": config["protocol_revision"],
            "status": "captured_expected_scientific_rejection",
            "argv": argv,
            "cwd": str(r2_root),
            "head": capture_spec["r2_runner_commit"],
            "capture_implementation_commit": capture_head,
            "analyzer_sha256": capture_spec["analyzer_sha256"],
            "r2_config_sha256": capture_spec["r2_config_sha256"],
            "terminal_sha256": sha256_file(terminal_path),
            "exit_code": completed.returncode,
            "stdout_sha256": stdout_identity["sha256"],
            "stdout_size_bytes": stdout_identity["size_bytes"],
            "stderr_sha256": stderr_identity["sha256"],
            "stderr_size_bytes": stderr_identity["size_bytes"],
            "analyzer_output_inventory_before_capture_artifacts": analyzer_inventory,
            "started_utc": started_utc,
            "finished_utc": finished_utc,
            "duration_seconds": duration,
            "interpretation": "exit 2 is the preregistered scientific gate rejection, not an execution failure",
        }
        atomic_write(orchestration / "analysis_invocation.json", canonical_json_bytes(invocation), exclusive=True)
    status = git(r2_root, "status", "--porcelain=v1").splitlines()
    prefix = r2_payload["analysis_root"] + "/"
    require(status and all(line[3:].startswith(prefix) for line in status), "capture changed paths outside R2 analysis root")
    return {
        "status": "captured_expected_scientific_rejection",
        "r2_analyzer_exit_code": completed.returncode,
        "r2_summary_status": summary["status"],
        "analysis_root": str(analysis),
        "analysis_invocation_sha256": sha256_file(analysis / "orchestration/analysis_invocation.json"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-project-root", type=Path, required=True)
    parser.add_argument("--r2-project-root", type=Path, required=True)
    args = parser.parse_args()
    payload = capture(args.capture_project_root.resolve(), args.r2_project_root.resolve())
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
