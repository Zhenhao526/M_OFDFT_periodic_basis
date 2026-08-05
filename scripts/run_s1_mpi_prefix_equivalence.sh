#!/usr/bin/env bash
set -euo pipefail

if [[ $# -gt 2 ]]; then
    echo "Usage: $0 [MANIFEST_TSV [CONFIG_JSON]]" >&2
    exit 2
fi

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
manifest=${1:-$project_root/config/S1_mpi_prefix_equivalence_manifest.tsv}
config=${2:-$project_root/config/S1_mpi_prefix_equivalence.json}
manifest=$(realpath "$manifest")
config=$(realpath "$config")
cd "$project_root"

if [[ -n "$(git status --porcelain)" ]]; then
    echo "Refusing MPI-equivalence replay from a dirty worktree" >&2
    exit 2
fi
python3 scripts/validate_s1_mpi_prefix_equivalence.py \
    "$manifest" --config "$config" --require-committed >/dev/null

runtime_fields=$(python3 - "$config" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
runtime = payload["runtime"]
audit = payload["runtime_audit"]
values = (
    runtime["recovery_root"],
    runtime["recovery_prefix"],
    runtime["old_prefix"],
    runtime["abacus_path"],
    runtime["abacus_sha256"],
    runtime["mpirun_path"],
    runtime["mpirun_sha256"],
    runtime["launcher_path"],
    runtime["launcher_sha256"],
    str(payload["rank_count"]),
    str(audit["allowed_failed_probe_expected_count_per_run"]),
    ":".join(audit["system_mapping_roots"]),
)
if any("\t" in value or "\n" in value for value in values):
    raise SystemExit("runtime config contains a TSV control character")
print("\t".join(values))
PY
)
IFS=$'\t' read -r recovery_root recovery_prefix old_prefix abacus abacus_sha256 mpirun \
    mpirun_sha256 launcher launcher_sha256 rank_count allowed_probe_count system_roots \
    <<<"$runtime_fields"

audit_launcher="$project_root/scripts/mpi_prefix_audit_launcher.py"
if [[ ! -x "$audit_launcher" ]]; then
    echo "MPI audit launcher is not executable: $audit_launcher" >&2
    exit 2
fi

exec 9<"$manifest"
IFS= read -r header <&9
expected_header=$'replay_experiment_id\treference_experiment_id\tinput_directory\tmaterial\tseries_id\tsolver\tinput_sha256\tstru_sha256\tkpt_sha256\tmetadata_sha256\tpseudopotential\tpseudopotential_sha256\treference_result_path\treference_result_sha256\treference_log_path\treference_log_sha256\treference_experiment_metadata_path\treference_experiment_metadata_sha256\tconfig_sha256'
if [[ "$header" != "$expected_header" ]]; then
    echo "Invalid MPI-equivalence manifest header: $header" >&2
    exit 2
fi

while IFS=$'\t' read -r replay_id reference_id input_directory material series_id solver \
    input_sha256 stru_sha256 kpt_sha256 metadata_sha256 pseudopotential \
    pseudopotential_sha256 reference_result_path reference_result_sha256 \
    reference_log_path reference_log_sha256 reference_experiment_metadata_path \
    reference_experiment_metadata_sha256 config_sha256 <&9; do
    run_directory="$project_root/runs/$replay_id"
    if [[ -d "$run_directory" ]]; then
        python3 scripts/validate_s1_mpi_prefix_equivalence.py \
            "$manifest" --config "$config" --require-committed --check-run "$replay_id" \
            >/dev/null
        echo "SKIP $replay_id already committed and strictly validated"
        continue
    fi

    echo "START $replay_id reference=$reference_id material=$material series=$series_id"
    status=0
    env -i \
    HOME="$HOME" \
    USER="${USER:-shenwei01}" \
    LOGNAME="${LOGNAME:-${USER:-shenwei01}}" \
    PATH=/usr/bin:/bin \
    LC_ALL=C \
    TZ=UTC \
    TMPDIR=/tmp \
    OMP_NUM_THREADS=1 \
    M_OFDFT_RUNTIME="$recovery_root" \
    M_OFDFT_PREFIX="$recovery_prefix" \
    OPAL_PREFIX="$recovery_prefix" \
    PRTE_PREFIX="$recovery_prefix" \
    PMIX_PREFIX="$recovery_prefix" \
    M_OFDFT_ABACUS="$abacus" \
    M_OFDFT_NPROCS="$rank_count" \
    M_OFDFT_MPIRUN="$audit_launcher" \
    M_OFDFT_PROVENANCE_MPIRUN="$mpirun" \
    M_OFDFT_REAL_MPIRUN="$mpirun" \
    M_OFDFT_EXPECTED_MPIRUN_SHA256="$mpirun_sha256" \
    M_OFDFT_EXPECTED_LAUNCHER="$launcher" \
    M_OFDFT_EXPECTED_LAUNCHER_SHA256="$launcher_sha256" \
    M_OFDFT_EXPECTED_ABACUS="$abacus" \
    M_OFDFT_EXPECTED_ABACUS_SHA256="$abacus_sha256" \
    M_OFDFT_MPI_AUDIT_DIR="$run_directory/mpi_runtime_audit" \
    M_OFDFT_RECOVERY_ROOT="$recovery_root" \
    M_OFDFT_RECOVERY_PREFIX="$recovery_prefix" \
    M_OFDFT_OLD_PREFIX="$old_prefix" \
    M_OFDFT_MPI_AUDIT_EXPECTED_RANKS="$rank_count" \
    M_OFDFT_MPI_AUDIT_ALLOWED_PROBE_COUNT="$allowed_probe_count" \
    M_OFDFT_MPI_AUDIT_SYSTEM_ROOTS="$system_roots" \
    M_OFDFT_MPI_AUDIT_STRACE_MODE=require \
    scripts/run_s1_single.sh "$replay_id" "$input_directory" </dev/null || status=$?

    validation_status=0
    if [[ -d "$run_directory" ]]; then
        python3 scripts/validate_s1_mpi_prefix_equivalence.py \
            "$manifest" --config "$config" --check-run "$replay_id" \
            >/dev/null || validation_status=$?
        git add "$run_directory"
        git commit -m "record MPI-prefix replay $replay_id against $reference_id"
    fi
    if [[ $status -ne 0 || $validation_status -ne 0 ]]; then
        echo "STOP $replay_id execution=$status validation=$validation_status; artifacts committed when present" >&2
        if [[ $status -ne 0 ]]; then
            exit "$status"
        fi
        exit 97
    fi
    python3 scripts/validate_s1_mpi_prefix_equivalence.py \
        "$manifest" --config "$config" --require-committed --check-run "$replay_id" \
        >/dev/null
    echo "DONE $replay_id"
done
exec 9<&-
