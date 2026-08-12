#!/usr/bin/env python3
from __future__ import annotations
import copy,hashlib,json,math,os,subprocess
from pathlib import Path
import s3_g3_wt_coefficient_common_r1 as legacy

CONFIG_REL=Path("config/S3_g3_al_multistructure_execution_r1.json")
MATRIX_REL=Path("config/S3_g3_al_multistructure_matrix_r1.json")
PROTOCOL_REL=Path("docs/S3_G3_AL_MULTISTRUCTURE_EXECUTION_R1_PROTOCOL.md")
COMMON_REL=Path("scripts/s3_g3_al_multistructure_common_r1.py")
WORKER_REL=Path("scripts/run_s3_g3_al_multistructure_worker_r1.py")
RUNNER_REL=Path("scripts/run_s3_g3_al_multistructure_execution_r1.py")
COLLECTOR_REL=Path("scripts/collect_s3_g3_al_multistructure_execution_r1.py")
VALIDATOR_REL=Path("scripts/validate_s3_g3_al_multistructure_execution_r1.py")
TEST_REL=Path("tests/test_s3_g3_al_multistructure_execution_r1.py")
IMPLEMENTATION_PATHS={str(x) for x in (CONFIG_REL,PROTOCOL_REL,COMMON_REL,WORKER_REL,RUNNER_REL,COLLECTOR_REL,VALIDATOR_REL,TEST_REL)}
require,canonical,git,sha_path,sha_bytes,write_exclusive=legacy.require,legacy.canonical,legacy.git,legacy.sha_path,legacy.sha_bytes,legacy.write_exclusive

def _git_bytes(root,commit,path): return subprocess.run(["git","show",f"{commit}:{path}"],cwd=root,check=True,stdout=subprocess.PIPE).stdout
def _load_registered(root,identity):
 p=root/identity["path"];require(p.is_file() and not p.is_symlink(),f"registered file absent: {p}");data=p.read_bytes();require(sha_bytes(data)==identity["sha256"],f"registered SHA differs: {p}");require(_git_bytes(root,identity["commit"],identity["path"])==data,f"registered Git bytes differ: {p}");return json.loads(data)
def execution_config(root): return json.loads((root/CONFIG_REL).read_text())
def matrix_config(root,e): return _load_registered(root,e["matrix"])
def science_config(root,e):
 identity={"commit":e["r1_scientific_source"]["preregistration_commit"],"path":e["r1_scientific_source"]["config_path"],"sha256":e["r1_scientific_source"]["config_sha256"]}
 return _load_registered(root,identity)

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

def formal_cases(matrix):
 out=[];n=101
 for structure in matrix["structures"]:
  base={"role":"multistructure","structure_id":structure["id"],"geometry_kind":structure["kind"],"geometry_parameter":structure["parameter"],"volume_ratio":1.0,"grid":matrix["grid"]}
  out.append({**base,"experiment_id":f"S3-20260812-{n:03d}","route":"full_grid_WT_reference","initialization":"uniform"});n+=1
  for init in matrix["coefficient_initializations"]:
   out.append({**base,"experiment_id":f"S3-20260812-{n:03d}","route":"coefficient_23_function","initialization":init});n+=1
 return out

def load(root):
 e=execution_config(root);m=matrix_config(root,e);c=copy.deepcopy(science_config(root,e));c["protocol_revision"]=e["protocol_revision"];c["status"]=e["status"];c["implementation_commit"]=e["implementation_commit"];c["base_commit"]=e["base_commit"]
 c["scope"].update({"gate":"G3_Al_twenty_structure_fixed_WT_coefficient_subgate","atom_count":1})
 c["formal_cases"]=formal_cases(m);c["acceptance"]["full_grid_reference_energy_difference_abs_strict_lt_mev_per_atom"]=e["policy"]["energy_difference_abs_strict_lt_mev_per_atom"];c["execution"]=copy.deepcopy(e["execution"]);c["output_files"]=e["output_files"];c["matrix_identity"]={"commit":e["matrix"]["commit"],"sha256":e["matrix"]["sha256"]};return c

