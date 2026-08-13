#!/usr/bin/env python3
from __future__ import annotations
import argparse,contextlib,hashlib,io,json,math,os,resource,time
from pathlib import Path
import numpy as np
from s3_g3_al_iso_v097_optimizer_recovery_common_r4 import build_basis,build_evaluator,build_registered_initial,cell_for_row,euler_residual_hartree,fraction_to_boundary,load,require,source_exact,validate_config,validate_runtime,write_exclusive

def project_active_direction(density,tangent,direction,threshold):
 direction=np.asarray(direction,dtype=float);delta=tangent@direction;active=np.asarray(density)<=threshold;outward=active&(delta<0)
 if np.any(outward):
  A=tangent[outward];direction=direction-np.linalg.lstsq(A,A@direction,rcond=1e-12)[0]
 return direction,int(np.count_nonzero(outward))

def optimize_coefficients(evaluate,y0,basis,policy,gradient_limit):
 y=np.asarray(y0,dtype=float);energy,grad,density,_=evaluate(y);accepted_coeff=[y.tolist()];accepted_energy=[energy];radii=[policy["trust_radius_initial"]];events=[];H=np.eye(y.size);radius=policy["trust_radius_initial"];claimed=False;iteration=0
 for iteration in range(1,policy["max_iterations"]+1):
  if float(np.linalg.norm(grad)/math.sqrt(basis["volume"]))<gradient_limit:claimed=True;break
  direction=-(H@grad);direction,nactive=project_active_direction(density,basis["tangent"],direction,policy["active_density_threshold"])
  if nactive:events.append({"iteration":iteration,"kind":"boundary_projection","trust_radius":radius})
  if float(grad@direction)>=-1e-14 or not np.all(np.isfinite(direction)):
   H=np.eye(y.size);direction=-grad;direction,nactive=project_active_direction(density,basis["tangent"],direction,policy["active_density_threshold"]);events.append({"iteration":iteration,"kind":"hessian_reset_non_descent","trust_radius":radius})
  norm=float(np.linalg.norm(direction));require(norm>0 and math.isfinite(norm),"zero/nonfinite recovery direction");direction*=min(1.0,radius/norm);slope=float(grad@direction);alpha=min(1.0,fraction_to_boundary(density,basis["tangent"]@direction,1e-14));accepted=False
  for _ in range(60):
   trial=y+alpha*direction;trial_energy,trial_grad,trial_density,_=evaluate(trial)
   if trial_energy<=energy+1e-4*alpha*slope:
    old_y,old_grad,old_energy=y,grad,energy;y,energy,grad,density=trial,trial_energy,trial_grad,trial_density;accepted_coeff.append(y.tolist());accepted_energy.append(energy);radius=min(policy["trust_radius_max"],radius*policy["trust_radius_grow"] if alpha>=0.5 else radius);radii.append(radius);s=y-old_y;dg=grad-old_grad;curvature=float(s@dg)
    if curvature>1e-14:
     inv=1/curvature;I=np.eye(y.size);left=I-inv*np.outer(s,dg);H=left@H@left.T+inv*np.outer(s,s);H=0.5*(H+H.T)
    else:H=np.eye(y.size);events.append({"iteration":iteration,"kind":"hessian_reset_curvature","trust_radius":radius})
    if len(accepted_energy)>=policy["stagnation_window"]+1 and accepted_energy[-policy["stagnation_window"]-1]-accepted_energy[-1]<policy["stagnation_energy_hartree"]:
     H=np.eye(y.size);radius=max(policy["trust_radius_min"],radius*policy["trust_radius_shrink"]);radii[-1]=radius;events.append({"iteration":iteration,"kind":"stagnation_reset","trust_radius":radius})
    accepted=True;break
   alpha*=0.5
  if not accepted:
   H=np.eye(y.size);radius=max(policy["trust_radius_min"],radius*policy["trust_radius_shrink"]);events.append({"iteration":iteration,"kind":"trust_radius_shrink","trust_radius":radius})
   if radius<=policy["trust_radius_min"]:break
 return {"y":y,"energy":energy,"grad":grad,"density":density,"accepted_coefficients":accepted_coeff,"accepted_energies":accepted_energy,"trust_radii":radii,"events":events,"iterations":iteration,"claimed":claimed}

