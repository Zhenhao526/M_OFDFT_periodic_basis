from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/"scripts"))
import s2_g2_density_expansion_policy_common_r1 as c
def test_policy_denominator(): c.validate_config(c.load(ROOT))
def test_historical_rejection_is_retained():
 cfg=c.load(ROOT); dense=c.source_json(ROOT,cfg,"dense_grid"); assert dense["status"]=="evidence_valid_scientific_gate_rejected" and len(dense["failed_gates"])==5
def test_new_scope_accepts_hard_gates_only():
 summary,policy,rows=c.build(ROOT,c.load(ROOT)); assert summary["status"]=="accepted_density_expansion_candidate" and len(rows)==11 and all(x["passed"] for x in rows); assert len(policy["diagnostics"])==7 and not any(x["affects_acceptance"] for x in policy["diagnostics"])
def test_render_is_deterministic(): assert c.render(ROOT,c.load(ROOT))==c.render(ROOT,c.load(ROOT))
