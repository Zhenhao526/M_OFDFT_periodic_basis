#!/usr/bin/env python3
from __future__ import annotations
import hashlib, json, subprocess
from pathlib import Path

BASE_COMMIT="712e1dc51373ab0144b6211a087fcd085ad04d50"
CONFIG_REL=Path("config/S2_g2_density_expansion_acceptance_policy_r1.json")
PROTOCOL_REL=Path("docs/S2_G2_DENSITY_EXPANSION_ACCEPTANCE_POLICY_R1_PROTOCOL.md")
COMMON_REL=Path("scripts/s2_g2_density_expansion_policy_common_r1.py")
ANALYZER_REL=Path("scripts/analyze_s2_g2_density_expansion_acceptance_policy_r1.py")
VALIDATOR_REL=Path("scripts/validate_s2_g2_density_expansion_acceptance_policy_r1.py")
TEST_REL=Path("tests/test_s2_g2_density_expansion_acceptance_policy_r1.py")
IMPLEMENTATION_PATHS={str(p) for p in (CONFIG_REL,PROTOCOL_REL,COMMON_REL,ANALYZER_REL,VALIDATOR_REL,TEST_REL)}

def require(x,msg):
    if not x: raise ValueError(msg)
def canonical(x): return (json.dumps(x,indent=2,sort_keys=True,ensure_ascii=False)+"\n").encode()
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def git(root,*args):
    p=subprocess.run(["git",*args],cwd=root,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    require(p.returncode==0,f"git {' '.join(args)} failed: {p.stderr.strip()}"); return p.stdout.strip()
def load(root): return json.loads((root/CONFIG_REL).read_text())

def validate_config(c):
    require(c["schema_version"]==1 and c["protocol_revision"]=="S2-G2-DENSITY-EXPANSION-ACCEPTANCE-POLICY-20260812-R1","schema/protocol differs")
    require(c["base_commit"]==BASE_COMMIT and c["new_solver_run_count"]==0,"base/run count differs")
    require(c["candidate_id"]=="r08_eta100_complementary" and c["candidate_basis_function_count_per_atom"]==23,"candidate differs")
    require(c["target_scope"]=="Al_density_expansion_representation_and_operator_accuracy","scope differs")
    p=c["policy"]
    require(p["historical_rejections_are_immutable"] is True,"history policy differs")
    require(p["hard_gate_ids"]==["density","electron_number","component_energies","combined_energy","fixed_wt_total","eggbox_energy","reference_pseudoforce","projection_excess_pseudoforce","selected_pseudoforce","cross_grid_pseudoforce","cross_grid_energy"],"hard gates differ")
    require(p["diagnostic_only_ids"]==["rank_floor","rank_deficiency","rank_path_span","condition","retained_margin","adjacent_subspace","cross_grid_subspace"],"diagnostics differ")
    require(all(p[k] is True for k in ("diagnostic_failures_do_not_affect_density_expansion_acceptance","does_not_claim_unique_or_well_conditioned_coefficients","does_not_close_g2c_performance","does_not_start_s3","mg_not_in_scope")),"limits differ")
    require(c["output"]["files"]==["README.md","gates.tsv","policy_revision.json","summary.json"],"outputs differ")

def source_json(root,c,key):
    s=c["source"]; commit=s[f"{key}_evidence_commit"]; rel=s[f"{key}_summary_path"]; expected=s[f"{key}_summary_sha256"]
    require(subprocess.run(["git","merge-base","--is-ancestor",commit,"HEAD"],cwd=root).returncode==0,f"{key} commit is not ancestor")
    path=root/rel; require(path.is_file() and not path.is_symlink() and sha(path)==expected,f"{key} source differs")
    data=subprocess.run(["git","show",f"{commit}:{rel}"],cwd=root,stdout=subprocess.PIPE,check=True).stdout
    require(hashlib.sha256(data).hexdigest()==expected and data==path.read_bytes(),f"{key} committed bytes differ")
    return json.loads(data)

def build(root,c):
    validate_config(c)
    dense=source_json(root,c,"dense_grid"); joint=source_json(root,c,"joint_svd"); null=source_json(root,c,"null_envelope")
    s=c["source"]; dense_cfg=root/s["dense_grid_config_path"]
    require(sha(dense_cfg)==s["dense_grid_config_sha256"],"dense config differs")
    cfg=json.loads(dense_cfg.read_text()); a=cfg["acceptance"]; m=dense["grid_metrics"]
    require(dense["status"]=="evidence_valid_scientific_gate_rejected" and dense["candidate_id"]==c["candidate_id"],"historical dense status differs")
    require(joint["status"]=="evidence_valid_scientific_gate_rejected","historical joint status differs")
    require(null["status"]=="accepted_null_envelope_diagnostic" and null["minimum_required_fixed_envelope_dimension"]==8 and null["current_23_function_periodic_expansion_retained"] is False,"historical null status differs")
    observed={
      "density":max(x["density_relative_l2"] for x in m),
      "electron_number":max(abs(x[k]-324.0)/324.0 for x in m for k in ("reference_electrons","selected_electrons")),
      "component_energies":max(abs(x["component_energy_errors_mev_per_atom"][k]) for x in m for k in ("hartree","external","xc")),
      "combined_energy":max(abs(x["component_energy_errors_mev_per_atom"]["combined_hartree_external_xc"]) for x in m),
      "fixed_wt_total":max(abs(x["component_energy_errors_mev_per_atom"]["total_with_fixed_wt"]) for x in m),
      "eggbox_energy":max(x["selected_energy_peak_to_peak_mev_per_atom"] for x in m),
      "reference_pseudoforce":max(x["reference_pseudoforce_max_ev_per_angstrom"] for x in m),
      "projection_excess_pseudoforce":max(x["projection_excess_pseudoforce_max_ev_per_angstrom"] for x in m),
      "selected_pseudoforce":max(x["selected_pseudoforce_max_ev_per_angstrom"] for x in m),
      "cross_grid_pseudoforce":abs(m[1]["projection_excess_pseudoforce_max_ev_per_angstrom"]-m[0]["projection_excess_pseudoforce_max_ev_per_angstrom"]),
      "cross_grid_energy":abs(m[1]["projection_excess_energy_peak_to_peak_mev_per_atom"]-m[0]["projection_excess_energy_peak_to_peak_mev_per_atom"]),
    }
    limits={"density":a["localized_density_relative_l2_strict_lt"],"electron_number":a["electron_relative_error_strict_lt"],"component_energies":a["component_energy_abs_error_max_mev_per_atom"],"combined_energy":a["combined_energy_abs_error_max_mev_per_atom"],"fixed_wt_total":a["fixed_kedf_total_energy_error_max_mev_per_atom"],"eggbox_energy":a["eggbox_energy_peak_to_peak_max_mev_per_atom"],"reference_pseudoforce":a["reference_pseudoforce_max_ev_per_angstrom"],"projection_excess_pseudoforce":a["projection_excess_pseudoforce_max_ev_per_angstrom"],"selected_pseudoforce":a["selected_pseudoforce_max_ev_per_angstrom"],"cross_grid_pseudoforce":a["cross_grid_pseudoforce_change_max_ev_per_angstrom"],"cross_grid_energy":a["cross_grid_energy_peak_to_peak_change_max_mev_per_atom"]}
    rows=[]
    for key in c["policy"]["hard_gate_ids"]:
        passed=observed[key] < limits[key] if key in ("density","electron_number") else observed[key] <= limits[key]
        rows.append({"gate_id":key,"role":"hard","observed":observed[key],"limit":limits[key],"passed":passed})
    diagnostic=[]
    for key in c["policy"]["diagnostic_only_ids"]:
        diagnostic.append({"gate_id":key,"role":"diagnostic_only","historical_passed":dense["gates"][key],"affects_acceptance":False})
    accepted=all(x["passed"] for x in rows)
    require(accepted,"registered density-expansion hard gate failed")
    summary={"schema_version":1,"protocol_revision":c["protocol_revision"],"status":"accepted_density_expansion_candidate","evidence_valid":True,"scientific_gate_accepted":True,"candidate_id":c["candidate_id"],"basis_function_count_per_atom":23,"hard_gate_count":len(rows),"hard_gate_passed_count":sum(x["passed"] for x in rows),"diagnostic_gate_count":len(diagnostic),"historical_dense_grid_status":dense["status"],"historical_failed_gates":dense["failed_gates"],"rank_and_angle_role":"diagnostic_only","g2c_performance_closed":False,"s3_started":False,"new_solver_run_count":0,"next_action":"preregister_G2c_compression_and_performance_for_the_accepted_23_function_density_expansion"}
    policy={"schema_version":1,"protocol_revision":c["protocol_revision"],"policy":c["policy"],"hard_gates":rows,"diagnostics":diagnostic,"historical_evidence":{"dense_grid":s["dense_grid_evidence_commit"],"joint_svd":s["joint_svd_evidence_commit"],"null_envelope":s["null_envelope_evidence_commit"]}}
    return summary,policy,rows

def render(root,c):
    summary,policy,rows=build(root,c)
    lines=["gate_id\trole\tobserved\tlimit\tpassed"]+[f"{x['gate_id']}\t{x['role']}\t{x['observed']}\t{x['limit']}\t{x['passed']}" for x in rows]
    readme=("# S2/G2 23-function density-expansion acceptance policy R1\n\n"
            "Status: `accepted_density_expansion_candidate`.\n\n"
            "The historical rank/angle rejections remain unchanged. Under the revised density-expansion scope, all 11 registered density, energy, eggbox and pseudoforce gates pass, so `r08_eta100_complementary` is accepted as the 23-function Al density representation. Rank, condition, retained margin and near-null angles remain diagnostic. G2c performance and S3 remain open. No solver was run.\n").encode()
    return {"README.md":readme,"gates.tsv":("\n".join(lines)+"\n").encode(),"policy_revision.json":canonical(policy),"summary.json":canonical(summary)}
