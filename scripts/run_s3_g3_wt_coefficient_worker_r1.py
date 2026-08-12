#!/usr/bin/env python3
from __future__ import annotations
import argparse,contextlib,hashlib,io,json,math,os,resource,time
from pathlib import Path
from s3_g3_wt_coefficient_common_r1 import build_basis,build_evaluator,build_registered_initial,euler_residual_hartree,fraction_to_boundary,load,load_source_density,require,source_exact,validate_config,validate_runtime,write_exclusive

def main():
    p=argparse.ArgumentParser(); p.add_argument("--project-root",type=Path,required=True); p.add_argument("--experiment-id",required=True); p.add_argument("--run-directory",type=Path,required=True); p.add_argument("--development-probe",action="store_true"); a=p.parse_args()
    root=a.project_root.resolve(); c=load(root); validate_config(c); require(a.development_probe or str(root)==c["execution"]["execution_worktree_root"],"execution worktree root differs"); require(not a.development_probe or c["optimization"]["development_probe_is_not_formal_evidence"] is True,"development probe forbidden"); source_exact(root,c); runtime=validate_runtime(root,c)
    row=next((x for x in c["formal_cases"] if x["experiment_id"]==a.experiment_id),None); require(row is not None,"unregistered ID"); require(set(os.sched_getaffinity(0))=={c["runtime"]["logical_cpu"]},"affinity differs")
    import numpy as np
    from dftpy.constants import ENERGY_CONV,STRESS_CONV
    from dftpy.field import DirectField
    from dftpy.optimization import Optimization
    source_cell,_=load_source_density(root,c); cell=source_cell*float(row["volume_ratio"])**(1/3); shape=tuple(int(x) for x in row["grid"]); basis=build_basis(c,cell,np.zeros(shape,dtype=np.float64)); ions,grid,evaluator=build_evaluator(c,basis); base,tangent=basis["base"],basis["tangent"]
    y0,grid_initial,initial_sha=build_registered_initial(c,row,basis,evaluator,ions)
    def field(values):
        f=DirectField(grid=grid); f[:]=np.asarray(values).reshape(shape); return f
    cache={}; trial_count=0
    def evaluate(y):
        nonlocal trial_count
        array=np.asarray(y,dtype=np.float64); key=array.tobytes()
        if key not in cache:
            values=base+tangent@array; require(float(values.min())>=0,"negative trial density before evaluator"); out=evaluator.get_energy_potential(field(values),calcType={"E","V"}); grad=tangent.T@np.asarray(out.potential,dtype=float).reshape(-1)*basis["dv"]; cache[key]=(float(out.energy),grad,values,np.asarray(out.potential,dtype=float).reshape(-1)); trial_count+=1
        return cache[key]
    captured=io.StringIO(); started=time.time(); accepted_coefficients=[]; accepted_energies=[]; fd_rows=[]; iterations=0
    with contextlib.redirect_stdout(captured),contextlib.redirect_stderr(captured):
        if row["route"]=="full_grid_WT_reference":
            rho=field(grid_initial); optimizer_code=1; optimizer_claimed=False
            for restart in range(1,c["optimization"]["full_grid_max_restarts_for_euler_residual"]+1):
                opt=Optimization(EnergyEvaluator=evaluator,optimization_method=c["optimization"]["full_grid_method"],optimization_options={"econv":c["optimization"]["full_grid_energy_convergence_hartree_per_atom"],"maxfun":c["optimization"]["full_grid_max_direction_steps"],"maxiter":c["optimization"]["full_grid_max_iterations"]}); rho=opt.optimize_rho(guess_rho=rho); optimizer_code=int(opt.converged); preliminary=evaluator.get_energy_potential(rho,calcType={"E","V"}); preliminary_density=np.asarray(rho,dtype=float).reshape(-1); preliminary_electrons=float(math.fsum(float(x) for x in preliminary_density)*basis["dv"]); preliminary_euler,_=euler_residual_hartree(preliminary_density,np.asarray(preliminary.potential,dtype=float).reshape(-1),basis["dv"],preliminary_electrons)
                optimizer_claimed=optimizer_code==0
                if optimizer_claimed and preliminary_euler<c["acceptance"]["projected_gradient_metric_hartree_strict_lt"]: break
            iterations=restart
        else:
            y=np.asarray(y0,dtype=np.float64); energy,grad,density,_=evaluate(y); accepted_coefficients.append(y.tolist()); accepted_energies.append(energy); inverse_hessian=np.eye(22); optimizer_claimed=False
            for iteration in range(1,c["optimization"]["coefficient_max_iterations"]+1):
                if float(np.linalg.norm(grad)/math.sqrt(basis["volume"]))<c["acceptance"]["projected_gradient_metric_hartree_strict_lt"]: optimizer_claimed=True; iterations=iteration-1; break
                direction=-(inverse_hessian@grad)
                if float(grad@direction)>=-1e-14: inverse_hessian=np.eye(22); direction=-grad
                alpha=min(1.0,fraction_to_boundary(density,tangent@direction,c["optimization"]["density_floor_electron_per_bohr3"])); slope=float(grad@direction); accepted=False
                for _ in range(60):
                    trial=y+alpha*direction; trial_energy,trial_grad,trial_density,_=evaluate(trial)
                    if trial_energy<=energy+1e-4*alpha*slope:
                        old_y,old_grad=y,grad; y,energy,grad,density=trial,trial_energy,trial_grad,trial_density; accepted_coefficients.append(y.tolist()); accepted_energies.append(energy); s=y-old_y; dg=grad-old_grad; curvature=float(s@dg)
                        if curvature>1e-14:
                            inv=1/curvature; identity=np.eye(22); left=identity-inv*np.outer(s,dg); inverse_hessian=left@inverse_hessian@left.T+inv*np.outer(s,s); inverse_hessian=0.5*(inverse_hessian+inverse_hessian.T)
                        else: inverse_hessian=np.eye(22)
                        accepted=True; break
                    alpha*=0.5
                require(accepted,"feasible Armijo line search failed"); iterations=iteration
            rho=field(density); optimizer_code=0 if optimizer_claimed else 1
        parts=evaluator.get_energy_potential(rho,calcType={"E","V"},split=True); stress=evaluator.get_stress(rho,split=True)
    density=np.asarray(rho,dtype=np.float64).reshape(-1); total_potential=np.asarray(parts["TOTAL"].potential,dtype=float).reshape(-1); electrons=float(math.fsum(float(x) for x in density)*basis["dv"]); negative=float(math.fsum(float(x) for x in np.maximum(-density,0))*basis["dv"]/c["source"]["expected_electrons"])
    require(float(density.min())>=0,"final density negative"); full_grid_euler,chemical=euler_residual_hartree(density,total_potential,basis["dv"],electrons)
    if row["route"]=="coefficient_23_function":
        final_gradient=tangent.T@total_potential*basis["dv"]; projected=float(np.linalg.norm(final_gradient)/math.sqrt(basis["volume"])); rng=np.random.default_rng(20260812)
        for index in range(10):
            d=rng.normal(size=22); d/=np.linalg.norm(d); eps=1e-5; ep=evaluate(eps*d)[0]; em=evaluate(-eps*d)[0]; analytic=float(evaluate(np.zeros(22))[1]@d); numeric=(ep-em)/(2*eps); error=abs(numeric-analytic)/max(1e-10,abs(numeric),abs(analytic)); fd_rows.append({"index":index,"direction_sha256":hashlib.sha256(np.ascontiguousarray(d,dtype=np.float64).tobytes()).hexdigest(),"epsilon":eps,"numeric":numeric,"analytic":analytic,"relative_error":error})
        fd={"accepted":len(fd_rows)==10 and max(x["relative_error"] for x in fd_rows)<1e-5,"max_relative_error":max(x["relative_error"] for x in fd_rows),"directions":fd_rows,"evaluation_point":"uniform_charge_conserving_interior"}
    else: projected=full_grid_euler; fd={"accepted":True,"max_relative_error":0.0,"directions":[],"evaluation_point":"not_applicable_full_grid"}
    components={k:float(v.energy) for k,v in sorted(parts.items())}; component_sum=sum(v for k,v in components.items() if k!="TOTAL"); require(abs(component_sum-components["TOTAL"])<1e-8,"energy component sum differs"); stress_gpa=np.asarray(stress["TOTAL"])*STRESS_CONV["Ha/Bohr3"]["GPa"]; require(np.all(np.isfinite(stress_gpa)) and float(np.max(np.abs(stress_gpa-stress_gpa.T)))<1e-8,"stress differs")
    monotonic=all(b<=aa+1e-10 for aa,b in zip(accepted_energies,accepted_energies[1:])); science_converged=optimizer_claimed and projected<c["acceptance"]["projected_gradient_metric_hartree_strict_lt"] and fd["accepted"] and monotonic
    run=a.run_directory; run.mkdir(parents=True,exist_ok=True); density_buffer=io.BytesIO(); np.save(density_buffer,density,allow_pickle=False); write_exclusive(run/"density.npy",density_buffer.getvalue()); write_exclusive(run/"functional.stdout",captured.getvalue().encode())
    result={"schema_version":1,"protocol_revision":c["protocol_revision"],"experiment_id":row["experiment_id"],"role":row["role"],"route":row["route"],"initialization":row["initialization"],"volume_ratio":row["volume_ratio"],"volume_bohr3":basis["volume"],"grid":list(map(int,basis["counts"])),"status":"accepted" if science_converged else "completed_scientific_rejected","optimizer_converged_code":optimizer_code,"optimizer_claimed_converged":optimizer_claimed,"energy_hartree":components["TOTAL"],"energy_ev_per_atom":components["TOTAL"]*ENERGY_CONV["Hartree"]["eV"],"energy_components_hartree":components,"energy_component_sum_error_hartree":abs(component_sum-components["TOTAL"]),"electron_number":electrons,"electron_number_absolute_error":abs(electrons-c["source"]["expected_electrons"]),"negative_density_fraction":negative,"minimum_density_electron_per_bohr3":float(density.min()),"projected_gradient_metric_hartree":projected,"full_grid_euler_residual_hartree":full_grid_euler,"chemical_potential_hartree":chemical,"gradient_finite_difference":fd,"pressure_gpa_diagnostic":-float(np.trace(stress_gpa))/3,"stress_gpa":stress_gpa.tolist(),"iterations":iterations,"objective_calls_including_fd":trial_count,"accepted_step_coefficients":accepted_coefficients,"accepted_step_energies_hartree":accepted_energies,"accepted_steps_monotonic":monotonic,"basis_sha256":basis["basis_sha256"],"registered_initial_density_sha256":initial_sha,"density_sha256":hashlib.sha256((run/"density.npy").read_bytes()).hexdigest(),"functional_stdout_sha256":hashlib.sha256((run/"functional.stdout").read_bytes()).hexdigest(),"wall_seconds":time.time()-started,"peak_rss_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,"cpu_affinity":sorted(os.sched_getaffinity(0)),"runtime":runtime}
    print(json.dumps(result,sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
