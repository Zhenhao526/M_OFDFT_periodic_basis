#!/usr/bin/env bash
set -euo pipefail

if [[ $# -gt 2 ]]; then
    echo "Usage: $0 [MANIFEST_TSV [CONFIG_JSON]]" >&2
    exit 2
fi

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
manifest=$(realpath "${1:-$project_root/config/S1_g1_thermodynamic_label_audit_r1_manifest.tsv}")
config=$(realpath "${2:-$project_root/config/S1_g1_thermodynamic_label_audit_r1.json}")
cd "$project_root"

if [[ -n $(git status --porcelain=v1 --untracked-files=all) ]]; then
    echo "Refusing thermodynamic-label audit from a dirty worktree" >&2
    exit 2
fi

bootstrap_python=$(command -v python3)
python_tool=$(
    "$bootstrap_python" - "$config" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["runtime"]["tools"]["python"]["path"])
PY
)

validator="$project_root/scripts/validate_s1_g1_thermodynamic_label_audit_r1.py"
parser="$project_root/scripts/parse_s1_g1_thermodynamic_labels.py"
analyzer="$project_root/scripts/analyze_s1_g1_thermodynamic_label_audit_r1.py"
analysis_directory="$project_root/analysis/s1/g1_thermodynamic_label_audit_r1_20260806"

"$python_tool" "$validator" "$manifest" --config "$config" --require-committed >/dev/null

mapfile -d '' run_plan < <(
    "$python_tool" - "$config" "$manifest" <<'PY'
import csv
import json
import sys

config = json.load(open(sys.argv[1], encoding="utf-8"))
with open(sys.argv[2], encoding="utf-8", newline="") as handle:
    rows = {row["experiment_id"]: row for row in csv.DictReader(handle, delimiter="\t")}
for experiment_id in config["execution_order"]:
    row = rows[experiment_id]
    for value in (
        experiment_id,
        row["input_directory"],
        row["material"],
        row["run_role"],
        row["execution_phase"],
    ):
        if "\0" in value:
            raise SystemExit("manifest contains a NUL")
        sys.stdout.write(value + "\0")
PY
)
if [[ ${#run_plan[@]} -ne 200 ]]; then
    echo "Execution plan does not contain exactly 40 five-field rows" >&2
    exit 2
fi

runtime_environment() {
    local run_directory=$1
    "$python_tool" - "$config" "$run_directory" <<'PY'
import json
import sys

config = json.load(open(sys.argv[1], encoding="utf-8"))
run = sys.argv[2]
runtime = config["runtime"]
replay = runtime["replay"]
reference = runtime["reference"]
tools = runtime["tools"]
wrappers = runtime["wrappers"]
audit = config["runtime_audit"]
python_tool = tools["python"]["path"]
values = {
    "HOME": f"{run}/runtime_home",
    "USER": "shenwei01",
    "LOGNAME": "shenwei01",
    "PATH": "/usr/bin:/bin",
    "LC_ALL": "C",
    "TZ": "UTC",
    "TMPDIR": "/tmp",
    "OMP_NUM_THREADS": "1",
    "CUDA_CACHE_DISABLE": "1",
    "M_OFDFT_RUNTIME_RELOCATION_MODE": "1",
    "M_OFDFT_RUNTIME": runtime["recovery_root"],
    "M_OFDFT_PREFIX": runtime["recovery_prefix"],
    "OPAL_PREFIX": runtime["recovery_prefix"],
    "PRTE_PREFIX": runtime["recovery_prefix"],
    "PMIX_PREFIX": runtime["recovery_prefix"],
    "UCX_MODULE_DIR": runtime["recovery_prefix"],
    "M_OFDFT_ABACUS": replay["abacus"]["path"],
    "M_OFDFT_NPROCS": str(config["rank_count"]),
    "M_OFDFT_MPIRUN": python_tool,
    "M_OFDFT_MPIRUN_SCRIPT": wrappers["namespace_launcher"]["path"],
    "M_OFDFT_PROVENANCE_MPIRUN": replay["mpirun"]["path"],
    "M_OFDFT_PYTHON_TOOL": python_tool,
    "M_OFDFT_PYTHON_SHA256": tools["python"]["sha256"],
    "M_OFDFT_REAL_MPIRUN": replay["mpirun"]["path"],
    "M_OFDFT_EXPECTED_MPIRUN_SHA256": replay["mpirun"]["sha256"],
    "M_OFDFT_EXPECTED_LAUNCHER": replay["launcher"]["path"],
    "M_OFDFT_EXPECTED_LAUNCHER_SHA256": replay["launcher"]["sha256"],
    "M_OFDFT_EXPECTED_ABACUS": replay["abacus"]["path"],
    "M_OFDFT_EXPECTED_ABACUS_SHA256": replay["abacus"]["sha256"],
    "M_OFDFT_MPI_AUDIT_DIR": f"{run}/mpi_runtime_audit",
    "M_OFDFT_RECOVERY_ROOT": runtime["recovery_root"],
    "M_OFDFT_RECOVERY_PREFIX": runtime["recovery_prefix"],
    "M_OFDFT_OLD_ROOT": runtime["old_root"],
    "M_OFDFT_OLD_PREFIX": runtime["old_prefix"],
    "M_OFDFT_MPI_AUDIT_EXPECTED_RANKS": str(config["rank_count"]),
    "M_OFDFT_NAMESPACE_PAYLOAD": wrappers["namespace_payload"]["path"],
    "M_OFDFT_NAMESPACE_PAYLOAD_SHA256": wrappers["namespace_payload"]["sha256"],
    "M_OFDFT_AUDIT_LAUNCHER": wrappers["audit_launcher"]["path"],
    "M_OFDFT_AUDIT_LAUNCHER_SHA256": wrappers["audit_launcher"]["sha256"],
    "M_OFDFT_RANK_WRAPPER": wrappers["rank_wrapper"]["path"],
    "M_OFDFT_RANK_WRAPPER_SHA256": wrappers["rank_wrapper"]["sha256"],
    "M_OFDFT_MOUNT_TOOL": tools["mount"]["path"],
    "M_OFDFT_HOST_UID": str(runtime["namespace"]["host_uid"]),
    "M_OFDFT_HOST_GID": str(runtime["namespace"]["host_gid"]),
}
for prefix, source in (("REPLAY", replay), ("REFERENCE", reference)):
    for name in ("abacus", "mpirun", "launcher"):
        upper = name.upper()
        for field in ("path", "realpath", "sha256"):
            values[f"M_OFDFT_{prefix}_{upper}_{field.upper()}"] = source[name][field]
for name, prefix in (
    ("strace", "STRACE"),
    ("python", "PYTHON"),
    ("unshare", "UNSHARE"),
    ("mount", "MOUNT"),
    ("bash", "BASH"),
):
    tool = tools[name]
    values[f"M_OFDFT_{prefix}_PATH"] = tool["path"]
    values[f"M_OFDFT_{prefix}_REALPATH"] = tool["realpath"]
    values[f"M_OFDFT_{prefix}_SHA256"] = tool["sha256"]
    values[f"M_OFDFT_{prefix}_VERSION_FIRST_LINE"] = tool["version_first_line"]
    values[f"M_OFDFT_{prefix}_VERSION_OUTPUT_SHA256"] = tool["version_output_sha256"]
values["M_OFDFT_STRACE_TOOL"] = tools["strace"]["path"]
values["M_OFDFT_UNSHARE_TOOL"] = tools["unshare"]["path"]
values["M_OFDFT_BASH_TOOL"] = tools["bash"]["path"]
for key, value in values.items():
    value = str(value)
    if "\0" in key or "\0" in value or "=" in key:
        raise SystemExit("invalid runtime environment registration")
    sys.stdout.write(f"{key}={value}\0")
PY
}

write_authoritative_status() {
    local run_directory=$1
    local experiment_id=$2
    local status=$3
    local workflow_exit=$4
    local parser_exit=$5
    local core_exit=$6
    local failure_stage=${7:-}
    "$python_tool" - "$run_directory" "$experiment_id" "$status" \
        "$workflow_exit" "$parser_exit" "$core_exit" "$failure_stage" <<'PY'
import json
import sys
from pathlib import Path

run = Path(sys.argv[1])
payload = {
    "schema_version": 1,
    "protocol_revision": "S1-G1-THERMODYNAMIC-LABEL-AUDIT-R1",
    "experiment_id": sys.argv[2],
    "status": sys.argv[3],
    "authoritative_for_r1": True,
    "retry_policy": "new_protocol_revision_and_new_experiment_ids_only",
    "workflow_exit_code": int(sys.argv[4]),
    "parser_exit_code": int(sys.argv[5]),
    "core_validator_exit_code": int(sys.argv[6]),
}
if payload["status"] != "accepted":
    payload["failure_stage"] = sys.argv[7]
path = run / "thermodynamic_label_status.json"
with path.open("x", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY
}

write_noncore_failure_classification() {
    local run_directory=$1
    local experiment_id=$2
    local failure_stage=$3
    local component_exit=$4
    local diagnostic_path=$5
    "$python_tool" - "$run_directory" "$experiment_id" "$failure_stage" \
        "$component_exit" "$diagnostic_path" <<'PY'
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "scripts"))
from validate_s1_g1_thermodynamic_label_audit_r1 import classify_noncore_failure

run = Path(sys.argv[1])
experiment_id = sys.argv[2]
stage = sys.argv[3]
component_exit = int(sys.argv[4])
diagnostic = Path(sys.argv[5])
payload = classify_noncore_failure(
    run, experiment_id, stage, component_exit, diagnostic
)
path = run / "thermodynamic_label_failure_classification.json"
with path.open("x", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY
}

write_failure_inventory() {
    local run_directory=$1
    local experiment_id=$2
    "$python_tool" - "$run_directory" "$experiment_id" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

run = Path(sys.argv[1])
output = run / "thermodynamic_label_failure_artifact_inventory.json"
files = []
for path in sorted(run.rglob("*"), key=lambda value: str(value.relative_to(run))):
    if path == output:
        continue
    if path.is_symlink():
        raise SystemExit(f"symbolic failure artifact is forbidden: {path}")
    if path.is_file():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files.append(
            {
                "path": str(path.relative_to(run)),
                "sha256": digest,
                "size_bytes": path.stat().st_size,
            }
        )
payload = {
    "schema_version": 1,
    "protocol_revision": "S1-G1-THERMODYNAMIC-LABEL-AUDIT-R1",
    "experiment_id": sys.argv[2],
    "files": files,
}
with output.open("x", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY
}

assert_commit_scope() {
    local prefix=$1
    if [[ -n $(git status --porcelain=v1 --untracked-files=all) ]]; then
        echo "Post-commit worktree is not clean" >&2
        exit 98
    fi
    local changed
    changed=$(git diff-tree --no-commit-id --name-only -r HEAD)
    if [[ -z "$changed" ]]; then
        echo "Commit contains no paths" >&2
        exit 98
    fi
    while IFS= read -r path; do
        if [[ "$path" != "$prefix"* ]]; then
            echo "Commit contains path outside $prefix: $path" >&2
            exit 98
        fi
    done <<<"$changed"
}

commit_run() {
    local experiment_id=$1
    local run_directory="$project_root/runs/$experiment_id"
    git add -- "$run_directory"
    local staged
    staged=$(git diff --cached --name-only)
    if [[ -z "$staged" ]]; then
        echo "No run evidence staged for $experiment_id" >&2
        exit 98
    fi
    while IFS= read -r path; do
        if [[ "$path" != "runs/$experiment_id/"* ]]; then
            echo "Staged path outside runs/$experiment_id: $path" >&2
            exit 98
        fi
    done <<<"$staged"
    git commit -m "record G1 thermodynamic-label audit $experiment_id"
    assert_commit_scope "runs/$experiment_id/"
}

archive_and_stop() {
    local experiment_id=$1
    local failure_commit
    failure_commit=$(git rev-parse HEAD)
    local archive_relative="failed_runs/runtime_relocation/$experiment_id/attempt-${failure_commit:0:12}"
    if [[ -e "$project_root/$archive_relative" ]]; then
        echo "Refusing to overwrite failed-attempt archive: $archive_relative" >&2
        exit 98
    fi
    mkdir -p "$project_root/failed_runs/runtime_relocation/$experiment_id"
    git mv "$project_root/runs/$experiment_id" "$project_root/$archive_relative"
    git commit -m "archive failed G1 thermodynamic-label attempt $experiment_id"
    "$python_tool" "$validator" "$manifest" --config "$config" --require-committed \
        --check-failure-archives "$experiment_id" >/dev/null
    echo "STOP $experiment_id: failed formal attempt committed and archived; R1 forbids retry" >&2
    exit 97
}

run_barriers() {
    local experiment_id=$1
    case "$experiment_id" in
        S1-20260806-039)
            "$python_tool" "$validator" "$manifest" --config "$config" \
                --require-committed --require-pilot-gate >/dev/null
            ;;
        S1-20260806-040)
            "$python_tool" "$validator" "$manifest" --config "$config" \
                --require-committed --require-k-gate >/dev/null
            ;;
    esac
    case "$experiment_id" in
        S1-20260806-007) quarter_id=S1-20260806-021 ;;
        S1-20260806-010) quarter_id=S1-20260806-024 ;;
        S1-20260806-013) quarter_id=S1-20260806-027 ;;
        S1-20260806-014) quarter_id=S1-20260806-028 ;;
        S1-20260806-017) quarter_id=S1-20260806-031 ;;
        S1-20260806-020) quarter_id=S1-20260806-034 ;;
        S1-20260806-022|S1-20260806-023|S1-20260806-025|S1-20260806-026|S1-20260806-029|S1-20260806-030|S1-20260806-032|S1-20260806-033)
            quarter_id=$experiment_id
            ;;
        *) quarter_id= ;;
    esac
    if [[ -n "$quarter_id" ]]; then
        "$python_tool" "$validator" "$manifest" --config "$config" \
            --require-committed --require-half-quarter-pair "$quarter_id" >/dev/null
    fi
    case "$experiment_id" in
        S1-20260806-013)
            "$python_tool" "$validator" "$manifest" --config "$config" \
                --require-committed --require-adjacent-eos al standard half >/dev/null
            ;;
        S1-20260806-020)
            "$python_tool" "$validator" "$manifest" --config "$config" \
                --require-committed --require-adjacent-eos mg standard half >/dev/null
            ;;
        S1-20260806-026)
            "$python_tool" "$validator" "$manifest" --config "$config" \
                --require-committed --require-adjacent-eos al half quarter >/dev/null
            ;;
        S1-20260806-033)
            "$python_tool" "$validator" "$manifest" --config "$config" \
                --require-committed --require-adjacent-eos mg half quarter >/dev/null
            ;;
    esac
}

