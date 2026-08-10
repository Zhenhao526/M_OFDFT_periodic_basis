#!/usr/bin/env python3
"""Independently recover scientific P0 from immutable R1 raw evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import s1_g1_three_layer_common as r1_common
from analyze_s1_g1_three_layer_r1 import p0_metrics
from parse_s1_g1_three_layer_r1 import parse_run as parse_r1_run
from parse_s1_g1_three_layer_continuation_r2 import (
    ELECTRONS,
    NBANDS,
    PRESSURE,
    FORBIDDEN_WARNING,
    parse_eig_occ,
    parse_stress,
    validate_cube_atoms,
)
from s1_g1_thermodynamic_label_common import parse_abacus_cube
from s1_g1_three_layer_continuation_r2_common import (
    CONFIG_PATH,
    MANIFEST_PATH,
    atomic_write,
    canonical_json_bytes,
    file_identity,
    find_project_root,
    git,
    load_config,
    read_json,
    read_text,
    require,
    require_clean_tree,
    require_tracked_matches_head,
    sha256_bytes,
    sha256_file,
    validate_pseudo,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def source_snapshot(state_root: Path) -> dict:
    require(state_root.is_dir() and not state_root.is_symlink(), "R1 source state root missing")
    all_paths = sorted(state_root.rglob("*"), key=lambda path: str(path))
    require(not any(path.is_symlink() for path in all_paths), "R1 source state contains symlink")
    files = [path for path in all_paths if path.is_file()]
    lines = b"".join(f"{sha256_file(path)}  {path}\n".encode("utf-8") for path in files)
    return {
        "file_count": len(files),
        "regular_file_bytes": sum(path.stat().st_size for path in files),
        "absolute_path_sha256sum_list_digest": sha256_bytes(lines),
    }


def git_file_at_commit(project_root: Path, commit: str, relative: str, expected_sha: str) -> dict:
    completed = subprocess.run(
        ["git", "show", f"{commit}:{relative}"],
        cwd=project_root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    require(sha256_bytes(completed.stdout) == expected_sha, f"source Git byte identity differs: {relative}")
    require(sha256_file(project_root / relative) == expected_sha, f"source worktree byte identity differs: {relative}")
    return {
        "path": relative,
        "commit": commit,
        "git_blob_oid": git(project_root, "rev-parse", f"{commit}:{relative}"),
        "sha256": expected_sha,
        "size_bytes": len(completed.stdout),
    }


def require_exact_children(directory: Path, expected: set[str]) -> None:
    actual = {path.name for path in directory.iterdir()} if directory.is_dir() else set()
    require(actual == expected, f"state denominator differs at {directory}: {sorted(actual ^ expected)}")


def output_dir(run_dir: Path, metadata: dict) -> Path:
    expected = run_dir / f"OUT.{metadata['suffix']}"
    require(expected.is_dir() and not expected.is_symlink(), "registered OUT directory missing")
    require([path for path in run_dir.glob("OUT.*") if path.is_dir()] == [expected], "OUT directory denominator differs")
    return expected


def enhanced_raw_checks(run_dir: Path, material: str, config: dict, reparsed: dict) -> dict:
    metadata = read_json(run_dir / "metadata.json")
    require(isinstance(metadata, dict), "metadata must be object")
    out = output_dir(run_dir, metadata)
    log_path = out / "running_scf.log"
    text = read_text(log_path)
    nbands_rows = NBANDS.findall(text)
    require(nbands_rows == [str(config["pseudodojo"]["materials"][material]["expected_nbands"])], "NBANDS differs")
    nbands = int(nbands_rows[0])
    pseudo = validate_pseudo(run_dir / metadata["pseudo"]["basename"], material, config)
    atom_count = int(metadata["atom_count"])
    expected_ne = float(metadata["expected_electrons"])
    require(abs(float(pseudo["z_valence"]) * atom_count - expected_ne) < 1e-12, "UPF zval*nat gate failed")
    reported_rows = ELECTRONS.findall(text)
    require(reported_rows and abs(float(reported_rows[-1]) - expected_ne) < 1e-12, "raw log Ne gate failed")
    cube_path = out / "chg.cube"
    cube = parse_abacus_cube(cube_path, quantity="electron_density", units="electron_per_bohr3", structure_path=run_dir / "STRU")
    integrated = cube.voxel_volume_bohr3 * math.fsum(cube.values)
    cube_relative_error = abs(integrated - expected_ne) / expected_ne
    require(cube_relative_error < float(config["acceptance"]["electron_relative_error_strictly_less_than"]), "raw cube Ne gate failed")
    cube_geometry = validate_cube_atoms(cube, run_dir / "STRU", material, pseudo, config)
    occupation = parse_eig_occ(out / "eig_occ.txt", nbands, expected_ne, config)
    pressure_rows = PRESSURE.findall(text)
    require(pressure_rows, "raw pressure missing")
    stress = parse_stress(text, Decimal(pressure_rows[-1]), config)
    warning_path = out / "warning.log"
    warning_text = read_text(warning_path)
    require(re.findall(r"AUTO_SET NBANDS to\s+([0-9]+)", warning_text) == [str(nbands)], "warning NBANDS differs")
    require(FORBIDDEN_WARNING.search(warning_text) is None, "warning contains forbidden marker")
    require(reparsed["affinity"]["accepted"] is True, "raw affinity gate failed")
    require(reparsed["runtime_nonlocal_projectors_total"] == int(pseudo["expanded_nonlocal_projectors_per_atom"]) * atom_count, "projector gate failed")
    evidence = [
        run_dir / name
        for name in (
            "INPUT", "STRU", "KPT", "input_metadata.json", "metadata.json", "pseudo_identity.json",
            metadata["pseudo"]["basename"], "run.stdout", "run.stderr", "runner_return.json", "result.json",
        )
    ]
    evidence.extend(run_dir / "affinity" / f"rank_{rank:03d}.json" for rank in range(int(config["runtime"]["rank_count"])))
    evidence.extend((log_path, cube_path, out / "eig_occ.txt", warning_path))
    return {
        "upf_zval_times_atom_count": float(pseudo["z_valence"]) * atom_count,
        "expected_electrons": expected_ne,
        "reported_electrons": float(reported_rows[-1]),
        "integrated_cube_electrons": integrated,
        "cube_relative_error": cube_relative_error,
        "eig_occupations": occupation,
        "cube_geometry": cube_geometry,
        "stress_trace_gate": stress,
        "nbands": nbands,
        "warning_log": {"sha256": sha256_file(warning_path), "forbidden_marker_present": False, "accepted": True},
        "pseudo_identity": pseudo,
        "affinity": reparsed["affinity"],
        "evidence_files": [file_identity(path, relative_to=run_dir) for path in evidence],
        "accepted": True,
    }


def verify_source_run(project_root: Path, state_root: Path, experiment_id: str, row: dict[str, str], config: dict, r1_config: dict) -> tuple[dict, dict]:
    run_dir = state_root / "runs" / experiment_id
    attempt_path = state_root / "attempts" / f"{experiment_id}.json"
    accepted_path = state_root / "accepted" / f"{experiment_id}.json"
    attempt = read_json(attempt_path)
    accepted = read_json(accepted_path)
    stored_result = read_json(run_dir / "result.json")
    runner_return = read_json(run_dir / "runner_return.json")
    metadata = read_json(run_dir / "metadata.json")
    input_metadata = read_json(run_dir / "input_metadata.json")
    require(all(isinstance(item, dict) for item in (attempt, accepted, stored_result, runner_return, metadata, input_metadata)), "source payload type differs")
    require(attempt.get("status") == "formal_attempt_started" and attempt.get("experiment_id") == experiment_id, "attempt identity differs")
    require(attempt.get("retry_policy") == "same_id_forbidden_new_revision_and_new_ids_only", "attempt retry policy differs")
    require(accepted.get("status") == "accepted" and accepted.get("experiment_id") == experiment_id, "accepted identity differs")
    require(runner_return.get("return_code") == 0 and runner_return.get("experiment_id") == experiment_id, "runner return differs")
    require(stored_result.get("status") == "accepted" and stored_result.get("experiment_id") == experiment_id, "stored result differs")
    require(accepted.get("result_sha256") == sha256_file(run_dir / "result.json"), "accepted/result SHA binding differs")
    require(metadata.get("runner_commit") == config["source_r1"]["runner_commit"], "source runner commit differs")
    for field in ("experiment_id", "phase", "requirement", "material", "role", "suffix"):
        require(str(metadata.get(field)) == str(row[field]), f"source manifest metadata differs: {experiment_id}/{field}")
    require(abs(float(metadata["volume_ratio"]) - float(row["volume_ratio"])) < 1e-12, "source volume differs")
    require(int(metadata["ecutwfc_ry"]) == int(row["ecutwfc_ry"]) and int(metadata["ecutrho_ry"]) == int(row["ecutrho_ry"]), "source cutoff differs")
    require(metadata["kmesh"] == list(r1_common.parse_kmesh(row["kmesh"])), "source kmesh differs")
    require(abs(float(metadata["expected_electrons"]) - float(row["expected_electrons"])) < 1e-12, "source Ne differs")
    committed_input = project_root / r1_config["input_root"] / experiment_id
    for name in ("INPUT", "STRU", "KPT"):
        require(sha256_file(run_dir / name) == sha256_file(committed_input / name), f"source committed input differs: {experiment_id}/{name}")
    require(canonical_json_bytes(input_metadata) == (committed_input / "metadata.json").read_bytes(), "source committed metadata differs")
    reparsed = parse_r1_run(run_dir, r1_config)
    require(canonical_json_bytes(reparsed) == (run_dir / "result.json").read_bytes(), "independent R1 parser replay differs")
    enhanced = enhanced_raw_checks(run_dir, row["material"], config, reparsed)
    return stored_result, {
        "experiment_id": experiment_id,
        "attempt_sha256": sha256_file(attempt_path),
        "accepted_sha256": sha256_file(accepted_path),
        "accepted_result_sha256": accepted["result_sha256"],
        "result_sha256": sha256_file(run_dir / "result.json"),
        "runner_return_sha256": sha256_file(run_dir / "runner_return.json"),
        "runner_return_code": 0,
        "r1_parser_byte_exact_replay": True,
        "enhanced_raw_gates": enhanced,
        "status": "accepted_source_evidence",
    }


def build_recovery(project_root: Path, config: dict) -> dict:
    source = config["source_r1"]
    state_root = Path(source["state_root"])
    snapshot = source_snapshot(state_root)
    require(snapshot["file_count"] == int(source["expected_file_count"]), "R1 state file count differs")
    require(snapshot["regular_file_bytes"] == int(source["expected_regular_file_bytes"]), "R1 state byte count differs")
    require(snapshot["absolute_path_sha256sum_list_digest"] == source["absolute_path_sha256sum_list_digest"], "R1 state tree digest differs")
    ids = list(source["accepted_ids"])
    require_exact_children(state_root / "attempts", {f"{value}.json" for value in ids})
    require_exact_children(state_root / "accepted", {f"{value}.json" for value in ids})
    require_exact_children(state_root / "runs", set(ids))
    for absent in (state_root / "phases", state_root / "barriers"):
        require(not absent.exists(), f"R1 forbidden orchestration directory appeared: {absent}")
    for absent in (state_root / "terminal.json", state_root / "completion.json", state_root / "supervisor_completion.json"):
        require(not absent.exists(), f"R1 forbidden terminal artifact appeared: {absent}")
    session = read_json(state_root / "session.json")
    require(isinstance(session, dict) and session.get("status") == "active", "R1 session disposition differs")
    require(session.get("runner_commit") == source["runner_commit"], "R1 session runner commit differs")
    require(session.get("config_sha256") == source["config_sha256"] and session.get("manifest_sha256") == source["manifest_sha256"], "R1 session registration differs")
    require(sha256_file(state_root / "session.json") == source["session_sha256"], "R1 session SHA differs")
    git_sources = [
        git_file_at_commit(project_root, source["runner_commit"], source["config_path"], source["config_sha256"]),
        git_file_at_commit(project_root, source["runner_commit"], source["manifest_path"], source["manifest_sha256"]),
    ]
    r1_config = r1_common.load_config(project_root)
    r1_rows = r1_common.load_manifest(project_root)
    row_by_id = {row["experiment_id"]: row for row in r1_rows}
    results: dict[str, dict] = {}
    evidence: list[dict] = []
    for experiment_id in ids:
        result, recovered = verify_source_run(project_root, state_root, experiment_id, row_by_id[experiment_id], config, r1_config)
        results[experiment_id] = result
        evidence.append(recovered)
    metrics = p0_metrics(results, r1_config)
    require(metrics["status"] == "accepted", "independently replayed P0 metrics rejected")
    return {
        "schema_version": 2,
        "protocol_revision": config["protocol_revision"],
        "status": "accepted",
        "source_operational_status": source["operational_disposition"],
        "source_operational_phase_accepted": False,
        "scientific_p0_recovery_status": "accepted",
        "source_state_root": str(state_root),
        "source_session_sha256": sha256_file(state_root / "session.json"),
        "source_runner_commit": source["runner_commit"],
        "source_snapshot": snapshot,
        "source_git_identities": git_sources,
        "accepted_source_ids": ids,
        "accepted_source_count": len(ids),
        "new_run_count": 0,
        "permanently_unexecuted_source_ids": list(source["permanently_unexecuted_ids"]),
        "p0_metrics": metrics,
        "per_run_recovery": evidence,
        "scope": {"al_hard": True, "mg_diagnostic_only": True, "r1_phase_marker_reconstructed": False},
    }


def verify_existing(project_root: Path, config: dict, recomputed: dict) -> dict:
    external = Path(config["external_state_root"]) / "barriers" / "r1_p0_recovery.json"
    versioned = project_root / config["versioned_recovery_barrier"]
    require(external.is_file() and not external.is_symlink(), "external recovery barrier missing")
    require(versioned.is_file() and not versioned.is_symlink(), "versioned recovery barrier missing")
    require(external.read_bytes() == versioned.read_bytes(), "recovery barrier copies differ")
    payload = read_json(external)
    require(isinstance(payload, dict) and payload.get("status") == "accepted", "recovery barrier not accepted")
    observed = {key: value for key, value in payload.items() if key not in {"created_utc", "continuation_prereg_commit", "config_sha256", "manifest_sha256"}}
    require(observed == recomputed, "recovery barrier replay differs")
    return {"status": "accepted", "barrier_sha256": sha256_file(external), "continuation_prereg_commit": payload["continuation_prereg_commit"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--formalize", action="store_true")
    parser.add_argument("--verify-existing", action="store_true")
    args = parser.parse_args()
    require(not (args.formalize and args.verify_existing), "recovery modes are mutually exclusive")
    project_root = find_project_root(args.project_root)
    config = load_config(project_root)
    head = require_clean_tree(project_root)
    registered = [CONFIG_PATH, MANIFEST_PATH, Path("docs/S1_G1_THREE_LAYER_CONTINUATION_R2_PROTOCOL.md"), Path("scripts/recover_s1_g1_three_layer_continuation_r2.py")]
    require_tracked_matches_head(project_root, registered)
    recomputed = build_recovery(project_root, config)
    if args.verify_existing:
        payload = verify_existing(project_root, config, recomputed)
    elif args.formalize:
        state_root = Path(config["external_state_root"])
        external = state_root / "barriers" / "r1_p0_recovery.json"
        versioned = project_root / config["versioned_recovery_barrier"]
        require(not state_root.exists(), "fresh continuation state already exists")
        require(not versioned.exists(), "versioned recovery barrier already exists")
        payload = {
            **recomputed,
            "continuation_prereg_commit": head,
            "config_sha256": sha256_file(project_root / CONFIG_PATH),
            "manifest_sha256": sha256_file(project_root / MANIFEST_PATH),
            "created_utc": utc_now(),
        }
        data = canonical_json_bytes(payload)
        atomic_write(external, data, exclusive=True)
        atomic_write(versioned, data, exclusive=True)
        require(external.read_bytes() == versioned.read_bytes(), "recovery barrier copy verification failed")
        payload = {"status": "accepted", "barrier_sha256": sha256_bytes(data), "continuation_prereg_commit": head, "versioned_path": config["versioned_recovery_barrier"]}
    else:
        payload = {"status": "accepted_verify_only", "source_snapshot": recomputed["source_snapshot"], "p0_metrics": recomputed["p0_metrics"], "new_state_written": False}
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
