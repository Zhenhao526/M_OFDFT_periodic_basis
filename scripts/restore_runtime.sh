#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "Usage: $0 RUNTIME_ARCHIVE TARGET_DIRECTORY" >&2
    exit 2
fi

archive=$(realpath "$1")
target=$2
checksum_file="${archive}.sha256"
expected_abacus_sha256=2d68a57c7b25608b3550854dabc2e63601eeca956bf185ad7d0967052bdbb4ba

if [[ ! -f "$archive" ]]; then
    echo "Runtime archive is missing: $archive" >&2
    exit 2
fi
if [[ ! -f "$checksum_file" ]]; then
    echo "Runtime checksum sidecar is missing: $checksum_file" >&2
    exit 2
fi
if [[ -e "$target" ]]; then
    echo "Refusing to overwrite existing recovery target: $target" >&2
    exit 2
fi

archive_directory=$(dirname "$archive")
archive_name=$(basename "$archive")
(
    cd "$archive_directory"
    sha256sum -c "${archive_name}.sha256"
)

start_epoch=$(date +%s)
mkdir -p "$target"
tar -xzf "$archive" -C "$target"

prefix="$target/conda_prefix"
abacus="$target/source/abacus_pw_para"
mpirun="$prefix/bin/mpirun"
if [[ ! -x "$abacus" || ! -x "$mpirun" ]]; then
    echo "Recovered runtime is incomplete under $target" >&2
    exit 1
fi

actual_abacus_sha256=$(sha256sum "$abacus" | awk '{print $1}')
if [[ "$actual_abacus_sha256" != "$expected_abacus_sha256" ]]; then
    echo "Recovered ABACUS SHA-256 mismatch" >&2
    exit 1
fi

env LD_LIBRARY_PATH="$prefix/lib" ldd "$abacus" > "$target/abacus_ldd.txt"
if grep -F '/home/shenwei01/wt_melting_runtime_20260724/conda_prefix/' "$target/abacus_ldd.txt" >/dev/null; then
    echo "Recovered ABACUS still resolves a library from the baseline prefix" >&2
    exit 1
fi

elapsed_seconds=$(( $(date +%s) - start_epoch ))
python3 - "$target/restore_result.json" "$archive" "$target" "$elapsed_seconds" "$actual_abacus_sha256" <<'PY'
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

payload = {
    "abacus_sha256": sys.argv[5],
    "archive": sys.argv[2],
    "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "elapsed_seconds": int(sys.argv[4]),
    "passed": True,
    "target": sys.argv[3],
}
Path(sys.argv[1]).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

echo "Recovered runtime in ${elapsed_seconds} seconds: $target"
echo "Set M_OFDFT_RUNTIME=$target"
echo "Set M_OFDFT_ABACUS=$abacus"
