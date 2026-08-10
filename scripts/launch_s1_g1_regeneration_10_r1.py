#!/usr/bin/env python3
"""Single command launcher for the fixed G1 ten-case regeneration audit."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from s1_g1_regeneration_10_common import (
    PROTOCOL_REVISION,
    canonical_bytes,
    git,
    validate_registration,
    write_atomic,
    write_exclusive,
)


def utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_cpu_list(value: str) -> set[int]:
    output = set()
    for part in value.split(","):
        bounds = [int(item) for item in part.split("-", 1)]
        output.update(range(bounds[0], bounds[-1] + 1))
    return output


def is_compute_rank_command(command: list[str]) -> bool:
    if not command:
        return False
    executable = Path(command[0]).name
    return executable.startswith("abacus_pw_para") or "rank_wrapper" in executable


def conflicting_compute_processes(requested: set[int]) -> list[dict[str, object]]:
    conflicts = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = [
                item.decode("utf-8", errors="replace")
                for item in (entry / "cmdline").read_bytes().split(b"\0")
                if item
            ]
            if not is_compute_rank_command(command):
                continue
            status = (entry / "status").read_text(encoding="ascii")
            allowed_text = next(
                line.split(":", 1)[1].strip()
                for line in status.splitlines()
                if line.startswith("Cpus_allowed_list:")
            )
            allowed = parse_cpu_list(allowed_text)
        except (FileNotFoundError, PermissionError, ProcessLookupError, StopIteration, ValueError):
            continue
        overlap = sorted(requested & allowed)
        if overlap:
            conflicts.append({
                "pid": int(entry.name), "argv": command,
                "cpus_allowed_list": allowed_text, "overlap": overlap,
            })
    return conflicts


def runner_command(config: dict, row: dict[str, str]) -> list[str]:
    return [
        config["runtime"]["tools"]["python"]["path"],
        "-s",
        str(Path(config["project_root"]) / "scripts/run_s1_g1_regeneration_case_r1.py"),
        "--case-id",
        row["case_id"],
    ]


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--all", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
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
        require_clean=args.all, require_state_absent=True,
    )
    commands = [runner_command(config, row) for row in rows]
    if args.dry_run:
        print(json.dumps({
            "protocol_revision": PROTOCOL_REVISION,
            "external_state_root": config["external_state_root"],
            "case_count": len(rows),
            "commands": commands,
        }, indent=2, sort_keys=True))
        return 0
    requested = set(config["cpu_list"])
    conflicts = conflicting_compute_processes(requested)
    if conflicts:
        print(json.dumps({"cpu_conflicts": conflicts}, indent=2), file=sys.stderr)
        return 2
    state = Path(config["external_state_root"])
    os.mkdir(state)
    os.mkdir(state / "attempts")
    os.mkdir(state / "cases")
    head = git(project_root, "rev-parse", "HEAD")
    launcher_argv = [
        config["runtime"]["tools"]["python"]["path"], "-s",
        str(Path(__file__).resolve()), "--all",
    ]
    launch = {
        "schema_version": 1,
        "protocol_revision": PROTOCOL_REVISION,
        "status": "running",
        "created_utc": utc(),
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "runner_commit": head,
        "launcher_command_argv": launcher_argv,
        "case_commands_exact": commands,
        "external_state_root": str(state),
        "retry_policy": config["retry_policy"],
        "rank_count": config["rank_count"],
        "cpu_list": config["cpu_list"],
        "cpu_conflicts_at_launch": conflicts,
        "registration": registration,
    }
    write_exclusive(state / "launch.json", launch)
    log_path = state / "launcher.log"
    with log_path.open("xb") as log:
        for index, (row, command) in enumerate(zip(rows, commands), 1):
            write_atomic(state / "current.json", canonical_bytes({
                "case_id": row["case_id"], "execution_index": index,
                "status": "running", "updated_utc": utc(),
            }))
            log.write((f"START {row['case_id']} {utc()}\n").encode())
            log.flush()
            process = subprocess.run(
                command, cwd=project_root, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT,
            )
            log.write(
                (f"END {row['case_id']} return_code={process.returncode} {utc()}\n").encode()
            )
            log.flush()
            os.fsync(log.fileno())
            if process.returncode:
                write_exclusive(state / "terminal.json", {
                    "schema_version": 1,
                    "protocol_revision": PROTOCOL_REVISION,
                    "status": "failed_no_retry",
                    "failed_case_id": row["case_id"],
                    "runner_return_code": process.returncode,
                    "runner_commit": head,
                    "finished_utc": utc(),
                })
                return process.returncode
    accepted = []
    total_wall = 0.0
    for row in rows:
        status = json.loads(
            (state / "cases" / row["case_id"] / "case_status.json").read_text(
                encoding="utf-8"
            )
        )
        if status.get("status") != "accepted":
            raise SystemExit(f"non-accepted terminal case: {row['case_id']}")
        accepted.append(row["case_id"])
        total_wall += float(status["wall_seconds"])
    write_atomic(state / "current.json", canonical_bytes({
        "case_id": None, "execution_index": 10, "status": "terminal_accepted",
        "updated_utc": utc(),
    }))
    write_exclusive(state / "terminal.json", {
        "schema_version": 1,
        "protocol_revision": PROTOCOL_REVISION,
        "status": "accepted",
        "runner_return_code": 0,
        "runner_commit": head,
        "accepted_case_ids": accepted,
        "registered": 10, "attempted": 10, "completed": 10, "accepted": 10,
        "failed": 0, "missing": 0, "skipped": 0, "retried": 0,
        "solver_wall_seconds_sum": total_wall,
        "finished_utc": utc(),
    })
    print(f"accepted 10/10 solver_wall_seconds_sum={total_wall:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
