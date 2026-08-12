#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,os,subprocess,time
from pathlib import Path
from s2_g2c_performance_common_r1 import case_rows,git,load,require,resource_preflight,source_exact,validate_config
def main():
 p=argparse.ArgumentParser(); p.add_argument("--project-root",type=Path,default=Path(__file__).resolve().parents[1]); p.add_argument("--dry-run",action="store_true"); a=p.parse_args(); root=a.project_root.resolve(); c=load(root); validate_config(c); source_exact(root,c); require(git(root,"status","--porcelain")=="","dirty"); require(c["status"]=="preregistered_no_execution","not preregistered"); require(git(root,"show","-s","--format=%P",git(root,"rev-parse","HEAD")).split()==[c["implementation_commit"]],"not prereg HEAD"); state=Path(c["execution"]["state_root"]); rows=case_rows(c)
 if a.dry_run: require(not state.exists(),"state exists"); print(json.dumps({"status":"accepted_dry_run","case_count":len(rows),"state_exists":False,"solver_count":0},sort_keys=True)); return 0
 require(not state.exists(),"state exists"); preflight=resource_preflight(c); state.mkdir(parents=True); (state/"runs").mkdir(); session={"schema_version":1,"protocol_revision":c["protocol_revision"],"runner_commit":git(root,"rev-parse","HEAD"),"config_sha256":__import__('hashlib').sha256((root/'config/S2_g2c_performance_r1.json').read_bytes()).hexdigest(),"case_ids":[x["case_id"] for x in rows],"resource_preflight":preflight,"started_unix_ns":time.time_ns()}; (state/"session.json").write_text(json.dumps(session,indent=2,sort_keys=True)+"\n")
 env={"HOME":os.environ["HOME"],"PATH":os.environ["PATH"],"PYTHONPATH":str(root/"scripts"),"OMP_NUM_THREADS":"1","MKL_NUM_THREADS":"1","OPENBLAS_NUM_THREADS":"1","PYTHONDONTWRITEBYTECODE":"1","PYTHONNOUSERSITE":"1"}
 for index,row in enumerate(rows,1):
  case_preflight=resource_preflight(c)
  cmd=["taskset","-c",str(c["benchmark"]["reserved_logical_cpu"]),c["runtime_python"],"-s","-B",str(root/"scripts/run_s2_g2c_performance_worker_r1.py"),"--project-root",str(root),"--case",row["case_id"]]
  start=time.perf_counter_ns(); proc=subprocess.run(cmd,cwd=root,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE); elapsed=(time.perf_counter_ns()-start)/1e9
  run=state/"runs"/f"{index:02d}_{row['case_id']}"; run.mkdir(); (run/"stdout.txt").write_text(proc.stdout); (run/"stderr.txt").write_text(proc.stderr); (run/"command.json").write_text(json.dumps(cmd,indent=2)+"\n"); (run/"resource_preflight.json").write_text(json.dumps(case_preflight,indent=2,sort_keys=True)+"\n")
  require(proc.returncode==0,f"worker failed {row['case_id']}: {proc.stderr[-1000:]}"); result=json.loads(proc.stdout.strip().splitlines()[-1]); require(result["case_id"]==row["case_id"],"worker result ID differs"); result["process_wall_seconds"]=elapsed; (run/"result.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n"); print(json.dumps({"completed":index,"total":len(rows),"case_id":row["case_id"],"route_wall_seconds":result["route_wall_seconds"],"peak_rss_kib":result["peak_rss_kib"]},sort_keys=True),flush=True)
 terminal={"schema_version":1,"status":"accepted","attempted":18,"accepted":18,"failed":0,"runner_return_code":0,"finished_unix_ns":time.time_ns()}; (state/"terminal.json").write_text(json.dumps(terminal,indent=2,sort_keys=True)+"\n"); return 0
if __name__=="__main__": raise SystemExit(main())