def validate_config(root,c):
 e=execution_config(root);m=matrix_config(root,e);require(e["schema_version"]==1 and e["protocol_revision"]=="S3-G3-AL-MULTISTRUCTURE-EXECUTION-20260812-R1","execution schema differs")
 require(e["policy"]=={"commit":"3f2322c5d91486abf2e8a6894ec9033c9d7087bf","summary_path":"analysis/s3/g3_energy_acceptance_policy_r2_20260812/summary.json","summary_sha256":"179e2d57901d6deea841c0c48a7207678d2722fbda0ccc8ddd9030dbae0c2b9e","energy_difference_abs_strict_lt_mev_per_atom":20.0,"full_grid_euler_gate_role":"diagnostic_only","all_other_coefficient_route_gates_unchanged":True},"policy differs")
 policy_path=root/e["policy"]["summary_path"];require(sha_path(policy_path)==e["policy"]["summary_sha256"] and _git_bytes(root,e["policy"]["commit"],e["policy"]["summary_path"])==policy_path.read_bytes(),"policy evidence bytes differ");policy=json.loads(policy_path.read_text());require(policy["status"]=="accepted_s3_al_v100_coefficient_pilot_r2" and policy["new_energy_limit_mev_per_atom"]==20.0,"policy evidence differs")
 cases=formal_cases(m);require(len(cases)==80 and [x["experiment_id"] for x in cases]==[f"S3-20260812-{i:03d}" for i in range(101,181)],"case denominator differs")
 require(sum(x["route"]=="full_grid_WT_reference" for x in cases)==20 and sum(x["route"]=="coefficient_23_function" for x in cases)==60,"route denominator differs")
 require(c["formal_cases"]==cases and c["acceptance"]["full_grid_reference_energy_difference_abs_strict_lt_mev_per_atom"]==20.0,"resolved science differs")
 legacy_acceptance=science_config(root,e)["acceptance"]
 for key,value in legacy_acceptance.items():
  expected=20.0 if key=="full_grid_reference_energy_difference_abs_strict_lt_mev_per_atom" else value
  require(c["acceptance"].get(key)==expected,f"acceptance changed: {key}")
 require(e["execution"]=={"execution_worktree_root":"/home/shenwei01/wt_s3_g3_al_multistructure_execution_r1_20260812","state_root":"/home/shenwei01/.local/state/m_ofdft/s3_g3_al_multistructure_execution_r1_20260812","analysis_root":"analysis/s3/g3_al_multistructure_execution_r1_20260812","formal_case_count":80,"no_retry":True,"new_electronic_structure_solver_run_count":80,"child_environment":{"HOME":"/home/shenwei01","PATH":"/usr/bin:/bin","OMP_NUM_THREADS":"1","MKL_NUM_THREADS":"1","OPENBLAS_NUM_THREADS":"1","PYTHONDONTWRITEBYTECODE":"1","PYTHONNOUSERSITE":"1"}},"execution differs")
 for row in cases:
  import numpy as np
  F=geometry_transform(row);require(np.all(np.isfinite(F)) and np.linalg.det(F)>0,"invalid deformation")
  expected=float(row["geometry_parameter"]) if row["geometry_kind"]=="isotropic_volume" else 1.0;require(abs(float(np.linalg.det(F))-expected)<2e-14,"deformation determinant differs")
 return c

