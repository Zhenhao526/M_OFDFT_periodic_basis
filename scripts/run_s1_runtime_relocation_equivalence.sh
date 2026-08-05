#!/usr/bin/env bash
set -euo pipefail

if [[ $# -gt 2 ]]; then
    echo "Usage: $0 [MANIFEST_TSV [CONFIG_JSON]]" >&2
    exit 2
fi

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
manifest=${1:-$project_root/config/S1_runtime_relocation_equivalence_manifest.tsv}
config=${2:-$project_root/config/S1_runtime_relocation_equivalence.json}
manifest=$(realpath "$manifest")
config=$(realpath "$config")
cd "$project_root"

if [[ -n $(git status --porcelain) ]]; then
    echo "Refusing runtime-relocation replay from a dirty worktree" >&2
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
values = [
    runtime["recovery_root"],
    runtime["recovery_prefix"],
    runtime["old_root"],
    runtime["old_prefix"],
    replay["abacus"]["path"],
    replay["abacus"]["realpath"],
    replay["abacus"]["sha256"],
    replay["mpirun"]["path"],
    replay["mpirun"]["realpath"],
    replay["mpirun"]["sha256"],
    replay["launcher"]["path"],
    replay["launcher"]["realpath"],
    replay["launcher"]["sha256"],
    reference["abacus"]["path"],
    reference["abacus"]["realpath"],
    reference["abacus"]["sha256"],
    reference["mpirun"]["path"],
    reference["mpirun"]["realpath"],
    reference["mpirun"]["sha256"],
    reference["launcher"]["path"],
    reference["launcher"]["realpath"],
    reference["launcher"]["sha256"],
    str(config["rank_count"]),
    str(runtime["namespace"]["host_uid"]),
    str(runtime["namespace"]["host_gid"]),
]
for name in ("python", "strace", "unshare", "mount", "bash"):
    tool = tools[name]
    values.extend(
        [
            tool["path"],
            tool["realpath"],
            tool["sha256"],
            tool["version_first_line"],
            tool["version_output_sha256"],
        ]
    )
for name in ("namespace_launcher", "namespace_payload", "audit_launcher", "rank_wrapper"):
    values.extend([wrappers[name]["path"], wrappers[name]["sha256"]])
for value in values:
    if "\0" in value:
        raise SystemExit("runtime config contains a NUL")
    sys.stdout.write(value)
    sys.stdout.write("\0")
PY
)

if [[ ${#runtime_values[@]} -ne 58 ]]; then
    echo "Unexpected runtime field count: ${#runtime_values[@]}" >&2
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
python_tool=${tool_path[python]}

"$python_tool" scripts/validate_s1_mpi_prefix_equivalence.py \
    "$manifest" --config "$config" --require-committed >/dev/null

assert_clean_and_commit_scope() {
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

assert_clean_archive_commit_scope() {
    local experiment_id=$1
    local archive_prefix=$2
    if [[ -n $(git status --porcelain) ]]; then
        echo "Post-archive worktree is not clean" >&2
        return 1
    fi
    local changed
    changed=$(git diff-tree --no-commit-id --name-only -r HEAD)
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
    "$python_tool" scripts/validate_s1_mpi_prefix_equivalence.py \
        "$manifest" --config "$config" --require-committed \
        --check-failure-run "$experiment_id" >/dev/null
    local failure_commit
    failure_commit=$(git log --no-renames --diff-filter=A --format=%H -- \
        "runs/$experiment_id/replay_status.json" | head -n 1)
    if [[ ! "$failure_commit" =~ ^[0-9a-f]{40}$ ]]; then
        echo "Cannot identify committed failed attempt for $experiment_id" >&2
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
    git commit -m "archive failed runtime-relocation attempt $experiment_id"
    assert_clean_archive_commit_scope "$experiment_id" "$archive_relative/"
}

write_replay_status() {
    local run_directory=$1
    local workflow_exit=$2
    local core_validation_exit=$3
    "$python_tool" - "$run_directory" "$workflow_exit" "$core_validation_exit" <<'PY'
import json
import os
import sys
from pathlib import Path

run = Path(sys.argv[1])
workflow_exit = int(sys.argv[2])
core_validation_exit = int(sys.argv[3])
run_status_path = run / "run_status.json"
audit_path = run / "mpi_runtime_audit" / "audit.json"
run_status = json.loads(run_status_path.read_text(encoding="utf-8")) if run_status_path.is_file() else None
audit = json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.is_file() else None
if not isinstance(run_status, dict):
    raise SystemExit("run_status.json is required before replay status")
if run_status.get("workflow_exit_code") != workflow_exit:
    raise SystemExit("captured workflow exit differs from run_status.json")
accepted = (
    workflow_exit == 0
    and core_validation_exit == 0
    and run_status.get("status") == "accepted"
)
payload = {
    "schema_version": 2,
    "status": "accepted" if accepted else "rejected",
    "workflow_exit_code": workflow_exit,
    "invocation_exit_code": run_status.get("invocation_exit_code"),
    "launcher_exit_code": run_status.get("launcher_exit_code"),
    "parser_exit_code": run_status.get("parser_exit_code"),
    "core_validation_exit_code": core_validation_exit,
    "run_status": run_status,
    "runtime_audit_status": audit.get("status") if isinstance(audit, dict) else None,
    "runtime_audit_failure_reasons": audit.get("failure_reasons", []) if isinstance(audit, dict) else [],
    "safe_retry_policy": "archive_committed_failure_then_retry_same_registered_id",
}
output = run / "replay_status.json"
temporary = output.with_name(f".{output.name}.tmp-{os.getpid()}")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, output)
failure = run / "failure.json"
if accepted:
    if failure.exists():
        raise SystemExit("accepted run unexpectedly has failure.json")
else:
    failure_payload = {
        "schema_version": 2,
        "status": "failed_attempt_preserved",
        "workflow_exit_code": workflow_exit,
        "invocation_exit_code": run_status.get("invocation_exit_code"),
        "launcher_exit_code": run_status.get("launcher_exit_code"),
        "parser_exit_code": run_status.get("parser_exit_code"),
        "core_validation_exit_code": core_validation_exit,
        "runtime_audit_failure_reasons": payload["runtime_audit_failure_reasons"],
        "retry_requires_committed_archive": True,
    }
    temporary = failure.with_name(f".{failure.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(failure_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, failure)
PY
}

exec 9<"$manifest"
IFS= read -r header <&9
expected_header=$'replay_experiment_id\treference_experiment_id\tinput_directory\tmaterial\tseries_id\tsolver\tinput_sha256\tstru_sha256\tkpt_sha256\tmetadata_sha256\tpseudopotential\tpseudopotential_sha256\treference_result_path\treference_result_sha256\treference_log_path\treference_log_sha256\treference_experiment_metadata_path\treference_experiment_metadata_sha256\treference_abacus_path\treference_abacus_realpath\treference_abacus_sha256\treference_mpirun_path\treference_mpirun_realpath\treference_mpirun_sha256\tconfig_sha256'
if [[ "$header" != "$expected_header" ]]; then
    echo "Invalid runtime-relocation manifest header" >&2
    exit 2
fi

while IFS=$'\t' read -r replay_id reference_id input_directory material series_id solver \
    input_sha256 stru_sha256 kpt_sha256 metadata_sha256 pseudopotential \
    pseudopotential_sha256 reference_result_path reference_result_sha256 \
    reference_log_path reference_log_sha256 reference_experiment_metadata_path \
    reference_experiment_metadata_sha256 reference_abacus_path reference_abacus_realpath \
    reference_abacus_sha256 reference_mpirun_path reference_mpirun_realpath \
    reference_mpirun_sha256 config_sha256 <&9; do
    run_directory="$project_root/runs/$replay_id"
    if [[ -d "$run_directory" ]]; then
        if "$python_tool" scripts/validate_s1_mpi_prefix_equivalence.py \
            "$manifest" --config "$config" --require-committed \
            --check-run "$replay_id" >/dev/null 2>&1; then
            echo "SKIP $replay_id already committed and strictly validated"
            continue
        fi
        archive_failed_attempt "$replay_id"
    fi

    echo "START $replay_id reference=$reference_id material=$material series=$series_id"
    workflow_status=0
    env -i \
    HOME="$run_directory/runtime_home" \
    USER="${USER:-shenwei01}" \
    LOGNAME="${LOGNAME:-${USER:-shenwei01}}" \
    PATH=/usr/bin:/bin \
    LC_ALL=C \
    TZ=UTC \
    TMPDIR=/tmp \
    OMP_NUM_THREADS=1 \
    M_OFDFT_RUNTIME_RELOCATION_MODE=1 \
    M_OFDFT_RUNTIME="$recovery_root" \
    M_OFDFT_PREFIX="$recovery_prefix" \
    OPAL_PREFIX="$recovery_prefix" \
    PRTE_PREFIX="$recovery_prefix" \
    PMIX_PREFIX="$recovery_prefix" \
    UCX_MODULE_DIR="$recovery_prefix" \
    M_OFDFT_ABACUS="$abacus_path" \
    M_OFDFT_NPROCS="$rank_count" \
    M_OFDFT_MPIRUN="$python_tool" \
    M_OFDFT_MPIRUN_SCRIPT="${wrapper_path[namespace_launcher]}" \
    M_OFDFT_PROVENANCE_MPIRUN="$mpirun_path" \
    M_OFDFT_PYTHON_TOOL="$python_tool" \
    M_OFDFT_PYTHON_SHA256="${tool_sha[python]}" \
    M_OFDFT_REAL_MPIRUN="$mpirun_path" \
    M_OFDFT_EXPECTED_MPIRUN_SHA256="$mpirun_sha256" \
    M_OFDFT_EXPECTED_LAUNCHER="$launcher_path" \
    M_OFDFT_EXPECTED_LAUNCHER_SHA256="$launcher_sha256" \
    M_OFDFT_EXPECTED_ABACUS="$abacus_path" \
    M_OFDFT_EXPECTED_ABACUS_SHA256="$abacus_sha256" \
    M_OFDFT_REPLAY_ABACUS_PATH="$abacus_path" \
    M_OFDFT_REPLAY_ABACUS_REALPATH="$abacus_realpath" \
    M_OFDFT_REPLAY_ABACUS_SHA256="$abacus_sha256" \
    M_OFDFT_REPLAY_MPIRUN_PATH="$mpirun_path" \
    M_OFDFT_REPLAY_MPIRUN_REALPATH="$mpirun_realpath" \
    M_OFDFT_REPLAY_MPIRUN_SHA256="$mpirun_sha256" \
    M_OFDFT_REPLAY_LAUNCHER_PATH="$launcher_path" \
    M_OFDFT_REPLAY_LAUNCHER_REALPATH="$launcher_realpath" \
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
    M_OFDFT_RECOVERY_ROOT="$recovery_root" \
    M_OFDFT_RECOVERY_PREFIX="$recovery_prefix" \
    M_OFDFT_OLD_ROOT="$old_root" \
    M_OFDFT_OLD_PREFIX="$old_prefix" \
    M_OFDFT_MPI_AUDIT_EXPECTED_RANKS="$rank_count" \
    M_OFDFT_NAMESPACE_PAYLOAD="${wrapper_path[namespace_payload]}" \
    M_OFDFT_NAMESPACE_PAYLOAD_SHA256="${wrapper_sha[namespace_payload]}" \
    M_OFDFT_AUDIT_LAUNCHER="${wrapper_path[audit_launcher]}" \
    M_OFDFT_AUDIT_LAUNCHER_SHA256="${wrapper_sha[audit_launcher]}" \
    M_OFDFT_RANK_WRAPPER="${wrapper_path[rank_wrapper]}" \
    M_OFDFT_RANK_WRAPPER_SHA256="${wrapper_sha[rank_wrapper]}" \
    M_OFDFT_MOUNT_TOOL="${tool_path[mount]}" \
    M_OFDFT_HOST_UID="$host_uid" \
    M_OFDFT_HOST_GID="$host_gid" \
    M_OFDFT_STRACE_TOOL="${tool_path[strace]}" \
    M_OFDFT_STRACE_PATH="${tool_path[strace]}" \
    M_OFDFT_STRACE_REALPATH="${tool_realpath[strace]}" \
    M_OFDFT_STRACE_SHA256="${tool_sha[strace]}" \
    M_OFDFT_STRACE_VERSION_FIRST_LINE="${tool_version_line[strace]}" \
    M_OFDFT_STRACE_VERSION_OUTPUT_SHA256="${tool_version_sha[strace]}" \
    M_OFDFT_PYTHON_PATH="${tool_path[python]}" \
    M_OFDFT_PYTHON_REALPATH="${tool_realpath[python]}" \
    M_OFDFT_PYTHON_VERSION_FIRST_LINE="${tool_version_line[python]}" \
    M_OFDFT_PYTHON_VERSION_OUTPUT_SHA256="${tool_version_sha[python]}" \
    M_OFDFT_UNSHARE_TOOL="${tool_path[unshare]}" \
    M_OFDFT_UNSHARE_PATH="${tool_path[unshare]}" \
    M_OFDFT_UNSHARE_REALPATH="${tool_realpath[unshare]}" \
    M_OFDFT_UNSHARE_SHA256="${tool_sha[unshare]}" \
    M_OFDFT_UNSHARE_VERSION_FIRST_LINE="${tool_version_line[unshare]}" \
    M_OFDFT_UNSHARE_VERSION_OUTPUT_SHA256="${tool_version_sha[unshare]}" \
    M_OFDFT_MOUNT_PATH="${tool_path[mount]}" \
    M_OFDFT_MOUNT_REALPATH="${tool_realpath[mount]}" \
    M_OFDFT_MOUNT_SHA256="${tool_sha[mount]}" \
    M_OFDFT_MOUNT_VERSION_FIRST_LINE="${tool_version_line[mount]}" \
    M_OFDFT_MOUNT_VERSION_OUTPUT_SHA256="${tool_version_sha[mount]}" \
    M_OFDFT_BASH_TOOL="${tool_path[bash]}" \
    M_OFDFT_BASH_PATH="${tool_path[bash]}" \
    M_OFDFT_BASH_REALPATH="${tool_realpath[bash]}" \
    M_OFDFT_BASH_SHA256="${tool_sha[bash]}" \
    M_OFDFT_BASH_VERSION_FIRST_LINE="${tool_version_line[bash]}" \
    M_OFDFT_BASH_VERSION_OUTPUT_SHA256="${tool_version_sha[bash]}" \
    scripts/run_s1_single.sh "$replay_id" "$input_directory" 9<&- </dev/null || workflow_status=$?

    core_validation_status=0
    if [[ -d "$run_directory" ]]; then
        "$python_tool" scripts/validate_s1_mpi_prefix_equivalence.py \
            "$manifest" --config "$config" --check-run-core "$replay_id" \
            >/dev/null || core_validation_status=$?
        write_replay_status "$run_directory" "$workflow_status" "$core_validation_status"
        if [[ $workflow_status -eq 0 && $core_validation_status -eq 0 ]]; then
            "$python_tool" scripts/validate_s1_mpi_prefix_equivalence.py \
                "$manifest" --config "$config" --check-run "$replay_id" >/dev/null
        else
            "$python_tool" scripts/validate_s1_mpi_prefix_equivalence.py \
                "$manifest" --config "$config" --check-failure-run "$replay_id" >/dev/null
        fi
        git add "$run_directory"
        staged=$(git diff --cached --name-only)
        while IFS= read -r path; do
            if [[ "$path" != "runs/$replay_id/"* ]]; then
                echo "Staged path outside run directory: $path" >&2
                exit 98
            fi
        done <<<"$staged"
        git commit -m "record runtime-relocation replay $replay_id against $reference_id"
        assert_clean_and_commit_scope "runs/$replay_id/"
    fi
    if [[ $workflow_status -ne 0 || $core_validation_status -ne 0 ]]; then
        "$python_tool" scripts/validate_s1_mpi_prefix_equivalence.py \
            "$manifest" --config "$config" --require-committed \
            --check-failure-run "$replay_id" >/dev/null
        echo "STOP $replay_id workflow=$workflow_status core_validation=$core_validation_status; failure committed" >&2
        exit 97
    fi
    "$python_tool" scripts/validate_s1_mpi_prefix_equivalence.py \
        "$manifest" --config "$config" --require-committed \
        --check-run "$replay_id" >/dev/null
    echo "DONE $replay_id"
done
exec 9<&-
