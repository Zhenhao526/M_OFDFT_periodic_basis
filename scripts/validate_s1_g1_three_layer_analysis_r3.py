#!/usr/bin/env python3
"""Validate frozen R3 registration and committed scientific-rejection disposition."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from analyze_s1_g1_three_layer_analysis_r3 import REGISTERED_CODE, load_config, replay, write_output
from s1_g1_three_layer_continuation_r2_common import (
    find_project_root,
    git,
    read_json,
    require,
    require_clean_tree,
    require_tracked_matches_head,
)


def preregistered(project_root: Path, config: dict) -> dict:
    head = require_clean_tree(project_root)
    require("__FREEZE" not in json.dumps(config), "R3 source identity not frozen")
    require_tracked_matches_head(project_root, REGISTERED_CODE)
    source_commit = config["source_r2"]["evidence_commit"]
    require(git(project_root, "merge-base", "--is-ancestor", source_commit, head) == "", "unexpected merge-base output")
    return {"status": "accepted", "mode": "preregistered", "head": head, "source_r2_evidence_commit": source_commit}


def committed(project_root: Path, config: dict) -> dict:
    head = require_clean_tree(project_root)
    output = project_root / config["output_root"]
    summary = read_json(output / "summary.json")
    require(isinstance(summary, dict) and summary.get("status") == "evidence_valid_scientific_gate_rejected", "R3 disposition differs")
    require(summary.get("evidence_valid") is True and summary.get("scientific_gate_status") == "rejected", "R3 semantics differ")
    with tempfile.TemporaryDirectory(prefix="g1_three_layer_analysis_r3_validate_") as temporary:
        replayed, gates = replay(project_root, config)
        rendered = Path(temporary) / "out"
        write_output(rendered, replayed, gates)
        for name in ("summary.json", "gate_metrics.tsv", "README.md"):
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
    project_root = find_project_root(args.project_root)
    config = load_config(project_root)
    payload = preregistered(project_root, config) if args.preregistered_only else committed(project_root, config)
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

