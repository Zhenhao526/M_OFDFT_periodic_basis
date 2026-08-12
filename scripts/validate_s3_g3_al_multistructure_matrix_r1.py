#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,subprocess
from pathlib import Path
CONFIG=Path("config/S3_g3_al_multistructure_matrix_r1.json")
PATHS=[CONFIG.as_posix(),"docs/S3_G3_AL_MULTISTRUCTURE_MATRIX_R1_PROTOCOL.md","scripts/validate_s3_g3_al_multistructure_matrix_r1.py","tests/test_s3_g3_al_multistructure_matrix_r1.py"]
def req(v,m):
 if not v: raise ValueError(m)
def git(root,*a): return subprocess.run(["git",*a],cwd=root,check=True,stdout=subprocess.PIPE,text=True).stdout.strip()
def load(root,commit="HEAD"): return json.loads(subprocess.run(["git","show",f"{commit}:{CONFIG}"],cwd=root,check=True,stdout=subprocess.PIPE).stdout)
def rows(c):
 out=[];n=101
 for s in c["structures"]:
  out.append({"experiment_id":f"S3-20260812-{n:03d}","structure_id":s["id"],"route":"full_grid_WT_reference","initialization":"uniform"});n+=1
  for init in c["coefficient_initializations"]: out.append({"experiment_id":f"S3-20260812-{n:03d}","structure_id":s["id"],"route":"coefficient_23_function","initialization":init});n+=1
 return out
def contract(c):
 req(len(c["structures"])==20 and len({x["id"] for x in c["structures"]})==20,"structure denominator differs")
 r=rows(c);req(len(r)==80 and r[0]["experiment_id"]==c["experiment_id_first"] and r[-1]["experiment_id"]==c["experiment_id_last"],"ID denominator differs")
 req(sum(x["route"]=="coefficient_23_function" for x in r)==60,"coefficient denominator differs")
 req(c["acceptance"]["energy_difference_abs_strict_lt_mev_per_atom"]==20.0,"energy policy differs")
 req(c["acceptance"]["projected_gradient_metric_hartree_strict_lt"]==1e-6 and c["acceptance"]["density_relative_l2_strict_lt"]==0.015,"unchanged gates differ")
 req(c["scope"]["g3_overall_closed"] is False and c["scope"]["s4_authorized"] is False and c["scope"]["mg_enabled"] is False,"scope differs")
 req(c["execution"]["formal_execution_authorized_by_this_matrix"] is False,"matrix unexpectedly authorizes execution")
 return r
def normalize(b):
 c=json.loads(b);c["status"]="implementation_pending_preregistration";c["implementation_commit"]="__FREEZE_IMPLEMENTATION_COMMIT__";return json.dumps(c,sort_keys=True,separators=(",",":"))
def implementation_at(root,head):
 c=load(root,head);req(c["status"]=="implementation_pending_preregistration","status differs");req(git(root,"show","-s","--format=%P",head).split()==[c["base_commit"]],"parent differs");req(git(root,"diff","--name-status",c["base_commit"],head).splitlines()==[f"A\t{x}" for x in sorted(PATHS)],"paths differ");return c
def implementation(root): return implementation_at(root,git(root,"rev-parse","HEAD"))
def prereg(root):
 head=git(root,"rev-parse","HEAD");p=git(root,"show","-s","--format=%P",head).split();req(len(p)==1,"prereg parent differs");c=load(root);req(c["status"]=="preregistered_matrix_no_execution" and c["implementation_commit"]==p[0],"identity differs");req(git(root,"diff","--name-status",p[0],head).splitlines()==[f"M\t{CONFIG}"],"prereg paths differ");old=subprocess.run(["git","show",f"{p[0]}:{CONFIG}"],cwd=root,check=True,stdout=subprocess.PIPE).stdout;new=subprocess.run(["git","show",f"{head}:{CONFIG}"],cwd=root,check=True,stdout=subprocess.PIPE).stdout;req(normalize(old)==normalize(new),"matrix content changed");implementation_at(root,p[0]);return c
def main():
 p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True);g.add_argument("--implementation-only",action="store_true");g.add_argument("--preregistered-only",action="store_true");p.add_argument("--project-root",type=Path,default=Path(__file__).resolve().parents[1]);a=p.parse_args();root=a.project_root.resolve();req(git(root,"status","--porcelain")=="","dirty");c=implementation(root) if a.implementation_only else prereg(root);r=contract(c);print(json.dumps({"status":"accepted_matrix_preregistered" if a.preregistered_only else "accepted_matrix_implementation","structures":20,"cases":len(r),"formal_execution_authorized":False},sort_keys=True));return 0
if __name__=="__main__": raise SystemExit(main())
