#!/usr/bin/env python3
"""Fail-closed validator for the S1-G1 displacement/strain reference set."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import subprocess
from pathlib import Path


PROTOCOL = "S1-G1-DISPLACEMENT-STRAIN-REFERENCE-R1"
CONFIG_REL = Path("config/S1_g1_displacement_strain_reference_r1.json")
MANIFEST_REL = Path("config/S1_g1_displacement_strain_reference_r1_manifest.tsv")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-committed", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / CONFIG_REL).read_text(encoding="utf-8"))
    require(config.get("protocol_revision") == PROTOCOL, "config protocol differs")
    with (root / MANIFEST_REL).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    ids = [row["experiment_id"] for row in rows]
    require(len(rows) == len(set(ids)) == 15, "manifest IDs are not exactly 15 unique values")
    require(ids == config["run_ids_exact"], "manifest order differs from config")
    state = Path(config["state_root"])
    launch = json.loads((state / "launch.json").read_text(encoding="utf-8"))
    terminal = json.loads((state / "terminal.json").read_text(encoding="utf-8"))
    require(launch.get("protocol_revision") == PROTOCOL, "launch protocol differs")
    require(terminal.get("status") == "accepted", "terminal status is not accepted")
    require(terminal.get("runner_return_code") == 0, "runner return code differs")
    require(terminal.get("accepted_run_count") == 15, "terminal accepted count differs")
    require(terminal.get("accepted_ids") == ids, "terminal accepted IDs differ")
    require(sha256(root / CONFIG_REL) == launch["config_sha256"], "config hash differs from launch")
    require(sha256(root / MANIFEST_REL) == launch["manifest_sha256"], "manifest hash differs from launch")
    require(len(list((state / "attempts").glob("*.json"))) == 15, "attempt marker count differs")
    require(len(list((state / "completions").glob("*.json"))) == 15, "completion marker count differs")

    analysis = root / config["analysis_root"]
    summary = json.loads((analysis / "summary.json").read_text(encoding="utf-8"))
    require(summary.get("protocol_revision") == PROTOCOL, "summary protocol differs")
    require(summary.get("status") == "accepted", "summary is not accepted")
    require(summary.get("accepted_run_count") == 15, "summary run count differs")
    require(summary.get("accepted_pair_count") == 7, "summary pair count differs")
    require(summary.get("diagnostic_pair_count") == 7, "diagnostic pair count differs")
    require(summary.get("failed_ids") == [], "summary contains failed IDs")
    require(summary.get("hostname_set") == ["node01"], "formal hostname differs")
    require(summary.get("rank_logical_cpus_exact") == [40, 41, 42, 43], "formal CPU set differs")
    require(summary.get("g4_acceptance_claim") is False, "analysis improperly claims G4 acceptance")
    require(
        float(summary["maximum_electron_relative_error"])
        < float(config["acceptance"]["electron_number_relative_error_strictly_less_than"]),
        "electron-number aggregate gate failed",
    )
    require(
        float(summary["maximum_cube_geometry_error_bohr"])
        < float(config["acceptance"]["cube_geometry_absolute_tolerance_bohr"]),
        "cube-geometry aggregate gate failed",
    )
    require(
        float(summary["maximum_identity_residual_ev_per_atom"])
        < float(config["acceptance"]["thermodynamic_identity_residual_ev_per_atom_strictly_less_than"]),
        "thermodynamic identity aggregate gate failed",
    )
    require(
        int(summary["minimum_cube_mantissa_digits"])
        >= int(config["acceptance"]["cube_output_precision_exact"]),
        "cube precision aggregate gate failed",
    )
    require(summary.get("formal_git_head") == launch["git_head"], "formal Git head differs")

    with (analysis / "points.tsv").open(encoding="utf-8", newline="") as handle:
        points = list(csv.DictReader(handle, delimiter="\t"))
    with (analysis / "pairs.tsv").open(encoding="utf-8", newline="") as handle:
        pairs = list(csv.DictReader(handle, delimiter="\t"))
    require(len(points) == 15 and [row["experiment_id"] for row in points] == ids, "points table differs")
    require(len(pairs) == 7, "pairs table does not contain seven rows")
    require(all(row["status"] == "diagnostic_only_not_G4_gate" for row in pairs), "pair status differs")
    require(len({row["pair_id"] for row in pairs}) == 7, "pair IDs are not unique")

    for row in rows:
        experiment_id = row["experiment_id"]
        raw = analysis / "raw" / experiment_id
        result = json.loads((raw / "analysis_result.json").read_text(encoding="utf-8"))
        require(result.get("status") == "accepted", f"point status differs: {experiment_id}")
        require(result.get("zero_temperature_exact_claim") is False, f"zero-T claim differs: {experiment_id}")
        require(result.get("rank_affinity_verified") is True, f"rank affinity differs: {experiment_id}")
        require(math.isfinite(float(result["elapsed_seconds"])), f"elapsed time nonfinite: {experiment_id}")
        labels = result["thermodynamic"]["energy_labels_ev_per_cell"]
        require(set(labels) == set(config["acceptance"]["thermodynamic_labels_exact"]), f"labels differ: {experiment_id}")
        require(all(math.isfinite(float(value)) for value in labels.values()), f"labels nonfinite: {experiment_id}")
        require(len(result["forces"]) == int(row["atom_count"]), f"force count differs: {experiment_id}")
        require(len(result["stress_kbar"]) == 3 and all(len(v) == 3 for v in result["stress_kbar"]), f"stress differs: {experiment_id}")
        evidence = raw / "run_evidence"
        state_run = state / "runs" / experiment_id
        suffix = row["suffix"]
        comparisons = (
            (evidence / "INPUT", state_run / "INPUT"),
            (evidence / "STRU", state_run / "STRU"),
            (evidence / f"OUT.{suffix}" / "running_scf.log", state_run / f"OUT.{suffix}" / "running_scf.log"),
            (evidence / f"OUT.{suffix}" / "chg.cube", state_run / f"OUT.{suffix}" / "chg.cube"),
            (evidence / f"OUT.{suffix}" / "pot.cube", state_run / f"OUT.{suffix}" / "pot.cube"),
            (evidence / "attempt_marker.json", state / "attempts" / f"{experiment_id}.json"),
            (evidence / "completion_marker.json", state / "completions" / f"{experiment_id}.json"),
        )
        for frozen, source in comparisons:
            require(frozen.is_file() and not frozen.is_symlink(), f"missing frozen evidence: {frozen}")
            require(source.is_file() and sha256(frozen) == sha256(source), f"frozen/source evidence differs: {frozen}")
        for rank in range(4):
            affinity = (evidence / "rank_affinity" / f"rank-{rank}.txt").read_text(encoding="utf-8")
            require(f"requested_cpu={40 + rank}\n" in affinity, f"requested CPU differs: {experiment_id}/{rank}")
            require(f"affinity_after={40 + rank}\n" in affinity, f"actual CPU differs: {experiment_id}/{rank}")

    if args.require_committed:
        require(not git(root, "status", "--porcelain").stdout.strip(), "worktree is dirty")
        ancestor = git(root, "merge-base", "--is-ancestor", launch["git_head"], "HEAD", check=False)
        require(ancestor.returncode == 0, "formal preregistration commit is not an ancestor of HEAD")
        files = [path for path in analysis.rglob("*") if path.is_file()]
        require(files, "analysis contains no files")
        for path in files:
            relative = str(path.relative_to(root))
            tracked = git(root, "ls-files", "--error-unmatch", "--", relative, check=False)
            require(tracked.returncode == 0, f"analysis evidence is not committed: {relative}")
            require(not path.is_symlink(), f"analysis evidence is symbolic: {relative}")
    print(
        "accepted: 15/15 displacement/strain references, 7/7 diagnostic pairs, "
        "complete Mermin labels/forces/stress/cubes/Ngrid; no G4 claim"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
