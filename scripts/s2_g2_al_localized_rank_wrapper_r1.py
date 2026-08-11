#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def topology(cpu: int) -> dict:
    root = Path(f"/sys/devices/system/cpu/cpu{cpu}/topology")
    siblings = sorted(int(value) for value in (root / "thread_siblings_list").read_text().strip().replace("-", ",").split(","))
    return {
        "os_logical_cpu": cpu,
        "physical_package_id": int((root / "physical_package_id").read_text()),
        "sysfs_core_id": int((root / "core_id").read_text()),
        "thread_siblings": siblings,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--expected-cores", required=True)
    args = parser.parse_args()
    expected = [int(value) for value in args.expected_cores.split(",")]
    if args.preflight:
        domain = sorted(expected + [value + 76 for value in expected])
        expected_topology = [topology(cpu) for cpu in domain]
        if sorted({row["sysfs_core_id"] for row in expected_topology}) != expected:
            raise ValueError("registered node topology differs")
        collisions = []
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit() or int(proc.name) == os.getpid():
                continue
            try:
                comm = (proc / "comm").read_text().strip().lower()
                command = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").lower()
                if not any(token in comm or token in command for token in ("abacus", "pw.x", "dftpy", "s2_g2_al_localized")):
                    continue
                status = (proc / "status").read_text()
                allowed_text = next(line.split(":", 1)[1].strip() for line in status.splitlines() if line.startswith("Cpus_allowed_list:"))
                allowed = []
                for part in allowed_text.split(","):
                    if "-" in part:
                        left, right = (int(value) for value in part.split("-"))
                        allowed.extend(range(left, right + 1))
                    else:
                        allowed.append(int(part))
                overlap = sorted(set(domain) & set(allowed))
                if overlap:
                    collisions.append({"pid": int(proc.name), "comm": comm, "overlap": overlap})
            except (FileNotFoundError, PermissionError, StopIteration, ValueError):
                continue
        payload = {"schema_version": 1, "hostname": socket.gethostname(), "registered_logical_domain": domain, "topology": expected_topology, "collisions": collisions, "accepted": not collisions}
        print(json.dumps(payload, sort_keys=True))
        return 0 if not collisions else 3
    if args.binary is None or args.evidence_dir is None:
        raise ValueError("rank mode requires binary and evidence directory")
    rank = int(os.environ["OMPI_COMM_WORLD_RANK"])
    local_rank = int(os.environ["OMPI_COMM_WORLD_LOCAL_RANK"])
    if rank >= len(expected):
        raise ValueError("rank exceeds registered core denominator")
    allowed = sorted(os.sched_getaffinity(0))
    rows = [topology(cpu) for cpu in allowed]
    core_ids = sorted({row["sysfs_core_id"] for row in rows})
    packages = sorted({row["physical_package_id"] for row in rows})
    if core_ids != [expected[rank]] or packages != [0]:
        raise ValueError(f"rank {rank} binding differs: cores={core_ids}, packages={packages}")
    payload = {
        "schema_version": 1,
        "rank": rank,
        "local_rank": local_rank,
        "hostname": socket.gethostname(),
        "pid_before_exec": os.getpid(),
        "os_logical_cpu_affinity": allowed,
        "topology": rows,
        "expected_sysfs_core_id": expected[rank],
        "binary_sha256": sha256(args.binary),
        "rank_wrapper_sha256": sha256(Path(__file__).resolve()),
        "accepted": True,
    }
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    path = args.evidence_dir / f"rank_{rank:03d}.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(payload, handle, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.execve(str(args.binary), [str(args.binary)], os.environ.copy())
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
