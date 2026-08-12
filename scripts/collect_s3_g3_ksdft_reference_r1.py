#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,tempfile
from pathlib import Path
import numpy as np
from s3_g3_ksdft_reference_common_r1 import canonical,fourier_resample,load,read_json,require,scientific_metrics,sha_path,source_exact
from parse_s3_g3_ksdft_reference_r1 import parse_run
from s1_g1_thermodynamic_label_common import parse_abacus_cube

def exact_tree(run,inventory):
    observed={p.relative_to(run).as_posix():{"sha256":sha_path(p),"size_bytes":p.stat().st_size} for p in sorted(run.rglob('*')) if p.is_file() and not p.is_symlink()}
    require(not any(p.is_symlink() for p in run.rglob('*')),"run contains symlink");require(observed==inventory,"run inventory differs")
def load_evidence(root,c):
    source_exact(root,c);state=Path(c["execution"]["state_root"]);terminal=read_json(state/"terminal.json");ids=[x["experiment_id"] for x in c["formal_cases"]]
    require(terminal["status"]=="accepted" and terminal["attempted"]==terminal["accepted"]==3 and terminal["failed"]==terminal["missing"]==terminal["skipped"]==terminal["retried"]==0 and terminal["runner_return_code"]==0,"terminal denominator differs");require(terminal["attempted_ids"]==terminal["accepted_ids"]==ids and terminal["unattempted_ids"]==[],"terminal IDs differ")
    rows=[];densities={}
    for index,row in enumerate(c["formal_cases"],1):
        eid=row["experiment_id"];run=state/"runs"/f"{index:02d}_{eid}";require(sha_path(run/"attempt.json")==terminal["attempt_sha256"][eid],"attempt SHA differs");require(sha_path(run/"runner_return.json")==terminal["runner_return_sha256"][eid],"return SHA differs");require(read_json(run/"runner_return.json")["return_code"]==0,"return code differs");require(sha_path(run/"result.json")==terminal["result_sha256"][eid],"result SHA differs");exact_tree(run,terminal["run_inventory"][eid]);stored=read_json(run/"result.json");replay=parse_run(run,c);require(canonical(stored)==canonical(replay),"KS raw replay differs");rows.append(stored);cube=parse_abacus_cube(run/f"OUT.{row['suffix']}"/"chg.cube",quantity="electron_density",units="electron_per_bohr3",structure_path=run/"STRU");densities[float(row["volume_ratio"])]=fourier_resample(np.asarray(cube.values).reshape(cube.dimensions),40)
    runs=read_json(root/c["source"]["s3_runs_path"])["runs"];selected=[];candidate={}
    for v,eid in c["source"]["candidate_density_ids"].items():
        item=next(x for x in runs if x["experiment_id"]==eid);selected.append(item);p=next(Path(c["source"]["s3_external_state_root"]).glob(f"runs/*_{eid}/density.npy"));candidate[float(v)]=np.load(p,allow_pickle=False).reshape((40,40,40))
    historical=read_json(root/c["source"]["historical_ks_v100_result_path"]);metrics=scientific_metrics(c,rows,selected,densities,candidate,historical);return terminal,rows,selected,metrics
def render(root,c):
    terminal,ks,cand,m=load_evidence(root,c);accepted=m["scientific_gate_accepted"];status="accepted_s3_al_ksdft_reference_pilot" if accepted else "evidence_valid_s3_al_ksdft_reference_pilot_rejected"
    summary={"schema_version":1,"protocol_revision":c["protocol_revision"],"status":status,"evidence_valid":True,"scientific_gate_accepted":accepted,"g3_overall_closed":False,"s4_authorized":False,"new_solver_run_count":3,"terminal_sha256":sha_path(Path(c["execution"]["state_root"])/"terminal.json"),"reference_hierarchy":{"scientific_hard_reference":"KSDFT_KS_NL","numerical_diagnostic_only":"full_grid_WT","absolute_cross_functional_energy_comparison":False},**m,"next_action":"expand_S3_across_structures" if accepted else "preserve_rejection_and_design_new_revision_without_retry"}
    runs={"schema_version":1,"protocol_revision":c["protocol_revision"],"ksdft_runs":ks,"candidate_runs":cand}
    lines=["gate\tvolume_ratio\tvalue\tlimit\taccepted"]
    for x in m["density_rows"]:lines.append(f"density_l2\t{x['volume_ratio']}\t{x['relative_l2']}\t{c['acceptance']['density_relative_l2_strictly_less_than']}\t{str(x['accepted']).lower()}")
    for x in m["anchored_energy_rows"]:lines.append(f"anchored_energy\t{x['volume_ratio']}\t{x['absolute_difference_mev_per_atom']}\t{c['acceptance']['anchored_relative_energy_abs_difference_mev_per_atom_strictly_less_than']}\t{str(x['accepted']).lower()}")
    lines.append(f"pressure\t1.0\t{m['pressure']['absolute_difference_gpa']}\t{c['acceptance']['pressure_difference_abs_strictly_less_than_gpa']}\t{str(m['pressure']['accepted']).lower()}")
    lines.append(f"v100_repeat_energy\t1.0\t{m['v100_repeat']['energy_abs_difference_mev_per_atom']}\t{c['acceptance']['ks_v100_repeat_energy_abs_difference_mev_per_atom_strictly_less_than']}\t{str(m['v100_repeat']['accepted']).lower()}")
    density_text=", ".join(f"{x['volume_ratio']}: {x['relative_l2']:.9g}" for x in m["density_rows"])
    energy_text=", ".join(f"{x['volume_ratio']}: {x['absolute_difference_mev_per_atom']:.9g} meV/atom" for x in m["anchored_energy_rows"])
    readme=(f"# S3/G3 Al KSDFT reference R1\n\nStatus: `{status}`. Three new KS-NL points completed and were compared with the frozen 23-function coefficient-WT results. KSDFT is the scientific hard reference; full-grid WT remains numerical diagnostic only. Absolute cross-functional energies were not compared.\n\nDensity L2 values: {density_text}. Anchored-energy absolute differences: {energy_text}. Pressure difference: {m['pressure']['absolute_difference_gpa']:.9g} GPa.\n").encode()
    return {"README.md":readme,"runs.json":canonical(runs),"gates.tsv":("\n".join(lines)+"\n").encode(),"summary.json":canonical(summary)}
def main():
 p=argparse.ArgumentParser();p.add_argument("--project-root",type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument("--dry-run",action="store_true");a=p.parse_args();root=a.project_root.resolve();c=load(root);out=render(root,c)
 if a.dry_run: print(json.dumps({"status":"accepted_dry_run","output_files":sorted(out)},sort_keys=True));return 0
 dest=root/c["execution"]["analysis_root"];require(not dest.exists(),"analysis exists");dest.mkdir(parents=True)
 for name,data in out.items(): (dest/name).write_bytes(data)
 return 0
if __name__=="__main__":raise SystemExit(main())