def main():
 p=argparse.ArgumentParser();p.add_argument("--project-root",type=Path,required=True);p.add_argument("--experiment-id",required=True);p.add_argument("--run-directory",type=Path,required=True);p.add_argument("--development-probe",action="store_true");a=p.parse_args();root=a.project_root.resolve();c=load(root);validate_config(root,c);require(a.development_probe or str(root)==c["execution"]["execution_worktree_root"],"execution root differs");source_exact(root,c);runtime=validate_runtime(root,c);row=next((x for x in c["formal_cases"] if x["experiment_id"]==a.experiment_id),None);require(row is not None,"unregistered ID");require(set(os.sched_getaffinity(0))=={c["runtime"]["logical_cpu"]},"affinity differs")
 from dftpy.constants import ENERGY_CONV,STRESS_CONV
 from dftpy.field import DirectField
 from dftpy.optimization import Optimization
 cell=cell_for_row(root,c,row);shape=tuple(row["grid"]);basis=build_basis(c,cell,shape);ions,grid,evaluator=build_evaluator(c,basis);y0,grid_initial,initial_sha=build_registered_initial(c,row,basis,evaluator,ions)
 def field(values):f=DirectField(grid=grid);f[:]=np.asarray(values).reshape(shape);return f
 cache={};calls=0
 def evaluate(y):
  nonlocal calls
  key=np.asarray(y,dtype=np.float64).tobytes()
  if key not in cache:
   values=basis["base"]+basis["tangent"]@np.frombuffer(key,dtype=np.float64);require(float(values.min())>=0,"negative trial density before evaluator");out=evaluator.get_energy_potential(field(values),calcType={"E","V"});cache[key]=(float(out.energy),basis["tangent"].T@np.asarray(out.potential,dtype=float).reshape(-1)*basis["dv"],values,np.asarray(out.potential,dtype=float).reshape(-1));calls+=1
  return cache[key]
 captured=io.StringIO();started=time.time();accepted_coeff=[];accepted_energy=[];events=[];radii=[]
 with contextlib.redirect_stdout(captured),contextlib.redirect_stderr(captured):
  if row["route"]=="full_grid_WT_reference":
   rho=field(grid_initial);optimizer_code=1;claimed=False
   for restart in range(1,c["optimization"]["full_grid_max_restarts_for_euler_residual"]+1):
    opt=Optimization(EnergyEvaluator=evaluator,optimization_method=c["optimization"]["full_grid_method"],optimization_options={"econv":c["optimization"]["full_grid_energy_convergence_hartree_per_atom"],"maxfun":c["optimization"]["full_grid_max_direction_steps"],"maxiter":c["optimization"]["full_grid_max_iterations"]});rho=opt.optimize_rho(guess_rho=rho);optimizer_code=int(opt.converged);pre=evaluator.get_energy_potential(rho,calcType={"E","V"});flat=np.asarray(rho).reshape(-1);ne=float(math.fsum(float(x) for x in flat)*basis["dv"]);eu,_=euler_residual_hartree(flat,np.asarray(pre.potential).reshape(-1),basis["dv"],ne);claimed=optimizer_code==0
    if claimed and eu<c["acceptance"]["projected_gradient_metric_hartree_strict_lt"]:break
   iterations=restart;algorithm="not_applicable_full_grid"
  else:
   opt=optimize_coefficients(evaluate,y0,basis,c["recovery_policy"],c["acceptance"]["projected_gradient_metric_hartree_strict_lt"]);accepted_coeff,accepted_energy,radii,events=opt["accepted_coefficients"],opt["accepted_energies"],opt["trust_radii"],opt["events"];iterations=opt["iterations"];claimed=opt["claimed"];optimizer_code=0 if claimed else 1;rho=field(opt["density"]);algorithm=c["recovery_policy"]["algorithm"]
  parts=evaluator.get_energy_potential(rho,calcType={"E","V"},split=True);stress=evaluator.get_stress(rho,split=True)
 density=np.asarray(rho,dtype=np.float64).reshape(-1);potential=np.asarray(parts["TOTAL"].potential,dtype=float).reshape(-1);ne=float(math.fsum(float(x) for x in density)*basis["dv"]);negative=float(math.fsum(float(x) for x in np.maximum(-density,0))*basis["dv"]/c["source"]["expected_electrons"]);require(float(density.min())>=0,"final density negative");euler,chemical=euler_residual_hartree(density,potential,basis["dv"],ne)
 fdrows=[]
 if row["route"]=="coefficient_23_function":
  grad=basis["tangent"].T@potential*basis["dv"];projected=float(np.linalg.norm(grad)/math.sqrt(basis["volume"]));rng=np.random.default_rng(20260812)
  for index in range(10):
   d=rng.normal(size=22);d/=np.linalg.norm(d);eps=1e-5;numeric=(evaluate(eps*d)[0]-evaluate(-eps*d)[0])/(2*eps);analytic=float(evaluate(np.zeros(22))[1]@d);error=abs(numeric-analytic)/max(1e-10,abs(numeric),abs(analytic));fdrows.append({"index":index,"direction_sha256":hashlib.sha256(np.ascontiguousarray(d).tobytes()).hexdigest(),"epsilon":eps,"numeric":numeric,"analytic":analytic,"relative_error":error})
  fd={"accepted":max(x["relative_error"] for x in fdrows)<1e-5,"max_relative_error":max(x["relative_error"] for x in fdrows),"directions":fdrows,"evaluation_point":"uniform_charge_conserving_interior"}
 else:projected=euler;fd={"accepted":True,"max_relative_error":0.0,"directions":[],"evaluation_point":"not_applicable_full_grid"}
 components={k:float(v.energy) for k,v in sorted(parts.items())};require(abs(sum(v for k,v in components.items() if k!="TOTAL")-components["TOTAL"])<1e-8,"component sum differs");stress_gpa=np.asarray(stress["TOTAL"])*STRESS_CONV["Ha/Bohr3"]["GPa"];monotonic=all(b<=aa+1e-10 for aa,b in zip(accepted_energy,accepted_energy[1:]));science=claimed and projected<c["acceptance"]["projected_gradient_metric_hartree_strict_lt"] and fd["accepted"] and monotonic
 run=a.run_directory;run.mkdir(parents=True,exist_ok=True);buf=io.BytesIO();np.save(buf,density,allow_pickle=False);write_exclusive(run/"density.npy",buf.getvalue());write_exclusive(run/"functional.stdout",captured.getvalue().encode())
 result={"schema_version":1,"protocol_revision":c["protocol_revision"],"experiment_id":row["experiment_id"],"role":row["role"],"route":row["route"],"initialization":row["initialization"],"volume_ratio":row["volume_ratio"],"volume_bohr3":basis["volume"],"grid":list(map(int,basis["counts"])),"status":"accepted" if science else "completed_scientific_rejected","optimizer_converged_code":optimizer_code,"optimizer_claimed_converged":claimed,"energy_hartree":components["TOTAL"],"energy_ev_per_atom":components["TOTAL"]*ENERGY_CONV["Hartree"]["eV"],"energy_components_hartree":components,"energy_component_sum_error_hartree":abs(sum(v for k,v in components.items() if k!="TOTAL")-components["TOTAL"]),"electron_number":ne,"electron_number_absolute_error":abs(ne-c["source"]["expected_electrons"]),"negative_density_fraction":negative,"minimum_density_electron_per_bohr3":float(density.min()),"projected_gradient_metric_hartree":projected,"full_grid_euler_residual_hartree":euler,"chemical_potential_hartree":chemical,"gradient_finite_difference":fd,"pressure_gpa_diagnostic":-float(np.trace(stress_gpa))/3,"stress_gpa":stress_gpa.tolist(),"iterations":iterations,"objective_calls_including_fd":calls,"accepted_step_coefficients":accepted_coeff,"accepted_step_energies_hartree":accepted_energy,"accepted_steps_monotonic":monotonic,"accepted_step_trust_radii":radii,"recovery_algorithm":algorithm,"recovery_events":events,"basis_sha256":basis["basis_sha256"],"registered_initial_density_sha256":initial_sha,"density_sha256":hashlib.sha256((run/"density.npy").read_bytes()).hexdigest(),"functional_stdout_sha256":hashlib.sha256((run/"functional.stdout").read_bytes()).hexdigest(),"wall_seconds":time.time()-started,"peak_rss_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,"cpu_affinity":sorted(os.sched_getaffinity(0)),"runtime":runtime,"structure_id":row["structure_id"],"geometry_kind":row["geometry_kind"],"geometry_parameter":row["geometry_parameter"]}
 print(json.dumps(result,sort_keys=True));return 0
if __name__=="__main__":raise SystemExit(main())
