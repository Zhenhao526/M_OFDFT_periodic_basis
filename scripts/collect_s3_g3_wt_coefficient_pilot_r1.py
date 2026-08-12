#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,os
from pathlib import Path
from s3_g3_wt_coefficient_common_r1 import CONFIG_REL,canonical,git,load,render,replay_result,require,sha_path,validate_config,validate_runtime,write_exclusive

def expected_command(c,row,run):
    execution=Path(c["execution"]["execution_worktree_root"])
    argv=["taskset","-c",str(c["runtime"]["logical_cpu"]),c["runtime"]["python"],"-s","-B",str(execution/"scripts/run_s3_g3_wt_coefficient_worker_r1.py"),"--project-root",str(execution),"--experiment-id",row["experiment_id"],"--run-directory",str(run)]
    return argv,str(execution)

def validate_accepted_preflight(c,preflight):
    require(preflight=={"status":"accepted","hostname":c["runtime"]["hostname"],"logical_cpu":c["runtime"]["logical_cpu"],"smt_domain":c["runtime"]["smt_domain"],"collisions":[]},"accepted preflight differs")

def validate_terminal_denominator(c,terminal,failed):
    ids=[x["experiment_id"] for x in c["formal_cases"]]; attempted=terminal["attempted_ids"]; accepted=terminal["accepted_ids"]; unattempted=terminal["unattempted_ids"]
    require(attempted==ids[:len(attempted)] and unattempted==ids[len(attempted):] and terminal["attempted"]==len(attempted) and terminal["accepted"]==len(accepted) and terminal["skipped"]==len(unattempted) and terminal["retried"]==0 and isinstance(terminal["finished_unix_ns"],int),"terminal ID denominator differs")
    require(set(terminal["attempt_sha256"])==set(attempted) and set(terminal["runner_return_sha256"])==set(attempted) and set(terminal["run_inventory"])==set(attempted) and set(terminal["result_sha256"])==set(accepted),"terminal map denominator differs")
    if failed:
        require(terminal["status"]=="failed_no_retry" and accepted==attempted[:-1] and terminal["failed"]==1 and terminal["missing"]==0 and terminal["runner_return_code"]!=0,"failed terminal differs")
    else:
        require(terminal["status"]=="accepted" and attempted==accepted==ids and not unattempted and terminal["failed"]==terminal["missing"]==terminal["skipped"]==terminal["runner_return_code"]==0,"accepted terminal differs")

def validate_session(c,state,terminal,runner_commit,config_sha256):
    session=json.loads((state/"session.json").read_text()); require(set(session)=={"schema_version","protocol_revision","runner_commit","config_sha256","case_ids","resource_preflight","detached","started_unix_ns"},"session denominator differs")
    require(session["schema_version"]==1 and session["protocol_revision"]==c["protocol_revision"] and session["runner_commit"]==runner_commit and session["config_sha256"]==config_sha256 and session["case_ids"]==[x["experiment_id"] for x in c["formal_cases"]],"session identity differs")
    validate_accepted_preflight(c,session["resource_preflight"]); detached=session["detached"]
    require(set(detached)=={"pid","sid","parent_pid","sighup_ignored","no_tty"} and detached["pid"]==detached["sid"]>1 and detached["parent_pid"]>=1 and detached["sighup_ignored"] is True and detached["no_tty"] is True,"detached proof differs")
    require(isinstance(session["started_unix_ns"],int) and terminal["finished_unix_ns"]>=session["started_unix_ns"] and terminal["session_sha256"]==sha_path(state/"session.json"),"session time/SHA differs")
    return session

def validate_envelope(c,row,run,terminal):
    experiment_id=row["experiment_id"]
    attempt=json.loads((run/"attempt.json").read_text()); command=json.loads((run/"command.json").read_text()); preflight=json.loads((run/"resource_preflight.json").read_text()); ret=json.loads((run/"runner_return.json").read_text())
    require(set(attempt)=={"schema_version","status","experiment_id","attempt_number","started_unix_ns"} and attempt["schema_version"]==1 and attempt["status"]=="formal_started" and attempt["experiment_id"]==experiment_id and attempt["attempt_number"]==1 and isinstance(attempt["started_unix_ns"],int),"attempt identity differs")
    require(sha_path(run/"attempt.json")==terminal["attempt_sha256"][experiment_id],"attempt SHA differs")
    argv,cwd=expected_command(c,row,run); require(command["argv"]==argv and command["cwd"]==cwd,"command differs")
    environment=command.get("environment"); require(isinstance(environment,dict) and set(environment)=={"HOME","PATH","PYTHONPATH","OMP_NUM_THREADS","MKL_NUM_THREADS","OPENBLAS_NUM_THREADS","PYTHONDONTWRITEBYTECODE","PYTHONNOUSERSITE"},"command environment denominator differs")
    require(environment["PYTHONPATH"]==str(Path(cwd)/"scripts") and all(environment[k]=="1" for k in ("OMP_NUM_THREADS","MKL_NUM_THREADS","OPENBLAS_NUM_THREADS","PYTHONDONTWRITEBYTECODE","PYTHONNOUSERSITE")) and environment["HOME"] and environment["PATH"],"command environment differs")
    require(preflight.get("status") in {"accepted","failed"},"preflight status differs")
    if preflight["status"]=="accepted": validate_accepted_preflight(c,preflight)
    require(set(ret) in ({"schema_version","experiment_id","return_code"},{"schema_version","experiment_id","return_code","reason"}) and ret["schema_version"]==1 and ret["experiment_id"]==experiment_id and isinstance(ret["return_code"],int),"runner return differs")
    require(sha_path(run/"runner_return.json")==terminal["runner_return_sha256"][experiment_id],"runner return SHA differs")
    return command,preflight,ret

