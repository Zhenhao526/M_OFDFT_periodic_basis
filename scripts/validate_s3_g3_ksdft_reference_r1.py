#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,subprocess,tempfile
from pathlib import Path
from collect_s3_g3_ksdft_reference_r1 import render
from s3_g3_ksdft_reference_common_r1 import CONFIG_REL,git,implementation_identity,load,registered_chain,require,source_exact

def validate_inputs(root,c):
    from generate_s3_g3_ksdft_reference_r1 import generate
    require(generate(root)==3,"input generation differs")
def validate_implementation(root):
    head=git(root,"rev-parse","HEAD");c=implementation_identity(root,head);source_exact(root,c);validate_inputs(root,c);require(not Path(c["execution"]["state_root"]).exists(),"state exists");require(not (root/c["execution"]["analysis_root"]).exists(),"analysis exists");return {"status":"accepted_implementation","case_count":3}
def validate_preregistered(root):
    head=git(root,"rev-parse","HEAD");_,c=registered_chain(root,head);source_exact(root,c);validate_inputs(root,c);require(not Path(c["execution"]["state_root"]).exists(),"state exists");require(not (root/c["execution"]["analysis_root"]).exists(),"analysis exists");return {"status":"accepted_preregistered","case_count":3}
def validate_committed(root):
    head=git(root,"rev-parse","HEAD");parents=git(root,"show","-s","--format=%P",head).split();require(len(parents)==1,"evidence parent count differs");_,c=registered_chain(root,parents[0]);changes=git(root,"diff","--name-status",parents[0],head).splitlines();prefix=c["execution"]["analysis_root"]+"/";require(len(changes)==4 and all(x.startswith("A\t"+prefix) for x in changes),"evidence diff differs");expected=render(root,c);dest=root/c["execution"]["analysis_root"];require(sorted(p.name for p in dest.iterdir())==sorted(expected),"output denominator differs")
    for name,data in expected.items():require((dest/name).read_bytes()==data,f"output differs: {name}")
    summary=json.loads(expected["summary.json"]);require(summary["evidence_valid"] is True and summary["g3_overall_closed"] is False and summary["s4_authorized"] is False,"disposition differs");return {"status":"accepted_committed_evidence","scientific_gate_accepted":summary["scientific_gate_accepted"],"case_count":3}
def main():
 p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True);g.add_argument("--implementation-only",action="store_true");g.add_argument("--preregistered-only",action="store_true");g.add_argument("--require-committed",action="store_true");p.add_argument("--project-root",type=Path,default=Path(__file__).resolve().parents[1]);a=p.parse_args();root=a.project_root.resolve();require(git(root,"status","--porcelain")=="","tree dirty");out=validate_implementation(root) if a.implementation_only else validate_preregistered(root) if a.preregistered_only else validate_committed(root);print(json.dumps(out,sort_keys=True));return 0
if __name__=="__main__":raise SystemExit(main())
