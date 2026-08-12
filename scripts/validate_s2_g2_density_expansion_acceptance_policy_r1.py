#!/usr/bin/env python3
import argparse,json
from pathlib import Path
from s2_g2_density_expansion_policy_common_r1 import BASE_COMMIT,CONFIG_REL,IMPLEMENTATION_PATHS,git,load,render,require,validate_config
def diff(r,a,b):
 d={}
 for line in git(r,"diff","--name-status",a,b).splitlines():
  if line: s,p=line.split("\t",1); d[p]=s
 return d
def impl(r,h): require(git(r,"show","-s","--format=%P",h).split()==[BASE_COMMIT],"implementation parent differs"); require(diff(r,BASE_COMMIT,h)=={p:"A" for p in IMPLEMENTATION_PATHS},"implementation diff differs")
def norm(c): c=json.loads(json.dumps(c)); c["status"]="implementation_pending_preregistration"; c["implementation_commit"]="__FREEZE_IMPLEMENTATION_COMMIT__"; return c
def pre(r,h):
 ps=git(r,"show","-s","--format=%P",h).split(); require(len(ps)==1,"prereg parent differs"); i=ps[0]; impl(r,i); require(diff(r,i,h)=={str(CONFIG_REL):"M"},"prereg diff differs"); a=json.loads(git(r,"show",f"{h}:{CONFIG_REL}")); b=json.loads(git(r,"show",f"{i}:{CONFIG_REL}")); require(a["status"]=="preregistered_no_execution" and a["implementation_commit"]==i and norm(a)==b,"prereg content differs"); return i
def main():
 p=argparse.ArgumentParser(); p.add_argument("--project-root",type=Path,default=Path(__file__).resolve().parents[1]); g=p.add_mutually_exclusive_group(required=True); g.add_argument("--implementation-only",action="store_true"); g.add_argument("--preregistered-only",action="store_true"); g.add_argument("--require-committed",action="store_true"); a=p.parse_args(); r=a.project_root.resolve(); require(git(r,"status","--porcelain")=="","dirty"); h=git(r,"rev-parse","HEAD"); c=load(r); validate_config(c)
 if a.implementation_only: impl(r,h); require(c["status"]=="implementation_pending_preregistration","status differs"); require(not (r/c["output"]["root"]).exists(),"output exists"); mode="implementation-only"
 elif a.preregistered_only: pre(r,h); require(not (r/c["output"]["root"]).exists(),"output exists"); render(r,c); mode="preregistered-only"
 else:
  ps=git(r,"show","-s","--format=%P",h).split(); require(len(ps)==1,"evidence parent differs"); pr=ps[0]; pre(r,pr); expected={f"{c['output']['root']}/{n}":"A" for n in c["output"]["files"]}; require(diff(r,pr,h)==expected,"evidence diff differs"); out=render(r,c); root=r/c["output"]["root"]; require(sorted(x.name for x in root.iterdir())==sorted(out),"output denominator differs"); [require((root/n).read_bytes()==b,f"output differs: {n}") for n,b in out.items()]; mode="require-committed"
 print(json.dumps({"status":"accepted","mode":mode,"scientific_disposition":"accepted_density_expansion_candidate" if not a.implementation_only else None,"new_solver_run_count":0},sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
