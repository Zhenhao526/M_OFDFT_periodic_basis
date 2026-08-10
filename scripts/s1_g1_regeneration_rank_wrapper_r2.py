#!/usr/bin/env python3
"""Deterministic per-rank proof/ACK barrier before ABACUS exec for regen10 R2."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


PROTOCOL_REVISION = "S1-G1-REGENERATION-10-R2"


def utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical(payload: object) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def publish_exclusive(path: Path, payload: object) -> None:
    temporary = path.parent / f".{path.name}.tmp-{os.getpid()}-{time.time_ns()}"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    linked = False
    try:
        view = memoryview(canonical(payload))
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short rank-evidence write")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.link(temporary, path)
        linked = True
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        if not linked and path.exists():
            raise FileExistsError(f"rank evidence already exists: {path}")


def process_start_time_ticks() -> int:
    raw = Path("/proc/self/stat").read_text(encoding="ascii").strip()
    close = raw.rfind(")")
    fields = raw[close + 2 :].split()
    return int(fields[19])


def parse_cpu_list(value: str) -> list[int]:
    cpus = []
    for part in value.split(","):
        bounds = [int(item) for item in part.split("-", 1)]
        cpus.extend(range(bounds[0], bounds[-1] + 1))
    if len(cpus) != 4 or len(set(cpus)) != 4:
        raise ValueError("rank wrapper requires exactly four distinct CPUs")
    return cpus


def stable_object(path: Path) -> tuple[dict, str]:
    first = path.read_bytes()
    first_hash = hashlib.sha256(first).hexdigest()
    second = path.read_bytes()
    if second != first:
        raise ValueError(f"barrier token changed while reading: {path}")
    payload = json.loads(first.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"barrier token is not an object: {path}")
    return payload, first_hash


def wait_token(path: Path, case_id: str, deadline: float) -> tuple[dict, str]:
    while time.monotonic() < deadline:
        if path.is_file() and not path.is_symlink():
            try:
                payload, digest = stable_object(path)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
                time.sleep(0.01)
                continue
            if (
                payload.get("protocol_revision") != PROTOCOL_REVISION
                or payload.get("case_id") != case_id
            ):
                raise ValueError(f"barrier token binding differs: {path.name}")
            return payload, digest
        time.sleep(0.01)
    raise TimeoutError(f"rank barrier timed out waiting for {path.name}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--proof-dir", type=Path, required=True)
    parser.add_argument("--cpu-list", required=True)
    parser.add_argument("--abacus", type=Path, required=True)
    parser.add_argument("--runner-commit", required=True)
    args = parser.parse_args()
    proof_dir = args.proof_dir.resolve()
    if not proof_dir.is_dir() or proof_dir.is_symlink():
        raise SystemExit("rank proof directory is absent or symbolic")
    try:
        world_rank = int(os.environ["OMPI_COMM_WORLD_RANK"])
        world_size = int(os.environ["OMPI_COMM_WORLD_SIZE"])
        local_rank = int(os.environ["OMPI_COMM_WORLD_LOCAL_RANK"])
        local_size = int(os.environ["OMPI_COMM_WORLD_LOCAL_SIZE"])
    except (KeyError, ValueError) as error:
        raise SystemExit("rank wrapper requires OpenMPI rank identity") from error
    if world_size != 4 or local_size != 4 or world_rank != local_rank:
        raise SystemExit("rank wrapper requires one-node ranks 0..3")
    cpus = parse_cpu_list(args.cpu_list)
    os.sched_setaffinity(0, {cpus[local_rank]})
    affinity = sorted(os.sched_getaffinity(0))
    if affinity != [cpus[local_rank]]:
        raise SystemExit("rank wrapper failed to set exact single-CPU affinity")
    wrapper = Path(__file__).resolve()
    abacus = args.abacus.resolve()
    start_ticks = process_start_time_ticks()
    deadline = time.monotonic() + 60.0
    proof_path = proof_dir / f"rank-{world_rank:03d}.proof.json"
    proof = {
        "schema_version": 1,
        "protocol_revision": PROTOCOL_REVISION,
        "case_id": args.case_id,
        "rank": world_rank,
        "world_size": world_size,
        "local_rank": local_rank,
        "local_size": local_size,
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "start_time_ticks": start_ticks,
        "hostname": socket.gethostname(),
        "affinity": affinity,
        "requested_cpu_list": cpus,
        "wrapper_path": str(wrapper),
        "wrapper_sha256": sha256(wrapper),
        "abacus_path": str(abacus),
        "abacus_sha256": sha256(abacus),
        "runner_commit": args.runner_commit,
        "created_utc": utc(),
    }
    publish_exclusive(proof_path, proof)
    proof_sha = sha256(proof_path)
    go, go_sha = wait_token(proof_dir / "GO.json", args.case_id, deadline)
    if go.get("proof_sha256", {}).get(str(world_rank)) != proof_sha:
        raise SystemExit("GO token does not bind this rank proof")
    if process_start_time_ticks() != start_ticks:
        raise SystemExit("rank PID identity changed before ACK")
    ack_path = proof_dir / f"rank-{world_rank:03d}.ack.json"
    ack = {
        "schema_version": 1,
        "protocol_revision": PROTOCOL_REVISION,
        "case_id": args.case_id,
        "rank": world_rank,
        "pid": os.getpid(),
        "start_time_ticks": start_ticks,
        "hostname": socket.gethostname(),
        "affinity": sorted(os.sched_getaffinity(0)),
        "proof_sha256": proof_sha,
        "go_sha256": go_sha,
        "created_utc": utc(),
    }
    publish_exclusive(ack_path, ack)
    ack_sha = sha256(ack_path)
    execute, _ = wait_token(proof_dir / "EXEC.json", args.case_id, deadline)
    if execute.get("ack_sha256", {}).get(str(world_rank)) != ack_sha:
        raise SystemExit("EXEC token does not bind this rank ACK")
    if sorted(os.sched_getaffinity(0)) != [cpus[local_rank]]:
        raise SystemExit("rank affinity changed before ABACUS exec")
    os.execve(str(abacus), [str(abacus)], dict(os.environ))
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
