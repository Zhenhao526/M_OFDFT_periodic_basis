#!/usr/bin/env python3
"""Analysis-only R5 replay of the accepted R4 Al-domain follow-up."""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from parse_s1_g1_three_layer_al_followup_r4 import parse_run as reparse_new_run
from replay_s1_g1_three_layer_r1_p0_followup_r3 import replay_r1_p0
from run_s1_g1_three_layer_al_followup_r4 import (
    committed_json,
    git_file_at_commit,
    verify_accepted_source,
    verify_continuation_phase_preflight,
    verify_continuation_source_orchestration,
    verify_pseudo_identity_closure,
    verify_r2_operational_failure_closure,
    verify_r3_operational_failure_closure,
)
from replay_s1_g1_three_layer_al_followup_r4_parser_regression import validate_fixture
from s1_g1_three_layer_al_followup_r4_common import (
    atomic_write,
    canonical_json_bytes,
    find_project_root,
    geometry_payload_bytes,
    load_config,
    load_manifest,
    read_json,
    require,
    sha256_bytes,
    sha256_file,
    verify_strain_geometry,
    validate_pseudo,
)


STRAIN_MAP = {
    "S1-20260810-351": "S1-20260810-204",
    "S1-20260810-352": "S1-20260810-205",
    "S1-20260810-353": "S1-20260810-206",
    "S1-20260810-354": "S1-20260810-207",
}
ANCHOR_IDS = ("S1-20260810-301", "S1-20260810-302", "S1-20260810-303")
RECOVERY_IDS = tuple(f"S1-20260810-{number:03d}" for number in range(301, 307))
CONTINUATION_IDS = ("S1-20260810-327", "S1-20260810-328")
CONTINUATION_PHASE_IDS = tuple(f"S1-20260810-{number:03d}" for number in range(327, 333))
GATE_FIELDS = ("gate", "point", "metric", "value", "limit", "inequality", "accepted")
ANALYSIS_PROTOCOL_REVISION = "S1-G1-THREE-LAYER-AL-DOMAIN-FOLLOWUP-ANALYSIS-20260810-R5"
R5_CONFIG_PATH = Path("config/S1_g1_three_layer_al_domain_followup_analysis_r5.json")
R4_CLOSURE_PATH = Path("orchestration/s1/g1_three_layer_al_domain_followup_r4_20260810/analyzer_false_negative_closure.json")
ANALYSIS_ROOT = Path("analysis/s1/g1_three_layer_al_domain_followup_analysis_r5_20260810")
EXPECTED_EXTENSION_KEYS = {
    "r2_operational_failure_closure",
    "r3_operational_failure_closure",
    "r3_parser_regression",
}
EXPECTED_IMPLEMENTATION_PATHS = (
    "docs/S1_G1_THREE_LAYER_AL_DOMAIN_FOLLOWUP_ANALYSIS_R5_PROTOCOL.md",
    "orchestration/s1/g1_three_layer_al_domain_followup_r4_20260810/analysis_r5_superseded_implementations.json",
    "scripts/analyze_s1_g1_three_layer_al_followup_analysis_r5.py",
    "scripts/validate_s1_g1_three_layer_al_followup_analysis_r5.py",
    "tests/test_s1_g1_three_layer_al_followup_analysis_r5.py",
)
R4_INPUT_PATHS = {
    "config": "config/S1_g1_three_layer_al_domain_followup_r4.json",
    "manifest": "config/S1_g1_three_layer_al_domain_followup_r4_manifest.tsv",
    "runner": "scripts/run_s1_g1_three_layer_al_followup_r4.py",
}


def _read_object(path: Path) -> dict:
    require(path.is_file() and not path.is_symlink(), f"missing JSON evidence: {path}")
    payload = read_json(path)
    require(isinstance(payload, dict), f"JSON root must be object: {path}")
    return payload


