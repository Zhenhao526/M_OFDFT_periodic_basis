#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import analyze_s2_g2_al_localized_analysis_r2 as analyzer
import s2_g2_al_localized_common_r1 as common

CONFIG_REL = analyzer.CONFIG_REL
BASE = analyzer.BASE_COMMIT
IMPLEMENTATION_PATHS = {
    "config/S2_g2_al_localized_analysis_r2.json",
    "docs/S2_G2_AL_LOCALIZED_ANALYSIS_R2_PROTOCOL.md",
    "scripts/analyze_s2_g2_al_localized_analysis_r2.py",
    "scripts/validate_s2_g2_al_localized_analysis_r2.py",
    "tests/unit/test_s2_g2_al_localized_analysis_r2.py",
}


def changed(root, old, new):
    return [tuple(line.split("\t", 1)) for line in common.git(root, "diff", "--name-status", old, new).splitlines()]


def normalized(config):
    value = json.loads(json.dumps(config)); value["status"] = "implementation_pending_preregistration"; value["implementation_commit"] = "__FREEZE_IMPLEMENTATION_COMMIT__"; return value


def implementation_identity(root, commit):
    common.require(common.git(root, "show", "-s", "--format=%P", commit).split() == [BASE], "implementation parent differs")
    rows = changed(root, BASE, commit)
    common.require(all(status == "A" for status, _ in rows) and {path for _, path in rows} == IMPLEMENTATION_PATHS, "implementation diff differs")


def prereg_identity(root, commit):
    config = json.loads(common.git(root, "show", f"{commit}:{CONFIG_REL}"))
    implementation = config["implementation_commit"]
    common.require(common.git(root, "show", "-s", "--format=%P", commit).split() == [implementation], "prereg parent differs")
    common.require(changed(root, implementation, commit) == [("M", str(CONFIG_REL))], "prereg diff differs")
    implementation_config = json.loads(common.git(root, "show", f"{implementation}:{CONFIG_REL}"))
    common.require(normalized(config) == implementation_config, "prereg changed scientific content")
    implementation_identity(root, implementation)
    return config


def main() -> int:
    parser = argparse.ArgumentParser(); group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--implementation-only", action="store_true"); group.add_argument("--preregistered-only", action="store_true"); group.add_argument("--require-committed", action="store_true")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1]); args = parser.parse_args()
    root = args.project_root.resolve(); common.require(common.git(root, "status", "--porcelain") == "", "worktree must be clean"); head = common.git(root, "rev-parse", "HEAD")
    if args.implementation_only:
        config = analyzer.load_config(root); analyzer.validate_config(config); common.require(config["status"] == "implementation_pending_preregistration", "implementation status differs"); implementation_identity(root, head); result = {"status":"accepted_implementation"}
    elif args.preregistered_only:
        config = prereg_identity(root, head); analyzer.validate_config(config); common.require(config["status"] == "preregistered_analysis_only", "prereg status differs"); common.require(not (root/config["analysis_root"]).exists(), "analysis already exists"); result = {"status":"accepted_preregistration"}
    else:
        parents = common.git(root, "show", "-s", "--format=%P", head).split(); common.require(len(parents)==1, "evidence parent differs"); prereg=parents[0]; config=prereg_identity(root,prereg); prefix=config["analysis_root"]+"/"; rows=changed(root,prereg,head); common.require(all(status=="A" and path.startswith(prefix) for status,path in rows), "evidence diff differs"); common.require({path.removeprefix(prefix) for _,path in rows}==set(config["output_files"]), "output denominator differs"); summary,outputs=analyzer.build_analysis(root,config); target=root/config["analysis_root"]; common.require({str(p.relative_to(target)) for p in target.rglob("*") if p.is_file()}==set(outputs), "output tree differs"); [common.require((target/name).read_bytes()==data,f"output replay differs: {name}") for name,data in outputs.items()]; result={"status":"accepted_committed_evidence","scientific_status":summary["status"],"new_solver_run_count":0}
    print(json.dumps(result,sort_keys=True)); return 0


if __name__ == "__main__": raise SystemExit(main())
