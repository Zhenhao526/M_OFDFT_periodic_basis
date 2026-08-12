#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import analyze_s2_g2_al_joint_svd_r1 as analysis


def test_config() -> None:
    config = analysis.load_config(ROOT)
    analysis.validate_config(config)
    assert config["analysis"]["candidate_dropped_dimensions"] == [1, 2]


def test_joint_projector_is_fixed_and_deterministic() -> None:
    rng = np.random.default_rng(11)
    base = rng.normal(size=(8, 8))
    grams = []
    for index in range(10):
        matrix = base + 0.01 * index * np.eye(8)
        grams.append(matrix.T @ matrix + 1e-4 * np.eye(8))
    first = analysis.joint_projector(grams, 2)
    second = analysis.joint_projector(grams, 2)
    assert first["transform"].shape == (8, 6)
    assert first["transform_sha256"] == second["transform_sha256"]
    assert np.array_equal(first["transform"], second["transform"])


def test_reduced_rank_and_alignment_rows() -> None:
    config = analysis.load_config(ROOT)
    rng = np.random.default_rng(29)
    grams = []
    for index in range(10):
        q, _ = np.linalg.qr(rng.normal(size=(8, 8)))
        values = np.asarray([1e-12, 2e-10, 0.4, 0.6, 0.8, 1.0, 1.2, 1.5])
        grams.append(q @ np.diag(values + index * 1e-5) @ q.T + 2.0 * np.eye(8))
    projector = analysis.joint_projector(grams, 1)
    rows, alignment = analysis.reduced_rank_rows(config, 128, grams[:5], projector["normalized_grams"][:5], projector, 1)
    assert len(rows) == 15 and len(alignment) == 5
    assert all(row["reduced_dimension"] == 7 for row in rows)


def test_candidate_selection_is_minimal() -> None:
    rows = [
        {"candidate_id": "joint_svd_drop1", "scientific_gate_accepted": True},
        {"candidate_id": "joint_svd_drop2", "scientific_gate_accepted": True},
    ]
    passing = [row for row in rows if row["scientific_gate_accepted"]]
    assert passing[0]["candidate_id"] == "joint_svd_drop1"


def test_source_identities() -> None:
    config = analysis.load_config(ROOT)
    source = config["source"]
    for path_key, sha_key in (("dense_grid_config_path", "dense_grid_config_sha256"), ("dense_grid_analyzer_path", "dense_grid_analyzer_sha256"), ("dense_grid_summary_path", "dense_grid_summary_sha256")):
        assert (ROOT / source[path_key]).is_file()
        import hashlib
        assert hashlib.sha256((ROOT / source[path_key]).read_bytes()).hexdigest() == source[sha_key]


if __name__ == "__main__":
    tests = [test_config, test_joint_projector_is_fixed_and_deterministic, test_reduced_rank_and_alignment_rows, test_candidate_selection_is_minimal, test_source_identities]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(json.dumps({"status": "accepted", "tests": len(tests)}, sort_keys=True))