for ((offset = 0; offset < ${#run_plan[@]}; offset += 5)); do
    experiment_id=${run_plan[$offset]}
    input_directory="$project_root/${run_plan[$((offset + 1))]}"
    material=${run_plan[$((offset + 2))]}
    role=${run_plan[$((offset + 3))]}
    phase=${run_plan[$((offset + 4))]}
    run_directory="$project_root/runs/$experiment_id"
    archive_root="$project_root/failed_runs/runtime_relocation/$experiment_id"

    if [[ -e "$archive_root" ]]; then
        echo "STOP $experiment_id: an R1 failed-attempt archive already exists; retry is forbidden" >&2
        exit 97
    fi
    if [[ -e "$run_directory" ]]; then
        if "$python_tool" "$validator" "$manifest" --config "$config" \
            --require-committed --check-run "$experiment_id" >/dev/null 2>&1; then
            echo "SKIP $experiment_id already committed and strictly accepted"
            run_barriers "$experiment_id"
            continue
        fi
        echo "STOP $experiment_id: existing run is not an accepted immutable result; no retry" >&2
        exit 97
    fi

    echo "START $experiment_id phase=$phase material=$material role=$role"
    mapfile -d '' env_args < <(runtime_environment "$run_directory")
    workflow_status=0
    env -i "${env_args[@]}" scripts/run_s1_single.sh \
        "$experiment_id" "$input_directory" </dev/null || workflow_status=$?

    label_parser_status=97
    if [[ $workflow_status -eq 0 ]]; then
        label_parser_status=0
        "$python_tool" "$parser" "$run_directory" --config "$config" \
            --manifest "$manifest" --write \
            >"$run_directory/thermodynamic_label_parser.stdout.txt" \
            2>"$run_directory/thermodynamic_label_parser.stderr.txt" \
            || label_parser_status=$?
    fi
    core_status=97
    if [[ $workflow_status -eq 0 && $label_parser_status -eq 0 ]]; then
        core_status=0
        "$python_tool" "$validator" "$manifest" --config "$config" \
            --check-run-core "$experiment_id" --write-core-failure-evidence \
            >"$run_directory/core_validator.stdout.txt" \
            2>"$run_directory/core_validator.stderr.txt" \
            || core_status=$?
    fi

    if [[ $workflow_status -eq 0 && $label_parser_status -eq 0 && $core_status -eq 0 ]]; then
        "$python_tool" "$validator" "$manifest" --config "$config" \
            --write-run-evidence "$experiment_id" >/dev/null
        write_authoritative_status "$run_directory" "$experiment_id" accepted 0 0 0
        "$python_tool" "$validator" "$manifest" --config "$config" \
            --check-run "$experiment_id" >/dev/null
    else
        failure_stage=workflow
        [[ $workflow_status -eq 0 ]] && failure_stage=thermodynamic_label_parser
        [[ $workflow_status -eq 0 && $label_parser_status -eq 0 ]] && failure_stage=core_validator
        if [[ ! -f "$run_directory/run_status.json" ]]; then
            mkdir -p "$run_directory"
            "$python_tool" scripts/write_s1_runtime_relocation_status.py "$run_directory" \
                --experiment-id "$experiment_id" --code-commit "$(git rev-parse HEAD)" \
                --workflow-exit "$workflow_status" --invocation-exit "$workflow_status" \
                --parser-exit "$label_parser_status" --core-validation-exit "$core_status" \
                --setup-completed false --runtime-relocation-mode true \
                --failure-stage "$failure_stage" --run-only >/dev/null
        fi
        if [[ "$failure_stage" == workflow ]]; then
            workflow_diagnostic="$run_directory/outer_workflow_failure.txt"
            printf 'run_s1_single workflow exit code: %s\n' "$workflow_status" >"$workflow_diagnostic"
            write_noncore_failure_classification "$run_directory" "$experiment_id" \
                "$failure_stage" "$workflow_status" "$workflow_diagnostic"
        elif [[ "$failure_stage" == thermodynamic_label_parser ]]; then
            write_noncore_failure_classification "$run_directory" "$experiment_id" \
                "$failure_stage" "$label_parser_status" \
                "$run_directory/thermodynamic_label_parser.stderr.txt"
        fi
        failure_decision=$(
            "$python_tool" - "$run_directory/thermodynamic_label_failure_classification.json" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["status"])
PY
        )
        write_authoritative_status "$run_directory" "$experiment_id" "$failure_decision" \
            "$workflow_status" "$label_parser_status" "$core_status" "$failure_stage"
        write_failure_inventory "$run_directory" "$experiment_id"
        "$python_tool" "$validator" "$manifest" --config "$config" \
            --check-failure-run "$experiment_id" >/dev/null
    fi

    commit_run "$experiment_id"
    if [[ $workflow_status -ne 0 || $label_parser_status -ne 0 || $core_status -ne 0 ]]; then
        "$python_tool" "$validator" "$manifest" --config "$config" --require-committed \
            --check-failure-run "$experiment_id" >/dev/null
        archive_and_stop "$experiment_id"
    fi
    "$python_tool" "$validator" "$manifest" --config "$config" --require-committed \
        --check-run "$experiment_id" >/dev/null
    run_barriers "$experiment_id"
    echo "DONE $experiment_id"
done

"$python_tool" "$validator" "$manifest" --config "$config" \
    --require-committed --require-all-runs >/dev/null

analysis_exit=0
"$python_tool" "$analyzer" "$analysis_directory" --config "$config" \
    --manifest "$manifest" || analysis_exit=$?
if [[ -d "$analysis_directory" ]]; then
    git add -- "$analysis_directory"
    git commit -m "record G1 thermodynamic-label audit R1 analysis"
    assert_commit_scope "analysis/s1/g1_thermodynamic_label_audit_r1_20260806/"
fi
analysis_status=$(
    "$python_tool" - "$analysis_directory/summary.json" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["audit_status"])
PY
)
if [[ $analysis_exit -ne 0 || "$analysis_status" != accepted ]]; then
    echo "STOP final analysis status=$analysis_status exit=$analysis_exit" >&2
    exit 97
fi
echo "ACCEPTED S1-G1 thermodynamic-label audit R1; overall G1 is pending (2/6)"
