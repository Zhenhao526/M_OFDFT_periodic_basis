#!/usr/bin/env python3
from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np
from s3_g3_al_iso_v097_optimizer_recovery_common_r4 import CONFIG_REL,canonical,git,load,render,replay_result,require,resource_preflight,sha_path,source_exact,validate_config,validate_runtime,write_exclusive
from run_s3_g3_al_iso_v097_optimizer_recovery_r4 import command,environment

def validate_terminal(c,t,failed):
 ids=[x["experiment_id"] for x in c["formal_cases"]];attempted=t["attempted_ids"];accepted=t["accepted_ids"]
 require(attempted==ids[:len(attempted)] and t["unattempted_ids"]==ids[len(attempted):],"terminal ID order differs");require(t["attempted"]==len(attempted) and t["accepted"]==len(accepted) and t["retried"]==0,"terminal counts differ")
 require(set(t["attempt_sha256"])==set(attempted) and set(t["runner_return_sha256"])==set(attempted) and set(t["run_inventory"])==set(attempted) and set(t["result_sha256"])==set(accepted),"terminal maps differ")
 if failed:require(t["status"]=="failed_no_retry" and accepted==attempted[:-1] and t["failed"]==1 and t["missing"]==0 and t["skipped"]==len(ids)-len(attempted) and t["runner_return_code"]!=0,"failure terminal differs")
 else:require(t["status"]=="accepted" and attempted==accepted==ids and t["failed"]==t["missing"]==t["skipped"]==t["runner_return_code"]==0,"success terminal differs")
def validate_session(root,c,state,t,runner_commit):
 s=json.loads((state/"session.json").read_text());require(set(s)=={"schema_version","protocol_revision","runner_commit","config_sha256","case_ids","resource_preflight","detached","started_unix_ns"} and s["schema_version"]==1,"session schema differs");require(s["protocol_revision"]==c["protocol_revision"] and s["runner_commit"]==runner_commit and s["config_sha256"]==sha_path(root/CONFIG_REL) and s["case_ids"]==[x["experiment_id"] for x in c["formal_cases"]],"session differs")
 d=s["detached"];require(set(d)=={"pid","sid","parent_pid","sighup_ignored","no_tty"} and d["pid"]==d["sid"]>1 and isinstance(d["parent_pid"],int) and d["parent_pid"]>=1 and d["sighup_ignored"] is True and d["no_tty"] is True,"detachment differs");require(isinstance(s["started_unix_ns"],int) and 0<s["started_unix_ns"]<t["finished_unix_ns"],"session time differs");require(s["resource_preflight"]=={"status":"accepted","hostname":c["runtime"]["hostname"],"logical_cpu":c["runtime"]["logical_cpu"],"smt_domain":c["runtime"]["smt_domain"],"collisions":[]} and t["session_sha256"]==sha_path(state/"session.json"),"session preflight/SHA differs")
def verify_tree(state,t,failed):
 for p in state.rglob("*"):require(not p.is_symlink() and (p.is_dir() or p.is_file()),f"invalid state item: {p}")
 require({p.name for p in state.iterdir()}==({"runs","session.json","terminal.json","failure.json"} if failed else {"runs","session.json","terminal.json"}),"state root differs")
 dirs=sorted((state/"runs").iterdir());require(len(dirs)==t["attempted"],"run directory count differs")
 for d,eid in zip(dirs,t["attempted_ids"]):
  require(d.name.endswith("_"+eid),"run directory identity differs");inv=t["run_inventory"][eid];require({p.name for p in d.iterdir()}==set(inv),"run files differ")
  for name,x in inv.items():require(sha_path(d/name)==x["sha256"] and (d/name).stat().st_size==x["size_bytes"],"run inventory differs")
def envelope(root,c,row,run,t,accepted):
 attempt=json.loads((run/"attempt.json").read_text());cmd=json.loads((run/"command.json").read_text());pre=json.loads((run/"resource_preflight.json").read_text());worker=json.loads((run/"worker_return.json").read_text());ret=json.loads((run/"runner_return.json").read_text());eid=row["experiment_id"]
 require(attempt["experiment_id"]==eid and attempt["attempt_number"]==1 and attempt["status"]=="formal_started" and sha_path(run/"attempt.json")==t["attempt_sha256"][eid],"attempt differs")
 require(cmd=={"argv":command(root,c,row,run),"cwd":str(root),"environment":environment(root,c)},"command differs")
 accepted_preflight={"status":"accepted","hostname":c["runtime"]["hostname"],"logical_cpu":c["runtime"]["logical_cpu"],"smt_domain":c["runtime"]["smt_domain"],"collisions":[]};failed_preflight={"status":"failed","error":"runner_exception"};require(pre==accepted_preflight if accepted else pre in (accepted_preflight,failed_preflight),"preflight differs")
 require(worker=={"schema_version":1,"experiment_id":eid,"return_code":worker["return_code"]} and (worker["return_code"] is None or isinstance(worker["return_code"],int)),"worker return differs");require(ret["schema_version"]==1 and ret["experiment_id"]==eid and ret["worker_return_code"]==worker["return_code"] and isinstance(ret["runner_return_code"],int) and set(ret) in ({"schema_version","experiment_id","worker_return_code","runner_return_code"},{"schema_version","experiment_id","worker_return_code","runner_return_code","reason"}) and sha_path(run/"runner_return.json")==t["runner_return_sha256"][eid],"runner return differs");return worker,ret
