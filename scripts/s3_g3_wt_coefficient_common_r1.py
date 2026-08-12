#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,math,os,socket,subprocess
from pathlib import Path

BASE_COMMIT="f7c16db69adb49438702ee5bfad25a8a56709f3e"
CONFIG_REL=Path("config/S3_g3_wt_coefficient_pilot_r1.json")
PROTOCOL_REL=Path("docs/S3_G3_WT_COEFFICIENT_PILOT_R1_PROTOCOL.md")
PROGRESS_REL=Path("docs/M_OFDFT_项目进度与交接.md")
COMMON_REL=Path("scripts/s3_g3_wt_coefficient_common_r1.py")
WORKER_REL=Path("scripts/run_s3_g3_wt_coefficient_worker_r1.py")
RUNNER_REL=Path("scripts/run_s3_g3_wt_coefficient_pilot_r1.py")
COLLECTOR_REL=Path("scripts/collect_s3_g3_wt_coefficient_pilot_r1.py")
VALIDATOR_REL=Path("scripts/validate_s3_g3_wt_coefficient_pilot_r1.py")
TEST_REL=Path("tests/test_s3_g3_wt_coefficient_pilot_r1.py")
IMPLEMENTATION_PATHS={str(p) for p in (CONFIG_REL,PROTOCOL_REL,PROGRESS_REL,COMMON_REL,WORKER_REL,RUNNER_REL,COLLECTOR_REL,VALIDATOR_REL,TEST_REL)}

def require(x,msg):
    if not x: raise ValueError(msg)
