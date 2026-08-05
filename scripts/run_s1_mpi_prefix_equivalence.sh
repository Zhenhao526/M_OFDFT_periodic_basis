#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
echo "Deprecated name: forwarding to runtime-relocation equivalence runner" >&2
exec "$project_root/scripts/run_s1_runtime_relocation_equivalence.sh" "$@"
