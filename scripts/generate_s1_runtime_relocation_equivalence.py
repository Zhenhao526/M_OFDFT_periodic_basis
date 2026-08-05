#!/usr/bin/env python3
"""Canonical entry point for S1 runtime-relocation preregistration."""

from generate_s1_mpi_prefix_equivalence import *  # noqa: F401,F403
from generate_s1_mpi_prefix_equivalence import main


if __name__ == "__main__":
    raise SystemExit(main())