def canonical(x): return (json.dumps(x,indent=2,sort_keys=True,ensure_ascii=False)+"\n").encode()
def sha_bytes(b): return hashlib.sha256(b).hexdigest()
def sha_path(p): return sha_bytes(p.read_bytes())
def git(root,*args):
    p=subprocess.run(["git",*args],cwd=root,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    require(p.returncode==0,f"git {' '.join(args)} failed: {p.stderr.strip()}"); return p.stdout.strip()
def load(root): return json.loads((root/CONFIG_REL).read_text())
def write_exclusive(path,data):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("xb") as f: f.write(data); f.flush(); os.fsync(f.fileno())
def fraction_to_boundary(rho,direction,floor=0.0,fraction=0.99):
    import numpy as np
    rho=np.asarray(rho,dtype=float); direction=np.asarray(direction,dtype=float); descending=direction<0
    if not np.any(descending): return float("inf")
    alpha=float(np.min((rho[descending]-floor)/(-direction[descending])))
    require(alpha>0 and math.isfinite(alpha),"no positive fraction-to-boundary step")
    return fraction*alpha

def validate_config(c):
    require(c["schema_version"]==1 and c["protocol_revision"]=="S3-G3-WT-COEFFICIENT-PILOT-20260812-R1","schema differs")
    require(c["base_commit"]==BASE_COMMIT,"base differs")
    require(c["scope"]=={"stage":"S3","gate":"G3_fixed_WT_coefficient_space_pilot","material":"Al","atom_count":1,"candidate_id":"r08_eta100_complementary","g2_density_representation_accepted":True,"g2c_performance_rejection_preserved":True,"performance_acceleration_claimed":False,"full_grid_reconstruction_and_fft_each_step_allowed":True,"mg_enabled":False,"ml_enabled":False},"scope differs")
    require(c["source"]["grid"]==[24,24,24] and c["source"]["expected_electrons"]==3.0,"source differs")
    b=c["basis"]
    require(b["basis_count"]==23 and len(b["alpha_bohr_minus2"])==8,"basis differs")
    require(b["initializations"]==["uniform","atomic_superposition_projection","low_g_perturbation_projection"],"initializations differ")
    require(b["optimization_coordinates"]=="22_charge_tangent_columns_orthonormal_under_voxel_L2_metric","coordinate metric differs")
    require(c["functionals"]=={"kedf":"WT","wt_alpha":5/6,"wt_beta":5/6,"wt_rho0":None,"xc":"PBE","hartree":"DFTpy_HARTREE","pseudo":"DFTpy_LocalPseudo_exact_bytes"},"functionals differ")
    require(c["optimization"]=={"full_grid_method":"TN","full_grid_energy_convergence_hartree_per_atom":1e-10,"full_grid_max_iterations":400,"full_grid_max_direction_steps":80,"full_grid_max_restarts_for_euler_residual":5,"coefficient_method":"feasible_fraction_to_boundary_Armijo_BFGS","coefficient_max_iterations":400,"density_floor_electron_per_bohr3":1e-14,"negative_density_constraint":"all_grid_points_nonnegative_linear_constraint","development_probe_is_not_formal_evidence":True},"optimization differs")
    cases=c["formal_cases"]
    require([x["experiment_id"] for x in cases]==[f"S3-20260812-{i:03d}" for i in range(1,30)],"IDs differ")
    require(len(cases)==29 and all(set(x)=={"experiment_id","role","route","initialization","volume_ratio","grid"} for x in cases),"case schema differs")
    require([x["grid"] for x in cases[:3]]==[[24,24,24],[32,32,32],[40,40,40]],"grid convergence denominator differs")
    require(all(x["grid"]==[40,40,40] for x in cases[3:]),"formal grid differs")
    require([x["route"] for x in cases]==["full_grid_WT_reference"]*16+["coefficient_23_function"]*13,"route denominator differs")
    core=[x for x in cases if x["role"]=="core"]
    require([(x["route"],x["volume_ratio"],x["initialization"]) for x in core]==[(route,volume,init) for route in ("full_grid_WT_reference","coefficient_23_function") for volume in (0.995,1.0,1.005) for init in b["initializations"]],"core matrix differs")
    pressure=[x for x in cases if x["role"]=="pressure_platform"]
    require([(x["route"],x["volume_ratio"],x["initialization"]) for x in pressure]==[(route,volume,"uniform") for route in ("full_grid_WT_reference","coefficient_23_function") for volume in (0.9975,1.0025,0.99,1.01)],"pressure matrix differs")
    gp=c["grid_and_pressure_policy"]
    require(gp=={"grid_convergence_counts":[24,32,40],"formal_grid_count":40,"grid_energy_difference_32_to_40_strict_lt_mev_per_atom":1.0,"grid_pressure_difference_32_to_40_strict_lt_gpa":0.02,"core_volume_ratios":[0.995,1.0,1.005],"pressure_central_difference_volume_deltas":[0.0025,0.005,0.01],"pressure_conversion_hartree_per_bohr3_to_gpa":29421.02648438959,"pressure_definition":"minus_central_difference_of_independently_self_consistent_total_energy"},"grid/pressure policy differs")
    a=c["acceptance"]
    require(a=={"electron_number_absolute_error_strict_lt":1e-10,"negative_density_fraction_strict_lt":1e-8,"projected_gradient_metric_hartree_strict_lt":1e-6,"three_initialization_energy_spread_strict_lt_mev_per_atom":1.0,"full_grid_reference_energy_difference_abs_strict_lt_mev_per_atom":10.0,"density_relative_l2_strict_lt":0.015,"pressure_difference_abs_strict_lt_gpa":0.2,"pressure_delta_platform_max_pairwise_strict_lt_gpa":0.02,"coefficient_variational_lower_bound_tolerance_mev_per_atom":-0.1,"minimum_density_greater_than_or_equal_electron_per_bohr3":0.0,"failed_missing_skipped_retried_max":0},"acceptance differs")
    require(c["execution"]=={"execution_worktree_root":"/home/shenwei01/wt_s3_g3_wt_coefficient_pilot_r1_formal_20260812","state_root":"/home/shenwei01/.local/state/m_ofdft/s3_g3_wt_coefficient_pilot_r1_20260812","analysis_root":"analysis/s3/g3_wt_coefficient_pilot_r1_20260812","formal_case_count":29,"no_retry":True,"new_electronic_structure_solver_run_count":29},"execution differs")
    require(c["output_files"]==["README.md","runs.json","gates.tsv","summary.json"],"outputs differ")

def source_exact(root,c):
    head=git(root,"rev-parse","HEAD")
    entries=[("density_policy_commit","density_policy_path","density_policy_sha256"),("g2c_evidence_commit","g2c_summary_path","g2c_summary_sha256"),("basis_evidence_commit","basis_spectrum_path","basis_spectrum_sha256"),("source_commit","density_path","density_sha256"),("source_commit","result_path","result_sha256"),("source_commit","structure_path","structure_sha256")]
    for ck,pk,sk in entries:
        commit,rel,expected=c["source"][ck],c["source"][pk],c["source"][sk]
        require(subprocess.run(["git","merge-base","--is-ancestor",commit,head],cwd=root).returncode==0,f"source commit not ancestor: {commit}")
        path=root/rel; require(path.is_file() and not path.is_symlink() and sha_path(path)==expected,f"source differs: {rel}")
        data=subprocess.run(["git","show",f"{commit}:{rel}"],cwd=root,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True).stdout
        require(data==path.read_bytes() and sha_bytes(data)==expected,f"committed source differs: {rel}")
    policy=json.loads((root/c["source"]["density_policy_path"]).read_text()); perf=json.loads((root/c["source"]["g2c_summary_path"]).read_text()); spectrum=json.loads((root/c["source"]["basis_spectrum_path"]).read_text())["candidates"][c["scope"]["candidate_id"]]
    require(policy["status"]=="accepted_density_expansion_candidate","density policy differs")
    require(perf["status"]=="evidence_valid_g2c_performance_rejected" and perf["performance_accepted"] is False,"G2c history differs")
    require(spectrum["basis_count"]==23 and spectrum["effective_rank"]==23,"basis evidence differs")
    return {"density_policy_status":policy["status"],"g2c_status":perf["status"],"candidate":spectrum}

def validate_runtime(root,c):
    import s2_g2_al1_pilot_common_r1 as pilot
    return pilot.validate_runtime(root,c)
def load_source_density(root,c):
    import numpy as np, s2_g2_al1_pilot_common_r1 as pilot
    s=c["source"]; cell=pilot.parse_structure_cell_bohr(root/s["structure_path"]); counts,rho=pilot.parse_cube(root/s["density_path"],cell)
    require(list(map(int,counts))==s["grid"],"grid differs"); volume=float(abs(np.linalg.det(cell))); require(abs(float(rho.sum(dtype=np.float64)*volume/rho.size)-s["expected_electrons"])<5e-10,"source electrons differ")
    return cell,rho

def build_basis(c,cell,reference_density):
    import numpy as np, s2_g2_al1_pilot_common_r1 as pilot
    counts=np.asarray(reference_density.shape,dtype=int); frac=pilot.fractional_grid(counts); volume=float(abs(np.linalg.det(cell))); dv=volume/reference_density.size; r2=pilot.minimum_image_r2(frac,cell)
    atomic=[]
    for alpha in c["basis"]["alpha_bohr_minus2"]:
        f=np.exp(-float(alpha)*r2); f/=float(f.sum(dtype=np.float64)*dv); atomic.append(f)
    atomic=np.stack(atomic,axis=1); compensated=atomic-np.mean(atomic,axis=0,keepdims=True)
    rows,low,_=pilot.low_g_vectors(frac,cell,c["source"]["expected_electrons"],c["basis"]["low_g_eta_max"],c["basis"]["integer_search_bound"])
    require([list(v) for _,v in rows]==c["basis"]["expected_half_space_vectors"],"low-G set differs")
    constant=np.full((reference_density.size,1),1/volume); low_block=np.column_stack((constant,low)); low_q,_=np.linalg.qr(low_block,mode="reduced"); complementary=compensated-low_q@(low_q.T@compensated); raw=np.column_stack((low,complementary))
    raw-=raw.sum(axis=0,keepdims=True)/raw.shape[0]; gram=raw.T@raw*dv; eigval=np.linalg.eigvalsh(gram); require(float(eigval.min())>0,"charge-tangent basis not positive definite")
    q_tangent,_=np.linalg.qr(raw*math.sqrt(dv),mode="reduced"); tangent=q_tangent/math.sqrt(dv); require(float(np.max(np.abs(tangent.T@tangent*dv-np.eye(22))))<2e-12,"orthonormal tangent differs")
    base=np.full(reference_density.size,c["source"]["expected_electrons"]/volume)
    return {"base":base,"tangent":tangent,"counts":counts,"volume":volume,"dv":dv,"cell":np.asarray(cell),"raw_gram_eigenvalues":eigval,"basis_sha256":sha_bytes(np.ascontiguousarray(np.column_stack((constant,raw)),dtype=np.float64).tobytes())}

def build_evaluator(c,basis):
    import numpy as np
    from ase import Atoms
    from dftpy.constants import Units
    from dftpy.functional import Functional,LocalPseudo,TotalFunctional
    from dftpy.grid import DirectGrid
    from dftpy.ions import Ions
    atoms=Atoms("Al",positions=[[0,0,0]],cell=basis["cell"]*Units.Bohr,pbc=True); ions=Ions.from_ase(atoms); grid=DirectGrid(lattice=ions.cell,nr=basis["counts"],full=False); pseudo=LocalPseudo(grid=grid,ions=ions,PP_list={"Al":c["pseudopotential"]["path"]})
    evaluator=TotalFunctional(KE=Functional(type="KEDF",name="WT",alpha=5/6,beta=5/6,rho0=None),XC=Functional(type="XC",name="PBE"),HARTREE=Functional(type="HARTREE"),PSEUDO=pseudo)
    return ions,grid,evaluator

def build_registered_initial(c,row,basis,evaluator,ions):
    import numpy as np
    from dftpy.density import DensityGenerator
    from scipy.optimize import LinearConstraint,minimize
    base,tangent=basis["base"],basis["tangent"]; init=row["initialization"]
    if init=="uniform": raw=base.copy()
    elif init=="atomic_superposition_projection": raw=np.asarray(DensityGenerator(pseudo=evaluator.PSEUDO).guess_rho(ions,grid=evaluator.PSEUDO.grid),dtype=float).reshape(-1)
    else:
        frac=np.stack(np.meshgrid(*[np.arange(int(n))/int(n) for n in basis["counts"]],indexing="ij"),axis=-1); raw=(base.reshape(tuple(basis["counts"]))*(1+0.05*np.cos(2*np.pi*frac[...,2]))).reshape(-1)
    raw*=c["source"]["expected_electrons"]/(float(np.sum(raw,dtype=np.float64))*basis["dv"]); require(float(raw.min())>=0,"raw initial density negative")
    guess=tangent.T@(raw-base)*basis["dv"]; floor=c["optimization"]["density_floor_electron_per_bohr3"]; constraint=LinearConstraint(tangent,np.full(base.size,floor)-base,np.full(base.size,np.inf))
    projection=minimize(lambda y:(0.5*float(np.sum((base+tangent@y-raw)**2))*basis["dv"],tangent.T@(base+tangent@y-raw)*basis["dv"]),guess,jac=True,method="SLSQP",constraints=[constraint],options={"ftol":1e-14,"maxiter":400})
    require(projection.success,"initial projection failed"); y=np.asarray(projection.x,dtype=np.float64); density=base+tangent@y; require(float(density.min())>=0,"registered initial density negative")
    require(abs(float(np.sum(density,dtype=np.float64))*basis["dv"]-c["source"]["expected_electrons"])<1e-12,"registered initial charge differs")
    return y,density,sha_bytes(np.ascontiguousarray(density,dtype=np.float64).tobytes())

def euler_residual_hartree(density,potential,dv,electrons):
    import numpy as np
    rho=np.asarray(density,dtype=float).reshape(-1); v=np.asarray(potential,dtype=float).reshape(-1); require(float(rho.min())>=0,"Euler density negative")
    mu=float(np.sum(v*rho,dtype=np.float64)*dv/electrons)
    residual=math.sqrt(float(np.sum(((v-mu)*np.sqrt(rho))**2,dtype=np.float64)*dv/electrons))
    return residual,mu

def validate_terminal_trajectory_link(base,tangent,accepted_coefficients,accepted_energies,final_density,final_energy):
    import numpy as np
    require(len(accepted_coefficients)==len(accepted_energies)>0,"terminal trajectory denominator differs")
    terminal_coeff=np.asarray(accepted_coefficients[-1],dtype=np.float64); require(terminal_coeff.shape==(tangent.shape[1],),"terminal coefficient shape differs")
    terminal_density=np.asarray(base)+np.asarray(tangent)@terminal_coeff; final_density=np.asarray(final_density,dtype=np.float64)
    require(terminal_density.shape==final_density.shape and float(np.max(np.abs(terminal_density-final_density)))<1e-12,"terminal coefficient/density link differs")
    require(abs(float(accepted_energies[-1])-float(final_energy))<1e-10,"terminal trajectory/energy link differs")

def validate_optimizer_disposition(code,claimed):
    require(code in {0,1,2},"optimizer code differs")
    require(isinstance(claimed,bool) and claimed==(code==0),"optimizer code/claim differs")
    return code==0

def replay_result(root,c,row,result,density):
    import contextlib,io,numpy as np
    from dftpy.constants import ENERGY_CONV,STRESS_CONV
    from dftpy.field import DirectField
    source_cell,_=load_source_density(root,c); cell=source_cell*float(row["volume_ratio"])**(1/3); shape=tuple(int(x) for x in row["grid"]); basis=build_basis(c,cell,np.zeros(shape,dtype=np.float64)); ions,grid,evaluator=build_evaluator(c,basis)
    field=DirectField(grid=grid); field[:]=np.asarray(density,dtype=np.float64).reshape(shape)
    with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()): parts=evaluator.get_energy_potential(field,calcType={"E","V"},split=True); stress=evaluator.get_stress(field,split=True)
    energy=float(parts["TOTAL"].energy); components={k:float(v.energy) for k,v in sorted(parts.items())}; stress_gpa=np.asarray(stress["TOTAL"])*STRESS_CONV["Ha/Bohr3"]["GPa"]; flat=np.asarray(density).reshape(-1); electrons=float(math.fsum(float(x) for x in flat)*basis["dv"]); negative=float(math.fsum(float(x) for x in np.maximum(-flat,0))*basis["dv"]/c["source"]["expected_electrons"]); require(float(flat.min())>=0,"replayed density negative")
    require(abs(energy-result["energy_hartree"])<1e-10 and abs(energy*ENERGY_CONV["Hartree"]["eV"]-result["energy_ev_per_atom"])<1e-8,"energy replay differs")
    require(set(components)==set(result["energy_components_hartree"]) and max(abs(components[k]-result["energy_components_hartree"][k]) for k in components)<1e-10,"energy components replay differ")
    require(float(np.max(np.abs(stress_gpa-np.asarray(result["stress_gpa"]))))<1e-7,"stress replay differs")
    require(abs(electrons-result["electron_number"])<1e-12 and abs(negative-result["negative_density_fraction"])<1e-14,"density integral replay differs")
    require(result["basis_sha256"]==basis["basis_sha256"] and abs(result["volume_bohr3"]-basis["volume"])<1e-10,"basis replay differs")
    potential=np.asarray(parts["TOTAL"].potential,dtype=float).reshape(-1); euler,chemical=euler_residual_hartree(flat,potential,basis["dv"],electrons); require(abs(euler-result["full_grid_euler_residual_hartree"])<1e-10 and abs(chemical-result["chemical_potential_hartree"])<1e-10,"Euler replay differs")
    initial_y,initial,initial_sha=build_registered_initial(c,row,basis,evaluator,ions); require(initial_sha==result["registered_initial_density_sha256"],"initial density replay differs")
    if row["route"]=="coefficient_23_function":
        metric=float(np.linalg.norm(basis["tangent"].T@potential*basis["dv"])/math.sqrt(basis["volume"])); require(abs(metric-result["projected_gradient_metric_hartree"])<1e-10,"gradient replay differs")
        accepted_coefficients=result["accepted_step_coefficients"]; accepted_energies=result["accepted_step_energies_hartree"]; require(len(accepted_coefficients)==len(accepted_energies)>0,"accepted step denominator differs"); observed=[]
        for coeff,registered_energy in zip(accepted_coefficients,accepted_energies):
            trial=basis["base"]+basis["tangent"]@np.asarray(coeff,dtype=float); require(float(trial.min())>=0,"accepted step density negative"); trial_field=DirectField(grid=grid); trial_field[:]=trial.reshape(shape); trial_energy=float(evaluator.get_energy_potential(trial_field,calcType={"E"}).energy); require(abs(trial_energy-registered_energy)<1e-10,"accepted step energy replay differs"); observed.append(trial_energy)
        monotonic=all(b<=a+1e-10 for a,b in zip(observed,observed[1:])); require(monotonic==result["accepted_steps_monotonic"],"accepted monotonic replay differs")
        validate_terminal_trajectory_link(basis["base"],basis["tangent"],accepted_coefficients,accepted_energies,flat,energy)
        require(abs(observed[-1]-energy)<1e-10,"terminal replayed energy link differs")
        rng=np.random.default_rng(20260812); rows=[]; base=basis["base"]; tangent=basis["tangent"]
        def point_energy(y):
            values=base+tangent@np.asarray(y); require(float(values.min())>=0,"FD density negative"); trial=DirectField(grid=grid); trial[:]=values.reshape(shape); out=evaluator.get_energy_potential(trial,calcType={"E","V"}); return float(out.energy),tangent.T@np.asarray(out.potential,dtype=float).reshape(-1)*basis["dv"]
        zero_energy,zero_grad=point_energy(np.zeros(22))
        for index in range(10):
            d=rng.normal(size=22); d/=np.linalg.norm(d); eps=1e-5; numeric=(point_energy(eps*d)[0]-point_energy(-eps*d)[0])/(2*eps); analytic=float(zero_grad@d); error=abs(numeric-analytic)/max(1e-10,abs(numeric),abs(analytic)); rows.append({"index":index,"direction_sha256":sha_bytes(np.ascontiguousarray(d,dtype=np.float64).tobytes()),"epsilon":eps,"numeric":numeric,"analytic":analytic,"relative_error":error})
        registered_rows=result["gradient_finite_difference"]["directions"]; require(len(registered_rows)==10,"FD denominator differs")
        for observed_row,registered_row in zip(rows,registered_rows):
            require(observed_row["index"]==registered_row["index"] and observed_row["direction_sha256"]==registered_row["direction_sha256"] and max(abs(observed_row[k]-registered_row[k]) for k in ("epsilon","numeric","analytic","relative_error"))<1e-10,"FD replay differs")
        require(result["gradient_finite_difference"]["accepted"]==(max(x["relative_error"] for x in rows)<1e-5),"FD disposition differs")
    else: require(abs(result["projected_gradient_metric_hartree"]-euler)<1e-10 and not result["accepted_step_coefficients"],"full-grid gradient replay differs")
    require(isinstance(result["iterations"],int) and 0<=result["iterations"]<=c["optimization"]["coefficient_max_iterations"] and isinstance(result["objective_calls_including_fd"],int) and result["objective_calls_including_fd"]>=0,"optimizer counters differ")
    validate_optimizer_disposition(result["optimizer_converged_code"],result["optimizer_claimed_converged"])
    expected_status="accepted" if result["optimizer_claimed_converged"] and result["projected_gradient_metric_hartree"]<c["acceptance"]["projected_gradient_metric_hartree_strict_lt"] and result["gradient_finite_difference"]["accepted"] and result["accepted_steps_monotonic"] else "completed_scientific_rejected"; require(result["status"]==expected_status,"result scientific status differs")
    return {"energy_hartree":energy,"electron_number":electrons,"negative_density_fraction":negative,"minimum_density":float(flat.min()),"projected_gradient_metric_hartree":result["projected_gradient_metric_hartree"],"initial_density_sha256":initial_sha}

