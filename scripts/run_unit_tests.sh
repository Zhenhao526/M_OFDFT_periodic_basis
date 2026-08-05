#!/usr/bin/env bash
set -euo pipefail

M_OFDFT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$M_OFDFT_ROOT"
python3 -m unittest discover -s tests/unit -v

