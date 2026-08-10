#!/usr/bin/env python3
"""Single-use runner for the frozen S1-G1 displacement/strain matrix."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path


PROTOCOL = "S1-G1-DISPLACEMENT-STRAIN-REFERENCE-R1"
CONFIG_REL = Path("config/S1_g1_displacement_strain_reference_r1.json")
MANIFEST_REL = Path("config/S1_g1_displacement_strain_reference_r1_manifest.tsv")
WRAPPER_REL = Path("scripts/s1_g1_displacement_rank_wrapper.sh")
RUNNER_REL = Path("scripts/run_s1_g1_displacement_strain_reference.py")


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_exclusive_json(path: Path, payload: object) -> None:
    data = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        written = 0
        while written < len(data):
            written += os.write(descriptor, data[written:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    parent_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def activated_environment(root: Path) -> dict[str, str]:
    command = [
        "/bin/bash", "--noprofile", "--norc", "-c",
        'source "$1"; /usr/bin/env -0', "bash", str(root / "environment/activate.sh"),
    ]
    raw = subprocess.check_output(command)
    environment = {}
    for item in raw.split(b"\0"):
        if not item:
            continue
        key, value = item.decode(errors="strict").split("=", 1)
        environment[key] = value
    environment.update({
        "OMP_NUM_THREADS": "1",
        "PYTHONNOUSERSITE": "1",
        "LC_ALL": "C",
        "TZ": "UTC",
    })
    return environment


def read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    if len(rows) != 15 or any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError("manifest must contain exactly 15 complete rows")
    return rows


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def run_one(
    root: Path,
    state: Path,
    config: dict,
    row: dict[str, str],
    environment: dict[str, str],
    git_head: str,
) -> dict[str, object]:
    experiment_id = row["experiment_id"]
    input_directory = root / row["input_directory"]
    attempt_path = state / "attempts" / f"{experiment_id}.json"
    completion_path = state / "completions" / f"{experiment_id}.json"
    run_directory = state / "runs" / experiment_id
    if run_directory.exists() or attempt_path.exists() or completion_path.exists():
        raise RuntimeError(f"single-use ID already consumed: {experiment_id}")
    for name, manifest_key in (
        ("INPUT", "input_sha256"), ("STRU", "stru_sha256"),
        ("KPT", "kpt_sha256"), ("metadata.json", "metadata_sha256"),
    ):
        path = input_directory / name
        if not path.is_file() or path.is_symlink() or sha256(path) != row[manifest_key]:
            raise RuntimeError(f"frozen input differs for {experiment_id}: {name}")
    marker = {
        "schema_version": 1,
        "protocol_revision": PROTOCOL,
        "experiment_id": experiment_id,
        "status": "started",
        "retry_policy": "none",
        "created_utc": now(),
        "git_head_before_attempt": git_head,
        "hostname": socket.gethostname(),
        "rank_logical_cpus": config["execution"]["rank_logical_cpus_exact"],
        "config_sha256": sha256(root / CONFIG_REL),
        "manifest_sha256": sha256(root / MANIFEST_REL),
    }
    write_exclusive_json(attempt_path, marker)
    os.mkdir(run_directory, 0o755)
    for name in ("INPUT", "STRU", "KPT"):
        shutil.copyfile(input_directory / name, run_directory / name)
    shutil.copyfile(input_directory / "metadata.json", run_directory / "input_metadata.json")
    pseudo = row["pseudopotential"]
    pseudo_source = root / "assets/pseudo" / pseudo
    if sha256(pseudo_source) != row["pseudopotential_sha256"]:
        raise RuntimeError(f"pseudopotential differs for {experiment_id}")
    shutil.copyfile(pseudo_source, run_directory / pseudo)
    checksums = {
        name: sha256(run_directory / name)
        for name in ("INPUT", "STRU", "KPT", pseudo, "input_metadata.json")
    }
    (run_directory / "input_sha256.json").write_text(
        json.dumps(checksums, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    affinity_directory = run_directory / "rank_affinity"
    affinity_directory.mkdir()
    run_environment = dict(environment)
    run_environment["M_OFDFT_AFFINITY_EVIDENCE_DIR"] = str(affinity_directory)
    abacus = Path(config["execution"]["abacus_path"])
    if sha256(abacus) != config["execution"]["abacus_sha256"]:
        raise RuntimeError("ABACUS binary hash differs")
    mpirun = Path(run_environment["M_OFDFT_PREFIX"]) / "bin/mpirun"
    wrapper = root / WRAPPER_REL
    command = [
        str(mpirun), "--bind-to", "none", "-np", "4", str(wrapper), "40", str(abacus)
    ]
    runtime = {
        "protocol_revision": PROTOCOL,
        "experiment_id": experiment_id,
        "hostname": socket.gethostname(),
        "parent_process_affinity": sorted(os.sched_getaffinity(0)),
        "rank_logical_cpus_requested": [40, 41, 42, 43],
        "command": command,
        "abacus_path": str(abacus),
        "abacus_sha256": sha256(abacus),
        "mpirun_path": str(mpirun),
        "mpirun_sha256": sha256(mpirun),
        "wrapper_path": str(wrapper),
        "wrapper_sha256": sha256(wrapper),
        "git_head": git_head,
        "started_utc": now(),
    }
    (run_directory / "runtime.json").write_text(
        json.dumps(runtime, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    start = time.monotonic()
    timed_command = ["/usr/bin/time", "-v", *command]
    timed_out = False
    with (run_directory / "run.stdout").open("wb") as stdout, (
        run_directory / "resource_usage.txt"
    ).open("wb") as stderr:
        try:
            process = subprocess.run(
                timed_command,
                cwd=run_directory,
                env=run_environment,
                stdout=stdout,
                stderr=stderr,
                timeout=7200,
                check=False,
            )
            return_code = process.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            return_code = 124
    elapsed = time.monotonic() - start
    suffix = row["suffix"]
    log_path = run_directory / f"OUT.{suffix}" / "running_scf.log"
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
    affinity_records = []
    for rank in range(4):
        path = affinity_directory / f"rank-{rank}.txt"
        affinity_records.append(path.read_text(encoding="utf-8") if path.is_file() else "")
    accepted = (
        return_code == 0
        and not timed_out
        and "#SCF IS CONVERGED#" in log_text
        and "!!SCF IS NOT CONVERGED!!" not in log_text
        and all(f"requested_cpu={40 + rank}\n" in affinity_records[rank] for rank in range(4))
        and all(f"affinity_after={40 + rank}\n" in affinity_records[rank] for rank in range(4))
        and (run_directory / f"OUT.{suffix}" / "chg.cube").is_file()
        and (run_directory / f"OUT.{suffix}" / "pot.cube").is_file()
    )
    result = {
        "protocol_revision": PROTOCOL,
        "experiment_id": experiment_id,
        "status": "accepted" if accepted else "failed",
        "runner_return_code": return_code,
        "timed_out": timed_out,
        "elapsed_seconds": elapsed,
        "hostname": socket.gethostname(),
        "rank_affinity_verified": accepted or (
            len(affinity_records) == 4
            and all(f"affinity_after={40 + rank}\n" in affinity_records[rank] for rank in range(4))
        ),
        "log_path": str(log_path.relative_to(run_directory)) if log_path.is_file() else None,
        "log_sha256": sha256(log_path) if log_path.is_file() else None,
        "finished_utc": now(),
    }
    (run_directory / "runner_result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    write_exclusive_json(completion_path, result)
    if not accepted:
        raise RuntimeError(f"formal run failed and may not be retried: {experiment_id}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="consume all 15 formal IDs in order")
    args = parser.parse_args()
    if not args.all:
        raise SystemExit("only the all-or-stop formal launch is supported; pass --all")
    root = Path(__file__).resolve().parents[1]
    if git(root, "status", "--porcelain"):
        raise SystemExit("formal launch requires a clean worktree")
    git_head = git(root, "rev-parse", "HEAD")
    config = json.loads((root / CONFIG_REL).read_text(encoding="utf-8"))
    if config.get("protocol_revision") != PROTOCOL or config.get("status") != "preregistered":
        raise SystemExit("config protocol/status differs")
    if socket.gethostname() != config["execution"]["hostname_exact"]:
        raise SystemExit("formal launch is restricted to node01")
    rows = read_manifest(root / MANIFEST_REL)
    if [row["experiment_id"] for row in rows] != config["run_ids_exact"]:
        raise SystemExit("manifest order differs from frozen run IDs")
    state = Path(config["state_root"])
    try:
        os.mkdir(state, 0o755)
    except FileExistsError as error:
        raise SystemExit(f"state root already exists; no retry is permitted: {state}") from error
    for child in ("attempts", "completions", "runs"):
        os.mkdir(state / child, 0o755)
    launch = {
        "schema_version": 1,
        "protocol_revision": PROTOCOL,
        "status": "running",
        "created_utc": now(),
        "git_head": git_head,
        "git_branch": git(root, "branch", "--show-current"),
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "config_sha256": sha256(root / CONFIG_REL),
        "manifest_sha256": sha256(root / MANIFEST_REL),
        "runner_sha256": sha256(root / RUNNER_REL),
        "wrapper_sha256": sha256(root / WRAPPER_REL),
        "retry_policy": "none",
    }
    write_exclusive_json(state / "launch.json", launch)
    environment = activated_environment(root)
    results: list[dict[str, object]] = []
    try:
        for row in rows:
            result = run_one(root, state, config, row, environment, git_head)
            results.append(result)
            print(
                f"accepted {result['experiment_id']} in {float(result['elapsed_seconds']):.1f} s "
                f"({len(results)}/15)",
                flush=True,
            )
    except BaseException as error:
        terminal = {
            "protocol_revision": PROTOCOL,
            "status": "failed",
            "runner_return_code": 1,
            "accepted_run_count": len(results),
            "failure": f"{type(error).__name__}: {error}",
            "finished_utc": now(),
        }
        write_exclusive_json(state / "terminal.json", terminal)
        raise
    terminal = {
        "protocol_revision": PROTOCOL,
        "status": "accepted",
        "runner_return_code": 0,
        "accepted_run_count": len(results),
        "accepted_ids": [row["experiment_id"] for row in results],
        "elapsed_seconds_total": sum(float(row["elapsed_seconds"]) for row in results),
        "finished_utc": now(),
    }
    write_exclusive_json(state / "terminal.json", terminal)
    print(json.dumps(terminal, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
