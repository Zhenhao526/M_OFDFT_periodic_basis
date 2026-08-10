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
from s1_g1_regeneration_10_common_r2 import (
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


def command_argv(
    config: dict, case_directory: Path, row: dict[str, str]
) -> list[str]:
    tools = config["runtime"]["tools"]
    cpu = ",".join(str(value) for value in config["cpu_list"])
    runner_commit = git(Path(config["project_root"]), "rev-parse", "HEAD")
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
        tools["python"]["path"],
        "-s",
        tools["rank_wrapper"]["path"],
        "--case-id",
        row["case_id"],
        "--proof-dir",
        str(case_directory / "rank_proofs"),
        "--cpu-list",
        cpu,
        "--abacus",
        tools["abacus"]["path"],
        "--runner-commit",
        runner_commit,
    ]


def stable_json(path: Path) -> tuple[dict, str]:
    first = path.read_bytes()
    second = path.read_bytes()
    if first != second:
        raise OSError(f"rank evidence changed while reading: {path}")
    payload = json.loads(first.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"rank evidence is not an object: {path}")
    return payload, sha256(path)


def load_rank_set(proof_dir: Path, suffix: str, rank_count: int) -> list[dict] | None:
    paths = [proof_dir / f"rank-{rank:03d}.{suffix}.json" for rank in range(rank_count)]
    if not all(path.is_file() and not path.is_symlink() for path in paths):
        return None
    output = []
    for path in paths:
        payload, digest = stable_json(path)
        output.append({"path": str(path), "sha256": digest, "payload": payload})
    return output


def require_live_identity(payload: dict, expected_cpu: int) -> None:
    record = process_record(int(payload["pid"]))
    if record is None:
        raise ValueError("rank proof PID is not live at barrier")
    if (
        record["start_time_ticks"] != payload["start_time_ticks"]
        or record["cpus"] != [expected_cpu]
    ):
        raise ValueError("rank proof PID/start-time/affinity differs from live /proc")


