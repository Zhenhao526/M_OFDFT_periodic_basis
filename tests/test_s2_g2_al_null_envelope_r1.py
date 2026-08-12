#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT / "scripts"))
import analyze_s2_g2_al_null_envelope_r1 as analysis


def test_config() -> None:
    config = analysis.load_config(ROOT); analysis.validate_config(config)


def test_envelope_exact_union() -> None:
    rng = np.random.default_rng(41); spaces = []
    for _ in range(10):
        q, _ = np.linalg.qr(rng.normal(size=(30, 2))); spaces.append(q)
    envelope, singular, checksum = analysis.build_envelope(spaces)
    assert envelope.shape == (30, 20) and len(singular) == 20 and len(checksum) == 64
    scan, coverage, selected = analysis.scan_dimensions(spaces, envelope, list(range(2, 21)), 15.0)
    assert 2 <= selected <= 20 and scan[-1]["all_local_spaces_covered"] and len(coverage) == 190


def test_identical_spaces_need_two() -> None:
    q = np.eye(12)[:, :2]; spaces = [q.copy() for _ in range(10)]
    envelope, _, _ = analysis.build_envelope(spaces)
    _, _, selected = analysis.scan_dimensions(spaces, envelope, list(range(2, 21)), 15.0)
    assert selected == 2


def test_rotating_spaces_exceed_two() -> None:
    spaces = []
    for index in range(10):
        q = np.zeros((20, 2)); q[2 * index, 0] = 1.0; q[2 * index + 1, 1] = 1.0; spaces.append(q)
    envelope, _, _ = analysis.build_envelope(spaces)
    _, _, selected = analysis.scan_dimensions(spaces, envelope, list(range(2, 21)), 15.0)
    assert selected > 2


if __name__ == "__main__":
    tests = [test_config, test_envelope_exact_union, test_identical_spaces_need_two, test_rotating_spaces_exceed_two]
    for test in tests: test(); print(f"PASS {test.__name__}")
    print(json.dumps({"status": "accepted", "tests": len(tests)}, sort_keys=True))
