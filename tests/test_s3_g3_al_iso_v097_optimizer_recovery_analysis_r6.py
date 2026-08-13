#!/usr/bin/env python3
import json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"scripts"))
import analyze_s3_g3_al_iso_v097_optimizer_recovery_analysis_r6 as a
class T(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.c=a.load(ROOT)
 def test_01_config(self):a.validate_config(self.c);self.assertEqual(self.c["new_solver_run_count"],0)
 def test_02_closure(self):cfg,state,execution_root=a.source(ROOT,self.c);self.assertTrue((state/"terminal.json").exists());self.assertEqual(a.git(execution_root,"rev-parse","HEAD"),self.c["r5_preregistration_commit"])
 def test_03_full_replay_and_serializer(self):
  out=a.build(ROOT,self.c);s=json.loads(out["summary.json"]);self.assertEqual(s["structure_id"],"iso_v097");self.assertTrue(all(line.split("\t")[1]=="iso_v097" for line in out["gates.tsv"].decode().splitlines()[1:]));self.assertEqual(s["new_solver_run_count"],0)
 def test_04_structure_tamper(self):
  bad=dict(self.c);bad["fixed_structure_id"]="bad"
  with self.assertRaises(ValueError):a.validate_config(bad)
if __name__=="__main__":unittest.main()
