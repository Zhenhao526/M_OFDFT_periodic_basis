#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from s2_g2_al_scale_geometry_common_r3 import (
    BASE_COMMIT,
    CONFIG_REL,
    IMPLEMENTATION_PATHS,
    build_analysis,
    git,
    load_config,
    render_outputs,
    require,
    validate_config,
)


def changed_paths(root: Path, parent: str, head: str) -> dict[str, str]:
    observed = {}
    for line in git(root, "diff", "--name-status", parent, head).splitlines():
        if line:
            status, path = line.split("\t", 1)
            observed[path] = status
    return observed


def validate_implementation_shape(root: Path, implementation: str) -> None:
    require(git(root, "show", "-s", "--format=%P", implementation).split() == [BASE_COMMIT],
            "implementation parent differs")
    changes = changed_paths(root, BASE_COMMIT, implementation)
    require(set(changes) == IMPLEMENTATION_PATHS, "implementation path denominator differs")
    require(all(status == "A" for status in changes.values()), "implementation paths must be additions")


def normalized_preregistered(config: dict) -> dict:
    normalized = json.loads(json.dumps(config))
    normalized["status"] = "implementation_pending_preregistration"
    normalized["implementation_commit"] = "__FREEZE_IMPLEMENTATION_COMMIT__"
    return normalized


def validate_registered_chain(root: Path, preregistration: str) -> str:
    parents = git(root, "show", "-s", "--format=%P", preregistration).split()
    require(len(parents) == 1, "preregistration must have one parent")
    implementation = parents[0]
    validate_implementation_shape(root, implementation)
    require(changed_paths(root, implementation, preregistration) == {str(CONFIG_REL): "M"},
            "preregistration must modify config only")
    registered = json.loads(git(root, "show", f"{preregistration}:{CONFIG_REL}"))
    implementation_config = json.loads(git(root, "show", f"{implementation}:{CONFIG_REL}"))
    require(registered["status"] == "preregistered_no_execution", "preregistered status differs")
    require(registered["implementation_commit"] == implementation, "registered implementation identity differs")
    require(normalized_preregistered(registered) == implementation_config, "non-registration config changed")
    return implementation


def main() -> int:
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--implementation-only", action="store_true")
    modes.add_argument("--preregistered-only", action="store_true")
    modes.add_argument("--require-committed", action="store_true")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.project_root.resolve()
    require(git(root, "status", "--porcelain") == "", "worktree must be clean")
    head = git(root, "rev-parse", "HEAD")
    config = load_config(root)
    validate_config(config)
    analysis = None
    if args.implementation_only:
        validate_implementation_shape(root, head)
        require(config["status"] == "implementation_pending_preregistration", "implementation status differs")
        require(config["implementation_commit"] == "__FREEZE_IMPLEMENTATION_COMMIT__", "implementation placeholder differs")
        require(not (root / config["output"]["root"]).exists(), "output must be absent")
        mode = "implementation-only"
    elif args.preregistered_only:
        validate_registered_chain(root, head)
        require(config == json.loads(git(root, "show", f"{head}:{CONFIG_REL}")), "worktree config differs")
        require(not (root / config["output"]["root"]).exists(), "output must be absent")
        analysis = build_analysis(root, config)
        mode = "preregistered-only"
    else:
        parents = git(root, "show", "-s", "--format=%P", head).split()
        require(len(parents) == 1, "evidence commit must have one parent")
        preregistration = parents[0]
        validate_registered_chain(root, preregistration)
        output_root = config["output"]["root"]
        expected_changes = {f"{output_root}/{name}": "A" for name in config["output"]["files"]}
        require(changed_paths(root, preregistration, head) == expected_changes, "evidence diff is not output-only")
        analysis = build_analysis(root, config)
        expected = render_outputs(analysis)
        actual_root = root / output_root
        require(actual_root.is_dir() and not actual_root.is_symlink(), "output root differs")
        require(sorted(path.name for path in actual_root.iterdir()) == sorted(config["output"]["files"]),
                "output denominator differs")
        for name, data in expected.items():
            path = actual_root / name
            require(path.is_file() and not path.is_symlink() and path.read_bytes() == data,
                    f"committed output differs: {name}")
        mode = "require-committed"
    summary = analysis["summary"] if analysis else None
    print(json.dumps({
        "status": "accepted", "mode": mode, "head": head,
        "scientific_disposition": summary["status"] if summary else None,
        "geometry_cases": summary["geometry_case_count"] if summary else None,
        "scale_cases": summary["scale_case_count"] if summary else None,
        "new_solver_run_count": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
