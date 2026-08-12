#!/usr/bin/env python3
from __future__ import annotations
import hashlib, json, os, subprocess, tempfile
from pathlib import Path

CONFIG_REL=Path("config/S3_g3_ksdft_reference_r1.json")
PROTOCOL="S3-G3-KSDFT-REFERENCE-20260812-R1"

def require(value,message):
    if not value: raise ValueError(message)
def canonical(value): return (json.dumps(value,sort_keys=True,indent=2,ensure_ascii=False)+"\n").encode()
def sha_bytes(value): return hashlib.sha256(value).hexdigest()
def sha_path(path):
    path=Path(path); require(path.is_file() and not path.is_symlink(),f"not a regular file: {path}")
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1048576),b""): h.update(block)
    return h.hexdigest()
def read_json(path):
    path=Path(path); require(path.is_file() and not path.is_symlink(),f"missing JSON: {path}")
    return json.loads(path.read_text())
def git(root,*args): return subprocess.run(["git",*args],cwd=root,check=True,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout.strip()
def atomic_write(path,data,exclusive=True):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    if exclusive:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,"wb") as f: f.write(data); f.flush(); os.fsync(f.fileno())
    else:
        fd,tmp=tempfile.mkstemp(prefix=f".{path.name}.",dir=path.parent)
        try:
            with os.fdopen(fd,"wb") as f: f.write(data); f.flush(); os.fsync(f.fileno())
            os.replace(tmp,path)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
    d=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY); os.fsync(d); os.close(d)
def load(root):
    c=read_json(Path(root)/CONFIG_REL); validate_config(c); return c
def validate_config(c):
    require(c["schema_version"]==1 and c["protocol_revision"]==PROTOCOL,"config identity differs")
    require(c["scope"]=={"stage":"S3","gate":"G3_Al_KSDFT_physical_reference_pilot","material":"Al","candidate_id":"r08_eta100_complementary","ksdft_is_scientific_hard_reference":True,"full_grid_wt_is_numerical_diagnostic_only":True,"absolute_cross_functional_energy_comparison_forbidden":True,"g2c_performance_rejection_preserved":True,"s4_authorized":False},"scope differs")
    cases=c["formal_cases"]; require(len(cases)==3,"case count differs")
    require([(x["experiment_id"],x["volume_ratio"]) for x in cases]==[("S3-20260812-030",.995),("S3-20260812-031",1.0),("S3-20260812-032",1.005)],"case matrix differs")
    a=c["acceptance"]; require(a["density_relative_l2_strictly_less_than"]==.015 and a["anchored_relative_energy_abs_difference_mev_per_atom_strictly_less_than"]==10 and a["pressure_difference_abs_strictly_less_than_gpa"]==.2,"scientific gates differ")
    require(c["execution"]["formal_case_count"]==3 and c["execution"]["new_solver_run_count"]==3 and c["execution"]["no_retry"] is True,"execution contract differs")
    return c
def source_exact(root,c):
    root=Path(root); s=c["source"]
    for commit,key,sha_key in ((s["s3_evidence_commit"],"s3_summary_path","s3_summary_sha256"),(s["s3_evidence_commit"],"s3_runs_path","s3_runs_sha256"),(s["historical_ks_v100_commit"],"historical_ks_v100_result_path","historical_ks_v100_result_sha256"),(s["historical_ks_v100_commit"],"historical_ks_v100_cube_path","historical_ks_v100_cube_sha256")):
        rel=s[key]; expected=s[sha_key]; path=root/rel
        require(sha_path(path)==expected,f"source SHA differs: {rel}")
        blob=subprocess.run(["git","show",f"{commit}:{rel}"],cwd=root,check=True,stdout=subprocess.PIPE).stdout
        require(blob==path.read_bytes() and sha_bytes(blob)==expected,f"committed source differs: {rel}")
    summary=read_json(root/s["s3_summary_path"]); require(summary["evidence_valid"] is True,"S3 evidence invalid")
    terminal=Path(s["s3_external_state_root"])/"terminal.json"; require(sha_path(terminal)==s["s3_terminal_sha256"],"S3 terminal differs")
    for v,eid in s["candidate_density_ids"].items():
        path=next(Path(s["s3_external_state_root"]).glob(f"runs/*_{eid}/density.npy"),None); require(path is not None and path.is_file() and not path.is_symlink(),f"candidate density missing: {v}")
    return True
def normalize_registration(data):
    c=json.loads(data); c["status"]="implementation_pending_preregistration"; c["implementation_commit"]="__FREEZE_IMPLEMENTATION_COMMIT__"; return canonical(c)
def config_at(root,commit): return json.loads(subprocess.run(["git","show",f"{commit}:{CONFIG_REL.as_posix()}"],cwd=root,check=True,stdout=subprocess.PIPE).stdout)
def registered_paths(c):
    paths=[CONFIG_REL.as_posix(),"docs/S3_G3_KSDFT_REFERENCE_R1_PROTOCOL.md","scripts/s3_g3_ksdft_reference_common_r1.py","scripts/generate_s3_g3_ksdft_reference_r1.py","scripts/s3_g3_ksdft_reference_rank_wrapper_r1.py","scripts/parse_s3_g3_ksdft_reference_r1.py","scripts/run_s3_g3_ksdft_reference_r1.py","scripts/collect_s3_g3_ksdft_reference_r1.py","scripts/validate_s3_g3_ksdft_reference_r1.py","tests/test_s3_g3_ksdft_reference_r1.py"]
    for row in c["formal_cases"]:
        paths += [f"{c['execution']['input_root']}/{row['experiment_id']}/{name}" for name in ("INPUT","STRU","KPT","metadata.json")]
    return paths