def validate_proofs(
    records: list[dict], row: dict[str, str], config: dict
) -> dict[str, str]:
    tools = config["runtime"]["tools"]
    expected_cpus = config["cpu_list"]
    runner_commit = git(Path(config["project_root"]), "rev-parse", "HEAD")
    if len(records) != config["rank_count"]:
        raise ValueError("rank proof count differs")
    pids = set()
    proof_hashes = {}
    for rank, record in enumerate(records):
        payload = record["payload"]
        expected = {
            "protocol_revision": PROTOCOL_REVISION,
            "case_id": row["case_id"],
            "rank": rank,
            "world_size": config["rank_count"],
            "local_rank": rank,
            "local_size": config["rank_count"],
            "hostname": config["expected_hostname"],
            "affinity": [expected_cpus[rank]],
            "requested_cpu_list": expected_cpus,
            "wrapper_path": tools["rank_wrapper"]["path"],
            "wrapper_sha256": tools["rank_wrapper"]["sha256"],
            "abacus_path": tools["abacus"]["path"],
            "abacus_sha256": tools["abacus"]["sha256"],
            "runner_commit": runner_commit,
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise ValueError(f"rank proof registration differs for rank {rank}")
        if (
            type(payload.get("pid")) is not int
            or type(payload.get("start_time_ticks")) is not int
            or payload["pid"] <= 0
            or payload["start_time_ticks"] <= 0
            or payload["pid"] in pids
        ):
            raise ValueError("rank proof PID identity is invalid or duplicate")
        pids.add(payload["pid"])
        require_live_identity(payload, expected_cpus[rank])
        proof_hashes[str(rank)] = record["sha256"]
    return proof_hashes


def validate_acks(
    records: list[dict], proofs: list[dict], proof_hashes: dict[str, str],
    go_sha256: str, row: dict[str, str], config: dict,
) -> dict[str, str]:
    ack_hashes = {}
    for rank, (record, proof) in enumerate(zip(records, proofs)):
        payload = record["payload"]
        proof_payload = proof["payload"]
        expected = {
            "protocol_revision": PROTOCOL_REVISION,
            "case_id": row["case_id"],
            "rank": rank,
            "pid": proof_payload["pid"],
            "start_time_ticks": proof_payload["start_time_ticks"],
            "hostname": config["expected_hostname"],
            "affinity": [config["cpu_list"][rank]],
            "proof_sha256": proof_hashes[str(rank)],
            "go_sha256": go_sha256,
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise ValueError(f"rank ACK registration differs for rank {rank}")
        require_live_identity(payload, config["cpu_list"][rank])
        ack_hashes[str(rank)] = record["sha256"]
    return ack_hashes


def run_solver(case_directory: Path, row: dict[str, str], config: dict) -> dict[str, object]:
    proof_dir = case_directory / "rank_proofs"
    proof_dir.mkdir()
    command = command_argv(config, case_directory, row)
    started_wall = time.monotonic()
    started_utc = utc()
    timeout = int(row["timeout_seconds"])
    observed: dict[tuple[int, int], dict[str, object]] = {}
    timed_out = False
    barrier_failure = None
    proofs = None
    proof_hashes = None
    go_sha256 = None
    acks = None
    ack_hashes = None
    exec_sha256 = None
    barrier_finished_seconds = None
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
        next_snapshot = 0.0
        while process.poll() is None:
            now = time.monotonic()
            if now >= next_snapshot:
                for record in snapshot_process_tree(process.pid):
                    observed[(int(record["pid"]), int(record["start_time_ticks"]))] = record
                next_snapshot = now + 0.25
            try:
                if proofs is None:
                    candidate = load_rank_set(proof_dir, "proof", config["rank_count"])
                    if candidate is not None:
                        candidate_hashes = validate_proofs(candidate, row, config)
                        write_exclusive(proof_dir / "GO.json", {
                            "schema_version": 1,
                            "protocol_revision": PROTOCOL_REVISION,
                            "case_id": row["case_id"],
                            "status": "proofs_validated_release_for_ack",
                            "proof_sha256": candidate_hashes,
                            "created_utc": utc(),
                        })
                        proofs, proof_hashes = candidate, candidate_hashes
                        go_sha256 = sha256(proof_dir / "GO.json")
                elif acks is None:
                    candidate = load_rank_set(proof_dir, "ack", config["rank_count"])
                    if candidate is not None:
                        candidate_hashes = validate_acks(
                            candidate, proofs, proof_hashes, go_sha256, row, config
                        )
                        write_exclusive(proof_dir / "EXEC.json", {
                            "schema_version": 1,
                            "protocol_revision": PROTOCOL_REVISION,
                            "case_id": row["case_id"],
                            "status": "acks_validated_release_for_abacus_exec",
                            "proof_sha256": proof_hashes,
                            "go_sha256": go_sha256,
                            "ack_sha256": candidate_hashes,
                            "created_utc": utc(),
                        })
                        acks, ack_hashes = candidate, candidate_hashes
                        exec_sha256 = sha256(proof_dir / "EXEC.json")
                        barrier_finished_seconds = time.monotonic() - started_wall
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                pass
            except Exception as error:
                barrier_failure = f"{type(error).__name__}: {error}"
                os.killpg(process.pid, signal.SIGTERM)
            if time.monotonic() >= deadline:
                timed_out = True
                os.killpg(process.pid, signal.SIGTERM)
            if timed_out or barrier_failure:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                break
            time.sleep(0.01)
        return_code = process.wait()
    wall_seconds = time.monotonic() - started_wall
    all_records = sorted(
        observed.values(), key=lambda item: (int(item["pid"]), int(item["start_time_ticks"]))
    )
    barrier_accepted = (
        proofs is not None
        and acks is not None
        and len(proofs) == len(acks) == config["rank_count"]
        and barrier_failure is None
        and go_sha256 is not None
        and exec_sha256 is not None
        and all(sha256(Path(record["path"])) == record["sha256"] for record in proofs + acks)
    )
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
        "rank_barrier": {
            "mode": "per_rank_o_excl_proof_go_ack_exec_v1",
            "accepted": barrier_accepted,
            "failure": barrier_failure,
            "proofs": proofs,
            "proof_sha256": proof_hashes,
            "go_sha256": go_sha256,
            "acks": acks,
            "ack_sha256": ack_hashes,
            "exec_sha256": exec_sha256,
            "barrier_finished_seconds": barrier_finished_seconds,
            "proof_and_ack_files_immutable_after_run": barrier_accepted,
        },
        "observed_affinity_diagnostic_only": {
            "sampling_period_seconds": 0.25,
            "all_descendants": all_records,
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
        default=project_root / "config/S1_g1_regeneration_10_r2.json",
    )
    parser.add_argument(
        "--manifest", type=Path,
        default=project_root / "config/S1_g1_regeneration_10_r2_manifest.tsv",
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
