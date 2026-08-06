#!/usr/bin/env bash
set -euo pipefail
trap '' HUP

if [[ $# -gt 2 ]]; then
    echo "Usage: $0 [MANIFEST_TSV [CONFIG_JSON]]" >&2
    exit 2
fi

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
manifest=$(realpath "${1:-$project_root/config/S1_g1_thermodynamic_label_audit_r2_manifest.tsv}")
config=$(realpath "${2:-$project_root/config/S1_g1_thermodynamic_label_audit_r2.json}")
cd "$project_root"

if [[ -n $(git status --porcelain=v1 --untracked-files=all) ]]; then
    echo "Refusing thermodynamic-label R2 audit from a dirty worktree" >&2
    exit 2
fi

bootstrap_python=/usr/bin/python3
mapfile -d '' registered_paths < <(
    "$bootstrap_python" -s - "$config" <<'PY'
import json
import sys

config = json.load(open(sys.argv[1], encoding="utf-8"))
values = (
    config["runtime"]["tools"]["python"]["path"],
    config["execution"]["supervisor_state_directory"],
    config["execution"]["attempt_ledger_root"],
    config["execution"]["detachment_attestation_path"],
    config["execution"]["barrier_failure_root"],
)
for value in values:
    if "\0" in value:
        raise SystemExit("registered path contains a NUL")
    sys.stdout.write(value + "\0")
PY
)
if [[ ${#registered_paths[@]} -ne 5 ]]; then
    echo "R2 execution path registration is incomplete" >&2
    exit 2
fi
python_tool=${registered_paths[0]}
state_directory=${registered_paths[1]}
attempt_ledger_root=${registered_paths[2]}
detachment_attestation=${registered_paths[3]}
barrier_failure_root=${registered_paths[4]}

validator="$project_root/scripts/validate_s1_g1_thermodynamic_label_audit_r2.py"
parser="$project_root/scripts/parse_s1_g1_thermodynamic_labels_r2.py"
analyzer="$project_root/scripts/analyze_s1_g1_thermodynamic_label_audit_r2.py"
analysis_directory="$project_root/analysis/s1/g1_thermodynamic_label_audit_r2_20260806"

for evidence in "$state_directory/launch.json" "$state_directory/go.json"; do
    if [[ ! -f "$evidence" || -L "$evidence" ]]; then
        echo "Detached supervisor evidence is missing: $evidence" >&2
        exit 2
    fi
done
if [[ ! -f "$project_root/$detachment_attestation" || -L "$project_root/$detachment_attestation" ]]; then
    echo "Committed detachment attestation is missing: $detachment_attestation" >&2
    exit 2
fi
git ls-files --error-unmatch -- "$detachment_attestation" >/dev/null
git diff --quiet HEAD -- "$detachment_attestation" || {
    echo "Detachment attestation differs from HEAD" >&2
    exit 2
}
if [[ -e "$state_directory/terminal.json" ]]; then
    echo "Single-use detached supervisor already has a terminal record" >&2
    exit 97
fi

: "${M_OFDFT_G1_R2_SUPERVISOR_STATE_DIRECTORY:?missing supervisor state binding}"
: "${M_OFDFT_G1_R2_SUPERVISOR_PID:?missing supervisor PID binding}"
: "${M_OFDFT_G1_R2_SUPERVISOR_START_TIME_TICKS:?missing supervisor start-time binding}"
: "${M_OFDFT_G1_R2_BOOT_ID:?missing supervisor boot binding}"
: "${M_OFDFT_G1_R2_LAUNCH_SHA256:?missing supervisor launch binding}"
: "${M_OFDFT_G1_R2_GO_SHA256:?missing supervisor GO binding}"
"$python_tool" -s - "$state_directory" "$PPID" \
    "$M_OFDFT_G1_R2_SUPERVISOR_STATE_DIRECTORY" \
    "$M_OFDFT_G1_R2_SUPERVISOR_PID" \
    "$M_OFDFT_G1_R2_SUPERVISOR_START_TIME_TICKS" \
    "$M_OFDFT_G1_R2_BOOT_ID" \
    "$M_OFDFT_G1_R2_LAUNCH_SHA256" \
    "$M_OFDFT_G1_R2_GO_SHA256" <<'PY'
import hashlib
import json
import os
import sys
from pathlib import Path

state = Path(sys.argv[1]).resolve()
bash_parent = int(sys.argv[2])
bound_state = Path(sys.argv[3]).resolve()
bound_pid = int(sys.argv[4])
bound_start = int(sys.argv[5])
bound_boot = sys.argv[6]
bound_launch_hash = sys.argv[7]
bound_go_hash = sys.argv[8]
if state != bound_state:
    raise SystemExit("runner supervisor state binding differs")

def read(path):
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"non-object supervisor record: {path}")
    return value

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def process(pid):
    raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii").strip()
    close = raw.rfind(")")
    fields = raw[close + 2:].split()
    return {
        "ppid": int(fields[1]),
        "process_group_id": int(fields[2]),
        "session_id": int(fields[3]),
        "tty_nr": int(fields[4]),
        "start_time_ticks": int(fields[19]),
    }

launch_path = state / "launch.json"
go_path = state / "go.json"
launch = read(launch_path)
go = read(go_path)
identity = launch.get("process")
if not isinstance(identity, dict):
    raise SystemExit("runner launch process identity is missing")
observed = process(bound_pid)
boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
if not (
    bash_parent == bound_pid == identity.get("pid") == go.get("supervisor_pid")
    and bound_start == identity.get("start_time_ticks") == go.get("supervisor_start_time_ticks")
    and observed["start_time_ticks"] == bound_start
    and observed["session_id"] == bound_pid
    and observed["process_group_id"] == bound_pid
    and observed["tty_nr"] == 0
    and boot == bound_boot == launch.get("boot_id") == go.get("boot_id")
    and digest(launch_path) == bound_launch_hash == go.get("launch_sha256")
    and digest(go_path) == bound_go_hash
):
    raise SystemExit("runner is not a live child of the attested single-use supervisor")
if (state / "terminal.json").exists():
    raise SystemExit("runner supervisor already has a terminal receipt")
PY

mapfile -d '' run_plan < <(
    "$python_tool" -s - "$config" "$manifest" <<'PY'
import csv
import json
import sys

config = json.load(open(sys.argv[1], encoding="utf-8"))
with open(sys.argv[2], encoding="utf-8", newline="") as handle:
    rows = {row["experiment_id"]: row for row in csv.DictReader(handle, delimiter="\t")}
matrix = config["logical_run_matrix"]
logical_by_effective = {
    row["effective_experiment_id"]: row["logical_experiment_id"]
    for row in matrix
    if row["evidence_origin"] == "r2_executed"
}
for experiment_id in config["execution_order"]:
    row = rows[experiment_id]
    values = (
        experiment_id,
        logical_by_effective[experiment_id],
        row["input_directory"],
        row["material"],
        row["run_role"],
        row["execution_phase"],
    )
    for value in values:
        if "\0" in value:
            raise SystemExit("manifest contains a NUL")
        sys.stdout.write(value + "\0")
PY
)
if [[ ${#run_plan[@]} -ne 180 ]]; then
    echo "Execution plan does not contain exactly 30 six-field rows" >&2
    exit 2
fi

runtime_environment() {
    local run_directory=$1
    "$python_tool" -s - "$config" "$run_directory" <<'PY'
import json
import sys

config = json.load(open(sys.argv[1], encoding="utf-8"))
run = sys.argv[2]
runtime = config["runtime"]
replay = runtime["replay"]
reference = runtime["reference"]
tools = runtime["tools"]
wrappers = runtime["wrappers"]
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
    "M_OFDFT_MPIRUN_SCRIPT": runtime["wrappers"]["namespace_launcher"]["path"],
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
        for field in ("path", "realpath", "sha256"):
            values[f"M_OFDFT_{prefix}_{name.upper()}_{field.upper()}"] = source[name][field]
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

record_gate_failure() {
    local gate_id=$1
    local after_effective_id=$2
    local after_logical_id=$3
    local gate_status=$4
    shift 4
    if [[ ! "$gate_id" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
        echo "Invalid R2 gate identifier: $gate_id" >&2
        exit 98
    fi
    if [[ $gate_status -eq 0 ]]; then
        echo "Refusing zero exit code for failed R2 gate: $gate_id" >&2
        exit 98
    fi
    local failure_relative="$barrier_failure_root/$gate_id.json"
    "$python_tool" -s - "$project_root" "$config" "$manifest" "$failure_relative" \
        "$gate_id" "$after_effective_id" "$after_logical_id" "$gate_status" \
        "$state_directory" "$@" <<'PY'
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(sys.argv[1]).resolve()
config = Path(sys.argv[2]).resolve()
manifest = Path(sys.argv[3]).resolve()
output = root / sys.argv[4]
barrier_name, effective, logical = sys.argv[5:8]
exit_code = int(sys.argv[8])
state = Path(sys.argv[9]).resolve()
command = sys.argv[10:]

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

payload = {
    "schema_version": 1,
    "protocol_revision": "S1-G1-THERMODYNAMIC-LABEL-AUDIT-R2",
    "status": "barrier_failed",
    "created_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "barrier_name": barrier_name,
    "experiment_id": None if effective == "null" else effective,
    "logical_experiment_id": None if logical == "null" else logical,
    "command_argv": command,
    "exit_code": exit_code,
    "config_path": str(config.relative_to(root)),
    "config_sha256": digest(config),
    "manifest_path": str(manifest.relative_to(root)),
    "manifest_sha256": digest(manifest),
    "git_head_before_failure": subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip(),
    "supervisor_state_directory": str(state),
    "supervisor_launch_path": str(state / "launch.json"),
    "supervisor_launch_sha256": digest(state / "launch.json"),
    "retry_policy": "stop_after_exact_scope_commit_no_continue_or_retry",
}
output.parent.mkdir(parents=True, exist_ok=True)
descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try:
    data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short barrier-evidence write")
        view = view[written:]
    os.fsync(descriptor)
finally:
    os.close(descriptor)
directory = os.open(output.parent, os.O_RDONLY)
try:
    os.fsync(directory)
finally:
    os.close(directory)
PY
    git add -- "$project_root/$failure_relative"
    git commit -m "record failed G1 thermodynamic-label R2 barrier $gate_id"
    assert_commit_scope "$failure_relative"
    update_external_state "$after_effective_id" "$after_logical_id" "barrier_failed:$gate_id"
    echo "STOP: R2 barrier failure committed: $gate_id" >&2
    exit 97
}

run_gate() {
    local gate_id=$1
    local after_effective_id=$2
    local after_logical_id=$3
    shift 3
    local gate_state="$state_directory/barriers"
    mkdir -p "$gate_state"
    local stdout_path="$gate_state/$gate_id.stdout.txt"
    local stderr_path="$gate_state/$gate_id.stderr.txt"
    local gate_status=0
    set +e
    "$@" >"$stdout_path" 2>"$stderr_path"
    gate_status=$?
    set -e
    if [[ $gate_status -eq 0 ]]; then
        return 0
    fi
    record_gate_failure "$gate_id" "$after_effective_id" "$after_logical_id" \
        "$gate_status" "$@"
}

create_attempt_marker() {
    local experiment_id=$1
    local logical_id=$2
    local ledger_relative="$attempt_ledger_root/$experiment_id.json"
    "$python_tool" -s - "$project_root" "$config" "$manifest" "$state_directory" \
        "$ledger_relative" "$experiment_id" "$logical_id" <<'PY'
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(sys.argv[1]).resolve()
config = Path(sys.argv[2]).resolve()
manifest = Path(sys.argv[3]).resolve()
state = Path(sys.argv[4]).resolve()
ledger = root / sys.argv[5]
experiment_id = sys.argv[6]
logical_id = sys.argv[7]

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

launch_path = state / "launch.json"
launch = json.loads(launch_path.read_text(encoding="utf-8"))
process = launch["process"]
payload = {
    "schema_version": 1,
    "protocol_revision": "S1-G1-THERMODYNAMIC-LABEL-AUDIT-R2",
    "experiment_id": experiment_id,
    "logical_experiment_id": logical_id,
    "status": "formal_attempt_started",
    "retry_policy": "new_protocol_revision_and_new_experiment_ids_only",
    "created_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "config_path": str(config.relative_to(root)),
    "config_sha256": digest(config),
    "manifest_path": str(manifest.relative_to(root)),
    "manifest_sha256": digest(manifest),
    "git_head_before_attempt": subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip(),
    "supervisor_state_directory": str(state),
    "supervisor_launch_path": str(launch_path),
    "supervisor_launch_sha256": digest(launch_path),
    "supervisor_pid": int(process["pid"]),
    "supervisor_start_time_ticks": int(process["start_time_ticks"]),
    "boot_id": launch["boot_id"],
}
data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
external = state / "attempts" / f"{experiment_id}.json"
for path in (external, ledger):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short attempt-marker write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
PY
    git add -- "$project_root/$ledger_relative"
    git commit -m "start G1 thermodynamic-label audit R2 $experiment_id"
    assert_commit_scope "$ledger_relative"
    run_gate "attempt-marker-${experiment_id##*-}" "$experiment_id" "$logical_id" \
        "$python_tool" -s "$validator" "$manifest" --config "$config" --require-committed \
        --check-attempt-marker "$experiment_id"
}

update_external_state() {
    local experiment_id=$1
    local logical_id=$2
    local status=$3
    "$python_tool" -s - "$state_directory" "$experiment_id" "$logical_id" "$status" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

state = Path(sys.argv[1])
path = state / "current_run.json"
temporary = state / f".current_run.tmp-{os.getpid()}"
payload = {
    "experiment_id": sys.argv[2],
    "logical_experiment_id": sys.argv[3],
    "status": sys.argv[4],
    "updated_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
}
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, path)
PY
}

write_authoritative_status() {
    local run_directory=$1 experiment_id=$2 logical_id=$3 status=$4
    local workflow_exit=$5 parser_exit=$6 core_exit=$7 failure_stage=${8:-}
    "$python_tool" -s - "$run_directory" "$experiment_id" "$logical_id" "$status" \
        "$workflow_exit" "$parser_exit" "$core_exit" "$failure_stage" <<'PY'
import json
import sys
from pathlib import Path

payload = {
    "schema_version": 1,
    "protocol_revision": "S1-G1-THERMODYNAMIC-LABEL-AUDIT-R2",
    "experiment_id": sys.argv[2],
    "logical_experiment_id": sys.argv[3],
    "status": sys.argv[4],
    "authoritative_for_r2": True,
    "retry_policy": "new_protocol_revision_and_new_experiment_ids_only",
    "workflow_exit_code": int(sys.argv[5]),
    "parser_exit_code": int(sys.argv[6]),
    "core_validator_exit_code": int(sys.argv[7]),
}
if payload["status"] != "accepted":
    payload["failure_stage"] = sys.argv[8]
path = Path(sys.argv[1]) / "thermodynamic_label_status_r2.json"
with path.open("x", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY
}

write_noncore_failure_classification() {
    local run_directory=$1 experiment_id=$2 logical_id=$3 failure_stage=$4
    local component_exit=$5 diagnostic_path=$6
    "$python_tool" -s - "$run_directory" "$experiment_id" "$logical_id" \
        "$failure_stage" "$component_exit" "$diagnostic_path" <<'PY'
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "scripts"))
from validate_s1_g1_thermodynamic_label_audit_r2 import classify_noncore_failure

run = Path(sys.argv[1])
payload = classify_noncore_failure(
    run, sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]), Path(sys.argv[6])
)
with (run / "thermodynamic_label_failure_classification_r2.json").open(
    "x", encoding="utf-8"
) as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY
}

write_failure_inventory() {
    local run_directory=$1 experiment_id=$2 logical_id=$3
    "$python_tool" -s - "$run_directory" "$experiment_id" "$logical_id" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

run = Path(sys.argv[1])
output = run / "thermodynamic_label_failure_artifact_inventory_r2.json"
files = []
for path in sorted(run.rglob("*"), key=lambda value: str(value.relative_to(run))):
    if path == output:
        continue
    if path.is_symlink():
        raise SystemExit(f"symbolic failure artifact is forbidden: {path}")
    if path.is_file():
        files.append({
            "path": str(path.relative_to(run)),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size_bytes": path.stat().st_size,
        })
payload = {
    "schema_version": 1,
    "protocol_revision": "S1-G1-THERMODYNAMIC-LABEL-AUDIT-R2",
    "experiment_id": sys.argv[2],
    "logical_experiment_id": sys.argv[3],
    "files": files,
}
with output.open("x", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY
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
    git commit -m "record G1 thermodynamic-label audit R2 $experiment_id"
    assert_commit_scope "runs/$experiment_id/"
}

archive_and_stop() {
    local experiment_id=$1
    local logical_id=$2
    local failure_commit
    failure_commit=$(git rev-parse HEAD)
    local archive_relative="failed_runs/runtime_relocation/$experiment_id/attempt-${failure_commit:0:12}"
    if [[ -e "$project_root/$archive_relative" ]]; then
        echo "Refusing to overwrite failed-attempt archive: $archive_relative" >&2
        exit 98
    fi
    mkdir -p "$project_root/failed_runs/runtime_relocation/$experiment_id"
    git mv "$project_root/runs/$experiment_id" "$project_root/$archive_relative"
    git commit -m "archive failed G1 thermodynamic-label audit R2 $experiment_id"
    run_gate "failure-archive-${experiment_id##*-}" "$experiment_id" "$logical_id" \
        "$python_tool" -s "$validator" "$manifest" --config "$config" --require-committed \
        --check-failure-archives "$experiment_id"
    update_external_state "$experiment_id" "$logical_id" archived_stopped
    echo "STOP $experiment_id: failed formal R2 attempt committed and archived; retry forbidden" >&2
    exit 97
}

run_barriers() {
    local effective_id=$1
    local logical_id=$2
    local quarter_id=
    case "$logical_id" in
        S1-20260806-007) quarter_id=S1-20260806-021 ;;
        S1-20260806-010) quarter_id=S1-20260806-024 ;;
        S1-20260806-013) quarter_id=S1-20260806-027 ;;
        S1-20260806-014) quarter_id=S1-20260806-028 ;;
        S1-20260806-017) quarter_id=S1-20260806-031 ;;
        S1-20260806-020) quarter_id=S1-20260806-034 ;;
        S1-20260806-022|S1-20260806-023|S1-20260806-025|S1-20260806-026|S1-20260806-029|S1-20260806-030|S1-20260806-032|S1-20260806-033)
            quarter_id=$logical_id ;;
    esac
    if [[ -n "$quarter_id" ]]; then
        run_gate "half-quarter-${quarter_id##*-}-after-${effective_id##*-}" \
            "$effective_id" "$logical_id" \
            "$python_tool" -s "$validator" "$manifest" --config "$config" \
            --require-committed --require-half-quarter-pair "$quarter_id"
    fi
    case "$logical_id" in
        S1-20260806-013)
            run_gate "eos-al-standard-half-after-${effective_id##*-}" \
                "$effective_id" "$logical_id" \
                "$python_tool" -s "$validator" "$manifest" --config "$config" --require-committed \
                --require-adjacent-eos al standard half ;;
        S1-20260806-020)
            run_gate "eos-mg-standard-half-after-${effective_id##*-}" \
                "$effective_id" "$logical_id" \
                "$python_tool" -s "$validator" "$manifest" --config "$config" --require-committed \
                --require-adjacent-eos mg standard half ;;
        S1-20260806-026)
            run_gate "eos-al-half-quarter-after-${effective_id##*-}" \
                "$effective_id" "$logical_id" \
                "$python_tool" -s "$validator" "$manifest" --config "$config" --require-committed \
                --require-adjacent-eos al half quarter ;;
        S1-20260806-033)
            run_gate "eos-mg-half-quarter-after-${effective_id##*-}" \
                "$effective_id" "$logical_id" \
                "$python_tool" -s "$validator" "$manifest" --config "$config" --require-committed \
                --require-adjacent-eos mg half quarter ;;
    esac
}

