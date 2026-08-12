#!/usr/bin/env python3
from __future__ import annotations
import copy,json,tempfile
from pathlib import Path
import numpy as np
from s3_g3_wt_coefficient_common_r1 import build_basis,canonical,euler_residual_hartree,fraction_to_boundary,load,load_source_density,sha_path,summarize,validate_config,validate_optimizer_disposition,validate_terminal_trajectory_link,write_exclusive
from collect_s3_g3_wt_coefficient_pilot_r1 import failure_outputs,validate_terminal_denominator,verify_regular_tree
from run_s3_g3_wt_coefficient_pilot_r1 import best_effort_progress

ROOT=Path(__file__).resolve().parents[1]
EV=27.211386245988

def synthetic_runs(c):
    runs=[]
    for row in c["formal_cases"]:
        v=float(row["volume_ratio"]); route=row["route"]; init_index=c["basis"]["initializations"].index(row["initialization"]); energy=-2.0+0.02*(v-1)+0.3*(v-1)**2+(0.0001 if route=="coefficient_23_function" else 0.0)+init_index*1e-7
        initial_sha=f"{v:.4f}-{row['initialization']}"; runs.append({"schema_version":1,"protocol_revision":c["protocol_revision"],"experiment_id":row["experiment_id"],"role":row["role"],"route":route,"initialization":row["initialization"],"volume_ratio":v,"volume_bohr3":100*v,"grid":row["grid"],"status":"accepted","optimizer_claimed_converged":True,"optimizer_converged_code":0,"energy_hartree":energy,"energy_ev_per_atom":energy*EV,"electron_number_absolute_error":1e-12,"negative_density_fraction":0.0,"minimum_density_electron_per_bohr3":1e-4,"projected_gradient_metric_hartree":1e-8,"gradient_finite_difference":{"accepted":True},"accepted_steps_monotonic":True,"pressure_gpa_diagnostic":1.0,"density_relative_l2_vs_full_grid":0.001,"registered_initial_density_sha256":initial_sha,"pairwise_final_density_relative_l2":{}})
    for route in ("full_grid_WT_reference","coefficient_23_function"):
        for volume in c["grid_and_pressure_policy"]["core_volume_ratios"]:
            group=[r for r in runs if r["role"]=="core" and r["route"]==route and r["volume_ratio"]==volume]
            for i in range(3):
                for j in range(i+1,3): group[i]["pairwise_final_density_relative_l2"][group[j]["experiment_id"]]=0.001; group[j]["pairwise_final_density_relative_l2"][group[i]["experiment_id"]]=0.001
    return runs
def test_01_config(): validate_config(load(ROOT))
def test_02_matrix():
    c=load(ROOT); assert len(c["formal_cases"])==29; assert sum(x["route"]=="coefficient_23_function" for x in c["formal_cases"])==13
def test_03_basis_metric():
    c=load(ROOT); cell,rho=load_source_density(ROOT,c); b=build_basis(c,cell,rho); assert np.max(np.abs(b["tangent"].T@b["tangent"]*b["dv"]-np.eye(22)))<2e-10
def test_04_passing_summary():
    c=load(ROOT); s=summarize(c,synthetic_runs(c)); assert s["scientific_gate_accepted"] and not s["g3_overall_closed"] and not s["s4_authorized"]
def test_05_gradient_gate():
    c=load(ROOT); r=synthetic_runs(c); next(x for x in r if x["route"]=="coefficient_23_function")["projected_gradient_metric_hartree"]=1e-5; assert not summarize(c,r)["scientific_gate_accepted"]
def test_06_pressure_gate():
    c=load(ROOT); r=synthetic_runs(c); next(x for x in r if x["experiment_id"]=="S3-20260812-029")["energy_hartree"]+=0.1; assert not summarize(c,r)["scientific_gate_accepted"]
def test_07_negative_density_gate():
    c=load(ROOT); r=synthetic_runs(c); r[0]["negative_density_fraction"]=1e-6; assert not summarize(c,r)["scientific_gate_accepted"]
