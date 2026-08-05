#!/usr/bin/env python3
"""Versioned KMP shared-memory lifecycle contract for S1-G1 R2 runs."""

from __future__ import annotations

import ast
import csv
import json
import re
from pathlib import Path, PurePosixPath


PROTOCOL_REVISION = "S1-G1-ELECTRON-NUMBER-R2-KMP-RUNTIME-CONTRACT-v1"
CONTRACT_SCHEMA_VERSION = 1
EXPECTED_RANK_COUNT = 4
KMP_PATTERN = r"^/dev/shm/__KMP_REGISTERED_LIB_[1-9][0-9]*_0$"

_KMP_PATH = re.compile(KMP_PATTERN)
_KMP_PREFIX = "/dev/shm/__KMP_REGISTERED_LIB_"
_KMP_MARKER = "__KMP_REGISTERED_LIB_"
_TRACE_NAME = re.compile(r"trace\.([1-9][0-9]*)\Z")
_QUOTED_STRING = re.compile(r'"(?:\\.|[^"\\])*"')
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_OBJECT_HEADER = (
    "pid",
    "role",
    "rank",
    "mapped_path",
    "loaded_realpath",
    "loaded_sha256",
    "classification",
)
_CREATE_FLAGS = "O_RDWR|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC"
_READ_FLAGS = "O_RDONLY|O_NOFOLLOW|O_CLOEXEC"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _read_regular_text(path: Path) -> str:
    _require(path.is_file() and not path.is_symlink(), f"not a regular file: {path}")
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise ValueError(f"cannot read {path}: {error}") from error


def _decode_quoted(value: str) -> str:
    try:
        decoded = ast.literal_eval(value)
    except (SyntaxError, ValueError):
        return value[1:-1]
    return decoded if isinstance(decoded, str) else value[1:-1]


def _rank_pids(audit: dict) -> dict[int, int]:
    raw = audit.get("rank_pids")
    expected_keys = {str(rank) for rank in range(EXPECTED_RANK_COUNT)}
    _require(isinstance(raw, dict), "audit rank_pids must be an object")
    _require(set(raw) == expected_keys, "audit rank_pids must contain ranks 0..3")
    _require(
        all(
            isinstance(pid, int) and not isinstance(pid, bool) and pid > 0
            for pid in raw.values()
        ),
        "audit rank PIDs must be positive integers",
    )
    _require(
        len(set(raw.values())) == EXPECTED_RANK_COUNT,
        "audit rank PIDs must be unique",
    )
    return {int(rank): raw[str(rank)] for rank in range(EXPECTED_RANK_COUNT)}


def _load_audit(
    audit_directory: Path, require_registered_mapping_pattern: bool
) -> tuple[dict, dict[int, int]]:
    audit_path = audit_directory / "audit.json"
    try:
        audit = json.loads(_read_regular_text(audit_path))
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid runtime audit JSON: {error}") from error
    _require(isinstance(audit, dict), "runtime audit JSON must be an object")
    patterns = audit.get("transient_mapping_patterns")
    _require(
        isinstance(patterns, list) and all(isinstance(item, str) for item in patterns),
        "audit transient_mapping_patterns must be a string list",
    )
    if require_registered_mapping_pattern:
        _require(
            patterns.count(KMP_PATTERN) == 1 and patterns[-1] == KMP_PATTERN,
            "audit must append the exact KMP transient pattern once",
        )
    else:
        _require(
            KMP_PATTERN not in patterns,
            "R1 bridge audit must predate the KMP transient pattern",
        )
    return audit, _rank_pids(audit)


def _kmp_path_pid(path: str) -> int:
    _require(_KMP_PATH.fullmatch(path) is not None, f"KMP path is out of contract: {path}")
    pid_text = path[len(_KMP_PREFIX) : -len("_0")]
    return int(pid_text)


def _event_kind(line: str, path: str) -> str:
    escaped = re.escape(path)
    create = re.fullmatch(
        rf'openat\(AT_FDCWD, "{escaped}", {re.escape(_CREATE_FLAGS)}, 0666\) = [0-9]+',
        line,
    )
    if create is not None:
        return "create"
    read = re.fullmatch(
        rf'openat\(AT_FDCWD, "{escaped}", {re.escape(_READ_FLAGS)}\) = [0-9]+',
        line,
    )
    if read is not None:
        return "read"
    unlink = re.fullmatch(rf'unlink\("{escaped}"\) = 0', line)
    if unlink is not None:
        return "unlink"
    raise ValueError(f"unsupported or unsuccessful KMP strace event: {line}")


