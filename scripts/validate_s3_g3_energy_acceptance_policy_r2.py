#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,subprocess
from pathlib import Path
from analyze_s3_g3_energy_acceptance_policy_r2 import CONFIG,canonical,evaluate,git,load,render,require,sources
PATHS=[CONFIG.as_posix(),"docs/S3_G3_ENERGY_ACCEPTANCE_POLICY_R2_PROTOCOL.md","scripts/analyze_s3_g3_energy_acceptance_policy_r2.py","scripts/validate_s3_g3_energy_acceptance_policy_r2.py","tests/test_s3_g3_energy_acceptance_policy_r2.py"]
def norm(data):
 c=json.loads(data);c["status"]="implementation_pending_preregistration";c["implementation_commit"]="__FREEZE_IMPLEMENTATION_COMMIT__";return canonical(c)
def config_at(root,commit):return json.loads(subprocess.run(["git","show",f"{commit}:{CONFIG}"],cwd=root,check=True,stdout=subprocess.PIPE).stdout)
def impl(root,head):
 c=config_at(root,head);require(c["status"]=="implementation_pending_preregistration","implementation status differs");require(git(root,"show","-s","--format=%P",head).split()==[c["base_commit"]],"implementation parent differs");require(git(root,"diff","--name-status",c["base_commit"],head).splitlines()==[f"A\t{x}" for x in sorted(PATHS)],"implementation paths differ");return c
def chain(root,commit):
 ps=git(root,"show","-s","--format=%P",commit).split();require(len(ps)==1,"prereg parent differs");implementation=ps[0];c=config_at(root,commit);require(c["status"]=="preregistered_no_execution" and c["implementation_commit"]==implementation,"prereg identity differs");require(git(root,"diff","--name-status",implementation,commit).splitlines()==[f"M\t{CONFIG}"],"prereg diff differs");old=subprocess.run(["git","show",f"{implementation}:{CONFIG}"],cwd=root,check=True,stdout=subprocess.PIPE).stdout;new=subprocess.run(["git","show",f"{commit}:{CONFIG}"],cwd=root,check=True,stdout=subprocess.PIPE).stdout;require(norm(old)==norm(new),"prereg changed policy");impl(root,implementation);return c
def implementation(root):
 c=impl(root,git(root,"rev-parse","HEAD"));sources(root,c);_,rows,diag,ok=evaluate(root,c);require(len(rows)==13 and ok,"policy replay differs");require(not (root/c["execution"]["analysis_root"]).exists(),"analysis exists");return {"status":"accepted_implementation","rows":13}
def prereg(root):
 c=chain(root,git(root,"rev-parse","HEAD"));sources(root,c);_,rows,diag,ok=evaluate(root,c);require(len(rows)==13 and ok,"policy replay differs");require(not (root/c["execution"]["analysis_root"]).exists(),"analysis exists");return {"status":"accepted_preregistered","rows":13}
def committed(root):
 head=git(root,"rev-parse","HEAD");ps=git(root,"show","-s","--format=%P",head).split();require(len(ps)==1,"evidence parent differs");c=chain(root,ps[0]);prefix=c["execution"]["analysis_root"]+"/";changes=git(root,"diff","--name-status",ps[0],head).splitlines();require(len(changes)==3 and all(x.startswith("A\t"+prefix) for x in changes),"evidence diff differs");expected=render(root,c);dest=root/c["execution"]["analysis_root"];require(sorted(x.name for x in dest.iterdir())==sorted(expected),"output denominator differs")
 for n,b in expected.items():require((dest/n).read_bytes()==b,f"output differs {n}")
 summary=json.loads(expected["summary.json"]);require(summary["status"]=="accepted_s3_al_v100_coefficient_pilot_r2" and summary["g3_overall_closed"] is False and summary["s4_authorized"] is False,"disposition differs");return {"status":"accepted_committed_policy","coefficient_runs_accepted":13,"new_solver_run_count":0}
def main():
 p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True);g.add_argument("--implementation-only",action="store_true");g.add_argument("--preregistered-only",action="store_true");g.add_argument("--require-committed",action="store_true");p.add_argument("--project-root",type=Path,default=Path(__file__).resolve().parents[1]);a=p.parse_args();root=a.project_root.resolve();require(git(root,"status","--porcelain")=="","dirty");out=implementation(root) if a.implementation_only else prereg(root) if a.preregistered_only else committed(root);print(json.dumps(out,sort_keys=True));return 0
if __name__=="__main__":raise SystemExit(main())
