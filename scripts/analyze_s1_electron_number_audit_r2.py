#!/usr/bin/env python3
"""Build the committed 90-point incremental G1 electron-number R2 report."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from s1_electron_number_common import sha256
from s1_g1_kmp_runtime_contract import validate_kmp_runtime_contract
from validate_s1_electron_number_audit import (
    replay_evidence as replay_evidence_r1,
    validate_registration as validate_registration_r1,
)
from validate_s1_electron_number_audit_r2 import (
    CONFIG_PATH,
    MANIFEST_PATH,
    replay_evidence as replay_evidence_r2,
    validate_registration as validate_registration_r2,
)


OUTPUT_DIRECTORY = Path("analysis/s1/electron_number_audit_r2_20260805")
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
    "evidence_revision",
    "kmp_contract_accepted",
    "kmp_rank_lifecycle_count",
    "kmp_successful_syscall_count",
    "point_accepted",
)


def _git(project_root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(project_root), *args], text=True
    ).strip()


def _project_path(project_root: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty project-relative path")
    path = (project_root / value).resolve()
    try:
        path.relative_to(project_root.resolve())
    except ValueError as error:
        raise ValueError(f"{label} resolves outside the project: {value}") from error
    return path


def _validate_kmp(
    run_directory: Path,
    contract: dict,
    *,
    require_registered_mapping_pattern: bool,
) -> dict:
    """Call the frozen contract, explicitly distinguishing R1 bridge evidence."""

    payload = validate_kmp_runtime_contract(
        run_directory,
        contract["libomp"]["path"],
        contract["libomp"]["realpath"],
        contract["libomp"]["sha256"],
        require_registered_mapping_pattern=require_registered_mapping_pattern,
    )
    expected = {
        "accepted": True,
        "rank_count": contract["rank_count_per_run"],
        "lifecycle_count": contract["lifecycle_count_per_run"],
        "successful_syscall_count": contract["successful_syscall_count_per_run"],
        "libomp_mapping_count": contract["rank_count_per_run"],
    }
    mismatches = [
        f"{key}={payload.get(key)!r}, expected {value!r}"
        for key, value in expected.items()
        if payload.get(key) != value
    ]
    if mismatches:
        raise ValueError(
            f"{run_directory.name}: KMP runtime summary mismatch: " + "; ".join(mismatches)
        )
    return payload


def _point(
    row: dict[str, str],
    integration: dict,
    equivalence: dict | None,
    *,
    evidence_revision: str,
    kmp: dict | None,
) -> dict[str, object]:
    scientific_accepted: object = (
        equivalence["accepted"]
        if equivalence is not None
        else "not_applicable_existing_density"
    )
    kmp_accepted: object = (
        kmp["accepted"] if kmp is not None else "not_applicable_existing_density"
    )
    accepted = integration.get("accepted") is True
    point_accepted = (
        accepted
        and (equivalence is None or scientific_accepted is True)
        and (kmp is None or kmp_accepted is True)
    )
    return {
        "source_experiment_id": row["source_experiment_id"],
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
        "accepted": accepted,
        "delta_energy_mev_per_atom": (
            equivalence["delta_energy_mev_per_atom"] if equivalence else ""
        ),
        "delta_pressure_gpa": (
            equivalence["delta_pressure_gpa"] if equivalence else ""
        ),
        "scientific_equivalence_accepted": scientific_accepted,
        "evidence_revision": evidence_revision,
        "kmp_contract_accepted": kmp_accepted,
        "kmp_rank_lifecycle_count": kmp["lifecycle_count"] if kmp else "",
        "kmp_successful_syscall_count": (
            kmp["successful_syscall_count"] if kmp else ""
        ),
        "point_accepted": point_accepted,
    }


def analyze(project_root: Path, config_path: Path, manifest_path: Path) -> dict:
    project_root = project_root.resolve()
    if _git(project_root, "status", "--porcelain"):
        raise ValueError("analysis requires a clean worktree")

    config, r2_rows, r2_details = validate_registration_r2(
        project_root,
        config_path,
        manifest_path,
        require_committed=True,
    )
    bridge = config["r1_bridge"]
    r1_config_path = _project_path(
        project_root, bridge["config_path"], "R1 bridge config_path"
    )
    r1_manifest_path = _project_path(
        project_root, bridge["manifest_path"], "R1 bridge manifest_path"
    )
    r1_config, r1_rows, r1_details = validate_registration_r1(
        project_root,
        r1_config_path,
        r1_manifest_path,
        require_committed=True,
    )

    output_directory = project_root / OUTPUT_DIRECTORY
    if output_directory.exists() or output_directory.is_symlink():
        raise ValueError(f"refusing to overwrite analysis: {OUTPUT_DIRECTORY}")

    reused_ids = tuple(bridge["reused_audit_ids"])
    r2_ids = tuple(config["execution"]["r2_audit_ids"])
    if len(reused_ids) != 11 or len(set(reused_ids)) != 11:
        raise ValueError("R1 bridge must contain 11 unique reused audit IDs")
    if len(r2_ids) != 19 or len(set(r2_ids)) != 19:
        raise ValueError("R2 execution must contain 19 unique audit IDs")
    if set(reused_ids) & set(r2_ids):
        raise ValueError("R1 reused and R2 executed audit IDs overlap")

    r1_by_audit = {
        row["audit_experiment_id"]: row
        for row in r1_rows
        if row["audit_experiment_id"]
    }
    r2_by_audit = {row["audit_experiment_id"]: row for row in r2_rows}
    if set(r2_by_audit) != set(r2_ids):
        raise ValueError("R2 validator rows differ from registered execution IDs")
    of_audit_ids = {
        row["audit_experiment_id"] for row in r1_rows if row["solver"] == "ofdft"
    }
    if of_audit_ids != set(reused_ids) | set(r2_ids):
        raise ValueError("11+19 OF audit partition does not reconstruct the R1 denominator")

    contract = config["kmp_contract"]
    reused_payloads: dict[str, tuple[dict, dict]] = {}
    for audit_id in reused_ids:
        row = r1_by_audit[audit_id]
        payload, errors = replay_evidence_r1(
            project_root,
            r1_config,
            row,
            require_committed=True,
            require_replay_status=True,
        )
        if errors:
            raise ValueError(
                f"cannot reuse R1 evidence {audit_id}:\n- " + "\n- ".join(errors)
            )
        kmp = _validate_kmp(
            project_root / "runs" / audit_id,
            contract,
            require_registered_mapping_pattern=False,
        )
        reused_payloads[audit_id] = (payload, kmp)

    r2_payloads: dict[str, dict] = {}
    for audit_id in r2_ids:
        payload, errors = replay_evidence_r2(
            project_root,
            config,
            r2_by_audit[audit_id],
            require_committed=True,
            require_replay_status=True,
        )
        if errors:
            raise ValueError(
                f"cannot analyze R2 evidence {audit_id}:\n- " + "\n- ".join(errors)
            )
        _validate_kmp(
            project_root / "runs" / audit_id,
            contract,
            require_registered_mapping_pattern=True,
        )
        r2_payloads[audit_id] = payload

    points: list[dict[str, object]] = []
    for row in r1_rows:
        audit_id = row["audit_experiment_id"]
        if row["solver"] == "ksdft":
            integration = r1_details[row["source_experiment_id"]]["integration"]
            points.append(
                _point(
                    row,
                    integration,
                    None,
                    evidence_revision="r1_existing_ks_density",
                    kmp=None,
                )
            )
        elif audit_id in reused_payloads:
            payload, kmp = reused_payloads[audit_id]
            points.append(
                _point(
                    row,
                    payload["integration"],
                    payload["scientific_equivalence"],
                    evidence_revision="r1_reused_accepted_ofdft",
                    kmp=kmp,
                )
            )
        elif audit_id in r2_payloads:
            payload = r2_payloads[audit_id]
            points.append(
                _point(
                    row,
                    payload["integration"],
                    payload["scientific_equivalence"],
                    evidence_revision="r2_executed_ofdft",
                    kmp=payload["kmp_runtime_contract"],
                )
            )
        else:
            raise ValueError(f"unclassified denominator row: {row['source_experiment_id']}")

    if len({point["source_experiment_id"] for point in points}) != len(points):
        raise ValueError("analysis contains duplicate source experiment IDs")
    density_failures = [
        point["source_experiment_id"]
        for point in points
        if point["accepted"] is not True
    ]
    equivalence_failures = [
        point["audit_experiment_id"]
        for point in points
        if point["solver"] == "ofdft"
        and point["scientific_equivalence_accepted"] is not True
    ]
    kmp_failures = [
        point["audit_experiment_id"]
        for point in points
        if point["solver"] == "ofdft" and point["kmp_contract_accepted"] is not True
    ]
    point_failures = [
        point["source_experiment_id"]
        for point in points
        if point["point_accepted"] is not True
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
        "r1_reused_ofdft": sum(
            point["evidence_revision"] == "r1_reused_accepted_ofdft"
            for point in points
        ),
        "r2_executed_ofdft": sum(
            point["evidence_revision"] == "r2_executed_ofdft" for point in points
        ),
    }
    expected_coverage = {
        "primary_baseline": 84,
        "supplemental_runtime_replay": 6,
        "ks_existing_density": 60,
        "r1_reused_ofdft": 11,
        "r2_executed_ofdft": 19,
    }
    coverage_exact = len(points) == 90 and coverage_counts == expected_coverage
    of_points = [point for point in points if point["solver"] == "ofdft"]
    observed_lifecycles = sum(
        int(point["kmp_rank_lifecycle_count"]) for point in of_points
    )
    observed_syscalls = sum(
        int(point["kmp_successful_syscall_count"]) for point in of_points
    )
    required_lifecycles = 30 * int(contract["lifecycle_count_per_run"])
    required_syscalls = 30 * int(contract["successful_syscall_count_per_run"])
    kmp_totals_exact = (
        len(of_points) == 30
        and observed_lifecycles == required_lifecycles == 120
        and observed_syscalls == required_syscalls == 360
    )

    failure_archives = bridge["failure_archives"]
    if not isinstance(failure_archives, dict):
        raise ValueError("R1 bridge failure_archives must be an object")
    failed_130 = failure_archives.get("S1-20260805-130")
    if not isinstance(failed_130, dict):
        raise ValueError("R1 bridge lacks the archived S1-130 failure")
    archive_path = failed_130.get("archive_path")
    if not isinstance(archive_path, str) or not archive_path:
        raise ValueError("archived S1-130 failure lacks its registered archive path")
    current_130_points = [
        point
        for point in points
        if point["audit_experiment_id"] == "S1-20260805-130"
    ]
    failed_130_excluded = (
        len(current_130_points) == 1
        and current_130_points[0]["evidence_revision"] == "r2_executed_ofdft"
        and archive_path not in json.dumps(current_130_points[0], sort_keys=True)
    )

    status = (
        "accepted"
        if coverage_exact
        and not density_failures
        and not equivalence_failures
        and not kmp_failures
        and not point_failures
        and kmp_totals_exact
        and failed_130_excluded
        else "rejected"
    )
    summary = {
        "schema_version": 2,
        "protocol_revision": config["protocol_revision"],
        "status": status,
        "recommended_action": (
            "close_only_g1_electron_number_item_keep_other_five_g1_items_pending"
            if status == "accepted"
            else "retain_g1_electron_number_item_pending"
        ),
        "analyzer_commit": _git(project_root, "rev-parse", "HEAD"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "r2_config_path": str(config_path.relative_to(project_root)),
        "r2_config_sha256": sha256(config_path),
        "r2_manifest_path": str(manifest_path.relative_to(project_root)),
        "r2_manifest_sha256": sha256(manifest_path),
        "r1_config_path": str(r1_config_path.relative_to(project_root)),
        "r1_config_sha256": sha256(r1_config_path),
        "r1_manifest_path": str(r1_manifest_path.relative_to(project_root)),
        "r1_manifest_sha256": sha256(r1_manifest_path),
        "coverage": {
            "required": 90,
            "observed": len(points),
            "coverage_exact": coverage_exact,
            "accepted": sum(point["point_accepted"] is True for point in points),
            "breakdown": coverage_counts,
            "required_breakdown": expected_coverage,
            "ofdft_split": {
                "r1_reused_required": 11,
                "r1_reused_observed": coverage_counts["r1_reused_ofdft"],
                "r2_executed_required": 19,
                "r2_executed_observed": coverage_counts["r2_executed_ofdft"],
                "total_required": 30,
                "total_observed": len(of_points),
            },
        },
        "kmp_runtime_contract": {
            "accepted_ofdft_runs_required": 30,
            "accepted_ofdft_runs_observed": sum(
                point["kmp_contract_accepted"] is True for point in of_points
            ),
            "rank_lifecycles_required": 120,
            "rank_lifecycles_observed": observed_lifecycles,
            "successful_syscalls_required": 360,
            "successful_syscalls_observed": observed_syscalls,
            "totals_exact": kmp_totals_exact,
        },
        "historical_failure_archive": {
            "experiment_id": "S1-20260805-130",
            "provenance": failed_130,
            "root_cause_only": True,
            "excluded_from_acceptance_denominator": failed_130_excluded,
            "denominator_contribution": 0,
            "accepted_current_run_counted": len(current_130_points),
        },
        "accepted_count": sum(point["point_accepted"] is True for point in points),
        "density_failure_ids": density_failures,
        "scientific_equivalence_failure_ids": equivalence_failures,
        "kmp_contract_failure_ids": kmp_failures,
        "point_failure_ids": point_failures,
        "maximum_certified_relative_error": maximum["certified_relative_error"],
        "maximum_error_source_experiment_id": maximum["source_experiment_id"],
        "acceptance_limit_strict": config["acceptance"][
            "per_point_certified_relative_error_strictly_less_than"
        ],
        "registration_revalidation": {
            "r1_reused_payload_count": len(r2_details["r1_reused"]),
            "r2_row_count": len(r2_rows),
        },
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
    readme = f"""# S1 G1 incremental electron-number audit R2

