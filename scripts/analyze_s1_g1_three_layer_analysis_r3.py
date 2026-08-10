#!/usr/bin/env python3
"""Replay committed R2 evidence and classify a valid scientific rejection."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import subprocess
import tempfile
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


def load_config(project_root: Path) -> dict:
    payload = read_json(project_root / CONFIG_PATH)
    require(isinstance(payload, dict), "R3 config must be object")
    require(payload.get("protocol_revision") == "S1-G1-THREE-LAYER-ANALYSIS-20260810-R3", "R3 protocol differs")
    return payload


def source_inventory(root: Path) -> dict:
    files = sorted(path for path in root.rglob("*") if path.is_file())
    require(files and not any(path.is_symlink() for path in root.rglob("*")), "unsafe R2 analysis tree")
    lines = "".join(f"{sha256_file(path)}  {path.relative_to(root).as_posix()}\n" for path in files).encode("utf-8")
    return {"file_count": len(files), "regular_file_bytes": sum(path.stat().st_size for path in files), "sha256sum_list_digest": hashlib.sha256(lines).hexdigest()}


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
    changed = git(project_root, "diff", "--name-only", f"{runner}..{commit}").splitlines()
    require(changed and all(path.startswith(prefix) for path in changed), "R2 evidence commit changed paths outside analysis")
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
    return {"commit": commit, "parent_runner_commit": runner, "analysis_tree_oid": tree_oid, "changed_path_count": len(changed), "files": committed}


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


def replay(project_root: Path, config: dict) -> tuple[dict, bytes]:
    source = project_root / config["source_r2"]["analysis_root"]
    require(source.is_dir() and not source.is_symlink(), "committed R2 analysis missing")
    inventory = source_inventory(source)
    require(inventory["file_count"] == int(config["source_r2"]["analysis_tree_file_count"]), "R2 analysis file count differs")
    require(inventory["regular_file_bytes"] == int(config["source_r2"]["analysis_tree_regular_file_bytes"]), "R2 analysis byte count differs")
    require(inventory["sha256sum_list_digest"] == config["source_r2"]["analysis_tree_sha256sum_list_digest"], "R2 analysis tree digest differs")
    commit_identity = verify_source_commit(project_root, config, source, inventory)
    identities = {"summary.json": "summary_sha256", "points.tsv": "points_sha256", "README.md": "readme_sha256", "orchestration/terminal.json": "terminal_sha256", "orchestration/analysis_invocation.json": "analysis_invocation_sha256", "orchestration/analysis.stdout": "analysis_stdout_sha256", "orchestration/analysis.stderr": "analysis_stderr_sha256"}
    for relative, key in identities.items():
        require(sha256_file(source / relative) == config["source_r2"][key], f"R2 source SHA differs: {relative}")
    invocation = read_json(source / "orchestration/analysis_invocation.json")
    require(isinstance(invocation, dict) and invocation.get("exit_code") == int(config["source_r2"]["analysis_expected_exit_code"]) == 2, "R2 analyzer exit semantics differ")
    require(invocation.get("head") == config["source_r2"]["runner_commit"], "R2 analysis invocation HEAD differs")
    require(invocation.get("stdout_sha256") == config["source_r2"]["analysis_stdout_sha256"] and invocation.get("stderr_sha256") == config["source_r2"]["analysis_stderr_sha256"], "R2 invocation stream binding differs")
    terminal = read_json(source / "orchestration/terminal.json")
    require(isinstance(terminal, dict) and terminal.get("status") == "accepted", "R2 terminal rejected")
    require(terminal.get("runner_commit") == config["source_r2"]["runner_commit"], "R2 terminal runner differs")
    require(terminal.get("recovery_barrier_sha256") == config["source_r2"]["recovery_barrier_sha256"], "R2 terminal recovery differs")
    require(terminal.get("attempted_count") == terminal.get("accepted_count") == 8, "R2 terminal denominator differs")
    require(terminal.get("failed_count") == terminal.get("retried_count") == terminal.get("runner_return_code") == 0, "R2 terminal failure/retry/RC differs")
    r2_config = load_r2_config(project_root)
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
        "source_r2_evidence_commit": config["source_r2"]["evidence_commit"],
        "source_r2_summary_sha256": config["source_r2"]["summary_sha256"],
        "source_r2_terminal_sha256": config["source_r2"]["terminal_sha256"],
        "source_r2_analysis_inventory": inventory,
        "source_r2_git_identity": {key: value for key, value in commit_identity.items() if key != "files"},
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
    project_root = find_project_root(args.project_root)
    config = load_config(project_root)
    require_clean_tree(project_root)
    require_tracked_matches_head(project_root, REGISTERED_CODE)
    git_ancestor(project_root, config["source_r2"]["evidence_commit"])
    summary, gates = replay(project_root, config)
    output = args.output_root or project_root / config["output_root"]
    require(not output.exists(), "R3 output already exists")
    write_output(output, summary, gates)
    print(json.dumps({"status": summary["status"], "output_root": str(output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
