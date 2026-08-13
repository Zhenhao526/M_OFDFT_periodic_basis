#!/usr/bin/env python3
from __future__ import annotations
import argparse,hashlib,json,subprocess
from pathlib import Path
import s3_g3_al_iso_v097_optimizer_recovery_common_r5 as r5c
import collect_s3_g3_al_iso_v097_optimizer_recovery_r5 as r5a
import validate_s3_g3_al_iso_v097_optimizer_recovery_r5 as r5v

CONFIG=Path("config/S3_g3_al_iso_v097_optimizer_recovery_analysis_r6.json")
BASE="631b6f6750b8f960f8f916fb962ba771ceef54a7"
OUTPUTS=("README.md","runs.json","gates.tsv","summary.json")
IMPL_PATHS=(str(CONFIG),"docs/S3_G3_AL_ISO_V097_OPTIMIZER_RECOVERY_ANALYSIS_R6_PROTOCOL.md","scripts/analyze_s3_g3_al_iso_v097_optimizer_recovery_analysis_r6.py","scripts/validate_s3_g3_al_iso_v097_optimizer_recovery_analysis_r6.py","tests/test_s3_g3_al_iso_v097_optimizer_recovery_analysis_r6.py")

def require(x,msg):
 if not x: raise ValueError(msg)
def git(root,*args):return subprocess.run(["git",*args],cwd=root,check=True,stdout=subprocess.PIPE,text=True).stdout.strip()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def canonical(x):return json.dumps(x,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()+b"\n"
def load(root):return json.loads((root/CONFIG).read_text())
def validate_config(c):
 require(set(c)=={"schema_version","protocol_revision","status","implementation_commit","base_closure_commit","r5_preregistration_commit","r5_implementation_commit","r5_closure_path","r5_closure_sha256","r5_invocation_path","r5_invocation_sha256","r5_terminal_sha256","r5_session_sha256","r5_state_file_count","r5_state_total_bytes","r5_state_inventory_sha256","fixed_structure_id","new_solver_run_count","analysis_root","output_files"},"config schema differs")
 require(c["schema_version"]==1 and c["base_closure_commit"]==BASE and c["fixed_structure_id"]=="iso_v097" and c["new_solver_run_count"]==0,"config identity differs")
 require(tuple(c["output_files"])==OUTPUTS,"outputs differ")
def state_inventory(state):
 rows=[];total=0
 for p in sorted(x for x in state.rglob("*") if x.is_file()):
  b=p.read_bytes();total+=len(b);rows.append({"path":str(p.relative_to(state)),"size_bytes":len(b),"sha256":hashlib.sha256(b).hexdigest()})
 return len(rows),total,hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(",",":")).encode()).hexdigest()
