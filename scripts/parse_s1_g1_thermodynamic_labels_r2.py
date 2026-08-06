#!/usr/bin/env python3
"""Parse one preregistered S1-G1 thermodynamic-label audit R2 run.

R2 deliberately reuses the frozen, tested R1 raw-output parser while replacing
only the registration namespace.  The wrapper keeps the R1 implementation
immutable and makes the R2 protocol/ID set explicit in the resulting evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path

import parse_s1_g1_thermodynamic_labels as r1_parser
from generate_s1_g1_thermodynamic_label_audit_r2 import (
    CONFIG_PATH,
    MANIFEST_PATH,
    PROTOCOL_REVISION,
    R2_AUDIT_IDS,
)
from s1_g1_thermodynamic_label_common import canonical_json_bytes, require


OUTPUT_BASENAME = "thermodynamic_labels.json"


def _stable_regular_bytes(path: Path, *, allow_proc_fd: bool) -> tuple[bytes, str]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    if not allow_proc_fd:
        nofollow = getattr(os, "O_NOFOLLOW", None)
        require(nofollow is not None, "stable provenance read requires O_NOFOLLOW")
        flags |= nofollow
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode), f"registration input is not regular: {path}")
        blocks: list[bytes] = []
        offset = 0
        while offset < before.st_size:
            block = os.pread(
                descriptor, min(1024 * 1024, before.st_size - offset), offset
            )
            if not block:
                break
            blocks.append(block)
            offset += len(block)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    fields = (
        "st_dev",
        "st_ino",
        "st_mode",
        "st_nlink",
        "st_uid",
        "st_gid",
        "st_rdev",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    payload = b"".join(blocks)
    require(
        not any(getattr(before, key) != getattr(after, key) for key in fields)
        and len(payload) == before.st_size,
        f"registration input changed or was read short: {path}",
    )
    return payload, hashlib.sha256(payload).hexdigest()


def _write_readonly(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            require(written > 0, "short registration materialization write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(path, 0o400)


def parse_run(
    run_directory: Path,
    *,
    config_path: Path,
    manifest_path: Path,
    scientific_config_path: Path | None = None,
    scientific_manifest_path: Path | None = None,
) -> dict[str, object]:
    """Parse an R2 run through the frozen R1 scientific parser."""

    require(
        (scientific_config_path is None) == (scientific_manifest_path is None),
        "scientific config and manifest must be supplied together",
    )
    canonical_config, config_sha256 = _stable_regular_bytes(
        config_path, allow_proc_fd=False
    )
    canonical_manifest, manifest_sha256 = _stable_regular_bytes(
        manifest_path, allow_proc_fd=False
    )
    scientific_config, scientific_config_sha256 = _stable_regular_bytes(
        scientific_config_path or config_path,
        allow_proc_fd=scientific_config_path is not None,
    )
    scientific_manifest, scientific_manifest_sha256 = _stable_regular_bytes(
        scientific_manifest_path or manifest_path,
        allow_proc_fd=scientific_manifest_path is not None,
    )
    require(
        scientific_config_sha256 == config_sha256
        and scientific_manifest_sha256 == manifest_sha256
        and scientific_config == canonical_config
        and scientific_manifest == canonical_manifest,
        "sealed scientific registration differs from canonical provenance",
    )

    original_ids = r1_parser.AUDIT_IDS
    original_revision = r1_parser.PROTOCOL_REVISION
    with tempfile.TemporaryDirectory(prefix="m-ofdft-g1-r2-registration-") as temporary:
        private = Path(temporary)
        materialized_config = private / "config.json"
        materialized_manifest = private / "manifest.tsv"
        _write_readonly(materialized_config, scientific_config)
        _write_readonly(materialized_manifest, scientific_manifest)
        try:
            r1_parser.AUDIT_IDS = R2_AUDIT_IDS
            r1_parser.PROTOCOL_REVISION = PROTOCOL_REVISION
            payload = r1_parser.parse_run(
                run_directory,
                config_path=materialized_config,
                manifest_path=materialized_manifest,
            )
        finally:
            r1_parser.AUDIT_IDS = original_ids
            r1_parser.PROTOCOL_REVISION = original_revision

    require(payload.get("protocol_revision") == PROTOCOL_REVISION, "R2 parser revision differs")
    require(payload.get("experiment_id") in R2_AUDIT_IDS, "R2 parser ID differs")
    registration = payload.get("registration")
    require(isinstance(registration, dict), "R2 parser registration record is missing")
    require(
        registration.get("config_sha256") == config_sha256
        and registration.get("manifest_sha256") == manifest_sha256,
        "R2 parser materialized registration hash differs",
    )
    registration["config_path"] = str(config_path)
    registration["manifest_path"] = str(manifest_path)
    payload["schema_revision"] = "S1-G1-THERMODYNAMIC-LABELS-R2"
    payload["parser_reuse_contract"] = {
        "scientific_parser": "parse_s1_g1_thermodynamic_labels.py",
        "registration_namespace": "R2",
        "r1_evidence_reinterpretation": False,
    }
    json.dumps(payload, allow_nan=False)
    return payload


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--config", type=Path, default=project_root / CONFIG_PATH)
    parser.add_argument("--manifest", type=Path, default=project_root / MANIFEST_PATH)
    parser.add_argument("--scientific-config", type=Path)
    parser.add_argument("--scientific-manifest", type=Path)
    parser.add_argument("--write", action="store_true")
    arguments = parser.parse_args()
    payload = parse_run(
        arguments.run_directory.resolve(),
        config_path=arguments.config.resolve(),
        manifest_path=arguments.manifest.resolve(),
        scientific_config_path=arguments.scientific_config,
        scientific_manifest_path=arguments.scientific_manifest,
    )
    encoded = canonical_json_bytes(payload)
    if arguments.write:
        output = arguments.run_directory.resolve() / OUTPUT_BASENAME
        with output.open("xb") as handle:
            handle.write(encoded)
        print(output)
    else:
        print(encoded.decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
