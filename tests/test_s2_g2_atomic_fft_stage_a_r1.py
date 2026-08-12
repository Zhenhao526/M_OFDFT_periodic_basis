from pathlib import Path
import json, sys
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/"scripts"))
import s2_g2_atomic_fft_stage_a_common_r1 as c

def test_config():
    cfg=c.load_config(ROOT); c.validate_config(cfg); assert [x["candidate_id"] for x in cfg["candidates"]]==["r08_atomic_fft","r10_atomic_fft"]
def test_gates_strict():
    a=c.load_config(ROOT)["acceptance"]; row={"electron_number_relative_error":0.0,"density_relative_l2":0.009,"density_min":0.0,"effective_condition_number":1e7,"errors_mev_per_atom":{"hartree":1,"external":2,"xc":3,"fixed_kedf":9},"combined_hartree_external_xc_error_mev_per_atom":6}; assert all(c.gates(row,a).values()); row["density_relative_l2"]=0.01; assert not c.gates(row,a)["density_l2"]
def test_matrix_identity():
    cfg=c.load_config(ROOT); matrix=json.loads((ROOT/"config/S2_g2_next_architecture_matrix_r1.json").read_text()); assert c.git(ROOT,"merge-base","--is-ancestor",cfg["matrix_preregistration_commit"],c.git(ROOT,"rev-parse","HEAD"))=="" and matrix["stages"][0]["candidate_ids"]==["r08_atomic_fft","r10_atomic_fft"]
