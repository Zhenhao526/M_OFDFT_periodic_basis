#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
    echo "usage: $0 FIRST_CPU COMMAND [ARG ...]" >&2
    exit 2
fi
: "${OMPI_COMM_WORLD_RANK:?missing OpenMPI rank}"
: "${M_OFDFT_AFFINITY_EVIDENCE_DIR:?missing affinity evidence directory}"

first_cpu=$1
shift
cpu=$((first_cpu + OMPI_COMM_WORLD_RANK))
evidence="$M_OFDFT_AFFINITY_EVIDENCE_DIR/rank-${OMPI_COMM_WORLD_RANK}.txt"
if [[ -e "$evidence" ]]; then
    echo "refusing to overwrite rank-affinity evidence: $evidence" >&2
    exit 2
fi
(
    set -C
    printf 'hostname=%s\nrank=%s\npid=%s\nrequested_cpu=%s\naffinity_before=%s\n' \
        "$(hostname)" "$OMPI_COMM_WORLD_RANK" "$$" "$cpu" "$(taskset -pc $$ | sed 's/.*: //')" \
        > "$evidence"
)
export M_OFDFT_PINNED_EVIDENCE="$evidence"
exec taskset -c "$cpu" /bin/bash -c '
    printf "affinity_after=%s\n" "$(taskset -pc $$ | sed "s/.*: //")" >> "$M_OFDFT_PINNED_EVIDENCE"
    exec "$@"
' bash "$@"
