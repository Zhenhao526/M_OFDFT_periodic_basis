#!/usr/bin/env python3
from __future__ import annotations
import hashlib, itertools, json, math, os, socket, subprocess
from pathlib import Path

BASE_COMMIT="aba35630037c623cc636225afc4083ae70cdbfac"
CONFIG_REL=Path("config/S2_g2c_performance_r1.json")
PROTOCOL_REL=Path("docs/S2_G2C_PERFORMANCE_R1_PROTOCOL.md")
COMMON_REL=Path("scripts/s2_g2c_performance_common_r1.py")
WORKER_REL=Path("scripts/run_s2_g2c_performance_worker_r1.py")
RUNNER_REL=Path("scripts/run_s2_g2c_performance_r1.py")
COLLECTOR_REL=Path("scripts/collect_s2_g2c_performance_r1.py")
VALIDATOR_REL=Path("scripts/validate_s2_g2c_performance_r1.py")
TEST_REL=Path("tests/test_s2_g2c_performance_r1.py")
IMPLEMENTATION_PATHS={str(x) for x in (CONFIG_REL,PROTOCOL_REL,COMMON_REL,WORKER_REL,RUNNER_REL,COLLECTOR_REL,VALIDATOR_REL,TEST_REL)}

def require(x,msg):
    if not x: raise ValueError(msg)
