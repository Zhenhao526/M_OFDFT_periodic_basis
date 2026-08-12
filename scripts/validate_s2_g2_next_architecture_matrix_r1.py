#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import subprocess
from pathlib import Path

CONFIG_REL = Path("config/S2_g2_next_architecture_matrix_r1.json")
MANIFEST_REL = Path("config/S2_g2_next_architecture_matrix_r1.tsv")
PROTOCOL_REL = Path("docs/S2_G2_NEXT_ARCHITECTURE_MATRIX_R1_PROTOCOL.md")
VALIDATOR_REL = Path("scripts/validate_s2_g2_next_architecture_matrix_r1.py")
TEST_REL = Path("tests/test_s2_g2_next_architecture_matrix_r1.py")
BASE = "483699e0752cab16d7d565646ee2563f7250207c"
PATHS = sorted(str(x) for x in (CONFIG_REL, MANIFEST_REL, PROTOCOL_REL, VALIDATOR_REL, TEST_REL))


def require(value: bool, message: str) -> None:
    if not value: raise ValueError(message)


def git(root: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    require(p.returncode == 0, f"git {' '.join(args)} failed: {p.stderr.strip()}"); return p.stdout.strip()


def sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: object) -> bytes: return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def parents(root: Path, commit: str) -> list[str]: return git(root, "rev-list", "--parents", "-n", "1", commit).split()[1:]


def diffs(root: Path, left: str, right: str) -> list[tuple[str, str]]:
    rows = []
    for line in git(root, "diff", "--name-status", left, right).splitlines():
        fields = line.split("\t"); require(len(fields) == 2, "rename/copy differs"); rows.append((fields[0], fields[1]))
    return rows


def load(root: Path) -> dict: return json.loads((root / CONFIG_REL).read_text())


def manifest(root: Path) -> list[dict]: return list(csv.DictReader((root / MANIFEST_REL).open(), delimiter="\t"))


def validate_content(root: Path, config: dict) -> list[dict]:
    require(config["schema_version"] == 1 and config["protocol_revision"] == "S2-G2-NEXT-ARCHITECTURE-MATRIX-20260812-R1", "protocol differs")
    require(config["base_commit"] == BASE and config["new_solver_run_count"] == 0, "base denominator differs")
    for path_key, sha_key in (("architecture_config_path", "architecture_config_sha256"), ("al1_pilot_summary_path", "al1_pilot_summary_sha256"), ("basis_convergence_summary_path", "basis_convergence_summary_sha256"), ("null_envelope_summary_path", "null_envelope_summary_sha256")):
        path = root / config["source"][path_key]; require(path.is_file() and not path.is_symlink() and sha(path) == config["source"][sha_key], f"source differs: {path}")
    pilot = json.loads((root / config["source"]["al1_pilot_summary_path"]).read_text())
    atomic = next(row for row in pilot["metrics"] if row["candidate_id"] == "atomic_fft")
    require(atomic["status"] == "rejected_pilot" and atomic["effective_rank"] == 6, "atomic FFT history differs")
    basis = json.loads((root / config["source"]["basis_convergence_summary_path"]).read_text())
    by_id = {row["candidate_id"]: row for row in basis["metrics"]}
    accepted = ["r04_eta160_explicit", "r06_eta200_explicit", "r10_eta130_complementary", "r04_eta160_complementary", "r06_eta200_complementary"]
    require(all(by_id[candidate]["status"] == "accepted_convergence" for candidate in accepted), "historical candidate eligibility differs")
    require(by_id["r10_eta130_explicit"]["failed_gates"] == ["condition_number"], "excluded explicit candidate differs")
    envelope = json.loads((root / config["source"]["null_envelope_summary_path"]).read_text())
    require(envelope["minimum_required_fixed_envelope_dimension"] == 8 and envelope["current_23_function_periodic_expansion_retained"] is False, "elimination evidence differs")
    require(config["eliminated"]["candidate_ids"] == ["r08_eta100_explicit", "r08_eta100_complementary"] and config["eliminated"]["may_reenter_under_different_gauge"] is False, "elimination scope differs")
    rows = manifest(root); require(len(rows) == 8, "manifest row count differs")
    ids = [row["candidate_id"] for row in rows]; require(ids == config["matrix"]["candidate_order"] and len(set(ids)) == 8, "candidate order differs")
    require(set(row["architecture_route"] for row in rows) == {"pw_fft_reference", "atomic_fft", "atomic_low_g_explicit", "atomic_low_g_complementary"}, "four-route denominator differs")
    require(not set(ids) & set(config["eliminated"]["candidate_ids"]), "eliminated candidate re-enters")
    require(config["scope"]["g2c_enabled"] is False and config["scope"]["s3_enabled"] is False and config["scope"]["mg_enabled"] is False, "scope opens early")
    require(config["acceptance"]["fixed_subspace_principal_angle_max_degrees"] == 15.0 and config["acceptance"]["effective_condition_number_strict_lt"] == 1e8, "gate differs")
    require(not Path(config["execution"]["formal_state_root"]).exists() and not (root / config["execution"]["analysis_root"]).exists(), "execution artifact exists")
    return rows


def implementation(root: Path, commit: str) -> dict:
    require(parents(root, commit) == [BASE], "implementation parent differs"); require(diffs(root, BASE, commit) == [("A", path) for path in PATHS], "implementation delta differs")
    config = json.loads(git(root, "show", f"{commit}:{CONFIG_REL}")); require(config["status"] == "implementation_pending_preregistration" and config["implementation_commit"] == "__FREEZE_IMPLEMENTATION_COMMIT__", "implementation identity differs"); return config


def prereg(root: Path, commit: str) -> tuple[str, dict]:
    parent = parents(root, commit); require(len(parent) == 1, "preregistration parent count differs"); impl = parent[0]; base = implementation(root, impl)
    require(diffs(root, impl, commit) == [("M", str(CONFIG_REL))], "preregistration delta differs"); config = json.loads(git(root, "show", f"{commit}:{CONFIG_REL}"))
    require(config["status"] == "preregistered_no_execution" and config["implementation_commit"] == impl, "preregistration identity differs")
    normalized = json.loads(json.dumps(config)); normalized["status"] = "implementation_pending_preregistration"; normalized["implementation_commit"] = "__FREEZE_IMPLEMENTATION_COMMIT__"; require(canonical(normalized) == canonical(base), "preregistration changes content"); return impl, config


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1]); group = parser.add_mutually_exclusive_group(required=True); group.add_argument("--implementation-only", action="store_true"); group.add_argument("--preregistered-only", action="store_true"); args = parser.parse_args(); root = args.project_root.resolve(); require(git(root, "status", "--porcelain") == "", "worktree dirty"); head = git(root, "rev-parse", "HEAD")
    config = implementation(root, head) if args.implementation_only else prereg(root, head)[1]; rows = validate_content(root, config)
    print(json.dumps({"status": "accepted_implementation" if args.implementation_only else "accepted_preregistered", "head": head, "matrix_rows": len(rows), "compressed_candidates": 7, "routes": 4, "new_solver_run_count": 0}, sort_keys=True)); return 0


if __name__ == "__main__": raise SystemExit(main())
