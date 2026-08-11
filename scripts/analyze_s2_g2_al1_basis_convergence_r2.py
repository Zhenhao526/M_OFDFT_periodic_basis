#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from s2_g2_al1_basis_convergence_common_r2 import build_analysis, git, load_config, render_outputs, require


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--write", action="store_true")
    args = parser.parse_args()
    root = args.project_root.resolve()
    require(git(root, "status", "--porcelain") == "", "worktree must be clean")
    config = load_config(root)
    require(config["status"] == "preregistered_no_execution", "analysis requires preregistered config")
    head = git(root, "rev-parse", "HEAD")
    require(git(root, "show", "-s", "--format=%P", head).split() == [config["implementation_commit"]],
            "analysis HEAD is not the config-only preregistration")
    analysis = build_analysis(root, config)
    outputs = render_outputs(analysis)
    output_root = root / config["output"]["root"]
    if args.write:
        require(not output_root.exists(), "analysis output already exists")
        output_root.mkdir(parents=True)
        for name in config["output"]["files"]:
            (output_root / name).write_bytes(outputs[name])
        mode = "write"
    else:
        require(not output_root.exists(), "dry-run requires absent output")
        mode = "dry-run"
    selected = analysis["summary"]["selected_candidate"]
    print(json.dumps({
        "status": analysis["summary"]["status"], "mode": mode,
        "accepted_candidate_count": analysis["summary"]["accepted_candidate_count"],
        "selected_candidate": selected["candidate_id"] if selected else None,
        "new_solver_run_count": 0, "output_files": len(outputs),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