def implementation_identity(root,head):
    c=config_at(root,head); validate_config(c); require(c["status"]=="implementation_pending_preregistration","implementation status differs")
    parents=git(root,"show","-s","--format=%P",head).split(); require(parents==[c["base_commit"]],"implementation parent differs")
    changes=git(root,"diff","--name-status",c["base_commit"],head).splitlines(); require(changes==[f"A\t{x}" for x in sorted(registered_paths(c))],"implementation diff differs")
    return c
def registered_chain(root,prereg):
    parents=git(root,"show","-s","--format=%P",prereg).split(); require(len(parents)==1,"prereg parent count differs"); impl=parents[0]
    c=config_at(root,prereg); validate_config(c); require(c["status"]=="preregistered_no_execution" and c["implementation_commit"]==impl,"prereg identity differs")
    changed=git(root,"diff","--name-status",impl,prereg).splitlines(); require(changed==[f"M\t{CONFIG_REL.as_posix()}"],"prereg diff differs")
    old=subprocess.run(["git","show",f"{impl}:{CONFIG_REL.as_posix()}"],cwd=root,check=True,stdout=subprocess.PIPE).stdout
    new=subprocess.run(["git","show",f"{prereg}:{CONFIG_REL.as_posix()}"],cwd=root,check=True,stdout=subprocess.PIPE).stdout
    require(normalize_registration(old)==normalize_registration(new),"prereg changed scientific content")
    implementation_identity(root,impl); validate_config(c); return impl,c

def fourier_resample(values,target):
    import numpy as np
    old=np.asarray(values,dtype=np.float64); require(old.ndim==3,"density rank differs"); transformed=np.fft.fftn(old)/old.size
    shifted=np.fft.fftshift(transformed); new=np.zeros((target,target,target),dtype=np.complex128)
    slices_old=[]; slices_new=[]
    for n in old.shape:
        m=min(n,target); start_old=(n-m)//2; start_new=(target-m)//2; slices_old.append(slice(start_old,start_old+m)); slices_new.append(slice(start_new,start_new+m))
    new[tuple(slices_new)]=shifted[tuple(slices_old)]*target**3
    out=np.fft.ifftn(np.fft.ifftshift(new)); require(float(np.max(np.abs(out.imag)))<1e-10,"resampled density complex"); return np.asarray(out.real,dtype=np.float64)

def scientific_metrics(c,ks_rows,candidate_rows,ks_densities,candidate_densities,historical):
    import numpy as np
    volumes=[.995,1.0,1.005]; a=c["acceptance"]
    ks={float(x["volume_ratio"]):x for x in ks_rows}; cand={float(x["volume_ratio"]):x for x in candidate_rows}
    require(sorted(ks)==volumes and sorted(cand)==volumes,"volume denominator differs")
    density=[]
    for v in volumes:
        ref=np.asarray(ks_densities[v],float); trial=np.asarray(candidate_densities[v],float); require(ref.shape==trial.shape,"density grid differs")
        value=float(np.linalg.norm(trial-ref)/np.linalg.norm(ref)); density.append({"volume_ratio":v,"relative_l2":value,"accepted":value<a["density_relative_l2_strictly_less_than"]})
    energy=[]; ks0=ks[1.0]["thermodynamic_labels_ev_per_atom"]["E_ec"]; c0=cand[1.0]["energy_ev_per_atom"]
    for v in volumes:
        k=(ks[v]["thermodynamic_labels_ev_per_atom"]["E_ec"]-ks0)*1000; q=(cand[v]["energy_ev_per_atom"]-c0)*1000; diff=abs(q-k)
        energy.append({"volume_ratio":v,"ks_anchored_mev_per_atom":k,"coefficient_anchored_mev_per_atom":q,"absolute_difference_mev_per_atom":diff,"accepted":diff<a["anchored_relative_energy_abs_difference_mev_per_atom_strictly_less_than"]})
    volume_bohr3=float(cand[1.0]["volume_bohr3"]); d=.005; conv=29421.02648438959
    kp=-(ks[1.005]["thermodynamic_labels_ev_per_atom"]["E_ec"]-ks[.995]["thermodynamic_labels_ev_per_atom"]["E_ec"])/(2*d*volume_bohr3)*conv
    cp=-(cand[1.005]["energy_hartree"]-cand[.995]["energy_hartree"])/(2*d*volume_bohr3)*conv
    pressure={"ks_gpa":kp,"coefficient_gpa":cp,"absolute_difference_gpa":abs(cp-kp),"accepted":abs(cp-kp)<a["pressure_difference_abs_strictly_less_than_gpa"]}
    repeat={"energy_abs_difference_mev_per_atom":abs(ks0-historical["thermodynamic_labels_ev_per_atom"]["E_ec"])*1000,"pressure_abs_difference_gpa":abs(ks[1.0]["pressure_gpa"]-historical["pressure_gpa"])}
    repeat["accepted"]=repeat["energy_abs_difference_mev_per_atom"]<a["ks_v100_repeat_energy_abs_difference_mev_per_atom_strictly_less_than"] and repeat["pressure_abs_difference_gpa"]<a["ks_v100_repeat_pressure_abs_difference_gpa_strictly_less_than"]
    accepted=all(x["accepted"] for x in density+energy) and pressure["accepted"] and repeat["accepted"]
    return {"density_rows":density,"anchored_energy_rows":energy,"pressure":pressure,"v100_repeat":repeat,"scientific_gate_accepted":accepted}