def source(root,c):
 require(git(root,"merge-base","--is-ancestor",c["r5_preregistration_commit"],"HEAD")=="","R5 prereg not ancestor")
 require(r5v.registered(root,c["r5_preregistration_commit"])["implementation_commit"]==c["r5_implementation_commit"],"R5 registration differs")
 closure=root/c["r5_closure_path"];require(sha(closure)==c["r5_closure_sha256"],"closure SHA differs");require(subprocess.run(["git","show",f"{BASE}:{c['r5_closure_path']}"],cwd=root,check=True,stdout=subprocess.PIPE).stdout==closure.read_bytes(),"closure Git bytes differ")
 invocation=root/c["r5_invocation_path"];require(sha(invocation)==c["r5_invocation_sha256"],"invocation SHA differs");require(subprocess.run(["git","show",f"{BASE}:{c['r5_invocation_path']}"],cwd=root,check=True,stdout=subprocess.PIPE).stdout==invocation.read_bytes(),"invocation Git bytes differ");inv=json.loads(invocation.read_text());require(inv["status"]=="execution_accepted_analyzer_render_false_negative" and inv["exception"]=="KeyError: 'structure_id'" and inv["analysis_root_absent_after_failure"] is True,"invocation disposition differs")
 cl=json.loads(closure.read_text());require(cl["status"]=="analysis_false_negative_closed_no_solver_retry" and cl["analysis_output_absent"] is True and cl["failure"]["second_false_negative_found"] is False,"closure disposition differs")
 cfg=r5c.load(root);r5c.validate_config(root,cfg);r5c.source_exact(root,cfg);execution_root=Path(cfg["execution"]["execution_worktree_root"]);require(execution_root.is_dir(),"R5 execution root absent");require(git(execution_root,"rev-parse","HEAD")==c["r5_preregistration_commit"] and git(execution_root,"status","--porcelain")=="","R5 execution worktree identity differs")
 for rel in ("scripts/s3_g3_al_iso_v097_optimizer_recovery_common_r5.py","scripts/run_s3_g3_al_iso_v097_optimizer_recovery_r5.py","scripts/collect_s3_g3_al_iso_v097_optimizer_recovery_r5.py","config/S3_g3_al_iso_v097_optimizer_recovery_r5.json"):
  require((execution_root/rel).read_bytes()==subprocess.run(["git","show",f"{c['r5_preregistration_commit']}:{rel}"],cwd=root,check=True,stdout=subprocess.PIPE).stdout,f"R5 execution blob differs: {rel}")
 state=Path(cfg["execution"]["state_root"]);require(sha(state/"terminal.json")==c["r5_terminal_sha256"] and sha(state/"session.json")==c["r5_session_sha256"],"state identity differs");require(state_inventory(state)==(c["r5_state_file_count"],c["r5_state_total_bytes"],c["r5_state_inventory_sha256"]),"state inventory differs")
 return cfg,state,execution_root
def build(root,c):
 cfg,state,execution_root=source(root,c);t=json.loads((state/"terminal.json").read_text());r5a.validate_terminal(cfg,t,False);r5a.validate_session(execution_root,cfg,state,t,c["r5_preregistration_commit"]);r5a.verify_tree(state,t,False);runs=r5a.load_success(execution_root,cfg,state,t);summary=r5c.summarize(cfg,runs);require(summary["structure_id"]==c["fixed_structure_id"],"structure identity differs")
 cols=["experiment_id","structure_id","initialization","reference_energy_difference_mev_per_atom","density_relative_l2","three_initialization_energy_spread_mev_per_atom","accepted"]
 table=[]
 for row in summary["coefficient_runs"]:
  x=dict(row);x["structure_id"]=summary["structure_id"];table.append(x)
 lines=["\t".join(cols)]+["\t".join(str(x[k]) for k in cols) for x in table]
 summary=dict(summary);summary["analysis_revision"]=c["protocol_revision"];summary["source_r5_preregistration_commit"]=c["r5_preregistration_commit"];summary["new_solver_run_count"]=0;summary["r5_serialization_false_negative_corrected"]=True
 readme=(f"# S3/G3 Al iso_v097 optimizer recovery analysis-only R6\n\nStatus: `{summary['status']}`. R6 replayed all four immutable R5 runs and corrected only the fixed structure identity in the gate-table serialization. New solver runs: `0`. G3 remains open; S4 is unauthorized.\n").encode()
 return {"README.md":readme,"runs.json":canonical({"schema_version":1,"protocol_revision":c["protocol_revision"],"source_protocol_revision":cfg["protocol_revision"],"runs":runs}),"gates.tsv":("\n".join(lines)+"\n").encode(),"summary.json":canonical(summary)}
def main():
 p=argparse.ArgumentParser();p.add_argument("--project-root",type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument("--dry-run",action="store_true");a=p.parse_args();root=a.project_root.resolve();c=load(root);validate_config(c);out=build(root,c)
 if not a.dry_run:
  target=root/c["analysis_root"];require(not target.exists(),"analysis exists");target.mkdir(parents=True)
  for n in OUTPUTS:(target/n).write_bytes(out[n])
 s=json.loads(out["summary.json"]);print(json.dumps({"status":s["status"],"scientific_gate_accepted":s["scientific_gate_accepted"],"new_solver_run_count":0,"output_files":4},sort_keys=True))
if __name__=="__main__":main()
