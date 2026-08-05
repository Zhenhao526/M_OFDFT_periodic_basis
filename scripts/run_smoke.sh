#!/usr/bin/env bash
set -euo pipefail

M_OFDFT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
M_OFDFT_EXPERIMENT_ID=${1:-S0-20260805-001}
M_OFDFT_EXPERIMENT_ROOT="$M_OFDFT_ROOT/runs/$M_OFDFT_EXPERIMENT_ID"
M_OFDFT_TEMPLATE="$M_OFDFT_ROOT/tests/smoke/al_fcc_wt"
M_OFDFT_PSEUDO="$M_OFDFT_ROOT/assets/pseudo/al.gga.psp"
M_OFDFT_NPROCS=${M_OFDFT_NPROCS:-4}

source "$M_OFDFT_ROOT/environment/activate.sh"
M_OFDFT_ABACUS=${M_OFDFT_ABACUS:-/home/shenwei01/wt_melting_runtime_20260724/build-abacus-wt-cpu/source/abacus_pw_para}
M_OFDFT_MPIRUN=${M_OFDFT_MPIRUN:-$M_OFDFT_PREFIX/bin/mpirun}
M_OFDFT_GIT_COMMIT=$(git -C "$M_OFDFT_ROOT" rev-parse HEAD)
M_OFDFT_GIT_DIRTY=false
if [[ -n "$(git -C "$M_OFDFT_ROOT" status --porcelain)" ]]; then
    M_OFDFT_GIT_DIRTY=true
fi

if [[ -e "$M_OFDFT_EXPERIMENT_ROOT" ]]; then
    echo "Refusing to overwrite existing experiment: $M_OFDFT_EXPERIMENT_ROOT" >&2
    exit 2
fi
if [[ ! -x "$M_OFDFT_ABACUS" ]]; then
    echo "ABACUS binary is not executable: $M_OFDFT_ABACUS" >&2
    exit 2
fi
if [[ ! -x "$M_OFDFT_MPIRUN" ]]; then
    echo "MPI launcher is not executable: $M_OFDFT_MPIRUN" >&2
    exit 2
fi
if [[ ! -f "$M_OFDFT_PSEUDO" ]]; then
    echo "Pseudopotential is missing: $M_OFDFT_PSEUDO" >&2
    exit 2
fi
M_OFDFT_ABACUS_SHA256=$(sha256sum "$M_OFDFT_ABACUS" | awk '{print $1}')

mkdir -p "$M_OFDFT_EXPERIMENT_ROOT/repeat1" "$M_OFDFT_EXPERIMENT_ROOT/repeat2"

python3 - "$M_OFDFT_EXPERIMENT_ROOT/experiment_metadata.json" "$M_OFDFT_EXPERIMENT_ID" "$M_OFDFT_NPROCS" "$M_OFDFT_ABACUS" "$M_OFDFT_PREFIX" "$M_OFDFT_GIT_COMMIT" "$M_OFDFT_GIT_DIRTY" "$M_OFDFT_ABACUS_SHA256" <<'PY'
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

output = Path(sys.argv[1])
payload = {
    "experiment_id": sys.argv[2],
    "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "stage": "S0",
    "purpose": "fcc Al WT-OFDFT reproducibility smoke test",
    "natoms": 4,
    "repeats": 2,
    "mpi_ranks": int(sys.argv[3]),
    "abacus_version": "v3.11.0-beta.5",
    "abacus_path": sys.argv[4],
    "abacus_sha256": sys.argv[8],
    "code_commit": sys.argv[6],
    "worktree_dirty": sys.argv[7] == "true",
    "runtime_prefix": sys.argv[5],
    "kinetic_functional": "WT",
    "xc": "PBE",
    "pseudopotential": "al.gga.psp",
}
output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

for M_OFDFT_REPEAT in repeat1 repeat2; do
    M_OFDFT_RUN_DIR="$M_OFDFT_EXPERIMENT_ROOT/$M_OFDFT_REPEAT"
    cp "$M_OFDFT_TEMPLATE/INPUT" "$M_OFDFT_RUN_DIR/INPUT"
    cp "$M_OFDFT_TEMPLATE/STRU" "$M_OFDFT_RUN_DIR/STRU"
    cp "$M_OFDFT_TEMPLATE/KPT" "$M_OFDFT_RUN_DIR/KPT"
    cp "$M_OFDFT_PSEUDO" "$M_OFDFT_RUN_DIR/al.gga.psp"
    (
        cd "$M_OFDFT_RUN_DIR"
        /usr/bin/time -v "$M_OFDFT_MPIRUN" --bind-to core -np "$M_OFDFT_NPROCS" "$M_OFDFT_ABACUS" >run.stdout 2>resource_usage.txt
    )
done

python3 "$M_OFDFT_ROOT/scripts/check_smoke.py" "$M_OFDFT_EXPERIMENT_ROOT" --natoms 4 --tolerance-mev-per-atom 0.1
sha256sum "$M_OFDFT_EXPERIMENT_ROOT"/repeat1/INPUT "$M_OFDFT_EXPERIMENT_ROOT"/repeat1/STRU "$M_OFDFT_EXPERIMENT_ROOT"/repeat1/KPT "$M_OFDFT_EXPERIMENT_ROOT"/repeat1/al.gga.psp >"$M_OFDFT_EXPERIMENT_ROOT/INPUT_SHA256SUMS"