def _git(project_root: Path, *args: str, text: bool = True) -> str | bytes:
    completed = subprocess.run(
        ["git", *args], cwd=project_root, check=True,
        text=text, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return completed.stdout


def _load_r5_config(project_root: Path) -> dict:
    payload = _read_object(project_root / R5_CONFIG_PATH)
    require(payload.get("analysis_protocol_revision") == ANALYSIS_PROTOCOL_REVISION, "R5 analysis protocol differs")
    require(payload.get("status") == "preregistered_analysis_only", "R5 preregistration status differs")
    require(payload.get("analysis_root") == ANALYSIS_ROOT.as_posix(), "R5 analysis root differs")
    require(payload.get("new_solver_run_count") == 0 and payload.get("new_solver_ids") == [], "R5 must not register solver work")
    return payload


def _introduction_commit(project_root: Path, relative: Path) -> str:
    rows = [
        row for row in str(_git(project_root, "log", "--diff-filter=A", "--format=%H", "--", relative.as_posix())).splitlines()
        if row
    ]
    require(len(rows) == 1 and len(rows[0]) == 40, f"R5 preregistration introduction differs: {relative}")
    return rows[0]


def _parent_row(project_root: Path, commit: str) -> list[str]:
    return str(_git(project_root, "rev-list", "--parents", "-n", "1", commit)).strip().split()


def require_exact_preregistration_head(head: str, preregistration: str) -> None:
    require(head == preregistration, "R5 analysis may execute only at exact preregistration commit")


def require_exact_implementation_diff(rows: list[list[str]], registered_paths: list[str]) -> None:
    require(
        registered_paths == list(EXPECTED_IMPLEMENTATION_PATHS),
        "R5 registered implementation path set/order differs",
    )
    require(
        rows == [["A", path] for path in EXPECTED_IMPLEMENTATION_PATHS],
        "R5 closure-to-implementation diff must be exactly five A-only registered paths",
    )


def verify_preregistration_topology(
    project_root: Path,
    r5_config: dict,
    *,
    require_head_is_preregistration: bool,
) -> dict:
    require(str(_git(project_root, "status", "--porcelain=v1")) == "", "R5 worktree/index must be clean")
    head = str(_git(project_root, "rev-parse", "HEAD")).strip()
    preregistration = _introduction_commit(project_root, R5_CONFIG_PATH)
    implementation = r5_config["implementation_commit"]
    closure_commit = r5_config["r4_analyzer_false_negative_closure"]["commit"]
    require(_parent_row(project_root, preregistration) == [preregistration, implementation], "R5 preregistration must have implementation as unique parent")
    superseded = r5_config.get("superseded_before_execution_commits")
    require(
        superseded == [
            "98752ed7965e499e2c95727cf611b2c27156c163",
            "7adc43019307bf863b778fe0594ff529913e574b",
            "68b0c895cb4cf8de170381fd802b85185897b325",
            "bacb2564244c5a2860bdd6698766827e406aa4ec",
        ],
        "R5 superseded-before-execution identities differ",
    )
    require(_parent_row(project_root, superseded[0]) == [superseded[0], closure_commit], "R5 first superseded implementation topology differs")
    require(_parent_row(project_root, superseded[1]) == [superseded[1], superseded[0]], "R5 second superseded implementation topology differs")
    require(_parent_row(project_root, superseded[2]) == [superseded[2], closure_commit], "R5 third superseded implementation topology differs")
    require(_parent_row(project_root, superseded[3]) == [superseded[3], superseded[2]], "R5 superseded preregistration topology differs")
    require(_parent_row(project_root, implementation) == [implementation, closure_commit], "R5 final implementation must have closure as unique parent")
    registered_paths = [identity["path"] for identity in r5_config["registered_implementation_files"]]
    implementation_rows = [
        row.split("\t", 1)
        for row in str(_git(project_root, "diff", "--name-status", closure_commit, implementation)).splitlines()
        if row
    ]
    require_exact_implementation_diff(implementation_rows, registered_paths)
    for candidate in superseded:
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", candidate, preregistration],
            cwd=project_root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        require(ancestor.returncode == 1, f"superseded R5 commit is an ancestor of final preregistration: {candidate}")
    changed = [
        row for row in str(_git(project_root, "diff", "--name-only", implementation, preregistration)).splitlines()
        if row
    ]
    require(changed == [R5_CONFIG_PATH.as_posix()], "R5 preregistration diff must contain only its config")
    committed_config = _git(project_root, "show", f"{preregistration}:{R5_CONFIG_PATH.as_posix()}", text=False)
    require(committed_config == (project_root / R5_CONFIG_PATH).read_bytes(), "R5 config differs from preregistration bytes")
    for identity in r5_config["registered_implementation_files"]:
        relative = identity["path"]
        path = project_root / relative
        require(path.is_file() and not path.is_symlink(), f"R5 implementation file missing: {relative}")
        committed = _git(project_root, "show", f"{implementation}:{relative}", text=False)
        require(committed == path.read_bytes(), f"R5 implementation file differs from implementation commit: {relative}")
        require(sha256_bytes(committed) == identity["sha256"], f"R5 implementation SHA differs: {relative}")
        require(len(committed) == identity["size_bytes"], f"R5 implementation size differs: {relative}")
        blob = str(_git(project_root, "rev-parse", f"{implementation}:{relative}")).strip()
        require(blob == identity["git_blob"], f"R5 implementation blob differs: {relative}")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", preregistration, head],
        cwd=project_root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    require(ancestor.returncode == 0, "R5 preregistration is not an ancestor of HEAD")
    if require_head_is_preregistration:
        require_exact_preregistration_head(head, preregistration)
    return {
        "accepted": True,
        "head": head,
        "implementation_commit": implementation,
        "superseded_before_execution_commits": superseded,
        "preregistration_commit": preregistration,
        "registered_implementation_file_count": len(r5_config["registered_implementation_files"]),
        "preregistration_only_path": R5_CONFIG_PATH.as_posix(),
    }


def _state_inventory(root: Path) -> tuple[list[dict], int, str]:
    rows: list[dict] = []
    total = 0
    for path in sorted(value for value in root.rglob("*") if value.is_file()):
        require(not path.is_symlink(), f"R4 state contains symlink: {path}")
        size = path.stat().st_size
        total += size
        rows.append({
            "path": path.relative_to(root).as_posix(),
            "sha256": sha256_file(path),
            "size_bytes": size,
        })
    digest = sha256_bytes(b"".join((json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode() for row in rows))
    return rows, total, digest


def verify_r4_false_negative_closure(project_root: Path, r5_config: dict) -> dict:
    spec = r5_config["r4_analyzer_false_negative_closure"]
    path = project_root / spec["path"]
    closure = _read_object(path)
    require(path.relative_to(project_root) == R4_CLOSURE_PATH, "R4 closure path differs")
    require(sha256_file(path) == spec["sha256"] and path.stat().st_size == spec["size_bytes"], "R4 closure file identity differs")
    require(str(_git(project_root, "rev-parse", f"{spec['commit']}:{spec['path']}")).strip() == spec["git_blob"], "R4 closure blob differs")
    require(_git(project_root, "show", f"{spec['commit']}:{spec['path']}", text=False) == path.read_bytes(), "R4 closure bytes differ from closure commit")
    require(closure.get("status") == "evidence_valid_operational_accepted_analyzer_false_negative", "R4 closure status differs")
    require(closure.get("analyzer_exit_code") == 1 and closure.get("analysis_output_created") is False, "R4 analyzer-failure premise differs")
    require(
        closure["false_negatives"][0]["frozen_only_top_level_keys"]
        == ["r2_operational_failure_closure", "r3_operational_failure_closure", "r3_parser_regression"],
        "R4 omitted parent keys differ",
    )
    require(closure["diagnostic_scope"].get("third_false_negative_found") is False, "R4 false-negative denominator differs")

    historical_spec = {
        "path": spec["historical_r4_analyzer_path"],
        "sha256": spec["historical_r4_analyzer_sha256"],
        "git_blob": spec["historical_r4_analyzer_git_blob"],
    }
    require(historical_spec == closure["analyzer_identity"], "historical R4 analyzer identity differs from closure")
    historical_path = project_root / historical_spec["path"]
    require(historical_path.is_file() and not historical_path.is_symlink(), "historical R4 analyzer missing")
    historical_bytes = historical_path.read_bytes()
    require(sha256_bytes(historical_bytes) == historical_spec["sha256"], "historical R4 analyzer SHA differs")
    require(str(_git(project_root, "hash-object", historical_spec["path"])).strip() == historical_spec["git_blob"], "historical R4 analyzer current blob differs")
    committed_historical = _git(project_root, "show", f"{spec['commit']}:{historical_spec['path']}", text=False)
    require(committed_historical == historical_bytes, "historical R4 analyzer bytes differ from closure commit")
    require(str(_git(project_root, "rev-parse", f"{spec['commit']}:{historical_spec['path']}")).strip() == historical_spec["git_blob"], "historical R4 analyzer committed blob differs")

    state_spec = closure["r4_state_identity"]
    terminal_spec = closure["r4_operational_terminal"]
    input_spec = closure["r4_input_identities"]
    expected_registered_inputs = {
        "config": {
            "path": R4_INPUT_PATHS["config"],
            "sha256": input_spec["config_sha256"],
            "git_blob": input_spec["config_git_blob"],
        },
        "manifest": {
            "path": R4_INPUT_PATHS["manifest"],
            "sha256": input_spec["manifest_sha256"],
            "git_blob": input_spec["manifest_git_blob"],
        },
        "runner": {
            "path": R4_INPUT_PATHS["runner"],
            "sha256": input_spec["runner_sha256"],
            "git_blob": input_spec["runner_git_blob"],
        },
    }
    expected_source_r4 = {
        "protocol_revision": closure["protocol_revision"],
        "runner_commit": terminal_spec["runner_commit"],
        "external_state_root": state_spec["external_state_root"],
        "session_sha256": terminal_spec["session_sha256"],
        "terminal_sha256": terminal_spec["terminal_sha256"],
        "state_file_count": state_spec["file_count"],
        "state_total_bytes": state_spec["total_bytes"],
        "state_inventory_sha256": state_spec["inventory_sha256"],
        "formal_ids": terminal_spec["accepted_ids"],
        "attempted_count": terminal_spec["attempted_count"],
        "accepted_count": terminal_spec["accepted_count"],
        "failed_count": terminal_spec["failed_count"],
        "retried_count": terminal_spec["retried_count"],
        "runner_return_code": terminal_spec["runner_return_code"],
        "registered_input_identities": expected_registered_inputs,
    }
    require(r5_config.get("source_r4") == expected_source_r4, "R5 source_r4 frozen identity differs from closure")
    for label, identity in expected_registered_inputs.items():
        input_path = project_root / identity["path"]
        require(input_path.is_file() and not input_path.is_symlink(), f"R4 registered {label} missing")
        current_bytes = input_path.read_bytes()
        require(sha256_bytes(current_bytes) == identity["sha256"], f"R4 registered {label} current SHA differs")
        committed_bytes = _git(project_root, "show", f"{terminal_spec['runner_commit']}:{identity['path']}", text=False)
        require(committed_bytes == current_bytes, f"R4 registered {label} bytes differ from runner commit")
        require(sha256_bytes(committed_bytes) == identity["sha256"], f"R4 registered {label} committed SHA differs")
        require(str(_git(project_root, "rev-parse", f"{terminal_spec['runner_commit']}:{identity['path']}")).strip() == identity["git_blob"], f"R4 registered {label} committed blob differs")

    closure_scientific = closure["scientific_diagnostic"]
    require(spec.get("scientific_diagnostic") == closure_scientific, "R5 closure scientific diagnostic freeze differs")
    expected_metrics = r5_config["expected_scientific_metrics"]
    require(expected_metrics.get("comparison") == "exact_binary64_replay", "R5 scientific comparison mode differs")
    derived_closure_scientific = {
        "build_analysis_row_count": expected_metrics["gate_row_count"],
        "endpoint_v090": expected_metrics["endpoints"]["v090"],
        "endpoint_v110": expected_metrics["endpoints"]["v110"],
        "gate_status": expected_metrics["overall_status"],
        "maximum_strain_absolute_difference_mev_per_atom": max(expected_metrics["strain_absolute_difference_mev_per_atom"].values()),
    }
    require(derived_closure_scientific == closure_scientific, "R5 expected scientific metrics differ from closure")

    state_root = Path(state_spec["external_state_root"])
    rows, total, digest = _state_inventory(state_root)
    require(len(rows) == state_spec["file_count"] and total == state_spec["total_bytes"], "R4 state inventory denominator differs")
    require(digest == state_spec["inventory_sha256"], "R4 state inventory digest differs")
    terminal = _read_object(state_root / "terminal.json")
    session = _read_object(state_root / "session.json")
    require(sha256_file(state_root / "terminal.json") == terminal_spec["terminal_sha256"], "R4 terminal SHA differs")
    require(sha256_file(state_root / "session.json") == terminal_spec["session_sha256"], "R4 session SHA differs")
    require(terminal.get("status") == "accepted", "R4 terminal rejected")
    require(terminal.get("attempted_ids") == terminal.get("accepted_ids") == terminal_spec["accepted_ids"], "R4 terminal ID denominator differs")
    require(terminal.get("attempted_count") == terminal.get("accepted_count") == 8, "R4 terminal denominator differs")
    require(terminal.get("failed_count") == terminal.get("retried_count") == 0 and terminal.get("runner_return_code") == 0, "R4 terminal failure/retry differs")
    require(session.get("runner_commit") == terminal_spec["runner_commit"], "R4 session runner differs")
    require(not (project_root / "analysis/s1/g1_three_layer_al_domain_followup_r4_20260810").exists(), "rejected R4 analysis root must remain absent")
    return {
        "accepted": True,
        "commit": spec["commit"],
        "git_blob": spec["git_blob"],
        "sha256": spec["sha256"],
        "historical_r4_analyzer": historical_spec,
        "registered_input_identities": expected_registered_inputs,
        "scientific_diagnostic": closure_scientific,
        "state_file_count": len(rows),
        "state_total_bytes": total,
        "state_inventory_sha256": digest,
        "session_sha256": terminal_spec["session_sha256"],
        "terminal_sha256": terminal_spec["terminal_sha256"],
    }



def _hardlink_or_copy(source: str, destination: str) -> str:
    source_path = Path(source)
    require(source_path.is_file() and not source_path.is_symlink(), f"unsafe replay source: {source_path}")
    try:
        os.link(source_path, destination)
    except OSError:
        shutil.copyfile(source_path, destination)
    return destination



def validate_minimal_input_pseudo_identity(input_identity: object, material: str, config: dict) -> dict:
    require(material in config["pseudodojo"]["materials"], "pseudo material contract missing")
    expected = config["pseudodojo"]["materials"][material]
    expected_minimal = {
        "basename": expected["basename"],
        "expanded_nonlocal_projectors_per_atom": expected["expanded_nonlocal_projectors_per_atom"],
        "format": "upf201",
        "sha256": expected["sha256"],
        "z_valence": expected["z_valence"],
    }
    require(isinstance(input_identity, dict), "minimal input pseudo identity missing")
    require(set(input_identity) == set(expected_minimal), "minimal input pseudo schema differs")
    require(input_identity == expected_minimal, "minimal input pseudo values differ")
    element_dir = {"al": "Al", "mg": "Mg"}[material]
    canonical_url = (
        f"https://raw.githubusercontent.com/{config['pseudodojo']['repository']}/"
        f"{config['pseudodojo']['commit']}/{element_dir}/{expected['basename']}"
    )
    require(expected["url"] == canonical_url, "pseudo upstream URL/repository/commit binding differs")
    require(isinstance(expected.get("git_blob_sha1"), str) and len(expected["git_blob_sha1"]) == 40, "pseudo Git blob identity missing")
    return {
        "accepted": True,
        "mapping": "legacy_minimal_input_pseudo_schema_to_frozen_config_upstream_identity",
        "minimal_input_identity": expected_minimal,
        "repository": config["pseudodojo"]["repository"],
        "upstream_commit": config["pseudodojo"]["commit"],
        "upstream_url": canonical_url,
        "git_blob_sha1": expected["git_blob_sha1"],
    }


def verify_new_pseudo_identity_closure_r5(run_dir: Path, result: dict, config: dict) -> dict:
    material = result.get("material")
    metadata = _read_object(run_dir / "metadata.json")
    input_metadata = _read_object(run_dir / "input_metadata.json")
    require(metadata.get("pseudo") == input_metadata.get("pseudo"), "executed/input minimal pseudo identity differs")
    mapping = validate_minimal_input_pseudo_identity(metadata.get("pseudo"), material, config)
    expected = config["pseudodojo"]["materials"][material]
    recorded_path = run_dir / "pseudo_identity.json"
    recorded = _read_object(recorded_path)
    runtime_recorded = metadata.get("pseudo_runtime_identity")
    require(recorded == runtime_recorded == result.get("pseudo_identity"), "recorded/result/runtime pseudo identity differs")
    cache_path = Path(config["external_pseudo_cache"]) / expected["basename"]
    cache_identity = validate_pseudo(cache_path, material, config)
    require(recorded == cache_identity, "recorded pseudo identity differs from validated external cache")
    raw_path = run_dir / expected["basename"]
    if raw_path.exists():
        require(raw_path.is_file() and not raw_path.is_symlink(), "raw run pseudo body is unsafe")
        require(validate_pseudo(raw_path, material, config) == cache_identity, "raw run pseudo differs from external cache")
    atom_count = int(result["atom_count"])
    expected_total = int(recorded["expanded_nonlocal_projectors_per_atom"]) * atom_count
    require(result.get("runtime_nonlocal_projectors_total") == expected_total, "log runtime projector count differs from pseudo identity")
    return {
        "material": material,
        "repository": mapping["repository"],
        "upstream_commit": mapping["upstream_commit"],
        "upstream_url": mapping["upstream_url"],
        "git_blob_sha1": mapping["git_blob_sha1"],
        "basename": expected["basename"],
        "sha256": expected["sha256"],
        "header_identity": cache_identity,
        "runtime_nonlocal_projectors_total": expected_total,
        "pseudo_identity_json_sha256": sha256_file(recorded_path),
        "external_cache_path": str(cache_path),
        "external_cache_verified_sha256": sha256_file(cache_path),
        "input_schema_mapping": mapping,
        "committed_body_policy": "deliberately_external_identity_only_due_to_repository_redistribution_policy",
        "accepted": True,
    }


def select_replay_pseudo_identity_closure(
    run_dir: Path,
    result: dict,
    config: dict,
    *,
    explicit_minimal_input_schema: bool,
) -> dict:
    return (
        verify_new_pseudo_identity_closure_r5(run_dir, result, config)
        if explicit_minimal_input_schema
        else verify_pseudo_identity_closure(run_dir, result, config, require_run_body=False)
    )


def reparse_with_external_pseudo(
    run_dir: Path,
    result: dict,
    config: dict,
    *,
    require_followup_orchestration: bool,
    explicit_minimal_input_schema: bool = False,
) -> tuple[dict, dict]:
    """Run the complete parser without committing a redistributability-unclear UPF body."""
    closure = select_replay_pseudo_identity_closure(
        run_dir, result, config,
        explicit_minimal_input_schema=explicit_minimal_input_schema,
    )
    basename = closure["basename"]
    raw_pseudo = run_dir / basename
    if raw_pseudo.is_file():
        require(not raw_pseudo.is_symlink(), "raw pseudo body must not be a symlink")
        reparsed = reparse_new_run(
            run_dir, config, require_followup_orchestration=require_followup_orchestration
        )
    else:
        require(not raw_pseudo.exists(), "unsafe missing-pseudo placeholder")
        for path in run_dir.rglob("*"):
            require(not path.is_symlink(), f"snapshot contains symlink: {path}")
        cache_path = Path(config["external_pseudo_cache"]) / basename
        # R5 fully validated the explicit minimal-schema mapping, cache, and
        # all recorded identities before any hard link or parser invocation.
        with tempfile.TemporaryDirectory(prefix="g1_al_followup_pseudo_replay_") as temporary:
            replay_dir = Path(temporary) / "run"
            shutil.copytree(run_dir, replay_dir, copy_function=_hardlink_or_copy)
            shutil.copyfile(cache_path, replay_dir / basename)
            reparsed = reparse_new_run(
                replay_dir, config, require_followup_orchestration=require_followup_orchestration
            )
    return reparsed, closure


def verify_new_result(state_root: Path, experiment_id: str, config: dict, session: dict) -> tuple[dict, dict]:
    run_dir = state_root / "runs" / experiment_id
    accepted_identity = verify_accepted_source(
        state_root,
        experiment_id,
        session,
        require_complete_orchestration=True,
        expected_config_sha256=session["config_sha256"],
        expected_manifest_sha256=session["manifest_sha256"],
    )
    result = _read_object(run_dir / "result.json")
    reparsed, pseudo_closure = reparse_with_external_pseudo(
        run_dir, result, config, require_followup_orchestration=True,
        explicit_minimal_input_schema=True,
    )
    require(canonical_json_bytes(reparsed) == (run_dir / "result.json").read_bytes(), f"new raw parser replay differs: {experiment_id}")
    require(result.get("status") == "accepted" and result.get("experiment_id") == experiment_id, f"new result rejected: {experiment_id}")
    require(result.get("protocol_revision") == config["protocol_revision"], "new result protocol differs")
    require(result.get("hard_gates") and all(result["hard_gates"].values()), f"per-point hard gate failed: {experiment_id}")
    require(result["runtime_nonlocal_projectors_total"] == 18, "runtime projector count differs")
    require(result["independent_electron_identity"]["accepted"] is True, "electron identity differs")
    require(result["band_occupation"]["accepted"] is True, "band gate differs")
    require(result["cube_geometry"]["accepted"] is True, "cube geometry differs")
    require(result["cube_geometry"]["origin_exactly_zero"] is True, "cube origin gate missing")
    require(result["cube_geometry"]["maximum_origin_absolute_error_bohr"] == 0.0, "cube origin differs")
    require(result["mechanics"]["accepted"] is True, "mechanics gate differs")
    for identity in result["evidence_files"]:
        path = run_dir / identity["path"]
        require(path.is_file() and not path.is_symlink(), f"missing new evidence: {path}")
        require(path.stat().st_size == identity["size_bytes"] and sha256_file(path) == identity["sha256"], f"new evidence identity differs: {path}")
    return result, {
        **accepted_identity,
        "pseudo_identity_closure": pseudo_closure,
        "independent_raw_replay_sha256": sha256_bytes(canonical_json_bytes(reparsed)),
        "all_hard_gates_accepted": all(reparsed["hard_gates"].values()),
    }


def verify_parent_result(run_dir: Path, experiment_id: str, config: dict) -> dict:
    result = _read_object(run_dir / "result.json")
    require(result.get("status") == "accepted" and result.get("experiment_id") == experiment_id, f"parent result rejected: {experiment_id}")
    require(result["runtime_nonlocal_projectors_total"] == 18, "parent projector count differs")
    require(result["pseudo_identity"]["sha256"] == config["pseudodojo"]["materials"]["al"]["sha256"], "parent pseudo differs")
    for identity in result["evidence_files"]:
        path = run_dir / identity["path"]
        require(path.is_file() and not path.is_symlink(), f"missing parent evidence: {path}")
        require(path.stat().st_size == identity["size_bytes"] and sha256_file(path) == identity["sha256"], f"parent evidence identity differs: {path}")
    require(_read_object(run_dir / "runner_return.json").get("return_code") == 0, "parent runner return differs")
    return result


def energy(result: dict) -> float:
    return float(result["thermodynamic_labels_ev_per_atom"]["E_ec"])


def local_reference_energy(project_root: Path, experiment_id: str) -> tuple[float, dict]:
    if experiment_id == "S1-20260807-043":
        path = project_root / "runs/S1-20260807-043/result.json"
        payload = _read_object(path)
        require(payload.get("converged") is True, "local anchor 043 not converged")
        value = float(payload["zero_temp_extrapolated_energy_ev_per_atom"])
    else:
        path = project_root / f"analysis/s1/g1_displacement_strain_reference_analysis_r2_20260810/raw/{experiment_id}/analysis_result.json"
        payload = _read_object(path)
        require(payload.get("status") == "accepted", f"local strain reference rejected: {experiment_id}")
        value = float(payload["thermodynamic"]["energy_labels_ev_per_atom"]["E_ec"])
    relative = path.relative_to(project_root).as_posix()
    blob = subprocess.run(["git", "rev-parse", f"HEAD:{relative}"], cwd=project_root, check=True, text=True, stdout=subprocess.PIPE).stdout.strip()
    return value, {"path": relative, "sha256": sha256_file(path), "git_blob": blob}


def verify_geometry_bindings(project_root: Path, config: dict, new_runs_root: Path, continuation_runs_root: Path) -> list[dict]:
    rows = load_manifest(project_root)
    output: list[dict] = []
    for row in rows:
        experiment_id = row["experiment_id"]
        generated = project_root / config["input_root"] / experiment_id / "STRU"
        executed = new_runs_root / experiment_id / "STRU"
        base = project_root / row["construction_base_path"]
        reference = project_root / row["registered_geometry_path"]
        require(sha256_file(base) == row["construction_base_stru_sha256"], "construction base SHA differs")
        require(sha256_file(reference) == row["registered_geometry_stru_sha256"], "registered geometry SHA differs")
        require(generated.read_bytes() == executed.read_bytes(), "executed STRU differs from preregistered input")
        require(sha256_bytes(geometry_payload_bytes(generated.read_bytes())) == row["geometry_payload_sha256"], "generated geometry payload differs")
        if row["phase"] == "strain":
            binding = config["geometry_bindings"][experiment_id]
            construction = verify_strain_geometry(base.read_bytes(), generated.read_bytes(), reference.read_bytes(), binding["deformation_gradient"])
            complete_reference = generated.read_bytes() == reference.read_bytes()
        else:
            require(generated.read_bytes() == reference.read_bytes(), "endpoint complete STRU differs from registered geometry")
            common = continuation_runs_root / row["accepted_common_id"] / "STRU"
            require(common.read_bytes() == generated.read_bytes(), "accepted continuation common STRU differs from endpoint geometry")
            construction = {"accepted": True, "accepted_common_id": row["accepted_common_id"], "accepted_common_complete_stru_byte_identical": True}
            complete_reference = True
        output.append({
            "experiment_id": experiment_id,
            "construction_base_id": row["construction_base_id"],
            "registered_geometry_id": row["registered_geometry_id"],
            "registered_geometry_stru_sha256": row["registered_geometry_stru_sha256"],
            "geometry_payload_sha256": row["geometry_payload_sha256"],
            "complete_registered_stru_byte_identical": complete_reference,
            "independent_construction_hard_gate": construction,
        })
    return output


def analyze_r3_parser_regression(result: dict, config: dict) -> dict:
    """Close the R3 parser failure using the complete frozen 343 raw record."""
    spec = config["r3_parser_regression_fixture"]
    expected = spec["expected_diagnostic"]
    require(result.get("status") == "accepted", "R3 fixture parse rejected")
    require(result.get("experiment_id") == spec["source_run_id"], "R3 fixture experiment ID differs")
    require(result.get("phase") == "strain" and result.get("role") == "al_tetragonal_plus", "R3 fixture phase/role differs")
    require("requirement" not in result, "legacy requirement leaked into the R4 parse result")
    mapping = result.get("metadata_schema_mapping")
    require(isinstance(mapping, dict) and mapping.get("accepted") is True, "metadata schema mapping proof missing")
    require(mapping.get("raw_schema") == "role_only_requirement_absent", "metadata schema mapping source differs")
    require(mapping.get("legacy_requirement_was_present_in_raw") is False, "legacy requirement overclaim")
    hard_gates = result.get("hard_gates")
    require(isinstance(hard_gates, dict) and hard_gates and all(value is True for value in hard_gates.values()), "R3 fixture per-point hard gates rejected")
    observed = {
        "E_ec_ev_per_atom": result["thermodynamic_labels_ev_per_atom"]["E_ec"],
        "pressure_gpa": result["pressure_gpa"],
        "cube_electron_relative_error": result["electron_number"]["relative_error"],
        "eig_occ_electron_relative_error": result["band_occupation"]["electron_relative_error"],
        "last_band_maximum_occupation": result["band_occupation"]["last_band_maximum_occupation"],
    }
    tolerances = {
        "E_ec_ev_per_atom": 1e-12,
        "pressure_gpa": 1e-12,
        "cube_electron_relative_error": 1e-20,
        "eig_occ_electron_relative_error": 1e-20,
        "last_band_maximum_occupation": 0.0,
    }
    for key, value in observed.items():
        difference = abs(float(value) - float(expected[key]))
        if tolerances[key] == 0.0:
            require(difference == 0.0, f"R3 fixture diagnostic differs: {key}")
        else:
            require(difference <= tolerances[key], f"R3 fixture diagnostic differs: {key}")
    return {
        "status": "accepted_parser_schema_failure_closed_by_complete_raw_replay",
        "source_run_id": spec["source_run_id"],
        "metadata_schema_mapping": mapping,
        "all_per_point_hard_gates_accepted": True,
        "observed_diagnostic": observed,
        "expected_diagnostic": expected,
        "accepted": True,
    }


def evaluate_gates(new: dict[str, dict], parent: dict[str, dict], local: dict[str, float], config: dict) -> tuple[dict, list[dict]]:
    strain_limit = float(config["acceptance"]["strain_anchored_difference_mev_per_atom_max"])
    strain_rows: list[dict] = []
    gate_rows: list[dict] = []
    for new_id, local_id in STRAIN_MAP.items():
        nl_response = energy(new[new_id]) - energy(parent["S1-20260810-301"])
        local_response = local[local_id] - local["S1-20260807-043"]
        delta = (nl_response - local_response) * 1000.0
        accepted = abs(delta) <= strain_limit
        strain_rows.append({
            "experiment_id": new_id, "local_reference_id": local_id,
            "nl_anchor_id": "S1-20260810-301", "local_anchor_id": "S1-20260807-043",
            "nl_strain_response_mev_per_atom": nl_response * 1000.0,
            "local_strain_response_mev_per_atom": local_response * 1000.0,
            "anchored_scheme_and_construction_difference_mev_per_atom": delta,
            "absolute_difference_mev_per_atom": abs(delta), "maximum_allowed_mev_per_atom": strain_limit,
            "inequality": "less_than_or_equal", "accepted": accepted,
        })
        gate_rows.append({"gate": "strain", "point": new_id, "metric": "absolute_scheme_and_construction_difference_mev_per_atom", "value": abs(delta), "limit": strain_limit, "inequality": "<=", "accepted": accepted})
    k_limit = float(config["acceptance"]["endpoint_anchored_k_difference_mev_per_atom_strictly_less_than"])
    cutoff_limit = float(config["acceptance"]["endpoint_anchored_cutoff_difference_mev_per_atom_strictly_less_than"])
    pressure_limit = float(config["acceptance"]["endpoint_cutoff_pressure_difference_gpa_strictly_less_than"])
    endpoint_specs = {
        "v090": ("S1-20260810-327", "S1-20260810-355", "S1-20260810-356"),
        "v110": ("S1-20260810-328", "S1-20260810-357", "S1-20260810-358"),
    }
    endpoint_rows: list[dict] = []
    for label, (common_id, extra_id, high_id) in endpoint_specs.items():
        common_shape = energy(parent[common_id]) - energy(parent["S1-20260810-301"])
        extra_shape = energy(new[extra_id]) - energy(parent["S1-20260810-302"])
        high_shape = energy(new[high_id]) - energy(parent["S1-20260810-303"])
        k_delta = abs(extra_shape - common_shape) * 1000.0
        cutoff_delta = abs(high_shape - extra_shape) * 1000.0
        pressure_delta = abs(float(new[high_id]["pressure_gpa"]) - float(new[extra_id]["pressure_gpa"]))
        accepted = k_delta < k_limit and cutoff_delta < cutoff_limit and pressure_delta < pressure_limit
        endpoint_rows.append({
            "endpoint": label, "common_id": common_id, "normal_extra_k_id": extra_id,
            "high_extra_k_id": high_id, "v100_anchor_ids": list(ANCHOR_IDS),
            "anchored_k_difference_mev_per_atom": k_delta,
            "anchored_cutoff_difference_mev_per_atom": cutoff_delta,
            "cutoff_pressure_difference_gpa": pressure_delta, "accepted": accepted,
        })
        for metric, value, limit in (("anchored_k_difference_mev_per_atom", k_delta, k_limit), ("anchored_cutoff_difference_mev_per_atom", cutoff_delta, cutoff_limit), ("cutoff_pressure_difference_gpa", pressure_delta, pressure_limit)):
            gate_rows.append({"gate": "endpoint", "point": label, "metric": metric, "value": value, "limit": limit, "inequality": "<", "accepted": value < limit})
    accepted = all(row["accepted"] for row in strain_rows + endpoint_rows)
    return {
        "status": "accepted" if accepted else "rejected",
        "strain": {"status": "accepted" if all(row["accepted"] for row in strain_rows) else "rejected", "point_count": 4, "rows": strain_rows},
        "endpoints": {"status": "accepted" if all(row["accepted"] for row in endpoint_rows) else "rejected", "endpoint_count": 2, "rows": endpoint_rows},
    }, gate_rows


def verify_expected_scientific_metrics(summary: dict, gate_rows: list[dict], r5_config: dict) -> dict:
    expected = r5_config["expected_scientific_metrics"]
    require(expected.get("comparison") == "exact_binary64_replay", "R5 scientific comparison mode differs")
    gates = summary.get("galileo_gates")
    require(isinstance(gates, dict), "R5 Galileo gate summary missing")
    observed_strain = {
        row["experiment_id"]: row["absolute_difference_mev_per_atom"]
        for row in gates["strain"]["rows"]
    }
    observed_endpoints = {
        row["endpoint"]: {
            "anchored_k_difference_mev_per_atom": row["anchored_k_difference_mev_per_atom"],
            "anchored_cutoff_difference_mev_per_atom": row["anchored_cutoff_difference_mev_per_atom"],
            "cutoff_pressure_difference_gpa": row["cutoff_pressure_difference_gpa"],
        }
        for row in gates["endpoints"]["rows"]
    }
    require(observed_strain == expected["strain_absolute_difference_mev_per_atom"], "R5 strain scientific metrics differ")
    require(observed_endpoints == expected["endpoints"], "R5 endpoint scientific metrics differ")
    require(summary.get("status") == gates.get("status") == expected["overall_status"], "R5 scientific status differs")
    require(len(gate_rows) == expected["gate_row_count"], "R5 scientific gate-row denominator differs")
    expected_gate_values: dict[tuple[str, str, str], float] = {}
    for experiment_id, value in expected["strain_absolute_difference_mev_per_atom"].items():
        expected_gate_values[("strain", experiment_id, "absolute_scheme_and_construction_difference_mev_per_atom")] = value
    for endpoint, metrics in expected["endpoints"].items():
        for metric, value in metrics.items():
            expected_gate_values[("endpoint", endpoint, metric)] = value
    observed_gate_values = {
        (row["gate"], row["point"], row["metric"]): row["value"]
        for row in gate_rows
    }
    require(len(observed_gate_values) == len(gate_rows), "R5 scientific gate rows contain duplicate identities")
    require(observed_gate_values == expected_gate_values, "R5 scientific gate-row values differ")
    require(all(row.get("accepted") is True for row in gate_rows), "R5 scientific gate row rejected")
    observed_closure_diagnostic = {
        "build_analysis_row_count": len(gate_rows),
        "endpoint_v090": observed_endpoints["v090"],
        "endpoint_v110": observed_endpoints["v110"],
        "gate_status": gates["status"],
        "maximum_strain_absolute_difference_mev_per_atom": max(observed_strain.values()),
    }
    require(
        observed_closure_diagnostic
        == r5_config["r4_analyzer_false_negative_closure"]["scientific_diagnostic"],
        "R5 recomputed scientific metrics differ from R4 closure diagnostic",
    )
    return {
        "accepted": True,
        "comparison": expected["comparison"],
        "gate_row_count": len(gate_rows),
        "overall_status": summary["status"],
        "strain_absolute_difference_mev_per_atom": observed_strain,
        "endpoints": observed_endpoints,
        "r4_closure_scientific_diagnostic": observed_closure_diagnostic,
    }


def verify_snapshot_evidence_inventory(run_dir: Path, identities: object, pseudo_closure: dict) -> list[dict]:
    require(isinstance(identities, list) and identities, "snapshot evidence inventory missing")
    verified: list[dict] = []
    seen: set[str] = set()
    for identity in identities:
        require(isinstance(identity, dict), "snapshot evidence identity must be an object")
        relative = identity.get("path")
        require(isinstance(relative, str) and relative and relative not in seen, "snapshot evidence path invalid or duplicate")
        relative_path = Path(relative)
        require(not relative_path.is_absolute() and ".." not in relative_path.parts, "snapshot evidence path escapes run")
        seen.add(relative)
        if relative_path.name.lower().endswith(".upf"):
            require(relative_path.name == pseudo_closure["basename"], "external pseudo evidence basename differs")
            require(identity.get("sha256") == pseudo_closure["sha256"], "external pseudo evidence SHA differs")
            raw_pseudo = run_dir / relative_path
            if raw_pseudo.exists():
                require(raw_pseudo.is_file() and not raw_pseudo.is_symlink(), "unsafe raw pseudo evidence")
                require(sha256_file(raw_pseudo) == identity.get("sha256"), "raw pseudo evidence SHA differs")
                size = raw_pseudo.stat().st_size
            else:
                size = Path(pseudo_closure["external_cache_path"]).stat().st_size
            require(identity.get("size_bytes") == size, "external pseudo evidence size differs")
        else:
            path = run_dir / relative_path
            require(path.is_file() and not path.is_symlink(), f"snapshot evidence missing: {path}")
            require(path.stat().st_size == identity.get("size_bytes"), f"snapshot evidence size differs: {path}")
            require(sha256_file(path) == identity.get("sha256"), f"snapshot evidence SHA differs: {path}")
        verified.append({"path": relative, "sha256": identity["sha256"], "size_bytes": identity["size_bytes"]})
    return verified


def continuation_replay_identity(run_dir: Path, result: dict, config: dict, continuation_spec: dict) -> tuple[dict, dict]:
    replay_config = json.loads(json.dumps(config))
    replay_config["protocol_revision"] = continuation_spec["protocol_revision"]
    replay_config["runtime"]["required_hostname"] = continuation_spec["required_hostname"]
    replay_config["runtime"]["physical_core_ids"] = continuation_spec["runtime_physical_core_ids"]
    replay_config["runtime"]["rank_count"] = continuation_spec["runtime_rank_count"]
    reparsed, pseudo_closure = reparse_with_external_pseudo(
        run_dir, result, replay_config, require_followup_orchestration=False
    )
    require(reparsed.get("status") == "accepted" and all(reparsed.get("hard_gates", {}).values()), "committed continuation raw replay rejected")
    for key in (
        "experiment_id", "atom_count", "expected_electrons", "runtime_nonlocal_projectors_total",
        "pseudo_identity", "thermodynamic_labels_ev_per_cell", "thermodynamic_labels_ev_per_atom",
        "pressure_kbar", "pressure_gpa",
    ):
        require(reparsed.get(key) == result.get(key), f"committed continuation stored/raw replay differs: {key}")
    return {
        "parser": "parse_s1_g1_three_layer_al_followup_r4.parse_run",
        "reparsed_sha256": sha256_bytes(canonical_json_bytes(reparsed)),
        "cube_origin_exactly_zero": reparsed["cube_geometry"]["origin_exactly_zero"],
        "stress_pressure_accepted": reparsed["mechanics"]["accepted"],
        "all_hard_gates_accepted": all(reparsed["hard_gates"].values()),
        "accepted": True,
    }, pseudo_closure


def verify_snapshot_source_identity(project_root: Path, config: dict, new_state: Path, r1_state: Path, continuation_state: Path) -> dict:
    session = _read_object(new_state / "session.json")
    frozen = session.get("parent_source_identity")
    require(isinstance(frozen, dict) and frozen.get("ready") is True, "follow-up session lacks frozen parent source identity")
    r1_session = _read_object(r1_state / "session.json")
    continuation_session = _read_object(continuation_state / "session.json")
    continuation_spec = config["source_states"]["continuation_r2"]
    barrier_path = continuation_state / continuation_spec["recovery_barrier_relative_path"]
    phase_path = continuation_state / continuation_spec["endpoint_phase_marker_relative_path"]
    barrier_payload = _read_object(barrier_path)
    phase_payload = _read_object(phase_path)
    continuation_runner = continuation_session["runner_commit"]
    continuation_config, _, continuation_config_bytes = committed_json(
        project_root, continuation_runner, continuation_spec["config_path"]
    )
    require(sha256_bytes(continuation_config_bytes) == continuation_session.get("config_sha256"), "snapshot continuation committed config differs")
    old_spec = config["source_states"]["r1_p0"]
    r1_config, _, r1_config_bytes = committed_json(
        project_root, old_spec["runner_commit"], old_spec["source_git_paths"][0]
    )
    require(sha256_file(barrier_path) == continuation_spec["recovery_barrier_sha256"], "snapshot barrier differs from frozen SHA")
    formalized_bytes, formalized_blob = git_file_at_commit(
        project_root,
        continuation_spec["recovery_formalization_commit"],
        continuation_spec["versioned_recovery_barrier_path"],
    )
    require(formalized_bytes == barrier_path.read_bytes(), "snapshot barrier differs from frozen formalization commit")
    versioned_bytes, versioned_blob = git_file_at_commit(project_root, continuation_runner, continuation_spec["versioned_recovery_barrier_path"])
    require(versioned_bytes == barrier_path.read_bytes(), "snapshot barrier differs from continuation runner Git blob")
    require(versioned_blob == formalized_blob, "snapshot recovery barrier Git blob changed after formalization")

    barrier_rows = barrier_payload.get("per_run_recovery")
    require(isinstance(barrier_rows, list), "snapshot recovery inventory missing")
    barrier_inventory = {
        row["experiment_id"]: row
        for row in barrier_rows
        if isinstance(row, dict) and isinstance(row.get("experiment_id"), str)
    }
    require(list(barrier_inventory) == list(RECOVERY_IDS), "snapshot recovery inventory denominator differs")
    source_config_identity = next(
        identity for identity in barrier_payload["source_git_identities"]
        if identity["path"] == old_spec["source_git_paths"][0]
    )
    require(sha256_bytes(r1_config_bytes) == source_config_identity["sha256"], "snapshot R1 committed config differs")
    recovered: dict[str, dict] = {}
    for experiment_id in RECOVERY_IDS:
        identity = verify_accepted_source(r1_state, experiment_id, r1_session)
        inventory = barrier_inventory[experiment_id]
        attempt_path = r1_state / "attempts" / f"{experiment_id}.json"
        require(inventory.get("attempt_sha256") == sha256_file(attempt_path), f"snapshot recovery attempt SHA differs: {experiment_id}")
        require(inventory.get("accepted_sha256") == identity["accepted_marker_sha256"], f"snapshot recovery marker SHA differs: {experiment_id}")
        require(inventory.get("accepted_result_sha256") == inventory.get("result_sha256") == identity["result_sha256"], f"snapshot recovery result SHA differs: {experiment_id}")
        require(inventory.get("runner_return_sha256") == identity["runner_return_sha256"] and inventory.get("runner_return_code") == 0, f"snapshot recovery return SHA differs: {experiment_id}")
        require(inventory.get("r1_parser_byte_exact_replay") is True, f"snapshot R1 parser replay proof differs: {experiment_id}")
        enhanced = inventory.get("enhanced_raw_gates")
        require(isinstance(enhanced, dict) and enhanced.get("accepted") is True, f"snapshot enhanced recovery gate rejected: {experiment_id}")
        result = _read_object(r1_state / "runs" / experiment_id / "result.json")
        pseudo_closure = verify_pseudo_identity_closure(
            r1_state / "runs" / experiment_id, result, config, require_run_body=False
        )
        identity["enhanced_raw_evidence"] = verify_snapshot_evidence_inventory(
            r1_state / "runs" / experiment_id, enhanced.get("evidence_files"), pseudo_closure
        )
        identity["pseudo_identity_closure"] = pseudo_closure
        base_reparsed, enhanced_reparsed, replay_identity = replay_r1_p0(
            r1_state / "runs" / experiment_id, r1_config, continuation_config
        )
        require(canonical_json_bytes(base_reparsed) == (r1_state / "runs" / experiment_id / "result.json").read_bytes(), f"snapshot R1 base replay differs: {experiment_id}")
        require(canonical_json_bytes(enhanced_reparsed) == canonical_json_bytes(enhanced), f"snapshot R1 enhanced replay differs: {experiment_id}")
        identity["committed_raw_replay"] = replay_identity
        recovered[experiment_id] = identity

    phase_sources: dict[str, dict] = {}
    for experiment_id in CONTINUATION_PHASE_IDS:
        identity = verify_accepted_source(continuation_state, experiment_id, continuation_session)
        result = _read_object(continuation_state / "runs" / experiment_id / "result.json")
        replay, pseudo_closure = continuation_replay_identity(
            continuation_state / "runs" / experiment_id, result, config, continuation_spec
        )
        identity["independent_raw_replay"] = replay
        identity["pseudo_identity_closure"] = pseudo_closure
        identity["orchestration_identity"] = verify_continuation_source_orchestration(
            continuation_state,
            experiment_id,
            continuation_session,
            project_root,
            continuation_spec,
            continuation_config,
        )
        phase_sources[experiment_id] = identity
    expected_phase_results = {experiment_id: phase_sources[experiment_id]["result_sha256"] for experiment_id in CONTINUATION_PHASE_IDS}
    require(phase_payload.get("accepted_result_sha256") == expected_phase_results, "snapshot phase result-SHA map differs")
    require(phase_payload.get("session_sha256") == sha256_file(continuation_state / "session.json"), "snapshot phase/session SHA differs")
    require(phase_payload.get("config_sha256") == continuation_session.get("config_sha256") == barrier_payload.get("config_sha256"), "snapshot phase/config SHA differs")
    require(phase_payload.get("manifest_sha256") == continuation_session.get("manifest_sha256") == barrier_payload.get("manifest_sha256"), "snapshot phase/manifest SHA differs")
    require(phase_payload.get("recovery_barrier_sha256") == sha256_file(barrier_path), "snapshot phase/barrier SHA differs")
    phase_preflight = verify_continuation_phase_preflight(
        continuation_state, continuation_session, phase_payload, list(CONTINUATION_PHASE_IDS), continuation_spec
    )
    observed = {
        "ready": True,
        "r1_session_sha256": sha256_file(r1_state / "session.json"),
        "r1_recovery_barrier_sha256": sha256_file(barrier_path),
        "r1_recovery_versioned_git_blob": versioned_blob,
        "r1_recovery_formalization_commit": continuation_spec["recovery_formalization_commit"],
        "r1_recovery_formalized_git_blob": formalized_blob,
        "continuation_runner_commit": continuation_runner,
        "continuation_session_sha256": sha256_file(continuation_state / "session.json"),
        "continuation_endpoint_phase_sha256": sha256_file(phase_path),
        "continuation_endpoint_phase_accepted_result_sha256": expected_phase_results,
        "continuation_phase_preflight_identity": phase_preflight,
        "r1_recovered_sources": recovered,
        "continuation_al_eos_sources": phase_sources,
    }

    head = str(_git(project_root, "rev-parse", "HEAD")).strip()
    extension = {
        "r2_operational_failure_closure": verify_r2_operational_failure_closure(config, project_root, head),
        "r3_operational_failure_closure": verify_r3_operational_failure_closure(config, project_root, head),
        "r3_parser_regression": validate_fixture(project_root, config),
    }
    require(set(extension) == EXPECTED_EXTENSION_KEYS, "R5 reconstructed parent extension key set differs")
    require(not (set(observed) & set(extension)), "R5 reconstructed parent extension overlaps base identity")
    for key, value in extension.items():
        require(frozen.get(key) == value, f"R5 independently reconstructed parent identity differs: {key}")
    observed.update(extension)
    require(observed == frozen, "parent source snapshot differs from runner-frozen identity")
    return observed


def build_analysis(project_root: Path, config: dict, new_state: Path, r1_state: Path, continuation_state: Path) -> tuple[dict, list[dict]]:
    source_identity = verify_snapshot_source_identity(project_root, config, new_state, r1_state, continuation_state)
    new_runs = new_state / "runs"
    r1_runs = r1_state / "runs"
    continuation_runs = continuation_state / "runs"
    session = _read_object(new_state / "session.json")
    terminal = _read_object(new_state / "terminal.json")
    config_sha = sha256_file(project_root / "config/S1_g1_three_layer_al_domain_followup_r4.json")
    manifest_sha = sha256_file(project_root / "config/S1_g1_three_layer_al_domain_followup_r4_manifest.tsv")
    require(session.get("protocol_revision") == config["protocol_revision"], "follow-up session protocol differs")
    require(session.get("config_sha256") == config_sha and session.get("manifest_sha256") == manifest_sha, "follow-up session registered-file identity differs")
    runner_commit = session.get("runner_commit")
    require(isinstance(runner_commit, str) and len(runner_commit) == 40, "follow-up runner commit differs")
    new: dict[str, dict] = {}
    new_identities: dict[str, dict] = {}
    for experiment_id in config["formal_ids"]:
        result, identity = verify_new_result(new_state, experiment_id, config, session)
        new[experiment_id] = result
        new_identities[experiment_id] = identity
    expected_result_map = {experiment_id: new_identities[experiment_id]["result_sha256"] for experiment_id in config["formal_ids"]}
    expected_terminal_maps = {
        "attempt_marker_sha256": {experiment_id: new_identities[experiment_id]["attempt_marker_sha256"] for experiment_id in config["formal_ids"]},
        "accepted_marker_sha256": {experiment_id: new_identities[experiment_id]["accepted_marker_sha256"] for experiment_id in config["formal_ids"]},
        "runner_return_sha256": {experiment_id: new_identities[experiment_id]["runner_return_sha256"] for experiment_id in config["formal_ids"]},
        "result_sha256": expected_result_map,
        "metadata_sha256": {experiment_id: new_identities[experiment_id]["metadata_sha256"] for experiment_id in config["formal_ids"]},
    }
    require(terminal.get("status") == "accepted" and terminal.get("protocol_revision") == config["protocol_revision"], "follow-up terminal rejected")
    require(terminal.get("runner_commit") == runner_commit, "follow-up terminal/runner differs")
    require(terminal.get("session_sha256") == sha256_file(new_state / "session.json"), "follow-up terminal/session SHA differs")
    require(terminal.get("config_sha256") == config_sha and terminal.get("manifest_sha256") == manifest_sha, "follow-up terminal registered-file identity differs")
    require(terminal.get("attempted_ids") == terminal.get("accepted_ids") == config["formal_ids"], "follow-up terminal ID denominator differs")
    require(terminal.get("attempted_count") == terminal.get("accepted_count") == 8, "follow-up terminal count denominator differs")
    require(terminal.get("accepted_result_sha256") == expected_result_map, "follow-up terminal result-SHA map differs")
    for key, expected in expected_terminal_maps.items():
        require(terminal.get(key) == expected, f"follow-up terminal {key} differs")
    require(terminal.get("failed_count") == terminal.get("retried_count") == 0 and terminal.get("runner_return_code") == 0, "follow-up terminal failure/retry differs")
    parent = {experiment_id: verify_parent_result(r1_runs / experiment_id, experiment_id, config) for experiment_id in ANCHOR_IDS}
    parent.update({experiment_id: verify_parent_result(continuation_runs / experiment_id, experiment_id, config) for experiment_id in CONTINUATION_IDS})
    local: dict[str, float] = {}
    identities: list[dict] = []
    for experiment_id in ("S1-20260807-043", *STRAIN_MAP.values()):
        local[experiment_id], identity = local_reference_energy(project_root, experiment_id)
        identities.append(identity)
    gates, gate_rows = evaluate_gates(new, parent, local, config)
    geometry = verify_geometry_bindings(project_root, config, new_runs, continuation_runs)
    status = "accepted" if gates["status"] == "accepted" else "rejected"
    r5_config = _load_r5_config(project_root)
    preregistration = _introduction_commit(project_root, R5_CONFIG_PATH)
    summary = {
        "schema_version": 1, "protocol_revision": config["protocol_revision"],
        "analysis_protocol_revision": ANALYSIS_PROTOCOL_REVISION,
        "analysis_preregistration_commit": preregistration,
        "analysis_implementation_commit": r5_config["implementation_commit"],
        "r4_analyzer_false_negative_closure": verify_r4_false_negative_closure(project_root, r5_config),
        "status": status,
        "scope_status": "accepted_al_domain_followup" if status == "accepted" else "rejected_al_domain_followup",
        "registered_run_count": 8, "accepted_run_count": 8, "failed_missing_skipped_retried_count": 0,
        "runner_commit": runner_commit, "all_per_point_hard_gates_accepted": all(all(result["hard_gates"].values()) for result in new.values()),
        "followup_orchestration_identities": new_identities,
        "geometry_bindings": geometry, "galileo_gates": gates,
        "parent_reference_ids": [*ANCHOR_IDS, *CONTINUATION_IDS], "parent_source_identity": source_identity,
        "legacy_local_reference_identities": identities,
        "runtime_provenance": config["runtime_provenance"],
        "pseudo_body_evidence_policy": {
            "status": "accepted_identity_closure_external_body_with_explicit_minimal_input_schema_mapping",
            "upf_body_committed": False,
            "server_external_cache_fully_validated": True,
            "identity_bindings": ["minimal_input_schema_explicit_mapping", "config_repository_commit_url_git_blob_sha_header", "pseudo_identity_json", "metadata_runtime_identity", "result_pseudo_identity", "log_runtime_projector_total"],
        },
        "scientific_interpretation": "same-engine NLPP-versus-local scheme plus construction discrepancy suitability bound; not an isolated LPP scheme-bias bound",
        "semantic_limits": {
            "second_independent_KS_engine_closed": False, "D_026_QE_scope_closed": False,
            "G4_force_or_stress_closed": False, "absolute_cross_PP_energy_gate_applied": False,
            "isolated_lpp_scheme_bias_claim": False, "zero_temperature_exact_claim": False,
        },
    }
    summary["expected_scientific_metrics_verification"] = verify_expected_scientific_metrics(
        summary, gate_rows, r5_config,
    )
    return summary, gate_rows


def gate_tsv_bytes(rows: list[dict]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=GATE_FIELDS, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode()


def readme_bytes(summary: dict) -> bytes:
    return ("\n".join([
        "# S1/G1 Al-domain 三层补充 analysis-only R5", "",
        f"状态：`{summary['scope_status']}`；重放 R4 351–358 共 8/8，新 solver 运行数为 0。", "",
        f"四个 301/043 锚定 strain 门：`{summary['galileo_gates']['strain']['status']}`；两个 327/328 端点的锚定 k/cutoff/pressure 门：`{summary['galileo_gates']['endpoints']['status']}`。", "",
        "R5 独立重建 R2/R3 closure 与 343 parser regression 三项身份后保留 whole-dict equality；未删除键或放宽父源比较。", "",
        "R5 将旧 minimal input pseudo schema 显式映射到冻结 config 的 repository/commit/canonical URL/git-blob/SHA，并继续完整核 runtime/result/pseudo_identity、外部 cache header/projectors。", "",
        "解释边界仍为同引擎 NLPP/local scheme+construction discrepancy 与适用性；不冒充孤立 LPP scheme bias，也不关闭第二 KS/QE 或 G4。",
    ]) + "\n").encode()


def write_analysis(output_root: Path, summary: dict, gate_rows: list[dict]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    atomic_write(output_root / "summary.json", canonical_json_bytes(summary))
    atomic_write(output_root / "gates.tsv", gate_tsv_bytes(gate_rows))
    atomic_write(output_root / "README.md", readme_bytes(summary))


def copy_exact(source: Path, destination: Path) -> None:
    require(source.is_file() and not source.is_symlink(), f"not a regular source: {source}")
    require(not destination.exists(), f"evidence destination exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    require(sha256_file(source) == sha256_file(destination), "evidence copy SHA differs")


def copy_run_snapshot(
    source_state: Path,
    destination_state: Path,
    experiment_id: str,
    *,
    include_attempt: bool,
    additional_evidence: object = None,
) -> None:
    source_run = source_state / "runs" / experiment_id
    result = _read_object(source_run / "result.json")
    inventories = [result["evidence_files"]]
    if additional_evidence is not None:
        require(isinstance(additional_evidence, list), "additional snapshot evidence must be a list")
        inventories.append(additional_evidence)
    names: set[str] = set()
    identities: dict[str, tuple[str, int]] = {}
    for inventory in inventories:
        for identity in inventory:
            require(isinstance(identity, dict), "snapshot source evidence identity differs")
            name = identity.get("path")
            require(isinstance(name, str) and name, "snapshot source evidence path missing")
            relative = Path(name)
            require(not relative.is_absolute() and ".." not in relative.parts, "snapshot source evidence path escapes run")
            expected = (identity.get("sha256"), identity.get("size_bytes"))
            require(name not in identities or identities[name] == expected, "conflicting snapshot source evidence identity")
            identities[name] = expected
            source = source_run / relative
            require(source.is_file() and not source.is_symlink(), f"snapshot source evidence missing: {source}")
            require(sha256_file(source) == expected[0] and source.stat().st_size == expected[1], f"snapshot source evidence identity differs: {source}")
            names.add(name)
    names.update({"result.json", "runner_return.json"})
    for optional in ("input_metadata.json", "pseudo_identity.json"):
        if (source_run / optional).is_file():
            names.add(optional)
    for name in sorted(names):
        if Path(name).name.lower().endswith(".upf"):
            continue
        copy_exact(source_run / name, destination_state / "runs" / experiment_id / name)
    copy_exact(source_state / "accepted" / f"{experiment_id}.json", destination_state / "accepted" / f"{experiment_id}.json")
    if include_attempt:
        copy_exact(source_state / "attempts" / f"{experiment_id}.json", destination_state / "attempts" / f"{experiment_id}.json")


def collect(project_root: Path, config: dict) -> tuple[Path, Path, Path, Path, dict, list[dict]]:
    source_new = Path(config["external_state_root"])
    terminal = _read_object(source_new / "terminal.json")
    require(terminal.get("status") == "accepted" and terminal.get("accepted_count") == 8 and terminal.get("runner_return_code") == 0, "follow-up terminal not accepted")
    source_r1 = Path(config["source_states"]["r1_p0"]["external_state_root"])
    source_cont = Path(config["source_states"]["continuation_r2"]["external_state_root"])
    external_summary, external_rows = build_analysis(project_root, config, source_new, source_r1, source_cont)
    analysis_root = project_root / ANALYSIS_ROOT
    require(not analysis_root.exists(), "analysis root already exists")
    snapshot = analysis_root / "state_snapshot"
    new = snapshot / "followup_r4"
    r1 = snapshot / "r1_p0"
    cont = snapshot / "continuation_r2"
    for experiment_id in config["formal_ids"]:
        copy_run_snapshot(source_new, new, experiment_id, include_attempt=True)
    continuation_spec = config["source_states"]["continuation_r2"]
    barrier_payload = _read_object(source_cont / continuation_spec["recovery_barrier_relative_path"])
    recovery_inventory = {
        row["experiment_id"]: row
        for row in barrier_payload.get("per_run_recovery", [])
        if isinstance(row, dict) and isinstance(row.get("experiment_id"), str)
    }
    require(list(recovery_inventory) == list(RECOVERY_IDS), "collect recovery denominator differs")
    for experiment_id in RECOVERY_IDS:
        enhanced = recovery_inventory[experiment_id].get("enhanced_raw_gates")
        require(isinstance(enhanced, dict), f"collect enhanced recovery evidence missing: {experiment_id}")
        copy_run_snapshot(
            source_r1,
            r1,
            experiment_id,
            include_attempt=True,
            additional_evidence=enhanced.get("evidence_files"),
        )
    for experiment_id in CONTINUATION_PHASE_IDS:
        copy_run_snapshot(source_cont, cont, experiment_id, include_attempt=True)
    copy_exact(source_new / "session.json", new / "session.json")
    copy_exact(source_new / "terminal.json", new / "terminal.json")
    copy_exact(source_r1 / "session.json", r1 / "session.json")
    copy_exact(source_cont / "session.json", cont / "session.json")
    for relative in (continuation_spec["recovery_barrier_relative_path"], continuation_spec["endpoint_phase_marker_relative_path"]):
        copy_exact(source_cont / relative, cont / relative)
    for relative in (
        "preflight/al_eos_detached_launch.json",
        "preflight/al_eos_core_collision.json",
        *(f"preflight/{experiment_id}.json" for experiment_id in CONTINUATION_PHASE_IDS),
    ):
        copy_exact(source_cont / relative, cont / relative)
    return analysis_root, new, r1, cont, external_summary, external_rows


def revision_bytes(project_root: Path, r5_config: dict, topology: dict, summary: dict) -> bytes:
    return canonical_json_bytes({
        "schema_version": 1,
        "analysis_protocol_revision": ANALYSIS_PROTOCOL_REVISION,
        "status": summary["status"],
        "new_solver_run_count": 0,
        "implementation_commit": r5_config["implementation_commit"],
        "preregistration_commit": topology["preregistration_commit"],
        "r4_analyzer_false_negative_closure": summary["r4_analyzer_false_negative_closure"],
        "source_r4_protocol_revision": summary["protocol_revision"],
        "source_r4_runner_commit": summary["runner_commit"],
        "accepted_run_count": summary["accepted_run_count"],
    })


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--collect", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    require(args.collect != args.dry_run, "choose exactly one of --collect or --dry-run")
    project_root = find_project_root(args.project_root)
    config = load_config(project_root)
    r5_config = _load_r5_config(project_root)
    topology = verify_preregistration_topology(
        project_root, r5_config, require_head_is_preregistration=True,
    )
    closure_identity = verify_r4_false_negative_closure(project_root, r5_config)
    analysis_root = project_root / ANALYSIS_ROOT
    require(not analysis_root.exists(), "R5 analysis root must be absent before execution")
    if args.dry_run:
        new = Path(config["external_state_root"])
        r1 = Path(config["source_states"]["r1_p0"]["external_state_root"])
        cont = Path(config["source_states"]["continuation_r2"]["external_state_root"])
        summary, rows = build_analysis(project_root, config, new, r1, cont)
        print(json.dumps({
            "status": "accepted_dry_run",
            "scientific_status": summary["status"],
            "gate_row_count": len(rows),
            "new_solver_run_count": 0,
            "analysis_output_created": False,
            "r4_closure": closure_identity,
            "topology": topology,
        }, sort_keys=True))
        return 0
    analysis_root, new, r1, cont, external_summary, external_rows = collect(project_root, config)
    summary, rows = build_analysis(project_root, config, new.resolve(), r1.resolve(), cont.resolve())
    require(canonical_json_bytes(summary) == canonical_json_bytes(external_summary), "R5 external/snapshot summary replay differs")
    require(gate_tsv_bytes(rows) == gate_tsv_bytes(external_rows), "R5 external/snapshot gate replay differs")
    write_analysis(analysis_root.resolve(), summary, rows)
    atomic_write(analysis_root / "analysis_revision.json", revision_bytes(project_root, r5_config, topology, summary))
    print(json.dumps({
        "status": summary["status"],
        "scope_status": summary["scope_status"],
        "output_root": str(analysis_root),
        "new_solver_run_count": 0,
    }, sort_keys=True))
    return 0 if summary["status"] == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
