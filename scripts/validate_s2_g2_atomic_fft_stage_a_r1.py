#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path
from s2_g2_atomic_fft_stage_a_common_r1 import BASE_COMMIT, CONFIG_REL, IMPLEMENTATION_PATHS, build_analysis, git, load_config, render_outputs, require, validate_config

def changes(root,p,h):
    out={}
    for line in git(root,"diff","--name-status",p,h).splitlines():
        if line: s,path=line.split("\t",1); out[path]=s
    return out
def implementation(root,head):
    require(git(root,"show","-s","--format=%P",head).split()==[BASE_COMMIT],"implementation parent differs"); c=changes(root,BASE_COMMIT,head); require(set(c)==IMPLEMENTATION_PATHS and all(v=="A" for v in c.values()),"implementation diff differs")
def normalize(c):
    c=json.loads(json.dumps(c)); c["status"]="implementation_pending_preregistration"; c["implementation_commit"]="__FREEZE_IMPLEMENTATION_COMMIT__"; return c
def prereg(root,head):
    ps=git(root,"show","-s","--format=%P",head).split(); require(len(ps)==1,"prereg parent count differs"); impl=ps[0]; implementation(root,impl); require(changes(root,impl,head)=={str(CONFIG_REL):"M"},"prereg diff differs"); reg=json.loads(git(root,"show",f"{head}:{CONFIG_REL}")); raw=json.loads(git(root,"show",f"{impl}:{CONFIG_REL}")); require(reg["status"]=="preregistered_no_execution" and reg["implementation_commit"]==impl and normalize(reg)==raw,"prereg content differs"); return impl
def main():
    p=argparse.ArgumentParser(); p.add_argument("--project-root",type=Path,default=Path(__file__).resolve().parents[1]); m=p.add_mutually_exclusive_group(required=True); m.add_argument("--implementation-only",action="store_true"); m.add_argument("--preregistered-only",action="store_true"); m.add_argument("--require-committed",action="store_true"); a=p.parse_args(); root=a.project_root.resolve(); require(git(root,"status","--porcelain")=="","worktree dirty"); head=git(root,"rev-parse","HEAD"); cfg=load_config(root); validate_config(cfg); analysis=None
    if a.implementation_only: implementation(root,head); require(cfg["status"]=="implementation_pending_preregistration","status differs"); require(not (root/cfg["output"]["root"]).exists(),"output exists"); mode="implementation-only"
    elif a.preregistered_only: prereg(root,head); require(not (root/cfg["output"]["root"]).exists(),"output exists"); analysis=build_analysis(root,cfg); mode="preregistered-only"
    else:
        ps=git(root,"show","-s","--format=%P",head).split(); require(len(ps)==1,"evidence parent differs"); pre=ps[0]; prereg(root,pre); expected={f"{cfg['output']['root']}/{n}":"A" for n in cfg["output"]["files"]}; require(changes(root,pre,head)==expected,"evidence diff differs"); analysis=build_analysis(root,cfg); rendered=render_outputs(analysis); out=root/cfg["output"]["root"]; require(sorted(p.name for p in out.iterdir())==sorted(rendered),"output denominator differs"); [require((out/n).read_bytes()==b,f"output differs: {n}") for n,b in rendered.items()]; mode="require-committed"
    print(json.dumps({"status":"accepted","mode":mode,"scientific_disposition":analysis["summary"]["status"] if analysis else None,"promoted":analysis["summary"]["promoted_to_stage_B"] if analysis else None,"new_solver_run_count":0},sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
