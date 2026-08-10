#!/usr/bin/env python3
"""Validate committed evidence for the S1/G1 DFTpy cross-code gate."""

from __future__ import annotations

import argparse
import filecmp
import json
import subprocess
import tempfile
from pathlib import Path

from analyze_s1_g1_cross_code_dftpy import analyze, write_outputs
from run_s1_g1_cross_code_dftpy import read_manifest


FROZEN_PATHS = (
    "docs/S1_G1_CROSS_CODE_DFTPY_PROTOCOL.md",
    "config/S1_g1_cross_code_dftpy.json",
    "config/S1_g1_cross_code_dftpy_manifest.tsv",
    "environment/dftpy_2.2.0_pylibxc_7.0.0.lock.json",
    "scripts/run_s1_g1_cross_code_dftpy.py",
    "scripts/analyze_s1_g1_cross_code_dftpy.py",
    "scripts/validate_s1_g1_cross_code_dftpy.py",
)


def _git(project_root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(project_root), *args],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _require_committed_tree(project_root: Path, analysis_directory: Path) -> list[str]:
    failures = []
    paths = list(FROZEN_PATHS)
    paths.extend(
        str(path.relative_to(project_root))
        for path in analysis_directory.rglob("*")
        if path.is_file()
    )
    for relative in paths:
        tracked = _git(project_root, "ls-files", "--error-unmatch", "--", relative, check=False)
        if tracked.returncode != 0:
            failures.append(f"not tracked: {relative}")
            continue
        changed = _git(project_root, "diff", "--quiet", "HEAD", "--", relative, check=False)
        if changed.returncode != 0:
            failures.append(f"differs from HEAD: {relative}")
    status = _git(
        project_root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--",
        str(analysis_directory.relative_to(project_root)),
        check=False,
    )
    if status.stdout:
        failures.append("analysis tree is not clean")
    return failures


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("analysis_directory", type=Path)
    parser.add_argument(
        "--manifest", type=Path, default=project_root / "config/S1_g1_cross_code_dftpy_manifest.tsv"
    )
    parser.add_argument(
        "--config", type=Path, default=project_root / "config/S1_g1_cross_code_dftpy.json"
    )
    parser.add_argument("--require-committed", action="store_true")
    args = parser.parse_args()
    analysis = args.analysis_directory.resolve()
    manifest = args.manifest.resolve()
    config = args.config.resolve()
    failures = []
    required = [analysis / name for name in ("summary.json", "points.tsv", "README.md")]
    if any(not path.is_file() for path in required):
        failures.append("analysis summary files are incomplete")
    raw = analysis / "raw"
    rows = read_manifest(manifest)
    expected_raw = {row["experiment_id"] for row in rows}
    observed_raw = {path.name for path in raw.iterdir() if path.is_dir()} if raw.is_dir() else set()
    if observed_raw != expected_raw:
        failures.append("raw formal-run denominator differs from manifest")
    runner_summary_path = raw / "runner_summary.json"
    if not runner_summary_path.is_file():
        failures.append("raw runner summary is missing")

    replay_summary = None
    if not failures:
        replay_summary, replay_rows = analyze(project_root, manifest, config, raw)
        if replay_summary["cross_code_status"] != "accepted":
            failures.append("replayed scientific analysis is not accepted")
        with tempfile.TemporaryDirectory(prefix="s1-g1-dftpy-replay-") as temp:
            replay = Path(temp) / "analysis"
            write_outputs(replay, replay_summary, replay_rows)
            for name in ("summary.json", "points.tsv", "README.md"):
                if not filecmp.cmp(replay / name, analysis / name, shallow=False):
                    failures.append(f"analysis replay differs byte-for-byte: {name}")

    if runner_summary_path.is_file():
        runner = json.loads(runner_summary_path.read_text(encoding="utf-8"))
        runner_commit = str(runner.get("runner_commit", ""))
        if len(runner_commit) != 40:
            failures.append("runner commit is missing")
        else:
            ancestor = _git(project_root, "merge-base", "--is-ancestor", runner_commit, "HEAD", check=False)
            if ancestor.returncode != 0:
                failures.append("runner commit is not an ancestor of current evidence")
            for relative in FROZEN_PATHS:
                frozen = _git(project_root, "show", f"{runner_commit}:{relative}", check=False)
                if frozen.returncode != 0:
                    failures.append(f"frozen implementation absent from runner commit: {relative}")
                    continue
                if frozen.stdout.encode() != (project_root / relative).read_bytes():
                    failures.append(f"frozen implementation changed after formal run: {relative}")
    if args.require_committed:
        failures.extend(_require_committed_tree(project_root, analysis))
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print(
        "accepted: 14/14 DFTpy points; both materials passed relative EOS, V0, pressure, and electron-number gates"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