run_gate imported-p0-before-041 null null \
    "$python_tool" -s "$validator" "$manifest" --config "$config" \
    --require-committed --require-pilot-gate

for ((offset = 0; offset < ${#run_plan[@]}; offset += 6)); do
    experiment_id=${run_plan[$offset]}
    logical_id=${run_plan[$((offset + 1))]}
    input_directory="$project_root/${run_plan[$((offset + 2))]}"
    material=${run_plan[$((offset + 3))]}
    role=${run_plan[$((offset + 4))]}
    phase=${run_plan[$((offset + 5))]}
    run_directory="$project_root/runs/$experiment_id"
    archive_root="$project_root/failed_runs/runtime_relocation/$experiment_id"
    ledger="$project_root/$attempt_ledger_root/$experiment_id.json"
    external_marker="$state_directory/attempts/$experiment_id.json"

    if [[ -e "$archive_root" ]]; then
        echo "STOP $experiment_id: a failed-attempt archive exists; retry forbidden" >&2
        exit 97
    fi
    if [[ -e "$run_directory" ]]; then
        echo "STOP $experiment_id: run path already exists; single-use R2 execution cannot resume" >&2
        exit 97
    fi
    if [[ -e "$ledger" || -e "$external_marker" ]]; then
        echo "STOP $experiment_id: attempt marker exists without accepted run; solver restart forbidden" >&2
        exit 97
    fi

    create_attempt_marker "$experiment_id" "$logical_id"
    update_external_state "$experiment_id" "$logical_id" solver_starting
    echo "START $experiment_id logical=$logical_id phase=$phase material=$material role=$role"
    mapfile -d '' env_args < <(runtime_environment "$run_directory")
    workflow_status=0
    env -i "${env_args[@]}" scripts/run_s1_single.sh \
        "$experiment_id" "$input_directory" </dev/null || workflow_status=$?
    update_external_state "$experiment_id" "$logical_id" workflow_finished

    label_parser_status=97
    if [[ $workflow_status -eq 0 ]]; then
        label_parser_status=0
        "$python_tool" -s "$parser" "$run_directory" --config "$config" \
            --manifest "$manifest" --write \
            >"$run_directory/thermodynamic_label_parser.stdout.txt" \
            2>"$run_directory/thermodynamic_label_parser.stderr.txt" \
            || label_parser_status=$?
    fi
    core_status=97
    if [[ $workflow_status -eq 0 && $label_parser_status -eq 0 ]]; then
        core_status=0
        "$python_tool" -s "$validator" "$manifest" --config "$config" \
            --check-run-core "$experiment_id" --write-core-failure-evidence \
            >"$run_directory/core_validator.stdout.txt" \
            2>"$run_directory/core_validator.stderr.txt" \
            || core_status=$?
    fi

    if [[ $workflow_status -eq 0 && $label_parser_status -eq 0 && $core_status -eq 0 ]]; then
        "$python_tool" -s "$validator" "$manifest" --config "$config" \
            --write-run-evidence "$experiment_id" >/dev/null
        write_authoritative_status "$run_directory" "$experiment_id" "$logical_id" accepted 0 0 0
    else
        failure_stage=workflow
        [[ $workflow_status -eq 0 ]] && failure_stage=thermodynamic_label_parser
        [[ $workflow_status -eq 0 && $label_parser_status -eq 0 ]] && failure_stage=core_validator
        if [[ ! -f "$run_directory/run_status.json" ]]; then
            mkdir -p "$run_directory"
            "$python_tool" -s scripts/write_s1_runtime_relocation_status.py "$run_directory" \
                --experiment-id "$experiment_id" --code-commit "$(git rev-parse HEAD)" \
                --workflow-exit "$workflow_status" --invocation-exit "$workflow_status" \
                --parser-exit "$label_parser_status" --core-validation-exit "$core_status" \
                --setup-completed false --runtime-relocation-mode true \
                --failure-stage "$failure_stage" --run-only >/dev/null
        fi
        if [[ "$failure_stage" == workflow ]]; then
            workflow_diagnostic="$run_directory/outer_workflow_failure.txt"
            printf 'run_s1_single workflow exit code: %s\n' "$workflow_status" >"$workflow_diagnostic"
            write_noncore_failure_classification "$run_directory" "$experiment_id" "$logical_id" \
                "$failure_stage" "$workflow_status" "$workflow_diagnostic"
        elif [[ "$failure_stage" == thermodynamic_label_parser ]]; then
            write_noncore_failure_classification "$run_directory" "$experiment_id" "$logical_id" \
                "$failure_stage" "$label_parser_status" \
                "$run_directory/thermodynamic_label_parser.stderr.txt"
        fi
        failure_decision=$(
            "$python_tool" -s - "$run_directory/thermodynamic_label_failure_classification_r2.json" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["status"])
PY
        )
        write_authoritative_status "$run_directory" "$experiment_id" "$logical_id" \
            "$failure_decision" "$workflow_status" "$label_parser_status" "$core_status" "$failure_stage"
        write_failure_inventory "$run_directory" "$experiment_id" "$logical_id"
    fi

    commit_run "$experiment_id"
    if [[ $workflow_status -ne 0 || $label_parser_status -ne 0 || $core_status -ne 0 ]]; then
        archive_and_stop "$experiment_id" "$logical_id"
    fi
    run_gate "accepted-run-${experiment_id##*-}" "$experiment_id" "$logical_id" \
        "$python_tool" -s "$validator" "$manifest" --config "$config" --require-committed \
        --check-run "$experiment_id"
    update_external_state "$experiment_id" "$logical_id" accepted_committed
    run_barriers "$experiment_id" "$logical_id"
    if [[ "$experiment_id" == S1-20260806-042 ]]; then
        run_gate k-gate-after-042 "$experiment_id" "$logical_id" \
            "$python_tool" -s "$validator" "$manifest" --config "$config" \
            --require-committed --require-k-gate
    fi
    echo "DONE $experiment_id logical=$logical_id"
done

run_gate final-all-after-070 S1-20260806-070 S1-20260806-033 \
    "$python_tool" -s "$validator" "$manifest" --config "$config" \
    --require-committed --require-all-runs

analysis_argv=(
    "$python_tool" -s "$analyzer" "$analysis_directory" --config "$config"
    --manifest "$manifest"
)
analysis_gate_state="$state_directory/barriers"
mkdir -p "$analysis_gate_state"
analysis_exit=0
"${analysis_argv[@]}" \
    >"$analysis_gate_state/final-analysis.stdout.txt" \
    2>"$analysis_gate_state/final-analysis.stderr.txt" || analysis_exit=$?
if [[ -d "$analysis_directory" ]]; then
    git add -- "$analysis_directory"
    git commit -m "record G1 thermodynamic-label audit R2 analysis"
    assert_commit_scope "analysis/s1/g1_thermodynamic_label_audit_r2_20260806/"
fi
if [[ $analysis_exit -ne 0 ]]; then
    record_gate_failure final-analysis S1-20260806-070 S1-20260806-033 \
        "$analysis_exit" "${analysis_argv[@]}"
fi
run_gate final-analysis-status S1-20260806-070 S1-20260806-033 \
    "$python_tool" -s "$validator" "$manifest" --config "$config" \
    --require-committed --check-analysis-summary
echo "SCIENTIFIC ANALYSIS ACCEPTED; awaiting detached supervisor completion receipt"