- Status: `{status}`
- Coverage: `{len(points)}/90`
- OF evidence split: R1 reused `{coverage_counts['r1_reused_ofdft']}/11` + R2 executed `{coverage_counts['r2_executed_ofdft']}/19`
- Accepted points: `{summary['accepted_count']}/90`
- KMP rank lifecycles: `{observed_lifecycles}/120`
- Successful KMP lifecycle syscalls: `{observed_syscalls}/360`
- Maximum certified relative error: `{summary['maximum_certified_relative_error']:.17g}` at `{summary['maximum_error_source_experiment_id']}`
- Strict per-point limit: `<1e-10`
- OF scientific-equivalence failures: `{len(equivalence_failures)}`

The archived failed R1 attempt for `S1-20260805-130` is retained only as
root-cause provenance and contributes zero points to the 90-point acceptance
denominator. The accepted current R2 run for that registered ID is counted
exactly once.

KS densities are independently integrated from the reciprocal-space `G=0`
coefficient. OF densities are independently integrated from `out_chg 1 17`
cube values using the `STRU` cell volume. All 30 accepted OF runs also satisfy
the raw create/read/unlink KMP lifecycle contract for four ranks.

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
    summary = analyze(
        project_root,
        args.config.resolve(),
        args.manifest.resolve(),
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
