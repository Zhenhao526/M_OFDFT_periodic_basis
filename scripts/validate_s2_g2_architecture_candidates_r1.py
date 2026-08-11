#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from s2_g2_architecture_common_r1 import (
    BASE_COMMIT,
    CASE_ROOT_REL,
    CONFIG_REL,
    MANIFEST_REL,
    PROTOCOL_REL,
    git,
    load_config,
    require,
    validate_content,
)

SCRIPT_PATHS = (
    Path("scripts/s2_g2_architecture_common_r1.py"),
    Path("scripts/generate_s2_g2_architecture_candidates_r1.py"),
    Path("scripts/validate_s2_g2_architecture_candidates_r1.py"),
)
TEST_PATH = Path("tests/unit/test_s2_g2_architecture_candidates_r1.py")


def implementation_paths() -> set[str]:
    paths = {str(CONFIG_REL), str(PROTOCOL_REL), str(MANIFEST_REL), str(TEST_PATH)}
    paths.update(str(path) for path in SCRIPT_PATHS)
    paths.update(str(CASE_ROOT_REL / f"S2-G2-20260811-{serial:03d}" / "metadata.json") for serial in range(1, 13))
    return paths


def changed_paths(root: Path, parent: str, head: str) -> tuple[set[str], dict[str, str]]:
    rows = git(root, "diff", "--name-status", parent, head).splitlines()
    paths: set[str] = set()
    statuses: dict[str, str] = {}
    for row in rows:
        if not row:
            continue
        status, path = row.split("\t", 1)
        paths.add(path)
        statuses[path] = status
    return paths, statuses


def validate_implementation(root: Path, config: dict, head: str) -> None:
    parents = git(root, "show", "-s", "--format=%P", head).split()
    require(parents == [BASE_COMMIT], "implementation must be the direct child of the G1 handoff")
    paths, statuses = changed_paths(root, BASE_COMMIT, head)
    require(paths == implementation_paths(), "implementation path denominator differs")
    require(all(statuses[path] == "A" for path in paths), "implementation paths must all be additions")
    require(config["status"] == "implementation_pending_preregistration", "implementation status differs")
    require(config["implementation_commit"] == "__FREEZE_IMPLEMENTATION_COMMIT__", "implementation placeholder differs")


def normalize_preregistered_config(config: dict) -> dict:
    normalized = json.loads(json.dumps(config))
    normalized["status"] = "implementation_pending_preregistration"
    normalized["implementation_commit"] = "__FREEZE_IMPLEMENTATION_COMMIT__"
    return normalized


def validate_preregistered(root: Path, config: dict, head: str) -> None:
    parents = git(root, "show", "-s", "--format=%P", head).split()
    require(len(parents) == 1, "preregistration must have one parent")
    implementation = parents[0]
    paths, statuses = changed_paths(root, implementation, head)
    require(paths == {str(CONFIG_REL)} and statuses[str(CONFIG_REL)] == "M", "preregistration must modify only config")
    implementation_parents = git(root, "show", "-s", "--format=%P", implementation).split()
    require(implementation_parents == [BASE_COMMIT], "registered implementation topology differs")
    impl_paths, impl_statuses = changed_paths(root, BASE_COMMIT, implementation)
    require(impl_paths == implementation_paths(), "registered implementation path denominator differs")
    require(all(impl_statuses[path] == "A" for path in impl_paths), "registered implementation contains a non-addition")
    require(config["status"] == "preregistered_no_execution", "preregistered status differs")
    require(config["implementation_commit"] == implementation, "implementation identity differs")
    implementation_config = json.loads(git(root, "show", f"{implementation}:{CONFIG_REL}"))
    require(normalize_preregistered_config(config) == implementation_config, "non-registration config content changed")


def main() -> int:
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--implementation-only", action="store_true")
    modes.add_argument("--preregistered-only", action="store_true")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.project_root.resolve()
    require(git(root, "status", "--porcelain") == "", "worktree is not clean")
    config = load_config(root)
    validate_content(root, config)
    head = git(root, "rev-parse", "HEAD")
    if args.implementation_only:
        validate_implementation(root, config, head)
        mode = "implementation-only"
    else:
        validate_preregistered(root, config, head)
        mode = "preregistered-only"
    print(json.dumps({"status": "accepted", "mode": mode, "head": head, "registered_cases": 12, "new_solver_run_count": 0}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
