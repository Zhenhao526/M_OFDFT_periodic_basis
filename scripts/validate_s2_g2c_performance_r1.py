#!/usr/bin/env python3
import argparse,json
from pathlib import Path
from s2_g2c_performance_common_r1 import BASE_COMMIT,CONFIG_REL,IMPLEMENTATION_PATHS,git,load,render,require,source_exact,validate_config
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
 p=argparse.ArgumentParser(); p.add_argument("--project-root",type=Path,default=Path(__file__).resolve().parents[1]); g=p.add_mutually_exclusive_group(required=True); g.add_argument("--implementation-only",action="store_true"); g.add_argument("--preregistered-only",action="store_true"); g.add_argument("--require-committed",action="store_true"); a=p.parse_args(); r=a.project_root.resolve(); require(git(r,"status","--porcelain")=="","dirty"); h=git(r,"rev-parse","HEAD"); c=load(r); validate_config(c); source_exact(r,c)
 if a.implementation_only: impl(r,h); require(c["status"]=="implementation_pending_preregistration","status differs"); require(not Path(c["execution"]["state_root"]).exists() and not (r/c["execution"]["analysis_root"]).exists(),"execution artifacts exist"); mode="implementation-only"; status=None
 elif a.preregistered_only: pre(r,h); require(not Path(c["execution"]["state_root"]).exists() and not (r/c["execution"]["analysis_root"]).exists(),"execution artifacts exist"); mode="preregistered-only"; status=None
 else:
  ps=git(r,"show","-s","--format=%P",h).split(); require(len(ps)==1,"evidence parent differs"); pr=ps[0]; pre(r,pr); expected={f"{c['execution']['analysis_root']}/{n}":"A" for n in c["output_files"]}; require(diff(r,pr,h)==expected,"evidence diff differs"); raw=json.loads((r/c["execution"]["analysis_root"]/"raw_runs.json").read_text())["runs"]; out=render(c,raw); target=r/c["execution"]["analysis_root"]; require(sorted(x.name for x in target.iterdir())==sorted(out),"output denominator differs"); [require((target/n).read_bytes()==b,f"output differs: {n}") for n,b in out.items()]; status=json.loads(out["summary.json"])["status"]; mode="require-committed"
 print(json.dumps({"status":"accepted","mode":mode,"scientific_disposition":status,"new_solver_run_count":0},sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
