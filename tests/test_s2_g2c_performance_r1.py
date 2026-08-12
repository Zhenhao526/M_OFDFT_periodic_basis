from pathlib import Path
import json,sys
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/"scripts"))
import s2_g2c_performance_common_r1 as c
def test_config_and_cases(): cfg=c.load(ROOT); c.validate_config(cfg); assert len(c.case_rows(cfg))==18 and len({x["case_id"] for x in c.case_rows(cfg)})==18
def test_source_chain(): c.source_exact(ROOT,c.load(ROOT))
def test_performance_rule_rejects_compression_alone():
 cfg=c.load(ROOT); raw=[]
 for case in c.case_rows(cfg): raw.append({**case,"status":"accepted_worker","route_wall_seconds":1.0 if case["route"]=="pw_fft_reference" else 1.1,"peak_rss_kib":1000 if case["route"]=="pw_fft_reference" else 1050,"density_relative_l2":0.005,"electron_number_relative_error":1e-13,"energies_ev":{"hartree":0.0,"external":0.0,"xc":0.0,"fixed_kedf":0.0},"generic_coefficient_count":100,"grid_degree_count":case["grid"][0]**3})
 assert c.summarize(cfg,raw)["status"]=="evidence_valid_g2c_performance_rejected"
def test_cpu_set_and_collision_classifier(tmp_path,monkeypatch):
 assert c._cpu_set("0-2,7,9-10") == {0,1,2,7,9,10}
 cfg=c.load(ROOT); monkeypatch.setattr(c.socket,"gethostname",lambda:"node01")
 topo=Path("/sys/devices/system/cpu/cpu74/topology/thread_siblings_list")
 if c._cpu_set(topo.read_text()) != {74,150}: return
 proc=tmp_path/"proc"; proc.mkdir(); p=proc/"999"; p.mkdir()
 (p/"status").write_text("Name:\tabacus_pw_para\nPPid:\t1\nCpus_allowed_list:\t74\n")
 (p/"cmdline").write_bytes(b"/opt/abacus_pw_para\0")
 try: c.resource_preflight(cfg,proc_root=proc,excluded_pids=set())
 except ValueError as e: assert "collision" in str(e)
 else: raise AssertionError("scientific collision was accepted")
 (p/"status").write_text("Name:\tabacus_pw_para\nPPid:\t1\nCpus_allowed_list:\t38-73\n")
 assert c.resource_preflight(cfg,proc_root=proc,excluded_pids=set())["status"]=="accepted"