def test_08_initialization_spread_gate():
    c=load(ROOT); r=synthetic_runs(c); next(x for x in r if x["experiment_id"]=="S3-20260812-008")["energy_ev_per_atom"]+=0.01; assert not summarize(c,r)["scientific_gate_accepted"]
def test_09_exclusive_write():
    with tempfile.TemporaryDirectory() as d:
        p=Path(d)/"x"; write_exclusive(p,b"a")
        try: write_exclusive(p,b"b")
        except FileExistsError: pass
        else: raise AssertionError("exclusive write overwrote evidence")
def test_10_threshold_tamper():
    c=load(ROOT); bad=copy.deepcopy(c); bad["acceptance"]["projected_gradient_metric_hartree_strict_lt"]=1e-5
    try: validate_config(bad)
    except ValueError: pass
    else: raise AssertionError("threshold tamper accepted")
def test_11_fraction_to_boundary():
    rho=np.array([1.0,2.0]); direction=np.array([-2.0,1.0]); alpha=fraction_to_boundary(rho,direction); assert alpha==0.495 and np.min(rho+alpha*direction)>0
def test_12_initial_density_pair_gate():
    c=load(ROOT); r=synthetic_runs(c); next(x for x in r if x["experiment_id"]=="S3-20260812-020")["registered_initial_density_sha256"]="tampered"; assert not summarize(c,r)["scientific_gate_accepted"]
def test_13_variational_lower_bound_gate():
    c=load(ROOT); r=synthetic_runs(c); x=next(x for x in r if x["experiment_id"]=="S3-20260812-020"); ref=next(x for x in r if x["experiment_id"]=="S3-20260812-007"); x["energy_ev_per_atom"]=ref["energy_ev_per_atom"]-0.001; assert not summarize(c,r)["scientific_gate_accepted"]
def test_14_minimum_density_gate():
    c=load(ROOT); r=synthetic_runs(c); r[0]["minimum_density_electron_per_bohr3"]=-1e-12; assert not summarize(c,r)["scientific_gate_accepted"]
def test_15_pressure_platform_gate():
    c=load(ROOT); r=synthetic_runs(c); next(x for x in r if x["experiment_id"]=="S3-20260812-014")["energy_hartree"]+=2e-4; assert not summarize(c,r)["scientific_gate_accepted"]
def test_16_scientific_rejection_is_evidence_valid():
    c=load(ROOT); r=synthetic_runs(c); r[0]["status"]="completed_scientific_rejected"; r[0]["projected_gradient_metric_hartree"]=1e-4; s=summarize(c,r); assert s["evidence_valid"] and not s["scientific_gate_accepted"] and s["status"].startswith("evidence_valid")
def test_16b_status_only_scientific_rejection_blocks_overall():
    c=load(ROOT); r=synthetic_runs(c); r[0]["status"]="completed_scientific_rejected"; r[0]["optimizer_claimed_converged"]=False; r[0]["optimizer_converged_code"]=1; assert not summarize(c,r)["scientific_gate_accepted"]
def test_17_euler_residual():
    residual,mu=euler_residual_hartree(np.ones(4),np.array([2.0,2.0,2.0,2.0]),0.25,1.0); assert residual==0.0 and mu==2.0
