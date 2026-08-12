#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT / "scripts"))
import validate_s2_g2_next_architecture_matrix_r1 as validator


def test_matrix_content() -> None:
    config = validator.load(ROOT); rows = validator.validate_content(ROOT, config)
    assert len(rows) == 8 and sum(row["eligible_compressed_winner"] == "true" for row in rows) == 7


def test_four_routes() -> None:
    routes = {row["architecture_route"] for row in validator.manifest(ROOT)}
    assert routes == {"pw_fft_reference", "atomic_fft", "atomic_low_g_explicit", "atomic_low_g_complementary"}


def test_eliminated_space_absent() -> None:
    config = validator.load(ROOT); ids = {row["candidate_id"] for row in validator.manifest(ROOT)}
    assert not ids.intersection(config["eliminated"]["candidate_ids"])
    assert config["eliminated"]["gate_relaxation_allowed"] is False


def test_atomic_reentry_is_staged() -> None:
    rows = {row["candidate_id"]: row for row in validator.manifest(ROOT)}
    assert rows["r08_atomic_fft"]["first_required_stage"] == "A_atomic_fft_al1_reentry"
    assert rows["r10_atomic_fft"]["include_108_dense_geometry"] == "conditional_on_stage_A"


if __name__ == "__main__":
    tests = [test_matrix_content, test_four_routes, test_eliminated_space_absent, test_atomic_reentry_is_staged]
    for test in tests: test(); print(f"PASS {test.__name__}")
    print(json.dumps({"status": "accepted", "tests": len(tests)}, sort_keys=True))
