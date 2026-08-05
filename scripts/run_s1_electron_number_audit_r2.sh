#!/usr/bin/env bash
set -euo pipefail

if [[ $# -gt 2 ]]; then
    echo "Usage: $0 [MANIFEST_TSV [CONFIG_JSON]]" >&2
    exit 2
fi

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
manifest=${1:-$project_root/config/S1_electron_number_audit_r2_manifest.tsv}
config=${2:-$project_root/config/S1_electron_number_audit_r2.json}
manifest=$(realpath "$manifest")
config=$(realpath "$config")
cd "$project_root"

if [[ -n $(git status --porcelain) ]]; then
    echo "Refusing R2 electron-number audit from a dirty worktree" >&2
    exit 2
fi

bootstrap_python=$(command -v python3)
mapfile -d '' runtime_values < <("$bootstrap_python" - "$config" <<'PY'
import json
import sys

config = json.load(open(sys.argv[1], encoding="utf-8"))
runtime = config["runtime"]
replay = runtime["replay"]
reference = runtime["reference"]
tools = runtime["tools"]
wrappers = runtime["wrappers"]
execution = config["execution"]
values = [
    runtime["recovery_root"], runtime["recovery_prefix"],
    runtime["old_root"], runtime["old_prefix"],
    replay["abacus"]["path"], replay["abacus"]["realpath"], replay["abacus"]["sha256"],
    replay["mpirun"]["path"], replay["mpirun"]["realpath"], replay["mpirun"]["sha256"],
    replay["launcher"]["path"], replay["launcher"]["realpath"], replay["launcher"]["sha256"],
    reference["abacus"]["path"], reference["abacus"]["realpath"], reference["abacus"]["sha256"],
    reference["mpirun"]["path"], reference["mpirun"]["realpath"], reference["mpirun"]["sha256"],
    reference["launcher"]["path"], reference["launcher"]["realpath"], reference["launcher"]["sha256"],
    str(config["runtime_audit"]["rank_count"]),
    str(runtime["namespace"]["host_uid"]), str(runtime["namespace"]["host_gid"]),
]
for name in ("python", "strace", "unshare", "mount", "bash"):
    tool = tools[name]
    values.extend([
        tool["path"], tool["realpath"], tool["sha256"],
        tool["version_first_line"], tool["version_output_sha256"],
    ])
for name in ("namespace_launcher", "namespace_payload", "audit_launcher", "rank_wrapper"):
    values.extend([wrappers[name]["path"], wrappers[name]["sha256"]])
pilots = execution["pilot_audit_ids"]
if len(pilots) != 2:
    raise SystemExit("R2 execution must register exactly two pilots")
values.extend(pilots)
for value in values:
    if not isinstance(value, str) or "\0" in value:
        raise SystemExit("runtime config contains a non-string or NUL value")
    sys.stdout.write(value + "\0")
PY
)

if [[ ${#runtime_values[@]} -ne 60 ]]; then
    echo "Unexpected R2 runtime field count: ${#runtime_values[@]}" >&2
    exit 2
fi
recovery_root=${runtime_values[0]}
recovery_prefix=${runtime_values[1]}
old_root=${runtime_values[2]}
old_prefix=${runtime_values[3]}
abacus_path=${runtime_values[4]}
abacus_realpath=${runtime_values[5]}
abacus_sha256=${runtime_values[6]}
mpirun_path=${runtime_values[7]}
mpirun_realpath=${runtime_values[8]}
mpirun_sha256=${runtime_values[9]}
launcher_path=${runtime_values[10]}
launcher_realpath=${runtime_values[11]}
launcher_sha256=${runtime_values[12]}
reference_abacus_path=${runtime_values[13]}
reference_abacus_realpath=${runtime_values[14]}
reference_abacus_sha256=${runtime_values[15]}
reference_mpirun_path=${runtime_values[16]}
reference_mpirun_realpath=${runtime_values[17]}
reference_mpirun_sha256=${runtime_values[18]}
reference_launcher_path=${runtime_values[19]}
reference_launcher_realpath=${runtime_values[20]}
reference_launcher_sha256=${runtime_values[21]}
rank_count=${runtime_values[22]}
host_uid=${runtime_values[23]}
host_gid=${runtime_values[24]}

offset=25
declare -A tool_path tool_realpath tool_sha tool_version_line tool_version_sha
for name in python strace unshare mount bash; do
    tool_path[$name]=${runtime_values[$offset]}
    tool_realpath[$name]=${runtime_values[$((offset + 1))]}
    tool_sha[$name]=${runtime_values[$((offset + 2))]}
    tool_version_line[$name]=${runtime_values[$((offset + 3))]}
    tool_version_sha[$name]=${runtime_values[$((offset + 4))]}
    offset=$((offset + 5))
done
declare -A wrapper_path wrapper_sha
for name in namespace_launcher namespace_payload audit_launcher rank_wrapper; do
    wrapper_path[$name]=${runtime_values[$offset]}
    wrapper_sha[$name]=${runtime_values[$((offset + 1))]}
    offset=$((offset + 2))
done
pilot_one=${runtime_values[58]}
pilot_two=${runtime_values[59]}
python_tool=${tool_path[python]}
validator=scripts/validate_s1_electron_number_audit_r2.py

"$python_tool" "$validator" "$manifest" --config "$config" --require-committed >/dev/null

write_replay_status() {
    local run_directory=$1
    local workflow_exit=$2
    local core_validation_exit=$3
    "$python_tool" - "$project_root" "$run_directory" "$workflow_exit" "$core_validation_exit" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

project_root = Path(sys.argv[1])
run = Path(sys.argv[2])
workflow_exit = int(sys.argv[3])
core_validation_exit = int(sys.argv[4])
sys.path.insert(0, str(project_root / "scripts"))
from write_s1_runtime_relocation_status import write_status

try:
    run_status = json.loads((run / "run_status.json").read_text(encoding="utf-8"))
except (FileNotFoundError, json.JSONDecodeError):
    run_status = {}
invocation_exit = run_status.get("invocation_exit_code")
parser_exit = run_status.get("parser_exit_code")
if not isinstance(invocation_exit, int) or not isinstance(parser_exit, int):
    invocation_exit = workflow_exit if workflow_exit != 0 else 97
    parser_exit = 97
try:
    code_commit = json.loads(
        (run / "experiment_metadata.json").read_text(encoding="utf-8")
    )["code_commit"]
except (FileNotFoundError, KeyError, json.JSONDecodeError):
    code_commit = subprocess.check_output(
        ["git", "-C", str(project_root), "rev-parse", "HEAD"], text=True
    ).strip()
write_status(
    run,
    experiment_id=run.name,
    code_commit=code_commit,
    workflow_exit=workflow_exit,
    invocation_exit=invocation_exit,
    parser_exit=parser_exit,
    core_validation_exit=core_validation_exit,
    setup_completed=run_status.get("setup_completed") is True,
    failure_stage=run_status.get("failure_stage") or "electron_number_r2_outer_validation",
)
PY
}

assert_commit_scope() {
    local expected_prefix=$1
    if [[ -n $(git status --porcelain) ]]; then
        echo "Post-commit worktree is not clean" >&2
        return 1
    fi
    local changed
    changed=$(git diff-tree --no-commit-id --name-only -r HEAD)
    if [[ -z "$changed" ]]; then
        echo "Commit contains no paths" >&2
        return 1
    fi
    while IFS= read -r path; do
        if [[ "$path" != "$expected_prefix"* ]]; then
            echo "Commit contains path outside $expected_prefix: $path" >&2
            return 1
        fi
    done <<<"$changed"
}

assert_archive_commit_scope() {
    local experiment_id=$1
    local archive_prefix=$2
    if [[ -n $(git status --porcelain) ]]; then
        echo "Post-archive worktree is not clean" >&2
        return 1
    fi
    local changed
    changed=$(git diff-tree --no-commit-id --name-only -r HEAD)
    if [[ -z "$changed" ]]; then
        echo "Archive commit contains no paths" >&2
        return 1
    fi
    while IFS= read -r path; do
        if [[ "$path" != "runs/$experiment_id/"* && "$path" != "$archive_prefix"* ]]; then
            echo "Archive commit contains unrelated path: $path" >&2
            return 1
        fi
    done <<<"$changed"
}

archive_failed_attempt() {
    local experiment_id=$1
    local run_directory="$project_root/runs/$experiment_id"
    "$python_tool" "$validator" "$manifest" --config "$config" \
        --require-committed --check-failure-run "$experiment_id" >/dev/null
    local failure_commit
    failure_commit=$(git log --no-renames --diff-filter=A --format=%H -n 1 -- \
        "runs/$experiment_id/replay_status.json")
    if [[ ! "$failure_commit" =~ ^[0-9a-f]{40}$ ]]; then
        echo "Cannot identify committed failed attempt for $experiment_id" >&2
        exit 2
    fi
    if [[ $(git rev-parse HEAD) != "$failure_commit" ]]; then
        echo "Failed attempt is not HEAD; refusing a non-adjacent archive" >&2
        exit 2
    fi
    local archive_relative="failed_runs/runtime_relocation/$experiment_id/attempt-${failure_commit:0:12}"
    local archive_directory="$project_root/$archive_relative"
    if [[ -e "$archive_directory" ]]; then
        echo "Refusing to overwrite failed-attempt archive: $archive_directory" >&2
        exit 2
    fi
    mkdir -p "$(dirname "$archive_directory")"
    git mv "$run_directory" "$archive_directory"
    git commit -m "archive failed R2 electron-number attempt $experiment_id"
    if [[ $(git rev-parse HEAD^) != "$failure_commit" ]]; then
        echo "Archive commit is not adjacent to the failed attempt" >&2
        exit 2
    fi
    assert_archive_commit_scope "$experiment_id" "$archive_relative/"
    "$python_tool" "$validator" "$manifest" --config "$config" \
        --require-committed --check-failure-archives "$experiment_id" >/dev/null
}

exec 9< <("$python_tool" - "$manifest" "$config" <<'PY'
import csv
import json
import sys

with open(sys.argv[1], encoding="utf-8", newline="") as handle:
    rows = list(csv.DictReader(handle, delimiter="\t"))
config = json.load(open(sys.argv[2], encoding="utf-8"))
execution = config["execution"]
order = execution["execution_order"]
pilots = execution["pilot_audit_ids"]
registered = execution["r2_audit_ids"]
if len(order) != 19 or len(set(order)) != 19:
    raise SystemExit("R2 execution_order must contain 19 unique IDs")
if order[:2] != pilots or pilots != ["S1-20260805-130", "S1-20260805-135"]:
    raise SystemExit("R2 pilots must be first in the order: 130 then 135")
if set(order) != set(registered):
    raise SystemExit("R2 execution_order and r2_audit_ids differ")
rows_by_id = {row["audit_experiment_id"]: row for row in rows}
if len(rows_by_id) != len(rows) or set(rows_by_id) != set(order):
    raise SystemExit("R2 manifest IDs differ from execution_order")
for audit_id in order:
    row = rows_by_id[audit_id]
    if row["solver"] != "ofdft":
        raise SystemExit(f"R2 row is not OFDFT: {audit_id}")
    print("\t".join((
        audit_id, row["source_experiment_id"], row["input_directory"],
        row["material"], row["series_id"],
    )))
PY
)

processed=0
while IFS=$'\t' read -r audit_id source_id input_directory material series_id <&9; do
    processed=$((processed + 1))
    run_directory="$project_root/runs/$audit_id"
    if [[ -d "$run_directory" ]]; then
        if "$python_tool" "$validator" "$manifest" --config "$config" \
            --require-committed --check-run "$audit_id" >/dev/null 2>&1; then
            echo "SKIP $audit_id already committed and strictly validated"
            continue
        fi
        archive_failed_attempt "$audit_id"
    fi

    if [[ "$audit_id" == "$pilot_two" ]]; then
        "$python_tool" "$validator" "$manifest" --config "$config" \
            --require-committed --check-run "$pilot_one" >/dev/null
    elif [[ "$audit_id" != "$pilot_one" ]]; then
        "$python_tool" "$validator" "$manifest" --config "$config" \
            --require-committed --check-run "$pilot_one" >/dev/null
        "$python_tool" "$validator" "$manifest" --config "$config" \
            --require-committed --check-run "$pilot_two" >/dev/null
    fi

    echo "START $audit_id source=$source_id material=$material series=$series_id"
    workflow_status=0
    env -i \
    HOME="$run_directory/runtime_home" \
    USER="${USER:-shenwei01}" LOGNAME="${LOGNAME:-${USER:-shenwei01}}" \
    PATH=/usr/bin:/bin LC_ALL=C TZ=UTC TMPDIR=/tmp \
    OMP_NUM_THREADS=1 CUDA_CACHE_DISABLE=1 \
    M_OFDFT_RUNTIME_RELOCATION_MODE=1 \
    M_OFDFT_RUNTIME="$recovery_root" M_OFDFT_PREFIX="$recovery_prefix" \
    OPAL_PREFIX="$recovery_prefix" PRTE_PREFIX="$recovery_prefix" \
    PMIX_PREFIX="$recovery_prefix" UCX_MODULE_DIR="$recovery_prefix" \
    M_OFDFT_ABACUS="$abacus_path" M_OFDFT_NPROCS="$rank_count" \
    M_OFDFT_MPIRUN="$python_tool" \
    M_OFDFT_MPIRUN_SCRIPT="${wrapper_path[namespace_launcher]}" \
    M_OFDFT_PROVENANCE_MPIRUN="$mpirun_path" M_OFDFT_PYTHON_TOOL="$python_tool" \
    M_OFDFT_PYTHON_SHA256="${tool_sha[python]}" M_OFDFT_REAL_MPIRUN="$mpirun_path" \
    M_OFDFT_EXPECTED_MPIRUN_SHA256="$mpirun_sha256" \
    M_OFDFT_EXPECTED_LAUNCHER="$launcher_path" M_OFDFT_EXPECTED_LAUNCHER_SHA256="$launcher_sha256" \
    M_OFDFT_EXPECTED_ABACUS="$abacus_path" M_OFDFT_EXPECTED_ABACUS_SHA256="$abacus_sha256" \
    M_OFDFT_REPLAY_ABACUS_PATH="$abacus_path" M_OFDFT_REPLAY_ABACUS_REALPATH="$abacus_realpath" \
    M_OFDFT_REPLAY_ABACUS_SHA256="$abacus_sha256" M_OFDFT_REPLAY_MPIRUN_PATH="$mpirun_path" \
    M_OFDFT_REPLAY_MPIRUN_REALPATH="$mpirun_realpath" M_OFDFT_REPLAY_MPIRUN_SHA256="$mpirun_sha256" \
    M_OFDFT_REPLAY_LAUNCHER_PATH="$launcher_path" M_OFDFT_REPLAY_LAUNCHER_REALPATH="$launcher_realpath" \
    M_OFDFT_REPLAY_LAUNCHER_SHA256="$launcher_sha256" \
    M_OFDFT_REFERENCE_ABACUS_PATH="$reference_abacus_path" \
    M_OFDFT_REFERENCE_ABACUS_REALPATH="$reference_abacus_realpath" \
    M_OFDFT_REFERENCE_ABACUS_SHA256="$reference_abacus_sha256" \
    M_OFDFT_REFERENCE_MPIRUN_PATH="$reference_mpirun_path" \
    M_OFDFT_REFERENCE_MPIRUN_REALPATH="$reference_mpirun_realpath" \
    M_OFDFT_REFERENCE_MPIRUN_SHA256="$reference_mpirun_sha256" \
    M_OFDFT_REFERENCE_LAUNCHER_PATH="$reference_launcher_path" \
    M_OFDFT_REFERENCE_LAUNCHER_REALPATH="$reference_launcher_realpath" \
    M_OFDFT_REFERENCE_LAUNCHER_SHA256="$reference_launcher_sha256" \
    M_OFDFT_MPI_AUDIT_DIR="$run_directory/mpi_runtime_audit" \
    M_OFDFT_RECOVERY_ROOT="$recovery_root" M_OFDFT_RECOVERY_PREFIX="$recovery_prefix" \
    M_OFDFT_OLD_ROOT="$old_root" M_OFDFT_OLD_PREFIX="$old_prefix" \
    M_OFDFT_MPI_AUDIT_EXPECTED_RANKS="$rank_count" \
    M_OFDFT_NAMESPACE_PAYLOAD="${wrapper_path[namespace_payload]}" \
    M_OFDFT_NAMESPACE_PAYLOAD_SHA256="${wrapper_sha[namespace_payload]}" \
    M_OFDFT_AUDIT_LAUNCHER="${wrapper_path[audit_launcher]}" \
    M_OFDFT_AUDIT_LAUNCHER_SHA256="${wrapper_sha[audit_launcher]}" \
    M_OFDFT_RANK_WRAPPER="${wrapper_path[rank_wrapper]}" \
    M_OFDFT_RANK_WRAPPER_SHA256="${wrapper_sha[rank_wrapper]}" \
    M_OFDFT_MOUNT_TOOL="${tool_path[mount]}" M_OFDFT_HOST_UID="$host_uid" M_OFDFT_HOST_GID="$host_gid" \
    M_OFDFT_STRACE_TOOL="${tool_path[strace]}" M_OFDFT_STRACE_PATH="${tool_path[strace]}" \
    M_OFDFT_STRACE_REALPATH="${tool_realpath[strace]}" M_OFDFT_STRACE_SHA256="${tool_sha[strace]}" \
    M_OFDFT_STRACE_VERSION_FIRST_LINE="${tool_version_line[strace]}" \
    M_OFDFT_STRACE_VERSION_OUTPUT_SHA256="${tool_version_sha[strace]}" \
    M_OFDFT_PYTHON_PATH="${tool_path[python]}" M_OFDFT_PYTHON_REALPATH="${tool_realpath[python]}" \
    M_OFDFT_PYTHON_VERSION_FIRST_LINE="${tool_version_line[python]}" \
    M_OFDFT_PYTHON_VERSION_OUTPUT_SHA256="${tool_version_sha[python]}" \
    M_OFDFT_UNSHARE_TOOL="${tool_path[unshare]}" M_OFDFT_UNSHARE_PATH="${tool_path[unshare]}" \
    M_OFDFT_UNSHARE_REALPATH="${tool_realpath[unshare]}" M_OFDFT_UNSHARE_SHA256="${tool_sha[unshare]}" \
    M_OFDFT_UNSHARE_VERSION_FIRST_LINE="${tool_version_line[unshare]}" \
    M_OFDFT_UNSHARE_VERSION_OUTPUT_SHA256="${tool_version_sha[unshare]}" \
    M_OFDFT_MOUNT_PATH="${tool_path[mount]}" M_OFDFT_MOUNT_REALPATH="${tool_realpath[mount]}" \
    M_OFDFT_MOUNT_SHA256="${tool_sha[mount]}" M_OFDFT_MOUNT_VERSION_FIRST_LINE="${tool_version_line[mount]}" \
    M_OFDFT_MOUNT_VERSION_OUTPUT_SHA256="${tool_version_sha[mount]}" \
    M_OFDFT_BASH_TOOL="${tool_path[bash]}" M_OFDFT_BASH_PATH="${tool_path[bash]}" \
    M_OFDFT_BASH_REALPATH="${tool_realpath[bash]}" M_OFDFT_BASH_SHA256="${tool_sha[bash]}" \
    M_OFDFT_BASH_VERSION_FIRST_LINE="${tool_version_line[bash]}" \
    M_OFDFT_BASH_VERSION_OUTPUT_SHA256="${tool_version_sha[bash]}" \
    scripts/run_s1_single.sh "$audit_id" "$input_directory" 9<&- </dev/null || workflow_status=$?

    core_status=97
    if [[ $workflow_status -eq 0 ]]; then
        if "$python_tool" "$validator" "$manifest" --config "$config" \
            --write-run-evidence "$audit_id" >/dev/null; then
            core_status=0
            "$python_tool" "$validator" "$manifest" --config "$config" \
                --check-run-core "$audit_id" >/dev/null || core_status=$?
        fi
    fi
    write_replay_status "$run_directory" "$workflow_status" "$core_status"
    if [[ $workflow_status -eq 0 && $core_status -eq 0 ]]; then
        final_validation_status=0
        "$python_tool" "$validator" "$manifest" --config "$config" \
            --check-run "$audit_id" >/dev/null || final_validation_status=$?
        if [[ $final_validation_status -ne 0 ]]; then
            core_status=$final_validation_status
            write_replay_status "$run_directory" "$workflow_status" "$core_status"
        fi
    fi
    if [[ $workflow_status -ne 0 || $core_status -ne 0 ]]; then
        "$python_tool" "$validator" "$manifest" --config "$config" \
            --check-failure-run "$audit_id" >/dev/null
    fi

    git add "$run_directory"
    staged=$(git diff --cached --name-only)
    if [[ -z "$staged" ]]; then
        echo "No R2 run evidence was staged for $audit_id" >&2
        exit 98
    fi
    while IFS= read -r path; do
        if [[ "$path" != "runs/$audit_id/"* ]]; then
            echo "Staged path outside run directory: $path" >&2
            exit 98
        fi
    done <<<"$staged"
    git commit -m "record R2 electron-number density replay $audit_id from $source_id"
    assert_commit_scope "runs/$audit_id/"
    if [[ $workflow_status -ne 0 || $core_status -ne 0 ]]; then
        "$python_tool" "$validator" "$manifest" --config "$config" \
            --require-committed --check-failure-run "$audit_id" >/dev/null
        echo "STOP $audit_id workflow=$workflow_status core=$core_status; failure committed" >&2
        exit 97
    fi
    "$python_tool" "$validator" "$manifest" --config "$config" \
        --require-committed --check-run "$audit_id" >/dev/null
    echo "DONE $audit_id"
done
exec 9<&-

if [[ $processed -ne 19 ]]; then
    echo "R2 electron-number runner processed $processed rows instead of 19" >&2
    exit 97
fi
"$python_tool" "$validator" "$manifest" --config "$config" \
    --require-committed --require-all-runs >/dev/null
