#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,subprocess
from pathlib import Path
from s3_g3_al_multistructure_common_r1 import CONFIG_REL,IMPLEMENTATION_PATHS,canonical,git,load,require,source_exact,validate_config
from collect_s3_g3_al_multistructure_execution_r1 import replay_outputs
BASE="5abf8263abd17d29f016f4fec6aaea0ead3371aa"
def parents(root,c):return git(root,"show","-s","--format=%P",c).split()
def changes(root,a,b):
 out=git(root,"-c","core.quotepath=false","diff","--name-status",a,b);return {} if not out else {x.split('\t',1)[1]:x.split('\t',1)[0] for x in out.splitlines()}
def norm(data):
 x=json.loads(data);x["status"]="implementation_pending_preregistration";x["implementation_commit"]="__FREEZE_IMPLEMENTATION_COMMIT__";return canonical(x)
def implementation(root,commit):
 require(parents(root,commit)==[BASE],"implementation parent differs");require(changes(root,BASE,commit)=={x:"A" for x in IMPLEMENTATION_PATHS},"implementation paths differ");c=json.loads(subprocess.run(["git","show",f"{commit}:{CONFIG_REL}"],cwd=root,check=True,stdout=subprocess.PIPE).stdout);require(c["status"]=="implementation_pending_preregistration","implementation status differs");return c
def registered(root,commit):
 ps=parents(root,commit);require(len(ps)==1,"prereg parent differs");impl=ps[0];require(changes(root,impl,commit)=={str(CONFIG_REL):"M"},"prereg paths differ");new=subprocess.run(["git","show",f"{commit}:{CONFIG_REL}"],cwd=root,check=True,stdout=subprocess.PIPE).stdout;old=subprocess.run(["git","show",f"{impl}:{CONFIG_REL}"],cwd=root,check=True,stdout=subprocess.PIPE).stdout;require(norm(new)==norm(old),"prereg content differs");c=json.loads(new);require(c["status"]=="preregistered_no_execution" and c["implementation_commit"]==impl,"prereg identity differs");implementation(root,impl);return c
def main():
 p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True);g.add_argument("--implementation-only",action="store_true");g.add_argument("--preregistered-only",action="store_true");g.add_argument("--require-committed",action="store_true");p.add_argument("--project-root",type=Path,default=Path(__file__).resolve().parents[1]);a=p.parse_args();root=a.project_root.resolve();require(git(root,"status","--porcelain")=="","dirty");head=git(root,"rev-parse","HEAD")
 if a.implementation_only:c=implementation(root,head);resolved=load(root);validate_config(root,resolved);source_exact(root,resolved);require(not Path(resolved["execution"]["state_root"]).exists(),"state exists");out={"status":"accepted_implementation","case_count":80}
 elif a.preregistered_only:c=registered(root,head);resolved=load(root);validate_config(root,resolved);source_exact(root,resolved);require(not Path(resolved["execution"]["state_root"]).exists(),"state exists");out={"status":"accepted_preregistered","case_count":80}
 else:
  ps=parents(root,head);require(len(ps)==1,"evidence parent differs");prereg=ps[0];c=registered(root,prereg);resolved=load(root);expected={str(Path(resolved["execution"]["analysis_root"])/n):"A" for n in resolved["output_files"]};require(changes(root,prereg,head)==expected,"evidence paths differ");rendered=replay_outputs(root,resolved,prereg)
  for n,b in rendered.items():require((root/resolved["execution"]["analysis_root"]/n).read_bytes()==b,f"analysis differs: {n}")
  s=json.loads(rendered["summary.json"]);require(s["evidence_valid"] is True and s["g3_overall_closed"] is False and s["s4_authorized"] is False,"disposition differs");out={"status":"accepted_committed_evidence","scientific_gate_accepted":s["scientific_gate_accepted"],"case_count":80}
 print(json.dumps(out,sort_keys=True));return 0
if __name__=="__main__":raise SystemExit(main())
