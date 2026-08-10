#!/usr/bin/env python3
"""Run the immutable R2 analyzer once and freeze its expected rc=2 result.

This entry point intentionally uses only the Python standard library.  It is
executed from a separately preregistered capture worktree and never imports
code from the mutable R2 worktree into this process.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CONFIG_PATH = Path("config/S1_g1_three_layer_analysis_r3.json")
CAPTURE_ARTIFACTS = (
    "orchestration/analysis.stdout",
    "orchestration/analysis.stderr",
    "orchestration/analysis_invocation.json",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_exclusive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def run_git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=root, check=check, text=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def git(root: Path, *args: str) -> str:
    return run_git(root, *args).stdout.strip()


def git_clean(root: Path) -> bool:
    return not git(root, "status", "--porcelain=v1", "--untracked-files=all")


def git_blob_identity(root: Path, commit: str, relative: str) -> dict[str, Any]:
    path = root / relative
    require(path.is_file() and not path.is_symlink(), f"dependency missing or unsafe: {relative}")
    oid = git(root, "rev-parse", f"{commit}:{relative}")
    payload = subprocess.run(
        ["git", "cat-file", "blob", oid], cwd=root, check=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout
    require(path.read_bytes() == payload, f"dependency differs from frozen commit: {relative}")
    return {
        "path": relative,
        "git_blob_oid": oid,
        "sha256": sha256_bytes(payload),
        "size_bytes": len(payload),
    }


def tree_inventory(root: Path) -> dict[str, Any]:
    require(root.is_dir() and not root.is_symlink(), f"inventory root missing or unsafe: {root}")
    all_paths = list(root.rglob("*"))
    require(not any(path.is_symlink() for path in all_paths), "analysis tree contains symlink")
    files = sorted((path for path in all_paths if path.is_file()), key=lambda path: path.relative_to(root).as_posix())
    entries = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in files
    ]
    digest_lines = "".join(f"{row['sha256']}  {row['path']}\n" for row in entries).encode("utf-8")
    return {
        "file_count": len(entries),
        "regular_file_bytes": sum(int(row["size_bytes"]) for row in entries),
        "sha256sum_list_digest": sha256_bytes(digest_lines),
        "files": entries,
    }


def require_no_upf_body(root: Path) -> None:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        require(path.suffix.lower() != ".upf" and not path.name.lower().endswith(".upf"), f"UPF entered analysis tree: {relative}")
        with path.open("rb") as handle:
            prefix = handle.read(512).lstrip()
        require(not prefix.startswith(b"<UPF") and b"<PP_HEADER" not in prefix, f"UPF body signature entered analysis tree: {relative}")


def load_capture_config(root: Path) -> tuple[dict[str, Any], bytes]:
    payload = (root / CONFIG_PATH).read_bytes()
    config = json.loads(payload.decode("utf-8"))
    require(isinstance(config, dict), "capture config must be an object")
    require(config.get("protocol_revision") == "S1-G1-THREE-LAYER-ANALYSIS-20260810-R3", "capture protocol differs")
    return config, payload


def validate_capture_registration(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    spec = config["capture"]
    require(sys.flags.no_user_site == 1, "capture requires Python -s")
    require(sys.dont_write_bytecode, "capture requires Python -B")
    require(os.environ.get("PYTHONDONTWRITEBYTECODE") == "1", "capture requires PYTHONDONTWRITEBYTECODE=1")
    require(os.environ.get("PYTHONNOUSERSITE") == "1", "capture requires PYTHONNOUSERSITE=1")
    require(git_clean(root), "capture worktree must be clean")
    head = git(root, "rev-parse", "HEAD")
    implementation = spec["implementation_commit"]
    require("__FREEZE" not in implementation, "capture implementation is not frozen")
    require(git(root, "rev-list", "--parents", "-n", "1", head).split() == [head, implementation], "capture prereg must have exactly the implementation parent")
    changed = git(root, "diff", "--name-only", f"{implementation}..{head}").splitlines()
    require(changed == [CONFIG_PATH.as_posix()], "capture prereg may change only the R3 config")
    script_relative = spec["capture_script_path"]
    script_identity = git_blob_identity(root, head, script_relative)
    require(script_identity["sha256"] == spec["capture_script_sha256"], "capture script SHA differs")
    implementation_blob = git(root, "rev-parse", f"{implementation}:{script_relative}")
    require(implementation_blob == script_identity["git_blob_oid"], "capture script changed after implementation")
    return {
        "capture_preregistered_commit": head,
        "capture_implementation_commit": implementation,
        "capture_script": script_identity,
        "registration_changed_paths": changed,
    }


def validate_r2_dependencies(r2_root: Path, spec: dict[str, Any]) -> list[dict[str, Any]]:
    runner = spec["r2_runner_commit"]
    require(git(r2_root, "rev-parse", "HEAD") == runner, "R2 worktree is not exact runner HEAD")
    require(git_clean(r2_root), "R2 worktree must be clean before collect")
    identities = []
    registered = spec["r2_dependencies"]
    require(isinstance(registered, list) and registered, "R2 dependency denominator missing")
    paths = [row["path"] for row in registered]
    require(len(paths) == len(set(paths)), "R2 dependency paths are not unique")
    for expected in registered:
        actual = git_blob_identity(r2_root, runner, expected["path"])
        require(actual["sha256"] == expected["sha256"], f"R2 dependency SHA differs: {expected['path']}")
        identities.append(actual)
    return identities


def minimal_subprocess_environment(r2_root: Path, temporary: Path, spec: dict[str, Any]) -> dict[str, str]:
    configured = spec["minimal_environment"]
    env = {str(key): str(value) for key, value in configured.items()}
    env["PYTHONPATH"] = str((r2_root / "scripts").resolve())
    env["TMPDIR"] = str(temporary)
    env["PYTHONPYCACHEPREFIX"] = str(temporary / "pycache")
    return env


def capture(capture_root: Path, r2_root: Path) -> dict[str, Any]:
    config, config_bytes = load_capture_config(capture_root)
    spec = config["capture"]
    require(str(r2_root) == spec["r2_worktree"], "R2 worktree path differs from registration")
    registration = validate_capture_registration(capture_root, config)
    dependencies = validate_r2_dependencies(r2_root, spec)
    dependency_by_path = {row["path"]: row for row in dependencies}
    require(dependency_by_path[spec["analyzer_path"]]["sha256"] == spec["analyzer_sha256"], "registered analyzer SHA cross-binding differs")
    require(dependency_by_path[spec["r2_config_path"]]["sha256"] == spec["r2_config_sha256"], "registered R2 config SHA cross-binding differs")

    r2_config_path = r2_root / spec["r2_config_path"]
    r2_payload = read_json(r2_config_path)
    require(isinstance(r2_payload, dict), "R2 config must be an object")
    for key, expected in config["expected_rejection"]["r2_acceptance_thresholds"].items():
        require(r2_payload["acceptance"].get(key) == expected, f"R2 threshold differs: {key}")
    analysis = r2_root / r2_payload["analysis_root"]
    require(not analysis.exists(), "R2 analysis root must not pre-exist")
    terminal_path = Path(r2_payload["external_state_root"]) / "terminal.json"
    terminal = read_json(terminal_path)
    require(isinstance(terminal, dict) and terminal.get("status") == "accepted", "capture requires accepted R2 terminal")
    require(terminal.get("runner_commit") == spec["r2_runner_commit"], "R2 terminal runner differs")
    require(terminal.get("attempted_count") == terminal.get("accepted_count") == 8, "R2 terminal denominator differs")
    require(terminal.get("failed_count") == terminal.get("retried_count") == terminal.get("runner_return_code") == 0, "R2 terminal failure/retry/RC differs")
    terminal_identity = {"path": str(terminal_path), "sha256": sha256_file(terminal_path), "size_bytes": terminal_path.stat().st_size}

    argv = [spec["python"], *spec["python_args"], spec["analyzer_path"], "--project-root", ".", "--collect"]
    started_utc = utc_now()
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="g1_three_layer_r2_capture_") as name:
        temporary = Path(name)
        stdout_path = temporary / "analysis.stdout"
        stderr_path = temporary / "analysis.stderr"
        env = minimal_subprocess_environment(r2_root, temporary, spec)
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            completed = subprocess.run(
                argv, cwd=r2_root, env=env, stdin=subprocess.DEVNULL,
                stdout=stdout, stderr=stderr, check=False,
            )
        finished_utc = utc_now()
        duration = time.monotonic() - start
        require(completed.returncode == int(spec["expected_exit_code"]) == 2, "R2 analyzer exit semantics differ")
        require(analysis.is_dir() and not analysis.is_symlink(), "R2 analyzer did not create analysis root")
        summary = read_json(analysis / "summary.json")
        require(isinstance(summary, dict) and summary.get("status") == spec["expected_summary_status"] == "rejected", "R2 summary disposition differs")
        require_no_upf_body(analysis)
        before = tree_inventory(analysis)
        before_paths = {row["path"] for row in before["files"]}
        require(not before_paths.intersection(CAPTURE_ARTIFACTS), "capture artifact already exists")
        copied_terminal = analysis / "orchestration/terminal.json"
        require(copied_terminal.read_bytes() == terminal_path.read_bytes(), "collected terminal differs from external terminal")

        stdout_bytes = stdout_path.read_bytes()
        stderr_bytes = stderr_path.read_bytes()
        stdout_identity = {"sha256": sha256_bytes(stdout_bytes), "size_bytes": len(stdout_bytes)}
        stderr_identity = {"sha256": sha256_bytes(stderr_bytes), "size_bytes": len(stderr_bytes)}
        atomic_write_exclusive(analysis / CAPTURE_ARTIFACTS[0], stdout_bytes)
        atomic_write_exclusive(analysis / CAPTURE_ARTIFACTS[1], stderr_bytes)
        invocation = {
            "schema_version": 2,
            "protocol_revision": config["protocol_revision"],
            "status": "captured_expected_scientific_rejection",
            "argv": argv,
            "capture_cwd": str(capture_root),
            "cwd": str(r2_root),
            "r2_runner_commit": spec["r2_runner_commit"],
            **registration,
            "capture_config_path": CONFIG_PATH.as_posix(),
            "capture_config_sha256": sha256_bytes(config_bytes),
            "r2_dependencies": dependencies,
            "analyzer": dependency_by_path[spec["analyzer_path"]],
            "r2_config": dependency_by_path[spec["r2_config_path"]],
            "terminal": terminal_identity,
            "exit_code": completed.returncode,
            "expected_summary_status": spec["expected_summary_status"],
            "summary_sha256": sha256_file(analysis / "summary.json"),
            "stdout_sha256": stdout_identity["sha256"],
            "stdout_size_bytes": stdout_identity["size_bytes"],
            "stderr_sha256": stderr_identity["sha256"],
            "stderr_size_bytes": stderr_identity["size_bytes"],
            "analyzer_output_inventory_before_capture_artifacts": before,
            "capture_added_artifacts": list(CAPTURE_ARTIFACTS),
            "subprocess_environment": env,
            "started_utc": started_utc,
            "finished_utc": finished_utc,
            "duration_seconds": duration,
            "interpretation": "exit 2 is the preregistered scientific gate rejection, not an execution failure",
        }
        atomic_write_exclusive(analysis / CAPTURE_ARTIFACTS[2], canonical_json_bytes(invocation))

    after = tree_inventory(analysis)
    after_paths = {row["path"] for row in after["files"]}
    require(after_paths == before_paths.union(CAPTURE_ARTIFACTS), "capture added an unexpected analysis artifact")
    require(after["file_count"] == before["file_count"] + 3, "capture artifact count differs")
    require_no_upf_body(analysis)
    status = git(r2_root, "status", "--porcelain=v1", "--untracked-files=all").splitlines()
    prefix = r2_payload["analysis_root"] + "/"
    require(status and all(len(line) > 3 and line[3:].startswith(prefix) for line in status), "capture changed paths outside R2 analysis root")
    return {
        "status": "captured_expected_scientific_rejection",
        "r2_analyzer_exit_code": completed.returncode,
        "r2_summary_status": summary["status"],
        "analysis_root": str(analysis),
        "analysis_invocation_sha256": sha256_file(analysis / CAPTURE_ARTIFACTS[2]),
        "analysis_file_count": after["file_count"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-project-root", type=Path, required=True)
    parser.add_argument("--r2-project-root", type=Path, required=True)
    args = parser.parse_args()
    payload = capture(args.capture_project_root.resolve(), args.r2_project_root.resolve())
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
