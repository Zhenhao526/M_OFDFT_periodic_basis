#!/usr/bin/env python3
import argparse,json
from pathlib import Path
from s2_g2c_performance_common_r1 import case_rows,git,load,render,require
def main():
 p=argparse.ArgumentParser(); p.add_argument("--project-root",type=Path,default=Path(__file__).resolve().parents[1]); a=p.parse_args(); r=a.project_root.resolve(); c=load(r); state=Path(c["execution"]["state_root"]); require(json.loads((state/"terminal.json").read_text())["status"]=="accepted","terminal differs"); raw=[]
 for index,row in enumerate(case_rows(c),1): raw.append(json.loads((state/"runs"/f"{index:02d}_{row['case_id']}"/"result.json").read_text()))
 out=render(c,raw); target=r/c["execution"]["analysis_root"]; require(not target.exists(),"analysis exists"); target.mkdir(parents=True); [(target/n).write_bytes(out[n]) for n in c["output_files"]]; print(json.dumps({"status":json.loads(out["summary.json"])["status"],"raw_cases":len(raw),"output_files":len(out)},sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
