#!/usr/bin/env python3
from __future__ import annotations
import argparse,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"scripts"))
from parse_s1_g1_three_layer_continuation_r2 import parse_run as base_parse
from s3_g3_ksdft_reference_common_r1 import canonical,load,require
def compatibility(c):
    return {**c,"pseudodojo":{"materials":c["pseudodojo"]["materials"]},"acceptance":c["acceptance"],"runtime":c["runtime"],"materials":c["materials"]}
def parse_run(run_dir,c):
    out=base_parse(run_dir,compatibility(c)); require(out["protocol_revision"]==c["protocol_revision"],"parsed protocol differs"); return out
def main():
 p=argparse.ArgumentParser();p.add_argument("run_dir",type=Path);p.add_argument("--project-root",type=Path,default=ROOT);a=p.parse_args();print(canonical(parse_run(a.run_dir,load(a.project_root))).decode(),end="");return 0
if __name__=="__main__": raise SystemExit(main())
