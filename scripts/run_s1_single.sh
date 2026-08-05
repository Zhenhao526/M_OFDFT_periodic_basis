#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "Usage: $0 EXPERIMENT_ID INPUT_DIRECTORY" >&2
    exit 2
fi

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
experiment_id=$1
input_directory=$(realpath "$2")
runtime_relocation_mode=${M_OFDFT_RUNTIME_RELOCATION_MODE:-0}
runtime_relocation_smoke_mode=${M_OFDFT_RUNTIME_RELOCATION_SMOKE_MODE:-0}
if [[ "$runtime_relocation_mode" != 0 && "$runtime_relocation_mode" != 1 ]]; then
    echo "M_OFDFT_RUNTIME_RELOCATION_MODE must be 0 or 1" >&2
    exit 2
fi
if [[ "$runtime_relocation_smoke_mode" != 0 && "$runtime_relocation_smoke_mode" != 1 ]]; then
    echo "M_OFDFT_RUNTIME_RELOCATION_SMOKE_MODE must be 0 or 1" >&2
    exit 2
fi
if [[ "$runtime_relocation_smoke_mode" == 1 && "$runtime_relocation_mode" != 1 ]]; then
    echo "Runtime-relocation smoke requires M_OFDFT_RUNTIME_RELOCATION_MODE=1" >&2
    exit 2
fi

if [[ "$runtime_relocation_smoke_mode" == 1 ]]; then
    expected_smoke_id=S1-RUNTIME-SMOKE-20260805-074
    expected_smoke_directory="$project_root/analysis/s1/runtime_relocation_smoke_20260805/run"
    if [[ "$experiment_id" != "$expected_smoke_id" ]]; then
        echo "Runtime smoke ID must be $expected_smoke_id" >&2
        exit 2
    fi
    run_directory=${M_OFDFT_RUN_DIRECTORY_OVERRIDE:-}
    if [[ "$run_directory" != "$expected_smoke_directory" ]]; then
        echo "Runtime smoke directory must be $expected_smoke_directory" >&2
        exit 2
    fi
elif [[ "$experiment_id" =~ ^S1-[0-9]{8}-[0-9]{3}$ ]]; then
    run_directory="$project_root/runs/$experiment_id"
else
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
mpirun_path=$mpirun
mpirun_realpath=$(realpath "$mpirun_path")
mpirun_script=${M_OFDFT_MPIRUN_SCRIPT:-}
if [[ -n "$mpirun_script" ]]; then
    mpirun_script_path=$mpirun_script
    mpirun_script_realpath=$(realpath "$mpirun_script_path")
    mpirun_command=("$mpirun_path" "$mpirun_script_path")
    mpirun_invocation_path=$mpirun_script_path
else
    mpirun_command=("$mpirun_path")
    mpirun_invocation_path=$mpirun_path
