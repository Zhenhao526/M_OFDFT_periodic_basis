#!/usr/bin/env python3
"""Shared constants and byte-level helpers for S1-R8 MPI replay."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import subprocess
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from parse_s1_single import (
    ENERGY_PATTERN,
    PRESSURE_PATTERN,
    ZERO_TEMP_ENERGY_PATTERN,
    parse_log,
)


PROTOCOL_REVISION = "S1-R8-RUNTIME-RELOCATION-R2"
CANONICAL_CONFIG_PATH = Path("config/S1_runtime_relocation_equivalence.json")
CANONICAL_MANIFEST_PATH = Path(
    "config/S1_runtime_relocation_equivalence_manifest.tsv"
)
LEGACY_CONFIG_PATH = Path("config/S1_mpi_prefix_equivalence.json")
LEGACY_MANIFEST_PATH = Path("config/S1_mpi_prefix_equivalence_manifest.tsv")
R8_CONFIG_PATH = Path("config/S1_non_equilibrium_convergence.json")
R8_MANIFEST_PATH = Path("config/S1_non_equilibrium_run_manifest.tsv")
DEFAULT_R8_SUMMARY_PATH = Path("analysis/s1/non_equilibrium_convergence_20260805/summary.json")
RUNTIME_SMOKE_ID = "S1-RUNTIME-SMOKE-20260805-074"
RUNTIME_SMOKE_REFERENCE_ID = "S1-20260805-074"
RUNTIME_SMOKE_ROOT = Path("analysis/s1/runtime_relocation_smoke_20260805")
RUNTIME_SMOKE_RUN_DIRECTORY = RUNTIME_SMOKE_ROOT / "run"
DEFAULT_RUNTIME_SMOKE_SUMMARY_PATH = RUNTIME_SMOKE_ROOT / "summary.json"
RUNTIME_SMOKE_EVIDENCE_MANIFEST = RUNTIME_SMOKE_ROOT / "evidence_manifest.tsv"

FIXED_PAIRS = (
    ("S1-20260805-113", "S1-20260805-074", "al", "ofdft_next_cutoff"),
    ("S1-20260805-114", "S1-20260805-081", "al", "ksdft_next_cutoff"),
    ("S1-20260805-115", "S1-20260805-088", "al", "ksdft_next_kmesh"),
    ("S1-20260805-116", "S1-20260805-095", "mg", "ofdft_next_cutoff"),
    ("S1-20260805-117", "S1-20260805-102", "mg", "ksdft_next_cutoff"),
    ("S1-20260805-118", "S1-20260805-109", "mg", "ksdft_next_kmesh"),
)

MANIFEST_HEADER = (
    "replay_experiment_id",
    "reference_experiment_id",
    "input_directory",
    "material",
    "series_id",
    "solver",
    "input_sha256",
    "stru_sha256",
    "kpt_sha256",
    "metadata_sha256",
    "pseudopotential",
    "pseudopotential_sha256",
    "reference_result_path",
    "reference_result_sha256",
    "reference_log_path",
    "reference_log_sha256",
    "reference_experiment_metadata_path",
    "reference_experiment_metadata_sha256",
    "reference_abacus_path",
    "reference_abacus_realpath",
    "reference_abacus_sha256",
    "reference_mpirun_path",
    "reference_mpirun_realpath",
    "reference_mpirun_sha256",
    "config_sha256",
)

REQUIRED_SOURCE_FILES = ("INPUT", "STRU", "KPT", "metadata.json")
HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
TRANSIENT_MAPPING_PATTERNS = (
    r"^/SYSV[0-9A-Fa-f]+$",
    r"^/dev/shm/sm_segment[A-Za-z0-9._-]*$",
    r"^/dev/shm/ucx_shm_posix_[A-Za-z0-9._-]+$",
    (
        r"^/tmp/ompi\.[0-9]+/(?:[A-Za-z0-9._-]+/)*"
        r"(?:pmix-gds-shmem2|shared_mem_cuda_)[A-Za-z0-9._@-]*"
        r"(?:/[A-Za-z0-9._-]+)*$"
    ),
    r"^/tmp/ompi\.[0-9]+/hwloc\.sm$",
)
SYSTEM_MAPPING_ROOTS = ("/usr", "/lib", "/lib64")
SYSTEM_MAPPING_EXACT_PATHS = ("/etc/ld.so.cache",)
REGISTERED_DEVICE_MAPPING_PATTERNS = (
    r"^/dev/infiniband/(?:uverbs[0-9]+|rdma_cm)$",
    r"^/dev/nvidia(?:[0-9]+|ctl|modeset|uvm|uvm-tools)$",
    r"^/dev/nvidia-caps/nvidia-cap[0-9]+$",
)


def registered_old_prefix_failed_probes(old_prefix: Path) -> tuple[dict, ...]:
    """Exact old-prefix ENOENT events allowed inside the isolation namespace."""

    old_prefix = old_prefix.resolve(strict=False)
    return (
        {
            "probe_id": "launcher_classid_stat",
            "path": str(old_prefix / "classid"),
            "syscall": "stat",
            "flags": None,
            "errno": "ENOENT",
            "role": "launcher",
            "rank": None,
            "expected_count": 1,
        },
        {
            "probe_id": "launcher_classid_open",
            "path": str(old_prefix / "classid"),
            "syscall": "openat",
            "flags": "O_RDONLY|O_CLOEXEC",
            "errno": "ENOENT",
            "role": "launcher",
            "rank": None,
            "expected_count": 1,
        },
        {
            "probe_id": "rank_classid_stat",
            "path": str(old_prefix / "classid"),
            "syscall": "stat",
            "flags": None,
            "errno": "ENOENT",
            "role": "rank",
            "rank": "each",
            "expected_count_per_rank": 1,
        },
        {
            "probe_id": "rank_classid_open",
            "path": str(old_prefix / "classid"),
            "syscall": "openat",
            "flags": "O_RDONLY|O_CLOEXEC",
            "errno": "ENOENT",
            "role": "rank",
            "rank": "each",
            "expected_count_per_rank": 1,
        },
        {
            "probe_id": "rank_ucx_conf_open",
            "path": str(old_prefix / "ucx.conf"),
            "syscall": "openat",
            "flags": "O_RDONLY",
            "errno": "ENOENT",
            "role": "rank",
            "rank": "each",
            "expected_count_per_rank": 1,
        },
        {
            "probe_id": "rank_old_prefix_open",
            "path": str(old_prefix),
            "syscall": "openat",
            "flags": "O_RDONLY",
            "errno": "ENOENT",
            "role": "rank",
            "rank": "each",
            "expected_count_per_rank": 1,
        },
        {
            "probe_id": "rank_old_prefix_directory_open",
            "path": str(old_prefix),
            "syscall": "openat",
            "flags": "O_RDONLY|O_NONBLOCK|O_CLOEXEC|O_DIRECTORY",
            "errno": "ENOENT",
            "role": "rank",
            "rank": "each",
            "expected_count_per_rank": 1,
        },
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def path_from_project(project_root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else project_root / path


def relative_or_absolute(project_root: Path, path: Path) -> str:
    path = path.resolve()
    try:
        return path.relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return str(path)


def exactly_one_running_log(run_directory: Path) -> Path:
    logs = sorted(run_directory.glob("OUT.*/running_scf.log"))
    if len(logs) != 1:
        raise ValueError(
            f"{run_directory}: expected exactly one OUT.*/running_scf.log, found {len(logs)}"
        )
    return logs[0]


def read_tsv(path: Path, header: tuple[str, ...] = MANIFEST_HEADER) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if tuple(reader.fieldnames or ()) != header:
            raise ValueError(f"invalid manifest header: {reader.fieldnames}")
        rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError("manifest has a ragged row")
    return rows


def render_tsv(rows: Iterable[dict[str, object]]) -> str:
    lines = ["\t".join(MANIFEST_HEADER)]
    for row in rows:
        values = []
        for key in MANIFEST_HEADER:
            value = str(row[key])
            if any(character in value for character in "\t\r\n"):
                raise ValueError(f"manifest field contains a control character: {key}")
            values.append(value)
        lines.append("\t".join(values))
    return "\n".join(lines) + "\n"


def read_r8_manifest(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    by_id = {str(row["experiment_id"]): row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError("S1-R8 manifest contains duplicate experiment IDs")
    return by_id


def normalized_run_input(source: bytes) -> bytes:
    pattern = re.compile(br"(?m)^pseudo_dir[ \t]+[^\r\n]*(\r?\n|$)")
    matches = list(pattern.finditer(source))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one pseudo_dir line, found {len(matches)}")
    return pattern.sub(lambda match: b"pseudo_dir ." + match.group(1), source, count=1)


def raw_observables(log_text: str, solver: str, atom_count: int) -> dict[str, object]:
    energy_pattern = ZERO_TEMP_ENERGY_PATTERN if solver == "ksdft" else ENERGY_PATTERN
    energies = energy_pattern.findall(log_text)
    pressures = PRESSURE_PATTERN.findall(log_text)
    if not energies or not pressures:
        raise ValueError("raw log is missing the registered energy or pressure token")
    energy_token = energies[-1]
    pressure_token = pressures[-1]
    energy_total = Decimal(energy_token)
    pressure_kbar = Decimal(pressure_token)
    return {
        "energy_token": energy_token,
        "energy_total_ev": energy_total,
        "energy_ev_per_atom": energy_total / Decimal(atom_count),
        "energy_quantum_mev_per_atom": decimal_quantum(energy_token)
        * Decimal(1000)
        / Decimal(atom_count),
        "pressure_token": pressure_token,
        "pressure_kbar": pressure_kbar,
        "pressure_gpa": pressure_kbar / Decimal(10),
        "pressure_quantum_gpa": decimal_quantum(pressure_token) / Decimal(10),
    }


def decimal_quantum(token: str) -> Decimal:
    value = Decimal(token)
    return Decimal(1).scaleb(value.as_tuple().exponent)


def equivalence_tier(reference: dict[str, object], replay: dict[str, object]) -> dict[str, object]:
    delta_energy = abs(
        Decimal(replay["energy_ev_per_atom"]) - Decimal(reference["energy_ev_per_atom"])
    ) * Decimal(1000)
    delta_pressure = abs(
        Decimal(replay["pressure_gpa"]) - Decimal(reference["pressure_gpa"])
    )
    energy_resolution = max(
        Decimal(reference["energy_quantum_mev_per_atom"]),
        Decimal(replay["energy_quantum_mev_per_atom"]),
    )
    pressure_resolution = max(
        Decimal(reference["pressure_quantum_gpa"]),
        Decimal(replay["pressure_quantum_gpa"]),
    )
    storage_exact = (
        replay["energy_token"] == reference["energy_token"]
        and replay["pressure_token"] == reference["pressure_token"]
    )
    resolution_equal = (
        delta_energy <= energy_resolution and delta_pressure <= pressure_resolution
    )
    scientific_passed = (
        delta_energy < Decimal("0.1") and delta_pressure < Decimal("0.02")
    )
    if storage_exact:
        tier = "storage_exact"
    elif not scientific_passed:
        tier = "not_equivalent"
    elif resolution_equal:
        tier = "storage_resolution_equal"
    else:
        tier = "scientific_tolerance_only"
    return {
        "tier": tier,
        "delta_energy_mev_per_atom": delta_energy,
        "delta_pressure_gpa": delta_pressure,
        "energy_storage_quantum_mev_per_atom": energy_resolution,
        "pressure_storage_quantum_gpa": pressure_resolution,
        "energy_strictly_below_0_1_mev_per_atom": delta_energy < Decimal("0.1"),
        "pressure_strictly_below_0_02_gpa": delta_pressure < Decimal("0.02"),
        "scientific_tolerance_passed": scientific_passed,
    }


def reparse_run(run_directory: Path) -> tuple[dict, Path, dict]:
    metadata_path = run_directory / "input_metadata.json"
    result_path = run_directory / "result.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    result = json.loads(result_path.read_text(encoding="utf-8"))
    log_path = exactly_one_running_log(run_directory)
    text = log_path.read_text(encoding="utf-8", errors="replace")
    reparsed = parse_log(
        text,
        float(metadata["expected_electrons"]),
        int(metadata["atom_count"]),
        str(metadata["solver"]),
    )
    if reparsed != result:
        raise ValueError(f"{run_directory}: result.json differs from raw-log reparse")
    return metadata, log_path, result


def git_clean(project_root: Path) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(project_root), "status", "--porcelain"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError(completed.stderr.strip() or "git status failed")
    return not completed.stdout.strip()


def require_tracked_at_head(project_root: Path, paths: Iterable[Path]) -> list[str]:
    failures: list[str] = []
    relative: list[str] = []
    for path in paths:
        if path.is_symlink():
            failures.append(f"symbolic_link:{path}")
            continue
        try:
            relative.append(path.resolve().relative_to(project_root.resolve()).as_posix())
        except ValueError:
            failures.append(f"outside_project:{path}")
    if not relative:
        return failures
    tracked = subprocess.run(
        ["git", "-C", str(project_root), "ls-files", "--", *relative],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if tracked.returncode != 0:
        return failures + ["git_ls_files_failed"]
    found = {line for line in tracked.stdout.splitlines() if line}
    failures.extend(f"untracked:{path}" for path in relative if path not in found)
    diff = subprocess.run(
        ["git", "-C", str(project_root), "diff", "--quiet", "HEAD", "--", *relative],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if diff.returncode != 0:
        failures.append("tracked_files_differ_from_head")
    return failures


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
    except ValueError:
        return False
    return True


def json_safe(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(data, encoding="utf-8")
    os.replace(temporary, path)
