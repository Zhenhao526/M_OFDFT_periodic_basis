#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path
from s2_g2_atomic_fft_stage_a_common_r1 import build_analysis, git, load_config, render_outputs, require

def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument("--project-root",type=Path,default=Path(__file__).resolve().parents[1]); m=p.add_mutually_exclusive_group(required=True); m.add_argument("--dry-run",action="store_true"); m.add_argument("--write",action="store_true"); a=p.parse_args(); root=a.project_root.resolve()
    require(git(root,"status","--porcelain")=="","worktree must be clean"); cfg=load_config(root); require(cfg["status"]=="preregistered_no_execution","config is not preregistered"); require(git(root,"show","-s","--format=%P",git(root,"rev-parse","HEAD")).split()==[cfg["implementation_commit"]],"HEAD is not config-only preregistration")
    analysis=build_analysis(root,cfg); outputs=render_outputs(analysis); out=root/cfg["output"]["root"]; require(not out.exists(),"output exists")
    if a.write:
        out.mkdir(parents=True); [ (out/name).write_bytes(outputs[name]) for name in cfg["output"]["files"] ]
    print(json.dumps({"status":analysis["summary"]["status"],"accepted_candidate_count":analysis["summary"]["accepted_candidate_count"],"promoted_to_stage_B":analysis["summary"]["promoted_to_stage_B"],"new_solver_run_count":0,"mode":"write" if a.write else "dry-run"},sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