fi
provenance_mpirun=${M_OFDFT_PROVENANCE_MPIRUN:-$mpirun}
if [[ "$provenance_mpirun" != */* ]]; then
    provenance_mpirun=$(command -v "$provenance_mpirun")
fi
provenance_mpirun_path=$provenance_mpirun
provenance_mpirun_realpath=$(realpath "$provenance_mpirun_path")
python_tool=${M_OFDFT_PYTHON_TOOL:-$(command -v python3)}
python_tool_path=$python_tool
python_tool_realpath=$(realpath "$python_tool_path")
abacus_path=$abacus
abacus_realpath=$(realpath "$abacus_path")
mpi_ranks=${M_OFDFT_NPROCS:-4}
pseudopotential=$("$python_tool_path" -c 'import json,sys; print(json.load(open(sys.argv[1]))["pseudopotential"])' "$input_directory/metadata.json")
git_commit=$(git -C "$project_root" rev-parse HEAD)
status_writer="$project_root/scripts/write_s1_runtime_relocation_status.py"
setup_completed=false
failure_stage=run_directory_created
invocation_exit_code=97
parser_exit_code=97
runtime_mode_text=false
if [[ "$runtime_relocation_mode" == 1 ]]; then
    runtime_mode_text=true
fi

mkdir -p "$run_directory"
finalize_created_attempt() {
    local shell_exit=$?
    trap - EXIT
    if [[ "$runtime_relocation_mode" != 1 ]]; then
        exit "$shell_exit"
    fi
    if [[ $shell_exit -ne 0 && $invocation_exit_code -eq 97 ]]; then
        invocation_exit_code=$shell_exit
    fi
    local workflow_exit=$invocation_exit_code
    if [[ $workflow_exit -eq 0 ]]; then
        workflow_exit=$parser_exit_code
    fi
    "$python_tool_path" "$status_writer" "$run_directory" \
        --experiment-id "$experiment_id" \
        --code-commit "$git_commit" \
        --workflow-exit "$workflow_exit" \
        --invocation-exit "$invocation_exit_code" \
        --parser-exit "$parser_exit_code" \
        --core-validation-exit 97 \
        --setup-completed "$setup_completed" \
        --runtime-relocation-mode "$runtime_mode_text" \
        --failure-stage "${failure_stage:-component_rejected}" >/dev/null 2>&1 || true
    exit "$shell_exit"
}
trap finalize_created_attempt EXIT
if [[ "$runtime_relocation_mode" == 1 ]]; then
    failure_stage=controlled_home_setup
    expected_home="$run_directory/runtime_home"
    if [[ "$HOME" != "$expected_home" ]]; then
        echo "Runtime-relocation HOME must be $expected_home" >&2
        exit 2
    fi
    mkdir -m 700 "$HOME"
    printf '%s\n' 'Controlled empty HOME for S1 runtime-relocation replay.' \
        > "$HOME/CONTROLLED_HOME.txt"
fi
failure_stage=input_archive_setup
cp "$input_directory/INPUT" "$input_directory/STRU" "$input_directory/KPT" "$run_directory/"
cp "$input_directory/metadata.json" "$run_directory/input_metadata.json"
cp "$project_root/assets/pseudo/$pseudopotential" "$run_directory/$pseudopotential"
sed -i 's|^pseudo_dir .*|pseudo_dir .|' "$run_directory/INPUT"
(
    cd "$run_directory"
    sha256sum INPUT STRU KPT "$pseudopotential" > INPUT_SHA256SUMS
)

failure_stage=experiment_metadata_setup
"$python_tool_path" - "$run_directory/experiment_metadata.json" "$experiment_id" "$git_commit" \
    "$abacus_path" "$provenance_mpirun_path" "$mpirun_invocation_path" "$mpirun_path" \
    "$mpi_ranks" "$OPAL_PREFIX" "$PRTE_PREFIX" "$PMIX_PREFIX" \
    "${UCX_MODULE_DIR:-}" <<'PY'
import datetime
import hashlib
import json
import os
import sys
from pathlib import Path

binary = Path(sys.argv[4])
mpirun = Path(sys.argv[5])
mpirun_invocation = Path(sys.argv[6])
mpirun_interpreter = Path(sys.argv[7])
payload = {
    "abacus_path": str(binary),
    "abacus_realpath": str(binary.resolve()),
    "abacus_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
    "code_commit": sys.argv[3],
    "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "experiment_id": sys.argv[2],
    "mpirun_path": str(mpirun),
    "mpirun_realpath": str(mpirun.resolve()),
    "mpirun_sha256": hashlib.sha256(mpirun.read_bytes()).hexdigest(),
    "mpirun_invocation_path": str(mpirun_invocation),
    "mpirun_invocation_sha256": hashlib.sha256(mpirun_invocation.read_bytes()).hexdigest(),
    "mpirun_invocation_interpreter_path": str(mpirun_interpreter),
    "mpirun_invocation_interpreter_realpath": str(mpirun_interpreter.resolve()),
    "mpirun_invocation_interpreter_sha256": hashlib.sha256(
        mpirun_interpreter.read_bytes()
    ).hexdigest(),
    "mpi_ranks": int(sys.argv[8]),
    "OPAL_PREFIX": sys.argv[9],
    "PRTE_PREFIX": sys.argv[10],
    "PMIX_PREFIX": sys.argv[11],
    "UCX_MODULE_DIR": sys.argv[12],
    "runtime_relocation_mode": os.environ.get("M_OFDFT_RUNTIME_RELOCATION_MODE") == "1",
    "runtime_environment": {
        key: os.environ.get(key)
        for key in (
            "PATH",
            "LD_LIBRARY_PATH",
            "LD_PRELOAD",
            "CMAKE_PREFIX_PATH",
            "MKLROOT",
            "HOME",
            "OMP_NUM_THREADS",
        )
    },
    "stage": "S1",
    "worktree_dirty": False,
}
Path(sys.argv[1]).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

setup_completed=true
failure_stage=runtime_invocation_or_parser

set +e
(
    cd "$run_directory"
    /usr/bin/time -v "${mpirun_command[@]}" --bind-to core -np "$mpi_ranks" "$abacus_path" \
        > run.stdout 2> resource_usage.txt
)
invocation_exit_code=$?
"$python_tool_path" "$project_root/scripts/parse_s1_single.py" "$run_directory"
parser_exit_code=$?
set -e
workflow_exit_code=$invocation_exit_code
if [[ $workflow_exit_code -eq 0 ]]; then
    workflow_exit_code=$parser_exit_code
fi
"$python_tool_path" "$status_writer" "$run_directory" \
    --experiment-id "$experiment_id" \
    --code-commit "$git_commit" \
    --workflow-exit "$workflow_exit_code" \
    --invocation-exit "$invocation_exit_code" \
    --parser-exit "$parser_exit_code" \
    --setup-completed true \
    --runtime-relocation-mode "$runtime_mode_text" \
    --failure-stage "$failure_stage" \
    --run-only
if [[ $invocation_exit_code -ne 0 ]]; then
    exit "$invocation_exit_code"
fi
exit "$parser_exit_code"
