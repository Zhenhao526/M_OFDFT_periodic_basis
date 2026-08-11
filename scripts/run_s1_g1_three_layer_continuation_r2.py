#!/usr/bin/env python3
"""Execute continuation IDs only after the committed recovery barrier."""

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

from parse_s1_g1_three_layer_continuation_r2 import parse_run
from s1_g1_three_layer_continuation_r2_common import (
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


PREREGISTRATION_PATH = Path("config/S1_g1_three_layer_continuation_r2_preregistration.json")


REGISTERED_CODE = (
    "docs/S1_G1_THREE_LAYER_CONTINUATION_R2_PROTOCOL.md",
    "config/S1_g1_three_layer_continuation_r2.json",
    "config/S1_g1_three_layer_continuation_r2_manifest.tsv",
    "scripts/s1_g1_three_layer_continuation_r2_common.py",
    "scripts/generate_s1_g1_three_layer_continuation_r2.py",
    "scripts/fetch_s1_g1_three_layer_pseudos.py",
    "scripts/s1_g1_three_layer_continuation_r2_rank_wrapper.py",
    "scripts/recover_s1_g1_three_layer_continuation_r2.py",
    "scripts/parse_s1_g1_three_layer_continuation_r2.py",
    "scripts/run_s1_g1_three_layer_continuation_r2.py",
    "scripts/analyze_s1_g1_three_layer_continuation_r2.py",
    "scripts/validate_s1_g1_three_layer_continuation_r2.py",
    "tests/test_s1_g1_three_layer_continuation_r2.py",
    PREREGISTRATION_PATH.as_posix(),
    "config/S1_g1_three_layer_r1.json",
    "config/S1_g1_three_layer_r1_manifest.tsv",
    "scripts/s1_g1_three_layer_common.py",
    "scripts/parse_s1_g1_three_layer_r1.py",
    "scripts/analyze_s1_g1_three_layer_r1.py",
    "scripts/s1_g1_thermodynamic_label_common.py",
    "scripts/s1_electron_number_common.py",
    "scripts/analyze_s1_eos.py",
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


def parse_cpu_list(value: str) -> set[int]:
    cpus: set[int] = set()
    for token in value.strip().split(","):
        if not token:
            continue
        if "-" in token:
            first, last = (int(part) for part in token.split("-", 1))
            require(first <= last, "invalid CPU range")
            cpus.update(range(first, last + 1))
        else:
            cpus.add(int(token))
    require(cpus, "empty CPU list")
    return cpus


def ancestor_pids(proc_root: Path, pid: int) -> set[int]:
    ancestors: set[int] = set()
    current = pid
    while current > 1 and current not in ancestors:
        ancestors.add(current)
        try:
            tail = (proc_root / str(current) / "stat").read_text(encoding="ascii").rsplit(")", 1)[1].split()
            current = int(tail[1])
        except (OSError, ValueError, IndexError):
            break
    ancestors.add(1)
    return ancestors


def inspect_core_collisions(
    config: dict,
    *,
    sys_cpu_root: Path = Path("/sys/devices/system/cpu"),
    proc_root: Path = Path("/proc"),
    excluded_pids: set[int] | None = None,
) -> dict:
    online = parse_cpu_list((sys_cpu_root / "online").read_text(encoding="ascii"))
    socket_id = int(config["runtime"]["physical_socket_id"])
    core_ids = [int(value) for value in config["runtime"]["physical_core_ids"]]
    topology = []
    target_cpus: set[int] = set()
    for cpu in sorted(online):
        root = sys_cpu_root / f"cpu{cpu}" / "topology"
        try:
            package = int((root / "physical_package_id").read_text(encoding="ascii"))
            core = int((root / "core_id").read_text(encoding="ascii"))
        except OSError:
            continue
        if package == socket_id and core in core_ids:
            siblings = parse_cpu_list((root / "thread_siblings_list").read_text(encoding="ascii"))
            require(cpu in siblings, "CPU sibling topology differs")
            target_cpus.update(siblings)
            topology.append({"logical_cpu": cpu, "socket_id": package, "physical_core_id": core, "thread_siblings": sorted(siblings)})
    require({row["physical_core_id"] for row in topology} == set(core_ids), "registered physical core topology incomplete")
    excluded = set(excluded_pids or ()) | ancestor_pids(proc_root, os.getpid())
    collisions = []
    scanned_narrow_tasks = 0
    for process in sorted((path for path in proc_root.iterdir() if path.name.isdigit()), key=lambda path: int(path.name)):
        pid = int(process.name)
        if pid in excluded:
            continue
        try:
            process_status = (process / "status").read_text(encoding="utf-8")
            uid_row = next(line for line in process_status.splitlines() if line.startswith("Uid:"))
            if int(uid_row.split()[1]) != os.getuid():
                continue
            cmdline = (process / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", errors="replace").strip()
            try:
                comm = (process / "comm").read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                comm = ""
            abacus_process = "abacus" in f"{comm} {cmdline}".lower()
            tasks = sorted((process / "task").iterdir(), key=lambda path: int(path.name))
        except (OSError, StopIteration, ValueError):
            continue
        for task in tasks:
            try:
                status = (task / "status").read_text(encoding="utf-8")
                allowed_row = next(line for line in status.splitlines() if line.startswith("Cpus_allowed_list:"))
                allowed = parse_cpu_list(allowed_row.split(":", 1)[1])
            except (OSError, StopIteration, ValueError):
                continue
            if len(allowed) >= len(online) and not abacus_process:
                continue
            scanned_narrow_tasks += 1
            overlap = sorted(allowed & target_cpus)
            if overlap:
                collisions.append({"pid": pid, "tid": int(task.name), "allowed_logical_cpus": sorted(allowed), "overlap_logical_cpus": overlap, "comm": comm, "cmdline": cmdline, "abacus_process": abacus_process})
    return {
        "schema_version": 1,
        "hostname": socket.gethostname(),
        "physical_socket_id": socket_id,
        "physical_core_ids": core_ids,
        "target_sibling_logical_cpus": sorted(target_cpus),
        "topology": topology,
        "online_logical_cpu_count": len(online),
        "excluded_process_ids": sorted(excluded),
        "scanned_narrow_affinity_task_count": scanned_narrow_tasks,
        "collisions": collisions,
        "accepted": not collisions,
        "created_utc": utc_now(),
    }


def detached_launcher_proof() -> dict:
    pid = os.getpid()
    sid = os.getsid(0)
    tty = {str(fd): os.isatty(fd) for fd in (0, 1, 2)}
    sighup_ignored = signal.getsignal(signal.SIGHUP) == signal.SIG_IGN
    accepted = sid == pid and not any(tty.values()) and sighup_ignored
    return {
        "schema_version": 1,
        "pid": pid,
        "ppid": os.getppid(),
        "session_id": sid,
        "session_leader": sid == pid,
        "isatty": tty,
        "sighup_ignored": sighup_ignored,
        "accepted": accepted,
        "required_launch_shape": "setsid with stdin /dev/null and stdout/stderr regular files",
        "created_utc": utc_now(),
    }


def safe_print(value: str) -> None:
    try:
        print(value, flush=True)
    except (BrokenPipeError, OSError):
        pass


def validate_recovery_barrier(project_root: Path, state_root: Path, config: dict, head: str) -> tuple[dict, str]:
    external = state_root / "barriers" / "r1_p0_recovery.json"
    relative = Path(config["versioned_recovery_barrier"])
    versioned = project_root / relative
    require(external.is_file() and not external.is_symlink(), "external recovery barrier missing")
    require(versioned.is_file() and not versioned.is_symlink(), "versioned recovery barrier missing")
    require(external.read_bytes() == versioned.read_bytes(), "external/versioned recovery barriers differ")
    require_tracked_matches_head(project_root, [relative])
    payload = read_json(external)
    require(isinstance(payload, dict) and payload.get("status") == "accepted", "scientific recovery barrier not accepted")
    require(payload.get("protocol_revision") == config["protocol_revision"], "recovery protocol differs")
    require(payload.get("source_operational_phase_accepted") is False, "R1 operational overclaim")
    require(payload.get("scientific_p0_recovery_status") == "accepted", "scientific P0 not recovered")
    require(payload.get("accepted_source_ids") == config["source_r1"]["accepted_ids"], "recovery denominator differs")
    require(payload.get("new_run_count") == 0, "recovery source counted as new runs")
    require(payload.get("config_sha256") == sha256_file(project_root / CONFIG_PATH), "recovery config binding differs")
    require(payload.get("manifest_sha256") == sha256_file(project_root / MANIFEST_PATH), "recovery manifest binding differs")
    prereg = payload.get("continuation_prereg_commit")
    require(isinstance(prereg, str) and len(prereg) == 40, "recovery prereg commit differs")
    ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", prereg, head], cwd=project_root)
    require(ancestor.returncode == 0, "recovery prereg commit is not ancestor of runner")
    return payload, sha256_file(external)


def validate_phase_barrier(state_root: Path, phase: str, config: dict, head: str, recovery_sha256: str) -> None:
    require(not (state_root / "terminal.json").exists(), "continuation terminal already exists")
    if phase == "al_eos":
        require(not (state_root / "session.json").exists(), "continuation solver session already exists")
        for name in ("attempts", "runs", "accepted", "phases", "preflight"):
            require(not (state_root / name).exists(), f"fresh continuation denominator already exists: {name}")
        return
    phase_payload = read_json(state_root / "phases" / "al_eos.json")
    require(isinstance(phase_payload, dict) and phase_payload.get("status") == "accepted", "Al EOS phase incomplete")
    expected_ids = [f"S1-20260810-{number:03d}" for number in range(327, 333)]
    require(phase_payload.get("protocol_revision") == config["protocol_revision"] and phase_payload.get("phase") == "al_eos", "Al EOS phase identity differs")
    require(phase_payload.get("runner_commit") == head and phase_payload.get("recovery_barrier_sha256") == recovery_sha256, "Al EOS phase provenance differs")
    require(phase_payload.get("accepted_ids") == expected_ids and phase_payload.get("accepted_count") == len(expected_ids), "Al EOS phase denominator differs")
    session_payload = read_json(state_root / "session.json")
    require(isinstance(session_payload, dict), "Al EOS session missing")
    require(phase_payload.get("session_sha256") == sha256_file(state_root / "session.json"), "Al EOS session binding differs")
    require(phase_payload.get("config_sha256") == session_payload.get("config_sha256"), "Al EOS config binding differs")
    require(phase_payload.get("manifest_sha256") == session_payload.get("manifest_sha256"), "Al EOS manifest binding differs")
    result_map = phase_payload.get("accepted_result_sha256")
    require(isinstance(result_map, dict) and list(result_map) == expected_ids, "Al EOS result denominator differs")
    for experiment_id in expected_ids:
        marker = read_json(state_root / "accepted" / f"{experiment_id}.json")
        require(isinstance(marker, dict) and marker.get("status") == "accepted", "Al EOS accepted marker differs")
        require(marker.get("result_sha256") == result_map[experiment_id], "Al EOS accepted result SHA differs")
    require(set(phase_payload.get("per_run_core_collision_preflight_sha256", {})) == set(expected_ids), "Al EOS phase preflight denominator differs")


def initialize_or_validate_session(
    state_root: Path,
    project_root: Path,
    config: dict,
    head: str,
    phase: str,
    recovery: dict,
    recovery_sha256: str,
    detached_proof_sha256: str,
    phase_core_preflight_sha256: str,
) -> dict:
    session_path = state_root / "session.json"
    if phase == "al_eos":
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
            "recovery_barrier_sha256": recovery_sha256,
            "recovery_prereg_commit": recovery["continuation_prereg_commit"],
            "recovery_source_runner_commit": recovery["source_runner_commit"],
            "initial_detached_launcher_proof_sha256": detached_proof_sha256,
            "initial_core_collision_preflight_sha256": phase_core_preflight_sha256,
            "retry_policy": "same_id_forbidden_new_revision_and_new_ids_only",
        }
        atomic_write(session_path, canonical_json_bytes(payload), exclusive=True)
        return payload
    payload = read_json(session_path)
    require(isinstance(payload, dict), "session must be object")
    require(payload.get("runner_commit") == head, "runner commit changed between phases")
    require(payload.get("config_sha256") == sha256_file(project_root / CONFIG_PATH), "config changed")
    require(payload.get("manifest_sha256") == sha256_file(project_root / MANIFEST_PATH), "manifest changed")
    require(payload.get("recovery_barrier_sha256") == recovery_sha256, "recovery barrier changed")
    require(payload.get("hostname") == socket.gethostname(), "session hostname changed")
    return payload


def write_failure(run_dir: Path, experiment_id: str, stage: str, message: str, return_code: int | None) -> None:
    payload = {
        "schema_version": 1,
        "protocol_revision": "S1-G1-THREE-LAYER-CONTINUATION-20260810-R2",
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
    core_preflight_path: Path,
    core_preflight_sha256: str,
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
        "core_collision_preflight_path": core_preflight_path.name,
        "core_collision_preflight_sha256": core_preflight_sha256,
        "core_collision_preflight_accepted": True,
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
            "physical_socket_id": config["runtime"]["physical_socket_id"],
            "core_collision_preflight_path": core_preflight_path.name,
            "core_collision_preflight_sha256": core_preflight_sha256,
        },
        "pseudo_runtime_identity": pseudo_identity,
    }
    atomic_write(run_dir / "metadata.json", canonical_json_bytes(metadata), exclusive=True)
    atomic_write(run_dir / "pseudo_identity.json", canonical_json_bytes(pseudo_identity), exclusive=True)
    affinity_dir = run_dir / "affinity"
    affinity_dir.mkdir()
    wrapper = project_root / "scripts/s1_g1_three_layer_continuation_r2_rank_wrapper.py"
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


def write_terminal(
    state_root: Path,
    project_root: Path,
    config: dict,
    session: dict,
    recovery_sha256: str,
) -> dict:
    expected_ids = [f"S1-20260810-{value:03d}" for value in range(327, 335)]
    require(not (state_root / "terminal.json").exists(), "continuation terminal already exists")
    require({path.stem for path in (state_root / "attempts").glob("*.json")} == set(expected_ids), "terminal attempt denominator differs")
    require({path.stem for path in (state_root / "accepted").glob("*.json")} == set(expected_ids), "terminal accepted denominator differs")
    require({path.name for path in (state_root / "runs").iterdir() if path.is_dir()} == set(expected_ids), "terminal run denominator differs")
    require(not list((state_root / "runs").rglob("failure.json")), "terminal contains preserved failure")
    phase_ids = {
        "al_eos": expected_ids[:6],
        "mg_required": expected_ids[6:],
    }
    phase_sha256 = {}
    accepted_result_sha256 = {}
    attempt_sha256 = {}
    accepted_marker_sha256 = {}
    runner_return_sha256 = {}
    result_sha256 = {}
    for phase, ids in phase_ids.items():
        path = state_root / "phases" / f"{phase}.json"
        marker = read_json(path)
        require(isinstance(marker, dict) and marker.get("status") == "accepted", f"terminal phase rejected: {phase}")
        require(marker.get("accepted_ids") == ids and marker.get("accepted_count") == len(ids), f"terminal phase denominator differs: {phase}")
        require(marker.get("runner_commit") == session["runner_commit"] and marker.get("recovery_barrier_sha256") == recovery_sha256, f"terminal phase provenance differs: {phase}")
        phase_sha256[phase] = sha256_file(path)
        accepted_result_sha256.update(marker["accepted_result_sha256"])
    require(list(accepted_result_sha256) == expected_ids, "terminal result denominator differs")
    for experiment_id in expected_ids:
        attempt_path = state_root / "attempts" / f"{experiment_id}.json"
        accepted_path = state_root / "accepted" / f"{experiment_id}.json"
        return_path = state_root / "runs" / experiment_id / "runner_return.json"
        result_path = state_root / "runs" / experiment_id / "result.json"
        attempt = read_json(attempt_path)
        accepted = read_json(accepted_path)
        returned = read_json(return_path)
        require(isinstance(attempt, dict) and attempt.get("status") == "formal_attempt_started" and attempt.get("experiment_id") == experiment_id, "terminal attempt identity differs")
        require(isinstance(accepted, dict) and accepted.get("status") == "accepted" and accepted.get("experiment_id") == experiment_id, "terminal accepted identity differs")
        require(isinstance(returned, dict) and returned.get("return_code") == 0 and returned.get("experiment_id") == experiment_id, "terminal runner return differs")
        require(accepted.get("result_sha256") == accepted_result_sha256[experiment_id] == sha256_file(result_path), "terminal accepted/result binding differs")
        attempt_sha256[experiment_id] = sha256_file(attempt_path)
        accepted_marker_sha256[experiment_id] = sha256_file(accepted_path)
        runner_return_sha256[experiment_id] = sha256_file(return_path)
        result_sha256[experiment_id] = sha256_file(result_path)
    payload = {
        "schema_version": 1,
        "protocol_revision": config["protocol_revision"],
        "status": "accepted",
        "runner_commit": session["runner_commit"],
        "session_sha256": sha256_file(state_root / "session.json"),
        "config_sha256": sha256_file(project_root / CONFIG_PATH),
        "manifest_sha256": sha256_file(project_root / MANIFEST_PATH),
        "recovery_barrier_sha256": recovery_sha256,
        "phase_marker_sha256": phase_sha256,
        "phase_ids": phase_ids,
        "accepted_ids": expected_ids,
        "attempted_count": len(expected_ids),
        "accepted_count": len(expected_ids),
        "failed_count": 0,
        "retried_count": 0,
        "runner_return_code": 0,
        "attempt_sha256": attempt_sha256,
        "accepted_marker_sha256": accepted_marker_sha256,
        "runner_return_sha256": runner_return_sha256,
        "accepted_result_sha256": accepted_result_sha256,
        "result_sha256": result_sha256,
        "created_utc": utc_now(),
    }
    atomic_write(state_root / "terminal.json", canonical_json_bytes(payload), exclusive=True)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True, choices=("al_eos", "mg_required"))
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
    recovery, recovery_sha256 = validate_recovery_barrier(project_root, state_root, config, head)
    validate_phase_barrier(state_root, args.phase, config, head, recovery_sha256)
    detached = detached_launcher_proof()
    require(detached["accepted"] is True, "runner is not an independently detached session")
    phase_collision = inspect_core_collisions(config)
    require(phase_collision["accepted"] is True, "registered physical cores have a live sibling collision")
    detached_path = state_root / "preflight" / f"{args.phase}_detached_launch.json"
    phase_collision_path = state_root / "preflight" / f"{args.phase}_core_collision.json"
    atomic_write(detached_path, canonical_json_bytes(detached), exclusive=True)
    atomic_write(phase_collision_path, canonical_json_bytes(phase_collision), exclusive=True)
    detached_sha256 = sha256_file(detached_path)
    phase_collision_sha256 = sha256_file(phase_collision_path)
    session = initialize_or_validate_session(
        state_root,
        project_root,
        config,
        head,
        args.phase,
        recovery,
        recovery_sha256,
        detached_sha256,
        phase_collision_sha256,
    )
    cache = Path(config["external_pseudo_cache"])
    for material, pseudo in config["pseudodojo"]["materials"].items():
        validate_pseudo(cache / pseudo["basename"], material, config)
    selected = phase_rows(rows, args.phase)
    accepted: list[dict] = []
    for row in selected:
        experiment_id = row["experiment_id"]
        case_collision = inspect_core_collisions(config)
        require(case_collision["accepted"] is True, f"registered physical cores have a live sibling collision before {experiment_id}")
        case_collision_path = state_root / "preflight" / f"{experiment_id}.json"
        atomic_write(case_collision_path, canonical_json_bytes(case_collision), exclusive=True)
        case_collision_sha256 = sha256_file(case_collision_path)
        safe_print(f"START {row['experiment_id']} phase={args.phase}")
        accepted_row = run_one(
            project_root,
            state_root,
            cache,
            row,
            config,
            head,
            case_collision_path,
            case_collision_sha256,
        )
        accepted.append(accepted_row)
        safe_print(f"ACCEPTED {row['experiment_id']} duration_seconds={accepted_row['duration_seconds']:.3f}")
    phase_payload = {
        "schema_version": 1,
        "protocol_revision": config["protocol_revision"],
        "status": "accepted",
        "phase": args.phase,
        "runner_commit": session["runner_commit"],
        "session_sha256": sha256_file(state_root / "session.json"),
        "config_sha256": sha256_file(project_root / CONFIG_PATH),
        "manifest_sha256": sha256_file(project_root / MANIFEST_PATH),
        "accepted_ids": [row["experiment_id"] for row in accepted],
        "accepted_count": len(accepted),
        "accepted_result_sha256": {
            row["experiment_id"]: row["result_sha256"] for row in accepted
        },
        "detached_launcher_proof_sha256": detached_sha256,
        "phase_core_collision_preflight_sha256": phase_collision_sha256,
        "per_run_core_collision_preflight_sha256": {
            row["experiment_id"]: read_json(state_root / "attempts" / f"{row['experiment_id']}.json")["core_collision_preflight_sha256"]
            for row in selected
        },
        "recovery_barrier_sha256": recovery_sha256,
        "created_utc": utc_now(),
    }
    atomic_write(state_root / "phases" / f"{args.phase}.json", canonical_json_bytes(phase_payload), exclusive=True)
    if args.phase == "mg_required":
        terminal = write_terminal(state_root, project_root, config, session, recovery_sha256)
        safe_print(json.dumps(terminal, sort_keys=True))
    safe_print(json.dumps(phase_payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        raise SystemExit(main())
    except Exception as error:
        print(f"FATAL: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        raise
