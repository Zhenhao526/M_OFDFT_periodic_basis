#!/usr/bin/env python3
from __future__ import annotations
import copy,hashlib,json,math,os,socket,subprocess
from pathlib import Path
import s3_g3_wt_coefficient_common_r1 as legacy

CONFIG_REL=Path("config/S3_g3_al_iso_v097_optimizer_recovery_r5.json")
MATRIX_REL=Path("config/S3_g3_al_multistructure_matrix_r1.json")
PROTOCOL_REL=Path("docs/S3_G3_AL_ISO_V097_OPTIMIZER_RECOVERY_R5_PROTOCOL.md")
COMMON_REL=Path("scripts/s3_g3_al_iso_v097_optimizer_recovery_common_r5.py")
WORKER_REL=Path("scripts/run_s3_g3_al_iso_v097_optimizer_recovery_worker_r5.py")
RUNNER_REL=Path("scripts/run_s3_g3_al_iso_v097_optimizer_recovery_r5.py")
COLLECTOR_REL=Path("scripts/collect_s3_g3_al_iso_v097_optimizer_recovery_r5.py")
VALIDATOR_REL=Path("scripts/validate_s3_g3_al_iso_v097_optimizer_recovery_r5.py")
TEST_REL=Path("tests/test_s3_g3_al_iso_v097_optimizer_recovery_r5.py")
IMPLEMENTATION_PATHS={str(x) for x in (CONFIG_REL,PROTOCOL_REL,COMMON_REL,WORKER_REL,RUNNER_REL,COLLECTOR_REL,VALIDATOR_REL,TEST_REL)}
require,canonical,git,sha_path,sha_bytes,write_exclusive=legacy.require,legacy.canonical,legacy.git,legacy.sha_path,legacy.sha_bytes,legacy.write_exclusive

def _git_bytes(root,commit,path): return subprocess.run(["git","show",f"{commit}:{path}"],cwd=root,check=True,stdout=subprocess.PIPE).stdout
def _load_registered(root,identity):
 p=root/identity["path"];require(p.is_file() and not p.is_symlink(),f"registered file absent: {p}");data=p.read_bytes();require(sha_bytes(data)==identity["sha256"],f"registered SHA differs: {p}");require(_git_bytes(root,identity["commit"],identity["path"])==data,f"registered Git bytes differ: {p}");return json.loads(data)
def execution_config(root): return json.loads((root/CONFIG_REL).read_text())
def source_execution_config(root,e):
 return _load_registered(root,{"commit":e["source_evidence"]["commit"],"path":e["source_evidence"]["config_path"],"sha256":e["source_evidence"]["config_sha256"]})

def geometry_transform(row):
 import numpy as np
 kind,p=row["geometry_kind"],float(row["geometry_parameter"]);I=np.eye(3)
 if kind=="isotropic_volume": return I*p**(1/3)
 if kind=="volume_conserving_tetragonal": return np.diag([1+p,(1+p)**-0.5,(1+p)**-0.5])
 if kind=="volume_conserving_orthorhombic": return np.diag([1+p,1/(1+p),1])
 if kind=="simple_shear_xy":
  F=I.copy();F[0,1]=p;return F
 if kind=="simple_shear_yz":
  F=I.copy();F[1,2]=p;return F
 if kind=="volume_normalized_symmetric_trigonal_shear":
  F=I.copy();F[0,1]=F[1,0]=F[0,2]=F[2,0]=F[1,2]=F[2,1]=p;return F/np.linalg.det(F)**(1/3)
 raise ValueError(f"unknown geometry: {kind}")

def load(root):
 e=execution_config(root);source=source_execution_config(root,e);c=copy.deepcopy(science_config_from_source(root,source));c["protocol_revision"]=e["protocol_revision"];c["status"]=e["status"];c["implementation_commit"]=e["implementation_commit"];c["base_commit"]=e["base_commit"]
 c["scope"].update({"gate":"G3_Al_iso_v097_optimizer_recovery_subgate","atom_count":1})
 c["formal_cases"]=copy.deepcopy(e["formal_cases"]);c["acceptance"]["full_grid_reference_energy_difference_abs_strict_lt_mev_per_atom"]=source["policy"]["energy_difference_abs_strict_lt_mev_per_atom"];c["execution"]=copy.deepcopy(e["execution"]);c["output_files"]=e["output_files"];c["recovery_policy"]=copy.deepcopy(e["recovery_policy"]);return c