def _validate_trace_lifecycles(
    trace_directory: Path, rank_pids: dict[int, int]
) -> tuple[dict[int, list[str]], int]:
    _require(
        trace_directory.is_dir() and not trace_directory.is_symlink(),
        f"invalid strace directory: {trace_directory}",
    )
    rank_by_pid = {pid: rank for rank, pid in rank_pids.items()}
    events_by_rank: dict[int, list[str]] = {
        rank: [] for rank in range(EXPECTED_RANK_COUNT)
    }
    seen_rank_trace_pids: set[int] = set()
    successful_syscalls = 0

    try:
        entries = sorted(trace_directory.iterdir(), key=lambda path: path.name)
    except OSError as error:
        raise ValueError(f"cannot enumerate strace directory: {error}") from error
    for path in entries:
        if path.is_symlink():
            raise ValueError(f"symbolic strace artifact is not allowed: {path.name}")
        if not path.is_file():
            continue
        text = _read_regular_text(path)
        name_match = _TRACE_NAME.fullmatch(path.name)
        if name_match is None:
            _require(
                _KMP_MARKER not in text,
                f"KMP event found outside canonical trace file: {path.name}",
            )
            continue
        trace_pid = int(name_match.group(1))
        if trace_pid in rank_by_pid:
            seen_rank_trace_pids.add(trace_pid)
        for raw_line in text.splitlines():
            if _KMP_MARKER not in raw_line:
                continue
            line = raw_line.strip()
            quoted_paths = [
                _decode_quoted(value)
                for value in _QUOTED_STRING.findall(line)
                if _KMP_MARKER in value
            ]
            _require(
                len(quoted_paths) == 1,
                f"KMP event must contain one quoted KMP path: {line}",
            )
            kmp_path = quoted_paths[0]
            path_pid = _kmp_path_pid(kmp_path)
            _require(
                trace_pid in rank_by_pid,
                f"KMP event belongs to non-rank trace PID {trace_pid}",
            )
            rank = rank_by_pid[trace_pid]
            _require(
                path_pid == trace_pid == rank_pids[rank],
                f"rank {rank} KMP path PID does not equal rank PID",
            )
            expected_path = f"{_KMP_PREFIX}{trace_pid}_0"
            _require(kmp_path == expected_path, f"rank {rank} KMP path differs")
            events_by_rank[rank].append(_event_kind(line, kmp_path))
            successful_syscalls += 1

    _require(
        seen_rank_trace_pids == set(rank_pids.values()),
        "one or more rank trace files are missing",
    )
    expected_lifecycle = ["create", "read", "unlink"]
    for rank in range(EXPECTED_RANK_COUNT):
        _require(
            events_by_rank[rank] == expected_lifecycle,
            f"rank {rank} KMP lifecycle must be exactly create/read/unlink once",
        )
    _require(
        successful_syscalls == EXPECTED_RANK_COUNT * len(expected_lifecycle),
        "KMP successful syscall count differs from 12",
    )
    return events_by_rank, successful_syscalls


def _load_object_rows(objects_path: Path) -> list[dict[str, str]]:
    _require(
        objects_path.is_file() and not objects_path.is_symlink(),
        f"not a regular file: {objects_path}",
    )
    try:
        with objects_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            _require(
                tuple(reader.fieldnames or ()) == _OBJECT_HEADER,
                "runtime objects.tsv header differs from contract",
            )
            return list(reader)
    except (OSError, csv.Error) as error:
        raise ValueError(f"cannot parse runtime objects.tsv: {error}") from error


def _row_rank(row: dict[str, str], rank_by_pid: dict[int, int], label: str) -> int:
    try:
        pid = int(row.get("pid", ""))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} mapping has invalid PID") from error
    _require(pid in rank_by_pid, f"{label} mapping PID is not a rank PID: {pid}")
    rank = rank_by_pid[pid]
    _require(row.get("role") == "rank", f"{label} mapping role differs from rank")
    _require(row.get("rank") == str(rank), f"{label} mapping rank differs from PID")
    return rank


def _is_libomp_row(row: dict[str, str], expected_path: str, expected_realpath: str) -> bool:
    mapped = row.get("mapped_path", "")
    loaded = row.get("loaded_realpath", "")
    return (
        mapped == expected_path
        or loaded == expected_realpath
        or PurePosixPath(mapped).name == "libomp.so"
        or PurePosixPath(loaded).name == "libomp.so"
    )


