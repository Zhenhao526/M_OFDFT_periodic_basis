#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
manifest="$project_root/manifests/PROJECT_SHA256SUMS"
temporary_manifest="$(mktemp "$project_root/manifests/.PROJECT_SHA256SUMS.XXXXXX")"
trap 'rm -f "$temporary_manifest"' EXIT

cd "$project_root"
find . -type f \
  ! -path './.git/*' \
  ! -path './manifests/PROJECT_SHA256SUMS' \
  ! -path './manifests/.PROJECT_SHA256SUMS.*' \
  ! -path '*/__pycache__/*' \
  ! -name '.DS_Store' \
  ! -name '._*' \
  -print0 \
  | LC_ALL=C sort -z \
  | xargs -0 sha256sum > "$temporary_manifest"

mv "$temporary_manifest" "$manifest"
trap - EXIT
echo "Updated $manifest"
