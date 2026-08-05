#!/usr/bin/env bash
set -uo pipefail

if [[ $# -eq 0 ]]; then
    echo "namespace payload requires mpirun arguments" >&2
    exit 2
fi

audit_directory=${M_OFDFT_MPI_AUDIT_DIR:?}
namespace_directory="$audit_directory/namespace"
old_root=${M_OFDFT_OLD_ROOT:?}
old_prefix=${M_OFDFT_OLD_PREFIX:?}
controlled_home=${HOME:?}
mount_tool=${M_OFDFT_MOUNT_TOOL:?}
audit_launcher=${M_OFDFT_AUDIT_LAUNCHER:?}
python_tool=${M_OFDFT_PYTHON_TOOL:?}
audit_pid=
mkdir -p "$namespace_directory"

cleanup_payload() {
    if [[ -n "${audit_pid:-}" ]]; then
        kill -TERM "$audit_pid" 2>/dev/null || true
        wait "$audit_pid" 2>/dev/null || true
    fi
}
trap cleanup_payload EXIT INT TERM

record_state() {
    local phase=$1
    local mountinfo_path="$namespace_directory/mountinfo.$phase"
    local state_path="$namespace_directory/state.$phase.json"
    "$python_tool" - "$phase" "$old_root" "$old_prefix" "$controlled_home" "$mountinfo_path" "$state_path" "$BASHPID" <<'PY'
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

phase, old_root_value, old_prefix_value, home_value, mountinfo_value, output_value, init_pid_value = sys.argv[1:]
old_root = Path(old_root_value)
old_prefix = Path(old_prefix_value)
controlled_home = Path(home_value)
mountinfo = Path(mountinfo_value)
mountinfo.write_bytes(Path("/proc/self/mountinfo").read_bytes())

def identity(path: Path):
    try:
        value = path.lstat()
    except FileNotFoundError:
        return None
    return {
        "device": value.st_dev,
        "gid": value.st_gid,
        "inode": value.st_ino,
        "mode": value.st_mode,
        "mode_type": stat.S_IFMT(value.st_mode),
        "mtime_ns": value.st_mtime_ns,
        "size": value.st_size,
        "uid": value.st_uid,
        "is_symlink": path.is_symlink(),
        "realpath": str(path.resolve(strict=False)),
    }

lines = mountinfo.read_text(encoding="utf-8", errors="replace").splitlines()
mount_lines = []
home_mount_lines = []
shared_mount_lines = []
for line in lines:
    fields = line.split()
    if len(fields) >= 5 and fields[4] == str(old_root):
        mount_lines.append(line)
    if len(fields) >= 5 and fields[4] == str(controlled_home):
        home_mount_lines.append(line)
    separator = fields.index("-") if "-" in fields else -1
    if separator >= 0 and any(field.startswith("shared:") for field in fields[6:separator]):
        shared_mount_lines.append(line)
payload = {
    "schema_version": 1,
    "phase": phase,
    "pid": os.getpid(),
    "namespace_init_pid": int(init_pid_value),
    "pid_namespace_inode": Path("/proc/self/ns/pid").stat().st_ino,
    "pid_namespace_link": os.readlink("/proc/self/ns/pid"),
    "nspid": next(
        (
            [int(value) for value in line.split()[1:]]
            for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines()
            if line.startswith("NSpid:")
        ),
        [],
    ),
    "uid": os.getuid(),
    "effective_uid": os.geteuid(),
    "old_root": str(old_root),
    "old_prefix": str(old_prefix),
    "old_root_exists": old_root.exists(),
    "old_prefix_exists": old_prefix.exists(),
    "old_root_lstat": identity(old_root),
    "old_prefix_lstat": identity(old_prefix),
    "old_root_mountinfo_lines": mount_lines,
    "controlled_home": str(controlled_home),
    "controlled_home_exists": controlled_home.is_dir(),
    "controlled_home_lstat": identity(controlled_home),
    "controlled_home_entries": (
        sorted(path.name for path in controlled_home.iterdir())
        if controlled_home.is_dir()
        else []
    ),
    "controlled_home_mountinfo_lines": home_mount_lines,
    "controlled_home_marker_sha256": (
        hashlib.sha256((controlled_home / "CONTROLLED_HOME.txt").read_bytes()).hexdigest()
        if (controlled_home / "CONTROLLED_HOME.txt").is_file()
        else None
    ),
    "shared_mount_lines": shared_mount_lines,
    "mountinfo_path": str(mountinfo),
    "uid_map": Path("/proc/self/uid_map").read_text(encoding="ascii"),
    "gid_map": Path("/proc/self/gid_map").read_text(encoding="ascii"),
}
temporary = Path(output_value).with_name(f".{Path(output_value).name}.tmp-{os.getpid()}")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, output_value)
PY
}

record_payload_status() {
    local status=$1
    local exit_code=$2
    "$python_tool" - "$namespace_directory/payload_status.json" "$status" "$exit_code" <<'PY'
import json
import os
import sys
from pathlib import Path

output, status, exit_code = sys.argv[1:]
payload = {
    "schema_version": 1,
    "status": status,
    "audit_launcher_exit_code": int(exit_code),
    "uid": os.getuid(),
    "effective_uid": os.geteuid(),
    "pid_namespace_inode": Path("/proc/self/ns/pid").stat().st_ino,
    "pid_namespace_link": os.readlink("/proc/self/ns/pid"),
}
path = Path(output)
temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, path)
PY
}

