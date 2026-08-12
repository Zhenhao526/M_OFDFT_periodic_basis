import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest import mock
import numpy as np
import s3_g3_al_multistructure_common_r2 as c
import collect_s3_g3_al_multistructure_execution_r2 as collector
import run_s3_g3_al_multistructure_execution_r2 as runner
ROOT=Path(__file__).resolve().parents[1]
class T(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.cfg=c.load(ROOT);c.validate_config(ROOT,cls.cfg)
 def synthetic_runs(self,delta_ev=0.015):
  runs=[]
  for row in self.cfg["formal_cases"]:
   item={**c.row_identity(row),"status":"accepted","energy_ev_per_atom":-10.0+(delta_ev if row["route"]=="coefficient_23_function" else 0.0),"electron_number_absolute_error":0.0,"negative_density_fraction":0.0,"minimum_density_electron_per_bohr3":0.0,"projected_gradient_metric_hartree":5e-7,"gradient_finite_difference":{"accepted":True},"accepted_steps_monotonic":True,"full_grid_euler_residual_hartree":5e-5}
   if row["route"]=="coefficient_23_function":item["density_relative_l2_vs_full_grid"]=0.003
   runs.append(item)
  return runs
 def test_01_denominator(self):self.assertEqual((len(self.cfg["formal_cases"]),self.cfg["formal_cases"][0]["experiment_id"],self.cfg["formal_cases"][-1]["experiment_id"]),(80,"S3-20260812-201","S3-20260812-280"))
 def test_02_geometry_determinants(self):
  for row in self.cfg["formal_cases"]:
   expected=row["geometry_parameter"] if row["geometry_kind"]=="isotropic_volume" else 1.0;self.assertAlmostEqual(np.linalg.det(c.geometry_transform(row)),expected,places=13)
 def test_03_summarize_acceptance(self):
  runs=self.synthetic_runs()
  s=c.summarize(self.cfg,runs);self.assertTrue(s["scientific_gate_accepted"]);self.assertEqual(len(s["coefficient_runs"]),60);self.assertFalse(s["g3_overall_closed"])
 def test_04_energy_gate_strict(self):
  runs=self.synthetic_runs(0.020001)
  self.assertFalse(c.summarize(self.cfg,runs)["scientific_gate_accepted"])
 def test_05_other_gate_unchanged(self):
  bad=copy.deepcopy(self.cfg);bad["acceptance"]["projected_gradient_metric_hartree_strict_lt"]=2e-6
  with self.assertRaises(ValueError):c.validate_config(ROOT,bad)
 def test_06_scope(self):
  self.assertEqual(self.cfg["acceptance"]["full_grid_reference_energy_difference_abs_strict_lt_mev_per_atom"],20.0);self.assertFalse(self.cfg["scope"]["mg_enabled"])
 def test_07_fixed_basis_all_geometries(self):
  for row in self.cfg["formal_cases"][::4]:
   basis=c.build_basis(self.cfg,c.cell_for_row(ROOT,self.cfg,row),(40,40,40));self.assertEqual(basis["tangent"].shape,(64000,22));self.assertGreater(float(basis["raw_gram_eigenvalues"][0]),0.0)
 def test_08_worker_array_shape_interface(self):
  row=self.cfg["formal_cases"][-4];basis=c.build_basis(self.cfg,c.cell_for_row(ROOT,self.cfg,row),np.zeros((40,40,40)));self.assertEqual(tuple(basis["counts"]),(40,40,40))
 def test_09_variational_lower_bound_preserved(self):
  runs=self.synthetic_runs(-0.0001001);s=c.summarize(self.cfg,runs);self.assertFalse(s["scientific_gate_accepted"]);self.assertFalse(s["coefficient_runs"][0]["gates"]["variational_lower_bound"])
 def test_10_success_terminal_denominator(self):
  ids=[x["experiment_id"] for x in self.cfg["formal_cases"]];t={"status":"accepted","attempted":80,"accepted":80,"failed":0,"missing":0,"skipped":0,"retried":0,"runner_return_code":0,"attempted_ids":ids,"accepted_ids":ids,"unattempted_ids":[],"attempt_sha256":{x:"a" for x in ids},"runner_return_sha256":{x:"r" for x in ids},"run_inventory":{x:{} for x in ids},"result_sha256":{x:"s" for x in ids}}
  collector.validate_terminal(self.cfg,t,False);t["missing"]=1
  with self.assertRaises(ValueError):collector.validate_terminal(self.cfg,t,False)
 def test_11_failure_terminal_denominator(self):
  ids=[x["experiment_id"] for x in self.cfg["formal_cases"]];attempted=ids[:2];t={"status":"failed_no_retry","attempted":2,"accepted":1,"failed":1,"missing":0,"skipped":78,"retried":0,"runner_return_code":70,"attempted_ids":attempted,"accepted_ids":attempted[:1],"unattempted_ids":ids[2:],"attempt_sha256":{x:"a" for x in attempted},"runner_return_sha256":{x:"r" for x in attempted},"run_inventory":{x:{} for x in attempted},"result_sha256":{attempted[0]:"s"}}
  collector.validate_terminal(self.cfg,t,True);t["skipped"]=77
  with self.assertRaises(ValueError):collector.validate_terminal(self.cfg,t,True)
 def test_12_registered_child_environment(self):
  env=runner.environment(ROOT,self.cfg);self.assertEqual(env["PATH"],"/usr/bin:/bin");self.assertEqual(env["PYTHONPATH"],str(ROOT/"scripts"));self.assertNotIn("DYLD_LIBRARY_PATH",env)
 def test_13_failure_replays_accepted_prefix(self):
  ids=[x["experiment_id"] for x in self.cfg["formal_cases"]];rows=self.cfg["formal_cases"][:2]
  with tempfile.TemporaryDirectory() as td:
   state=Path(td);(state/"runs").mkdir();attempt_sha={};return_sha={};inventories={}
   for index,row in enumerate(rows,1):
    run=state/"runs"/f"{index:03d}_{row['experiment_id']}";run.mkdir();payloads={"attempt.json":{"schema_version":1,"status":"formal_started","experiment_id":row["experiment_id"],"attempt_number":1,"started_unix_ns":1},"command.json":{"argv":runner.command(ROOT,self.cfg,row,run),"cwd":str(ROOT),"environment":runner.environment(ROOT,self.cfg)},"resource_preflight.json":{"status":"accepted","hostname":self.cfg["runtime"]["hostname"],"logical_cpu":self.cfg["runtime"]["logical_cpu"],"smt_domain":self.cfg["runtime"]["smt_domain"],"collisions":[]},"runner_return.json":{"schema_version":1,"experiment_id":row["experiment_id"],"return_code":0 if index==1 else 70}}
    for name,obj in payloads.items():(run/name).write_bytes(c.canonical(obj))
    if index==1:(run/"result.json").write_bytes(b"{}")
    attempt_sha[row["experiment_id"]]=c.sha_path(run/"attempt.json");return_sha[row["experiment_id"]]=c.sha_path(run/"runner_return.json")
   failure={"schema_version":1,"status":"failed_no_retry","experiment_id":rows[-1]["experiment_id"],"return_code":70,"reason":"test","attempted_ids":ids[:2],"accepted_ids":ids[:1],"unattempted_ids":ids[2:],"attempt_sha256":attempt_sha,"runner_return_sha256":return_sha};(state/"failure.json").write_bytes(c.canonical(failure));t={"attempted":2,"accepted":1,"attempted_ids":ids[:2],"accepted_ids":ids[:1],"unattempted_ids":ids[2:],"runner_return_code":70,"failure_sha256":c.sha_path(state/"failure.json"),"attempt_sha256":attempt_sha,"runner_return_sha256":return_sha,"result_sha256":{ids[0]:c.sha_path(state/"runs"/f"001_{ids[0]}"/"result.json")}}
   replayed={**c.row_identity(rows[0]),"status":"accepted"}
   with mock.patch.object(collector,"replay_run_result",return_value=(replayed,np.zeros(1))) as check:
    out=collector.failure_output(ROOT,self.cfg,state,t);self.assertEqual(check.call_count,1);self.assertEqual(json.loads(out["runs.json"])["runs"][0]["experiment_id"],ids[0])
 def test_14_state_tree_rejects_extra_and_symlink(self):
  with tempfile.TemporaryDirectory() as td:
   state=Path(td);(state/"runs").mkdir();(state/"session.json").write_text("{}");(state/"terminal.json").write_text("{}");t={"attempted":0,"attempted_ids":[],"run_inventory":{}}
   collector.verify_tree(state,t,False);(state/"extra").write_text("x")
   with self.assertRaises(ValueError):collector.verify_tree(state,t,False)
   (state/"extra").unlink();os.symlink(state/"session.json",state/"link")
   with self.assertRaises(ValueError):collector.verify_tree(state,t,False)
 def test_15_inventory_rejects_symlink_and_directory(self):
  with tempfile.TemporaryDirectory() as td:
   run=Path(td);(run/"raw").write_text("x");self.assertIn("raw",runner.inventory(run));os.symlink(run/"raw",run/"link")
   with self.assertRaises(ValueError):runner.inventory(run)
   (run/"link").unlink();(run/"extra_dir").mkdir()
   with self.assertRaises(ValueError):runner.inventory(run)
 def test_16_scientific_rejection_completes_denominator(self):
  runs=self.synthetic_runs();runs[1]["status"]="completed_scientific_rejected";s=c.summarize(self.cfg,runs);self.assertTrue(s["evidence_valid"]);self.assertFalse(s["scientific_gate_accepted"]);self.assertEqual(s["formal_case_count"],80)
 def test_17_replay_loader_uses_frozen_target_cell(self):
  row=self.cfg["formal_cases"][0];observed={}
  def fake_replay(root,cfg,registered,result,density):
   cell,_=c.legacy.load_source_density(root,cfg);observed["cell"]=cell;return {"accepted":True}
  result=c.row_identity(row)
  with mock.patch.object(c.legacy,"replay_result",side_effect=fake_replay):self.assertTrue(c.replay_result(ROOT,self.cfg,row,result,np.zeros(1))["accepted"])
  np.testing.assert_allclose(observed["cell"],c.cell_for_row(ROOT,self.cfg,row),rtol=0,atol=0)
 def test_18_failure_map_tamper_rejected(self):
  ids=[x["experiment_id"] for x in self.cfg["formal_cases"]]
  with tempfile.TemporaryDirectory() as td:
   state=Path(td);(state/"failure.json").write_bytes(c.canonical({"schema_version":1,"status":"failed_no_retry","experiment_id":ids[0],"return_code":70,"reason":"x","attempted_ids":[ids[0]],"accepted_ids":[],"unattempted_ids":ids[1:],"attempt_sha256":{ids[0]:"wrong"},"runner_return_sha256":{ids[0]:"r"}}));t={"attempted_ids":[ids[0]],"accepted_ids":[],"unattempted_ids":ids[1:],"runner_return_code":70,"failure_sha256":c.sha_path(state/"failure.json"),"attempt_sha256":{ids[0]:"a"},"runner_return_sha256":{ids[0]:"r"}}
   with self.assertRaises(ValueError):collector.failure_output(ROOT,self.cfg,state,t)
 def test_19_command_argv_cwd_environment_tamper_rejected(self):
  row=self.cfg["formal_cases"][0];eid=row["experiment_id"]
  with tempfile.TemporaryDirectory() as td:
   run=Path(td);attempt={"schema_version":1,"status":"formal_started","experiment_id":eid,"attempt_number":1,"started_unix_ns":1};pre={"status":"accepted","hostname":self.cfg["runtime"]["hostname"],"logical_cpu":self.cfg["runtime"]["logical_cpu"],"smt_domain":self.cfg["runtime"]["smt_domain"],"collisions":[]};ret={"schema_version":1,"experiment_id":eid,"return_code":0};registered={"argv":runner.command(ROOT,self.cfg,row,run),"cwd":str(ROOT),"environment":runner.environment(ROOT,self.cfg)}
   for name,obj in (("attempt.json",attempt),("resource_preflight.json",pre),("runner_return.json",ret)):(run/name).write_bytes(c.canonical(obj))
   t={"attempt_sha256":{eid:c.sha_path(run/"attempt.json")},"runner_return_sha256":{eid:c.sha_path(run/"runner_return.json")}}
   for key,bad in (("argv",["/bin/false"]),("cwd","/tmp/attacker"),("environment",{"BAD":"1"})):
    payload=copy.deepcopy(registered);payload[key]=bad;(run/"command.json").write_bytes(c.canonical(payload))
    with self.assertRaises(ValueError):collector.envelope(ROOT,self.cfg,row,run,t,True)
    with self.assertRaises(ValueError):collector.envelope(ROOT,self.cfg,row,run,t,False)
    (run/"command.json").unlink()
 def test_20_session_schema_and_time_tamper_rejected(self):
  ids=[x["experiment_id"] for x in self.cfg["formal_cases"]]
  with tempfile.TemporaryDirectory() as td:
   state=Path(td);session={"schema_version":1,"protocol_revision":self.cfg["protocol_revision"],"runner_commit":"runner","config_sha256":c.sha_path(ROOT/c.CONFIG_REL),"case_ids":ids,"resource_preflight":{"status":"accepted","hostname":self.cfg["runtime"]["hostname"],"logical_cpu":self.cfg["runtime"]["logical_cpu"],"smt_domain":self.cfg["runtime"]["smt_domain"],"collisions":[]},"detached":{"pid":7,"sid":7,"parent_pid":1,"sighup_ignored":True,"no_tty":True},"started_unix_ns":10};(state/"session.json").write_bytes(c.canonical(session));t={"session_sha256":c.sha_path(state/"session.json"),"finished_unix_ns":20};collector.validate_session(ROOT,self.cfg,state,t,"runner");session["extra"]="tamper";(state/"session.json").write_bytes(c.canonical(session));t["session_sha256"]=c.sha_path(state/"session.json")
   with self.assertRaises(ValueError):collector.validate_session(ROOT,self.cfg,state,t,"runner")
 def test_21_monitor_shell_is_not_execution_process(self):
  tokens=["bash","-lc","monitor /home/shenwei01/.local/state/m_ofdft/s3_g3_al_multistructure_execution_r2_20260812"]
  self.assertFalse(c._registered_execution_process("bash",tokens))
 def test_22_exact_runner_and_worker_tokens_are_execution_processes(self):
  for script in ("run_s3_g3_al_multistructure_execution_r2.py","run_s3_g3_al_multistructure_worker_r2.py"):
   self.assertTrue(c._registered_execution_process("python3",["/usr/bin/python3",f"/tmp/{script}"]))
 def test_23_shell_substring_and_unregistered_python_are_not_collisions(self):
  self.assertFalse(c._registered_execution_process("bash",["bash","-c","python run_s3_g3_al_multistructure_execution_r2.py"]))
  self.assertFalse(c._registered_execution_process("python3",["python3","/tmp/run_s3_g3_al_multistructure_execution_r20.py"]))
 def test_24_r1_failure_evidence_is_frozen(self):
  e=json.loads((ROOT/c.CONFIG_REL).read_text())["r1_operational_failure"]
  s=json.loads((ROOT/e["summary_path"]).read_text());self.assertEqual((s["status"],s["accepted_prefix_count"],s["terminal"]["attempted"],s["terminal"]["accepted"]),("evidence_valid_operational_failure_no_retry",13,14,13))
if __name__=="__main__":unittest.main()
