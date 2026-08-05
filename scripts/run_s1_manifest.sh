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
    echo "Refusing manifest run from a dirty worktree" >&2
    exit 2
fi

exec 3<"$manifest"
IFS= read -r header <&3
expected_header=$'experiment_id\tinput_directory\tmaterial\tseries_id\tvolume_ratio'
if [[ "$header" != "$expected_header" ]]; then
    echo "Invalid manifest header: $header" >&2
    exit 2
fi

while IFS=$'\t' read -r experiment_id input_directory material series_id volume_ratio <&3; do
    run_directory="runs/$experiment_id"
    if [[ -d "$run_directory" ]]; then
        if git ls-files --error-unmatch "$run_directory/result.json" >/dev/null 2>&1; then
            echo "SKIP $experiment_id already committed"
            continue
        fi
        echo "Refusing uncommitted existing run: $run_directory" >&2
        exit 2
    fi

    echo "START $experiment_id material=$material series=$series_id volume_ratio=$volume_ratio"
    status=0
    scripts/run_s1_single.sh "$experiment_id" "$input_directory" </dev/null || status=$?
    git add "$run_directory"
    git commit -m "record $material $series_id EOS V/V0=$volume_ratio ($experiment_id)"
    if [[ $status -ne 0 ]]; then
        echo "STOP $experiment_id failed with status $status; result committed" >&2
        exit "$status"
    fi
    echo "DONE $experiment_id"
done
exec 3<&-
