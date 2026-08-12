#!/usr/bin/env python3
from __future__ import annotations
import argparse, contextlib, io, json, math, os, resource, time
from pathlib import Path

import numpy as np
import s2_g2_al1_pilot_common_r1 as pilot
import s2_g2_al_scale_geometry_common_r3 as scale
import analyze_s2_g2_al_localized_r1 as localized
from s2_g2c_performance_common_r1 import load, low_g_count, require, source_exact

M1=np.asarray([[-1,1,1],[1,-1,1],[1,1,-1]],dtype=int)

def reference_conventional_block(rho, size=40):
    old=np.asarray(rho,dtype=float); f=np.fft.fftn(old)/old.size; out=np.zeros((size,size,size),dtype=np.complex128)
    axes=[np.fft.fftfreq(n)*n for n in old.shape]
    for i,a in enumerate(axes[0]):
      for j,b in enumerate(axes[1]):
       for k,d in enumerate(axes[2]):
        vec=np.asarray([int(round(a)),int(round(b)),int(round(d))]); mapped=vec@M1.T; out[tuple((mapped%size).astype(int))]+=f[i,j,k]*out.size
    result=np.fft.ifftn(out); require(float(np.max(np.abs(result.imag)))<1e-10,"reference interpolation is complex"); return result.real

def candidate_conventional_block(cell,coeff,size=40):
    frac=np.stack(np.meshgrid(*[np.arange(size,dtype=float)/size]*3,indexing="ij"),axis=-1).reshape(-1,3)
    primitive=(frac@M1)%1.0; r2=pilot.minimum_image_r2(primitive,cell); volume=float(abs(np.linalg.det(cell))); dv=4*volume/(size**3)
    columns=[np.full(len(frac),1.0/volume)]
    alphas=[0.075,0.15,0.3,0.6,1.2,2.4,4.8,9.6]
    for alpha in alphas:
        fn=np.exp(-alpha*r2); fn/=float(fn.sum(dtype=np.float64)*dv/4.0); columns.append(fn-1.0/volume)
    vectors=[(0,0,1),(0,1,0),(1,0,0),(1,1,1),(0,1,1),(1,0,1),(1,1,0)]
    for v in vectors:
        phase=2*math.pi*(primitive@np.asarray(v,dtype=float)); columns.extend((np.cos(phase),np.sin(phase)))
    matrix=np.stack(columns,axis=1); require(matrix.shape==(size**3,23),"candidate matrix differs"); return (matrix@np.asarray(coeff,dtype=float)).reshape((size,)*3)

def main():
    p=argparse.ArgumentParser(); p.add_argument("--project-root",type=Path,default=Path(__file__).resolve().parents[1]); p.add_argument("--case",required=True); a=p.parse_args(); root=a.project_root.resolve(); c=load(root); source_exact(root,c)
    case=next(x for x in __import__('s2_g2c_performance_common_r1').case_rows(c) if x["case_id"]==a.case)
    require(set(os.sched_getaffinity(0))=={c["benchmark"]["reserved_logical_cpu"]},"worker affinity differs")
    scale_cfg=json.loads((root/c["source"]["scale_config_path"]).read_text()); mapped=scale.runtime_source_config(scale_cfg); cell,rho,_=pilot.validate_sources(root,mapped)
    spectrum=json.loads((root/c["source"]["basis_spectrum_path"]).read_text()); coeff=spectrum["candidates"]["r08_eta100_explicit"]["coefficients"]; require(len(coeff)==23,"coefficient count differs")
    ref_block=reference_conventional_block(rho,40); cand_probe=candidate_conventional_block(cell,coeff,40); density_l2=float(np.linalg.norm(cand_probe-ref_block)/np.linalg.norm(ref_block))
    n=case["conventional_repeat"]; matrix=n*M1; supercell=matrix@cell; positions=np.asarray(scale.coset_positions(matrix),dtype=float); require(len(positions)==case["atom_count"],"atom count differs")
    before=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss; start=time.perf_counter_ns()
    if case["route"]=="pw_fft_reference": density=np.tile(ref_block,(n,n,n))
    else: density=np.tile(candidate_conventional_block(cell,coeff,40),(n,n,n))
    eval_cfg={"source":{"pseudopotential_path":c["source_runtime_pseudopotential_path"]}} if "source_runtime_pseudopotential_path" in c else {"source":{"pseudopotential_path":scale_cfg["pseudopotential"]["path"]}}
    with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()): energies,_=localized.evaluate_components(eval_cfg,supercell,case["grid"],positions,{"density":density})
    wall=(time.perf_counter_ns()-start)/1e9; after=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    dv=float(abs(np.linalg.det(supercell)))/density.size; electrons=float(density.sum(dtype=np.float64)*dv); low=low_g_count(supercell,3*case["atom_count"],1.0,24); generic=1+2*low+8*case["atom_count"]
    out={**case,"status":"accepted_worker","route_wall_seconds":wall,"peak_rss_kib":int(max(before,after)),"density_relative_l2":density_l2,"electron_number_relative_error":abs(electrons-3*case["atom_count"])/(3*case["atom_count"]),"energies_ev":energies["density"],"generic_half_space_low_g_count":low,"generic_coefficient_count":generic,"grid_degree_count":int(np.prod(case["grid"])),"affinity":sorted(os.sched_getaffinity(0))}
    print(json.dumps(out,sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
