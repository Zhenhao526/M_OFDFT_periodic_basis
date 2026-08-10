#!/usr/bin/env python3
"""Validate pre-registration or committed evidence for G1 regeneration-10 R1."""

from __future__ import annotations

import argparse
import filecmp
import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from analyze_s1_g1_regeneration_10_r1 import analyze, write_outputs
from s1_g1_regeneration_10_common import (
    FROZEN_IMPLEMENTATION_PATHS,
    PROTOCOL_REVISION,
    git,
    implementation_matches_runner_commit,
    read_config,
    sha256,
    validate_registration,
    write_exclusive,
)


def utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def require_tracked_clean(project_root: Path, paths: list[Path]) -> list[str]:
    failures = []
    for path in paths:
        relative = str(path.relative_to(project_root))
        tracked = subprocess.run(
            ["git", "-C", str(project_root), "ls-files", "--error-unmatch", "--", relative],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if tracked.returncode:
            failures.append(f"not tracked: {relative}")
            continue
        changed = subprocess.run(
            ["git", "-C", str(project_root), "diff", "--quiet", "HEAD", "--", relative]
        )
        if changed.returncode:
            failures.append(f"differs from HEAD: {relative}")
    if git(project_root, "status", "--porcelain=v1", "--untracked-files=all"):
        failures.append("worktree is not clean")
    return failures


def compare_trees(expected: Path, observed: Path) -> list[str]:
    failures = []
    expected_files = sorted(
        str(path.relative_to(expected)) for path in expected.rglob("*") if path.is_file()
    )
    observed_files = sorted(
        str(path.relative_to(observed)) for path in observed.rglob("*") if path.is_file()
    )
    if expected_files != observed_files:
        return ["analysis file denominator differs"]
    for relative in expected_files:
        if not filecmp.cmp(expected / relative, observed / relative, shallow=False):
            failures.append(f"analysis replay differs byte-for-byte: {relative}")
    return failures


def validate_accepted(
    project_root: Path, config_path: Path, manifest_path: Path, analysis: Path
) -> tuple[list[str], dict | None]:
    failures = []
    config = read_config(config_path)
    state = Path(config["external_state_root"])
    required = (
        "summary.json", "points.tsv", "label_metrics.tsv",
        "evidence_manifest.tsv", "README.md",
    )
    if any(not (analysis / name).is_file() for name in required):
        return ["analysis summary files are incomplete"], None
    try:
        values = analyze(project_root, config_path, manifest_path)
    except Exception as error:
        return [f"independent analysis failed: {type(error).__name__}: {error}"], None
    summary = values[0]
    if summary.get("status") != "accepted":
        failures.append("independent scientific analysis is rejected")
    with tempfile.TemporaryDirectory(prefix="g1-regen10-replay-") as temporary:
        replay = Path(temporary) / "analysis"
        write_outputs(replay, *values)
        failures.extend(compare_trees(replay, analysis))
    terminal = json.loads((state / "terminal.json").read_text(encoding="utf-8"))
    runner_commit = str(terminal.get("runner_commit", ""))
    failures.extend(implementation_matches_runner_commit(project_root, runner_commit))
    if terminal.get("status") != "accepted" or terminal.get("runner_return_code") != 0:
        failures.append("external terminal record is not accepted with return code zero")
    return failures, summary


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--preregistered", action="store_true")
    parser.add_argument("--require-committed", action="store_true")
    parser.add_argument("--write-completion", action="store_true")
    parser.add_argument("--analysis", type=Path)
    parser.add_argument(
        "--config", type=Path,
        default=project_root / "config/S1_g1_regeneration_10_r1.json",
    )
    parser.add_argument(
        "--manifest", type=Path,
        default=project_root / "config/S1_g1_regeneration_10_r1_manifest.tsv",
    )
    args = parser.parse_args()
    config_path, manifest_path = args.config.resolve(), args.manifest.resolve()
    failures = []
    if args.preregistered:
        if args.require_committed or args.write_completion or args.analysis:
            failures.append("preregistered mode cannot combine evidence options")
        try:
            config, _, _ = validate_registration(
                project_root, config_path, manifest_path,
                require_clean=True, require_state_absent=True,
            )
            if git(project_root, "branch", "--show-current") != "codex/g1-regen10-r1":
                failures.append("formal branch differs")
            expected = config["command_contract"]["all_cases"]
            actual = [
                config["runtime"]["tools"]["python"]["path"], "-s",
                str(project_root / "scripts/launch_s1_g1_regeneration_10_r1.py"),
                "--all",
            ]
            if expected != actual:
                failures.append("single-command contract differs")
            for relative in FROZEN_IMPLEMENTATION_PATHS:
                if subprocess.run(
                    ["git", "-C", str(project_root), "ls-files", "--error-unmatch", "--", relative],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                ).returncode:
                    failures.append(f"pre-registration path untracked: {relative}")
        except Exception as error:
            failures.append(f"pre-registration validation failed: {type(error).__name__}: {error}")
        if not failures:
            print("preregistered: 10 fixed IDs; source OIDs/hashes and runtime frozen; state absent")
    else:
        config = read_config(config_path)
        analysis = (
            args.analysis.resolve()
            if args.analysis
            else project_root / config["analysis_directory"]
        )
        accepted_failures, summary = validate_accepted(
            project_root, config_path, manifest_path, analysis
        )
        failures.extend(accepted_failures)
        completion = project_root / config["completion_record"]
        if args.write_completion and not failures and summary is not None:
            if completion.exists():
                failures.append("completion record already exists")
            else:
                state = Path(config["external_state_root"])
                write_exclusive(completion, {
                    "schema_version": 1,
                    "protocol_revision": PROTOCOL_REVISION,
                    "status": "accepted",
                    "runner_commit": summary["runner_commit"],
                    "analysis_summary_path": str(analysis.relative_to(project_root)),
                    "analysis_summary_sha256": sha256(analysis / "summary.json"),
                    "external_state_root": str(state),
                    "terminal_sha256": sha256(state / "terminal.json"),
                    "denominator": summary["denominator"],
                    "hard_gates": summary["hard_gates"],
                    "created_utc": utc(),
                })
                print(f"wrote completion: {completion}")
        if args.require_committed:
            if not completion.is_file():
                failures.append("completion record is missing")
            else:
                payload = json.loads(completion.read_text(encoding="utf-8"))
                state = Path(config["external_state_root"])
                completion_matches = summary is not None and (
                    payload.get("protocol_revision") == PROTOCOL_REVISION
                    and payload.get("status") == "accepted"
                    and payload.get("runner_commit") == summary["runner_commit"]
                    and payload.get("analysis_summary_path")
                    == str(analysis.relative_to(project_root))
                    and payload.get("analysis_summary_sha256")
                    == sha256(analysis / "summary.json")
                    and payload.get("external_state_root") == str(state)
                    and payload.get("terminal_sha256") == sha256(state / "terminal.json")
                    and payload.get("denominator") == summary["denominator"]
                    and payload.get("hard_gates") == summary["hard_gates"]
                )
                if not completion_matches:
                    failures.append("completion record differs from accepted analysis")
            tracked = [project_root / relative for relative in FROZEN_IMPLEMENTATION_PATHS]
            tracked.extend(path for path in analysis.rglob("*") if path.is_file())
            if completion.is_file():
                tracked.append(completion)
            failures.extend(require_tracked_clean(project_root, tracked))
        if not failures and not args.write_completion:
            print(
                "accepted: 10/10 fixed regenerations; zero failed/missing/skipped/retried; "
                "all scalar, label, electron, field, provenance, and affinity gates passed"
            )
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
