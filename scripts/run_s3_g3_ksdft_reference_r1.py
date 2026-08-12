#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,os,shutil,signal,socket,subprocess,sys,time,traceback
from pathlib import Path
from s3_g3_ksdft_reference_common_r1 import CONFIG_REL,atomic_write,canonical,git,load,require,sha_path,source_exact
from parse_s3_g3_ksdft_reference_r1 import parse_run
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from run_s1_g1_three_layer_continuation_r2 import inspect_core_collisions

def env(c):
 p=c["runtime"]["prefix"];return {"HOME":"/home/shenwei01","USER":"shenwei01","LOGNAME":"shenwei01","PATH":f"{p}/bin:/usr/bin:/bin","LD_LIBRARY_PATH":f"{p}/lib","OPAL_PREFIX":p,"PRTE_PREFIX":p,"PMIX_PREFIX":p,"OMP_NUM_THREADS":"1","OMP_PROC_BIND":"true","OMP_PLACES":"cores","MKL_NUM_THREADS":"1","OPENBLAS_NUM_THREADS":"1","LC_ALL":"C","LANG":"C","TZ":"UTC"}
def progress(x):
 try: print(json.dumps(x,sort_keys=True),flush=True)
 except (BrokenPipeError,OSError): pass
def main():
 p=argparse.ArgumentParser();p.add_argument("--project-root",type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument("--dry-run",action="store_true");a=p.parse_args();root=a.project_root.resolve();c=load(root);source_exact(root,c);require(git(root,"status","--porcelain")=="","dirty tree");require(str(root)==c["execution"]["execution_worktree_root"],"execution root differs");require(socket.gethostname()==c["runtime"]["required_hostname"],"hostname differs");head=git(root,"rev-parse","HEAD");require(c["status"]=="preregistered_no_execution" and git(root,"show","-s","--format=%P",head).split()==[c["implementation_commit"]],"not prereg HEAD");state=Path(c["execution"]["state_root"]);pre=inspect_core_collisions(c)
 if a.dry_run: require(not state.exists(),"state exists");require(pre["accepted"],"collision");print(json.dumps({"status":"accepted_dry_run","case_count":3,"state_exists":False,"collisions":[]},sort_keys=True));return 0
 signal.signal(signal.SIGHUP,signal.SIG_IGN);require(os.getsid(0)==os.getpid() and all(not os.isatty(x) for x in (0,1,2)),"runner not detached");require(not state.exists(),"state exists");require(pre["accepted"],"collision")
 boot=state.with_name(state.name+f".bootstrap.{os.getpid()}");session={"schema_version":1,"protocol_revision":c["protocol_revision"],"runner_commit":head,"config_sha256":sha_path(root/CONFIG_REL),"case_ids":[x["experiment_id"] for x in c["formal_cases"]],"detached":{"pid":os.getpid(),"sid":os.getsid(0),"parent_pid":os.getppid(),"sighup_ignored":True,"no_tty":True},"resource_preflight":pre,"started_unix_ns":time.time_ns()}
 try: boot.mkdir(parents=True);(boot/"runs").mkdir();atomic_write(boot/"session.json",canonical(session));os.rename(boot,state)
 except BaseException:
  shutil.rmtree(boot,ignore_errors=True);raise
 session_sha=sha_path(state/"session.json");accepted=[];attempts={};returns={};results={};inventories={};e=env(c);total=3
 def failure(index,row,run,rc,reason):
  run.mkdir(exist_ok=True)
  if not (run/"attempt.json").exists(): atomic_write(run/"attempt.json",canonical({"schema_version":1,"status":"formal_started","experiment_id":row["experiment_id"],"attempt_number":1,"started_unix_ns":time.time_ns()}))
  attempts[row["experiment_id"]]=sha_path(run/"attempt.json")
  for name,data in (("command.json",canonical({"argv":[],"cwd":str(root),"environment":e})),("resource_preflight.json",canonical({"status":"failed","error":"runner_exception"})),("run.stdout",b""),("run.stderr",reason.encode())):
   if not (run/name).exists(): atomic_write(run/name,data)
  if not (run/"runner_return.json").exists(): atomic_write(run/"runner_return.json",canonical({"schema_version":1,"experiment_id":row["experiment_id"],"return_code":rc,"reason":reason}))
  returns[row["experiment_id"]]=sha_path(run/"runner_return.json");inventories[row["experiment_id"]]={p.name:{"sha256":sha_path(p),"size_bytes":p.stat().st_size} for p in sorted(run.iterdir()) if p.is_file() and not p.is_symlink()};attempted=[x["experiment_id"] for x in c["formal_cases"][:index]];unattempted=[x["experiment_id"] for x in c["formal_cases"][index:]]
  payload={"schema_version":1,"status":"failed_no_retry","experiment_id":row["experiment_id"],"return_code":rc,"reason":reason,"attempted_ids":attempted,"accepted_ids":accepted,"unattempted_ids":unattempted};atomic_write(state/"failure.json",canonical(payload));terminal={"schema_version":1,"status":"failed_no_retry","attempted":index,"accepted":len(accepted),"failed":1,"missing":0,"skipped":total-index,"retried":0,"runner_return_code":rc,"attempted_ids":attempted,"accepted_ids":accepted,"unattempted_ids":unattempted,"attempt_sha256":attempts,"runner_return_sha256":returns,"result_sha256":results,"run_inventory":inventories,"session_sha256":session_sha,"failure_sha256":sha_path(state/"failure.json"),"finished_unix_ns":time.time_ns()};atomic_write(state/"terminal.json",canonical(terminal));return rc
 cache=Path(c["execution"]["pseudo_cache"]);pseudo=cache/c["pseudodojo"]["materials"]["al"]["basename"];require(sha_path(pseudo)==c["pseudodojo"]["materials"]["al"]["sha256"],"pseudo differs")
 for index,row in enumerate(c["formal_cases"],1):
  run=state/"runs"/f"{index:02d}_{row['experiment_id']}"
  try:
   run.mkdir();atomic_write(run/"attempt.json",canonical({"schema_version":1,"status":"formal_started","experiment_id":row["experiment_id"],"attempt_number":1,"started_unix_ns":time.time_ns()}));attempts[row["experiment_id"]]=sha_path(run/"attempt.json");inp=root/c["execution"]["input_root"]/row["experiment_id"]
   for name in ("INPUT","STRU","KPT"): shutil.copyfile(inp/name,run/name)
   shutil.copyfile(inp/"metadata.json",run/"input_metadata.json");shutil.copyfile(pseudo,run/pseudo.name);metadata=json.loads((inp/"metadata.json").read_text());metadata.update({"runner_commit":head,"hostname":socket.gethostname(),"started_unix_ns":time.time_ns(),"runtime":c["runtime"],"pseudo_runtime_identity":{"basename":pseudo.name,"sha256":sha_path(pseudo)}});atomic_write(run/"metadata.json",canonical(metadata));affinity=run/"affinity";affinity.mkdir()
   wrapper=root/"scripts/s3_g3_ksdft_reference_rank_wrapper_r1.py";cmd=[c["runtime"]["mpi"],"--map-by",c["runtime"]["map_by"],"--bind-to","core","--report-bindings","-np",str(c["runtime"]["rank_count"]),"/usr/bin/python3",str(wrapper),"--binary",c["runtime"]["binary"],"--evidence-dir",str(affinity),"--expected-cores",','.join(str(x) for x in c["runtime"]["physical_core_ids"])];atomic_write(run/"command.json",canonical({"argv":cmd,"cwd":str(run),"environment":e}));case_pre=inspect_core_collisions(c);require(case_pre["accepted"],"case collision");atomic_write(run/"resource_preflight.json",canonical(case_pre));start=time.monotonic()
   with (run/"run.stdout").open("xb") as out,(run/"run.stderr").open("xb") as err: proc=subprocess.run(cmd,cwd=run,env=e,stdin=subprocess.DEVNULL,stdout=out,stderr=err,timeout=c["runtime"]["per_run_timeout_seconds"])
   atomic_write(run/"runner_return.json",canonical({"schema_version":1,"experiment_id":row["experiment_id"],"return_code":proc.returncode,"duration_seconds":time.monotonic()-start}));returns[row["experiment_id"]]=sha_path(run/"runner_return.json");require(proc.returncode==0,"solver nonzero");result=parse_run(run,c);atomic_write(run/"result.json",canonical(result));accepted.append(row["experiment_id"]);results[row["experiment_id"]]=sha_path(run/"result.json");inventories[row["experiment_id"]]={p.relative_to(run).as_posix():{"sha256":sha_path(p),"size_bytes":p.stat().st_size} for p in sorted(run.rglob('*')) if p.is_file() and not p.is_symlink()};progress({"completed":index,"total":total,"experiment_id":row["experiment_id"],"energy_ev_per_atom":result["thermodynamic_labels_ev_per_atom"]["E_ec"]})
  except BaseException as exc:
   if not run.exists(): run.mkdir()
   if not (run/"run.stderr").exists(): atomic_write(run/"run.stderr",traceback.format_exc().encode())
   return failure(index,row,run,70,f"runner_exception:{type(exc).__name__}:{exc}")
 terminal={"schema_version":1,"status":"accepted","attempted":3,"accepted":3,"failed":0,"missing":0,"skipped":0,"retried":0,"runner_return_code":0,"attempted_ids":[x["experiment_id"] for x in c["formal_cases"]],"accepted_ids":accepted,"unattempted_ids":[],"attempt_sha256":attempts,"runner_return_sha256":returns,"result_sha256":results,"run_inventory":inventories,"session_sha256":session_sha,"finished_unix_ns":time.time_ns()};atomic_write(state/"terminal.json",canonical(terminal));return 0
if __name__=="__main__":raise SystemExit(main())