def replay_run_result(root,c,row,run,t,require_terminal_result_sha):
 runtime=validate_runtime(root,c);result=json.loads((run/"result.json").read_text());eid=row["experiment_id"]
 if require_terminal_result_sha:require(sha_path(run/"result.json")==t["result_sha256"][eid],"result SHA differs")
 require(result["cpu_affinity"]==[c["runtime"]["logical_cpu"]] and result["runtime"]==runtime,"runtime differs");lines=(run/"stdout.txt").read_text().strip().splitlines();require(lines and canonical(json.loads(lines[-1]))==canonical(result),"stdout/result differs");density=np.load(run/"density.npy",allow_pickle=False);require(sha_path(run/"density.npy")==result["density_sha256"],"density SHA differs");replay_result(root,c,row,result,density);return result,density
def load_rows(root,c,state,t,rows):
 runs=[];densities={}
 for index,row in enumerate(rows,1):
  run=state/"runs"/f"{index:03d}_{row['experiment_id']}";worker,ret=envelope(root,c,row,run,t,True);require(worker["return_code"]==ret["runner_return_code"]==0,"accepted return differs");result,density=replay_run_result(root,c,row,run,t,True);runs.append(result);densities[row["experiment_id"]]=density
 return runs,densities
def load_success(root,c,state,t):
 runs,densities=load_rows(root,c,state,t,c["formal_cases"])
 for sid in {x["structure_id"] for x in runs}:
  group=[x for x in runs if x["structure_id"]==sid];ref=next(x for x in group if x["route"]=="full_grid_WT_reference");reference=densities[ref["experiment_id"]]
  for r in [x for x in group if x["route"]=="coefficient_23_function"]:r["density_relative_l2_vs_full_grid"]=float(np.linalg.norm(densities[r["experiment_id"]]-reference)/np.linalg.norm(reference));r["density_reference_id"]=ref["experiment_id"]
 return runs
def failure_output(root,c,state,t):
 f=json.loads((state/"failure.json").read_text());require(t["failure_sha256"]==sha_path(state/"failure.json") and f["schema_version"]==1 and f["status"]=="failed_no_retry" and f["experiment_id"]==t["attempted_ids"][-1] and f["return_code"]==t["runner_return_code"],"failure identity differs");require(f["attempted_ids"]==t["attempted_ids"] and f["accepted_ids"]==t["accepted_ids"] and f["unattempted_ids"]==t["unattempted_ids"] and f["attempt_sha256"]==t["attempt_sha256"] and f["runner_return_sha256"]==t["runner_return_sha256"],"failure maps differ")
 accepted_rows=c["formal_cases"][:t["accepted"]];accepted_runs,_=load_rows(root,c,state,t,accepted_rows)
 for index,row in enumerate(c["formal_cases"][:t["attempted"]],1):
  accepted=row["experiment_id"] in t["accepted_ids"];run=state/"runs"/f"{index:03d}_{row['experiment_id']}";worker,ret=envelope(root,c,row,run,t,accepted);require((run/"result.json").exists()==accepted,"failure result denominator differs")
  if row["experiment_id"] not in t["accepted_ids"]:require(ret["runner_return_code"]==t["runner_return_code"] and (worker["return_code"] is None or isinstance(worker["return_code"],int)),"failure return differs")
 s={"schema_version":1,"protocol_revision":c["protocol_revision"],"status":"evidence_valid_operational_failure_no_retry","evidence_valid":True,"scientific_gate_accepted":False,"terminal":t,"formal_case_count":4,"accepted_prefix_count":len(accepted_runs),"g3_overall_closed":False,"s4_authorized":False,"next_action":"freeze_failure_and_require_new_revision"};return {"README.md":b"# S3 Al iso_v097 optimizer recovery R4\n\nOperational failure, frozen with no retry. All accepted-prefix raw evidence was replayed.\n","runs.json":canonical({"schema_version":1,"runs":accepted_runs}),"gates.tsv":b"gate\taccepted\noperational_completion\tFalse\n","summary.json":canonical(s)}
def replay_outputs(root,c,runner_commit):
 validate_config(root,c);source_exact(root,c);validate_runtime(root,c);state=Path(c["execution"]["state_root"]);t=json.loads((state/"terminal.json").read_text());failed=t["status"]=="failed_no_retry";validate_terminal(c,t,failed);validate_session(root,c,state,t,runner_commit);verify_tree(state,t,failed);return failure_output(root,c,state,t) if failed else render(c,load_success(root,c,state,t))
def main():
 p=argparse.ArgumentParser();p.add_argument("--project-root",type=Path,default=Path(__file__).resolve().parents[1]);a=p.parse_args();root=a.project_root.resolve();c=load(root);validate_config(root,c);out=replay_outputs(root,c,git(root,"rev-parse","HEAD"));target=root/c["execution"]["analysis_root"];require(not target.exists(),"analysis exists");target.mkdir(parents=True)
 for name in c["output_files"]:write_exclusive(target/name,out[name])
 print(json.dumps({"status":json.loads(out["summary.json"])["status"],"output_files":4},sort_keys=True));return 0
if __name__=="__main__":raise SystemExit(main())
