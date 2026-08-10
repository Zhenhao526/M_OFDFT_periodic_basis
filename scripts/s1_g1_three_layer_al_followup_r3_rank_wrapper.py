#!/usr/bin/env python3
"""Fail closed on the exact OS-CPU/package/core/sibling contract before smoke or ABACUS."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_cpu_list(text: str) -> list[int]:
    values: set[int] = set()
    for token in text.strip().split(","):
        if not token:
            continue
        if "-" in token:
            start, end = (int(value) for value in token.split("-", 1))
            require(start <= end, "invalid CPU range")
            values.update(range(start, end + 1))
        else:
            values.add(int(token))
    return sorted(values)


def read_topology(logical_cpu: int) -> dict[str, object]:
    root = Path(f"/sys/devices/system/cpu/cpu{logical_cpu}/topology")
    package = int((root / "physical_package_id").read_text(encoding="ascii").strip())
    core = int((root / "core_id").read_text(encoding="ascii").strip())
    return {
        "os_logical_cpu_id": logical_cpu,
        "physical_package_id": package,
        "sysfs_core_id": core,
        "thread_siblings": parse_cpu_list((root / "thread_siblings_list").read_text(encoding="ascii")),
    }


def validate_current_rank(config: dict, rank: int, local_rank: int, mode: str) -> dict:
    runtime = config["runtime"]
    hostname = socket.gethostname()
    require(hostname == runtime["required_hostname"], "rank hostname differs from frozen host")
    require(rank < runtime["rank_count"], "MPI rank exceeds frozen rank count")
    require(local_rank == rank, "single-node local/global rank order differs")
    expected_affinity = runtime["thread_siblings_by_rank"][rank]
    expected_primary = runtime["primary_os_logical_cpu_ids_by_rank"][rank]
    expected_package = runtime["required_physical_package_id"]
    expected_core = runtime["sysfs_core_id_by_rank"][rank]
    logical_affinity = sorted(os.sched_getaffinity(0))
    require(logical_affinity == expected_affinity, f"rank {rank} OS logical affinity {logical_affinity} != {expected_affinity}")
    topology = [read_topology(cpu) for cpu in logical_affinity]
    require(expected_primary in logical_affinity, f"rank {rank} primary OS CPU missing")
    require(all(row["physical_package_id"] == expected_package for row in topology), f"rank {rank} package differs")
    require(all(row["sysfs_core_id"] == expected_core for row in topology), f"rank {rank} sysfs core_id differs")
    require(all(row["thread_siblings"] == expected_affinity for row in topology), f"rank {rank} sibling topology differs")
    return {
        "schema_version": 1,
        "protocol_revision": config["protocol_revision"],
        "mode": mode,
        "rank": rank,
        "local_rank": local_rank,
        "hostname": hostname,
        "pid_before_exec": os.getpid(),
        "os_logical_cpu_affinity": logical_affinity,
        "logical_cpu_affinity": logical_affinity,
        "primary_os_logical_cpu_id": expected_primary,
        "physical_package_id": expected_package,
        "sysfs_core_id": expected_core,
        "thread_siblings": expected_affinity,
        "topology": topology,
        "accepted": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("smoke", "solver"), required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--config-sha256", required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--binary", type=Path)
    args = parser.parse_args()
    require(args.config.is_file() and not args.config.is_symlink(), "config missing")
    require(sha256_file(args.config) == args.config_sha256, "config SHA differs")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    rank = int(os.environ["OMPI_COMM_WORLD_RANK"])
    local_rank = int(os.environ["OMPI_COMM_WORLD_LOCAL_RANK"])
    payload = validate_current_rank(config, rank, local_rank, args.mode)
    payload["config_sha256"] = args.config_sha256
    payload["rank_wrapper_sha256"] = sha256_file(Path(__file__).resolve())
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    path = args.evidence_dir / f"rank_{rank:03d}.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    if args.mode == "smoke":
        return 0
    require(args.binary is not None and args.binary.is_file(), "solver binary missing")
    require(sha256_file(args.binary) == config["runtime"]["binary_sha256"], "solver binary SHA differs")
    os.execve(str(args.binary), [str(args.binary)], os.environ.copy())
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
