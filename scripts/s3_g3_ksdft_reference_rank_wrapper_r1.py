#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,os,socket
from pathlib import Path
def main():
 p=argparse.ArgumentParser();p.add_argument("--binary",required=True);p.add_argument("--evidence-dir",type=Path,required=True);p.add_argument("--expected-cores",required=True);a=p.parse_args();rank=int(os.environ["OMPI_COMM_WORLD_RANK"]);local=int(os.environ["OMPI_COMM_WORLD_LOCAL_RANK"]);expected=[int(x) for x in a.expected_cores.split(',')]; cpus=sorted(os.sched_getaffinity(0));top=[]
 for cpu in cpus:
  r=Path(f"/sys/devices/system/cpu/cpu{cpu}/topology");top.append({"logical_cpu":cpu,"core_id":int((r/"core_id").read_text()),"socket_id":int((r/"physical_package_id").read_text())})
 cores=sorted({x["core_id"] for x in top})
 if cores!=[expected[rank]]: raise ValueError(f"rank {rank} binding {cores} differs")
 payload={"schema_version":1,"rank":rank,"local_rank":local,"hostname":socket.gethostname(),"pid_before_exec":os.getpid(),"logical_cpu_affinity":cpus,"topology":top,"physical_core_ids":cores,"expected_physical_core_id":expected[rank],"accepted":True};a.evidence_dir.mkdir(parents=True,exist_ok=True);path=a.evidence_dir/f"rank_{rank:03d}.json";fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,"w") as f: json.dump(payload,f,sort_keys=True,indent=2);f.write("\n");f.flush();os.fsync(f.fileno())
 os.execve(a.binary,[a.binary],os.environ.copy())
if __name__=="__main__": raise SystemExit(main())
