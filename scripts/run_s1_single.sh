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
runtime_relocation_mode=${M_OFDFT_RUNTIME_RELOCATION_MODE:-0}
if [[ "$runtime_relocation_mode" != 0 && "$runtime_relocation_mode" != 1 ]]; then
    echo "M_OFDFT_RUNTIME_RELOCATION_MODE must be 0 or 1" >&2
    exit 2
fi

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

mkdir -p "$run_directory"
if [[ "$runtime_relocation_mode" == 1 ]]; then
    expected_home="$run_directory/runtime_home"
    if [[ "$HOME" != "$expected_home" ]]; then
        echo "Runtime-relocation HOME must be $expected_home" >&2
        exit 2
    fi
    mkdir -m 700 "$HOME"
    printf '%s\n' 'Controlled empty HOME for S1 runtime-relocation replay.' \
        > "$HOME/CONTROLLED_HOME.txt"
fi
cp "$input_directory/INPUT" "$input_directory/STRU" "$input_directory/KPT" "$run_directory/"
cp "$input_directory/metadata.json" "$run_directory/input_metadata.json"
cp "$project_root/assets/pseudo/$pseudopotential" "$run_directory/$pseudopotential"
sed -i 's|^pseudo_dir .*|pseudo_dir .|' "$run_directory/INPUT"
(
    cd "$run_directory"
    sha256sum INPUT STRU KPT "$pseudopotential" > INPUT_SHA256SUMS
)

git_commit=$(git -C "$project_root" rev-parse HEAD)
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
"$python_tool_path" - "$run_directory" "$invocation_exit_code" "$parser_exit_code" \
    "$runtime_relocation_mode" <<'PY'
import json
import os
import sys
from pathlib import Path

run = Path(sys.argv[1])
invocation_exit_code = int(sys.argv[2])
parser_exit_code = int(sys.argv[3])
runtime_relocation_mode = sys.argv[4] == "1"
result_path = run / "result.json"
audit_path = run / "mpi_runtime_audit" / "audit.json"
host_status_path = run / "mpi_runtime_audit" / "namespace" / "host_status.json"
counterpart_path = run / "mpi_runtime_audit" / "counterpart_audit.json"
result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else None
audit = json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.is_file() else None
host_status = json.loads(host_status_path.read_text(encoding="utf-8")) if host_status_path.is_file() else None
counterpart = json.loads(counterpart_path.read_text(encoding="utf-8")) if counterpart_path.is_file() else None
launcher_exit_code = (
    audit.get("launcher_exit_code") if isinstance(audit, dict) else invocation_exit_code
)
workflow_exit_code = invocation_exit_code if invocation_exit_code != 0 else parser_exit_code
accepted = (
    invocation_exit_code == 0
    and parser_exit_code == 0
    and isinstance(result, dict)
    and result.get("converged") is True
)
if runtime_relocation_mode:
    accepted = (
        accepted
        and isinstance(audit, dict)
        and audit.get("status") == "accepted"
        and isinstance(host_status, dict)
        and host_status.get("status") == "accepted"
        and isinstance(counterpart, dict)
        and counterpart.get("status") == "accepted"
    )
payload = {
    "schema_version": 2,
    "status": "accepted" if accepted else "rejected",
    "runtime_relocation_mode": runtime_relocation_mode,
    "workflow_exit_code": workflow_exit_code,
    "invocation_exit_code": invocation_exit_code,
    "launcher_exit_code": launcher_exit_code,
    "parser_exit_code": parser_exit_code,
    "result_json_present": result_path.is_file(),
    "result_converged": result.get("converged") if isinstance(result, dict) else None,
    "runtime_audit_json_present": audit_path.is_file(),
    "runtime_audit_status": audit.get("status") if isinstance(audit, dict) else None,
    "namespace_host_status": host_status.get("status") if isinstance(host_status, dict) else None,
    "counterpart_audit_status": counterpart.get("status") if isinstance(counterpart, dict) else None,
}
output = run / "run_status.json"
temporary = output.with_name(f".{output.name}.tmp-{os.getpid()}")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, output)
PY
if [[ $invocation_exit_code -ne 0 ]]; then
    exit "$invocation_exit_code"
fi
exit "$parser_exit_code"
