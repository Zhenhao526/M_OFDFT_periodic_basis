# Remote baseline

- Host: `node01`
- OS: Ubuntu 22.04.1 LTS
- Kernel: Linux 5.15.0-43-generic x86_64
- Logical CPUs visible: 152
- Memory: approximately 1.0 TiB
- Scheduler: Slurm-compatible commands at `/usr/local/bin`; compute partition has six 152-core nodes with eight A100 GPUs per node
- Project root: `/home/shenwei01/M_OFDFT_periodic_basis`
- Baseline date: 2026-08-05

## Reused immutable baseline

- ABACUS binary: `/home/shenwei01/wt_melting_runtime_20260724/build-abacus-wt-cpu/source/abacus_pw_para`
- Runtime prefix: `/home/shenwei01/wt_melting_runtime_20260724/conda_prefix`
- ABACUS reported version: `v3.11.0-beta.5`
- Build type: Release
- MPI: enabled
- OpenMP: enabled
- LibXC: enabled
- CUDA: disabled for the S0 CPU smoke test

The ABACUS source snapshot has no `.git` metadata. Reproducibility is therefore anchored to the binary SHA-256, source archive SHA-256, reported version, CMake cache snapshot, and package lock copied into this repository.

