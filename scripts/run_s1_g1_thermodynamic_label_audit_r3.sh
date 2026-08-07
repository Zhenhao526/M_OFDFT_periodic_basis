#!/usr/bin/env bash
set -euo pipefail
trap '' HUP

if [[ $# -ne 5 ]]; then
    echo "Usage: /usr/bin/bash /proc/self/fd/200 PROJECT_ROOT CANONICAL_MANIFEST CANONICAL_CONFIG /proc/self/fd/201 /proc/self/fd/202" >&2
    exit 2
fi

bootstrap_python=/usr/bin/python3
runner_fd_path=$0
project_root=$1
canonical_manifest=$2
canonical_config=$3
frozen_manifest=$4
frozen_config=$5

: "${M_OFDFT_G1_R3_SUPERVISOR_STATE_DIRECTORY:?missing supervisor state binding}"
: "${M_OFDFT_G1_R3_SUPERVISOR_PID:?missing supervisor PID binding}"
: "${M_OFDFT_G1_R3_SUPERVISOR_START_TIME_TICKS:?missing supervisor start-time binding}"
: "${M_OFDFT_G1_R3_BOOT_ID:?missing supervisor boot binding}"
: "${M_OFDFT_G1_R3_LAUNCH_SHA256:?missing supervisor launch binding}"
: "${M_OFDFT_G1_R3_GO_SHA256:?missing supervisor GO binding}"

# This fixed bootstrap is the only executable used before the sealed runner,
# config, manifest, canonical provenance twins, exact argv, and GO are checked.
# Command substitution propagates a failed preflight; process substitution does not.
preflight_output=$("$bootstrap_python" -s - \
    "$runner_fd_path" "$project_root" "$canonical_manifest" "$canonical_config" \
    "$frozen_manifest" "$frozen_config" "$$" "$PPID" \
    "$M_OFDFT_G1_R3_SUPERVISOR_STATE_DIRECTORY" \
    "$M_OFDFT_G1_R3_SUPERVISOR_PID" \
    "$M_OFDFT_G1_R3_SUPERVISOR_START_TIME_TICKS" \
    "$M_OFDFT_G1_R3_BOOT_ID" \
    "$M_OFDFT_G1_R3_LAUNCH_SHA256" \
    "$M_OFDFT_G1_R3_GO_SHA256" <<'PY'
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath

PROTOCOL = "S1-G1-THERMODYNAMIC-LABEL-AUDIT-R3"
RUNNER_RELATIVE = "scripts/run_s1_g1_thermodynamic_label_audit_r3.sh"
MANIFEST_RELATIVE = "config/S1_g1_thermodynamic_label_audit_r3_manifest.tsv"
CONFIG_RELATIVE = "config/S1_g1_thermodynamic_label_audit_r3.json"
FIXED_FDS = {"runner": 200, "manifest": 201, "config": 202}
PROC_PATHS = {name: f"/proc/self/fd/{fd}" for name, fd in FIXED_FDS.items()}
SEAL_NAMES = ["F_SEAL_SEAL", "F_SEAL_SHRINK", "F_SEAL_GROW", "F_SEAL_WRITE"]
SEAL_MASK = 15
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
UTC = re.compile(
    r"20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,9})?Z\Z"
)

(
    runner_fd_path,
    project_root_value,
    canonical_manifest_value,
    canonical_config_value,
    frozen_manifest_path,
    frozen_config_path,
    runner_bash_pid_value,
    supervisor_parent_value,
    bound_state_value,
    bound_pid_value,
    bound_start_value,
    bound_boot,
    bound_launch_hash,
    bound_go_hash,
) = sys.argv[1:]
runner_bash_pid = int(runner_bash_pid_value)
supervisor_parent = int(supervisor_parent_value)
bound_pid = int(bound_pid_value)
bound_start = int(bound_start_value)
project_root = Path(project_root_value)
state = Path(bound_state_value)

if sys.executable != "/usr/bin/python3" or sys.flags.no_user_site != 1:
    raise SystemExit("runner bootstrap Python is not fixed /usr/bin/python3 -s")
if (
    runner_fd_path != PROC_PATHS["runner"]
    or frozen_manifest_path != PROC_PATHS["manifest"]
    or frozen_config_path != PROC_PATHS["config"]
):
    raise SystemExit("runner sealed FD argv differs from the frozen 200/201/202 registration")
if (
    not project_root.is_absolute()
    or str(project_root.resolve()) != project_root_value
    or not state.is_absolute()
    or str(state.resolve()) != bound_state_value
):
    raise SystemExit("runner project/state path is not absolute canonical")

def canonical(payload):
    return (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()

def stable_bytes(path):
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise SystemExit("runner stable reads require O_NOFOLLOW")
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0) | nofollow
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise SystemExit(f"runner provenance path is not regular: {path}")
        chunks = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity = (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
        "st_rdev", "st_size", "st_mtime_ns", "st_ctime_ns",
    )
    if any(getattr(before, name) != getattr(after, name) for name in identity):
        raise SystemExit(f"runner provenance path changed while reading: {path}")
    data = b"".join(chunks)
    if len(data) != before.st_size:
        raise SystemExit(f"runner provenance path was read short: {path}")
    return data, hashlib.sha256(data).hexdigest()

def read_stable(path):
    data, digest = stable_bytes(path)
    value = json.loads(data.decode("utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"non-object supervisor record: {path}")
    return value, digest

def sealed_bytes(name):
    descriptor = FIXED_FDS[name]
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode):
        raise SystemExit(f"sealed execution {name} FD is not regular")
    kernel_mask = 0
    for seal_name in SEAL_NAMES:
        value = getattr(fcntl, seal_name, None)
        if type(value) is not int:
            raise SystemExit(f"sealed execution constant is unavailable: {seal_name}")
        kernel_mask |= value
    if kernel_mask != SEAL_MASK or fcntl.fcntl(descriptor, fcntl.F_GET_SEALS) != SEAL_MASK:
        raise SystemExit(f"sealed execution {name} seal mask differs")
    chunks = []
    offset = 0
    while offset < before.st_size:
        block = os.pread(descriptor, min(1024 * 1024, before.st_size - offset), offset)
        if not block:
            break
        chunks.append(block)
        offset += len(block)
    after = os.fstat(descriptor)
    identity = (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
        "st_rdev", "st_size", "st_mtime_ns", "st_ctime_ns",
    )
    if (
        any(getattr(before, field) != getattr(after, field) for field in identity)
        or offset != before.st_size
    ):
        raise SystemExit(f"sealed execution {name} FD changed or was read short")
    data = b"".join(chunks)
    return data, hashlib.sha256(data).hexdigest()

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

def git(*arguments):
    return subprocess.check_output(
        ["git", "-C", str(project_root), *arguments], text=True
    ).strip()

launch_path = state / "launch.json"
go_path = state / "go.json"
launch, launch_hash = read_stable(launch_path)
go, go_hash = read_stable(go_path)
if (
    launch.get("protocol_revision") != PROTOCOL
    or launch.get("status") != "waiting_for_detachment_attestation"
):
    raise SystemExit("runner launch protocol/status differs")
if (
    not HEX64.fullmatch(bound_launch_hash)
    or not HEX64.fullmatch(bound_go_hash)
):
    raise SystemExit("runner bound launch/GO SHA-256 is invalid")
sealed_payloads = {}
sealed_hashes = {}
for name in FIXED_FDS:
    sealed_payloads[name], sealed_hashes[name] = sealed_bytes(name)
try:
    config_payload = json.loads(sealed_payloads["config"].decode("utf-8"))
except (UnicodeDecodeError, json.JSONDecodeError) as error:
    raise SystemExit("sealed execution config is not UTF-8 JSON") from error
if not isinstance(config_payload, dict):
    raise SystemExit("sealed execution config root is not an object")
if (
    config_payload.get("protocol_revision") != PROTOCOL
    or config_payload.get("status") != "preregistered"
):
    raise SystemExit("sealed execution config registration differs")
execution = config_payload.get("execution")
runtime = config_payload.get("runtime")
if not isinstance(execution, dict) or not isinstance(runtime, dict):
    raise SystemExit("sealed execution config runtime/execution table is missing")
sealed_contract = {
    "mode": "linux_memfd_sealed_v1",
    "fixed_fds_exact": FIXED_FDS,
    "proc_paths_exact": PROC_PATHS,
    "seal_mask_exact": SEAL_MASK,
    "seal_names_exact": SEAL_NAMES,
    "popen_pass_fds_exact": list(FIXED_FDS.values()),
    "registered_bash_executes_runner_fd": True,
    "scientific_config_manifest_from_sealed_fds_required": True,
    "canonical_paths_provenance_only": True,
}
if execution.get("sealed_execution_inputs") != sealed_contract:
    raise SystemExit("runner sealed-execution-input configuration contract differs")
if execution.get("supervisor_state_directory") != str(state):
    raise SystemExit("runner frozen supervisor state directory differs")

canonical_paths = {
    "runner": project_root / RUNNER_RELATIVE,
    "manifest": project_root / MANIFEST_RELATIVE,
    "config": project_root / CONFIG_RELATIVE,
}
if (
    canonical_manifest_value != str(canonical_paths["manifest"])
    or canonical_config_value != str(canonical_paths["config"])
):
    raise SystemExit("runner canonical manifest/config argv differs")
canonical_hashes = {}
for name, path in canonical_paths.items():
    _, canonical_hashes[name] = stable_bytes(path)
    if canonical_hashes[name] != sealed_hashes[name]:
        raise SystemExit(f"canonical {name} SHA-256 differs from sealed execution input")

registered_files = {
    "config_path": CONFIG_RELATIVE,
    "config_sha256": sealed_hashes["config"],
    "manifest_path": MANIFEST_RELATIVE,
    "manifest_sha256": sealed_hashes["manifest"],
    "runner_path": RUNNER_RELATIVE,
    "runner_sha256": sealed_hashes["runner"],
}
sealed_record = {
    "mode": "linux_memfd_sealed_v1",
    "seal_mask": SEAL_MASK,
    "seal_names": SEAL_NAMES,
    "pass_fds": list(FIXED_FDS.values()),
    "inputs": {
        name: {
            "fd": FIXED_FDS[name],
            "proc_path": PROC_PATHS[name],
            "canonical_path": str(canonical_paths[name]),
            "sha256": sealed_hashes[name],
        }
        for name in FIXED_FDS
    },
}
sealed_record_sha256 = hashlib.sha256(canonical(sealed_record)).hexdigest()
if launch.get("registered_files") != registered_files:
    raise SystemExit("runner launch registered files differ from sealed/canonical bytes")
if launch.get("sealed_execution_inputs") != sealed_record:
    raise SystemExit("runner launch sealed-execution-input record differs")

tools = runtime.get("tools")
if not isinstance(tools, dict):
    raise SystemExit("runner frozen tool table is missing")
python_registration = tools.get("python")
bash_registration = tools.get("bash")
if not isinstance(python_registration, dict) or not isinstance(bash_registration, dict):
    raise SystemExit("runner frozen Python/Bash registration is missing")
if python_registration.get("path") != "/usr/bin/python3":
    raise SystemExit("runner registered Python is not fixed /usr/bin/python3")
for label, registration in (("python", python_registration), ("bash", bash_registration)):
    path_value = registration.get("path")
    realpath_value = registration.get("realpath")
    sha256_value = registration.get("sha256")
    if (
        not isinstance(path_value, str)
        or not Path(path_value).is_absolute()
        or not isinstance(realpath_value, str)
        or not Path(realpath_value).is_absolute()
        or not isinstance(sha256_value, str)
        or not HEX64.fullmatch(sha256_value)
        or str(Path(path_value).resolve()) != realpath_value
        or stable_bytes(Path(realpath_value))[1] != sha256_value
    ):
        raise SystemExit(f"runner registered {label} identity differs")

expected_runner_argv = [
    bash_registration["path"],
    PROC_PATHS["runner"],
    str(project_root),
    str(canonical_paths["manifest"]),
    str(canonical_paths["config"]),
    PROC_PATHS["manifest"],
    PROC_PATHS["config"],
]
try:
    actual_runner_argv = [
        item.decode("utf-8")
        for item in Path(f"/proc/{runner_bash_pid}/cmdline").read_bytes().split(b"\0")
        if item
    ]
except UnicodeDecodeError as error:
    raise SystemExit("runner live argv is not UTF-8") from error
if (
    len(expected_runner_argv) != 7
    or launch.get("runner_argv") != expected_runner_argv
    or actual_runner_argv != expected_runner_argv
    or str(Path(f"/proc/{runner_bash_pid}/exe").resolve())
    != bash_registration["realpath"]
):
    raise SystemExit("runner launch/actual argv is not the exact seven-item registration")

identity = launch.get("process")
if not isinstance(identity, dict):
    raise SystemExit("runner launch process identity is missing")
go_keys = {
    "schema_version",
    "protocol_revision",
    "status",
    "launch_sha256",
    "boot_id",
    "supervisor_pid",
    "supervisor_start_time_ticks",
    "attestation_path",
    "attestation_sha256",
    "git_head",
    "registered_files",
    "sealed_execution_inputs_sha256",
    "created_utc",
}
registered_attestation = execution.get("detachment_attestation_path")
if not isinstance(registered_attestation, str) or not registered_attestation:
    raise SystemExit("runner frozen attestation path is missing")
attestation_relative = Path(registered_attestation)
if (
    attestation_relative.is_absolute()
    or ".." in attestation_relative.parts
    or PurePosixPath(registered_attestation).as_posix() != registered_attestation
):
    raise SystemExit("runner frozen attestation path is invalid")
attestation, attestation_hash = read_stable(project_root / attestation_relative)
git_head = git("rev-parse", "HEAD")
if not HEX40.fullmatch(git_head):
    raise SystemExit("runner current Git HEAD is invalid")
introduction_commits = [
    value
    for value in git(
        "log", "--format=%H", "--diff-filter=A", "--", registered_attestation
    ).splitlines()
    if value
]
if introduction_commits != [git_head]:
    raise SystemExit("runner GO Git HEAD is not the unique detachment introduction commit")
created_utc = go.get("created_utc")
if (
    set(go) != go_keys
    or type(go.get("schema_version")) is not int
    or go.get("schema_version") != 1
    or go.get("protocol_revision") != PROTOCOL
    or go.get("status") != "go"
    or not isinstance(created_utc, str)
    or not UTC.fullmatch(created_utc)
    or go.get("registered_files") != registered_files
    or go.get("sealed_execution_inputs_sha256") != sealed_record_sha256
    or go.get("attestation_path") != registered_attestation
    or go.get("attestation_sha256") != attestation_hash
    or go.get("git_head") != git_head
    or attestation.get("status") != "accepted"
):
    raise SystemExit("runner GO exact registration differs")
observed = process(bound_pid)
runner_observed = process(runner_bash_pid)
boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
if not (
    supervisor_parent == bound_pid == identity.get("pid") == go.get("supervisor_pid")
    and runner_observed["ppid"] == bound_pid
    and runner_observed["session_id"] == bound_pid
    and runner_observed["process_group_id"] == bound_pid
    and runner_observed["tty_nr"] == 0
    and bound_start == identity.get("start_time_ticks") == go.get("supervisor_start_time_ticks")
    and observed["start_time_ticks"] == bound_start
    and observed["session_id"] == bound_pid
    and observed["process_group_id"] == bound_pid
    and observed["tty_nr"] == 0
    and boot == bound_boot == launch.get("boot_id") == go.get("boot_id")
    and launch_hash == bound_launch_hash == go.get("launch_sha256")
    and go_hash == bound_go_hash
):
    raise SystemExit("runner is not a live child of the attested single-use supervisor")
if launch.get("project_root") != str(project_root) or launch.get("state_directory") != str(state):
    raise SystemExit("runner launch project/state registration differs")
if git("status", "--porcelain=v1", "--untracked-files=all"):
    raise SystemExit("refusing thermodynamic-label R3 audit from a dirty worktree")
for relative in (RUNNER_RELATIVE, MANIFEST_RELATIVE, CONFIG_RELATIVE, registered_attestation):
    subprocess.check_call(
        ["git", "-C", str(project_root), "ls-files", "--error-unmatch", "--", relative],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if subprocess.run(
        ["git", "-C", str(project_root), "diff", "--quiet", "HEAD", "--", relative]
    ).returncode != 0:
        raise SystemExit(f"runner canonical provenance differs from HEAD: {relative}")

go_accepted = []
journal_raw, _ = stable_bytes(state / "journal.jsonl")
for line in journal_raw.decode("utf-8").splitlines():
    event = json.loads(line)
    if isinstance(event, dict) and event.get("event") == "go_accepted":
        go_accepted.append(event)
if (
    len(go_accepted) != 1
    or set(go_accepted[0]) != {"event", "pid", "utc", "git_head", "go_sha256"}
    or go_accepted[0].get("pid") != bound_pid
    or go_accepted[0].get("git_head") != git_head
    or go_accepted[0].get("go_sha256") != bound_go_hash
):
    raise SystemExit("runner journal does not bind the validated GO bytes")
if (state / "terminal.json").exists() or (state / "terminal.json").is_symlink():
    raise SystemExit("runner supervisor already has a terminal receipt")

values = (
    python_registration["path"],
    str(state),
    execution.get("attempt_ledger_root"),
    registered_attestation,
    execution.get("barrier_failure_root"),
    bound_go_hash,
    git_head,
)
for value in values:
    if not isinstance(value, str) or not value or "\0" in value or "\n" in value:
        raise SystemExit("runner registered path/GO binding is invalid")
print("\n".join(values))
PY
)
mapfile -t registered_paths <<<"$preflight_output"
if [[ ${#registered_paths[@]} -ne 7 ]]; then
    echo "R3 execution path/GO registration is incomplete" >&2
    exit 2
fi
python_tool=${registered_paths[0]}
state_directory=${registered_paths[1]}
attempt_ledger_root=${registered_paths[2]}
detachment_attestation=${registered_paths[3]}
barrier_failure_root=${registered_paths[4]}
go_sha256=${registered_paths[5]}
go_git_head=${registered_paths[6]}
readonly bootstrap_python runner_fd_path project_root canonical_manifest canonical_config
readonly frozen_manifest frozen_config python_tool state_directory attempt_ledger_root
readonly detachment_attestation barrier_failure_root go_sha256 go_git_head

cd "$project_root"
validator="$project_root/scripts/validate_s1_g1_thermodynamic_label_audit_r3.py"
parser="$project_root/scripts/parse_s1_g1_thermodynamic_labels_r3.py"
analyzer="$project_root/scripts/analyze_s1_g1_thermodynamic_label_audit_r3.py"
analysis_directory="$project_root/analysis/s1/g1_thermodynamic_label_audit_r3_20260807"

mapfile -d '' run_plan < <(
    "$python_tool" -s - "$frozen_config" "$frozen_manifest" <<'PY'
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
    if row["evidence_origin"] == "r3_executed"
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
if [[ ${#run_plan[@]} -ne 240 ]]; then
    echo "Execution plan does not contain exactly 40 six-field rows" >&2
    exit 2
fi

runtime_environment() {
    local run_directory=$1
    "$python_tool" -s - "$frozen_config" "$run_directory" <<'PY'
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
        echo "Invalid R3 gate identifier: $gate_id" >&2
        exit 98
    fi
    if [[ $gate_status -eq 0 ]]; then
        echo "Refusing zero exit code for failed R3 gate: $gate_id" >&2
        exit 98
    fi
    local failure_relative="$barrier_failure_root/$gate_id.json"
    "$python_tool" -s - "$project_root" "$canonical_config" "$canonical_manifest" "$failure_relative" \
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
    "protocol_revision": "S1-G1-THERMODYNAMIC-LABEL-AUDIT-R3",
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
    git commit -m "record failed G1 thermodynamic-label R3 barrier $gate_id"
    assert_commit_scope "$failure_relative"
    update_external_state "$after_effective_id" "$after_logical_id" "barrier_failed:$gate_id"
    echo "STOP: R3 barrier failure committed: $gate_id" >&2
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
    "$python_tool" -s - "$project_root" "$canonical_config" "$canonical_manifest" \
        "$state_directory" "$ledger_relative" "$experiment_id" "$logical_id" \
        "$go_sha256" "$go_git_head" <<'PY'
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
fixed_go_sha256 = sys.argv[8]
fixed_go_git_head = sys.argv[9]

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

launch_path = state / "launch.json"
launch = json.loads(launch_path.read_text(encoding="utf-8"))
process = launch["process"]
go_path = state / "go.json"
go = json.loads(go_path.read_text(encoding="utf-8"))
if digest(go_path) != fixed_go_sha256 or go.get("git_head") != fixed_go_git_head:
    raise SystemExit("attempt marker fixed GO bytes/head differ")
payload = {
    "schema_version": 1,
    "protocol_revision": "S1-G1-THERMODYNAMIC-LABEL-AUDIT-R3",
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
    "supervisor_go_path": str(go_path),
    "supervisor_go_sha256": fixed_go_sha256,
    "go_git_head": fixed_go_git_head,
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
    git commit -m "start G1 thermodynamic-label audit R3 $experiment_id"
    assert_commit_scope "$ledger_relative"
    run_gate "attempt-marker-${experiment_id##*-}" "$experiment_id" "$logical_id" \
        "$python_tool" -s "$validator" "$canonical_manifest" --config "$canonical_config" \
        --scientific-config "$frozen_config" --scientific-manifest "$frozen_manifest" --require-committed \
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
    "protocol_revision": "S1-G1-THERMODYNAMIC-LABEL-AUDIT-R3",
    "experiment_id": sys.argv[2],
    "logical_experiment_id": sys.argv[3],
    "status": sys.argv[4],
    "authoritative_for_r3": True,
    "retry_policy": "new_protocol_revision_and_new_experiment_ids_only",
    "workflow_exit_code": int(sys.argv[5]),
    "parser_exit_code": int(sys.argv[6]),
    "core_validator_exit_code": int(sys.argv[7]),
}
if payload["status"] != "accepted":
    payload["failure_stage"] = sys.argv[8]
path = Path(sys.argv[1]) / "thermodynamic_label_status_r3.json"
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
from validate_s1_g1_thermodynamic_label_audit_r3 import classify_noncore_failure

run = Path(sys.argv[1])
payload = classify_noncore_failure(
    run, sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]), Path(sys.argv[6])
)
with (run / "thermodynamic_label_failure_classification_r3.json").open(
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
output = run / "thermodynamic_label_failure_artifact_inventory_r3.json"
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
    "protocol_revision": "S1-G1-THERMODYNAMIC-LABEL-AUDIT-R3",
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
    git commit -m "record G1 thermodynamic-label audit R3 $experiment_id"
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
    git commit -m "archive failed G1 thermodynamic-label audit R3 $experiment_id"
    run_gate "failure-archive-${experiment_id##*-}" "$experiment_id" "$logical_id" \
        "$python_tool" -s "$validator" "$canonical_manifest" --config "$canonical_config" \
        --scientific-config "$frozen_config" --scientific-manifest "$frozen_manifest" --require-committed \
        --check-failure-archives "$experiment_id"
    update_external_state "$experiment_id" "$logical_id" archived_stopped
    echo "STOP $experiment_id: failed formal R3 attempt committed and archived; retry forbidden" >&2
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
            "$python_tool" -s "$validator" "$canonical_manifest" --config "$canonical_config" \
            --scientific-config "$frozen_config" --scientific-manifest "$frozen_manifest" \
            --require-committed --require-half-quarter-pair "$quarter_id"
    fi
    case "$logical_id" in
        S1-20260806-013)
            run_gate "eos-al-standard-half-after-${effective_id##*-}" \
                "$effective_id" "$logical_id" \
                "$python_tool" -s "$validator" "$canonical_manifest" --config "$canonical_config" \
                --scientific-config "$frozen_config" --scientific-manifest "$frozen_manifest" --require-committed \
                --require-adjacent-eos al standard half ;;
        S1-20260806-020)
            run_gate "eos-mg-standard-half-after-${effective_id##*-}" \
                "$effective_id" "$logical_id" \
                "$python_tool" -s "$validator" "$canonical_manifest" --config "$canonical_config" \
                --scientific-config "$frozen_config" --scientific-manifest "$frozen_manifest" --require-committed \
                --require-adjacent-eos mg standard half ;;
        S1-20260806-026)
            run_gate "eos-al-half-quarter-after-${effective_id##*-}" \
                "$effective_id" "$logical_id" \
                "$python_tool" -s "$validator" "$canonical_manifest" --config "$canonical_config" \
                --scientific-config "$frozen_config" --scientific-manifest "$frozen_manifest" --require-committed \
                --require-adjacent-eos al half quarter ;;
        S1-20260806-033)
            run_gate "eos-mg-half-quarter-after-${effective_id##*-}" \
                "$effective_id" "$logical_id" \
                "$python_tool" -s "$validator" "$canonical_manifest" --config "$canonical_config" \
                --scientific-config "$frozen_config" --scientific-manifest "$frozen_manifest" --require-committed \
                --require-adjacent-eos mg half quarter ;;
    esac
}

run_gate production-parser-registration-before-001 null null \
    "$python_tool" -s "$validator" "$canonical_manifest" --config "$canonical_config" \
    --scientific-config "$frozen_config" --scientific-manifest "$frozen_manifest" \
    --require-committed

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
        echo "STOP $experiment_id: run path already exists; single-use R3 execution cannot resume" >&2
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
        "$python_tool" -s "$parser" "$run_directory" --config "$canonical_config" \
            --manifest "$canonical_manifest" \
            --scientific-config "$frozen_config" \
            --scientific-manifest "$frozen_manifest" --write \
            >"$run_directory/thermodynamic_label_parser.stdout.txt" \
            2>"$run_directory/thermodynamic_label_parser.stderr.txt" \
            || label_parser_status=$?
    fi
    core_status=97
    if [[ $workflow_status -eq 0 && $label_parser_status -eq 0 ]]; then
        core_status=0
        "$python_tool" -s "$validator" "$canonical_manifest" --config "$canonical_config" \
            --scientific-config "$frozen_config" --scientific-manifest "$frozen_manifest" \
            --check-run-core "$experiment_id" --write-core-failure-evidence \
            >"$run_directory/core_validator.stdout.txt" \
            2>"$run_directory/core_validator.stderr.txt" \
            || core_status=$?
    fi

    if [[ $workflow_status -eq 0 && $label_parser_status -eq 0 && $core_status -eq 0 ]]; then
        "$python_tool" -s "$validator" "$canonical_manifest" --config "$canonical_config" \
            --scientific-config "$frozen_config" --scientific-manifest "$frozen_manifest" \
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
            "$python_tool" -s - "$run_directory/thermodynamic_label_failure_classification_r3.json" <<'PY'
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
        "$python_tool" -s "$validator" "$canonical_manifest" --config "$canonical_config" \
        --scientific-config "$frozen_config" --scientific-manifest "$frozen_manifest" --require-committed \
        --check-run "$experiment_id"
    update_external_state "$experiment_id" "$logical_id" accepted_committed
    run_barriers "$experiment_id" "$logical_id"
    if [[ "$experiment_id" == S1-20260807-012 ]]; then
        run_gate k-gate-after-012 "$experiment_id" "$logical_id" \
            "$python_tool" -s "$validator" "$canonical_manifest" --config "$canonical_config" \
            --scientific-config "$frozen_config" --scientific-manifest "$frozen_manifest" \
            --require-committed --require-k-gate
    fi
    echo "DONE $experiment_id logical=$logical_id"
done

run_gate final-all-after-040 S1-20260807-040 S1-20260806-033 \
    "$python_tool" -s "$validator" "$canonical_manifest" --config "$canonical_config" \
    --scientific-config "$frozen_config" --scientific-manifest "$frozen_manifest" \
    --require-committed --require-all-runs

analysis_argv=(
    "$python_tool" -s "$analyzer" "$analysis_directory" --config "$canonical_config"
    --manifest "$canonical_manifest" --scientific-config "$frozen_config"
    --scientific-manifest "$frozen_manifest"
)
analysis_gate_state="$state_directory/barriers"
mkdir -p "$analysis_gate_state"
analysis_exit=0
"${analysis_argv[@]}" \
    >"$analysis_gate_state/final-analysis.stdout.txt" \
    2>"$analysis_gate_state/final-analysis.stderr.txt" || analysis_exit=$?
if [[ -d "$analysis_directory" ]]; then
    git add -- "$analysis_directory"
    git commit -m "record G1 thermodynamic-label audit R3 analysis"
    assert_commit_scope "analysis/s1/g1_thermodynamic_label_audit_r3_20260807/"
fi
if [[ $analysis_exit -ne 0 ]]; then
    record_gate_failure final-analysis S1-20260807-040 S1-20260806-033 \
        "$analysis_exit" "${analysis_argv[@]}"
fi
run_gate final-analysis-status S1-20260807-040 S1-20260806-033 \
    "$python_tool" -s "$validator" "$canonical_manifest" --config "$canonical_config" \
    --scientific-config "$frozen_config" --scientific-manifest "$frozen_manifest" \
    --require-committed --check-analysis-summary
echo "SCIENTIFIC ANALYSIS ACCEPTED; awaiting detached supervisor completion receipt"