def science_config_from_source(root,source):
 identity={"commit":source["r1_scientific_source"]["preregistration_commit"],"path":source["r1_scientific_source"]["config_path"],"sha256":source["r1_scientific_source"]["config_sha256"]}
 return _load_registered(root,identity)

def validate_config(root,c):
 e=execution_config(root);source=source_execution_config(root,e);require(e["schema_version"]==1 and e["protocol_revision"]=="S3-G3-AL-ISO-V097-OPTIMIZER-RECOVERY-20260813-R5","execution schema differs")
 require(e["base_commit"]=="4b1f2188567b5645e0a76d400879f9572f15d230","base differs")
 r4=e["r4_operational_failure"];require(r4=={"closure_commit":"4b1f2188567b5645e0a76d400879f9572f15d230","closure_path":"analysis/s3/g3_al_iso_v097_optimizer_recovery_r4_20260813/operational_failure_closure.json","closure_sha256":"e20c470bb7ce098f9fddeeb63c4ca0b711e6b9c23e998935fd9670c18844cb0e","terminal_sha256":"415518157edfbeb25e8afc0f80c47f1a0b2016b1422fa71ac7e581fc627ce630","failure_sha256":"08f4c9292de10da0e5c1e2e56151c2091a2b43576c4a6871262c272882884a67","accepted_prefix":1,"failed_experiment_id":"S3-20260813-382","disposition":"operational_failure_closed_no_retry"},"R4 closure identity differs")
 r4path=root/r4["closure_path"];require(sha_path(r4path)==r4["closure_sha256"] and _git_bytes(root,r4["closure_commit"],r4["closure_path"])==r4path.read_bytes(),"R4 closure bytes differ");r4data=json.loads(r4path.read_text());require(r4data["status"]==r4["disposition"] and r4data["state_inventory"]["terminal_sha256"]==r4["terminal_sha256"] and r4data["state_inventory"]["failure_sha256"]==r4["failure_sha256"] and r4data["denominator"]["accepted_prefix"]==1 and r4data["denominator"]["failed_experiment_id"]==r4["failed_experiment_id"],"R4 closure content differs")
 identity=e["source_evidence"];require(identity=={"commit":"b4176a27f96fbad1a0812403e8a2b170d24c1543","config_path":"config/S3_g3_al_multistructure_execution_r3.json","config_sha256":"d2085635de41219f665d6a36e285497d11a40041eef29fd2ba4276b63f51bb15","summary_path":"analysis/s3/g3_al_multistructure_execution_r3_20260813/summary.json","summary_sha256":"aef2b60d84babdad5e02d0e108228addfcad2276fe19c1f74edae3c32fcc6274","runs_path":"analysis/s3/g3_al_multistructure_execution_r3_20260813/runs.json","runs_sha256":"0db6b34d8d0f2e6052a8f68d5a11d6acd70a7c1224b9c15a0505de9968c619cb","terminal_sha256":"449482dae5b9843ffa839180a3c55de7add35b6f0f114141575f2dadf3383239","disposition":"evidence_valid_s3_al_twenty_structure_coefficient_subgate_rejected","accepted_structure_count":19,"failed_structure_id":"iso_v097","failed_uniform_id":"S3-20260813-310"},"source evidence identity differs")
 for key in ("config","summary","runs"):
  path=root/identity[f"{key}_path"];require(sha_path(path)==identity[f"{key}_sha256"] and _git_bytes(root,identity["commit"],identity[f"{key}_path"])==path.read_bytes(),f"source {key} bytes differ")
 summary=json.loads((root/identity["summary_path"]).read_text());require(summary["status"]==identity["disposition"] and summary["scientific_gate_accepted"] is False and sum(x["accepted"] for x in summary["structures"])==19,"source summary differs")
 failed=next(x for x in summary["structures"] if x["structure_id"]=="iso_v097");require(failed["accepted"] is False and failed["coefficient_energy_spread_mev_per_atom"]>6995,"source failure differs")
 cases=e["formal_cases"];require([x["experiment_id"] for x in cases]==[f"S3-20260813-{i:03d}" for i in range(391,395)] and len(cases)==4,"case denominator differs")
 require([x["route"] for x in cases]==["full_grid_WT_reference"]+["coefficient_23_function"]*3 and [x["initialization"] for x in cases]==["uniform","uniform","atomic_superposition_projection","low_g_perturbation_projection"],"case matrix differs")
 require(all(x["structure_id"]=="iso_v097" and x["geometry_kind"]=="isotropic_volume" and x["geometry_parameter"]==0.97 and x["grid"]==[40,40,40] for x in cases),"geometry differs")
 require(c["formal_cases"]==cases and c["acceptance"]["full_grid_reference_energy_difference_abs_strict_lt_mev_per_atom"]==20.0,"resolved science differs")
 legacy_acceptance=science_config_from_source(root,source)["acceptance"]
 for key,value in legacy_acceptance.items():
  expected=20.0 if key=="full_grid_reference_energy_difference_abs_strict_lt_mev_per_atom" else value
  require(c["acceptance"].get(key)==expected,f"acceptance changed: {key}")
 expected_initial={"uniform":"ecb9a5c341dd8a61f9a6e6a8290a1838e919b6c6afde890e83000208056fe29a","atomic_superposition_projection":"6c81a5cb131b7aa2aad5d27dbd452547254460729376511889772803a27ea3e2","low_g_perturbation_projection":"ae00e78b01887905f4c1368e833ac77458fd0849bd4b4221c5586fc1862ac886"}
 expected_policy={"algorithm":"feasible_trust_radius_Armijo_BFGS_with_active_boundary_projection","same_algorithm_for_all_three_initializations":True,"registered_uniform_density_preserved":True,"continuation_or_initial_substitution_forbidden":True,"trust_radius_initial":0.05,"trust_radius_min":1e-6,"trust_radius_max":0.2,"trust_radius_grow":1.5,"trust_radius_shrink":0.25,"active_density_threshold":1e-8,"stagnation_energy_hartree":1e-12,"stagnation_window":5,"max_iterations":600,"registered_initial_density_sha256":expected_initial}
 require(e["recovery_policy"]==expected_policy and c["recovery_policy"]==expected_policy,"recovery policy differs")
 old_runs=json.loads((root/identity["runs_path"]).read_text())["runs"]
 observed={x["initialization"]:x["registered_initial_density_sha256"] for x in old_runs if x["experiment_id"] in {"S3-20260813-310","S3-20260813-311","S3-20260813-312"}}
 require(observed==expected_initial,"registered initial-density source map differs")
 require(e["execution"]=={"execution_worktree_root":"/home/shenwei01/wt_s3_g3_al_iso_v097_optimizer_recovery_r5_20260813","state_root":"/home/shenwei01/.local/state/m_ofdft/s3_g3_al_iso_v097_optimizer_recovery_r5_20260813","analysis_root":"analysis/s3/g3_al_iso_v097_optimizer_recovery_r5_20260813","formal_case_count":4,"no_retry":True,"new_electronic_structure_solver_run_count":4,"child_environment":{"HOME":"/home/shenwei01","PATH":"/usr/bin:/bin","OMP_NUM_THREADS":"1","MKL_NUM_THREADS":"1","OPENBLAS_NUM_THREADS":"1","PYTHONDONTWRITEBYTECODE":"1","PYTHONNOUSERSITE":"1"}},"execution differs")
 for row in cases:
  import numpy as np
  F=geometry_transform(row);require(np.all(np.isfinite(F)) and np.linalg.det(F)>0,"invalid deformation")
  expected=float(row["geometry_parameter"]) if row["geometry_kind"]=="isotropic_volume" else 1.0;require(abs(float(np.linalg.det(F))-expected)<2e-14,"deformation determinant differs")
 return c

