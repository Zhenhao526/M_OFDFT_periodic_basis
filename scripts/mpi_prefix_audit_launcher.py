#!/usr/bin/env python3
"""Deprecated compatibility entry point for runtime-relocation auditing."""

from runtime_relocation_audit_launcher import *  # noqa: F401,F403
from runtime_relocation_audit_launcher import _descendants, main  # noqa: F401


if __name__ == "__main__":
    raise SystemExit(main())
