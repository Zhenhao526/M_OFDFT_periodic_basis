#!/usr/bin/env python3
"""S1-G1 R2 shim around the immutable R1 runtime-audit launcher."""

from __future__ import annotations

from pathlib import Path

import runtime_relocation_audit_launcher as _r1
from s1_g1_kmp_runtime_contract import KMP_PATTERN


def main() -> int:
    """Run R1 with one process-local KMP transient-mapping extension."""

    original_patterns = _r1.TRANSIENT_MAPPING_PATTERNS
    original_classify = _r1.classify_mapping
    if not isinstance(original_patterns, tuple):
        raise RuntimeError("R1 transient mapping patterns are not an immutable tuple")
    if KMP_PATTERN in original_patterns:
        raise RuntimeError("R1 already contains the G1-R2 KMP transient pattern")
    extended_patterns = (*original_patterns, KMP_PATTERN)

    def classify_mapping_g1_r2(
        original: Path,
        realpath: Path,
        old_prefix: Path,
        recovery_root: Path,
        system_roots: tuple[str, ...] = _r1.SYSTEM_MAPPING_ROOTS,
        system_exact_paths: tuple[str, ...] = _r1.SYSTEM_MAPPING_EXACT_PATHS,
        device_patterns: tuple[str, ...] = _r1.REGISTERED_DEVICE_MAPPING_PATTERNS,
        transient_patterns: tuple[str, ...] | None = None,
    ) -> str:
        selected = (
            extended_patterns
            if transient_patterns is None
            else tuple(transient_patterns)
        )
        if KMP_PATTERN not in selected:
            selected = (*selected, KMP_PATTERN)
        return original_classify(
            original,
            realpath,
            old_prefix,
            recovery_root,
            system_roots,
            system_exact_paths,
            device_patterns,
            selected,
        )

    _r1.TRANSIENT_MAPPING_PATTERNS = extended_patterns
    _r1.classify_mapping = classify_mapping_g1_r2
    try:
        return _r1.main()
    finally:
        _r1.classify_mapping = original_classify
        _r1.TRANSIENT_MAPPING_PATTERNS = original_patterns


if __name__ == "__main__":
    raise SystemExit(main())
