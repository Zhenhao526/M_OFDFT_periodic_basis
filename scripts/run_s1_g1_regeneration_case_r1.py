#!/usr/bin/env python3
"""Execute exactly one registered G1 regeneration case once."""

from __future__ import annotations

import argparse
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

from parse_s1_single import parse_log
from s1_g1_regeneration_10_common import (
    PROTOCOL_REVISION,
    canonical_bytes,
    git,
    read_config,
    read_manifest,
    scientific_case_analysis,
    sha256,
    source_paths,
    validate_registration,
    verify_source,
    write_exclusive,
)


def utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_cpu_list(value: str) -> list[int]:
    output = set()
    for part in value.split(","):
        fields = part.split("-", 1)
        start = int(fields[0])
        stop = int(fields[-1])
        output.update(range(start, stop + 1))
    return sorted(output)


def process_record(pid: int) -> dict[str, object] | None:
    root = Path("/proc") / str(pid)
    try:
        stat = (root / "stat").read_text(encoding="ascii")
        close = stat.rfind(")")
        fields = stat[close + 2 :].split()
        status = (root / "status").read_text(encoding="ascii")
        allowed = next(
            line.split(":", 1)[1].strip()
            for line in status.splitlines()
            if line.startswith("Cpus_allowed_list:")
        )
        command = [
            item.decode("utf-8", errors="replace")
            for item in (root / "cmdline").read_bytes().split(b"\0")
            if item
        ]
        return {
            "pid": pid,
            "ppid": int(fields[1]),
            "start_time_ticks": int(fields[19]),
            "comm": stat[stat.find("(") + 1 : close],
            "argv": command,
            "cpus_allowed_list": allowed,
            "cpus": parse_cpu_list(allowed),
        }
    except (FileNotFoundError, ProcessLookupError, PermissionError, StopIteration, ValueError):
        return None


def snapshot_process_tree(root_pid: int) -> list[dict[str, object]]:
    records = {}
    for path in Path("/proc").iterdir():
        if path.name.isdigit():
            record = process_record(int(path.name))
            if record is not None:
                records[int(path.name)] = record
    members = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, record in records.items():
            if pid not in members and record["ppid"] in members:
                members.add(pid)
                changed = True
    return [records[pid] for pid in sorted(members) if pid in records]


def runtime_environment(case_directory: Path, config: dict) -> dict[str, str]:
    prefix = config["runtime"]["prefix"]
    environment = {
        "HOME": str(case_directory / "runtime_home"),
        "USER": "shenwei01",
        "LOGNAME": "shenwei01",
        "PATH": f"{prefix}/bin:/usr/bin:/bin",
        "LD_LIBRARY_PATH": f"{prefix}/lib",
        "CMAKE_PREFIX_PATH": prefix,
        "MKLROOT": prefix,
        "OPAL_PREFIX": prefix,
        "PRTE_PREFIX": prefix,
        "PMIX_PREFIX": prefix,
        "UCX_MODULE_DIR": prefix,
        "LC_ALL": "C",
        "TZ": "UTC",
        "TMPDIR": "/tmp",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "CUDA_CACHE_DISABLE": "1",
    }
    return environment


def command_argv(config: dict, case_directory: Path) -> list[str]:
    tools = config["runtime"]["tools"]
    cpu = ",".join(str(value) for value in config["cpu_list"])
    return [
        tools["taskset"]["path"],
        "--cpu-list",
        cpu,
        tools["time"]["path"],
        "-v",
        "-o",
        str(case_directory / "resource_usage.txt"),
        tools["mpirun"]["path"],
        "--bind-to",
        "none",
        "-np",
        str(config["rank_count"]),
        tools["taskset"]["path"],
        "--cpu-list",
        cpu,
        tools["abacus"]["path"],
    ]