def source_exact(root,c): return legacy.source_exact(root,c)
def validate_runtime(root,c): return legacy.validate_runtime(root,c)
def _cpu_set(text): return legacy._cpu_set(text)
def _registered_execution_process(comm,tokens):
 names={"run_s3_g3_al_iso_v097_optimizer_recovery_r5.py","run_s3_g3_al_iso_v097_optimizer_recovery_worker_r5.py"}
 return comm.startswith("python") and any(Path(token).name in names for token in tokens)
def resource_preflight(c,proc_root=Path('/proc'),current_pid=None,topology_reader=None,hostname=None):
 hostname=socket.gethostname() if hostname is None else hostname;require(hostname==c["runtime"]["hostname"],"hostname differs");cpu=c["runtime"]["logical_cpu"];domain=set(c["runtime"]["smt_domain"])
 topology_reader=topology_reader or (lambda n: Path(f"/sys/devices/system/cpu/cpu{n}/topology/thread_siblings_list").read_text())
 require(_cpu_set(topology_reader(cpu))==domain,"SMT topology differs")
 ancestors=set();pid=os.getpid() if current_pid is None else int(current_pid)
 while pid>1 and pid not in ancestors:
  ancestors.add(pid)
  try:
   fields={x.split(':',1)[0]:x.split(':',1)[1].strip() for x in (proc_root/str(pid)/'status').read_text().splitlines() if ':' in x};pid=int(fields["PPid"])
  except Exception:break
 collisions=[]
 for entry in proc_root.iterdir():
  if not entry.name.isdigit() or int(entry.name) in ancestors:continue
  try:
   status=(entry/'status').read_text();fields={x.split(':',1)[0]:x.split(':',1)[1].strip() for x in status.splitlines() if ':' in x};comm=fields.get('Name','').lower();tokens=[x.decode(errors='replace') for x in (entry/'cmdline').read_bytes().split(b'\0') if x];allowed=_cpu_set(fields.get('Cpus_allowed_list',''))
  except Exception:continue
  if _registered_execution_process(comm,tokens) and allowed&domain:collisions.append({"pid":int(entry.name),"comm":comm,"allowed":sorted(allowed)})
 require(not collisions,f"multistructure workflow collision: {collisions}")
 return {"status":"accepted","hostname":hostname,"logical_cpu":cpu,"smt_domain":sorted(domain),"collisions":[]}
