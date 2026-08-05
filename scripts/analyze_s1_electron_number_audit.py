#!/usr/bin/env python3
"""Create the committed 90-point G1 electron-number audit report."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from generate_s1_electron_number_audit import CONFIG_PATH, MANIFEST_PATH
from s1_electron_number_common import PROTOCOL_REVISION, sha256
from validate_s1_electron_number_audit import replay_evidence, validate_registration


OUTPUT_DIRECTORY = Path("analysis/s1/electron_number_audit_20260805")
POINT_FIELDS = (
    "source_experiment_id",
    "audit_experiment_id",
    "scope",
    "material",
    "series_id",
    "solver",
    "volume_ratio",
    "density_format",
    "density_path",
    "density_sha256",
    "expected_electrons",
    "integrated_electrons",
    "relative_error",
    "certified_relative_error",
    "accepted",
    "delta_energy_mev_per_atom",
    "delta_pressure_gpa",
    "scientific_equivalence_accepted",
)


def _git(project_root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(project_root), *args], text=True
    ).strip()


def analyze(project_root: Path, config_path: Path, manifest_path: Path) -> dict:
    project_root = project_root.resolve()
    if _git(project_root, "status", "--porcelain"):
        raise ValueError("analysis requires a clean worktree")
    config, rows, details = validate_registration(
        project_root, config_path, manifest_path, require_committed=True
    )
    output_directory = project_root / OUTPUT_DIRECTORY
    if output_directory.exists():
        raise ValueError(f"refusing to overwrite analysis: {OUTPUT_DIRECTORY}")

    points: list[dict[str, object]] = []
    for row in rows:
        source_id = row["source_experiment_id"]
        if row["solver"] == "ksdft":
            integration = details[source_id]["integration"]
            equivalence = None
        else:
            payload, errors = replay_evidence(
                project_root,
                config,
                row,
                require_committed=True,
                require_replay_status=True,
            )
            if errors:
                raise ValueError(
                    f"cannot analyze {row['audit_experiment_id']}:\n- "
                    + "\n- ".join(errors)
                )
            integration = payload["integration"]
            equivalence = payload["scientific_equivalence"]
        point = {
            "source_experiment_id": source_id,
            "audit_experiment_id": row["audit_experiment_id"],
            "scope": row["scope"],
            "material": row["material"],
            "series_id": row["series_id"],
            "solver": row["solver"],
            "volume_ratio": row["volume_ratio"],
            "density_format": integration["density_format"],
            "density_path": row["density_path"],
            "density_sha256": integration["density_sha256"],
            "expected_electrons": integration["expected_electrons"],
            "integrated_electrons": integration["integrated_electrons"],
            "relative_error": integration["relative_error"],
            "certified_relative_error": integration["certified_relative_error"],
            "accepted": integration["accepted"],
            "delta_energy_mev_per_atom": (
                equivalence["delta_energy_mev_per_atom"] if equivalence else ""
            ),
            "delta_pressure_gpa": (
                equivalence["delta_pressure_gpa"] if equivalence else ""
            ),
            "scientific_equivalence_accepted": (
                equivalence["accepted"] if equivalence else "not_applicable_existing_density"
            ),
        }
        points.append(point)

    failures = [
        point["source_experiment_id"] for point in points if point["accepted"] is not True
    ]
    equivalence_failures = [
        point["audit_experiment_id"]
        for point in points
        if point["audit_experiment_id"]
        and point["scientific_equivalence_accepted"] is not True
    ]
    maximum = max(points, key=lambda point: float(point["certified_relative_error"]))
    coverage_counts = {
        "primary_baseline": sum(
            point["scope"] == "primary_baseline" for point in points
        ),
        "supplemental_runtime_replay": sum(
            point["scope"] == "supplemental_runtime_replay" for point in points
        ),
        "ks_existing_density": sum(point["solver"] == "ksdft" for point in points),
        "ofdft_high_precision_replay": sum(
            point["solver"] == "ofdft" for point in points
        ),
    }
    coverage_exact = coverage_counts == {
        "primary_baseline": 84,
        "supplemental_runtime_replay": 6,
        "ks_existing_density": 60,
        "ofdft_high_precision_replay": 30,
    }
    status = (
        "accepted"
        if len(points) == 90
        and coverage_exact
        and not failures
        and not equivalence_failures
        else "rejected"
    )
    summary = {
        "schema_version": 1,
        "protocol_revision": PROTOCOL_REVISION,
        "status": status,
        "recommended_action": (
            "close_only_g1_electron_number_item_keep_other_five_g1_items_pending"
            if status == "accepted"
            else "retain_g1_electron_number_item_pending"
        ),
        "analyzer_commit": _git(project_root, "rev-parse", "HEAD"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config_path": str(config_path.relative_to(project_root)),
        "config_sha256": sha256(config_path),
        "manifest_path": str(manifest_path.relative_to(project_root)),
        "manifest_sha256": sha256(manifest_path),
        "coverage": {
            "required": 90,
            "observed": len(points),
            "breakdown_exact": coverage_exact,
            "primary_baseline_required": 84,
            "primary_baseline_observed": coverage_counts["primary_baseline"],
            "supplemental_runtime_replay_required": 6,
            "supplemental_runtime_replay_observed": coverage_counts[
                "supplemental_runtime_replay"
            ],
            "ks_existing_density": coverage_counts["ks_existing_density"],
            "ofdft_high_precision_replay": coverage_counts[
                "ofdft_high_precision_replay"
            ],
        },
        "accepted_count": sum(point["accepted"] is True for point in points),
        "failure_ids": failures,
        "scientific_equivalence_failure_ids": equivalence_failures,
        "maximum_certified_relative_error": maximum["certified_relative_error"],
        "maximum_error_source_experiment_id": maximum["source_experiment_id"],
        "acceptance_limit_strict": config["acceptance"][
            "per_point_certified_relative_error_strictly_less_than"
        ],
    }

    output_directory.mkdir(parents=True, exist_ok=False)
    (output_directory / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    with (output_directory / "points.tsv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle, fieldnames=POINT_FIELDS, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(points)
    readme = f"""# S1 G1 independent electron-number audit

- Status: `{status}`
- Coverage: `{len(points)}/90` (84 frozen R7/R8 baseline points plus 6 runtime replays)
- Accepted points: `{summary['accepted_count']}/90`
- Maximum certified relative error: `{summary['maximum_certified_relative_error']:.17g}` at `{summary['maximum_error_source_experiment_id']}`
- Strict per-point limit: `<1e-10`
- OF source/replay scientific equivalence failures: `{len(equivalence_failures)}`

KS densities are independently integrated from the reciprocal-space `G=0`
coefficient. OF densities are independently integrated from `out_chg 1 17`
cube values using the `STRU` cell volume; the six-decimal cube axes are never
used as the volume authority. Expected charge is reconstructed from `STRU`
atom counts and the frozen local-pseudopotential `zion` fields, not from the
nominal electron count printed by ABACUS.

This report closes only the G1 electron-number item when accepted. The other
five G1 items and the complete G1 gate remain pending.
"""
    (output_directory / "README.md").write_text(readme, encoding="utf-8")
    return summary


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=project_root / CONFIG_PATH)
    parser.add_argument("--manifest", type=Path, default=project_root / MANIFEST_PATH)
    args = parser.parse_args()
    summary = analyze(project_root, args.config.resolve(), args.manifest.resolve())
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
