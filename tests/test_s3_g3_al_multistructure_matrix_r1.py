import json,unittest
from pathlib import Path
from validate_s3_g3_al_multistructure_matrix_r1 import contract,rows
ROOT=Path(__file__).resolve().parents[1]
class T(unittest.TestCase):
 def setUp(self): self.c=json.loads((ROOT/"config/S3_g3_al_multistructure_matrix_r1.json").read_text())
 def test_01_denominator(self):
  r=contract(self.c);self.assertEqual((len(r),r[0]["experiment_id"],r[-1]["experiment_id"]),(80,"S3-20260812-101","S3-20260812-180"))
 def test_02_per_structure(self):
  r=rows(self.c)
  for s in self.c["structures"]:
   q=[x for x in r if x["structure_id"]==s["id"]];self.assertEqual(len(q),4);self.assertEqual(sum(x["route"]=="coefficient_23_function" for x in q),3)
 def test_03_scope(self):
  self.assertEqual(self.c["acceptance"]["energy_difference_abs_strict_lt_mev_per_atom"],20.0);self.assertFalse(self.c["scope"]["g3_overall_closed"]);self.assertFalse(self.c["execution"]["formal_execution_authorized_by_this_matrix"])
 def test_04_tamper(self):
  c=json.loads(json.dumps(self.c));c["acceptance"]["projected_gradient_metric_hartree_strict_lt"]=2e-6
  with self.assertRaises(ValueError): contract(c)
if __name__=="__main__": unittest.main()