def source_cell(root,c): return legacy.load_source_density(root,c)[0]
def cell_for_row(root,c,row):
 import numpy as np
 return np.asarray(source_cell(root,c))@geometry_transform(row).T
def build_basis(c,cell,shape):
 import numpy as np
 import s2_g2_al1_pilot_common_r1 as pilot
 counts=np.asarray(shape.shape if hasattr(shape,"shape") and len(shape.shape)==3 else shape,dtype=int);require(counts.shape==(3,) and np.all(counts>0),"grid shape differs");frac=pilot.fractional_grid(counts);volume=float(abs(np.linalg.det(cell)));dv=volume/int(np.prod(counts));r2=pilot.minimum_image_r2(frac,cell)
 atomic=[]
 for alpha in c["basis"]["alpha_bohr_minus2"]:
  f=np.exp(-float(alpha)*r2);f/=float(f.sum(dtype=np.float64)*dv);atomic.append(f)
 atomic=np.stack(atomic,axis=1);compensated=atomic-np.mean(atomic,axis=0,keepdims=True)
 waves=[]
 for vector in c["basis"]["expected_half_space_vectors"]:
  phase=2*np.pi*(frac@np.asarray(vector,dtype=float));waves.extend((np.cos(phase),np.sin(phase)))
 low=np.column_stack(waves);constant=np.full((len(frac),1),1/volume);low_block=np.column_stack((constant,low));low_q,_=np.linalg.qr(low_block,mode="reduced");complementary=compensated-low_q@(low_q.T@compensated);raw=np.column_stack((low,complementary));raw-=raw.sum(axis=0,keepdims=True)/raw.shape[0];gram=raw.T@raw*dv;eigval=np.linalg.eigvalsh(gram);require(float(eigval.min())>0,"charge-tangent basis not positive definite");q,_=np.linalg.qr(raw*math.sqrt(dv),mode="reduced");tangent=q/math.sqrt(dv);require(float(np.max(np.abs(tangent.T@tangent*dv-np.eye(22))))<2e-12,"orthonormal tangent differs");base=np.full(len(frac),c["source"]["expected_electrons"]/volume)
 return {"base":base,"tangent":tangent,"counts":counts,"volume":volume,"dv":dv,"cell":np.asarray(cell),"raw_gram_eigenvalues":eigval,"basis_sha256":sha_bytes(np.ascontiguousarray(np.column_stack((constant,raw)),dtype=np.float64).tobytes())}
def build_evaluator(c,basis): return legacy.build_evaluator(c,basis)
def build_registered_initial(c,row,basis,evaluator,ions): return legacy.build_registered_initial(c,row,basis,evaluator,ions)
def euler_residual_hartree(*a): return legacy.euler_residual_hartree(*a)
def fraction_to_boundary(*a): return legacy.fraction_to_boundary(*a)
def validate_optimizer_disposition(*a): return legacy.validate_optimizer_disposition(*a)
def validate_terminal_trajectory_link(*a): return legacy.validate_terminal_trajectory_link(*a)
def row_identity(row): return {k:row[k] for k in ("experiment_id","role","structure_id","geometry_kind","geometry_parameter","route","initialization","grid")}

