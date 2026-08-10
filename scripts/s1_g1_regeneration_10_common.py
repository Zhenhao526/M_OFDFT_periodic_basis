#!/usr/bin/env python3
"""Shared fail-closed helpers for the fixed G1 ten-case regeneration audit."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import subprocess
from pathlib import Path

from parse_s1_single import parse_log
from s1_electron_number_common import integrate_cube, parse_charge_grid, scientific_equivalence
from s1_g1_thermodynamic_label_common import (
    compare_density_fields,
    compare_potential_derivative_fields,
    json_safe,
    parse_abacus_cube,
    parse_thermodynamic_log,
)

PROTOCOL_REVISION = "S1-G1-REGENERATION-10-R1"
MANIFEST_FIELDS = (
    "execution_index", "case_id", "source_run_id", "material", "solver", "profile",
    "source_tree_oid", "input_sha256", "stru_sha256", "kpt_sha256",
    "metadata_sha256", "pseudopotential", "pseudopotential_sha256",
    "source_result_sha256", "source_log_relpath", "source_log_sha256",
    "density_relpath", "source_density_sha256", "potential_relpath",
    "source_potential_sha256", "grid", "atom_count", "expected_electrons",
    "timeout_seconds",
)
FROZEN_IMPLEMENTATION_PATHS = (
    "docs/S1_G1_REGENERATION_10_R1_PROTOCOL.md",
    "config/S1_g1_regeneration_10_r1.json",
    "config/S1_g1_regeneration_10_r1_manifest.tsv",
    "scripts/s1_g1_regeneration_10_common.py",
    "scripts/run_s1_g1_regeneration_case_r1.py",
    "scripts/launch_s1_g1_regeneration_10_r1.py",
    "scripts/analyze_s1_g1_regeneration_10_r1.py",
    "scripts/validate_s1_g1_regeneration_10_r1.py",
    "tests/test_s1_g1_regeneration_10_r1.py",
)
ENERGY_LABELS = (
    "F", "m", "U", "E_ec", "E_one_elec", "E_localpp", "T_sU", "F_s",
    "E_Hartree", "E_xc", "E_Ewald", "mu",
)


def sha256(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"not a regular file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_bytes(payload: object) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def write_exclusive(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        data = memoryview(canonical_bytes(payload))
        while data:
            written = os.write(descriptor, data)
            if written <= 0:
                raise OSError("short exclusive evidence write")
            data = data[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.tmp-{os.getpid()}"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short atomic evidence write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)


def read_config(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("protocol_revision") != PROTOCOL_REVISION:
        raise ValueError("configuration protocol differs")
    return value


def read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if tuple(reader.fieldnames or ()) != MANIFEST_FIELDS:
            raise ValueError("unexpected regeneration manifest header")
        rows = list(reader)
    expected = [f"S1-G1-REGEN10-20260810-{index:03d}" for index in range(1, 11)]
    if len(rows) != 10 or [row["case_id"] for row in rows] != expected:
        raise ValueError("formal ten-case denominator differs")
    if [int(row["execution_index"]) for row in rows] != list(range(1, 11)):
        raise ValueError("execution indices differ")
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError("malformed regeneration manifest row")
    return rows


def git(project_root: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(project_root), *arguments], text=True
    ).strip()


def source_paths(project_root: Path, row: dict[str, str]) -> dict[str, Path]:
    source = project_root / "runs" / row["source_run_id"]
    paths = {
        "INPUT": source / "INPUT",
        "STRU": source / "STRU",
        "KPT": source / "KPT",
        "input_metadata.json": source / "input_metadata.json",
        row["pseudopotential"]: source / row["pseudopotential"],
        "result.json": source / "result.json",
        "running_scf.log": source / row["source_log_relpath"],
    }
    if row["profile"] == "ks_r4_field":
        paths["chg.cube"] = source / row["density_relpath"]
        paths["pot.cube"] = source / row["potential_relpath"]
    return paths


def source_hash_contract(row: dict[str, str]) -> dict[str, str]:
    hashes = {
        "INPUT": row["input_sha256"],
        "STRU": row["stru_sha256"],
        "KPT": row["kpt_sha256"],
        "input_metadata.json": row["metadata_sha256"],
        row["pseudopotential"]: row["pseudopotential_sha256"],
        "result.json": row["source_result_sha256"],
        "running_scf.log": row["source_log_sha256"],
    }
    if row["profile"] == "ks_r4_field":
        hashes["chg.cube"] = row["source_density_sha256"]
        hashes["pot.cube"] = row["source_potential_sha256"]
    return hashes


def verify_source(project_root: Path, row: dict[str, str]) -> dict[str, object]:
    tree = git(project_root, "rev-parse", f"HEAD:runs/{row['source_run_id']}")
    if tree != row["source_tree_oid"]:
        raise ValueError(f"source tree OID differs: {row['source_run_id']}")
    observed = {name: sha256(path) for name, path in source_paths(project_root, row).items()}
    if observed != source_hash_contract(row):
        raise ValueError(f"source bytes differ: {row['source_run_id']}")
    return {"source_run_id": row["source_run_id"], "tree_oid": tree, "file_sha256": observed}


def validate_registration(
    project_root: Path,
    config_path: Path,
    manifest_path: Path,
    *,
    require_clean: bool,
    require_state_absent: bool,
) -> tuple[dict, list[dict[str, str]], dict[str, object]]:
    config = read_config(config_path)
    rows = read_manifest(manifest_path)
    if Path(config["project_root"]) != project_root or Path(config["manifest"]) != manifest_path:
        raise ValueError("frozen project or manifest path differs")
    if config.get("formal_case_count") != 10:
        raise ValueError("formal case count differs")
    if config.get("case_ids") != [row["case_id"] for row in rows]:
        raise ValueError("config and manifest denominator differ")
    if config.get("rank_count") != 4 or config.get("cpu_list") != [20, 21, 22, 23]:
        raise ValueError("rank or CPU registration differs")
    if config.get("retry_policy") != "single_attempt_per_id_o_excl_no_retry":
        raise ValueError("retry policy differs")
    if require_clean and git(project_root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("formal regeneration requires a clean worktree")
    runtime = {}
    for name, registration in config["runtime"]["tools"].items():
        path = Path(registration["path"])
        realpath = Path(registration["realpath"])
        if path.resolve() != realpath:
            raise ValueError(f"runtime realpath differs: {name}")
        digest = sha256(realpath)
        if digest != registration["sha256"]:
            raise ValueError(f"runtime tool differs: {name}")
        runtime[name] = {"path": str(path), "realpath": str(realpath), "sha256": digest}
    if require_state_absent and Path(config["external_state_root"]).exists():
        raise ValueError("fresh external state root already exists")
    sources = [verify_source(project_root, row) for row in rows]
    return config, rows, {
        "git_head": git(project_root, "rev-parse", "HEAD"),
        "runtime": runtime,
        "sources": sources,
    }


def _grid(row: dict[str, str]) -> tuple[int, int, int]:
    values = tuple(int(value) for value in row["grid"].split("x"))
    if len(values) != 3 or any(value <= 0 for value in values):
        raise ValueError("invalid registered grid")
    return values


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"non-object JSON: {path}")
    return value


def scientific_case_analysis(
    project_root: Path, state_root: Path, row: dict[str, str]
) -> dict[str, object]:
    source_record = verify_source(project_root, row)
    source = project_root / "runs" / row["source_run_id"]
    config = read_config(project_root / "config/S1_g1_regeneration_10_r1.json")
    replay = state_root / "cases" / row["case_id"]
    runtime = _load(replay / "runtime_metadata.json")
    replay_result = _load(replay / "result.json")
    source_result = _load(source / "result.json")
    expected, atoms = float(row["expected_electrons"]), int(row["atom_count"])
    replay_log_path = replay / row["source_log_relpath"]
    source_log_path = source / row["source_log_relpath"]
    replay_log = replay_log_path.read_text(encoding="utf-8", errors="strict")
    source_log = source_log_path.read_text(encoding="utf-8", errors="strict")
    if replay_result != parse_log(replay_log, expected, atoms, row["solver"]):
        raise ValueError("result.json differs from independent raw-log parse")
    scalar = scientific_equivalence(source_result, replay_result)
    failures = [] if scalar["accepted"] else ["scalar_energy_or_pressure_gate"]
    for name, expected_hash in (
        ("INPUT", row["input_sha256"]),
        ("STRU", row["stru_sha256"]),
        ("KPT", row["kpt_sha256"]),
        ("input_metadata.json", row["metadata_sha256"]),
        (row["pseudopotential"], row["pseudopotential_sha256"]),
    ):
        if sha256(replay / name) != expected_hash:
            failures.append(f"replay_input_hash:{name}")
    tools = config["runtime"]["tools"]
    cpu_text = ",".join(str(value) for value in config["cpu_list"])
    expected_command = [
        tools["taskset"]["path"], "--cpu-list", cpu_text,
        tools["time"]["path"], "-v", "-o", str(replay / "resource_usage.txt"),
        tools["mpirun"]["path"], "--bind-to", "none", "-np", str(config["rank_count"]),
        tools["taskset"]["path"], "--cpu-list", cpu_text, tools["abacus"]["path"],
    ]
    prefix = config["runtime"]["prefix"]
    expected_environment = {
        "HOME": str(replay / "runtime_home"),
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
    runtime_contract_ok = (
        runtime.get("command_argv") == expected_command
        and runtime.get("environment") == expected_environment
        and runtime.get("timeout_seconds") == int(row["timeout_seconds"])
        and runtime.get("timed_out") is False
    )
    if not runtime_contract_ok:
        failures.append("runtime_command_environment_gate")
    requested = set(config["cpu_list"])
    affinity = runtime.get("observed_affinity", {})
    abacus_records = affinity.get("abacus_processes", [])
    all_records = affinity.get("all_descendants", [])
    affinity_ok = (
        runtime.get("hostname") == config["expected_hostname"]
        and runtime.get("rank_count") == config["rank_count"]
        and runtime.get("return_code") == 0
        and len(abacus_records) >= 4
        and bool(all_records)
        and all(
            set(record.get("cpus", []))
            and set(record.get("cpus", [])).issubset(requested)
            for record in all_records
        )
    )
    if not affinity_ok:
        failures.append("runtime_affinity_gate")
    output: dict[str, object] = {
        "case_id": row["case_id"], "source_run_id": row["source_run_id"],
        "material": row["material"], "solver": row["solver"], "profile": row["profile"],
        "source_integrity": source_record, "runtime": runtime,
        "runtime_contract_accepted": runtime_contract_ok,
        "runtime_affinity_accepted": affinity_ok, "scalar_equivalence": scalar,
        "thermodynamic_labels": None, "electron_number": None,
        "density_field": None, "potential_derivative_field": None,
        "raw_field_sha256_equal_diagnostic": None,
    }
    if row["profile"] == "ofdft_scalar":
        if list(replay.rglob("*.cube")):
            failures.append("ofdft_profile_unregistered_cube_output")
    elif row["profile"] == "ks_r4_field":
        source_thermo = parse_thermodynamic_log(source_log, expected_atom_count=atoms)
        replay_thermo = parse_thermodynamic_log(replay_log, expected_atom_count=atoms)
        source_labels = source_thermo["energy_labels_ev_per_cell"]
        replay_labels = replay_thermo["energy_labels_ev_per_cell"]
        if tuple(source_labels) != ENERGY_LABELS or tuple(replay_labels) != ENERGY_LABELS:
            failures.append("thermodynamic_label_set")
        label_rows = []
        for label in ENERGY_LABELS:
            source_value, replay_value = float(source_labels[label]), float(replay_labels[label])
            if not math.isfinite(source_value) or not math.isfinite(replay_value):
                failures.append(f"nonfinite_label:{label}")
                continue
            divisor = 1 if label == "mu" else atoms
            delta = abs(replay_value - source_value) * 1000.0 / divisor
            accepted = delta < 0.1
            if not accepted:
                failures.append(f"thermodynamic_label_delta:{label}")
            label_rows.append({
                "label": label, "source_ev_per_cell": source_value,
                "replay_ev_per_cell": replay_value, "absolute_delta": delta,
                "delta_units": "meV" if label == "mu" else "meV/atom",
                "limit": 0.1, "accepted": accepted,
            })
        grid = _grid(row)
        if parse_charge_grid(replay_log_path) != grid:
            failures.append("replay_grid")
        replay_density_path = replay / row["density_relpath"]
        replay_potential_path = replay / row["potential_relpath"]
        electron = integrate_cube(replay_density_path, replay / "STRU", expected, grid)
        if not electron["accepted"]:
            failures.append("electron_number_gate")
        source_density = parse_abacus_cube(
            source / row["density_relpath"], quantity="density",
            units="electron/bohr^3", structure_path=source / "STRU", expected_grid=grid,
        )
        replay_density = parse_abacus_cube(
            replay_density_path, quantity="density", units="electron/bohr^3",
            structure_path=replay / "STRU", expected_grid=grid,
        )
        density = compare_density_fields(source_density, replay_density)
        if not density["accepted"]:
            failures.append("density_field_gate")
        source_potential = parse_abacus_cube(
            source / row["potential_relpath"], quantity="potential", units="Ry",
            structure_path=source / "STRU", expected_grid=grid,
        )
        replay_potential = parse_abacus_cube(
            replay_potential_path, quantity="potential", units="Ry",
            structure_path=replay / "STRU", expected_grid=grid,
        )
        potential = compare_potential_derivative_fields(source_potential, replay_potential)
        if not potential["accepted"]:
            failures.append("potential_derivative_gate")
        output.update({
            "thermodynamic_labels": {
                "source": source_thermo, "replay": replay_thermo,
                "label_metrics": label_rows,
                "all_labels_accepted": all(item["accepted"] for item in label_rows),
                "zero_temperature_exact_claim": False,
            },
            "electron_number": electron, "density_field": density,
            "potential_derivative_field": potential,
            "raw_field_sha256_equal_diagnostic": {
                "density": source_density.sha256 == replay_density.sha256,
                "potential": source_potential.sha256 == replay_potential.sha256,
            },
        })
    else:
        failures.append("unknown_profile")
    output["failure_reasons"], output["accepted"] = failures, not failures
    return json_safe(output)


def implementation_matches_runner_commit(project_root: Path, runner_commit: str) -> list[str]:
    if len(runner_commit) != 40:
        return ["runner commit is not a full SHA"]
    if subprocess.run(
        ["git", "-C", str(project_root), "merge-base", "--is-ancestor", runner_commit, "HEAD"]
    ).returncode:
        return ["runner commit is not an ancestor of HEAD"]
    failures = []
    for relative in FROZEN_IMPLEMENTATION_PATHS:
        process = subprocess.run(
            ["git", "-C", str(project_root), "show", f"{runner_commit}:{relative}"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if process.returncode:
            failures.append(f"missing from runner commit: {relative}")
        elif process.stdout != (project_root / relative).read_bytes():
            failures.append(f"changed after formal run: {relative}")
    return failures
