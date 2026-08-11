#!/usr/bin/env python3
"""Record each MPI rank's physical-core binding immediately before execve."""

from __future__ import annotations

import argparse
import json
import os
import socket
from pathlib import Path


def read_topology(cpu: int) -> dict[str, int]:
    root = Path(f"/sys/devices/system/cpu/cpu{cpu}/topology")
    return {
        "logical_cpu": cpu,
        "core_id": int((root / "core_id").read_text(encoding="ascii").strip()),
        "socket_id": int((root / "physical_package_id").read_text(encoding="ascii").strip()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--expected-cores", required=True)
    args = parser.parse_args()
    rank = int(os.environ["OMPI_COMM_WORLD_RANK"])
    local_rank = int(os.environ["OMPI_COMM_WORLD_LOCAL_RANK"])
    expected = [int(value) for value in args.expected_cores.split(",")]
    if rank >= len(expected):
        raise ValueError("MPI rank exceeds expected core list")
    logical_cpus = sorted(os.sched_getaffinity(0))
    topology = [read_topology(cpu) for cpu in logical_cpus]
    core_ids = sorted({item["core_id"] for item in topology})
    if core_ids != [expected[rank]]:
        raise ValueError(f"rank {rank} core binding {core_ids} != {[expected[rank]]}")
    payload = {
        "schema_version": 1,
        "rank": rank,
        "local_rank": local_rank,
        "hostname": socket.gethostname(),
        "pid_before_exec": os.getpid(),
        "logical_cpu_affinity": logical_cpus,
        "topology": topology,
        "physical_core_ids": core_ids,
        "expected_physical_core_id": expected[rank],
        "accepted": True,
    }
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    path = args.evidence_dir / f"rank_{rank:03d}.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.execve(args.binary, [args.binary], os.environ.copy())
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
