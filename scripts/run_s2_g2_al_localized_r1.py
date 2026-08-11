#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import socket
import subprocess
import time
from pathlib import Path

from s2_g2_al_localized_common_r1 import (
    CONFIG_REL, INPUT_NAMES, WRAPPER_REL, atomic_write, canonical_json, file_identity, git,
    load_config, parse_reference_run, registered_input_payloads, require, sha256_path, utc_now,
    validate_config,
)


def run_checked(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    completed = subprocess.run(command, check=False, **kwargs)
    require(completed.returncode == 0, f"command failed: {command}")
    return completed


def preserve_failure(state: Path, session: dict, experiment_id: str, stage: str, message: str, return_code: int | None = None) -> dict:
    failure = {
        "schema_version": 1, "status": "failed_no_retry", "experiment_id": experiment_id,
        "stage": stage, "message": message, "return_code": return_code, "created_utc": utc_now(),
    }
    atomic_write(state / "failures" / f"{experiment_id}.json", canonical_json(failure))
    session["status"] = "stopped_failed_no_retry"
    session["failed_count"] = 1
    session["finished_utc"] = utc_now()
    (state / "session.json").write_bytes(canonical_json(session))
    return failure


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.project_root.resolve()
    config = load_config(root)
    validate_config(config)
    require(config["status"] == "preregistered_no_execution", "runner requires preregistration")
    require(git(root, "status", "--porcelain") == "", "runner worktree must be clean")
    head = git(root, "rev-parse", "HEAD")
    require(git(root, "show", "-s", "--format=%P", head).split() == [config["implementation_commit"]], "runner HEAD is not config-only preregistration")
    runtime = config["runtime"]
    require(socket.gethostname() == "node01", "orchestrator must run on node01")
    require(sha256_path(Path(runtime["binary"])) == runtime["binary_sha256"], "local binary identity differs")
    require(sha256_path(Path(runtime["mpi"])) == runtime["mpi_sha256"], "local MPI identity differs")
    input_dir = root / config["execution"]["input_root"]
    expected_inputs = registered_input_payloads(root, config)
    for name, data in expected_inputs.items():
        require((input_dir / name).read_bytes() == data, f"registered input differs: {name}")
    state = Path(config["execution"]["state_root"])
    require(not state.exists(), "formal state already exists; retry forbidden")
    state.mkdir(parents=True)
    for name in ("attempts", "runs", "accepted", "failures"):
        (state / name).mkdir()
    experiment_id = config["reference"]["experiment_id"]
    run_dir = state / "runs" / experiment_id
    run_dir.mkdir()
    for name in INPUT_NAMES:
        shutil.copyfile(input_dir / name, run_dir / ("input_metadata.json" if name == "metadata.json" else name))
    pseudo = Path(config["source"]["pseudopotential_path"])
    require(sha256_path(pseudo) == config["source"]["pseudopotential_sha256"], "pseudo SHA differs")
    shutil.copyfile(pseudo, run_dir / "Al_std.upf")
    input_metadata = json.loads((run_dir / "input_metadata.json").read_text())
    metadata = {
        **input_metadata,
        "runner_commit": head,
        "preregistration_commit": head,
        "orchestrator_hostname": socket.gethostname(),
        "solver_hostname": runtime["hostname"],
        "started_utc": utc_now(),
        "runtime": runtime,
    }
    atomic_write(run_dir / "metadata.json", canonical_json(metadata))
    session = {
        "schema_version": 1, "protocol_revision": config["protocol_revision"], "status": "active",
        "experiment_ids": [experiment_id], "runner_commit": head, "config_sha256": sha256_path(root / CONFIG_REL),
        "hostname": socket.gethostname(), "created_utc": utc_now(), "retry_policy": config["execution"]["retry_policy"],
    }
    atomic_write(state / "session.json", canonical_json(session))
    attempt = {
        "schema_version": 1, "protocol_revision": config["protocol_revision"], "status": "formal_attempt_started",
        "experiment_id": experiment_id, "runner_commit": head, "created_utc": utc_now(),
        "input_files": [file_identity(run_dir / ("input_metadata.json" if name == "metadata.json" else name), run_dir) for name in INPUT_NAMES],
    }
    atomic_write(state / "attempts" / f"{experiment_id}.json", canonical_json(attempt))
    remote = config["execution"]["remote_run_root"]
    host = runtime["hostname"]
    wrapper = root / WRAPPER_REL
    remote_wrapper = f"{remote}/{WRAPPER_REL.name}"
    remote_check = subprocess.run(["ssh", host, "test", "!", "-e", remote], check=False)
    require(remote_check.returncode == 0, "remote run root already exists; retry forbidden")
    run_checked(["ssh", host, "mkdir", "-p", remote])
    copy_sources = [run_dir / name for name in ("INPUT", "STRU", "KPT", "input_metadata.json", "metadata.json", "Al_std.upf")] + [wrapper]
    run_checked(["scp", *[str(path) for path in copy_sources], f"{host}:{remote}/"])
    preflight = subprocess.run(
        ["ssh", host, "/usr/bin/python3", remote_wrapper, "--preflight", "--expected-cores", ",".join(str(x) for x in runtime["physical_core_ids"])],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    (run_dir / "remote_preflight.stdout").write_bytes(preflight.stdout)
    (run_dir / "remote_preflight.stderr").write_bytes(preflight.stderr)
    if preflight.returncode != 0:
        failure = preserve_failure(state, session, experiment_id, "remote_preflight", preflight.stderr.decode(errors="replace"), preflight.returncode)
        print(json.dumps(failure, sort_keys=True))
        return 1
    preflight_payload = json.loads(preflight.stdout)
    require(preflight_payload["accepted"] is True and preflight_payload["hostname"] == host, "remote preflight payload differs")
    remote_command = [
        "env", *[f"{key}={value}" for key, value in runtime["thread_environment"].items()],
        runtime["mpi"], "--map-by", runtime["map_by"], "--bind-to", "core", "--report-bindings",
        "-np", str(runtime["rank_count"]), "/usr/bin/python3", remote_wrapper,
        "--binary", runtime["binary"], "--evidence-dir", f"{remote}/affinity",
        "--expected-cores", ",".join(str(x) for x in runtime["physical_core_ids"]),
    ]
    shell_command = f"cd {shlex.quote(remote)} && " + " ".join(shlex.quote(value) for value in remote_command)
    started = time.monotonic()
    with (run_dir / "run.stdout").open("wb") as stdout, (run_dir / "run.stderr").open("wb") as stderr:
        try:
            completed = subprocess.run(["ssh", host, shell_command], stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, timeout=int(runtime["per_run_timeout_seconds"]), check=False)
            return_code = completed.returncode
            timed_out = False
        except subprocess.TimeoutExpired:
            return_code = 124
            timed_out = True
    duration = time.monotonic() - started
    retrieved = {}
    for remote_name in ("OUT.s2_g2_al108_localized_r1", "affinity"):
        exists = subprocess.run(["ssh", host, "test", "-e", f"{remote}/{remote_name}"], check=False).returncode == 0
        retrieved[remote_name] = exists
        if exists:
            copied = subprocess.run(["scp", "-r", f"{host}:{remote}/{remote_name}", str(run_dir / remote_name)], check=False)
            retrieved[remote_name] = copied.returncode == 0
    return_payload = {
        "schema_version": 1, "experiment_id": experiment_id, "remote_host": host,
        "command": remote_command, "return_code": return_code, "timed_out": timed_out,
        "duration_seconds": duration, "finished_utc": utc_now(), "retrieved_remote_evidence": retrieved,
    }
    atomic_write(run_dir / "runner_return.json", canonical_json(return_payload))
    if return_code != 0:
        failure = preserve_failure(state, session, experiment_id, "solver", "remote solver returned nonzero", return_code)
        print(json.dumps(failure, sort_keys=True))
        return 1
    try:
        require(all(retrieved.values()), "successful solver did not yield complete remote evidence")
        result = parse_reference_run(run_dir, config)
    except Exception as error:
        failure = preserve_failure(state, session, experiment_id, "parser", f"{type(error).__name__}: {error}", 0)
        print(json.dumps(failure, sort_keys=True))
        return 1
    atomic_write(run_dir / "result.json", canonical_json(result))
    accepted = {
        "schema_version": 1, "protocol_revision": config["protocol_revision"], "status": "accepted_reference",
        "experiment_id": experiment_id, "runner_commit": head, "result_sha256": sha256_path(run_dir / "result.json"),
        "duration_seconds": duration, "created_utc": utc_now(),
    }
    atomic_write(state / "accepted" / f"{experiment_id}.json", canonical_json(accepted))
    session["status"] = "accepted_terminal"
    session["accepted_count"] = 1
    session["failed_count"] = 0
    session["runner_return_code"] = 0
    session["finished_utc"] = utc_now()
    (state / "session.json").write_bytes(canonical_json(session))
    atomic_write(state / "terminal.json", canonical_json({
        "schema_version": 1, "protocol_revision": config["protocol_revision"], "status": "accepted",
        "attempted": 1, "accepted": 1, "failed": 0, "retried": 0, "runner_return_code": 0,
        "experiment_ids": [experiment_id], "session_sha256": sha256_path(state / "session.json"),
        "result_sha256": sha256_path(run_dir / "result.json"), "created_utc": utc_now(),
    }))
    print(json.dumps({"status": "accepted_reference", "experiment_id": experiment_id, "duration_seconds": duration}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