def replay_result(root,c,row,result,density):
 require({k:result[k] for k in row_identity(row)}==row_identity(row),"result geometry identity differs")
 target_cell=cell_for_row(root,c,row);original_source=legacy.load_source_density;original_basis=legacy.build_basis
 legacy.load_source_density=lambda _root,_c:(target_cell,None);legacy.build_basis=build_basis
 try:
  old_limit=c["optimization"]["coefficient_max_iterations"]
  if row["route"]=="coefficient_23_function":c["optimization"]["coefficient_max_iterations"]=c["recovery_policy"]["max_iterations"]
  try:replay=legacy.replay_result(root,c,row,result,density)
  finally:c["optimization"]["coefficient_max_iterations"]=old_limit
  validate_recovery_trajectory(c,row,result)
  require(result["registered_initial_density_sha256"]==c["recovery_policy"]["registered_initial_density_sha256"][row["initialization"]],"cross-revision initial density SHA differs")
  if row["route"]=="coefficient_23_function":replay_recovery_algorithm(root,c,row,result,density,target_cell)
  return replay
 finally:legacy.load_source_density=original_source;legacy.build_basis=original_basis

def validate_recovery_trajectory(c,row,result):
 if row["route"]=="full_grid_WT_reference":
  require(result.get("recovery_algorithm")=="not_applicable_full_grid","full-grid recovery identity differs");return
 require(result.get("recovery_algorithm")==c["recovery_policy"]["algorithm"],"recovery algorithm differs")
 events=result.get("recovery_events");radii=result.get("accepted_step_trust_radii");coeff=result["accepted_step_coefficients"]
 require(isinstance(events,list) and isinstance(radii,list) and len(radii)==len(coeff)>0,"recovery trajectory denominator differs")
 require(all(isinstance(x,(int,float)) and c["recovery_policy"]["trust_radius_min"]<=x<=c["recovery_policy"]["trust_radius_max"] for x in radii),"trust radius differs")
 allowed={"boundary_projection","hessian_reset_non_descent","hessian_reset_curvature","stagnation_reset","trust_radius_shrink"}
 for event in events:
  require(set(event)=={"iteration","kind","trust_radius"} and isinstance(event["iteration"],int) and 1<=event["iteration"]<=c["recovery_policy"]["max_iterations"] and event["kind"] in allowed and c["recovery_policy"]["trust_radius_min"]<=event["trust_radius"]<=c["recovery_policy"]["trust_radius_max"],"recovery event differs")
 require(result["iterations"]<=c["recovery_policy"]["max_iterations"],"recovery iteration count differs")

def replay_recovery_algorithm(root,c,row,result,registered_density,target_cell):
 import numpy as np
 from dftpy.field import DirectField
 from run_s3_g3_al_iso_v097_optimizer_recovery_worker_r5 import optimize_coefficients
 cell=np.asarray(target_cell,dtype=float);shape=tuple(row["grid"]);basis=build_basis(c,cell,shape);ions,grid,evaluator=build_evaluator(c,basis);y0,initial,initial_sha=build_registered_initial(c,row,basis,evaluator,ions);require(initial_sha==c["recovery_policy"]["registered_initial_density_sha256"][row["initialization"]],"replayed registered initial SHA differs")
 cache={}
 def field(values):f=DirectField(grid=grid);f[:]=np.asarray(values).reshape(shape);return f
 def evaluate(y):
  array=np.asarray(y,dtype=np.float64);key=array.tobytes()
  if key not in cache:
   values=basis["base"]+basis["tangent"]@array;require(float(values.min())>=0,"replayed negative trial density");out=evaluator.get_energy_potential(field(values),calcType={"E","V"});cache[key]=(float(out.energy),basis["tangent"].T@np.asarray(out.potential,dtype=float).reshape(-1)*basis["dv"],values,np.asarray(out.potential,dtype=float).reshape(-1))
  return cache[key]
 observed=optimize_coefficients(evaluate,y0,basis,c["recovery_policy"],c["acceptance"]["projected_gradient_metric_hartree_strict_lt"])
 require(observed["claimed"]==result["optimizer_claimed_converged"] and observed["iterations"]==result["iterations"],"replayed recovery disposition differs")
 require(canonical(observed["accepted_coefficients"])==canonical(result["accepted_step_coefficients"]),"replayed recovery coefficients differ")
 require(canonical(observed["accepted_energies"])==canonical(result["accepted_step_energies_hartree"]),"replayed recovery energies differ")
 require(canonical(observed["trust_radii"])==canonical(result["accepted_step_trust_radii"]) and canonical(observed["events"])==canonical(result["recovery_events"]),"replayed recovery policy trace differs")
 require(np.array_equal(np.asarray(observed["density"],dtype=np.float64),np.asarray(registered_density,dtype=np.float64).reshape(-1)),"replayed recovery terminal density differs")

