#!/usr/bin/env python3
"""Replay committed R2 evidence and classify a valid scientific rejection."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from analyze_s1_eos import fit_bm3
from analyze_s1_g1_three_layer_continuation_r2 import build_final_analysis, write_analysis
from s1_g1_three_layer_continuation_r2_common import (
    atomic_write,
    canonical_json_bytes,
    find_project_root,
    git,
    load_config as load_r2_config,
    load_manifest,
    read_json,
    require,
    require_clean_tree,
    require_tracked_matches_head,
    sha256_file,
)


CONFIG_PATH = Path("config/S1_g1_three_layer_analysis_r3.json")
PROTOCOL_PATH = Path("docs/S1_G1_THREE_LAYER_ANALYSIS_R3_PROTOCOL.md")
REGISTERED_CODE = (
    CONFIG_PATH,
    PROTOCOL_PATH,
    Path("scripts/analyze_s1_g1_three_layer_analysis_r3.py"),
    Path("scripts/capture_s1_g1_three_layer_r2_rejection.py"),
    Path("scripts/validate_s1_g1_three_layer_analysis_r3.py"),
    Path("tests/test_s1_g1_three_layer_analysis_r3.py"),
)
CAPTURE_ARTIFACTS = (
    "orchestration/analysis.stdout",
    "orchestration/analysis.stderr",
    "orchestration/analysis_invocation.json",
)
FINAL_OUTPUT_FILES = ("README.md", "gate_metrics.tsv", "summary.json")
INVOCATION_KEYS = {
    "schema_version", "protocol_revision", "status", "argv", "capture_cwd", "cwd",
    "r2_runner_commit", "capture_preregistered_commit", "capture_implementation_commit",
    "capture_script", "registration_changed_paths", "capture_config_path",
    "capture_config_sha256", "r2_dependencies", "analyzer", "r2_config", "terminal",
    "exit_code", "expected_summary_status", "summary_sha256", "stdout_sha256",
    "stdout_size_bytes", "stderr_sha256", "stderr_size_bytes",
    "analyzer_output_inventory_before_capture_artifacts", "capture_added_artifacts",
    "subprocess_environment", "started_utc", "finished_utc", "duration_seconds",
    "interpretation",
}


def load_config(project_root: Path) -> dict:
    payload = read_json(project_root / CONFIG_PATH)
    require(isinstance(payload, dict), "R3 config must be object")
    require(payload.get("protocol_revision") == "S1-G1-THREE-LAYER-ANALYSIS-20260810-R3", "R3 protocol differs")
    return payload


def source_inventory(root: Path) -> dict:
    require(root.is_dir() and not root.is_symlink(), "inventory root missing or unsafe")
    all_paths = list(root.rglob("*"))
    require(not any(path.is_symlink() for path in all_paths), "unsafe R2 analysis tree")
    files = sorted((path for path in all_paths if path.is_file()), key=lambda path: path.relative_to(root).as_posix())
    require(files, "empty R2 analysis tree")
    entries = [
        {"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path), "size_bytes": path.stat().st_size}
        for path in files
    ]
    lines = "".join(f"{row['sha256']}  {row['path']}\n" for row in entries).encode("utf-8")
    return {
        "file_count": len(entries),
        "regular_file_bytes": sum(int(row["size_bytes"]) for row in entries),
        "sha256sum_list_digest": hashlib.sha256(lines).hexdigest(),
        "files": entries,
    }


def require_no_upf_body(root: Path) -> None:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        require(path.suffix.lower() != ".upf" and not path.name.lower().endswith(".upf"), f"UPF entered evidence tree: {relative}")
        with path.open("rb") as handle:
            prefix = handle.read(512).lstrip()
        require(not prefix.startswith(b"<UPF") and b"<PP_HEADER" not in prefix, f"UPF body signature entered evidence tree: {relative}")


def require_isolated_python() -> None:
    require(sys.flags.no_user_site == 1, "R3 requires Python -s")
    require(sys.dont_write_bytecode, "R3 requires Python -B")
    require(os.environ.get("PYTHONDONTWRITEBYTECODE") == "1", "R3 requires PYTHONDONTWRITEBYTECODE=1")
    require(os.environ.get("PYTHONNOUSERSITE") == "1", "R3 requires PYTHONNOUSERSITE=1")


def git_blob_identity(project_root: Path, commit: str, relative: str) -> dict:
    oid = git(project_root, "rev-parse", f"{commit}:{relative}")
    completed = subprocess.run(
        ["git", "cat-file", "blob", oid], cwd=project_root, check=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    path = project_root / relative
    require(path.is_file() and not path.is_symlink(), f"replay dependency missing: {relative}")
    require(path.read_bytes() == completed.stdout, f"replay dependency differs from runner: {relative}")
    return {"path": relative, "git_blob_oid": oid, "sha256": hashlib.sha256(completed.stdout).hexdigest(), "size_bytes": len(completed.stdout)}


def committed_blob_identity(project_root: Path, commit: str, relative: str) -> tuple[dict, bytes]:
    oid = git(project_root, "rev-parse", f"{commit}:{relative}")
    completed = subprocess.run(
        ["git", "cat-file", "blob", oid], cwd=project_root, check=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return ({"path": relative, "git_blob_oid": oid, "sha256": hashlib.sha256(completed.stdout).hexdigest(), "size_bytes": len(completed.stdout)}, completed.stdout)


def verify_r2_dependencies(project_root: Path, config: dict) -> list[dict]:
    runner = config["source_r2"]["runner_commit"]
    expected = config["capture"]["r2_dependencies"]
    require(isinstance(expected, list) and expected, "R2 replay dependency denominator missing")
    require(len({row["path"] for row in expected}) == len(expected), "R2 replay dependency paths duplicate")
    actual = []
    for row in expected:
        identity = git_blob_identity(project_root, runner, row["path"])
        require(identity["sha256"] == row["sha256"], f"R2 replay dependency SHA differs: {row['path']}")
        actual.append(identity)
    return actual


def verify_capture_registration_commits(project_root: Path, config: dict, invocation: dict) -> dict:
    source_spec = config["source_r2"]
    implementation = source_spec["capture_implementation_commit"]
    prereg = source_spec["capture_preregistered_commit"]
    require(invocation["capture_implementation_commit"] == implementation, "capture implementation source differs")
    require(invocation["capture_preregistered_commit"] == prereg, "capture prereg source differs")
    require(git(project_root, "rev-list", "--parents", "-n", "1", prereg).split() == [prereg, implementation], "capture prereg Git parent differs")
    changed = git(project_root, "diff", "--name-only", f"{implementation}..{prereg}").splitlines()
    require(changed == [CONFIG_PATH.as_posix()], "capture prereg Git diff differs")
    analysis_implementation = config["registration"]["analysis_implementation_commit"]
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", prereg, analysis_implementation], cwd=project_root,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    require(ancestor.returncode == 0, "capture prereg is not an ancestor of R3 analysis implementation")
    script_path = config["capture"]["capture_script_path"]
    script_impl, _ = committed_blob_identity(project_root, implementation, script_path)
    script_prereg, _ = committed_blob_identity(project_root, prereg, script_path)
    require(script_impl == script_prereg == invocation["capture_script"], "capture script Git blob identity differs")
    capture_config_identity, capture_config_bytes = committed_blob_identity(project_root, prereg, CONFIG_PATH.as_posix())
    require(capture_config_identity["sha256"] == invocation["capture_config_sha256"], "capture prereg config Git SHA differs")
    captured_config = json.loads(capture_config_bytes.decode("utf-8"))
    require(captured_config["capture"] == config["capture"], "capture registration block changed after capture")
    require(captured_config["capture"]["implementation_commit"] == implementation, "capture prereg config implementation differs")
    require(captured_config["capture"]["capture_script_sha256"] == script_prereg["sha256"], "capture prereg config/script SHA differs")
    return {
        "capture_implementation_commit": implementation,
        "capture_preregistered_commit": prereg,
        "capture_preregistered_parent": implementation,
        "capture_preregistered_changed_paths": changed,
        "capture_script": script_prereg,
        "capture_config": capture_config_identity,
    }


def validate_preregistered_topology(project_root: Path, config: dict, head: str | None = None) -> dict:
    prereg = head or git(project_root, "rev-parse", "HEAD")
    implementation = config["registration"]["analysis_implementation_commit"]
    require("__FREEZE" not in implementation, "R3 analysis implementation not frozen")
    require(git(project_root, "rev-list", "--parents", "-n", "1", prereg).split() == [prereg, implementation], "R3 prereg parent differs")
    changed = git(project_root, "diff", "--name-only", f"{implementation}..{prereg}").splitlines()
    require(changed == config["registration"]["preregistered_allowed_changed_paths"], "R3 prereg changed-path denominator differs")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", config["source_r2"]["evidence_commit"], prereg],
        cwd=project_root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    require(ancestor.returncode == 0, "R2 evidence commit is not an ancestor of R3 prereg")
    return {"analysis_implementation_commit": implementation, "analysis_preregistered_commit": prereg, "changed_paths": changed}


def git_ancestor(project_root: Path, commit: str) -> None:
    completed = subprocess.run(["git", "merge-base", "--is-ancestor", commit, "HEAD"], cwd=project_root)
    require(completed.returncode == 0, "R2 evidence commit is not an ancestor")


def verify_source_commit(project_root: Path, config: dict, source: Path, inventory: dict) -> dict:
    commit = config["source_r2"]["evidence_commit"]
    runner = config["source_r2"]["runner_commit"]
    git_ancestor(project_root, commit)
    parents = git(project_root, "rev-list", "--parents", "-n", "1", commit).split()
    require(parents == [commit, runner], "R2 evidence commit parent differs")
    prefix = config["source_r2"]["analysis_root"] + "/"
    absent_at_runner = subprocess.run(
        ["git", "cat-file", "-e", f"{runner}:{config['source_r2']['analysis_root']}"], cwd=project_root,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    require(absent_at_runner.returncode != 0, "R2 analysis root existed at runner commit")
    changed = git(project_root, "diff", "--name-only", f"{runner}..{commit}").splitlines()
    require(changed and all(path.startswith(prefix) for path in changed), "R2 evidence commit changed paths outside analysis")
    changed_status = git(project_root, "diff", "--name-status", f"{runner}..{commit}").splitlines()
    require(changed_status == [f"A\t{path}" for path in changed], "R2 evidence commit must only add analysis files")
    tree_oid = git(project_root, "rev-parse", f"{commit}:{config['source_r2']['analysis_root']}")
    require(tree_oid == config["source_r2"]["analysis_tree_oid"], "R2 analysis tree OID differs")
    listing = git(project_root, "ls-tree", "-r", "--full-tree", commit, "--", config["source_r2"]["analysis_root"]).splitlines()
    committed = {}
    for line in listing:
        metadata, path = line.split("\t", 1)
        mode, kind, oid = metadata.split()
        require(kind == "blob" and mode in {"100644", "100755"}, "R2 analysis contains non-blob entry")
        relative = Path(path).relative_to(config["source_r2"]["analysis_root"]).as_posix()
        completed = subprocess.run(["git", "cat-file", "blob", oid], cwd=project_root, check=True, stdout=subprocess.PIPE)
        committed[relative] = {"git_blob_oid": oid, "sha256": hashlib.sha256(completed.stdout).hexdigest(), "size_bytes": len(completed.stdout)}
        require((source / relative).read_bytes() == completed.stdout, f"working/source commit bytes differ: {relative}")
    require(set(committed) == {path.relative_to(source).as_posix() for path in source.rglob("*") if path.is_file()}, "R2 committed analysis denominator differs")
    require(len(committed) == inventory["file_count"], "R2 committed analysis count differs")
    return {"commit": commit, "parent_runner_commit": runner, "analysis_tree_oid": tree_oid, "changed_path_count": len(changed), "changed_status": changed_status, "files": committed}


def independent_point_metrics(path: Path) -> dict:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    series = {}
    for name in ("ks_nl", "ks_l", "of_l"):
        selected = sorted((row for row in rows if row["material"] == "al" and row["series"] == name), key=lambda row: float(row["volume_ratio"]))
        require(len(selected) == 7, f"independent points denominator differs: {name}")
        fit = fit_bm3([float(row["volume_angstrom3_per_atom"]) for row in selected], [float(row["energy_ev_per_atom"]) for row in selected])
        reference = next(float(row["energy_ev_per_atom"]) for row in selected if abs(float(row["volume_ratio"]) - 1.0) < 1e-12)
        anchored = {float(row["volume_ratio"]): (float(row["energy_ev_per_atom"]) - reference) * 1000.0 for row in selected}
        series[name] = {"fit": fit, "anchored_mev_per_atom": anchored}
    nl, kl = series["ks_nl"], series["ks_l"]
    delta_v0 = abs(kl["fit"]["v0_angstrom3_per_atom"] - nl["fit"]["v0_angstrom3_per_atom"]) / nl["fit"]["v0_angstrom3_per_atom"] * 100.0
    delta_b0 = abs(kl["fit"]["b0_gpa"] - nl["fit"]["b0_gpa"]) / nl["fit"]["b0_gpa"] * 100.0
    anchored_max = max(abs(kl["anchored_mev_per_atom"][ratio] - nl["anchored_mev_per_atom"][ratio]) for ratio in nl["anchored_mev_per_atom"])
    return {"series": series, "equilibrium_volume_difference_percent": delta_v0, "bulk_modulus_difference_percent": delta_b0, "anchored_curve_max_abs_difference_mev_per_atom": anchored_max}


def rejection_signatures(summary: dict, r2_config: dict) -> list[str]:
    signatures: list[str] = []
    al = summary["al_three_layer_eos"]
    comparison = al["ks_l_vs_ks_nl"]
    if summary["p0_recovery"]["al_hard"]["status"] != "accepted":
        signatures.append("al_p0_recovery")
    for name, fit in al["fits"].items():
        if fit["status"] != "accepted":
            signatures.append(f"al_{name}_bm3_fit")
    if comparison["equilibrium_volume_difference_percent"] > float(r2_config["acceptance"]["al_ksl_vs_ksnl_equilibrium_volume_difference_percent_max"]):
        signatures.append("al_ks_l_vs_ks_nl_equilibrium_volume_difference_percent")
    if comparison["bulk_modulus_difference_percent"] > float(r2_config["acceptance"]["al_ksl_vs_ksnl_bulk_modulus_difference_percent_max"]):
        signatures.append("al_ks_l_vs_ks_nl_bulk_modulus_difference_percent")
    return signatures


def inventory_from_entries(entries: list[dict]) -> dict:
    normalized = sorted(entries, key=lambda row: row["path"])
    require(len({row["path"] for row in normalized}) == len(normalized), "inventory paths duplicate")
    lines = "".join(f"{row['sha256']}  {row['path']}\n" for row in normalized).encode("utf-8")
    return {
        "file_count": len(normalized),
        "regular_file_bytes": sum(int(row["size_bytes"]) for row in normalized),
        "sha256sum_list_digest": hashlib.sha256(lines).hexdigest(),
        "files": normalized,
    }


def validate_capture_invocation(
    source: Path,
    config: dict,
    inventory: dict,
    dependencies: list[dict],
    r2_config: dict,
) -> dict:
    source_spec = config["source_r2"]
    capture_spec = config["capture"]
    invocation_path = source / "orchestration/analysis_invocation.json"
    invocation = read_json(invocation_path)
    require(isinstance(invocation, dict), "R2 analysis invocation must be an object")
    require(set(invocation) == INVOCATION_KEYS, "R2 analysis invocation key denominator differs")
    require(invocation.get("schema_version") == 2, "R2 analysis invocation schema differs")
    require(invocation.get("protocol_revision") == config["protocol_revision"], "R2 analysis invocation protocol differs")
    require(invocation.get("status") == "captured_expected_scientific_rejection", "R2 analysis invocation status differs")
    expected_argv = [capture_spec["python"], *capture_spec["python_args"], capture_spec["analyzer_path"], "--project-root", ".", "--collect"]
    require(invocation.get("argv") == expected_argv, "R2 analysis invocation argv differs")
    require(invocation.get("cwd") == source_spec["r2_worktree"] == capture_spec["r2_worktree"], "R2 analysis invocation cwd differs")
    require(invocation.get("capture_cwd") == source_spec["capture_worktree"], "capture invocation cwd differs")
    require(invocation.get("r2_runner_commit") == source_spec["runner_commit"] == capture_spec["r2_runner_commit"], "R2 analysis invocation runner differs")
    require(invocation.get("capture_implementation_commit") == source_spec["capture_implementation_commit"] == capture_spec["implementation_commit"], "capture implementation commit differs")
    require(invocation.get("capture_preregistered_commit") == source_spec["capture_preregistered_commit"], "capture prereg commit differs")
    require(invocation.get("capture_config_path") == CONFIG_PATH.as_posix(), "capture config path differs")
    require(invocation.get("capture_config_sha256") == source_spec["capture_config_sha256"], "capture config SHA differs")
    require(invocation.get("registration_changed_paths") == [CONFIG_PATH.as_posix()], "capture registration diff differs")
    capture_script = invocation.get("capture_script")
    require(isinstance(capture_script, dict), "capture script identity missing")
    require(capture_script.get("path") == capture_spec["capture_script_path"], "capture script path differs")
    require(capture_script.get("sha256") == source_spec["capture_script_sha256"] == capture_spec["capture_script_sha256"], "capture script SHA differs")
    require(isinstance(capture_script.get("git_blob_oid"), str) and len(capture_script["git_blob_oid"]) == 40, "capture script blob missing")
    require(int(capture_script.get("size_bytes", -1)) > 0, "capture script size missing")

    require(invocation.get("r2_dependencies") == dependencies, "capture/R3 dependency blob inventory differs")
    dependency_by_path = {row["path"]: row for row in dependencies}
    require(dependency_by_path[capture_spec["analyzer_path"]]["sha256"] == capture_spec["analyzer_sha256"], "registered analyzer SHA cross-binding differs")
    require(dependency_by_path[capture_spec["r2_config_path"]]["sha256"] == capture_spec["r2_config_sha256"], "registered R2 config SHA cross-binding differs")
    require(invocation.get("analyzer") == dependency_by_path[capture_spec["analyzer_path"]], "capture analyzer identity differs")
    require(invocation.get("r2_config") == dependency_by_path[capture_spec["r2_config_path"]], "capture R2 config identity differs")

    terminal_path = source / "orchestration/terminal.json"
    terminal_identity = invocation.get("terminal")
    require(isinstance(terminal_identity, dict), "capture terminal identity missing")
    expected_terminal_path = str(Path(r2_config["external_state_root"]) / "terminal.json")
    require(terminal_identity.get("path") == expected_terminal_path, "capture external terminal path differs")
    require(terminal_identity.get("sha256") == source_spec["terminal_sha256"] == sha256_file(terminal_path), "capture terminal SHA differs")
    require(int(terminal_identity.get("size_bytes", -1)) == terminal_path.stat().st_size, "capture terminal size differs")

    require(invocation.get("exit_code") == int(source_spec["analysis_expected_exit_code"]) == 2, "R2 analyzer exit semantics differ")
    require(invocation.get("expected_summary_status") == config["expected_rejection"]["r2_summary_status"] == "rejected", "capture summary disposition differs")
    require(invocation.get("summary_sha256") == source_spec["summary_sha256"] == sha256_file(source / "summary.json"), "capture summary SHA differs")
    streams = {
        "stdout": (source / CAPTURE_ARTIFACTS[0], source_spec["analysis_stdout_sha256"]),
        "stderr": (source / CAPTURE_ARTIFACTS[1], source_spec["analysis_stderr_sha256"]),
    }
    for name, (path, expected_sha) in streams.items():
        require(invocation.get(f"{name}_sha256") == expected_sha == sha256_file(path), f"capture {name} SHA differs")
        require(int(invocation.get(f"{name}_size_bytes", -1)) == path.stat().st_size, f"capture {name} size differs")

    environment = invocation.get("subprocess_environment")
    require(isinstance(environment, dict), "capture environment missing")
    expected_static = {str(key): str(value) for key, value in capture_spec["minimal_environment"].items()}
    for key, expected in expected_static.items():
        require(environment.get(key) == expected, f"capture environment differs: {key}")
    require(environment.get("PYTHONPATH") == str(Path(source_spec["r2_worktree"]) / "scripts"), "capture PYTHONPATH differs")
    temporary = environment.get("TMPDIR")
    require(isinstance(temporary, str) and Path(temporary).name.startswith("g1_three_layer_r2_capture_"), "capture TMPDIR differs")
    require(environment.get("PYTHONPYCACHEPREFIX") == str(Path(temporary) / "pycache"), "capture pycache isolation differs")
    require(set(environment) == set(expected_static).union({"PYTHONPATH", "TMPDIR", "PYTHONPYCACHEPREFIX"}), "capture environment has unregistered keys")

    require(invocation.get("capture_added_artifacts") == list(CAPTURE_ARTIFACTS), "capture artifact list differs")
    final_by_path = {row["path"]: row for row in inventory["files"]}
    require(set(CAPTURE_ARTIFACTS).issubset(final_by_path), "capture artifact missing from source tree")
    before_rows = [row for row in inventory["files"] if row["path"] not in CAPTURE_ARTIFACTS]
    before = inventory_from_entries(before_rows)
    require(invocation.get("analyzer_output_inventory_before_capture_artifacts") == before, "capture pre-artifact inventory differs")
    require(inventory["file_count"] == before["file_count"] + 3, "capture did not add exactly three artifacts")
    started_text = invocation.get("started_utc")
    finished_text = invocation.get("finished_utc")
    require(isinstance(started_text, str) and started_text.endswith("Z"), "capture start UTC schema differs")
    require(isinstance(finished_text, str) and finished_text.endswith("Z"), "capture finish UTC schema differs")
    try:
        started = datetime.fromisoformat(started_text.replace("Z", "+00:00"))
        finished = datetime.fromisoformat(finished_text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("capture UTC parse failed") from exc
    require(started.tzinfo == timezone.utc and finished.tzinfo == timezone.utc and finished > started, "capture UTC ordering differs")
    duration = float(invocation.get("duration_seconds", -1.0))
    require(duration > 0.0 and abs((finished - started).total_seconds() - duration) < 0.05, "capture duration/UTC binding differs")
    require(invocation.get("interpretation") == "exit 2 is the preregistered scientific gate rejection, not an execution failure", "capture interpretation differs")
    require(sha256_file(invocation_path) == source_spec["analysis_invocation_sha256"], "capture invocation SHA differs")
    return invocation


def replay(project_root: Path, config: dict, analysis_preregistered_commit: str | None = None) -> tuple[dict, bytes]:
    source = project_root / config["source_r2"]["analysis_root"]
    require(source.is_dir() and not source.is_symlink(), "committed R2 analysis missing")
    require_no_upf_body(source)
    inventory = source_inventory(source)
    require(inventory["file_count"] == int(config["source_r2"]["analysis_tree_file_count"]), "R2 analysis file count differs")
    require(inventory["regular_file_bytes"] == int(config["source_r2"]["analysis_tree_regular_file_bytes"]), "R2 analysis byte count differs")
    require(inventory["sha256sum_list_digest"] == config["source_r2"]["analysis_tree_sha256sum_list_digest"], "R2 analysis tree digest differs")
    commit_identity = verify_source_commit(project_root, config, source, inventory)
    dependencies = verify_r2_dependencies(project_root, config)
    identities = {"summary.json": "summary_sha256", "points.tsv": "points_sha256", "README.md": "readme_sha256", "orchestration/terminal.json": "terminal_sha256", "orchestration/analysis_invocation.json": "analysis_invocation_sha256", "orchestration/analysis.stdout": "analysis_stdout_sha256", "orchestration/analysis.stderr": "analysis_stderr_sha256"}
    for relative, key in identities.items():
        require(sha256_file(source / relative) == config["source_r2"][key], f"R2 source SHA differs: {relative}")
    terminal = read_json(source / "orchestration/terminal.json")
    require(isinstance(terminal, dict) and terminal.get("status") == "accepted", "R2 terminal rejected")
    require(terminal.get("runner_commit") == config["source_r2"]["runner_commit"], "R2 terminal runner differs")
    require(terminal.get("recovery_barrier_sha256") == config["source_r2"]["recovery_barrier_sha256"], "R2 terminal recovery differs")
    require(terminal.get("attempted_count") == terminal.get("accepted_count") == 8, "R2 terminal denominator differs")
    require(terminal.get("failed_count") == terminal.get("retried_count") == terminal.get("runner_return_code") == 0, "R2 terminal failure/retry/RC differs")
    r2_config = load_r2_config(project_root)
    invocation = validate_capture_invocation(source, config, inventory, dependencies, r2_config)
    capture_git_identity = verify_capture_registration_commits(project_root, config, invocation)
    for key, expected in config["expected_rejection"]["r2_acceptance_thresholds"].items():
        require(r2_config["acceptance"].get(key) == expected, f"R2/R3 threshold identity differs: {key}")
    require(float(config["expected_rejection"]["equilibrium_volume_difference_percent_max"]) == float(r2_config["acceptance"]["al_ksl_vs_ksnl_equilibrium_volume_difference_percent_max"]), "R3 delta V0 threshold differs from R2")
    require(float(config["expected_rejection"]["bulk_modulus_difference_percent_max"]) == float(r2_config["acceptance"]["al_ksl_vs_ksnl_bulk_modulus_difference_percent_max"]), "R3 delta B0 threshold differs from R2")
    rows = load_manifest(project_root)
    replayed, points, _ = build_final_analysis(project_root, r2_config, rows, (source / "raw").resolve())
    with tempfile.TemporaryDirectory(prefix="g1_three_layer_analysis_r3_replay_") as temporary:
        rendered = Path(temporary) / "r2"
        write_analysis(rendered, replayed, points)
        for name in ("summary.json", "points.tsv", "README.md"):
            require((source / name).read_bytes() == (rendered / name).read_bytes(), f"R2 byte replay differs: {name}")
    require(replayed["status"] == config["expected_rejection"]["r2_summary_status"] == "rejected", "R2 scientific disposition differs")
    signatures = rejection_signatures(replayed, r2_config)
    require(signatures == config["expected_rejection"]["hard_failure_signatures"], "scientific rejection signature differs")
    require(all(fit["status"] == "accepted" for fit in replayed["al_three_layer_eos"]["fits"].values()), "BM3 evidence invalid")
    require(replayed["p0_recovery"]["al_hard"]["status"] == "accepted", "Al P0 evidence invalid")
    require(replayed["mg_compatibility_diagnostic"]["affects_overall"] is False, "Mg affected R3 disposition")
    comparison = replayed["al_three_layer_eos"]["ks_l_vs_ks_nl"]
    independent = independent_point_metrics(source / "points.tsv")
    require(abs(independent["equilibrium_volume_difference_percent"] - comparison["equilibrium_volume_difference_percent"]) < 1e-10, "independent delta V0 differs")
    require(abs(independent["bulk_modulus_difference_percent"] - comparison["bulk_modulus_difference_percent"]) < 1e-10, "independent delta B0 differs")
    require(abs(independent["anchored_curve_max_abs_difference_mev_per_atom"] - comparison["anchored_curve_max_abs_difference_mev_per_atom"]) < 1e-8, "independent anchored curve differs")
    volume_limit = float(config["expected_rejection"]["equilibrium_volume_difference_percent_max"])
    prereg_commit = analysis_preregistered_commit or git(project_root, "rev-parse", "HEAD")
    output = {
        "schema_version": 1,
        "protocol_revision": config["protocol_revision"],
        "status": config["expected_rejection"]["r3_disposition_status"],
        "validator_status": "accepted",
        "execution_evidence_status": "accepted",
        "r2_execution_terminal_status": "accepted",
        "evidence_valid": True,
        "scientific_gate_status": "rejected",
        "new_solver_run_count": 0,
        "analysis_implementation_commit": config["registration"]["analysis_implementation_commit"],
        "analysis_preregistered_commit": prereg_commit,
        "analysis_preregistered_config_sha256": sha256_file(project_root / CONFIG_PATH),
        "source_r2_evidence_commit": config["source_r2"]["evidence_commit"],
        "source_r2_summary_sha256": config["source_r2"]["summary_sha256"],
        "source_r2_terminal_sha256": config["source_r2"]["terminal_sha256"],
        "source_r2_analysis_inventory": inventory,
        "source_r2_git_identity": {key: value for key, value in commit_identity.items() if key != "files"},
        "capture_git_identity": capture_git_identity,
        "r2_replay_dependency_count": len(dependencies),
        "hard_failure_signatures": signatures,
        "al_ks_l_vs_ks_nl": {
            "equilibrium_volume_difference_percent": comparison["equilibrium_volume_difference_percent"],
            "equilibrium_volume_difference_percent_max": volume_limit,
            "equilibrium_volume_excess_percentage_points": comparison["equilibrium_volume_difference_percent"] - volume_limit,
            "equilibrium_volume_threshold_ratio": comparison["equilibrium_volume_difference_percent"] / volume_limit,
            "bulk_modulus_difference_percent": comparison["bulk_modulus_difference_percent"],
            "bulk_modulus_difference_percent_max": float(config["expected_rejection"]["bulk_modulus_difference_percent_max"]),
            "ks_nl_fit": replayed["al_three_layer_eos"]["fits"]["ks_nl"],
            "ks_l_fit": replayed["al_three_layer_eos"]["fits"]["ks_l"],
        },
        "mg_affects_overall": False,
        "g1": config["g1_disposition"],
        "scope_limits": config["scope_limits"],
        "interpretation": "valid committed evidence; preregistered scientific hard gate rejected without threshold change",
    }
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=("gate", "value", "threshold", "status"), delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerow({"gate": "al_ks_l_vs_ks_nl_delta_v0_percent", "value": comparison["equilibrium_volume_difference_percent"], "threshold": config["expected_rejection"]["equilibrium_volume_difference_percent_max"], "status": "rejected"})
    writer.writerow({"gate": "al_ks_l_vs_ks_nl_delta_b0_percent", "value": comparison["bulk_modulus_difference_percent"], "threshold": config["expected_rejection"]["bulk_modulus_difference_percent_max"], "status": "accepted"})
    return output, buffer.getvalue().encode("utf-8")


def readme(summary: dict) -> bytes:
    gate = summary["al_ks_l_vs_ks_nl"]
    lines = [
        "# S1/G1 三层验证 analysis-only R3", "",
        f"处置：`{summary['status']}`。R2 raw/terminal 已完整逐字 replay，证据有效；科学 hard gate 拒绝。", "",
        f"Al KS-L↔KS-NL |ΔV0|={gate['equilibrium_volume_difference_percent']:.9f}% > {gate['equilibrium_volume_difference_percent_max']:.6f}%（rejected）；|ΔB0|={gate['bulk_modulus_difference_percent']:.9f}% <= {gate['bulk_modulus_difference_percent_max']:.6f}%（accepted）。", "",
        "未改变阈值。Mg 仅为 diagnostic，不影响处置。独立第二 KS/QE、端点复查、应变/相门与 G1 6/6 均未关闭。", "",
    ]
    return "\n".join(lines).encode("utf-8")


def write_output(root: Path, summary: dict, gates: bytes) -> None:
    root.mkdir(parents=True, exist_ok=True)
    atomic_write(root / "summary.json", canonical_json_bytes(summary))
    atomic_write(root / "gate_metrics.tsv", gates)
    atomic_write(root / "README.md", readme(summary))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args()
    require_isolated_python()
    project_root = find_project_root(args.project_root)
    config = load_config(project_root)
    head = require_clean_tree(project_root)
    require_tracked_matches_head(project_root, REGISTERED_CODE)
    topology = validate_preregistered_topology(project_root, config, head)
    summary, gates = replay(project_root, config, topology["analysis_preregistered_commit"])
    output = args.output_root or project_root / config["output_root"]
    require(output.resolve() == (project_root / config["output_root"]).resolve(), "R3 output root differs from registration")
    require(not output.exists(), "R3 output already exists")
    write_output(output, summary, gates)
    require(sorted(path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()) == list(FINAL_OUTPUT_FILES), "R3 output denominator differs")
    require_no_upf_body(output)
    print(json.dumps({"status": summary["status"], "output_root": str(output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