record_state before_mount
preflight_status=0
if [[ $EUID -ne 0 || ! -d "$old_root" || ! -d "$old_prefix" || ! -d "$controlled_home" || -L "$controlled_home" ]]; then
    preflight_status=97
fi
if [[ $("$python_tool" - "$namespace_directory/state.before_mount.json" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
ok = len(payload["old_root_mountinfo_lines"]) == 0
ok = ok and len(payload["controlled_home_mountinfo_lines"]) == 0
ok = ok and payload["controlled_home_entries"] == ["CONTROLLED_HOME.txt"]
print(0 if ok else 1)
PY
) -ne 0 ]]; then
    preflight_status=97
fi
if [[ $preflight_status -ne 0 ]]; then
    record_payload_status namespace_preflight_rejected "$preflight_status"
    exit "$preflight_status"
fi

mount_status=0
"$mount_tool" -t tmpfs -o size=1m,nosuid,nodev,noexec tmpfs "$old_root" || mount_status=$?
home_bind_status=0
"$mount_tool" --bind "$controlled_home" "$controlled_home" || home_bind_status=$?
home_readonly_status=0
"$mount_tool" -o remount,bind,ro,nosuid,nodev,noexec "$controlled_home" || home_readonly_status=$?
record_state after_mount
mount_evidence_ok=$("$python_tool" - "$namespace_directory/state.after_mount.json" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
lines = payload["old_root_mountinfo_lines"]
if len(lines) != 1:
    print(0)
else:
    fields = lines[0].split()
    separator = fields.index("-") if "-" in fields else -1
    options = set(fields[5].split(",")) if len(fields) > 5 else set()
    ok = separator >= 0 and fields[separator + 1:separator + 3] == ["tmpfs", "tmpfs"]
    ok = ok and {"nosuid", "nodev", "noexec"}.issubset(options)
home_lines = payload["controlled_home_mountinfo_lines"]
if len(home_lines) != 1:
    ok = False
else:
    home_fields = home_lines[0].split()
    home_options = set(home_fields[5].split(",")) if len(home_fields) > 5 else set()
    ok = ok and {"ro", "nosuid", "nodev", "noexec"}.issubset(home_options)
ok = ok and payload["controlled_home_entries"] == ["CONTROLLED_HOME.txt"]
print(1 if ok else 0)
PY
)
if [[ $mount_status -ne 0 || $home_bind_status -ne 0 || $home_readonly_status -ne 0 || $mount_evidence_ok -ne 1 || -e "$old_prefix" || -L "$old_prefix" ]]; then
    record_payload_status namespace_mount_rejected 97
    exit 97
fi

audit_status=0
"$python_tool" "$audit_launcher" "$@" </dev/null &
audit_pid=$!
wait "$audit_pid" || audit_status=$?
audit_pid=
record_state after_run
postflight_evidence_ok=$("$python_tool" - "$namespace_directory/state.after_run.json" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
home_lines = payload["controlled_home_mountinfo_lines"]
home_options = set(home_lines[0].split()[5].split(",")) if len(home_lines) == 1 else set()
ok = len(payload["old_root_mountinfo_lines"]) == 1
ok = ok and len(home_lines) == 1
ok = ok and {"ro", "nosuid", "nodev", "noexec"}.issubset(home_options)
ok = ok and payload["controlled_home_entries"] == ["CONTROLLED_HOME.txt"]
print(1 if ok else 0)
PY
)
if [[ -e "$old_prefix" || -L "$old_prefix" || $postflight_evidence_ok -ne 1 ]]; then
    record_payload_status namespace_isolation_lost 97
    exit 97
fi
if [[ $audit_status -eq 0 ]]; then
    record_payload_status accepted 0
else
    record_payload_status audit_rejected "$audit_status"
fi
exit "$audit_status"