def source_twenty_structure_portrait(root,e):
 return json.loads((root/e["source_evidence"]["summary_path"]).read_text())

def summarize(c,runs):
 require(len(runs)==4 and [r["experiment_id"] for r in runs]==[x["experiment_id"] for x in c["formal_cases"]],"run denominator differs");a=c["acceptance"];ref=runs[0];coeff=runs[1:];require(ref["route"]=="full_grid_WT_reference" and all(x["route"]=="coefficient_23_function" for x in coeff),"route denominator differs")
 spread=(max(x["energy_ev_per_atom"] for x in coeff)-min(x["energy_ev_per_atom"] for x in coeff))*1000;rows=[]
 for r in coeff:
  delta=(r["energy_ev_per_atom"]-ref["energy_ev_per_atom"])*1000;gates={"run_status":r["status"]=="accepted","electron_number":r["electron_number_absolute_error"]<a["electron_number_absolute_error_strict_lt"],"negative_density":r["negative_density_fraction"]<a["negative_density_fraction_strict_lt"],"minimum_density":r["minimum_density_electron_per_bohr3"]>=a["minimum_density_greater_than_or_equal_electron_per_bohr3"],"projected_gradient":r["projected_gradient_metric_hartree"]<a["projected_gradient_metric_hartree_strict_lt"],"gradient_finite_difference":r["gradient_finite_difference"]["accepted"],"accepted_steps_monotonic":r["accepted_steps_monotonic"],"energy_reference":abs(delta)<a["full_grid_reference_energy_difference_abs_strict_lt_mev_per_atom"],"variational_lower_bound":delta>=a["coefficient_variational_lower_bound_tolerance_mev_per_atom"],"density_l2":r["density_relative_l2_vs_full_grid"]<a["density_relative_l2_strict_lt"],"three_initialization_spread":spread<a["three_initialization_energy_spread_strict_lt_mev_per_atom"]}
  rows.append({"experiment_id":r["experiment_id"],"initialization":r["initialization"],"reference_id":ref["experiment_id"],"reference_energy_difference_mev_per_atom":abs(delta),"coefficient_minus_reference_energy_mev_per_atom":delta,"density_relative_l2":r["density_relative_l2_vs_full_grid"],"three_initialization_energy_spread_mev_per_atom":spread,"recovery_event_count":len(r["recovery_events"]),"gates":gates,"accepted":all(gates.values())})
 accepted=all(x["accepted"] for x in rows);return {"schema_version":1,"protocol_revision":c["protocol_revision"],"status":"accepted_s3_al_iso_v097_optimizer_recovery" if accepted else "evidence_valid_s3_al_iso_v097_optimizer_recovery_rejected","evidence_valid":True,"scientific_gate_accepted":accepted,"formal_case_count":4,"structure_id":"iso_v097","reference_id":ref["experiment_id"],"full_grid_euler_residual_hartree_diagnostic":ref["full_grid_euler_residual_hartree"],"coefficient_runs":rows,"three_initialization_energy_spread_mev_per_atom":spread,"combines_with_r3_accepted_structure_count":19,"al_twenty_structure_coefficient_subgate_accepted":accepted,"g3_overall_closed":False,"s4_authorized":False,"g2c_performance_rejection_preserved":True,"next_action":"implement_Mg_representation_and_twenty_structure_S3_revision" if accepted else "freeze_optimizer_robustness_rejection_and_keep_G3_open"}

def render(c,runs):
 s=summarize(c,runs);cols=["experiment_id","structure_id","initialization","reference_energy_difference_mev_per_atom","density_relative_l2","three_initialization_energy_spread_mev_per_atom","accepted"];lines=['\t'.join(cols)]+['\t'.join(str(x[k]) for k in cols) for x in s["coefficient_runs"]]
 readme=(f"# S3/G3 Al iso_v097 optimizer recovery R5\n\nStatus: `{s['status']}`. Four fresh cases recompute one full-grid reference and all three registered coefficient initializations with one uniform optimizer-recovery policy. R3 remains immutable. G3 remains open; S4 is unauthorized.\n").encode();return {"README.md":readme,"runs.json":canonical({"schema_version":1,"protocol_revision":c["protocol_revision"],"runs":runs}),"gates.tsv":('\n'.join(lines)+'\n').encode(),"summary.json":canonical(s)}
