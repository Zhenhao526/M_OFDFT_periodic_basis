#!/usr/bin/env python3
"""MPI rank barrier used to make ABACUS PID/map capture deterministic."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


RANK_ENV_KEYS = ("OMPI_COMM_WORLD_RANK", "PMIX_RANK", "PMI_RANK")
PREFIX_ENV_KEYS = ("OPAL_PREFIX", "PRTE_PREFIX", "PMIX_PREFIX", "UCX_MODULE_DIR")


def _rank() -> int:
    values = []
    for key in RANK_ENV_KEYS:
        value = os.environ.get(key)
        if value is None:
            continue
        try:
            values.append((key, int(value)))
        except ValueError as error:
            raise ValueError(f"invalid {key}: {value}") from error
    if not values:
        raise ValueError("MPI rank environment is missing")
    ranks = {value for _, value in values}
    if len(ranks) != 1:
        raise ValueError(f"MPI rank environment disagrees: {values}")
    return values[0][1]


def _atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _canonicalize_spawned_rank_environment() -> dict:
    """Collapse MPI-injected duplicates without admitting any foreign prefix."""

    recovery_prefix = os.environ.get("M_OFDFT_RECOVERY_PREFIX")
    if not recovery_prefix or not Path(recovery_prefix).is_absolute():
        raise ValueError("frozen recovery prefix is missing or not absolute")
    expected_library = str(Path(recovery_prefix) / "lib")
    library_value = os.environ.get("LD_LIBRARY_PATH")
    library_entries = library_value.split(":") if library_value else []
    if not library_entries or any(
        entry != expected_library for entry in library_entries
    ):
        raise ValueError(
            "spawned rank LD_LIBRARY_PATH contains a non-recovery component"
        )
    for key in ("OPAL_PREFIX", "PMIX_PREFIX", "UCX_MODULE_DIR"):
        if os.environ.get(key) != recovery_prefix:
            raise ValueError(f"spawned rank {key} differs from recovery prefix")
    # PRRTE deliberately removes PRTE_PREFIX while spawning an application.
    # Restore the frozen value before evidence capture and ABACUS exec, but
    # reject any nonempty alternative value.
    if os.environ.get("PRTE_PREFIX") not in (None, "", recovery_prefix):
        raise ValueError("spawned rank PRTE_PREFIX differs from recovery prefix")
    if os.environ.get("CUDA_CACHE_DISABLE") != "1":
        raise ValueError("spawned rank CUDA JIT cache is not disabled")
    incoming = {
        "schema_version": 1,
        "prefix_environment": {
            key: os.environ.get(key) for key in PREFIX_ENV_KEYS
        },
        "ld_library_path_raw": library_value,
        "ld_library_path_entries": library_entries,
        "cuda_cache_disable": os.environ.get("CUDA_CACHE_DISABLE"),
        "normalization_action": (
            "restore_four_recovery_prefixes_and_collapse_identical_library_entries"
        ),
    }
    for key in PREFIX_ENV_KEYS:
        os.environ[key] = recovery_prefix
    os.environ["LD_LIBRARY_PATH"] = expected_library
    return incoming


def main() -> int:
    if len(sys.argv) < 2:
        print("rank wrapper requires the frozen ABACUS path", file=sys.stderr)
        return 2
    handshake = Path(os.environ["M_OFDFT_RANK_HANDSHAKE_DIR"])
    expected_ranks = int(os.environ["M_OFDFT_MPI_AUDIT_EXPECTED_RANKS"])
    timeout_seconds = float(os.environ.get("M_OFDFT_RANK_HANDSHAKE_TIMEOUT", "120"))
    rank = _rank()
    if rank < 0 or rank >= expected_ranks:
        raise ValueError(f"rank {rank} is outside 0..{expected_ranks - 1}")
    expected_abacus = Path(os.environ["M_OFDFT_EXPECTED_ABACUS"]).resolve(strict=True)
    requested_abacus = Path(sys.argv[1]).resolve(strict=True)
    if requested_abacus != expected_abacus:
        raise ValueError("rank wrapper target differs from frozen ABACUS")
    incoming_environment = _canonicalize_spawned_rank_environment()

    ready_directory = handshake / "ready"
    release_directory = handshake / "release"
    failure_directory = handshake / "failure"
    for directory in (ready_directory, release_directory, failure_directory):
        directory.mkdir(parents=True, exist_ok=True)
    ready_path = ready_directory / f"rank-{rank}.json"
    if ready_path.exists() or ready_path.is_symlink():
        raise ValueError(f"duplicate rank handshake: {ready_path}")
    _atomic_json(
        ready_path,
        {
            "schema_version": 1,
            "pid": os.getpid(),
            "rank": rank,
            "expected_ranks": expected_ranks,
            "target_abacus_realpath": str(expected_abacus),
            "incoming_environment_normalization": incoming_environment,
            "prefix_environment": {
                key: os.environ.get(key)
                for key in ("OPAL_PREFIX", "PRTE_PREFIX", "PMIX_PREFIX", "UCX_MODULE_DIR")
            },
            "runtime_environment": {
                key: os.environ.get(key)
                for key in (
                    "PATH",
                    "LD_LIBRARY_PATH",
                    "LD_PRELOAD",
                    "CMAKE_PREFIX_PATH",
                    "MKLROOT",
                    "HOME",
                    "OMP_NUM_THREADS",
                    "CUDA_CACHE_DISABLE",
                )
            },
            "wrapper_state": "ready_before_exec",
        },
    )

    release_path = release_directory / f"rank-{rank}"
    abort_path = handshake / "abort"
    deadline = time.monotonic() + timeout_seconds
    while not release_path.is_file():
        if abort_path.exists():
            return 98
        if time.monotonic() >= deadline:
            _atomic_json(
                failure_directory / f"rank-{rank}.json",
                {
                    "schema_version": 1,
                    "pid": os.getpid(),
                    "rank": rank,
                    "status": "release_timeout",
                },
            )
            return 98
        time.sleep(0.01)
    try:
        os.execv(str(expected_abacus), [str(expected_abacus), *sys.argv[2:]])
    except OSError as error:
        _atomic_json(
            failure_directory / f"rank-{rank}.json",
            {
                "schema_version": 1,
                "pid": os.getpid(),
                "rank": rank,
                "status": "exec_failed",
                "errno": error.errno,
                "error": str(error),
            },
        )
        return 98


if __name__ == "__main__":
    raise SystemExit(main())