def _validate_object_rows(
    rows: list[dict[str, str]],
    rank_pids: dict[int, int],
    expected_libomp_path: str,
    expected_libomp_realpath: str,
    expected_libomp_sha256: str,
    require_registered_mapping_pattern: bool,
) -> tuple[dict[int, int], dict[int, int]]:
    rank_by_pid = {pid: rank for rank, pid in rank_pids.items()}
    libomp_counts = {rank: 0 for rank in range(EXPECTED_RANK_COUNT)}
    kmp_counts = {rank: 0 for rank in range(EXPECTED_RANK_COUNT)}

    for row in rows:
        mapped = row.get("mapped_path", "")
        loaded = row.get("loaded_realpath", "")
        if _KMP_MARKER in mapped or _KMP_MARKER in loaded:
            rank = _row_rank(row, rank_by_pid, "KMP")
            expected_kmp_path = f"{_KMP_PREFIX}{rank_pids[rank]}_0"
            _require(
                _kmp_path_pid(mapped) == rank_pids[rank],
                f"rank {rank} mapped KMP path PID differs",
            )
            _require(
                mapped == expected_kmp_path and loaded == expected_kmp_path,
                f"rank {rank} mapped KMP path/realpath differs",
            )
            expected_classification = (
                "transient_system"
                if require_registered_mapping_pattern
                else "unexpected"
            )
            _require(
                row.get("classification") == expected_classification,
                f"rank {rank} mapped KMP classification differs",
            )
            _require(
                row.get("loaded_sha256") == "",
                f"rank {rank} mapped KMP row must not have a SHA-256",
            )
            kmp_counts[rank] += 1
            _require(
                kmp_counts[rank] <= 1,
                f"rank {rank} has more than one captured KMP mapping",
            )

        if _is_libomp_row(row, expected_libomp_path, expected_libomp_realpath):
            rank = _row_rank(row, rank_by_pid, "libomp.so")
            _require(
                row.get("mapped_path") == expected_libomp_path,
                f"rank {rank} libomp.so path differs",
            )
            _require(
                row.get("loaded_realpath") == expected_libomp_realpath,
                f"rank {rank} libomp.so realpath differs",
            )
            _require(
                row.get("loaded_sha256") == expected_libomp_sha256,
                f"rank {rank} libomp.so SHA-256 differs",
            )
            _require(
                row.get("classification") == "recovery_runtime",
                f"rank {rank} libomp.so classification differs",
            )
            libomp_counts[rank] += 1

    for rank in range(EXPECTED_RANK_COUNT):
        _require(
            libomp_counts[rank] == 1,
            f"rank {rank} must have exactly one recovery libomp.so mapping",
        )
    return libomp_counts, kmp_counts


def validate_kmp_runtime_contract(
    run_directory: Path | str,
    expected_libomp_path: Path | str,
    expected_libomp_realpath: Path | str,
    expected_libomp_sha256: str,
    *,
    require_registered_mapping_pattern: bool = True,
) -> dict:
    """Validate one four-rank KMP lifecycle and return its frozen summary.

    The helper intentionally ignores every non-KMP, non-``libomp.so`` mapping;
    the pre-existing R1 validator remains authoritative for those objects.
    Contract violations are reported by raising :class:`ValueError`.  The
    default is the registered R2 contract.  Setting
    ``require_registered_mapping_pattern=False`` selects the explicit R1
    calibration bridge: the pattern must be absent and a captured KMP row, if
    any, must retain R1's exact ``unexpected`` classification.
    """

    run_directory = Path(run_directory)
    audit_directory = run_directory / "mpi_runtime_audit"
    libomp_path = str(Path(expected_libomp_path))
    libomp_realpath = str(Path(expected_libomp_realpath))
    libomp_sha256 = str(expected_libomp_sha256)
    _require(
        isinstance(require_registered_mapping_pattern, bool),
        "require_registered_mapping_pattern must be boolean",
    )
    _require(PurePosixPath(libomp_path).name == "libomp.so", "expected libomp path differs")
    _require(
        _HEX_SHA256.fullmatch(libomp_sha256) is not None,
        "expected libomp SHA-256 must be 64 lowercase hex characters",
    )

    _audit, rank_pids = _load_audit(
        audit_directory, require_registered_mapping_pattern
    )
    events_by_rank, successful_syscalls = _validate_trace_lifecycles(
        audit_directory / "strace", rank_pids
    )
    object_rows = _load_object_rows(audit_directory / "objects.tsv")
    libomp_counts, kmp_counts = _validate_object_rows(
        object_rows,
        rank_pids,
        libomp_path,
        libomp_realpath,
        libomp_sha256,
        require_registered_mapping_pattern,
    )
    captured_mapping_count = sum(kmp_counts.values())
    ranks = [
        {
            "rank": rank,
            "pid": rank_pids[rank],
            "kmp_path": f"{_KMP_PREFIX}{rank_pids[rank]}_0",
            "lifecycle": events_by_rank[rank],
            "successful_syscall_count": len(events_by_rank[rank]),
            "captured_mapping_count": kmp_counts[rank],
            "libomp_mapping_count": libomp_counts[rank],
        }
        for rank in range(EXPECTED_RANK_COUNT)
    ]
    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "protocol_revision": PROTOCOL_REVISION,
        "accepted": True,
        "contract_mode": (
            "registered_r2"
            if require_registered_mapping_pattern
            else "r1_calibration_bridge"
        ),
        "registered_mapping_pattern_required": require_registered_mapping_pattern,
        "kmp_pattern": KMP_PATTERN,
        "rank_count": EXPECTED_RANK_COUNT,
        "lifecycle_count": EXPECTED_RANK_COUNT,
        "successful_syscall_count": successful_syscalls,
        "captured_mapping_count": captured_mapping_count,
        "libomp_mapping_count": sum(libomp_counts.values()),
        "libomp_path": libomp_path,
        "libomp_realpath": libomp_realpath,
        "libomp_sha256": libomp_sha256,
        "ranks": ranks,
    }


__all__ = (
    "CONTRACT_SCHEMA_VERSION",
    "EXPECTED_RANK_COUNT",
    "KMP_PATTERN",
    "PROTOCOL_REVISION",
    "validate_kmp_runtime_contract",
)
