#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,subprocess,tempfile
from pathlib import Path
from s3_g3_wt_coefficient_common_r1 import BASE_COMMIT,CONFIG_REL,IMPLEMENTATION_PATHS,canonical,git,load,render,replay_result,require,sha_path,source_exact,validate_config,validate_runtime
from collect_s3_g3_wt_coefficient_pilot_r1 import failure_outputs,load_success_runs,validate_session,validate_terminal_denominator,verify_regular_tree

def parents(root,commit): return git(root,"show","-s","--format=%P",commit).split()
def changed(root,a,b):
    out=git(root,"-c","core.quotepath=false","diff","--name-status",a,b)
    return {} if not out else {line.split("\t",1)[1]:line.split("\t",1)[0] for line in out.splitlines()}
def implementation_identity(root,commit):
    require(parents(root,commit)==[BASE_COMMIT],"implementation parent differs")
    expected={p:("M" if p==str(Path("docs/M_OFDFT_项目进度与交接.md")) else "A") for p in IMPLEMENTATION_PATHS}
    require(changed(root,BASE_COMMIT,commit)==expected,"implementation path denominator differs")
def normalized_config(data):
    obj=json.loads(data); obj["status"]="implementation_pending_preregistration"; obj["implementation_commit"]="__FREEZE_IMPLEMENTATION_COMMIT__"; return canonical(obj)
def registered_chain(root,prereg):
    ps=parents(root,prereg); require(len(ps)==1,"prereg parent count differs"); implementation=ps[0]; implementation_identity(root,implementation)
    require(changed(root,implementation,prereg)=={str(CONFIG_REL):"M"},"prereg diff differs")
    current=subprocess.run(["git","show",f"{prereg}:{CONFIG_REL}"],cwd=root,stdout=subprocess.PIPE,check=True).stdout; old=subprocess.run(["git","show",f"{implementation}:{CONFIG_REL}"],cwd=root,stdout=subprocess.PIPE,check=True).stdout
    require(normalized_config(current)==old,"prereg contains non-registration change")
    c=json.loads(current); require(c["status"]=="preregistered_no_execution" and c["implementation_commit"]==implementation,"prereg identity differs"); validate_config(c); return implementation,c
def replay_outputs(root,c,runner_commit):
    state=Path(c["execution"]["state_root"]); terminal=json.loads((state/"terminal.json").read_text()); failed=terminal["status"]=="failed_no_retry"; validate_terminal_denominator(c,terminal,failed); validate_session(c,state,terminal,runner_commit,sha_path(root/CONFIG_REL)); verify_regular_tree(state,terminal,failed)
    runtime=validate_runtime(root,c)
    return failure_outputs(root,c,terminal,state,runtime) if failed else render(c,load_success_runs(root,c,state,terminal,runtime))
def validate_implementation(root):
    head=git(root,"rev-parse","HEAD"); implementation_identity(root,head); c=load(root); validate_config(c); source_exact(root,c); require(c["status"]=="implementation_pending_preregistration","implementation status differs"); require(not Path(c["execution"]["state_root"]).exists(),"formal state exists"); require(not (root/c["execution"]["analysis_root"]).exists(),"analysis exists"); return {"status":"accepted_implementation","case_count":29}
def validate_preregistered(root):
    head=git(root,"rev-parse","HEAD"); _,c=registered_chain(root,head); source_exact(root,c); require(not Path(c["execution"]["state_root"]).exists(),"formal state exists"); require(not (root/c["execution"]["analysis_root"]).exists(),"analysis exists"); return {"status":"accepted_preregistered","case_count":29}
def validate_committed(root):
    head=git(root,"rev-parse","HEAD"); ps=parents(root,head); require(len(ps)==1,"evidence parent count differs"); prereg=ps[0]; _,c=registered_chain(root,prereg); source_exact(root,c); expected={str(Path(c["execution"]["analysis_root"])/name):"A" for name in c["output_files"]}; require(changed(root,prereg,head)==expected,"evidence diff differs"); out=replay_outputs(root,c,prereg)
    for name,data in out.items(): require((root/c["execution"]["analysis_root"]/name).read_bytes()==data,"committed analysis differs")
    summary=json.loads(out["summary.json"]); require(summary["evidence_valid"] is True and summary["s4_authorized"] is False,"disposition differs"); return {"status":"accepted_committed_evidence","scientific_gate_accepted":summary["scientific_gate_accepted"],"case_count":29}
def main():
    p=argparse.ArgumentParser(); g=p.add_mutually_exclusive_group(required=True); g.add_argument("--implementation-only",action="store_true"); g.add_argument("--preregistered-only",action="store_true"); g.add_argument("--require-committed",action="store_true"); p.add_argument("--project-root",type=Path,default=Path(__file__).resolve().parents[1]); a=p.parse_args(); root=a.project_root.resolve(); require(git(root,"status","--porcelain")=="","tree dirty"); result=validate_implementation(root) if a.implementation_only else validate_preregistered(root) if a.preregistered_only else validate_committed(root); print(json.dumps(result,sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