def source_exact(root,c): return legacy.source_exact(root,c)
def validate_runtime(root,c): return legacy.validate_runtime(root,c)
def _cpu_set(text): return legacy._cpu_set(text)
def resource_preflight(c):
 base=legacy.resource_preflight(c);domain=set(c["runtime"]["smt_domain"]);ancestors=set();pid=os.getpid()
 while pid>1 and pid not in ancestors:
  ancestors.add(pid)
  try:
   fields={x.split(':',1)[0]:x.split(':',1)[1].strip() for x in (Path('/proc')/str(pid)/'status').read_text().splitlines() if ':' in x};pid=int(fields["PPid"])
  except Exception:break
 collisions=[]
 for entry in Path('/proc').iterdir():
  if not entry.name.isdigit() or int(entry.name) in ancestors:continue
  try:
   status=(entry/'status').read_text();fields={x.split(':',1)[0]:x.split(':',1)[1].strip() for x in status.splitlines() if ':' in x};cmd=(entry/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace').lower();allowed=_cpu_set(fields.get('Cpus_allowed_list',''))
  except Exception:continue
  if 's3_g3_al_multistructure' in cmd and allowed&domain:collisions.append({"pid":int(entry.name),"comm":fields.get('Name','').lower(),"allowed":sorted(allowed)})
 require(not collisions,f"multistructure workflow collision: {collisions}");require(base["status"]=="accepted" and base["collisions"]==[],"legacy preflight differs");return base
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
 original=legacy.load_source_density;legacy.load_source_density=lambda _root,_c:(cell_for_row(root,c,row),None)
 try: return legacy.replay_result(root,c,row,result,density)
 finally: legacy.load_source_density=original

def summarize(c,runs):
 import numpy as np
 require(len(runs)==80 and [r["experiment_id"] for r in runs]==[x["experiment_id"] for x in c["formal_cases"]],"run denominator differs");a=c["acceptance"];by_structure={}
 for r in runs: by_structure.setdefault(r["structure_id"],[]).append(r)
 structures=[];coefficient_rows=[]
 for sid,group in by_structure.items():
  ref=next(x for x in group if x["route"]=="full_grid_WT_reference");coeff=[x for x in group if x["route"]=="coefficient_23_function"];require(len(coeff)==3,"coefficient group differs")
  spread=(max(x["energy_ev_per_atom"] for x in coeff)-min(x["energy_ev_per_atom"] for x in coeff))*1000
  for r in coeff:
   delta=(r["energy_ev_per_atom"]-ref["energy_ev_per_atom"])*1000;gates={"run_status":r["status"]=="accepted","electron_number":r["electron_number_absolute_error"]<a["electron_number_absolute_error_strict_lt"],"negative_density":r["negative_density_fraction"]<a["negative_density_fraction_strict_lt"],"minimum_density":r["minimum_density_electron_per_bohr3"]>=a["minimum_density_greater_than_or_equal_electron_per_bohr3"],"projected_gradient":r["projected_gradient_metric_hartree"]<a["projected_gradient_metric_hartree_strict_lt"],"gradient_finite_difference":r["gradient_finite_difference"]["accepted"],"accepted_steps_monotonic":r["accepted_steps_monotonic"],"energy_reference":abs(delta)<a["full_grid_reference_energy_difference_abs_strict_lt_mev_per_atom"],"variational_lower_bound":delta>=a["coefficient_variational_lower_bound_tolerance_mev_per_atom"],"density_l2":r["density_relative_l2_vs_full_grid"]<a["density_relative_l2_strict_lt"],"three_initialization_spread":spread<a["three_initialization_energy_spread_strict_lt_mev_per_atom"]}
   coefficient_rows.append({"experiment_id":r["experiment_id"],"structure_id":sid,"initialization":r["initialization"],"reference_id":ref["experiment_id"],"reference_energy_difference_mev_per_atom":abs(delta),"coefficient_minus_reference_energy_mev_per_atom":delta,"density_relative_l2":r["density_relative_l2_vs_full_grid"],"three_initialization_energy_spread_mev_per_atom":spread,"gates":gates,"accepted":all(gates.values())})
  structures.append({"structure_id":sid,"reference_id":ref["experiment_id"],"full_grid_euler_residual_hartree_diagnostic":ref["full_grid_euler_residual_hartree"],"coefficient_energy_spread_mev_per_atom":spread,"accepted":all(x["accepted"] for x in coefficient_rows if x["structure_id"]==sid)})
 accepted=all(x["accepted"] for x in coefficient_rows);return {"schema_version":1,"protocol_revision":c["protocol_revision"],"status":"accepted_s3_al_twenty_structure_coefficient_subgate" if accepted else "evidence_valid_s3_al_twenty_structure_coefficient_subgate_rejected","evidence_valid":True,"scientific_gate_accepted":accepted,"formal_case_count":80,"structure_count":20,"coefficient_case_count":60,"full_grid_reference_case_count":20,"coefficient_runs":coefficient_rows,"structures":structures,"full_grid_euler_role":"diagnostic_only","g3_overall_closed":False,"s4_authorized":False,"g2c_performance_rejection_preserved":True,"next_action":"implement_Mg_representation_and_twenty_structure_S3_revision" if accepted else "freeze_rejection_and_keep_G3_open"}

def render(c,runs):
 s=summarize(c,runs);cols=["experiment_id","structure_id","initialization","reference_energy_difference_mev_per_atom","density_relative_l2","three_initialization_energy_spread_mev_per_atom","accepted"];lines=['\t'.join(cols)]+['\t'.join(str(x[k]) for k in cols) for x in s["coefficient_runs"]]
 readme=(f"# S3/G3 Al twenty-structure fixed-WT coefficient execution R1\n\nStatus: `{s['status']}`. Eighty fresh cases cover twenty registered geometries. Full-grid Euler residuals are diagnostic. G3 overall and S4 remain closed.\n").encode();return {"README.md":readme,"runs.json":canonical({"schema_version":1,"protocol_revision":c["protocol_revision"],"runs":runs}),"gates.tsv":('\n'.join(lines)+'\n').encode(),"summary.json":canonical(s)}
