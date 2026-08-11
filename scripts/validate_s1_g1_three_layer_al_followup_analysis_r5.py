#!/usr/bin/env python3
"""Fail-closed preregistration and committed-evidence validator for analysis R5."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import analyze_s1_g1_three_layer_al_followup_analysis_r5 as r5
from s1_g1_three_layer_al_followup_r4_common import (
    canonical_json_bytes,
    find_project_root,
    load_config,
    read_json,
    require,
    sha256_bytes,
    sha256_file,
)


def _git(project_root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=project_root, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout


def _object(path: Path) -> dict:
    require(path.is_file() and not path.is_symlink(), f"missing JSON: {path}")
    payload = read_json(path)
    require(isinstance(payload, dict), f"JSON root differs: {path}")
    return payload


def _validate_scientific_replay(
    project_root: Path,
    scientific_config: dict,
    r5_config: dict,
    output_root: Path | None,
) -> tuple[dict, list[dict]]:
    if output_root is None:
        new = Path(scientific_config["external_state_root"])
        r1 = Path(scientific_config["source_states"]["r1_p0"]["external_state_root"])
        continuation = Path(scientific_config["source_states"]["continuation_r2"]["external_state_root"])
    else:
        snapshot = output_root / "state_snapshot"
        new = snapshot / "followup_r4"
        r1 = snapshot / "r1_p0"
        continuation = snapshot / "continuation_r2"
    summary, rows = r5.build_analysis(project_root, scientific_config, new, r1, continuation)
    require(summary.get("status") == "accepted", "R5 scientific replay rejected")
    require(summary.get("analysis_protocol_revision") == r5.ANALYSIS_PROTOCOL_REVISION, "R5 summary analysis revision differs")
    require(summary.get("accepted_run_count") == 8 and summary.get("failed_missing_skipped_retried_count") == 0, "R5 summary denominator differs")
    require(summary["galileo_gates"]["status"] == "accepted" and len(rows) == 10, "R5 gate denominator/status differs")
    expected_metrics = r5.verify_expected_scientific_metrics(summary, rows, r5_config)
    require(summary.get("expected_scientific_metrics_verification") == expected_metrics, "R5 summary scientific-metric verification differs")
    return summary, rows


def require_evidence_only_diff(rows: list[list[str]], tracked: list[str], expected_prefix: str) -> None:
    require(rows, "R5 evidence commit is empty")
    require(all(len(row) == 2 for row in rows), "R5 evidence diff row differs")
    require(all(status == "A" and path.startswith(expected_prefix) for status, path in rows), "R5 evidence commit contains non-output or non-addition change")
    require([path for _, path in rows] == tracked, "R5 evidence diff/file set differs")


def _validate_evidence_commit(
    project_root: Path,
    r5_config: dict,
    topology: dict,
    scientific_config: dict,
    closure_identity: dict,
) -> dict:
    head = topology["head"]
    preregistration = topology["preregistration_commit"]
    require(r5._parent_row(project_root, head) == [head, preregistration], "R5 evidence commit must have preregistration as unique parent")
    require(_git(project_root, "ls-tree", "-r", "--name-only", preregistration, "--", r5.ANALYSIS_ROOT.as_posix()) == "", "R5 analysis output existed at preregistration")
    rows = [
        line.split("\t", 1)
        for line in _git(project_root, "diff", "--name-status", preregistration, head).splitlines()
        if line
    ]
    expected_prefix = r5.ANALYSIS_ROOT.as_posix() + "/"
    output_root = project_root / r5.ANALYSIS_ROOT
    require(output_root.is_dir() and not output_root.is_symlink(), "R5 analysis output root missing")
    files = sorted(path for path in output_root.rglob("*") if path.is_file())
    require(files and all(not path.is_symlink() for path in files), "R5 output contains unsafe file")
    require(not any(path.suffix.lower() == ".upf" for path in files), "R5 output must not commit UPF bodies")
    relative_files = [path.relative_to(project_root).as_posix() for path in files]
    tracked = [
        line for line in _git(project_root, "ls-tree", "-r", "--name-only", head, "--", r5.ANALYSIS_ROOT.as_posix()).splitlines()
        if line
    ]
    require(relative_files == tracked, "R5 filesystem/committed output file set differs")
    require_evidence_only_diff(rows, tracked, expected_prefix)
    summary, gate_rows = _validate_scientific_replay(
        project_root, scientific_config, r5_config, output_root,
    )
    require((output_root / "summary.json").read_bytes() == canonical_json_bytes(summary), "R5 committed summary replay differs")
    require((output_root / "gates.tsv").read_bytes() == r5.gate_tsv_bytes(gate_rows), "R5 committed gates replay differs")
    require((output_root / "README.md").read_bytes() == r5.readme_bytes(summary), "R5 committed README replay differs")
    require(
        (output_root / "analysis_revision.json").read_bytes()
        == r5.revision_bytes(project_root, r5_config, topology, summary),
        "R5 committed revision identity differs",
    )
    revision = _object(output_root / "analysis_revision.json")
    require(revision.get("new_solver_run_count") == 0 and revision.get("status") == "accepted", "R5 revision status differs")
    require(revision.get("r4_analyzer_false_negative_closure") == closure_identity, "R5 revision/closure identity differs")
    digest_rows = [
        {
            "path": path.relative_to(output_root).as_posix(),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in files
    ]
    return {
        "accepted": True,
        "evidence_commit": head,
        "preregistration_commit": preregistration,
        "output_file_count": len(files),
        "output_total_bytes": sum(path.stat().st_size for path in files),
        "output_inventory_sha256": sha256_bytes(b"".join(canonical_json_bytes(row) for row in digest_rows)),
        "scientific_status": summary["status"],
        "gate_row_count": len(gate_rows),
        "new_solver_run_count": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--require-committed", action="store_true")
    args = parser.parse_args()
    project_root = find_project_root(args.project_root)
    r5_config = r5._load_r5_config(project_root)
    topology = r5.verify_preregistration_topology(
        project_root,
        r5_config,
        require_head_is_preregistration=not args.require_committed,
    )
    closure_identity = r5.verify_r4_false_negative_closure(project_root, r5_config)
    scientific_config = load_config(project_root)
    if args.require_committed:
        details = _validate_evidence_commit(
            project_root, r5_config, topology, scientific_config, closure_identity,
        )
        status = "accepted_committed_analysis_r5"
    else:
        require(not (project_root / r5.ANALYSIS_ROOT).exists(), "R5 output must be absent at preregistration")
        summary, gate_rows = _validate_scientific_replay(
            project_root, scientific_config, r5_config, None,
        )
        details = {
            "accepted": True,
            "scientific_status": summary["status"],
            "gate_row_count": len(gate_rows),
            "analysis_output_exists": False,
            "new_solver_run_count": 0,
        }
        status = "accepted_preregistered_analysis_r5"
    print(json.dumps({
        "status": status,
        "analysis_protocol_revision": r5.ANALYSIS_PROTOCOL_REVISION,
        "topology": topology,
        "r4_closure": closure_identity,
        "details": details,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
