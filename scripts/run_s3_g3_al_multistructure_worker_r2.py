#!/usr/bin/env python3
from __future__ import annotations
import io,json,sys,contextlib
from pathlib import Path
import numpy as np
import run_s3_g3_wt_coefficient_worker_r1 as worker
import s3_g3_al_multistructure_common_r2 as common

def _arg(name):
 i=sys.argv.index(name);return sys.argv[i+1]
def main():
 root=Path(_arg("--project-root")).resolve();experiment_id=_arg("--experiment-id");c=common.load(root);common.validate_config(root,c);row=next(x for x in c["formal_cases"] if x["experiment_id"]==experiment_id);cell=common.cell_for_row(root,c,row)
 worker.load=common.load;worker.validate_config=lambda x:common.validate_config(root,x);worker.source_exact=common.source_exact;worker.validate_runtime=common.validate_runtime;worker.load_source_density=lambda _root,_c:(cell,np.zeros((24,24,24),dtype=np.float64));worker.build_basis=common.build_basis;worker.build_evaluator=common.build_evaluator;worker.build_registered_initial=common.build_registered_initial
 stream=io.StringIO()
 with contextlib.redirect_stdout(stream): rc=worker.main()
 lines=stream.getvalue().splitlines();common.require(lines,"worker emitted no result");result=json.loads(lines[-1]);result.update({"structure_id":row["structure_id"],"geometry_kind":row["geometry_kind"],"geometry_parameter":row["geometry_parameter"]})
 for line in lines[:-1]: print(line)
 print(json.dumps(result,sort_keys=True));return rc
if __name__=="__main__": raise SystemExit(main())
