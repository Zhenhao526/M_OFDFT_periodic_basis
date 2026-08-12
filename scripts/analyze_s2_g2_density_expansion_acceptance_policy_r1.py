#!/usr/bin/env python3
import argparse,json
from pathlib import Path
from s2_g2_density_expansion_policy_common_r1 import git,load,render,require
def main():
 p=argparse.ArgumentParser(); p.add_argument("--project-root",type=Path,default=Path(__file__).resolve().parents[1]); g=p.add_mutually_exclusive_group(required=True); g.add_argument("--dry-run",action="store_true"); g.add_argument("--write",action="store_true"); a=p.parse_args(); r=a.project_root.resolve(); require(git(r,"status","--porcelain")=="","dirty"); c=load(r); require(c["status"]=="preregistered_no_execution","not preregistered"); require(git(r,"show","-s","--format=%P",git(r,"rev-parse","HEAD")).split()==[c["implementation_commit"]],"wrong prereg HEAD"); out=render(r,c); target=r/c["output"]["root"]; require(not target.exists(),"output exists")
 if a.write: target.mkdir(parents=True); [(target/n).write_bytes(out[n]) for n in c["output"]["files"]]
 print(json.dumps({"status":"accepted_density_expansion_candidate","new_solver_run_count":0,"mode":"write" if a.write else "dry-run"},sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
