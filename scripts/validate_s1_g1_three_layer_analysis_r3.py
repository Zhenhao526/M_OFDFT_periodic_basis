#!/usr/bin/env python3
"""Validate frozen R3 registration and committed scientific-rejection disposition."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from analyze_s1_g1_three_layer_analysis_r3 import (
    CONFIG_PATH,
    FINAL_OUTPUT_FILES,
    REGISTERED_CODE,
    load_config,
    replay,
    require_isolated_python,
    require_no_upf_body,
    validate_preregistered_topology,
    write_output,
)
from s1_g1_three_layer_continuation_r2_common import (
    find_project_root,
    git,
    read_json,
    require,
    require_clean_tree,
    require_tracked_matches_head,
    sha256_file,
)


def preregistered(project_root: Path, config: dict) -> dict:
    head = require_clean_tree(project_root)
    require("__FREEZE" not in json.dumps(config), "R3 source identity not frozen")
    require_tracked_matches_head(project_root, REGISTERED_CODE)
    topology = validate_preregistered_topology(project_root, config, head)
    output = project_root / config["output_root"]
    require(not output.exists(), "R3 output exists before analysis")
    summary, _ = replay(project_root, config, head)
    require(summary["status"] == "evidence_valid_scientific_gate_rejected", "R3 prereg disposition differs")
    return {
        "status": "accepted",
        "mode": "preregistered",
        "head": head,
        "analysis_implementation_commit": topology["analysis_implementation_commit"],
        "source_r2_evidence_commit": config["source_r2"]["evidence_commit"],
        "scientific_gate_status": summary["scientific_gate_status"],
    }


def committed(project_root: Path, config: dict) -> dict:
    head = require_clean_tree(project_root)
    parents = git(project_root, "rev-list", "--parents", "-n", "1", head).split()
    require(len(parents) == 2, "R3 final commit must have exactly one parent")
    prereg = parents[1]
    topology = validate_preregistered_topology(project_root, config, prereg)
    require(git(project_root, "show", f"{prereg}:{CONFIG_PATH.as_posix()}") == (project_root / CONFIG_PATH).read_text(encoding="utf-8").rstrip("\n"), "R3 config changed after prereg")
    expected_paths = [f"{config['output_root']}/{name}" for name in FINAL_OUTPUT_FILES]
    changed = git(project_root, "diff", "--name-status", f"{prereg}..{head}").splitlines()
    require(changed == [f"A\t{path}" for path in expected_paths], "R3 final diff is not exactly the registered output")
    output_at_prereg = subprocess.run(
        ["git", "cat-file", "-e", f"{prereg}:{config['output_root']}"], cwd=project_root,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    require(output_at_prereg.returncode != 0, "R3 output existed in prereg commit")
    output = project_root / config["output_root"]
    require(output.is_dir() and not output.is_symlink(), "R3 output missing or unsafe")
    require(sorted(path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()) == list(FINAL_OUTPUT_FILES), "R3 output file denominator differs")
    require_no_upf_body(output)
    summary = read_json(output / "summary.json")
    require(isinstance(summary, dict) and summary.get("status") == "evidence_valid_scientific_gate_rejected", "R3 disposition differs")
    require(summary.get("evidence_valid") is True and summary.get("scientific_gate_status") == "rejected", "R3 semantics differ")
    require(summary.get("validator_status") == summary.get("execution_evidence_status") == summary.get("r2_execution_terminal_status") == "accepted", "R3 evidence statuses differ")
    require(summary.get("analysis_implementation_commit") == topology["analysis_implementation_commit"], "R3 summary implementation differs")
    require(summary.get("analysis_preregistered_commit") == prereg, "R3 summary prereg differs")
    require(summary.get("analysis_preregistered_config_sha256") == sha256_file(project_root / CONFIG_PATH), "R3 summary config SHA differs")
    with tempfile.TemporaryDirectory(prefix="g1_three_layer_analysis_r3_validate_") as temporary:
        replayed, gates = replay(project_root, config, prereg)
        rendered = Path(temporary) / "out"
        write_output(rendered, replayed, gates)
        for name in FINAL_OUTPUT_FILES:
            require((output / name).read_bytes() == (rendered / name).read_bytes(), f"R3 replay differs: {name}")
    require_tracked_matches_head(project_root, list(REGISTERED_CODE) + [path.relative_to(project_root) for path in output.rglob("*") if path.is_file()])
    return {"status": "accepted", "mode": "require_committed", "head": head, "disposition": summary["status"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--preregistered-only", action="store_true")
    parser.add_argument("--require-committed", action="store_true")
    args = parser.parse_args()
    require(args.preregistered_only != args.require_committed, "select exactly one validator mode")
    require_isolated_python()
    project_root = find_project_root(args.project_root)
    config = load_config(project_root)
    payload = preregistered(project_root, config) if args.preregistered_only else committed(project_root, config)
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