def failure_fixture(root):
    c=load(ROOT); state=root/"state"; run=state/"runs/01_S3-20260812-001"; run.mkdir(parents=True); row=c["formal_cases"][0]; write_exclusive(state/"session.json",canonical({"x":1})); attempt={"schema_version":1,"status":"formal_started","experiment_id":row["experiment_id"],"attempt_number":1,"started_unix_ns":1}; write_exclusive(run/"attempt.json",canonical(attempt)); execution=Path(c["execution"]["execution_worktree_root"]); cmd=["taskset","-c",str(c["runtime"]["logical_cpu"]),c["runtime"]["python"],"-s","-B",str(execution/"scripts/run_s3_g3_wt_coefficient_worker_r1.py"),"--project-root",str(execution),"--experiment-id",row["experiment_id"],"--run-directory",str(run)]; env={"HOME":"/tmp","PATH":"/usr/bin","PYTHONPATH":str(execution/"scripts"),"OMP_NUM_THREADS":"1","MKL_NUM_THREADS":"1","OPENBLAS_NUM_THREADS":"1","PYTHONDONTWRITEBYTECODE":"1","PYTHONNOUSERSITE":"1"}; write_exclusive(run/"command.json",canonical({"argv":cmd,"cwd":str(execution),"environment":env})); write_exclusive(run/"resource_preflight.json",canonical({"status":"accepted","hostname":c["runtime"]["hostname"],"logical_cpu":c["runtime"]["logical_cpu"],"smt_domain":c["runtime"]["smt_domain"],"collisions":[]})); write_exclusive(run/"runner_return.json",canonical({"schema_version":1,"experiment_id":row["experiment_id"],"return_code":9})); write_exclusive(run/"stdout.txt",b""); write_exclusive(run/"stderr.txt",b"failure")
    failure={"schema_version":1,"status":"failed_no_retry","experiment_id":row["experiment_id"],"return_code":9,"reason":"test","attempted_ids":[row["experiment_id"]],"accepted_ids":[],"unattempted_ids":[x["experiment_id"] for x in c["formal_cases"][1:]],"attempt_sha256":{row["experiment_id"]:sha_path(run/"attempt.json")},"runner_return_sha256":{row["experiment_id"]:sha_path(run/"runner_return.json")}}; write_exclusive(state/"failure.json",canonical(failure)); inventory={p.name:{"sha256":sha_path(p),"size_bytes":p.stat().st_size} for p in run.iterdir()}; terminal={"schema_version":1,"status":"failed_no_retry","attempted":1,"accepted":0,"failed":1,"missing":0,"skipped":28,"retried":0,"runner_return_code":9,"attempted_ids":[row["experiment_id"]],"accepted_ids":[],"unattempted_ids":failure["unattempted_ids"],"attempt_sha256":failure["attempt_sha256"],"runner_return_sha256":failure["runner_return_sha256"],"result_sha256":{},"run_inventory":{row["experiment_id"]:inventory},"session_sha256":sha_path(state/"session.json"),"failure_sha256":sha_path(state/"failure.json"),"finished_unix_ns":2}; write_exclusive(state/"terminal.json",canonical(terminal)); return c,state,terminal
def test_18_failure_closure_replay():
    with tempfile.TemporaryDirectory() as d:
        c,state,terminal=failure_fixture(Path(d)); verify_regular_tree(state,terminal,True); assert json.loads(failure_outputs(ROOT,c,terminal,state,{})["summary.json"])["evidence_valid"]
def test_19_failure_tamper_rejected():
    with tempfile.TemporaryDirectory() as d:
        c,state,terminal=failure_fixture(Path(d)); (state/"runs/01_S3-20260812-001/stderr.txt").write_text("tamper")
        try: verify_regular_tree(state,terminal,True)
        except ValueError: pass
        else: raise AssertionError("failure raw tamper accepted")
def test_20_extra_or_symlink_rejected():
    with tempfile.TemporaryDirectory() as d:
        c,state,terminal=failure_fixture(Path(d)); (state/"extra").write_text("x")
        try: verify_regular_tree(state,terminal,True)
        except ValueError: pass
        else: raise AssertionError("extra state file accepted")
def test_21_failure_semantic_tamper_rejected():
    with tempfile.TemporaryDirectory() as d:
        c,state,terminal=failure_fixture(Path(d)); command_path=state/"runs/01_S3-20260812-001/command.json"; command=json.loads(command_path.read_text()); command["environment"]["OMP_NUM_THREADS"]="2"; command_path.write_bytes(canonical(command)); terminal["run_inventory"][c["formal_cases"][0]["experiment_id"]]["command.json"]={"sha256":sha_path(command_path),"size_bytes":command_path.stat().st_size}
        try: failure_outputs(ROOT,c,terminal,state,{})
        except ValueError: pass
        else: raise AssertionError("failure command semantic tamper accepted")
