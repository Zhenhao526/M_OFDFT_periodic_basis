#!/usr/bin/env python3
import copy,json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"scripts"))
import analyze_s3_g3_energy_acceptance_policy_r2 as a
class T(unittest.TestCase):
 def test_01_sources_and_13_rows(self):
  c=a.load(ROOT);old,rows,diag,ok=a.evaluate(ROOT,c);self.assertEqual(len(rows),13);self.assertTrue(ok);self.assertTrue(all(x["reference_energy_difference_mev_per_atom"]<20 for x in rows))
 def test_02_old_ten_still_rejects(self):
  c=a.load(ROOT);c=copy.deepcopy(c);c["policy_revision"]["new_energy_limit_mev_per_atom_strictly_less_than"]=10;old,runs=a.sources(ROOT,c);self.assertTrue(any(x["reference_energy_difference_mev_per_atom"]>=10 for x in old["coefficient_runs"]))
 def test_03_other_gate_failure_rejects(self):
  c=a.load(ROOT);old,_=a.sources(ROOT,c);row=copy.deepcopy(old["coefficient_runs"][0]);row["gates"]["density_l2"]=False;unchanged={k:v for k,v in row["gates"].items() if k!="energy_reference"};self.assertFalse(all(unchanged.values()))
 def test_04_render_scope(self):
  c=a.load(ROOT);s=json.loads(a.render(ROOT,c)["summary.json"]);self.assertEqual(s["status"],"accepted_s3_al_v100_coefficient_pilot_r2");self.assertFalse(s["g3_overall_closed"]);self.assertFalse(s["s4_authorized"]);self.assertEqual(s["new_solver_run_count"],0)
if __name__=="__main__":unittest.main()
