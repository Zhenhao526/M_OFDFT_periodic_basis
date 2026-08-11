#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

import analyze_s2_g2_al_localized_r1 as analyzer
from s2_g2_al_localized_common_r1 import (
    ANALYZER_REL, BASE_COMMIT, COMMON_REL, CONFIG_REL, GENERATOR_REL, PROTOCOL_REL,
    RUNNER_REL, TEST_REL, VALIDATOR_REL, WRAPPER_REL, git, load_config,
    registered_input_payloads, require, validate_config,
)

REGISTERED_IMPLEMENTATION_PATHS = {
    str(CONFIG_REL), str(PROTOCOL_REL), str(COMMON_REL), str(GENERATOR_REL), str(WRAPPER_REL),
    str(RUNNER_REL), str(ANALYZER_REL), str(VALIDATOR_REL), str(TEST_REL),
}


def changed(root: Path, older: str, newer: str) -> list[tuple[str, str]]:
    rows = []
    for line in git(root, "diff", "--name-status", older, newer).splitlines():
        status, path = line.split("\t", 1)
        rows.append((status, path))
    return rows


def normalized_config(config: dict) -> dict:
    payload = json.loads(json.dumps(config))
    payload["status"] = "implementation_pending_preregistration"
    payload["implementation_commit"] = "__FREEZE_IMPLEMENTATION_COMMIT__"
    return payload


def validate_implementation(root: Path, head: str) -> dict:
    config = load_config(root)
    validate_config(config)
    require(config["status"] == "implementation_pending_preregistration", "implementation status differs")
    require(config["implementation_commit"] == "__FREEZE_IMPLEMENTATION_COMMIT__", "implementation sentinel differs")
    require(git(root, "show", "-s", "--format=%P", head).split() == [BASE_COMMIT], "implementation parent differs")
    rows = changed(root, BASE_COMMIT, head)
    actual = {path for status, path in rows if status == "A"}
    require(all(status == "A" for status, _ in rows), "implementation may only add paths")
    input_prefix = config["execution"]["input_root"] + "/"
    require(actual == REGISTERED_IMPLEMENTATION_PATHS | {input_prefix + name for name in ("INPUT", "STRU", "KPT", "metadata.json")}, "implementation path denominator differs")
    require(not (root / config["execution"]["analysis_root"]).exists(), "analysis exists before execution")
    require(not Path(config["execution"]["state_root"]).exists(), "formal state exists before preregistration")
    expected = registered_input_payloads(root, config)
    for name, data in expected.items():
        require((root / config["execution"]["input_root"] / name).read_bytes() == data, f"input differs: {name}")
    return {"status": "accepted_implementation", "registered_paths": len(actual)}


def validate_preregistered(root: Path, head: str) -> dict:
    config = load_config(root)
    validate_config(config)
    require(config["status"] == "preregistered_no_execution", "preregistration status differs")
    implementation = config["implementation_commit"]
    require(git(root, "show", "-s", "--format=%P", head).split() == [implementation], "preregistration parent differs")
    require(changed(root, implementation, head) == [("M", str(CONFIG_REL))], "preregistration diff differs")
    implementation_config = json.loads(git(root, "show", f"{implementation}:{CONFIG_REL}") )
    require(normalized_config(config) == implementation_config, "preregistration changed non-registration content")
    validate_implementation_detached(root, implementation)
    require(not Path(config["execution"]["state_root"]).exists(), "formal state exists before launch")
    require(not (root / config["execution"]["analysis_root"]).exists(), "analysis exists before execution")
    return {"status": "accepted_preregistration", "implementation_commit": implementation}


def validate_implementation_detached(root: Path, commit: str) -> None:
    require(git(root, "show", "-s", "--format=%P", commit).split() == [BASE_COMMIT], "registered implementation parent differs")
    rows = changed(root, BASE_COMMIT, commit)
    config = json.loads(git(root, "show", f"{commit}:{CONFIG_REL}"))
    prefix = config["execution"]["input_root"] + "/"
    expected = REGISTERED_IMPLEMENTATION_PATHS | {prefix + name for name in ("INPUT", "STRU", "KPT", "metadata.json")}
    require(all(status == "A" for status, _ in rows) and {path for _, path in rows} == expected, "registered implementation diff differs")


def validate_committed(root: Path, head: str) -> dict:
    config = load_config(root)
    parent = git(root, "show", "-s", "--format=%P", head).split()
    require(len(parent) == 1, "evidence commit must have one parent")
    prereg = parent[0]
    require(git(root, "show", "-s", "--format=%P", prereg).split() == [config["implementation_commit"]], "evidence parent is not registered preregistration")
    require(changed(root, config["implementation_commit"], prereg) == [("M", str(CONFIG_REL))], "registered preregistration diff differs")
    prereg_config = json.loads(git(root, "show", f"{prereg}:{CONFIG_REL}"))
    implementation_config = json.loads(git(root, "show", f"{config['implementation_commit']}:{CONFIG_REL}"))
    require(normalized_config(prereg_config) == implementation_config, "registered preregistration content differs")
    validate_implementation_detached(root, config["implementation_commit"])
    output_prefix = config["execution"]["analysis_root"] + "/"
    rows = changed(root, prereg, head)
    require(all(status == "A" and path.startswith(output_prefix) for status, path in rows), "evidence diff escaped analysis root")
    require({path.removeprefix(output_prefix) for _, path in rows} == set(config["output"]["files"]), "evidence output denominator differs")
    summary, expected = analyzer.build_analysis(root, config)
    output_root = root / config["execution"]["analysis_root"]
    require(output_root.is_dir() and not any(path.is_symlink() for path in output_root.rglob("*")), "output tree differs")
    require({str(path.relative_to(output_root)) for path in output_root.rglob("*") if path.is_file()} == set(expected), "output tree denominator differs")
    for name, data in expected.items():
        require((output_root / name).read_bytes() == data, f"committed output replay differs: {name}")
    return {"status": "accepted_committed_evidence", "scientific_status": summary["status"], "scientific_gate_accepted": summary["scientific_gate_accepted"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--implementation-only", action="store_true")
    group.add_argument("--preregistered-only", action="store_true")
    group.add_argument("--require-committed", action="store_true")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.project_root.resolve()
    require(git(root, "status", "--porcelain") == "", "worktree must be clean")
    head = git(root, "rev-parse", "HEAD")
    if args.implementation_only:
        result = validate_implementation(root, head)
    elif args.preregistered_only:
        result = validate_preregistered(root, head)
    else:
        result = validate_committed(root, head)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