def verify_regular_tree(state:Path,terminal,failed:bool):
    for path in state.rglob("*"):
        require(not path.is_symlink(),f"symlink in state: {path}")
        require(path.is_dir() or path.is_file(),f"special state item: {path}")
    root_names={p.name for p in state.iterdir()}
    require(root_names==({"runs","session.json","terminal.json","failure.json"} if failed else {"runs","session.json","terminal.json"}),"state root denominator differs")
    dirs=sorted((state/"runs").iterdir())
    require(len(dirs)==terminal["attempted"] and all(x.is_dir() for x in dirs),"run directory denominator differs")
    for run,row in zip(dirs,terminal["attempted_ids"]):
        require(run.name.endswith("_"+row),"run directory identity differs")
        inventory=terminal["run_inventory"][row]; require({p.name for p in run.iterdir()}==set(inventory),"run file denominator differs")
        for name,identity in inventory.items(): require(sha_path(run/name)==identity["sha256"] and (run/name).stat().st_size==identity["size_bytes"],"run inventory differs")

def _load_run_results(root,c,state,terminal,runtime,rows):
    import numpy as np
    runs=[]; densities={}
    for row in rows:
        index=c["formal_cases"].index(row)+1; run=state/"runs"/f"{index:02d}_{row['experiment_id']}"; result=json.loads((run/"result.json").read_text()); command,preflight,ret=validate_envelope(c,row,run,terminal)
        require(preflight["status"]=="accepted" and ret["return_code"]==0,"accepted run envelope differs")
        require(result["status"] in {"accepted","completed_scientific_rejected"},"result operational status differs")
        require(sha_path(run/"result.json")==terminal["result_sha256"][row["experiment_id"]],"result SHA differs")
        stdout_lines=(run/"stdout.txt").read_text().strip().splitlines(); require(stdout_lines and canonical(json.loads(stdout_lines[-1]))==canonical(result),"worker stdout/result differs")
        require({k:result[k] for k in ("experiment_id","role","route","initialization","volume_ratio","grid")}=={k:row[k] for k in ("experiment_id","role","route","initialization","volume_ratio","grid")},"result row differs")
        require(result["cpu_affinity"]==[c["runtime"]["logical_cpu"]] and result["runtime"]==runtime,"result runtime differs")
        density=np.load(run/"density.npy",allow_pickle=False); require(density.ndim==1 and str(density.dtype)=="float64" and sha_path(run/"density.npy")==result["density_sha256"],"density identity differs"); replay_result(root,c,row,result,density); densities[row["experiment_id"]]=density; runs.append(result)
    return runs,densities

