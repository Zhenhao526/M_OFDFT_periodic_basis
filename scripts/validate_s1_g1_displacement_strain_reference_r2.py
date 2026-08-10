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


PROTOCOL = "S1-G1-DISPLACEMENT-STRAIN-REFERENCE-ANALYSIS-R2"
SOURCE_EXECUTION_PROTOCOL = "S1-G1-DISPLACEMENT-STRAIN-REFERENCE-R1"
CONFIG_REL = Path("config/S1_g1_displacement_strain_reference_analysis_r2.json")
SOURCE_CONFIG_REL = Path("config/S1_g1_displacement_strain_reference_r1.json")
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
    require(config.get("status") == "preregistered_analysis_only", "analysis config status differs")
    with (root / MANIFEST_REL).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    ids = [row["experiment_id"] for row in rows]
    require(len(rows) == len(set(ids)) == 15, "manifest IDs are not exactly 15 unique values")
    require(ids == config["run_ids_exact"], "manifest order differs from config")
    source_registration = config["source_execution"]
    require(sha256(root / SOURCE_CONFIG_REL) == source_registration["source_config_sha256"], "registered source config differs")
    require(sha256(root / MANIFEST_REL) == source_registration["source_manifest_sha256"], "registered source manifest differs")
    state = Path(config["state_root"])
    launch = json.loads((state / "launch.json").read_text(encoding="utf-8"))
    terminal = json.loads((state / "terminal.json").read_text(encoding="utf-8"))
    require(launch.get("protocol_revision") == SOURCE_EXECUTION_PROTOCOL, "source launch protocol differs")
    require(launch.get("git_head") == source_registration["formal_git_head"], "source formal Git head differs")
    require(terminal.get("status") == "accepted", "terminal status is not accepted")
    require(terminal.get("runner_return_code") == 0, "runner return code differs")
    require(terminal.get("accepted_run_count") == 15, "terminal accepted count differs")
    require(terminal.get("accepted_ids") == ids, "terminal accepted IDs differ")
    require(sha256(root / SOURCE_CONFIG_REL) == launch["config_sha256"], "source execution config hash differs from launch")
    require(sha256(root / MANIFEST_REL) == launch["manifest_sha256"], "manifest hash differs from launch")
    require(len(list((state / "attempts").glob("*.json"))) == 15, "attempt marker count differs")
    require(len(list((state / "completions").glob("*.json"))) == 15, "completion marker count differs")

    analysis = root / config["analysis_root"]
    r1_rejection = json.loads(
        (root / "analysis/s1/g1_displacement_strain_reference_r1_20260810/summary.json").read_text(encoding="utf-8")
    )
    require(r1_rejection.get("status") == "rejected_analysis_false_negative", "R1 analysis rejection closure differs")
    require(r1_rejection.get("solver_accepted_run_count") == 15, "R1 solver terminal closure differs")
    require(r1_rejection.get("accepted_scientific_run_count") == 0, "R1 analysis improperly contributes accepted points")
    require(r1_rejection.get("solver_ids_may_be_retried") is False, "R1 no-retry closure differs")
    summary = json.loads((analysis / "summary.json").read_text(encoding="utf-8"))
    require(summary.get("protocol_revision") == PROTOCOL, "summary protocol differs")
    require(summary.get("status") == "accepted", "summary is not accepted")
    require(summary.get("accepted_run_count") == 15, "summary run count differs")
    require(summary.get("accepted_pair_count") == 7, "summary pair count differs")
    require(summary.get("diagnostic_pair_count") == 7, "diagnostic pair count differs")
    require(summary.get("source_execution_protocol") == SOURCE_EXECUTION_PROTOCOL, "source execution protocol differs")
    require(summary.get("source_execution_reused_without_new_solver_ids") is True, "analysis-only reuse claim differs")
    require(int(summary.get("pbc_wrapped_cube_atom_rows", 0)) >= 2, "PBC-wrapped atom evidence is missing")
    require("false-negative" in summary.get("pbc_minimum_image_correction", ""), "R1 false-negative correction is missing")
    require(summary.get("failed_ids") == [], "summary contains failed IDs")
    require(summary.get("hostname_set") == ["node01"], "formal hostname differs")
    require(summary.get("rank_logical_cpus_exact") == [40, 41, 42, 43], "formal CPU set differs")
    runtime_identity = summary.get("runtime_identity", {})
    require(
        runtime_identity.get("audit_abacus_sha256")
        == "2d68a57c7b25608b3550854dabc2e63601eeca956bf185ad7d0967052bdbb4ba",
        "audit ABACUS identity differs",
    )
    require(
        runtime_identity.get("r4_parent_relocated_abacus_sha256")
        == "438c74b9ada4c8df15ffbb66da6755907dfd2a3812ecf868fafd4d7dc4db62e1"
        and runtime_identity.get("byte_identical_to_r4_runtime") is False,
        "R4 runtime-identity distinction is missing",
    )
    require(
        runtime_identity.get("equivalence_evidence_points") == 6
        and runtime_identity.get("equivalence_tier_exact") == "storage_exact",
        "six-point runtime-equivalence closure is not registered",
    )
    require(summary.get("g4_acceptance_claim") is False, "analysis improperly claims G4 acceptance")
    require("generated_utc" not in summary, "scientific summary contains a nondeterministic current time")
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
    physical_geometry = summary.get("independent_physical_geometry", {})
    require(physical_geometry.get("accepted_point_count") == 15, "independent geometry count differs")
    require(
        float(physical_geometry.get("maximum_displacement_error_angstrom", math.inf)) < 1.0e-9,
        "independent Cartesian displacement gate failed",
    )
    require(
        float(physical_geometry.get("maximum_strain_matrix_error", math.inf)) < 1.0e-12,
        "independent deformation-matrix gate failed",
    )
    require(
        "LATTICE_CONSTANT*bohr_to_angstrom" in physical_geometry.get("method", ""),
        "independent physical-unit reconstruction is not registered",
    )
    require(
        float(summary.get("maximum_stress_symmetry_error_kbar", math.inf))
        < float(config["acceptance"]["stress_symmetry_absolute_error_kbar_strictly_less_than"]),
        "stress symmetry aggregate gate failed",
    )
    require(
        float(summary.get("maximum_stress_pressure_trace_error_kbar", math.inf))
        < float(config["acceptance"]["stress_pressure_trace_absolute_error_kbar_strictly_less_than"]),
        "stress/pressure aggregate gate failed",
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
        require(
            result.get("independent_physical_geometry", {}).get("accepted") is True,
            f"independent physical geometry differs: {experiment_id}",
        )
        cube_pbc = result.get("cube_geometry_pbc_audit", {})
        for quantity in ("density", "potential"):
            audit = cube_pbc.get(quantity, {})
            require(
                "subtract nearest integer" in audit.get("atom_comparison", ""),
                f"PBC minimum-image method differs: {experiment_id}/{quantity}",
            )
            require(
                float(audit.get("maximum_accepted_geometry_error_bohr", math.inf))
                < float(config["acceptance"]["cube_geometry_absolute_tolerance_bohr"]),
                f"PBC cube geometry gate failed: {experiment_id}/{quantity}",
            )
        require(math.isfinite(float(result["elapsed_seconds"])), f"elapsed time nonfinite: {experiment_id}")
        labels = result["thermodynamic"]["energy_labels_ev_per_cell"]
        require(set(labels) == set(config["acceptance"]["thermodynamic_labels_exact"]), f"labels differ: {experiment_id}")
        require(all(math.isfinite(float(value)) for value in labels.values()), f"labels nonfinite: {experiment_id}")
        require(len(result["forces"]) == int(row["atom_count"]), f"force count differs: {experiment_id}")
        require(len(result["stress_kbar"]) == 3 and all(len(v) == 3 for v in result["stress_kbar"]), f"stress differs: {experiment_id}")
        integrity = result.get("independent_input_integrity", {})
        require(integrity.get("atom_count") == int(row["atom_count"]), f"independent atom count differs: {experiment_id}")
        require(integrity.get("atom_order") == [item["atom"] for item in result["forces"]], f"force/STRU atom order differs: {experiment_id}")
        require(integrity.get("kmesh") == [int(value) for value in row["kmesh"].split("x")], f"independent KPT differs: {experiment_id}")
        require(integrity.get("pseudopotential_sha256") == row["pseudopotential_sha256"], f"independent pseudo differs: {experiment_id}")
        require(integrity.get("density_potential_geometry_identical") is True, f"density/potential geometry differs: {experiment_id}")
        require(integrity.get("cube_atom_order_and_identity_verified") is True, f"cube atom identity/order differs: {experiment_id}")
        stress_validation = result.get("stress_validation", {})
        require(
            float(stress_validation.get("symmetry_max_abs_error_kbar", math.inf))
            < float(config["acceptance"]["stress_symmetry_absolute_error_kbar_strictly_less_than"]),
            f"stress symmetry differs: {experiment_id}",
        )
        require(
            float(stress_validation.get("trace_over_three_minus_pressure_abs_kbar", math.inf))
            < float(config["acceptance"]["stress_pressure_trace_absolute_error_kbar_strictly_less_than"]),
            f"stress/pressure differs: {experiment_id}",
        )
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

    with (analysis / "state_sha256.tsv").open(encoding="utf-8", newline="") as handle:
        state_records = list(csv.DictReader(handle, delimiter="\t"))
    require(len(state_records) == int(summary["state_sha256_record_count"]), "state SHA manifest count differs")
    require(len(state_records) == 300, "state SHA manifest must contain exactly 20 files per run")
    require(
        len({(row["source_relative_to_state"], row["frozen_relative_to_analysis"]) for row in state_records})
        == len(state_records),
        "state SHA manifest contains duplicate mappings",
    )
    for record in state_records:
        source = state / record["source_relative_to_state"]
        frozen = analysis / record["frozen_relative_to_analysis"]
        require(source.is_file() and not source.is_symlink(), f"state manifest source differs: {source}")
        require(frozen.is_file() and not frozen.is_symlink(), f"state manifest frozen path differs: {frozen}")
        require(source.stat().st_size == frozen.stat().st_size == int(record["size_bytes"]), f"state manifest size differs: {source}")
        require(sha256(source) == sha256(frozen) == record["sha256"], f"state manifest SHA differs: {source}")

    closure = summary.get("deterministic_analysis", {})
    require(closure.get("wall_clock_time_in_scientific_summary") is False, "deterministic time closure differs")
    require(closure.get("state_manifest") == "state_sha256.tsv", "deterministic state manifest differs")
    require(closure.get("analyzer_sha256") == sha256(root / "scripts/analyze_s1_g1_displacement_strain_reference_r2.py"), "analyzer implementation differs")
    require(closure.get("validator_sha256") == sha256(Path(__file__).resolve()), "validator implementation differs")
    require(closure.get("analysis_config_sha256") == sha256(root / CONFIG_REL), "analysis config closure differs")
    require(closure.get("source_execution_config_sha256") == sha256(root / SOURCE_CONFIG_REL), "source config closure differs")
    require(closure.get("source_manifest_sha256") == sha256(root / MANIFEST_REL), "source manifest closure differs")

    if args.require_committed:
        require(not git(root, "status", "--porcelain").stdout.strip(), "worktree is dirty")
        ancestor = git(root, "merge-base", "--is-ancestor", launch["git_head"], "HEAD", check=False)
        require(ancestor.returncode == 0, "formal preregistration commit is not an ancestor of HEAD")
        analysis_ancestor = git(
            root, "merge-base", "--is-ancestor", closure["analysis_preregistration_git_head"], "HEAD", check=False
        )
        require(analysis_ancestor.returncode == 0, "analysis preregistration commit is not an ancestor of HEAD")
        files = [path for path in analysis.rglob("*") if path.is_file()]
        require(files, "analysis contains no files")
        for path in files:
            relative = str(path.relative_to(root))
            tracked = git(root, "ls-files", "--error-unmatch", "--", relative, check=False)
            require(tracked.returncode == 0, f"analysis evidence is not committed: {relative}")
            require(not path.is_symlink(), f"analysis evidence is symbolic: {relative}")
    print(
        "accepted analysis R2: 15/15 frozen displacement/strain references, 7/7 diagnostic pairs, "
        "complete Mermin labels/forces/stress/cubes/Ngrid; no G4 claim"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