def canonical(x): return (json.dumps(x,indent=2,sort_keys=True,ensure_ascii=False)+"\n").encode()
def sha_bytes(b): return hashlib.sha256(b).hexdigest()
def sha_path(p): return sha_bytes(p.read_bytes())
def git(root,*args):
    p=subprocess.run(["git",*args],cwd=root,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    require(p.returncode==0,f"git {' '.join(args)} failed: {p.stderr.strip()}"); return p.stdout.strip()
def load(root): return json.loads((root/CONFIG_REL).read_text())

def _cpu_set(text):
    out=set()
    for part in text.strip().split(","):
        if not part: continue
        if "-" in part:
            lo,hi=(int(x) for x in part.split("-",1)); out.update(range(lo,hi+1))
        else: out.add(int(part))
    return out

def _ancestor_pids(pid=None,proc_root=Path("/proc")):
    pid=os.getpid() if pid is None else int(pid); out=set()
    while pid>1 and pid not in out:
        out.add(pid)
        try:
            status=(proc_root/str(pid)/"status").read_text()
            pid=int(next(line.split()[1] for line in status.splitlines() if line.startswith("PPid:")))
        except (FileNotFoundError,PermissionError,StopIteration,ValueError): break
    return out

def resource_preflight(c,proc_root=Path("/proc"),excluded_pids=None):
    b=c["benchmark"]; require(socket.gethostname()==b["execution_hostname"],"benchmark hostname differs")
    cpu=int(b["reserved_logical_cpu"]); domain=set(int(x) for x in b["reserved_smt_domain"])
    require(cpu in domain and len(domain)==2,"reserved SMT domain differs")
    siblings=_cpu_set((Path(f"/sys/devices/system/cpu/cpu{cpu}/topology/thread_siblings_list")).read_text())
    require(siblings==domain,"live SMT topology differs")
    excluded=_ancestor_pids(proc_root=proc_root) if excluded_pids is None else set(excluded_pids)
    collisions=[]
    for entry in proc_root.iterdir():
        if not entry.name.isdigit() or int(entry.name) in excluded: continue
        try:
            status=(entry/"status").read_text(); cmd=(entry/"cmdline").read_bytes().replace(b"\0",b" ").decode(errors="replace")
            fields={line.split(":",1)[0]:line.split(":",1)[1].strip() for line in status.splitlines() if ":" in line}
            comm=fields.get("Name","").lower(); low=cmd.lower(); allowed=_cpu_set(fields.get("Cpus_allowed_list",""))
        except (FileNotFoundError,PermissionError,ProcessLookupError): continue
        scientific=("abacus" in comm or comm in {"pw.x","pp.x"} or (comm.startswith("python") and ("dftpy" in low or "g2c_performance" in low)))
        if scientific and allowed & domain:
            collisions.append({"pid":int(entry.name),"comm":comm,"allowed":sorted(allowed),"cmdline_sha256":sha_bytes(cmd.encode())})
    require(not collisions,f"reserved SMT collision: {collisions}")
    return {"status":"accepted","hostname":socket.gethostname(),"logical_cpu":cpu,"smt_domain":sorted(domain),"collisions":[]}

def validate_config(c):
    require(c["schema_version"]==1 and c["protocol_revision"]=="S2-G2C-PERFORMANCE-20260812-R1","schema differs")
    require(c["base_commit"]==BASE_COMMIT and c["candidate_id"]=="r08_eta100_complementary","base/candidate differs")
    b=c["benchmark"]; require(b["atom_counts"]==[32,108,256] and b["conventional_repeats"]==[2,3,4] and b["grid_counts"]==[[80,80,80],[120,120,120],[160,160,160]],"size denominator differs")
    require(b["routes"]==["pw_fft_reference","candidate_23_function"] and b["measured_repeats"]==3 and b["thread_count"]==1,"run denominator differs")
    require(b["execution_hostname"]=="node01" and b["reserved_logical_cpu"]==74 and b["reserved_smt_domain"]==[74,150],"resource reservation differs")
    require(b["timed_boundary"]=="density_representation_decode_through_Hartree_local_pseudo_PBE_XC_fixed_WT_energies" and b["common_source_preparation_excluded_from_route_wall"] is True,"timing boundary differs")
    p=c["performance"]; require(p=={"effective_coefficient_fraction_max":0.3,"time_speedup_min":1.5,"peak_memory_improvement_min":2.0,"other_metric_degradation_max_fraction":0.2,"aggregation":"median_of_three_cold_processes_per_route_and_size","overall_requires_each_size":True},"performance gates differ")
    require(c["execution"]["case_count"]==18 and c["execution"]["new_electronic_structure_solver_run_count"]==0,"execution differs")
    require(c["output_files"]==["README.md","raw_runs.json","size_metrics.tsv","summary.json"],"outputs differ")

def source_exact(root,c):
    for prefix,path_key,sha_key,commit_key in (("policy","policy_summary_path","policy_summary_sha256","policy_evidence_commit"),("basis","basis_spectrum_path","basis_spectrum_sha256","basis_evidence_commit")):
        rel=c["source"][path_key]; expected=c["source"][sha_key]; commit=c["source"][commit_key]; path=root/rel
        require(path.is_file() and not path.is_symlink() and sha_path(path)==expected,f"{prefix} source differs")
        data=subprocess.run(["git","show",f"{commit}:{rel}"],cwd=root,stdout=subprocess.PIPE,check=True).stdout
        require(data==path.read_bytes() and sha_bytes(data)==expected,f"{prefix} committed source differs")
    policy=json.loads((root/c["source"]["policy_summary_path"]).read_text()); require(policy["status"]=="accepted_density_expansion_candidate" and policy["candidate_id"]==c["candidate_id"],"policy status differs")
    scale=root/c["source"]["scale_config_path"]; require(sha_path(scale)==c["source"]["scale_config_sha256"],"scale config differs")

def case_rows(c):
    rows=[]
    for atoms,n,grid in zip(c["benchmark"]["atom_counts"],c["benchmark"]["conventional_repeats"],c["benchmark"]["grid_counts"]):
        for route in c["benchmark"]["routes"]:
            for repeat in range(1,c["benchmark"]["measured_repeats"]+1): rows.append({"case_id":f"S2-G2C-{atoms:03d}-{route}-{repeat}","atom_count":atoms,"conventional_repeat":n,"grid":grid,"route":route,"repeat":repeat})
    return rows

def low_g_count(cell,electrons,eta=1.0,bound=20):
    import numpy as np
    volume=float(abs(np.linalg.det(cell))); kf=(3*math.pi**2*electrons/volume)**(1/3); reciprocal=2*math.pi*np.linalg.inv(cell).T; count=0
    for v in itertools.product(range(-bound,bound+1),repeat=3):
        if v==(0,0,0): continue
        if next(x for x in v if x!=0)<0: continue
        if float(np.linalg.norm(np.asarray(v)@reciprocal)/(2*kf))<=eta+1e-12: count+=1
    return count

def summarize(c,raw):
    import statistics
    require(len(raw)==18,"raw denominator differs"); ids=[r["case_id"] for r in raw]; require(ids==[r["case_id"] for r in case_rows(c)] and len(set(ids))==18,"raw IDs differ")
    sizes=[]; a=c["accuracy"]; p=c["performance"]
    for atoms,grid in zip(c["benchmark"]["atom_counts"],c["benchmark"]["grid_counts"]):
        by={route:[r for r in raw if r["atom_count"]==atoms and r["route"]==route] for route in c["benchmark"]["routes"]}
        for route in by: require(len(by[route])==3 and all(r["status"]=="accepted_worker" for r in by[route]),f"worker failure: {atoms}/{route}")
        pw=by["pw_fft_reference"]; cand=by["candidate_23_function"]
        pw_t=statistics.median(r["route_wall_seconds"] for r in pw); ca_t=statistics.median(r["route_wall_seconds"] for r in cand)
        pw_m=statistics.median(r["peak_rss_kib"] for r in pw); ca_m=statistics.median(r["peak_rss_kib"] for r in cand)
        ref=pw[0]["energies_ev"]; sel=cand[0]["energies_ev"]; errs={k:(sel[k]-ref[k])*1000/atoms for k in ref}; combined=errs["hartree"]+errs["external"]+errs["xc"]
        density=max(r["density_relative_l2"] for r in cand); electron=max(r["electron_number_relative_error"] for r in cand)
        accuracy={"density":density<a["density_relative_l2_strict_lt"],"electron":electron<a["electron_number_relative_error_strict_lt"],"component":max(abs(errs[k]) for k in ("hartree","external","xc"))<=a["component_energy_abs_error_max_mev_per_atom"],"combined":abs(combined)<=a["combined_energy_abs_error_max_mev_per_atom"],"fixed_wt":abs(errs["fixed_kedf"]+combined)<=a["fixed_wt_total_energy_error_max_mev_per_atom"]}
        coeff=cand[0]["generic_coefficient_count"]; fraction=coeff/int(math.prod(grid)); speed=pw_t/ca_t; mem=pw_m/ca_m; time_deg=ca_t/pw_t; mem_deg=ca_m/pw_m
        performance=(speed>=p["time_speedup_min"] and mem_deg<=1+p["other_metric_degradation_max_fraction"]) or (mem>=p["peak_memory_improvement_min"] and time_deg<=1+p["other_metric_degradation_max_fraction"])
        sizes.append({"atom_count":atoms,"grid":grid,"pw_median_wall_seconds":pw_t,"candidate_median_wall_seconds":ca_t,"time_speedup":speed,"pw_median_peak_rss_kib":pw_m,"candidate_median_peak_rss_kib":ca_m,"peak_memory_improvement":mem,"generic_coefficient_count":coeff,"grid_degree_count":int(math.prod(grid)),"coefficient_fraction":fraction,"energy_errors_mev_per_atom":errs,"combined_error_mev_per_atom":combined,"density_relative_l2":density,"electron_number_relative_error":electron,"accuracy_gates":accuracy,"compression_passed":fraction<p["effective_coefficient_fraction_max"],"performance_passed":performance})
    accuracy=all(all(x["accuracy_gates"].values()) and x["compression_passed"] for x in sizes); performance=all(x["performance_passed"] for x in sizes); status="accepted_g2c_performance" if accuracy and performance else "evidence_valid_g2c_performance_rejected"
    return {"schema_version":1,"protocol_revision":c["protocol_revision"],"status":status,"evidence_valid":True,"accuracy_and_compression_accepted":accuracy,"performance_accepted":performance,"candidate_id":c["candidate_id"],"size_count":3,"raw_case_count":18,"size_metrics":sizes,"new_electronic_structure_solver_run_count":0,"s3_authorized":bool(accuracy and performance),"next_action":"start_S3_fixed_KEDF_solver_revision" if accuracy and performance else "record_G2_reproducible_performance_negative_and_do_not_start_S3"}

def render(c,raw):
    s=summarize(c,raw); cols=["atom_count","grid","pw_median_wall_seconds","candidate_median_wall_seconds","time_speedup","pw_median_peak_rss_kib","candidate_median_peak_rss_kib","peak_memory_improvement","generic_coefficient_count","grid_degree_count","coefficient_fraction","density_relative_l2","combined_error_mev_per_atom","performance_passed"]
    lines=["\t".join(cols)]+["\t".join(str(x[k]) for k in cols) for x in s["size_metrics"]]
    readme=(f"# S2/G2c performance R1\n\nStatus: `{s['status']}`.\n\nAccuracy and compression: `{s['accuracy_and_compression_accepted']}`. Performance: `{s['performance_accepted']}`. Three cold single-thread processes per route and size; no electronic-structure solver was run. Representation compression is reported separately and is not treated as a performance pass.\n").encode()
    return {"README.md":readme,"raw_runs.json":canonical({"schema_version":1,"protocol_revision":c["protocol_revision"],"runs":raw}),"size_metrics.tsv":("\n".join(lines)+"\n").encode(),"summary.json":canonical(s)}
