import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest import mock
import numpy as np
import s3_g3_al_iso_v097_optimizer_recovery_common_r4 as c
import collect_s3_g3_al_iso_v097_optimizer_recovery_r4 as collector
import run_s3_g3_al_iso_v097_optimizer_recovery_r4 as runner
import run_s3_g3_al_iso_v097_optimizer_recovery_worker_r4 as worker
ROOT=Path(__file__).resolve().parents[1]
class T(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.cfg=c.load(ROOT);c.validate_config(ROOT,cls.cfg)
 def synthetic(self,delta=.015):
  out=[]
  for row in self.cfg["formal_cases"]:
   r={**c.row_identity(row),"status":"accepted","energy_ev_per_atom":-10+(delta if row["route"]=="coefficient_23_function" else 0),"electron_number_absolute_error":0.,"negative_density_fraction":0.,"minimum_density_electron_per_bohr3":0.,"projected_gradient_metric_hartree":5e-7,"gradient_finite_difference":{"accepted":True},"accepted_steps_monotonic":True,"full_grid_euler_residual_hartree":2e-6,"recovery_events":[],"accepted_step_trust_radii":[.05]}
   if row["route"]=="coefficient_23_function":r["density_relative_l2_vs_full_grid"]=.003
   out.append(r)
  return out
 def test_01_denominator(self):self.assertEqual(([x["experiment_id"] for x in self.cfg["formal_cases"]],len(self.cfg["formal_cases"])),([f"S3-20260813-{x}" for x in range(381,385)],4))
 def test_02_geometry_and_basis(self):
  for row in self.cfg["formal_cases"]:self.assertAlmostEqual(np.linalg.det(c.geometry_transform(row)),.97,places=13)
  b=c.build_basis(self.cfg,c.cell_for_row(ROOT,self.cfg,self.cfg["formal_cases"][0]),(40,40,40));self.assertEqual(b["tangent"].shape,(64000,22));self.assertGreater(b["raw_gram_eigenvalues"][0],0)
 def test_03_acceptance(self):s=c.summarize(self.cfg,self.synthetic());self.assertTrue(s["scientific_gate_accepted"]);self.assertTrue(s["al_twenty_structure_coefficient_subgate_accepted"]);self.assertFalse(s["g3_overall_closed"])
 def test_04_unchanged_energy_and_spread_gates(self):
  self.assertFalse(c.summarize(self.cfg,self.synthetic(.020001))["scientific_gate_accepted"]);runs=self.synthetic();runs[1]["energy_ev_per_atom"]+=.001001;self.assertFalse(c.summarize(self.cfg,runs)["scientific_gate_accepted"])
 def test_05_source_portrait_exact(self):
  e=json.loads((ROOT/c.CONFIG_REL).read_text());s=json.loads((ROOT/e["source_evidence"]["summary_path"]).read_text());self.assertEqual((s["scientific_gate_accepted"],sum(x["accepted"] for x in s["structures"])),(False,19));self.assertFalse(next(x for x in s["structures"] if x["structure_id"]=="iso_v097")["accepted"])
 def test_06_policy_tamper(self):
  for key,value in (("trust_radius_initial",.06),("max_iterations",601)):
   bad=copy.deepcopy(json.loads((ROOT/c.CONFIG_REL).read_text()));bad["recovery_policy"][key]=value
   with mock.patch.object(c,"execution_config",return_value=bad):
    with self.assertRaises(ValueError):c.validate_config(ROOT,c.load(ROOT))
 def test_07_active_projection(self):
  tangent=np.eye(2);d,n=worker.project_active_direction(np.array([1e-9,1.]),tangent,np.array([-2.,1.]),1e-8);self.assertEqual(n,1);self.assertGreaterEqual(d[0],-1e-12);self.assertAlmostEqual(d[1],1.)
  rng=np.random.default_rng(7);A=rng.normal(size=(1000,22));direction=rng.normal(size=22);outward=(A@direction)<0;projected,n=worker.project_active_direction(np.zeros(1000),A,direction,1e-8);self.assertEqual(n,int(outward.sum()));self.assertLess(np.linalg.norm(A[outward]@projected),1e-9)
 def test_08_optimizer_quadratic(self):
  target=np.array([.2,-.3]);basis={"volume":1.,"tangent":np.eye(2)}
  def evaluate(y):y=np.asarray(y);return (.5*float(np.sum((y-target)**2)),y-target,np.ones(2),None)
  p=copy.deepcopy(self.cfg["recovery_policy"]);p["max_iterations"]=200;out=worker.optimize_coefficients(evaluate,np.zeros(2),basis,p,1e-8);self.assertTrue(out["claimed"]);np.testing.assert_allclose(out["y"],target,atol=1e-7)
 def test_09_same_algorithm_all_inits(self):
  self.assertTrue(self.cfg["recovery_policy"]["same_algorithm_for_all_three_initializations"]);self.assertTrue(self.cfg["recovery_policy"]["registered_uniform_density_preserved"]);self.assertTrue(self.cfg["recovery_policy"]["continuation_or_initial_substitution_forbidden"])
 def test_10_recovery_schema_tamper(self):
  row=self.cfg["formal_cases"][1];r={"recovery_algorithm":self.cfg["recovery_policy"]["algorithm"],"recovery_events":[],"accepted_step_trust_radii":[.05],"accepted_step_coefficients":[[0.]*22],"iterations":1};c.validate_recovery_trajectory(self.cfg,row,r);r["accepted_step_trust_radii"]=[.9]
  with self.assertRaises(ValueError):c.validate_recovery_trajectory(self.cfg,row,r)
 def test_11_success_terminal(self):
  ids=[x["experiment_id"] for x in self.cfg["formal_cases"]];t={"status":"accepted","attempted":4,"accepted":4,"failed":0,"missing":0,"skipped":0,"retried":0,"runner_return_code":0,"attempted_ids":ids,"accepted_ids":ids,"unattempted_ids":[],"attempt_sha256":{x:"a" for x in ids},"runner_return_sha256":{x:"r" for x in ids},"run_inventory":{x:{} for x in ids},"result_sha256":{x:"s" for x in ids}};collector.validate_terminal(self.cfg,t,False);t["missing"]=1
  with self.assertRaises(ValueError):collector.validate_terminal(self.cfg,t,False)
 def test_12_failure_terminal(self):
  ids=[x["experiment_id"] for x in self.cfg["formal_cases"]];t={"status":"failed_no_retry","attempted":2,"accepted":1,"failed":1,"missing":0,"skipped":2,"retried":0,"runner_return_code":70,"attempted_ids":ids[:2],"accepted_ids":ids[:1],"unattempted_ids":ids[2:],"attempt_sha256":{x:"a" for x in ids[:2]},"runner_return_sha256":{x:"r" for x in ids[:2]},"run_inventory":{x:{} for x in ids[:2]},"result_sha256":{ids[0]:"s"}};collector.validate_terminal(self.cfg,t,True);t["skipped"]=1
  with self.assertRaises(ValueError):collector.validate_terminal(self.cfg,t,True)
 def test_13_command_tamper(self):
  row=self.cfg["formal_cases"][0];eid=row["experiment_id"]
  with tempfile.TemporaryDirectory() as td:
   run=Path(td);objs={"attempt.json":{"schema_version":1,"status":"formal_started","experiment_id":eid,"attempt_number":1,"started_unix_ns":1},"resource_preflight.json":{"status":"accepted","hostname":self.cfg["runtime"]["hostname"],"logical_cpu":self.cfg["runtime"]["logical_cpu"],"smt_domain":self.cfg["runtime"]["smt_domain"],"collisions":[]},"worker_return.json":{"schema_version":1,"experiment_id":eid,"return_code":0},"runner_return.json":{"schema_version":1,"experiment_id":eid,"worker_return_code":0,"runner_return_code":0}}
   for name,obj in objs.items():(run/name).write_bytes(c.canonical(obj))
   t={"attempt_sha256":{eid:c.sha_path(run/"attempt.json")},"runner_return_sha256":{eid:c.sha_path(run/"runner_return.json")}}
   good={"argv":runner.command(ROOT,self.cfg,row,run),"cwd":str(ROOT),"environment":runner.environment(ROOT,self.cfg)}
   for key,bad in (("argv",["/bin/false"]),("cwd","/tmp/attacker"),("environment",{"BAD":"1"})):
    x=copy.deepcopy(good);x[key]=bad;(run/"command.json").write_bytes(c.canonical(x))
    with self.assertRaises(ValueError):collector.envelope(ROOT,self.cfg,row,run,t,True)
 def test_14_tree_and_inventory(self):
  with tempfile.TemporaryDirectory() as td:
   run=Path(td);(run/"raw").write_text("x");self.assertIn("raw",runner.inventory(run));os.symlink(run/"raw",run/"link")
   with self.assertRaises(ValueError):runner.inventory(run)
 def test_15_process_tokens(self):
  self.assertFalse(c._registered_execution_process("bash",["bash","monitor run_s3_g3_al_iso_v097_optimizer_recovery_r4.py"]));self.assertTrue(c._registered_execution_process("python3",["python3","/tmp/run_s3_g3_al_iso_v097_optimizer_recovery_worker_r4.py"]));self.assertFalse(c._registered_execution_process("python3",["python3","/tmp/run_s3_g3_al_iso_v097_optimizer_recovery_worker_r40.py"]))
 def test_16_science_rejection_is_evidence_valid(self):runs=self.synthetic();runs[1]["status"]="completed_scientific_rejected";s=c.summarize(self.cfg,runs);self.assertTrue(s["evidence_valid"]);self.assertFalse(s["scientific_gate_accepted"])
 def test_17_initial_density_names_exact(self):self.assertEqual([x["initialization"] for x in self.cfg["formal_cases"]],["uniform","uniform","atomic_superposition_projection","low_g_perturbation_projection"])
 def test_18_execution_fresh(self):self.assertFalse(Path(self.cfg["execution"]["state_root"]).exists());self.assertFalse((ROOT/self.cfg["execution"]["analysis_root"]).exists())
 def test_19_registered_uniform_initial_hash_preserved(self):
  source=json.loads((ROOT/json.loads((ROOT/c.CONFIG_REL).read_text())["source_evidence"]["runs_path"]).read_text())["runs"];old=next(x for x in source if x["experiment_id"]=="S3-20260813-310");self.assertEqual(old["registered_initial_density_sha256"],"ecb9a5c341dd8a61f9a6e6a8290a1838e919b6c6afde890e83000208056fe29a")
 def test_20_recovery_algorithm_replay_is_called_and_tamper_rejected(self):
  row=self.cfg["formal_cases"][1];result={**c.row_identity(row),"registered_initial_density_sha256":self.cfg["recovery_policy"]["registered_initial_density_sha256"]["uniform"],"recovery_algorithm":self.cfg["recovery_policy"]["algorithm"],"recovery_events":[],"accepted_step_trust_radii":[.05],"accepted_step_coefficients":[[0.]*22],"iterations":1}
  with mock.patch.object(c.legacy,"replay_result",return_value={"ok":True}),mock.patch.object(c,"replay_recovery_algorithm") as check:c.replay_result(ROOT,self.cfg,row,result,np.zeros(1));check.assert_called_once()
  result["registered_initial_density_sha256"]="bad"
  with mock.patch.object(c.legacy,"replay_result",return_value={"ok":True}),mock.patch.object(c,"replay_recovery_algorithm"):
   with self.assertRaises(ValueError):c.replay_result(ROOT,self.cfg,row,result,np.zeros(1))
 def test_21_replay_accepts_registered_iteration_above_400(self):
  row=self.cfg["formal_cases"][1];result={**c.row_identity(row),"registered_initial_density_sha256":self.cfg["recovery_policy"]["registered_initial_density_sha256"]["uniform"],"recovery_algorithm":self.cfg["recovery_policy"]["algorithm"],"recovery_events":[],"accepted_step_trust_radii":[.05],"accepted_step_coefficients":[[0.]*22],"iterations":500};observed={}
  def fake(root,cfg,r,res,density):observed["limit"]=cfg["optimization"]["coefficient_max_iterations"];return {"ok":True}
  with mock.patch.object(c.legacy,"replay_result",side_effect=fake),mock.patch.object(c,"replay_recovery_algorithm"):c.replay_result(ROOT,self.cfg,row,result,np.zeros(1))
  self.assertEqual(observed["limit"],600);self.assertEqual(self.cfg["optimization"]["coefficient_max_iterations"],400)
if __name__=="__main__":unittest.main()
