#!/usr/bin/env python3
"""Analyze the 10 reused + 30 newly executed logical slots of G1 R2."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path

from analyze_s1_g1_thermodynamic_label_audit_r1 import (
    FAILURE_CATEGORIES,
    RATIOS,
    _field_pair,
    _fit,
    _point_from_run,
    _source_ids,
    classify_run_failures,
    compare_adjacent,
    has_capability_or_evidence_failure,
)
from s1_g1_thermodynamic_label_common import MANIFEST_FIELDS
from validate_s1_g1_thermodynamic_label_audit_r2 import (
    COMMON_QUARTER_LOGICAL_IDS,
    CONFIG_PATH,
    EXTRA_QUARTER_LOGICAL_IDS,
    HALF_LOGICAL_IDS,
    LOGICAL_IDS,
    MANIFEST_PATH,
    PROTOCOL_REVISION,
    R1_REUSED_AUDIT_IDS,
    R2_AUDIT_IDS,
    STANDARD_LOGICAL_IDS,
    evaluate_k_gate,
    logical_effective_id,
    replay_effective_evidence,
    source_kind,
    validate_registration,
)


DEFAULT_OUTPUT = Path("analysis/s1/g1_thermodynamic_label_audit_r2_20260806")


def _git_clean(project_root: Path) -> bool:
    return not subprocess.check_output(
        ["git", "-C", str(project_root), "status", "--porcelain"], text=True
    )


def _atomic_write(path: Path, data: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(data, encoding="utf-8")
    os.replace(temporary, path)


def _stable_content_bytes(path: Path, *, allow_proc_fd: bool) -> tuple[bytes, str]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    if not allow_proc_fd:
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise ValueError("stable analysis registration read requires O_NOFOLLOW")
        flags |= nofollow
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"analysis registration input is not regular: {path}")
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
    if any(getattr(before, name) != getattr(after, name) for name in fields) or len(
        payload
    ) != before.st_size:
        raise ValueError(f"analysis registration input changed or was read short: {path}")
    return payload, hashlib.sha256(payload).hexdigest()


def _write_readonly_snapshot(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ValueError("short analysis registration snapshot write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(path, 0o400)


def _manifest_from_bytes(payload: bytes) -> list[dict[str, str]]:
    with io.StringIO(payload.decode("utf-8"), newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if tuple(reader.fieldnames or ()) != MANIFEST_FIELDS:
            raise ValueError("sealed scientific manifest header differs")
        rows = list(reader)
    if not all(
        None not in row and all(value is not None for value in row.values())
        for row in rows
    ):
        raise ValueError("sealed scientific manifest row is malformed")
    return rows


def _logical_series(material: str, level: str) -> tuple[str, ...]:
    mapping = {
        ("al", "half"): HALF_LOGICAL_IDS[:7],
        ("mg", "half"): HALF_LOGICAL_IDS[7:],
        ("al", "quarter"): COMMON_QUARTER_LOGICAL_IDS[:7],
        ("mg", "quarter"): COMMON_QUARTER_LOGICAL_IDS[7:],
    }
    return tuple(mapping[(material, level)])


def _readme(summary: dict[str, object]) -> str:
    failures = summary["failure_ids"]
    assert isinstance(failures, dict)
    lines = [
        "# S1 G1 thermodynamic-label audit R2",
        "",
        f"- Audit status: `{summary['audit_status']}`",
        f"- Effective accepted logical runs: `{summary['accepted_effective_run_count']}/40`",
        f"- Immutable R1 runs reused: `{summary['accepted_reused_r1_count']}/10`",
        f"- New R2 runs accepted: `{summary['accepted_new_r2_count']}/30`",
        f"- Main EOS scalar points: `{summary['main_eos_scalar_count']}/42`",
        f"- Overall protocol status: `{summary['overall_protocol_status']}`",
        f"- G1 status before committed supervisor completion: `{summary['g1_status']}`",
        "",
        "The R2 denominator is an exact logical partition: ten immutable R1 accepted "
        "runs plus thirty new R2 runs. The archived indeterminate R1-034 attempt "
        "contributes zero. All scalar and field labels retain their finite-temperature "
        "Mermin interpretation; no exact-zero-temperature field claim is made.",
        "",
        "## Failure ledger",
        "",
    ]
    for category in FAILURE_CATEGORIES:
        values = failures[category]
        lines.append(f"- `{category}`: {', '.join(values) if values else 'none'}")
    return "\n".join(lines) + "\n"


def analyze(
    project_root: Path,
    config_path: Path,
    manifest_path: Path,
    output_directory: Path,
    *,
    require_committed: bool = True,
    skip_terminal_evidence_validation: bool = False,
    scientific_config_path: Path | None = None,
    scientific_manifest_path: Path | None = None,
) -> dict[str, object]:
    project_root = project_root.resolve()
    if (scientific_config_path is None) != (scientific_manifest_path is None):
        raise ValueError(
            "scientific config and manifest must be supplied together"
        )
    if output_directory.exists() or output_directory.is_symlink():
        raise ValueError(f"refusing to overwrite analysis output: {output_directory}")
    if require_committed and not _git_clean(project_root):
        raise ValueError("refusing final R2 analysis from a dirty worktree")
    validated_config, validated_rows, registration = validate_registration(
        project_root,
        config_path.resolve(),
        manifest_path.resolve(),
        require_committed=require_committed,
        skip_terminal_evidence_validation=skip_terminal_evidence_validation,
    )
    content_config_path = scientific_config_path or config_path
    content_manifest_path = scientific_manifest_path or manifest_path
    config_raw, content_config_sha256 = _stable_content_bytes(
        content_config_path, allow_proc_fd=scientific_config_path is not None
    )
    manifest_raw, content_manifest_sha256 = _stable_content_bytes(
        content_manifest_path, allow_proc_fd=scientific_manifest_path is not None
    )
    config = json.loads(config_raw.decode("utf-8"))
    rows = _manifest_from_bytes(manifest_raw)
    if config != validated_config or rows != validated_rows:
        raise ValueError(
            "sealed scientific config/manifest differ from canonical provenance"
        )
    registration_snapshot: tempfile.TemporaryDirectory[str] | None = None
    replay_scientific_config = scientific_config_path
    replay_scientific_manifest = scientific_manifest_path
    if replay_scientific_config is None:
        registration_snapshot = tempfile.TemporaryDirectory(
            prefix="m-ofdft-g1-r2-analysis-registration-"
        )
        snapshot_root = Path(registration_snapshot.name)
        replay_scientific_config = snapshot_root / "config.json"
        replay_scientific_manifest = snapshot_root / "manifest.tsv"
        _write_readonly_snapshot(replay_scientific_config, config_raw)
        _write_readonly_snapshot(replay_scientific_manifest, manifest_raw)

    logical_to_effective = {
        logical: logical_effective_id(config, logical) for logical in LOGICAL_IDS
    }
    if len(logical_to_effective) != 40 or len(set(logical_to_effective.values())) != 40:
        raise ValueError("R2 logical/effective mapping is not an exact 40-slot bijection")
    if logical_to_effective["S1-20260806-034"] == "S1-20260806-034":
        raise ValueError("archived R1-034 is forbidden from the R2 acceptance denominator")

    failures: dict[str, list[str]] = {name: [] for name in FAILURE_CATEGORIES}
    capability_failures: list[str] = []
    evidence_by_logical: dict[str, dict[str, object]] = {}
    evidence_origin: dict[str, str] = {}
    for logical in LOGICAL_IDS:
        payload, run_failures = replay_effective_evidence(
            project_root,
            config,
            rows,
            logical,
            require_committed=require_committed,
            require_replay_status=True,
            scientific_config_path=replay_scientific_config,
            scientific_manifest_path=replay_scientific_manifest,
        )
        effective = logical_to_effective[logical]
        if run_failures:
            failures["completion"].append(effective)
            classified = classify_run_failures(run_failures)
            for category in classified:
                failures[category].append(effective)
            numerical_only = (
                not has_capability_or_evidence_failure(run_failures)
                and bool(classified)
                and classified.issubset(
                    {"thermodynamic_identity", "electron_number", "replay_equivalence"}
                )
            )
            if not numerical_only:
                capability_failures.append(effective)
            continue
        evidence_by_logical[logical] = payload
        evidence_origin[logical] = source_kind(logical)
        electron = payload.get("electron_number_integration")
        if not isinstance(electron, dict) or electron.get("accepted") is not True:
            failures["electron_number"].append(effective)
        kmp = payload.get("kmp_runtime_contract")
        if not isinstance(kmp, dict) or kmp.get("accepted") is not True:
            failures["runtime_kmp"].append(effective)
        if logical in STANDARD_LOGICAL_IDS:
            equivalence = payload.get("standard_replay_equivalence")
            if not isinstance(equivalence, dict) or equivalence.get("accepted") is not True:
                failures["replay_equivalence"].append(effective)

    series: dict[str, dict[str, object]] = {}
    all_points: list[dict[str, object]] = []
    for material in ("al", "mg"):
        specifications = (
            ("standard", _source_ids(material), None),
            ("half", tuple(logical_to_effective[item] for item in _logical_series(material, "half")), _logical_series(material, "half")),
            ("quarter", tuple(logical_to_effective[item] for item in _logical_series(material, "quarter")), _logical_series(material, "quarter")),
        )
        for level, effective_ids, logical_ids in specifications:
            points: list[dict[str, object]] = []
            for index, (ratio, effective) in enumerate(zip(RATIOS, effective_ids)):
                try:
                    point = _point_from_run(
                        project_root / "runs" / effective,
                        experiment_id=effective,
                        material=material,
                        level=level,
                        volume_ratio=ratio,
                    )
                    if logical_ids is not None:
                        logical = logical_ids[index]
                        point["logical_experiment_id"] = logical
                        point["evidence_origin"] = evidence_origin.get(logical, source_kind(logical))
                    else:
                        point["evidence_origin"] = "immutable_dense_standard_scalar_source"
                    points.append(point)
                except (FileNotFoundError, KeyError, TypeError, ValueError) as error:
                    category = "source_integrity" if level == "standard" else "scf"
                    failures[category].append(effective)
                    capability_failures.append(effective)
                    points.append({"experiment_id": effective, "error": str(error)})
            valid = [point for point in points if "error" not in point]
            fit, fit_failures = _fit(valid) if len(valid) == 7 else ({}, ["incomplete"])
            key = f"{material}/{level}"
            if fit_failures:
                failures["eos_fit"].append(key)
            series[key] = {
                "status": "accepted" if not fit_failures else "rejected",
                "points": points,
                "fit": fit,
                "fit_failures": fit_failures,
            }
            all_points.extend(valid)

    comparisons: dict[str, dict[str, object]] = {}
    for material in ("al", "mg"):
        for coarse_level, fine_level in (("standard", "half"), ("half", "quarter")):
            key = f"{material}/{coarse_level}_to_{fine_level}"
            coarse = series[f"{material}/{coarse_level}"]
            fine = series[f"{material}/{fine_level}"]
            if coarse["status"] != "accepted" or fine["status"] != "accepted":
                comparisons[key] = {"accepted": False, "reason": "invalid_input_fit"}
                failures["adjacent_smearing_energy"].append(key)
                failures["equilibrium_volume"].append(key)
                continue
            comparison = compare_adjacent(
                coarse["points"],  # type: ignore[arg-type]
                fine["points"],  # type: ignore[arg-type]
                coarse["fit"],  # type: ignore[arg-type]
                fine["fit"],  # type: ignore[arg-type]
            )
            comparisons[key] = comparison
            if comparison["energy_accepted"] is not True:
                failures["adjacent_smearing_energy"].append(key)
            if comparison["volume_accepted"] is not True:
                failures["equilibrium_volume"].append(key)

    smearing_diagnostics: dict[str, list[dict[str, object]]] = {}
    for material in ("al", "mg"):
        for coarse_level, fine_level in (("standard", "half"), ("half", "quarter")):
            key = f"{material}/{coarse_level}_to_{fine_level}"
            coarse_points = {
                float(point["volume_ratio"]): point
                for point in series[f"{material}/{coarse_level}"]["points"]  # type: ignore[index]
                if "error" not in point
            }
            fine_points = {
                float(point["volume_ratio"]): point
                for point in series[f"{material}/{fine_level}"]["points"]  # type: ignore[index]
                if "error" not in point
            }
            diagnostics = []
            for ratio in sorted(set(coarse_points) & set(fine_points)):
                coarse_m = abs(float(coarse_points[ratio]["entropy_minus_ts_ev_per_atom"]))
                fine_m = abs(float(fine_points[ratio]["entropy_minus_ts_ev_per_atom"]))
                diagnostics.append(
                    {
                        "volume_ratio": ratio,
                        "coarse_abs_m_ev_per_atom": coarse_m,
                        "fine_abs_m_ev_per_atom": fine_m,
                        "fine_to_coarse_abs_m_ratio": fine_m / coarse_m if coarse_m else None,
                        "coarse_pressure_gpa": float(coarse_points[ratio]["pressure_gpa"]),
                        "fine_pressure_gpa": float(fine_points[ratio]["pressure_gpa"]),
                        "pressure_difference_gpa": float(fine_points[ratio]["pressure_gpa"])
                        - float(coarse_points[ratio]["pressure_gpa"]),
                        "acceptance_role": "mandatory_diagnostic_only",
                    }
                )
            smearing_diagnostics[key] = diagnostics

    label_metrics: list[dict[str, object]] = []
    for half_logical, quarter_logical in zip(HALF_LOGICAL_IDS, COMMON_QUARTER_LOGICAL_IDS):
        half = logical_to_effective[half_logical]
        quarter = logical_to_effective[quarter_logical]
        pair = f"{half}:{quarter}"
        try:
            metric = _field_pair(project_root, half, quarter)
            metric.update(
                {
                    "comparison_kind": "half_to_quarter_hard_gate",
                    "coarse_logical_experiment_id": half_logical,
                    "reference_logical_experiment_id": quarter_logical,
                }
            )
            label_metrics.append(metric)
            if not float(metric["d1"]) < 0.005 or not float(metric["d2"]) < 0.005:
                failures["density"].append(pair)
            if not float(metric["dg"]) < 0.01 or not float(metric["rms_g_ev"]) < 0.005:
                failures["derivative"].append(pair)
        except (FileNotFoundError, KeyError, TypeError, ValueError) as error:
            failures["density"].append(pair)
            failures["derivative"].append(pair)
            capability_failures.append(pair)
            label_metrics.append(
                {"comparison_kind": "half_to_quarter_hard_gate", "pair": pair, "error": str(error)}
            )

    standard_half_pairs = ((1, 7), (2, 10), (3, 13), (4, 14), (5, 17), (6, 20))
    for standard_number, half_number in standard_half_pairs:
        standard_logical = f"S1-20260806-{standard_number:03d}"
        half_logical = f"S1-20260806-{half_number:03d}"
        standard = logical_to_effective[standard_logical]
        half = logical_to_effective[half_logical]
        pair = f"{standard}:{half}"
        try:
            metric = _field_pair(project_root, standard, half)
            metric.update(
                {
                    "comparison_kind": "standard_to_half_diagnostic_only",
                    "coarse_logical_experiment_id": standard_logical,
                    "reference_logical_experiment_id": half_logical,
                }
            )
            label_metrics.append(metric)
        except (FileNotFoundError, KeyError, TypeError, ValueError) as error:
            failures["density"].append(pair)
            failures["derivative"].append(pair)
            capability_failures.append(pair)
            label_metrics.append(
                {"comparison_kind": "standard_to_half_diagnostic_only", "pair": pair, "error": str(error)}
            )

    try:
        k_gate = evaluate_k_gate(
            project_root, config, rows, require_committed=require_committed
        )
        if k_gate.get("accepted") is not True:
            failures["k_gate"].extend(["al", "mg"])
        for pair in k_gate.get("pairs", []):
            fields = pair["field_metrics"]
            label_metrics.append(
                {
                    "comparison_kind": "common_to_extra_k_hard_gate",
                    "coarse_experiment_id": pair["coarse_experiment_id"],
                    "reference_experiment_id": pair["reference_experiment_id"],
                    "coarse_logical_experiment_id": pair["coarse_logical_experiment_id"],
                    "reference_logical_experiment_id": pair["reference_logical_experiment_id"],
                    **fields,
                }
            )
    except (FileNotFoundError, KeyError, TypeError, ValueError) as error:
        k_gate = {"accepted": False, "error": str(error)}
        failures["k_gate"].extend(["al", "mg"])
        capability_failures.append("k_gate")

    runtime_by_origin = {
        "r1_reused": {"accepted_run_count": 0, "rank_lifecycle_count": 0, "successful_syscall_count": 0},
        "r2_executed": {"accepted_run_count": 0, "rank_lifecycle_count": 0, "successful_syscall_count": 0},
    }
    for logical, payload in evidence_by_logical.items():
        origin = source_kind(logical)
        kmp = payload.get("kmp_runtime_contract")
        if not isinstance(kmp, dict):
            continue
        runtime_by_origin[origin]["accepted_run_count"] += 1
        runtime_by_origin[origin]["rank_lifecycle_count"] += int(kmp["lifecycle_count"])
        runtime_by_origin[origin]["successful_syscall_count"] += int(kmp["successful_syscall_count"])
    lifecycle_count = sum(value["rank_lifecycle_count"] for value in runtime_by_origin.values())
    syscall_count = sum(value["successful_syscall_count"] for value in runtime_by_origin.values())
    expected_runtime = {
        "r1_reused": {"accepted_run_count": 10, "rank_lifecycle_count": 40, "successful_syscall_count": 120},
        "r2_executed": {"accepted_run_count": 30, "rank_lifecycle_count": 120, "successful_syscall_count": 360},
    }
    if runtime_by_origin != expected_runtime:
        failures["runtime_kmp"].append("aggregate_partition")

    for category in failures:
        failures[category] = sorted(set(failures[category]))
    accepted_reused = sum(logical in evidence_by_logical for logical in R1_REUSED_AUDIT_IDS)
    accepted_new = sum(logical in evidence_by_logical for logical in LOGICAL_IDS if logical not in R1_REUSED_AUDIT_IDS)
    exact_counts = {
        "accepted_effective_runs": len(evidence_by_logical),
        "accepted_reused_r1_runs": accepted_reused,
        "accepted_new_r2_runs": accepted_new,
        "main_eos_scalar_points": len(all_points),
        "half_quarter_field_pairs": sum(
            row.get("comparison_kind") == "half_to_quarter_hard_gate" and "error" not in row
            for row in label_metrics
        ),
        "standard_half_diagnostic_pairs": sum(
            row.get("comparison_kind") == "standard_to_half_diagnostic_only" and "error" not in row
            for row in label_metrics
        ),
        "k_anchor_pairs": len(k_gate.get("pairs", [])) if isinstance(k_gate, dict) else 0,
        "kmp_rank_lifecycles": lifecycle_count,
        "kmp_successful_syscalls": syscall_count,
    }
    expected_counts = {
        "accepted_effective_runs": 40,
        "accepted_reused_r1_runs": 10,
        "accepted_new_r2_runs": 30,
        "main_eos_scalar_points": 42,
        "half_quarter_field_pairs": 14,
        "standard_half_diagnostic_pairs": 6,
        "k_anchor_pairs": 6,
        "kmp_rank_lifecycles": 160,
        "kmp_successful_syscalls": 480,
    }
    if len(all_points) != 42:
        failures["completion"].append("main_eos_42_point_denominator")
    hard_accepted = (
        all(not values for values in failures.values())
        and exact_counts == expected_counts
        and len(series) == 6
        and all(payload["status"] == "accepted" for payload in series.values())
        and len(comparisons) == 4
        and all(payload["accepted"] is True for payload in comparisons.values())
        and k_gate.get("accepted") is True
    )
    status = "accepted" if hard_accepted else ("indeterminate_paused" if capability_failures else "rejected")
    summary: dict[str, object] = {
        "schema_version": 2,
        "protocol_revision": PROTOCOL_REVISION,
        "audit_status": status,
        "accepted_effective_run_count": len(evidence_by_logical),
        "accepted_reused_r1_count": accepted_reused,
        "accepted_new_r2_count": accepted_new,
        "main_eos_scalar_count": len(all_points),
        "logical_to_effective_id": logical_to_effective,
        "evidence_origin_by_logical_id": evidence_origin,
        "excluded_r1_failure": {
            "experiment_id": "S1-20260806-034",
            "status": "indeterminate_archived",
            "acceptance_contribution": 0,
            "posthoc_acceptance_forbidden": True,
        },
        "series": series,
        "adjacent_smearing_comparisons": comparisons,
        "smearing_entropy_pressure_diagnostics": smearing_diagnostics,
        "k_gate": k_gate,
        "runtime_kmp_aggregate": {
            "by_origin": runtime_by_origin,
            "accepted_run_count": len(evidence_by_logical),
            "rank_lifecycle_count": lifecycle_count,
            "successful_syscall_count": syscall_count,
            "accepted": runtime_by_origin == expected_runtime,
        },
        "exact_counts": exact_counts,
        "failure_ids": failures,
        "capability_failure_ids": sorted(set(capability_failures)),
        "overall_protocol_status": (
            "pending_supervisor_completion" if hard_accepted else status
        ),
        "g1_status": "pending (1/6)",
        "authorized_scope": "no_G1_advancement",
        "zero_temperature_exact_claim": False,
        "registration": registration,
        "config_sha256": content_config_sha256,
        "manifest_sha256": content_manifest_sha256,
    }

    output_directory.mkdir(parents=True, exist_ok=False)
    _atomic_write(
        output_directory / "summary.json",
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
    )
    with (output_directory / "points.tsv").open("x", encoding="utf-8", newline="") as handle:
        fields = (
            "material",
            "smearing_level",
            "volume_ratio",
            "volume_per_atom_angstrom3",
            "experiment_id",
            "logical_experiment_id",
            "evidence_origin",
            "e_ec_ev_per_atom",
            "free_energy_ev_per_atom",
            "entropy_minus_ts_ev_per_atom",
            "pressure_gpa",
        )
        writer = csv.DictWriter(
            handle, fieldnames=fields, delimiter="\t", lineterminator="\n", extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(
            sorted(
                all_points,
                key=lambda point: (
                    str(point["material"]),
                    str(point["smearing_level"]),
                    float(point["volume_ratio"]),
                ),
            )
        )
    metric_fields = (
        "comparison_kind",
        "coarse_experiment_id",
        "reference_experiment_id",
        "coarse_logical_experiment_id",
        "reference_logical_experiment_id",
        "d1",
        "d2",
        "dg",
        "rms_g_ev",
        "accepted",
        "error",
    )
    with (output_directory / "label_metrics.tsv").open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=metric_fields,
            delimiter="\t",
            lineterminator="\n",
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(label_metrics)
    _atomic_write(output_directory / "README.md", _readme(summary))
    if registration_snapshot is not None:
        registration_snapshot.cleanup()
    return summary


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output_directory", nargs="?", type=Path, default=project_root / DEFAULT_OUTPUT
    )
    parser.add_argument("--config", type=Path, default=project_root / CONFIG_PATH)
    parser.add_argument("--manifest", type=Path, default=project_root / MANIFEST_PATH)
    parser.add_argument("--scientific-config", type=Path)
    parser.add_argument("--scientific-manifest", type=Path)
    parser.add_argument("--allow-uncommitted", action="store_true")
    arguments = parser.parse_args()
    summary = analyze(
        project_root,
        arguments.config.resolve(),
        arguments.manifest.resolve(),
        arguments.output_directory.resolve(),
        require_committed=not arguments.allow_uncommitted,
        scientific_config_path=arguments.scientific_config,
        scientific_manifest_path=arguments.scientific_manifest,
    )
    print(
        json.dumps(
            {
                "audit_status": summary["audit_status"],
                "output_directory": str(arguments.output_directory.resolve()),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if summary["audit_status"] == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
