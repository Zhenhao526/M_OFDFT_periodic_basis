#!/usr/bin/env python3
from __future__ import annotations
import argparse, math
from pathlib import Path
from s3_g3_ksdft_reference_common_r1 import atomic_write, canonical, load, require, sha_bytes

BOHR_PER_ANG=1.8897261254578281
def render(c,row):
    q=c["input_contract"]; suffix=row["suffix"]
    inp=("INPUT_PARAMETERS\n"+"\n".join([f"suffix {suffix}",f"out_chg 1 {q['density_cube_precision']}","calculation scf","esolver_type ksdft","basis_type pw","dft_functional PBE","symmetry 0","pseudo_dir .","pseudo_rcut 16",f"ecutwfc {q['ecutwfc_ry']}",f"ecutrho {q['ecutrho_ry']}",f"scf_nmax {q['scf_nmax']}","cal_force 1","cal_stress 1",f"scf_thr {q['scf_thr']:.0e}","ks_solver cg",f"smearing_method {q['smearing_method']}",f"smearing_sigma {q['smearing_sigma_ry']}","mixing_type broyden","mixing_beta 0.4","vnl_in_h 1"])+"\n").encode()
    a=c["materials"]["al"]["a0_angstrom"]*row["volume_ratio"]**(1/3); half=a/2
    stru=("ATOMIC_SPECIES\nAl 26.9815385 Al_std.upf upf201\n\nLATTICE_CONSTANT\n"+f"{BOHR_PER_ANG:.16f}\n\nLATTICE_VECTORS\n"+f"0.0000000000000000 {half:.16f} {half:.16f}\n{half:.16f} 0.0000000000000000 {half:.16f}\n{half:.16f} {half:.16f} 0.0000000000000000\n\nATOMIC_POSITIONS\nDirect\n\nAl\n0.0\n1\n0.0000000000000000 0.0000000000000000 0.0000000000000000 1 1 1\n").encode()
    k=q["kmesh"]; kpt=(f"K_POINTS\n0\nGamma\n{k[0]} {k[1]} {k[2]} 0 0 0\n").encode()
    return inp,stru,kpt
def generate(root):
    c=load(root); out=root/c["execution"]["input_root"]
    for row in c["formal_cases"]:
        d=out/row["experiment_id"]; d.mkdir(parents=True,exist_ok=True); inp,stru,kpt=render(c,row)
        for name,data in (("INPUT",inp),("STRU",stru),("KPT",kpt)):
            p=d/name
            if p.exists(): require(p.read_bytes()==data,f"generated {name} differs")
            else: atomic_write(p,data)
        meta={"schema_version":1,"protocol_revision":c["protocol_revision"],"experiment_id":row["experiment_id"],"phase":"ksdft_reference","requirement":"ksdft_scientific_hard_reference","material":"al","volume_ratio":row["volume_ratio"],"role":"ks_nl_physical_reference","suffix":row["suffix"],"atom_count":1,"expected_electrons":3.0,"pseudo":{"basename":"Al_std.upf","sha256":c["pseudodojo"]["materials"]["al"]["sha256"],"upstream_url":c["pseudodojo"]["materials"]["al"]["url"],"upstream_commit":c["pseudodojo"]["commit"],"format":"upf201"},"input_identity":{"INPUT_sha256":sha_bytes(inp),"STRU_sha256":sha_bytes(stru),"KPT_sha256":sha_bytes(kpt)},"thermodynamic_semantics":{"F":"E_KohnSham=!FINAL_ETOT_IS","m":"E_entropy(-TS)<=0","U":"F-m","E_ec":"F-m/2; finite-smearing estimator","absolute_cross_functional_energy_comparison_forbidden":True}}
        p=d/"metadata.json"; data=canonical(meta)
        if p.exists(): require(p.read_bytes()==data,"metadata differs")
        else: atomic_write(p,data)
    return len(c["formal_cases"])
def main():
    p=argparse.ArgumentParser();p.add_argument("--project-root",type=Path,default=Path(__file__).resolve().parents[1]);a=p.parse_args();print(f"generated_or_verified={generate(a.project_root.resolve())}");return 0
if __name__=="__main__": raise SystemExit(main())
