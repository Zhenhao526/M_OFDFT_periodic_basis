#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 MANIFEST_TSV" >&2
    exit 2
fi

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
manifest=$(realpath "$1")
cd "$project_root"

if [[ ! -f "$manifest" ]]; then
    echo "Missing manifest: $manifest" >&2
    exit 2
fi
if [[ -n "$(git status --porcelain)" ]]; then
    echo "Refusing S1-R8 run from a dirty worktree" >&2
    exit 2
fi

scripts/validate_s1_non_equilibrium_manifest.py "$manifest" >/dev/null

exec 3<"$manifest"
IFS= read -r header <&3
expected_header=$'experiment_id\tinput_directory\tmaterial\tseries_id\tcomparison_axis\tvolume_ratio\treference_experiment_id\tinput_metadata_sha256'
if [[ "$header" != "$expected_header" ]]; then
    echo "Invalid S1-R8 manifest header: $header" >&2
    exit 2
fi

while IFS=$'\t' read -r experiment_id input_directory material series_id comparison_axis volume_ratio reference_experiment_id input_metadata_sha256 <&3; do
    run_directory="runs/$experiment_id"
    if [[ -d "$run_directory" ]]; then
        if ! git ls-files --error-unmatch "$run_directory/result.json" >/dev/null 2>&1; then
            echo "Refusing uncommitted existing run: $run_directory" >&2
            exit 2
        fi
        python3 - "$run_directory" "$experiment_id" "$input_metadata_sha256" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

run = Path(sys.argv[1])
experiment_id = sys.argv[2]
expected_metadata_sha = sys.argv[3]
result = json.loads((run / "result.json").read_text())
experiment = json.loads((run / "experiment_metadata.json").read_text())
metadata_path = run / "input_metadata.json"
actual_metadata_sha = hashlib.sha256(metadata_path.read_bytes()).hexdigest()
if experiment.get("experiment_id") != experiment_id:
    raise SystemExit("committed run experiment ID does not match manifest")
if actual_metadata_sha != expected_metadata_sha:
    raise SystemExit("committed run input metadata does not match manifest")
if not result.get("converged"):
    raise SystemExit("committed S1-R8 run is failed; register a new retry ID")
PY
        echo "SKIP $experiment_id already committed and validated"
        continue
    fi

    echo "START $experiment_id material=$material series=$series_id axis=$comparison_axis volume_ratio=$volume_ratio reference=$reference_experiment_id"
    status=0
    scripts/run_s1_single.sh "$experiment_id" "$input_directory" </dev/null || status=$?
    git add "$run_directory"
    git commit -m "record $material $series_id V/V0=$volume_ratio ($experiment_id)"
    if [[ $status -ne 0 ]]; then
        echo "STOP $experiment_id failed with status $status; result committed" >&2
        exit "$status"
    fi
    echo "DONE $experiment_id"
done
exec 3<&-