def failure_outputs(root,c,terminal,state,runtime):
    failure=json.loads((state/"failure.json").read_text()); require(sha_path(state/"failure.json")==terminal["failure_sha256"],"failure SHA differs"); require(failure["status"]=="failed_no_retry" and failure["attempted_ids"]==terminal["attempted_ids"] and failure["accepted_ids"]==terminal["accepted_ids"] and failure["unattempted_ids"]==terminal["unattempted_ids"] and failure["return_code"]==terminal["runner_return_code"],"failure identity differs")
    require(failure["attempt_sha256"]==terminal["attempt_sha256"] and failure["runner_return_sha256"]==terminal["runner_return_sha256"],"failure orchestration maps differ")
    accepted_rows=[row for row in c["formal_cases"] if row["experiment_id"] in terminal["accepted_ids"]]
    accepted_results,_=_load_run_results(root,c,state,terminal,runtime,accepted_rows)
    accepted_by_id={r["experiment_id"]:r for r in accepted_results}
    runs=[]
    for index,row in enumerate(c["formal_cases"][:terminal["attempted"]],1):
        run=state/"runs"/f"{index:02d}_{row['experiment_id']}"
        command,preflight,ret=validate_envelope(c,row,run,terminal); accepted=row["experiment_id"] in terminal["accepted_ids"]
        require(preflight["status"]=="accepted" if accepted else preflight["status"] in {"accepted","failed"},"failure preflight differs")
        require(((run/"result.json").is_file())==accepted,"failure result denominator differs")
        item={"experiment_id":row["experiment_id"],"command":command,"resource_preflight":preflight,"runner_return":ret,"stdout_sha256":sha_path(run/"stdout.txt"),"stderr_sha256":sha_path(run/"stderr.txt")}
        if accepted: item["result"]=accepted_by_id[row["experiment_id"]]
        runs.append(item)
    summary={"schema_version":1,"protocol_revision":c["protocol_revision"],"status":"evidence_valid_operational_failure_no_retry","evidence_valid":True,"scientific_gate_accepted":False,"terminal":terminal,"formal_case_count":29,"g3_overall_closed":False,"s4_authorized":False,"next_action":"freeze_failure_and_require_new_revision"}
    readme=("# S3/G3 Al V100 fixed WT coefficient-space pilot R1\n\nThe formal revision stopped on its first operational failure. No retry was performed; attempted and unattempted IDs are preserved in the terminal.\n").encode()
    return {"README.md":readme,"runs.json":canonical({"schema_version":1,"runs":runs}),"gates.tsv":b"gate\taccepted\noperational_completion\tFalse\n","summary.json":canonical(summary)}

def load_success_runs(root,c,state,terminal,runtime):
    import numpy as np
    runs,densities=_load_run_results(root,c,state,terminal,runtime,c["formal_cases"])
    selected={v:min([r for r in runs if r["role"]=="core" and r["route"]=="full_grid_WT_reference" and r["volume_ratio"]==v],key=lambda r:r["energy_ev_per_atom"]) for v in c["grid_and_pressure_policy"]["core_volume_ratios"]}
    for r in [x for x in runs if x["route"]=="coefficient_23_function"]:
        ref=selected[r["volume_ratio"]] if r["role"]=="core" else next(x for x in runs if x["route"]=="full_grid_WT_reference" and x["volume_ratio"]==r["volume_ratio"] and x["initialization"]=="uniform"); candidate,reference=densities[r["experiment_id"]],densities[ref["experiment_id"]]; require(candidate.shape==reference.shape,"density shape differs"); r["density_relative_l2_vs_full_grid"]=float(np.linalg.norm(candidate-reference)/np.linalg.norm(reference)); r["density_reference_id"]=ref["experiment_id"]
    for r in runs: r["pairwise_final_density_relative_l2"]={}
    for route in ("full_grid_WT_reference","coefficient_23_function"):
        for volume in c["grid_and_pressure_policy"]["core_volume_ratios"]:
            group=[r for r in runs if r["role"]=="core" and r["route"]==route and r["volume_ratio"]==volume]
            for i in range(3):
                for j in range(i+1,3):
                    value=float(np.linalg.norm(densities[group[i]["experiment_id"]]-densities[group[j]["experiment_id"]])/np.linalg.norm(densities[group[i]["experiment_id"]])); group[i]["pairwise_final_density_relative_l2"][group[j]["experiment_id"]]=value; group[j]["pairwise_final_density_relative_l2"][group[i]["experiment_id"]]=value
    return runs

def main():
    p=argparse.ArgumentParser(); p.add_argument("--project-root",type=Path,default=Path(__file__).resolve().parents[1]); a=p.parse_args(); root=a.project_root.resolve(); c=load(root); validate_config(c); runtime=validate_runtime(root,c); state=Path(c["execution"]["state_root"]); terminal=json.loads((state/"terminal.json").read_text()); failed=terminal["status"]=="failed_no_retry"; validate_terminal_denominator(c,terminal,failed); validate_session(c,state,terminal,git(root,"rev-parse","HEAD"),sha_path(root/CONFIG_REL)); verify_regular_tree(state,terminal,failed)
    if failed: out=failure_outputs(root,c,terminal,state,runtime)
    else:
        require(terminal["status"]=="accepted" and terminal["attempted"]==terminal["accepted"]==29 and terminal["failed"]==terminal["missing"]==terminal["skipped"]==0 and terminal["runner_return_code"]==0,"terminal differs")
        out=render(c,load_success_runs(root,c,state,terminal,runtime))
    target=root/c["execution"]["analysis_root"]; require(not target.exists(),"analysis exists"); target.mkdir(parents=True,exist_ok=False)
    for name in c["output_files"]: write_exclusive(target/name,out[name])
    s=json.loads(out["summary.json"]); print(json.dumps({"status":s["status"],"output_files":len(out)},sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
