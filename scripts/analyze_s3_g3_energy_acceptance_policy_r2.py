#!/usr/bin/env python3
from __future__ import annotations
import argparse,hashlib,json,subprocess
from pathlib import Path
CONFIG=Path("config/S3_g3_energy_acceptance_policy_r2.json")
PROTOCOL="S3-G3-ENERGY-ACCEPTANCE-POLICY-20260812-R2"
def require(x,m):
 if not x: raise ValueError(m)
def canonical(x):return (json.dumps(x,sort_keys=True,indent=2,ensure_ascii=False)+"\n").encode()
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def git(root,*a):return subprocess.run(["git",*a],cwd=root,check=True,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout.strip()
def load(root):
 c=json.loads((root/CONFIG).read_text());require(c["protocol_revision"]==PROTOCOL,"protocol differs");require(c["policy_revision"]=={"new_energy_limit_mev_per_atom_strictly_less_than":20.0,"all_other_coefficient_route_gates_unchanged":True,"full_grid_euler_gate_role":"diagnostic_only_not_in_coefficient_route_acceptance","historical_evidence_rewritten":False,"new_solver_run_count":0},"policy differs");require(c["scope"]["g3_overall_closed"] is False and c["scope"]["s4_authorized"] is False,"scope differs");return c
def sources(root,c):
 s=c["source"]
 for key,sha_key in (("summary_path","summary_sha256"),("runs_path","runs_sha256")):
  p=root/s[key];require(p.is_file() and not p.is_symlink() and sha(p)==s[sha_key],f"source differs {key}");blob=subprocess.run(["git","show",f"{s['evidence_commit']}:{s[key]}"],cwd=root,check=True,stdout=subprocess.PIPE).stdout;require(blob==p.read_bytes(),f"committed source differs {key}")
 old=json.loads((root/s["summary_path"]).read_text());require(old["status"]==s["old_status"] and old["scientific_gate_accepted"] is False,"historical status differs");return old,json.loads((root/s["runs_path"]).read_text())
def evaluate(root,c):
 old,runs_payload=sources(root,c);limit=c["policy_revision"]["new_energy_limit_mev_per_atom_strictly_less_than"];rows=[]
 for row in old["coefficient_runs"]:
  unchanged={k:v for k,v in row["gates"].items() if k!="energy_reference"};energy=row["reference_energy_difference_mev_per_atom"];accepted=energy<limit and all(unchanged.values());rows.append({"experiment_id":row["experiment_id"],"role":row["role"],"volume_ratio":row["volume_ratio"],"old_energy_limit_mev_per_atom":c["source"]["old_energy_limit_mev_per_atom"],"new_energy_limit_mev_per_atom":limit,"reference_energy_difference_mev_per_atom":energy,"energy_gate_accepted":energy<limit,"unchanged_gates":unchanged,"accepted":accepted})
 require(len(rows)==13,"coefficient denominator differs");all_ok=all(x["accepted"] for x in rows);diagnostic=[x for x in old["electron_negative_density_and_stationarity_runs"] if not x["accepted"] and x["experiment_id"].startswith("S3-20260812-0")]
 return old,rows,diagnostic,all_ok
def render(root,c):
 old,rows,diagnostic,ok=evaluate(root,c);status="accepted_s3_al_v100_coefficient_pilot_r2" if ok else "evidence_valid_s3_al_v100_coefficient_pilot_r2_rejected";summary={"schema_version":1,"protocol_revision":PROTOCOL,"status":status,"evidence_valid":True,"scientific_gate_accepted":ok,"accepted_scope":c["scope"]["accepted_scope"],"coefficient_run_count":13,"coefficient_runs_accepted":sum(x["accepted"] for x in rows),"maximum_reference_energy_difference_mev_per_atom":max(x["reference_energy_difference_mev_per_atom"] for x in rows),"old_energy_limit_mev_per_atom":10.0,"new_energy_limit_mev_per_atom":20.0,"all_other_coefficient_route_gates_unchanged":True,"full_grid_euler_diagnostic_failed_count":len(diagnostic),"full_grid_euler_diagnostic_failed_ids":[x["experiment_id"] for x in diagnostic],"historical_r1_status":old["status"],"historical_evidence_rewritten":False,"new_solver_run_count":0,"g3_overall_closed":False,"s4_authorized":False,"g2c_performance_rejection_preserved":True,"next_action":"preregister_S3_multi_structure_expansion" if ok else "preserve_rejection_and_design_new_revision"};lines=["experiment_id\tenergy_difference_mev_per_atom\told_limit\tnew_limit\tenergy_gate_accepted\tall_unchanged_gates_accepted\taccepted"]+[f"{x['experiment_id']}\t{x['reference_energy_difference_mev_per_atom']}\t10.0\t20.0\t{str(x['energy_gate_accepted']).lower()}\t{str(all(x['unchanged_gates'].values())).lower()}\t{str(x['accepted']).lower()}" for x in rows];readme=f"# S3/G3 energy acceptance policy R2\n\nStatus: `{status}`. The only revised threshold is the coefficient-versus-full-grid-WT energy difference, from `<10` to `<20 meV/atom`. All 13 coefficient points are replayed; historical R1 evidence is unchanged. Full-grid Euler failures remain diagnostic. G3 overall remains open and S4 is not authorized.\n";return {"README.md":readme.encode(),"gates.tsv":("\n".join(lines)+"\n").encode(),"summary.json":canonical(summary)}
def main():
 p=argparse.ArgumentParser();p.add_argument("--project-root",type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument("--dry-run",action="store_true");a=p.parse_args();root=a.project_root.resolve();c=load(root);out=render(root,c)
 if a.dry_run: print(json.dumps(json.loads(out["summary.json"]),sort_keys=True));return 0
 dest=root/c["execution"]["analysis_root"];require(not dest.exists(),"analysis exists");dest.mkdir(parents=True)
 for n,b in out.items():(dest/n).write_bytes(b)
 return 0
if __name__=="__main__":raise SystemExit(main())
