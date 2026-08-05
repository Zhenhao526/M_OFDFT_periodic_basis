#!/usr/bin/env bash
set -euo pipefail

M_OFDFT_RUNTIME=/home/shenwei01/wt_melting_runtime_20260724
M_OFDFT_PREFIX="$M_OFDFT_RUNTIME/conda_prefix"

export PATH="$M_OFDFT_PREFIX/bin:/usr/bin:/bin"
export LD_LIBRARY_PATH="$M_OFDFT_PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export CMAKE_PREFIX_PATH="$M_OFDFT_PREFIX${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
export MKLROOT="$M_OFDFT_PREFIX"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"

