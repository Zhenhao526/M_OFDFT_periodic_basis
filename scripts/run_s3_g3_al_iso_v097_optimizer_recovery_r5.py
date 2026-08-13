#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,os,signal,subprocess,time,traceback
from pathlib import Path
import numpy as np
from s3_g3_al_iso_v097_optimizer_recovery_common_r5 import CONFIG_REL,canonical,git,load,replay_result,require,resource_preflight,sha_bytes,sha_path,source_exact,validate_config,validate_runtime,write_exclusive

def best_effort_progress(payload,printer=print):
 try: printer(json.dumps(payload,sort_keys=True),flush=True)
 except (BrokenPipeError,OSError): return False
 return True
def main():
 p=argparse.ArgumentParser();p.add_argument("--project-root",type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument("--dry-run",action="store_true");a=p.parse_args();root=a.project_root.resolve();c=load(root);validate_config(root,c);require(str(root)==c["execution"]["execution_worktree_root"],"execution root differs");source_exact(root,c);require(git(root,"status","--porcelain")=="","dirty");require(c["status"]=="preregistered_no_execution","not preregistered");head=git(root,"rev-parse","HEAD");require(git(root,"show","-s","--format=%P",head).split()==[c["implementation_commit"]],"not prereg HEAD");state=Path(c["execution"]["state_root"]);total=len(c["formal_cases"])
 if a.dry_run: require(not state.exists(),"state exists");print(json.dumps({"status":"accepted_dry_run","case_count":total,"state_exists":False},sort_keys=True));return 0
 signal.signal(signal.SIGHUP,signal.SIG_IGN);require(os.getsid(0)==os.getpid() and all(not os.isatty(fd) for fd in (0,1,2)),"runner not detached");require(not state.exists(),"state exists");runtime=validate_runtime(root,c);env=environment(root,c);initial=resource_preflight(c);bootstrap=state.with_name(state.name+f".bootstrap.{os.getpid()}");session={"schema_version":1,"protocol_revision":c["protocol_revision"],"runner_commit":head,"config_sha256":sha_path(root/CONFIG_REL),"case_ids":[x["experiment_id"] for x in c["formal_cases"]],"resource_preflight":initial,"detached":{"pid":os.getpid(),"sid":os.getsid(0),"parent_pid":os.getppid(),"sighup_ignored":signal.getsignal(signal.SIGHUP)==signal.SIG_IGN,"no_tty":all(not os.isatty(fd) for fd in (0,1,2))},"started_unix_ns":time.time_ns()}
 try: bootstrap.mkdir(parents=True,exist_ok=False);(bootstrap/"runs").mkdir();write_exclusive(bootstrap/"session.json",canonical(session));os.rename(bootstrap,state)
 except BaseException:
  (bootstrap/"session.json").unlink(missing_ok=True)
  if (bootstrap/"runs").exists():(bootstrap/"runs").rmdir()
  if bootstrap.exists():bootstrap.rmdir()
  raise
 session_sha=sha_path(state/"session.json");accepted=[];result_sha={};returns={};attempts={};inventories={}
 def freeze(index,row,run,rc,reason,worker_rc=None):
  run.mkdir(exist_ok=True)
  if not (run/"attempt.json").exists():write_exclusive(run/"attempt.json",canonical({"schema_version":1,"status":"formal_started","experiment_id":row["experiment_id"],"attempt_number":1,"started_unix_ns":time.time_ns()}))
  attempts[row["experiment_id"]]=sha_path(run/"attempt.json")
  if not (run/"command.json").exists():write_exclusive(run/"command.json",canonical({"argv":command(root,c,row,run),"cwd":str(root),"environment":env}))
  if not (run/"resource_preflight.json").exists():write_exclusive(run/"resource_preflight.json",canonical({"status":"failed","error":"runner_exception"}))
  for name,data in (("stdout.txt",b""),("stderr.txt",reason.encode())):
   if not (run/name).exists():write_exclusive(run/name,data)
  if not (run/"worker_return.json").exists():write_exclusive(run/"worker_return.json",canonical({"schema_version":1,"experiment_id":row["experiment_id"],"return_code":worker_rc}))
  if not (run/"runner_return.json").exists():write_exclusive(run/"runner_return.json",canonical({"schema_version":1,"experiment_id":row["experiment_id"],"worker_return_code":worker_rc,"runner_return_code":rc,"reason":reason}))
  returns[row["experiment_id"]]=sha_path(run/"runner_return.json");inventories[row["experiment_id"]]=inventory(run);attempted=[x["experiment_id"] for x in c["formal_cases"][:index]];unattempted=[x["experiment_id"] for x in c["formal_cases"][index:]];failure={"schema_version":1,"status":"failed_no_retry","experiment_id":row["experiment_id"],"return_code":rc,"reason":reason,"attempted_ids":attempted,"accepted_ids":accepted,"unattempted_ids":unattempted,"attempt_sha256":attempts,"runner_return_sha256":returns};write_exclusive(state/"failure.json",canonical(failure));terminal={"schema_version":1,"status":"failed_no_retry","attempted":index,"accepted":len(accepted),"failed":1,"missing":0,"skipped":total-index,"retried":0,"runner_return_code":rc,"attempted_ids":attempted,"accepted_ids":accepted,"unattempted_ids":unattempted,"attempt_sha256":attempts,"runner_return_sha256":returns,"result_sha256":result_sha,"run_inventory":inventories,"session_sha256":session_sha,"failure_sha256":sha_path(state/"failure.json"),"finished_unix_ns":time.time_ns()};write_exclusive(state/"terminal.json",canonical(terminal));return rc
 for index,row in enumerate(c["formal_cases"],1):
  run=state/"runs"/f"{index:03d}_{row['experiment_id']}"
  worker_rc=None
  try:
   run.mkdir();write_exclusive(run/"attempt.json",canonical({"schema_version":1,"status":"formal_started","experiment_id":row["experiment_id"],"attempt_number":1,"started_unix_ns":time.time_ns()}));attempts[row["experiment_id"]]=sha_path(run/"attempt.json");cmd=command(root,c,row,run);write_exclusive(run/"command.json",canonical({"argv":cmd,"cwd":str(root),"environment":env}));write_exclusive(run/"resource_preflight.json",canonical(resource_preflight(c)));proc=subprocess.run(cmd,cwd=root,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE);worker_rc=proc.returncode;write_exclusive(run/"stdout.txt",proc.stdout.encode());write_exclusive(run/"stderr.txt",proc.stderr.encode());write_exclusive(run/"worker_return.json",canonical({"schema_version":1,"experiment_id":row["experiment_id"],"return_code":worker_rc}))
   if worker_rc:return freeze(index,row,run,worker_rc,"worker_nonzero_return",worker_rc)
   result=json.loads(proc.stdout.strip().splitlines()[-1]);require(result["experiment_id"]==row["experiment_id"] and result["status"] in {"accepted","completed_scientific_rejected"},"worker result differs");require(result["cpu_affinity"]==[c["runtime"]["logical_cpu"]] and result["runtime"]==runtime,"worker runtime differs");density=np.load(run/"density.npy",allow_pickle=False);require(sha_path(run/"density.npy")==result["density_sha256"],"worker density SHA differs");replay_result(root,c,row,result,density);result_bytes=canonical(result);write_exclusive(run/"result.json",result_bytes);write_exclusive(run/"runner_return.json",canonical({"schema_version":1,"experiment_id":row["experiment_id"],"worker_return_code":0,"runner_return_code":0}));returns[row["experiment_id"]]=sha_path(run/"runner_return.json");observed_inventory=inventory(run);result_sha[row["experiment_id"]]=sha_path(run/"result.json");inventories[row["experiment_id"]]=observed_inventory;accepted.append(row["experiment_id"]);best_effort_progress({"completed":index,"total":total,"experiment_id":row["experiment_id"],"scientific_status":result["status"],"wall_seconds":result["wall_seconds"]})
  except BaseException as exc:
   if not (run/"stderr.txt").exists():run.mkdir(exist_ok=True);write_exclusive(run/"stderr.txt",traceback.format_exc().encode())
   return freeze(index,row,run,70,f"runner_exception:{type(exc).__name__}",worker_rc)
 terminal={"schema_version":1,"status":"accepted","attempted":total,"accepted":total,"failed":0,"missing":0,"skipped":0,"retried":0,"runner_return_code":0,"attempted_ids":[x["experiment_id"] for x in c["formal_cases"]],"accepted_ids":accepted,"unattempted_ids":[],"attempt_sha256":attempts,"runner_return_sha256":returns,"result_sha256":result_sha,"run_inventory":inventories,"session_sha256":session_sha,"finished_unix_ns":time.time_ns()};write_exclusive(state/"terminal.json",canonical(terminal));return 0
def command(root,c,row,run):return ["/usr/bin/taskset","-c",str(c["runtime"]["logical_cpu"]),c["runtime"]["python"],"-s","-B",str(root/"scripts/run_s3_g3_al_iso_v097_optimizer_recovery_worker_r5.py"),"--project-root",str(root),"--experiment-id",row["experiment_id"],"--run-directory",str(run)]
def environment(root,c):return {**c["execution"]["child_environment"],"PYTHONPATH":str(root/"scripts")}
def inventory(run):
 out={}
 for p in sorted(run.iterdir()):
  require(not p.is_symlink() and p.is_file(),f"invalid run evidence item: {p}");out[p.name]={"sha256":sha_path(p),"size_bytes":p.stat().st_size}
 return out
if __name__=="__main__":raise SystemExit(main())
