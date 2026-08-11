#!/usr/bin/env python3
"""Analysis-only G1 acceptance policy revision using committed R3 and R5 evidence."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import analyze_s1_g1_three_layer_analysis_r3 as r3
import analyze_s1_g1_three_layer_al_followup_analysis_r5 as r5
import validate_s1_g1_three_layer_al_followup_analysis_r5 as r5v
from s1_g1_three_layer_al_followup_r4_common import load_config as load_r4_config

CONFIG_PATH = Path("config/S1_g1_al_acceptance_policy_r1.json")
R5_CONFIG_PATH = Path("config/S1_g1_three_layer_al_domain_followup_analysis_r5.json")
OUTPUT_NAMES = ("README.md", "analysis_revision.json", "gates.tsv", "summary.json")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def read_json(path: Path) -> dict:
    require(path.is_file() and not path.is_symlink(), f"missing JSON: {path}")
    value = json.loads(path.read_text())
    require(isinstance(value, dict), f"JSON root differs: {path}")
    return value


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout.strip()


def parent_row(root: Path, commit: str) -> list[str]:
    return git(root, "rev-list", "--parents", "-n", "1", commit).split()


def verify_source_file(root: Path, commit: str, relative: str, expected_sha: str) -> None:
    path = root / relative
    require(path.is_file() and not path.is_symlink(), f"source file missing: {relative}")
    require(sha256_file(path) == expected_sha, f"source SHA differs: {relative}")
    committed = subprocess.run(
        ["git", "show", f"{commit}:{relative}"], cwd=root, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout
    require(path.read_bytes() == committed, f"source worktree/commit bytes differ: {relative}")


def verify_scope(scope: dict) -> None:
    require(scope.get("accepted_item6_scope") == "Al_ABACUS_same_engine_registered_PP_pair_scheme_plus_construction_suitability_bound", "accepted scope differs")
    for key in (
        "original_D026_second_independent_KS_or_QE_closed",
        "second_independent_KS_closed",
        "qe_control_closed",
        "g4_projector_only_causality_closed",
    ):
        require(scope.get(key) is False, f"open semantic limit was closed: {key}")
    require(scope.get("mg_compatibility_role") == "diagnostic_only_not_in_overall", "Mg role differs")
    require(scope.get("of_l_vs_ks_l_role") == "accepted_error_portrait_not_physical_accuracy", "OF-L portrait role differs")
    require(scope.get("hqlpp_qe_binding_smoke_role") == "open_failed_no_retry_contributes_zero_not_in_overall", "HQLPP/QE smoke role differs")


def evaluate_metrics(metrics: dict, limits: dict) -> dict:
    checks = {
        "equilibrium_volume_difference": metrics["equilibrium_volume_difference_percent"] <= limits["equilibrium_volume_difference_percent_max"],
        "bulk_modulus_difference": metrics["bulk_modulus_difference_percent"] <= limits["bulk_modulus_difference_percent_max"],
        "ks_l_bm3_residual": metrics["ks_l_bm3_max_abs_residual_mev_per_atom"] < limits["bm3_residual_mev_per_atom_strict_lt"],
        "ks_nl_bm3_residual": metrics["ks_nl_bm3_max_abs_residual_mev_per_atom"] < limits["bm3_residual_mev_per_atom_strict_lt"],
        "anchored_curve": metrics["anchored_curve_max_abs_difference_mev_per_atom"] <= limits["anchored_curve_difference_mev_per_atom_max"],
        "strain": metrics["maximum_strain_absolute_difference_mev_per_atom"] <= limits["strain_difference_mev_per_atom_max"],
        "v090_k": metrics["v090_anchored_k_difference_mev_per_atom"] < limits["endpoint_k_difference_mev_per_atom_strict_lt"],
        "v090_cutoff": metrics["v090_anchored_cutoff_difference_mev_per_atom"] < limits["endpoint_cutoff_difference_mev_per_atom_strict_lt"],
        "v090_pressure": metrics["v090_cutoff_pressure_difference_gpa"] < limits["endpoint_pressure_difference_gpa_strict_lt"],
        "v110_k": metrics["v110_anchored_k_difference_mev_per_atom"] < limits["endpoint_k_difference_mev_per_atom_strict_lt"],
        "v110_cutoff": metrics["v110_anchored_cutoff_difference_mev_per_atom"] < limits["endpoint_cutoff_difference_mev_per_atom_strict_lt"],
        "v110_pressure": metrics["v110_cutoff_pressure_difference_gpa"] < limits["endpoint_pressure_difference_gpa_strict_lt"],
    }
    require(all(checks.values()), "one or more revised G1 hard gates rejected")
    return checks


def parse_r5_gates(path: Path) -> list[dict]:
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    require(len(rows) == 10, "R5 gate denominator differs")
    require(all(row.get("accepted") == "True" for row in rows), "R5 committed gate rejected")
    return rows


def replay_sources(root: Path, cfg: dict) -> tuple[dict, dict, list[dict]]:
    r3_spec = cfg["source_r3"]
    r5_spec = cfg["source_r5"]
    base = cfg["registration"]["integration_base_commit"]
    require(parent_row(root, base) == [base, *cfg["registration"]["integration_base_parents"]], "integration-base parent row differs")
    for commit in cfg["registration"]["integration_base_parents"]:
        require(git(root, "merge-base", "--is-ancestor", commit, "HEAD") == "", f"source commit is not an ancestor: {commit}")
    verify_source_file(root, r3_spec["final_commit"], r3_spec["summary_path"], r3_spec["summary_sha256"])
    verify_source_file(root, r3_spec["final_commit"], r3_spec["gate_metrics_path"], r3_spec["gate_metrics_sha256"])
    for key in ("summary", "gates", "analysis_revision"):
        verify_source_file(root, r5_spec["evidence_commit"], r5_spec[f"{key}_path"], r5_spec[f"{key}_sha256"])

    old_summary = read_json(root / r3_spec["summary_path"])
    require(old_summary.get("status") == r3_spec["historical_status"], "historical R3 disposition changed")
    require(old_summary.get("evidence_valid") is True and old_summary.get("scientific_gate_status") == "rejected", "historical R3 rejection/evidence status differs")
    require(old_summary["al_ks_l_vs_ks_nl"]["equilibrium_volume_difference_percent_max"] == r3_spec["historical_equilibrium_volume_difference_percent_max"], "historical 0.5-percent gate changed")
    replayed_r3, _ = r3.replay(root, r3.load_config(root), r3_spec["preregistration_commit"])
    require(replayed_r3 == old_summary, "R3 full replay differs from committed summary")

    r5_summary = read_json(root / r5_spec["summary_path"])
    r5_config = read_json(root / R5_CONFIG_PATH)
    replayed_r5, replayed_rows = r5v._validate_scientific_replay(
        root, load_r4_config(root), r5_config, root / r5.ANALYSIS_ROOT,
    )
    require(replayed_r5 == r5_summary, "R5 full replay differs from committed summary")
    require(r5.gate_tsv_bytes(replayed_rows) == (root / r5_spec["gates_path"]).read_bytes(), "R5 full gate replay differs")
    committed_gate_rows = parse_r5_gates(root / r5_spec["gates_path"])
    return old_summary, r5_summary, committed_gate_rows


def _metric_from_rows(rows: list[dict], point: str, metric: str) -> float:
    matches = [float(row["value"]) for row in rows if row["point"] == point and row["metric"] == metric]
    require(len(matches) == 1, f"R5 metric denominator differs: {point}/{metric}")
    return matches[0]


def derive_metrics(root: Path, cfg: dict, r3_summary: dict, r5_summary: dict, r5_rows: list[dict]) -> dict:
    comparison = r3_summary["al_ks_l_vs_ks_nl"]
    r3_config = r3.load_config(root)
    independent = r3.independent_point_metrics(root / r3_config["source_r2"]["analysis_root"] / "points.tsv")
    r2_summary = read_json(root / r3_config["source_r2"]["analysis_root"] / "summary.json")
    diagnostic = r5_summary["r4_analyzer_false_negative_closure"]["scientific_diagnostic"]
    metrics = {
        "equilibrium_volume_difference_percent": comparison["equilibrium_volume_difference_percent"],
        "bulk_modulus_difference_percent": comparison["bulk_modulus_difference_percent"],
        "anchored_curve_max_abs_difference_mev_per_atom": independent["anchored_curve_max_abs_difference_mev_per_atom"],
        "ks_l_bm3_max_abs_residual_mev_per_atom": comparison["ks_l_fit"]["max_abs_residual_mev_per_atom"],
        "ks_nl_bm3_max_abs_residual_mev_per_atom": comparison["ks_nl_fit"]["max_abs_residual_mev_per_atom"],
        "of_l_vs_ks_l_equilibrium_volume_difference_percent": r2_summary["al_three_layer_eos"]["of_l_vs_ks_l"]["equilibrium_volume_difference_percent"],
        "maximum_strain_absolute_difference_mev_per_atom": diagnostic["maximum_strain_absolute_difference_mev_per_atom"],
        "v090_anchored_k_difference_mev_per_atom": _metric_from_rows(r5_rows, "v090", "anchored_k_difference_mev_per_atom"),
        "v090_anchored_cutoff_difference_mev_per_atom": _metric_from_rows(r5_rows, "v090", "anchored_cutoff_difference_mev_per_atom"),
        "v090_cutoff_pressure_difference_gpa": _metric_from_rows(r5_rows, "v090", "cutoff_pressure_difference_gpa"),
        "v110_anchored_k_difference_mev_per_atom": _metric_from_rows(r5_rows, "v110", "anchored_k_difference_mev_per_atom"),
        "v110_anchored_cutoff_difference_mev_per_atom": _metric_from_rows(r5_rows, "v110", "anchored_cutoff_difference_mev_per_atom"),
        "v110_cutoff_pressure_difference_gpa": _metric_from_rows(r5_rows, "v110", "cutoff_pressure_difference_gpa"),
    }
    for key, expected in cfg["expected_metrics"].items():
        require(abs(metrics[key] - expected) < 1e-10, f"registered scientific metric differs: {key}")
    return metrics


def gate_rows(metrics: dict, limits: dict, source_r5_rows: list[dict]) -> list[dict]:
    rows = [
        {"gate": "al_delta_v0_percent", "source": "R3", "value": metrics["equilibrium_volume_difference_percent"], "limit": limits["equilibrium_volume_difference_percent_max"], "inequality": "<=", "affects_overall": True, "accepted": True, "interpretation": "revised_user_authorized_gate"},
        {"gate": "al_delta_b0_percent", "source": "R3", "value": metrics["bulk_modulus_difference_percent"], "limit": limits["bulk_modulus_difference_percent_max"], "inequality": "<=", "affects_overall": True, "accepted": True, "interpretation": "unchanged_gate"},
        {"gate": "ks_l_bm3_residual_mev_per_atom", "source": "R3", "value": metrics["ks_l_bm3_max_abs_residual_mev_per_atom"], "limit": limits["bm3_residual_mev_per_atom_strict_lt"], "inequality": "<", "affects_overall": True, "accepted": True, "interpretation": "unchanged_gate"},
        {"gate": "ks_nl_bm3_residual_mev_per_atom", "source": "R3", "value": metrics["ks_nl_bm3_max_abs_residual_mev_per_atom"], "limit": limits["bm3_residual_mev_per_atom_strict_lt"], "inequality": "<", "affects_overall": True, "accepted": True, "interpretation": "unchanged_gate"},
        {"gate": "anchored_curve_max_mev_per_atom", "source": "R3", "value": metrics["anchored_curve_max_abs_difference_mev_per_atom"], "limit": limits["anchored_curve_difference_mev_per_atom_max"], "inequality": "<=", "affects_overall": True, "accepted": True, "interpretation": "unchanged_gate"},
        {"gate": "historical_delta_v0_0p5_percent", "source": "R3", "value": metrics["equilibrium_volume_difference_percent"], "limit": 0.5, "inequality": ">", "affects_overall": False, "accepted": True, "interpretation": "historical_rejection_preserved_not_rewritten"},
        {"gate": "of_l_vs_ks_l_error_portrait", "source": "R3", "value": metrics["of_l_vs_ks_l_equilibrium_volume_difference_percent"], "limit": 1.0, "inequality": "portrait", "affects_overall": False, "accepted": True, "interpretation": "accepted_error_portrait_not_physical_accuracy"},
    ]
    for source in source_r5_rows:
        rows.append({
            "gate": f"r5_{source['gate']}_{source['point']}_{source['metric']}",
            "source": "R5", "value": float(source["value"]), "limit": float(source["limit"]),
            "inequality": source["inequality"], "affects_overall": True,
            "accepted": source["accepted"] == "True", "interpretation": "unchanged_R5_gate",
        })
    require(len(rows) == 17 and all(row["accepted"] for row in rows if row["affects_overall"]), "final gate denominator/status differs")
    return rows


def gates_tsv_bytes(rows: list[dict]) -> bytes:
    fields = ("gate", "source", "value", "limit", "inequality", "affects_overall", "accepted", "interpretation")
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode()


def build_analysis(root: Path, cfg: dict) -> tuple[dict, list[dict]]:
    verify_scope(cfg["scope_decision"])
    require(cfg.get("new_solver_run_count") == 0, "policy revision must not run a solver")
    require(cfg["acceptance"]["equilibrium_volume_difference_percent_max"] == 1.0, "authorized 1-percent gate differs")
    r3_summary, r5_summary, source_r5_rows = replay_sources(root, cfg)
    metrics = derive_metrics(root, cfg, r3_summary, r5_summary, source_r5_rows)
    checks = evaluate_metrics(metrics, cfg["acceptance"])
    rows = gate_rows(metrics, cfg["acceptance"], source_r5_rows)
    expected = cfg["expected_disposition"]
    summary = {
        "schema_version": 1,
        "protocol_revision": cfg["protocol_revision"],
        "status": expected["status"],
        "evidence_valid": expected["evidence_valid"],
        "scientific_gate_accepted": expected["scientific_gate_accepted"],
        "g1": {"status": "accepted", "accepted_subitems": expected["accepted_subitems"], "required_subitems": expected["required_subitems"]},
        "new_solver_run_count": 0,
        "policy_decision": {"authorized_date": "2026-08-11", "old_equilibrium_volume_limit_percent": 0.5, "new_equilibrium_volume_limit_percent": 1.0, "historical_R3_rejection_preserved": True},
        "source_commits": {"R3": cfg["source_r3"]["final_commit"], "R5": cfg["source_r5"]["evidence_commit"], "integration_base": cfg["registration"]["integration_base_commit"]},
        "source_status": {"R3_historical": r3_summary["status"], "R5_followup": r5_summary["status"]},
        "metrics": metrics,
        "hard_gate_checks": checks,
        "gate_row_count": len(rows),
        "scope_decision": cfg["scope_decision"],
        "interpretation": "accepted only for the Al ABACUS same-engine registered-PP-pair scheme-plus-construction suitability bound; second independent KS/QE remains open",
    }
    return summary, rows


def revision_payload(root: Path, cfg: dict) -> dict:
    head = git(root, "rev-parse", "HEAD")
    implementation = cfg["registration"]["implementation_commit"]
    if implementation == "__FREEZE_IMPLEMENTATION_COMMIT__":
        preregistration = "__FREEZE_PREREGISTRATION_COMMIT__"
    elif parent_row(root, head) == [head, implementation]:
        preregistration = head
    else:
        row = parent_row(root, head)
        require(len(row) == 2 and parent_row(root, row[1]) == [row[1], implementation], "cannot derive preregistration commit from topology")
        preregistration = row[1]
    return {
        "schema_version": 1,
        "protocol_revision": cfg["protocol_revision"],
        "status": "accepted",
        "implementation_commit": cfg["registration"]["implementation_commit"],
        "preregistration_commit": preregistration,
        "integration_base_commit": cfg["registration"]["integration_base_commit"],
        "source_r3_final_commit": cfg["source_r3"]["final_commit"],
        "source_r5_evidence_commit": cfg["source_r5"]["evidence_commit"],
        "config_sha256": sha256_file(root / CONFIG_PATH),
        "new_solver_run_count": 0,
    }


def readme_bytes(summary: dict) -> bytes:
    metrics = summary["metrics"]
    return (
        "# G1 Al acceptance policy R1\n\n"
        "This analysis-only revision preserves the historical 0.5% R3 rejection and applies the user-authorized 1.0% equilibrium-volume gate.\n\n"
        f"- Disposition: `{summary['status']}`; G1 `{summary['g1']['accepted_subitems']}/{summary['g1']['required_subitems']}`.\n"
        f"- Al |delta V0|: `{metrics['equilibrium_volume_difference_percent']:.10f}% <= 1.0%`.\n"
        f"- Al |delta B0|: `{metrics['bulk_modulus_difference_percent']:.10f}% <= 10%`.\n"
        f"- R5 strain maximum: `{metrics['maximum_strain_absolute_difference_mev_per_atom']:.10f} meV/atom`.\n"
        "- Scope: Al ABACUS same-engine registered PP-pair scheme+construction suitability only.\n"
        "- Still open: independent second KS/QE, Mg hard comparison, projector-only causality, and G4 force/stress.\n"
        "- HQLPP/QE binding-smoke failure is preserved, no-retry, contributes zero, and does not affect this disposition.\n"
    ).encode()


def rendered_outputs(root: Path, cfg: dict) -> dict[str, bytes]:
    summary, rows = build_analysis(root, cfg)
    return {
        "README.md": readme_bytes(summary),
        "analysis_revision.json": canonical_json_bytes(revision_payload(root, cfg)),
        "gates.tsv": gates_tsv_bytes(rows),
        "summary.json": canonical_json_bytes(summary),
    }


def write_outputs(root: Path, cfg: dict) -> None:
    output_root = root / cfg["analysis_root"]
    require(not output_root.exists(), "analysis output already exists")
    output_root.mkdir(parents=True)
    for name, data in rendered_outputs(root, cfg).items():
        (output_root / name).write_bytes(data)


def find_root() -> Path:
    return Path(git(Path.cwd(), "rev-parse", "--show-toplevel"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    require(args.write != args.dry_run, "choose exactly one mode")
    root = find_root()
    cfg = read_json(root / CONFIG_PATH)
    if args.write:
        write_outputs(root, cfg)
    else:
        summary, rows = build_analysis(root, cfg)
        print(json.dumps({"status": summary["status"], "g1": summary["g1"], "gate_rows": len(rows), "new_solver_run_count": 0}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