def run_solver(case_directory: Path, row: dict[str, str], config: dict) -> dict[str, object]:
    command = command_argv(config, case_directory)
    started_wall = time.monotonic()
    started_utc = utc()
    timeout = int(row["timeout_seconds"])
    observed: dict[tuple[int, int], dict[str, object]] = {}
    timed_out = False
    stdout_path = case_directory / "run.stdout"
    stderr_path = case_directory / "run.stderr"
    with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
        process = subprocess.Popen(
            command,
            cwd=case_directory,
            env=runtime_environment(case_directory, config),
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            for record in snapshot_process_tree(process.pid):
                observed[(int(record["pid"]), int(record["start_time_ticks"]))] = record
            if time.monotonic() >= deadline:
                timed_out = True
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                break
            time.sleep(0.25)
        return_code = process.wait()
    wall_seconds = time.monotonic() - started_wall
    all_records = sorted(
        observed.values(), key=lambda item: (int(item["pid"]), int(item["start_time_ticks"]))
    )
    abacus = [
        record for record in all_records
        if record["argv"]
        and Path(record["argv"][0]).name.startswith("abacus_pw_para")
    ]
    metadata = {
        "schema_version": 1,
        "protocol_revision": PROTOCOL_REVISION,
        "case_id": row["case_id"],
        "runner_commit": git(Path(config["project_root"]), "rev-parse", "HEAD"),
        "hostname": socket.gethostname(),
        "requested_cpu_list": config["cpu_list"],
        "runner_affinity_at_launch": sorted(os.sched_getaffinity(0)),
        "rank_count": config["rank_count"],
        "command_argv": command,
        "environment": runtime_environment(case_directory, config),
        "started_utc": started_utc,
        "finished_utc": utc(),
        "wall_seconds": wall_seconds,
        "timeout_seconds": timeout,
        "timed_out": timed_out,
        "return_code": return_code,
        "stdout_sha256": sha256(stdout_path),
        "stderr_sha256": sha256(stderr_path),
        "resource_usage_sha256": (
            sha256(case_directory / "resource_usage.txt")
            if (case_directory / "resource_usage.txt").is_file()
            else None
        ),
        "observed_affinity": {
            "sampling_period_seconds": 0.25,
            "all_descendants": all_records,
            "abacus_processes": abacus,
        },
    }
    write_exclusive(case_directory / "runtime_metadata.json", metadata)
    return metadata


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-id", required=True)
    parser.add_argument(
        "--config", type=Path,
        default=project_root / "config/S1_g1_regeneration_10_r1.json",
    )
    parser.add_argument(
        "--manifest", type=Path,
        default=project_root / "config/S1_g1_regeneration_10_r1_manifest.tsv",
    )
    args = parser.parse_args()
    config_path, manifest_path = args.config.resolve(), args.manifest.resolve()
    config, rows, registration = validate_registration(
        project_root, config_path, manifest_path,
        require_clean=True, require_state_absent=False,
    )
    matches = [row for row in rows if row["case_id"] == args.case_id]
    if len(matches) != 1:
        raise SystemExit("case ID is outside the fixed denominator")
    row = matches[0]
    state = Path(config["external_state_root"])
    launch_path = state / "launch.json"
    if not launch_path.is_file() or launch_path.is_symlink():
        raise SystemExit("formal launcher record is absent")
    launch = json.loads(launch_path.read_text(encoding="utf-8"))
    head = git(project_root, "rev-parse", "HEAD")
    if (
        launch.get("protocol_revision") != PROTOCOL_REVISION
        or launch.get("runner_commit") != head
        or launch.get("status") != "running"
    ):
        raise SystemExit("formal launcher binding differs")
    index = int(row["execution_index"])
    for prior in rows[: index - 1]:
        status_path = state / "cases" / prior["case_id"] / "case_status.json"
        if not status_path.is_file() or json.loads(
            status_path.read_text(encoding="utf-8")
        ).get("status") != "accepted":
            raise SystemExit("prior fixed case is not accepted")
    for candidate in rows[index - 1 :]:
        if (state / "attempts" / f"{candidate['case_id']}.json").exists():
            raise SystemExit("case attempt marker already exists; retry forbidden")
    source_before = verify_source(project_root, row)
    runner_argv = [sys.executable, "-s", str(Path(__file__).resolve()), "--case-id", row["case_id"]]
    marker = {
        "schema_version": 1,
        "protocol_revision": PROTOCOL_REVISION,
        "case_id": row["case_id"],
        "source_run_id": row["source_run_id"],
        "source_tree_oid": row["source_tree_oid"],
        "status": "formal_attempt_started",
        "created_utc": utc(),
        "runner_commit": head,
        "runner_command_argv": runner_argv,
        "retry_policy": config["retry_policy"],
        "launch_sha256": sha256(launch_path),
    }
    write_exclusive(state / "attempts" / f"{row['case_id']}.json", marker)
    case_directory = state / "cases" / row["case_id"]
    os.mkdir(case_directory)
    try:
        sources = source_paths(project_root, row)
        for name in ("INPUT", "STRU", "KPT", "input_metadata.json", row["pseudopotential"]):
            shutil.copyfile(sources[name], case_directory / name)
        os.mkdir(case_directory / "runtime_home")
        runtime = run_solver(case_directory, row, config)
        if runtime["return_code"] != 0 or runtime["timed_out"]:
            raise RuntimeError("solver invocation failed or timed out")
        replay_log = case_directory / row["source_log_relpath"]
        parsed = parse_log(
            replay_log.read_text(encoding="utf-8", errors="strict"),
            float(row["expected_electrons"]), int(row["atom_count"]), row["solver"],
        )
        write_exclusive(case_directory / "result.json", parsed)
        if not parsed["converged"]:
            raise RuntimeError("replay did not converge")
        analysis = scientific_case_analysis(project_root, state, row)
        write_exclusive(case_directory / "scientific_result.json", analysis)
        if not analysis["accepted"]:
            raise RuntimeError("case scientific gates rejected")
        source_after = verify_source(project_root, row)
        if source_before != source_after:
            raise RuntimeError("source changed during replay")
        write_exclusive(case_directory / "case_status.json", {
            "schema_version": 1,
            "protocol_revision": PROTOCOL_REVISION,
            "case_id": row["case_id"],
            "status": "accepted",
            "runner_commit": head,
            "finished_utc": utc(),
            "wall_seconds": runtime["wall_seconds"],
            "source_integrity_before_and_after_equal": True,
        })
        print(f"accepted {row['case_id']} wall_seconds={runtime['wall_seconds']:.3f}")
        return 0
    except Exception as error:
        failure_path = case_directory / "failure.json"
        if not failure_path.exists():
            write_exclusive(failure_path, {
                "schema_version": 1,
                "protocol_revision": PROTOCOL_REVISION,
                "case_id": row["case_id"],
                "status": "failed_no_retry",
                "error_type": type(error).__name__,
                "error": str(error),
                "finished_utc": utc(),
            })
        print(f"FAILED {row['case_id']}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
