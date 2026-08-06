#!/usr/bin/env python3
"""Parse one preregistered S1-G1 thermodynamic-label audit R2 run.

R2 deliberately reuses the frozen, tested R1 raw-output parser while replacing
only the registration namespace.  The wrapper keeps the R1 implementation
immutable and makes the R2 protocol/ID set explicit in the resulting evidence.
"""

from __future__ import annotations

import argparse
import json
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


def parse_run(
    run_directory: Path,
    *,
    config_path: Path,
    manifest_path: Path,
) -> dict[str, object]:
    """Parse an R2 run through the frozen R1 scientific parser."""

    original_ids = r1_parser.AUDIT_IDS
    original_revision = r1_parser.PROTOCOL_REVISION
    try:
        r1_parser.AUDIT_IDS = R2_AUDIT_IDS
        r1_parser.PROTOCOL_REVISION = PROTOCOL_REVISION
        payload = r1_parser.parse_run(
            run_directory,
            config_path=config_path,
            manifest_path=manifest_path,
        )
    finally:
        r1_parser.AUDIT_IDS = original_ids
        r1_parser.PROTOCOL_REVISION = original_revision

    require(payload.get("protocol_revision") == PROTOCOL_REVISION, "R2 parser revision differs")
    require(payload.get("experiment_id") in R2_AUDIT_IDS, "R2 parser ID differs")
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
    parser.add_argument("--write", action="store_true")
    arguments = parser.parse_args()
    payload = parse_run(
        arguments.run_directory.resolve(),
        config_path=arguments.config.resolve(),
        manifest_path=arguments.manifest.resolve(),
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