def _cpu_set(text):
    out=set()
    for part in text.strip().split(','):
        if not part: continue
        if '-' in part:
            a,b=map(int,part.split('-',1)); out.update(range(a,b+1))
        else: out.add(int(part))
    return out
def resource_preflight(c):
    require(socket.gethostname()==c["runtime"]["hostname"],"hostname differs"); cpu=c["runtime"]["logical_cpu"]; domain=set(c["runtime"]["smt_domain"]); require(_cpu_set(Path(f"/sys/devices/system/cpu/cpu{cpu}/topology/thread_siblings_list").read_text())==domain,"SMT topology differs")
    ancestors=set(); p=os.getpid()
    while p>1 and p not in ancestors:
        ancestors.add(p)
        try: p=int(next(x.split()[1] for x in (Path('/proc')/str(p)/'status').read_text().splitlines() if x.startswith('PPid:')))
        except Exception: break
    collisions=[]
    for e in Path('/proc').iterdir():
        if not e.name.isdigit() or int(e.name) in ancestors: continue
        try:
            status=(e/'status').read_text(); fields={x.split(':',1)[0]:x.split(':',1)[1].strip() for x in status.splitlines() if ':' in x}; comm=fields.get('Name','').lower(); cmd=(e/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace').lower(); allowed=_cpu_set(fields.get('Cpus_allowed_list',''))
        except Exception: continue
        scientific=('abacus' in comm or comm in {'pw.x','pp.x'} or (comm.startswith('python') and ('dftpy' in cmd or 's3_g3_wt' in cmd)))
        if scientific and allowed&domain: collisions.append({"pid":int(e.name),"comm":comm,"allowed":sorted(allowed)})
    require(not collisions,f"reserved SMT collision: {collisions}")
    return {"status":"accepted","hostname":socket.gethostname(),"logical_cpu":cpu,"smt_domain":sorted(domain),"collisions":[]}

def summarize(c,runs):
    require(len(runs)==29,"run denominator differs")
    require([r["experiment_id"] for r in runs]==[x["experiment_id"] for x in c["formal_cases"]],"IDs differ")
    by_id={r["experiment_id"]:r for r in runs}; a=c["acceptance"]; gp=c["grid_and_pressure_policy"]
    require(all(r["status"] in {"accepted","completed_scientific_rejected"} for r in runs),"run status differs")
    electron_rows=[{"experiment_id":r["experiment_id"],"electron_number_absolute_error":r["electron_number_absolute_error"],"negative_density_fraction":r["negative_density_fraction"],"minimum_density_electron_per_bohr3":r["minimum_density_electron_per_bohr3"],"stationarity_metric_hartree":r["projected_gradient_metric_hartree"],"accepted":r["electron_number_absolute_error"]<a["electron_number_absolute_error_strict_lt"] and r["negative_density_fraction"]<a["negative_density_fraction_strict_lt"] and r["minimum_density_electron_per_bohr3"]>=a["minimum_density_greater_than_or_equal_electron_per_bohr3"] and r["projected_gradient_metric_hartree"]<a["projected_gradient_metric_hartree_strict_lt"]} for r in runs]
    grid_energy=abs(by_id["S3-20260812-002"]["energy_ev_per_atom"]-by_id["S3-20260812-003"]["energy_ev_per_atom"])*1000
    grid_pressure=abs(by_id["S3-20260812-002"]["pressure_gpa_diagnostic"]-by_id["S3-20260812-003"]["pressure_gpa_diagnostic"])
    grid_gate=grid_energy<gp["grid_energy_difference_32_to_40_strict_lt_mev_per_atom"] and grid_pressure<gp["grid_pressure_difference_32_to_40_strict_lt_gpa"]
    core_spreads=[]; selected={}
    for route in ("full_grid_WT_reference","coefficient_23_function"):
        for volume in gp["core_volume_ratios"]:
            group=[r for r in runs if r["role"]=="core" and r["route"]==route and r["volume_ratio"]==volume]
            require(len(group)==3,"core group denominator differs")
            spread=(max(r["energy_ev_per_atom"] for r in group)-min(r["energy_ev_per_atom"] for r in group))*1000
            core_spreads.append({"route":route,"volume_ratio":volume,"energy_spread_mev_per_atom":spread,"accepted":spread<a["three_initialization_energy_spread_strict_lt_mev_per_atom"]})
            if route=="full_grid_WT_reference": selected[volume]=min(group,key=lambda r:r["energy_ev_per_atom"])
    coeff_rows=[]
    for r in [x for x in runs if x["route"]=="coefficient_23_function"]:
        ref=selected[r["volume_ratio"]] if r["role"]=="core" else next(x for x in runs if x["route"]=="full_grid_WT_reference" and x["volume_ratio"]==r["volume_ratio"] and x["initialization"]=="uniform")
        signed_delta=(r["energy_ev_per_atom"]-ref["energy_ev_per_atom"])*1000; vals={"electron_number_absolute_error":r["electron_number_absolute_error"],"negative_density_fraction":r["negative_density_fraction"],"minimum_density_electron_per_bohr3":r["minimum_density_electron_per_bohr3"],"projected_gradient_metric_hartree":r["projected_gradient_metric_hartree"],"reference_energy_difference_mev_per_atom":abs(signed_delta),"coefficient_minus_reference_energy_mev_per_atom":signed_delta,"density_relative_l2":r["density_relative_l2_vs_full_grid"],"reference_id":ref["experiment_id"]}
        gates={"electron_number":vals["electron_number_absolute_error"]<a["electron_number_absolute_error_strict_lt"],"negative_density":vals["negative_density_fraction"]<a["negative_density_fraction_strict_lt"],"minimum_density":vals["minimum_density_electron_per_bohr3"]>=a["minimum_density_greater_than_or_equal_electron_per_bohr3"],"projected_gradient":vals["projected_gradient_metric_hartree"]<a["projected_gradient_metric_hartree_strict_lt"],"energy_reference":vals["reference_energy_difference_mev_per_atom"]<a["full_grid_reference_energy_difference_abs_strict_lt_mev_per_atom"],"variational_lower_bound":signed_delta>=a["coefficient_variational_lower_bound_tolerance_mev_per_atom"],"density_l2":vals["density_relative_l2"]<a["density_relative_l2_strict_lt"],"gradient_finite_difference":r["gradient_finite_difference"]["accepted"],"accepted_steps_monotonic":r["accepted_steps_monotonic"]}
        coeff_rows.append({"experiment_id":r["experiment_id"],"role":r["role"],"volume_ratio":r["volume_ratio"],"initialization":r["initialization"],**vals,"gates":gates,"accepted":all(gates.values())})
    volume0=by_id["S3-20260812-003"]["volume_bohr3"]
    pressure_rows=[]
    volume_pairs={0.0025:(0.9975,1.0025),0.005:(0.995,1.005),0.01:(0.99,1.01)}
    for delta,(minus_v,plus_v) in volume_pairs.items():
        values={}
        for route in ("full_grid_WT_reference","coefficient_23_function"):
            minus=next(r for r in runs if r["route"]==route and r["volume_ratio"]==minus_v and r["initialization"]=="uniform")
            plus=next(r for r in runs if r["route"]==route and r["volume_ratio"]==plus_v and r["initialization"]=="uniform")
            values[route]=-(plus["energy_hartree"]-minus["energy_hartree"])/(2*delta*volume0)*gp["pressure_conversion_hartree_per_bohr3_to_gpa"]
        difference=abs(values["coefficient_23_function"]-values["full_grid_WT_reference"])
        pressure_rows.append({"volume_delta":delta,"full_grid_pressure_gpa":values["full_grid_WT_reference"],"coefficient_pressure_gpa":values["coefficient_23_function"],"pressure_difference_gpa":difference,"accepted":difference<a["pressure_difference_abs_strict_lt_gpa"]})
    pressure_values={route:[x[("full_grid_pressure_gpa" if route=="full_grid_WT_reference" else "coefficient_pressure_gpa")] for x in pressure_rows] for route in ("full_grid_WT_reference","coefficient_23_function")}; pressure_platform=[{"route":route,"max_pairwise_difference_gpa":max(values)-min(values),"accepted":max(values)-min(values)<a["pressure_delta_platform_max_pairwise_strict_lt_gpa"]} for route,values in pressure_values.items()]
    initial_pairs=[]
    for r in [x for x in runs if x["route"]=="coefficient_23_function"]:
        ref=next(x for x in runs if x["route"]=="full_grid_WT_reference" and x["volume_ratio"]==r["volume_ratio"] and x["initialization"]==r["initialization"]); equal=r["registered_initial_density_sha256"]==ref["registered_initial_density_sha256"]; initial_pairs.append({"coefficient_id":r["experiment_id"],"reference_id":ref["experiment_id"],"initialization":r["initialization"],"volume_ratio":r["volume_ratio"],"sha256_equal":equal,"accepted":equal})
    final_l2=[]
    for route in ("full_grid_WT_reference","coefficient_23_function"):
        for volume in gp["core_volume_ratios"]:
            group=[r for r in runs if r["role"]=="core" and r["route"]==route and r["volume_ratio"]==volume]; final_l2.append({"route":route,"volume_ratio":volume,"pairwise_final_density_relative_l2":[{"ids":[group[i]["experiment_id"],group[j]["experiment_id"]],"value":group[i]["pairwise_final_density_relative_l2"][group[j]["experiment_id"]]} for i in range(3) for j in range(i+1,3)]})
    run_status_gate=all(r["status"]=="accepted" and r["optimizer_claimed_converged"] is True and r["optimizer_converged_code"]==0 for r in runs)
    accepted=run_status_gate and all(x["accepted"] for x in electron_rows+core_spreads+coeff_rows+pressure_rows+pressure_platform+initial_pairs) and grid_gate
    return {"schema_version":1,"protocol_revision":c["protocol_revision"],"status":"accepted_s3_al_v100_fixed_WT_coefficient_pilot" if accepted else "evidence_valid_s3_al_v100_fixed_WT_coefficient_pilot_rejected","evidence_valid":True,"scientific_gate_accepted":accepted,"all_run_scientific_statuses_accepted":run_status_gate,"candidate_id":c["scope"]["candidate_id"],"formal_case_count":29,"failed_missing_skipped_retried":0,"grid_convergence":{"grid24_energy_ev_per_atom":by_id["S3-20260812-001"]["energy_ev_per_atom"],"grid24_pressure_gpa":by_id["S3-20260812-001"]["pressure_gpa_diagnostic"],"energy_32_to_40_mev_per_atom":grid_energy,"pressure_32_to_40_gpa":grid_pressure,"accepted":grid_gate},"electron_negative_density_and_stationarity_runs":electron_rows,"three_initialization_spreads":core_spreads,"three_initialization_final_density_l2":final_l2,"initial_density_route_pairs":initial_pairs,"selected_full_grid_reference_ids":{str(k):v["experiment_id"] for k,v in selected.items()},"coefficient_runs":coeff_rows,"finite_difference_pressure_rows":pressure_rows,"pressure_delta_platform":pressure_platform,"g2c_performance_status_preserved":"evidence_valid_g2c_performance_rejected","performance_acceleration_claimed":False,"g3_overall_closed":False,"s4_authorized":False,"next_action":"expand_S3_to_registered_multistructure_Al_Mg_matrix" if accepted else "record_pilot_rejection_and_keep_G3_open"}

def render(c,runs):
    s=summarize(c,runs); cols=["experiment_id","role","volume_ratio","initialization","electron_number_absolute_error","negative_density_fraction","projected_gradient_metric_hartree","reference_energy_difference_mev_per_atom","density_relative_l2","accepted"]; lines=['\t'.join(cols)]+['\t'.join(str(r[k]) for k in cols) for r in s["coefficient_runs"]]
    readme=(f"# S3/G3 Al V100 fixed WT coefficient-space pilot R1\n\nStatus: `{s['status']}`. Twenty-nine fresh runs cover grid convergence, three volumes and three initializations, plus a three-spacing pressure platform. No retry is permitted. This pilot does not close G3 or authorize S4. G2c remains rejected and no acceleration claim is made.\n").encode()
    return {"README.md":readme,"runs.json":canonical({"schema_version":1,"protocol_revision":c["protocol_revision"],"runs":runs}),"gates.tsv":('\n'.join(lines)+'\n').encode(),"summary.json":canonical(s)}