def test_22_terminal_map_denominator():
    with tempfile.TemporaryDirectory() as d:
        c,state,terminal=failure_fixture(Path(d)); validate_terminal_denominator(c,terminal,True); terminal["attempt_sha256"]["extra"]="0"*64
        try: validate_terminal_denominator(c,terminal,True)
        except ValueError: pass
        else: raise AssertionError("extra terminal map identity accepted")
def test_23_failure_identity_and_return_code():
    with tempfile.TemporaryDirectory() as d:
        c,state,terminal=failure_fixture(Path(d)); failure_path=state/"failure.json"; failure=json.loads(failure_path.read_text()); failure["experiment_id"]=c["formal_cases"][1]["experiment_id"]; failure_path.write_bytes(canonical(failure)); terminal["failure_sha256"]=sha_path(failure_path)
        try: failure_outputs(ROOT,c,terminal,state,{})
        except ValueError: pass
        else: raise AssertionError("wrong failure experiment accepted")
def test_24_terminal_coefficient_density_energy_link():
    base=np.array([1.0,1.0]); tangent=np.array([[1.0],[-1.0]]); coefficients=[[0.0],[0.2]]; energies=[-1.0,-1.1]; final=base+tangent@np.array([0.2]); validate_terminal_trajectory_link(base,tangent,coefficients,energies,final,-1.1)
    try: validate_terminal_trajectory_link(base,tangent,coefficients,energies,final+np.array([1e-6,0]),-1.1)
    except ValueError: pass
    else: raise AssertionError("terminal density substitution accepted")
    try: validate_terminal_trajectory_link(base,tangent,coefficients,energies,final,-1.0)
    except ValueError: pass
    else: raise AssertionError("terminal energy substitution accepted")
def test_25_progress_pipe_failure_does_not_change_state():
    calls=[]
    def broken(*args,**kwargs): calls.append((args,kwargs)); raise BrokenPipeError("closed")
    assert best_effort_progress({"completed":1},broken) is False and len(calls)==1
    sink=[]
    def good(*args,**kwargs): sink.append((args,kwargs))
    assert best_effort_progress({"completed":1},good) is True and len(sink)==1 and sink[0][1]["flush"] is True
def test_26_optimizer_code_two_is_scientific_rejection():
    assert validate_optimizer_disposition(0,True) is True
    assert validate_optimizer_disposition(1,False) is False
    assert validate_optimizer_disposition(2,False) is False
    for code,claimed in ((2,True),(0,False),(3,False)):
        try: validate_optimizer_disposition(code,claimed)
        except ValueError: pass
        else: raise AssertionError("invalid optimizer code/claim accepted")
    c=load(ROOT); r=synthetic_runs(c); r[0]["status"]="completed_scientific_rejected"; r[0]["optimizer_converged_code"]=2; r[0]["optimizer_claimed_converged"]=False; s=summarize(c,r); assert s["evidence_valid"] and not s["scientific_gate_accepted"]
def test_27_dftpy_converged_but_euler_rejected_is_valid():
    c=load(ROOT); r=synthetic_runs(c); r[0]["status"]="completed_scientific_rejected"; r[0]["optimizer_converged_code"]=0; r[0]["optimizer_claimed_converged"]=True; r[0]["projected_gradient_metric_hartree"]=2e-6; s=summarize(c,r); assert s["evidence_valid"] and not s["scientific_gate_accepted"] and not s["all_run_scientific_statuses_accepted"]
    try: validate_optimizer_disposition(0,False)
    except ValueError: pass
    else: raise AssertionError("DFTpy code0 claim tamper accepted")

if __name__=="__main__":
    for name,value in sorted(globals().copy().items()):
        if name.startswith("test_"): value(); print(f"PASS {name}")
