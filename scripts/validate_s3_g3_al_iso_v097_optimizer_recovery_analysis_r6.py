#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,subprocess
from pathlib import Path
import analyze_s3_g3_al_iso_v097_optimizer_recovery_analysis_r6 as a
def changes(root,x,y):
 out=a.git(root,"diff","--name-status",x,y);return {} if not out else {z.split("\t",1)[1]:z.split("\t",1)[0] for z in out.splitlines()}
def parents(root,x):return a.git(root,"show","-s","--format=%P",x).split()
def norm(b):
 x=json.loads(b);x["status"]="implementation_pending_preregistration";x["implementation_commit"]="__FREEZE_IMPLEMENTATION_COMMIT__";return a.canonical(x)
def implementation(root,x):
 a.require(parents(root,x)==[a.BASE],"implementation parent differs");a.require(changes(root,a.BASE,x)=={p:"A" for p in a.IMPL_PATHS},"implementation paths differ");c=json.loads(subprocess.run(["git","show",f"{x}:{a.CONFIG}"],cwd=root,check=True,stdout=subprocess.PIPE).stdout);a.validate_config(c);a.require(c["status"]=="implementation_pending_preregistration","implementation status differs");return c
def registered(root,x):
 ps=parents(root,x);a.require(len(ps)==1,"prereg parent differs");impl=ps[0];a.require(changes(root,impl,x)=={str(a.CONFIG):"M"},"prereg paths differ");old=subprocess.run(["git","show",f"{impl}:{a.CONFIG}"],cwd=root,check=True,stdout=subprocess.PIPE).stdout;new=subprocess.run(["git","show",f"{x}:{a.CONFIG}"],cwd=root,check=True,stdout=subprocess.PIPE).stdout;a.require(norm(old)==norm(new),"prereg content differs");c=json.loads(new);a.require(c["status"]=="preregistered_no_execution" and c["implementation_commit"]==impl,"prereg identity differs");implementation(root,impl);return c
def main():
 p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True);g.add_argument("--implementation-only",action="store_true");g.add_argument("--preregistered-only",action="store_true");g.add_argument("--require-committed",action="store_true");p.add_argument("--project-root",type=Path,default=Path(__file__).resolve().parents[1]);x=p.parse_args();root=x.project_root.resolve();a.require(a.git(root,"status","--porcelain")=="","dirty");head=a.git(root,"rev-parse","HEAD")
 if x.implementation_only:c=implementation(root,head);a.require(not (root/c["analysis_root"]).exists(),"analysis exists");out={"status":"accepted_implementation"}
 elif x.preregistered_only:c=registered(root,head);a.require(not (root/c["analysis_root"]).exists(),"analysis exists");out={"status":"accepted_preregistered"}
 else:
  ps=parents(root,head);a.require(len(ps)==1,"evidence parent differs");pr=ps[0];c=registered(root,pr);expected={str(Path(c["analysis_root"])/n):"A" for n in a.OUTPUTS};a.require(changes(root,pr,head)==expected,"evidence paths differ");rendered=a.build(root,c)
  for n,b in rendered.items():a.require((root/c["analysis_root"]/n).read_bytes()==b,f"output differs: {n}")
  s=json.loads(rendered["summary.json"]);a.require(s["evidence_valid"] is True and s["g3_overall_closed"] is False and s["s4_authorized"] is False and s["new_solver_run_count"]==0,"disposition differs");out={"status":"accepted_committed_analysis_r6","scientific_gate_accepted":s["scientific_gate_accepted"]}
 print(json.dumps(out,sort_keys=True))
if __name__=="__main__":main()
