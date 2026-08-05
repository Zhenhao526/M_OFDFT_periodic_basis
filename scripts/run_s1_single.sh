#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "Usage: $0 EXPERIMENT_ID INPUT_DIRECTORY" >&2
    exit 2
fi

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
experiment_id=$1
input_directory=$(realpath "$2")
run_directory="$project_root/runs/$experiment_id"

if [[ ! "$experiment_id" =~ ^S1-[0-9]{8}-[0-9]{3}$ ]]; then
    echo "Invalid S1 experiment ID: $experiment_id" >&2
    exit 2
fi
if [[ -e "$run_directory" ]]; then
    echo "Refusing to overwrite existing experiment: $run_directory" >&2
    exit 2
fi
for required in INPUT STRU KPT metadata.json; do
    if [[ ! -f "$input_directory/$required" ]]; then
        echo "Missing input file: $input_directory/$required" >&2
        exit 2
    fi
done
if [[ -n "$(git -C "$project_root" status --porcelain)" ]]; then
    echo "Refusing S1 run from a dirty worktree" >&2
    exit 2
fi

source "$project_root/environment/activate.sh"
abacus=${M_OFDFT_ABACUS:-/home/shenwei01/wt_melting_runtime_20260724/build-abacus-wt-cpu/source/abacus_pw_para}
mpirun=${M_OFDFT_MPIRUN:-$M_OFDFT_PREFIX/bin/mpirun}
if [[ "$mpirun" != */* ]]; then
    mpirun=$(command -v "$mpirun")
fi
mpirun=$(realpath "$mpirun")
mpi_ranks=${M_OFDFT_NPROCS:-4}
pseudopotential=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["pseudopotential"])' "$input_directory/metadata.json")

mkdir -p "$run_directory"
cp "$input_directory/INPUT" "$input_directory/STRU" "$input_directory/KPT" "$run_directory/"
cp "$input_directory/metadata.json" "$run_directory/input_metadata.json"
cp "$project_root/assets/pseudo/$pseudopotential" "$run_directory/$pseudopotential"
sed -i 's|^pseudo_dir .*|pseudo_dir .|' "$run_directory/INPUT"
(
    cd "$run_directory"
    sha256sum INPUT STRU KPT "$pseudopotential" > INPUT_SHA256SUMS
)

git_commit=$(git -C "$project_root" rev-parse HEAD)
python3 - "$run_directory/experiment_metadata.json" "$experiment_id" "$git_commit" \
    "$abacus" "$mpirun" "$mpi_ranks" "$OPAL_PREFIX" "$PRTE_PREFIX" "$PMIX_PREFIX" <<'PY'
import datetime
import hashlib
import json
import sys
from pathlib import Path

binary = Path(sys.argv[4])
mpirun = Path(sys.argv[5])
payload = {
    "abacus_path": str(binary),
    "abacus_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
    "code_commit": sys.argv[3],
    "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "experiment_id": sys.argv[2],
    "mpi_ranks": int(sys.argv[6]),
    "mpirun_path": str(mpirun),
    "mpirun_sha256": hashlib.sha256(mpirun.read_bytes()).hexdigest(),
    "OPAL_PREFIX": sys.argv[7],
    "PRTE_PREFIX": sys.argv[8],
    "PMIX_PREFIX": sys.argv[9],
    "stage": "S1",
    "worktree_dirty": False,
}
Path(sys.argv[1]).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

(
    cd "$run_directory"
    /usr/bin/time -v "$mpirun" --bind-to core -np "$mpi_ranks" "$abacus" > run.stdout 2> resource_usage.txt
)
python3 "$project_root/scripts/parse_s1_single.py" "$run_directory"
