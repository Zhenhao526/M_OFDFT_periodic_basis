#!/usr/bin/env python3
"""Independently analyze the fixed G1 ten-case regeneration audit."""

from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path

from s1_g1_regeneration_10_common import (
    PROTOCOL_REVISION,
    canonical_bytes,
    read_config,
    read_manifest,
    scientific_case_analysis,
    sha256,
    write_atomic,
)

POINT_FIELDS = (
    "execution_index", "case_id", "source_run_id", "material", "solver", "profile",
    "status", "hostname", "wall_seconds", "energy_delta_mev_per_atom",
    "pressure_delta_gpa", "max_label_delta", "electron_certified_relative_error",
    "density_d1", "density_d2", "potential_dg", "potential_rms_ev",
    "raw_density_sha_equal", "raw_potential_sha_equal",
)
LABEL_FIELDS = (
    "case_id", "source_run_id", "material", "label", "source_ev_per_cell",
    "replay_ev_per_cell", "absolute_delta", "delta_units", "limit", "accepted",
)
EVIDENCE_FIELDS = ("relative_path", "sha256", "size_bytes")


def tsv_bytes(fields: tuple[str, ...], rows: list[dict[str, object]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field, "") for field in fields})
    return buffer.getvalue().encode()


def analyze(
    project_root: Path, config_path: Path, manifest_path: Path
) -> tuple[dict, list[dict[str, object]], list[dict[str, object]], list[dict[str, object]], dict[str, dict]]:
    config = read_config(config_path)
    rows = read_manifest(manifest_path)
    state = Path(config["external_state_root"])
    launch = json.loads((state / "launch.json").read_text(encoding="utf-8"))
    terminal = json.loads((state / "terminal.json").read_text(encoding="utf-8"))
    expected_ids = [row["case_id"] for row in rows]
    attempts = sorted(path.stem for path in (state / "attempts").glob("*.json"))
    cases = sorted(path.name for path in (state / "cases").iterdir() if path.is_dir())
    expected_commands = [
        [
            config["runtime"]["tools"]["python"]["path"], "-s",
            str(project_root / "scripts/run_s1_g1_regeneration_case_r1.py"),
            "--case-id", case_id,
        ]
        for case_id in expected_ids
    ]
    case_results: dict[str, dict] = {}
    point_rows, label_rows = [], []
    stored_match = True
    case_statuses = {}
    for row in rows:
        case_id = row["case_id"]
        result = scientific_case_analysis(project_root, state, row)
        case_results[case_id] = result
        stored = (state / "cases" / case_id / "scientific_result.json").read_bytes()
        stored_match = stored_match and stored == canonical_bytes(result)
        status = json.loads(
            (state / "cases" / case_id / "case_status.json").read_text(encoding="utf-8")
        )
        case_statuses[case_id] = status.get("status")
        thermo = result["thermodynamic_labels"]
        electron = result["electron_number"]
        density = result["density_field"]
        potential = result["potential_derivative_field"]
        diagnostics = result["raw_field_sha256_equal_diagnostic"]
        point_rows.append({
            "execution_index": row["execution_index"],
            "case_id": case_id,
            "source_run_id": row["source_run_id"],
            "material": row["material"],
            "solver": row["solver"],
            "profile": row["profile"],
            "status": "accepted" if result["accepted"] else "rejected",
            "hostname": result["runtime"]["hostname"],
            "wall_seconds": result["runtime"]["wall_seconds"],
            "energy_delta_mev_per_atom": result["scalar_equivalence"]["delta_energy_mev_per_atom"],
            "pressure_delta_gpa": result["scalar_equivalence"]["delta_pressure_gpa"],
            "max_label_delta": max(
                (item["absolute_delta"] for item in thermo["label_metrics"]), default=""
            ) if thermo else "",
            "electron_certified_relative_error": (
                electron["certified_relative_error"] if electron else ""
            ),
            "density_d1": density["d1"] if density else "",
            "density_d2": density["d2"] if density else "",
            "potential_dg": potential["dg"] if potential else "",
            "potential_rms_ev": potential["absolute_rms_ev"] if potential else "",
            "raw_density_sha_equal": diagnostics["density"] if diagnostics else "",
            "raw_potential_sha_equal": diagnostics["potential"] if diagnostics else "",
        })
        if thermo:
            for metric in thermo["label_metrics"]:
                label_rows.append({
                    "case_id": case_id,
                    "source_run_id": row["source_run_id"],
                    "material": row["material"],
                    **metric,
                })
    evidence_rows = []
    for path in sorted(state.rglob("*"), key=lambda item: str(item.relative_to(state))):
        if path.is_file():
            evidence_rows.append({
                "relative_path": str(path.relative_to(state)),
                "sha256": sha256(path),
                "size_bytes": path.stat().st_size,
            })
    registered = len(expected_ids)
    attempted = len(set(attempts) & set(expected_ids))
    completed = sum(case_statuses.get(case_id) == "accepted" for case_id in expected_ids)
    accepted = sum(bool(case_results[case_id]["accepted"]) for case_id in expected_ids)
    failed = sum((state / "cases" / case_id / "failure.json").exists() for case_id in expected_ids)
    missing = len(set(expected_ids) - set(cases))
    skipped = len(set(expected_ids) - set(attempts))
    retried = 0
    denominator_gate = {
        "registered": registered, "attempted": attempted, "completed": completed,
        "accepted": accepted, "failed": failed, "missing": missing,
        "skipped": skipped, "retried": retried,
        "accepted_gate": (
            (registered, attempted, completed, accepted) == (10, 10, 10, 10)
            and (failed, missing, skipped, retried) == (0, 0, 0, 0)
            and attempts == expected_ids and cases == expected_ids
        ),
    }
    hard_gates = {
        "terminal_accepted_runner_return_code_zero": (
            terminal.get("status") == "accepted"
            and terminal.get("runner_return_code") == 0
            and terminal.get("runner_commit") == launch.get("runner_commit")
        ),
        "fixed_denominator_and_no_retry": denominator_gate["accepted_gate"],
        "exact_case_command_order": launch.get("case_commands_exact") == expected_commands,
        "all_source_and_replay_scientific_gates": accepted == 10,
        "all_runtime_hostname_rank_affinity_gates": all(
            result["runtime_affinity_accepted"] for result in case_results.values()
        ),
        "all_runtime_command_environment_gates": all(
            result["runtime_contract_accepted"] for result in case_results.values()
        ),
        "stored_runtime_analysis_matches_independent_reparse": stored_match,
        "source_tree_and_hash_contract": all(
            result["source_integrity"]["tree_oid"] == row["source_tree_oid"]
            for row, result in zip(rows, case_results.values())
        ),
        "thermodynamic_label_rows_exact": len(label_rows) == 72,
    }
    summary = {
        "schema_version": 1,
        "protocol_revision": PROTOCOL_REVISION,
        "status": "accepted" if all(hard_gates.values()) else "rejected",
        "runner_commit": launch.get("runner_commit"),
        "external_state_root": str(state),
        "terminal_sha256": sha256(state / "terminal.json"),
        "denominator": denominator_gate,
        "hard_gates": hard_gates,
        "thresholds": config["acceptance_thresholds"],
        "maxima": {
            "energy_delta_mev_per_atom": max(
                item["scalar_equivalence"]["delta_energy_mev_per_atom"]
                for item in case_results.values()
            ),
            "pressure_delta_gpa": max(
                item["scalar_equivalence"]["delta_pressure_gpa"]
                for item in case_results.values()
            ),
            "thermodynamic_label_delta": max(
                item["absolute_delta"] for item in label_rows
            ),
            "electron_certified_relative_error": max(
                item["electron_number"]["certified_relative_error"]
                for item in case_results.values() if item["electron_number"]
            ),
            "density_d1": max(
                item["density_field"]["d1"] for item in case_results.values()
                if item["density_field"]
            ),
            "density_d2": max(
                item["density_field"]["d2"] for item in case_results.values()
                if item["density_field"]
            ),
            "potential_derivative_dg": max(
                item["potential_derivative_field"]["dg"] for item in case_results.values()
                if item["potential_derivative_field"]
            ),
            "potential_derivative_rms_ev": max(
                item["potential_derivative_field"]["absolute_rms_ev"]
                for item in case_results.values() if item["potential_derivative_field"]
            ),
        },
        "runtime": {
            "hostname": sorted({item["runtime"]["hostname"] for item in case_results.values()}),
            "cpu_list": config["cpu_list"],
            "rank_count": config["rank_count"],
            "solver_wall_seconds_sum": sum(
                float(item["runtime"]["wall_seconds"]) for item in case_results.values()
            ),
            "external_evidence_file_count": len(evidence_rows),
            "external_evidence_bytes": sum(int(item["size_bytes"]) for item in evidence_rows),
        },
        "raw_field_sha_equality_is_diagnostic_only": True,
        "zero_temperature_exact_claim": False,
    }
    return summary, point_rows, label_rows, evidence_rows, case_results


def readme(summary: dict) -> bytes:
    maxima, denominator = summary["maxima"], summary["denominator"]
    gates = "\n".join(
        f"- {'PASS' if accepted else 'FAIL'}: {name}"
        for name, accepted in summary["hard_gates"].items()
    )
    text = f"""# G1 fixed ten-case single-command regeneration R1

Status: **{summary['status']}**. The immutable denominator is
registered/attempted/completed/accepted =
{denominator['registered']}/{denominator['attempted']}/{denominator['completed']}/{denominator['accepted']};
failed/missing/skipped/retried =
{denominator['failed']}/{denominator['missing']}/{denominator['skipped']}/{denominator['retried']}.

## Hard gates

{gates}

## Worst observed differences

- Energy: {maxima['energy_delta_mev_per_atom']:.12g} meV/atom.
- Pressure: {maxima['pressure_delta_gpa']:.12g} GPa.
- Thermodynamic label: {maxima['thermodynamic_label_delta']:.12g} meV/atom (or meV for mu).
- Certified electron relative error: {maxima['electron_certified_relative_error']:.12g}.
- Density D1/D2: {maxima['density_d1']:.12g} / {maxima['density_d2']:.12g}.
- Potential derivative dg/RMS: {maxima['potential_derivative_dg']:.12g} / {maxima['potential_derivative_rms_ev']:.12g} eV.

Raw cube SHA equality is diagnostic only. All field decisions use the registered
geometry-aware density and gauge-projected potential metrics. Finite-smearing
labels are not represented as exact zero-temperature quantities.
"""
    return text.encode()


def write_outputs(
    output: Path, summary: dict, points: list[dict[str, object]],
    labels: list[dict[str, object]], evidence: list[dict[str, object]],
    cases: dict[str, dict],
) -> None:
    output.mkdir(parents=True, exist_ok=False)
    write_atomic(output / "summary.json", canonical_bytes(summary))
    write_atomic(output / "points.tsv", tsv_bytes(POINT_FIELDS, points))
    write_atomic(output / "label_metrics.tsv", tsv_bytes(LABEL_FIELDS, labels))
    write_atomic(output / "evidence_manifest.tsv", tsv_bytes(EVIDENCE_FIELDS, evidence))
    write_atomic(output / "README.md", readme(summary))
    for case_id, payload in cases.items():
        write_atomic(output / "cases" / f"{case_id}.json", canonical_bytes(payload))


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", type=Path,
        default=project_root / "config/S1_g1_regeneration_10_r1.json",
    )
    parser.add_argument(
        "--manifest", type=Path,
        default=project_root / "config/S1_g1_regeneration_10_r1_manifest.tsv",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    config_path, manifest_path = args.config.resolve(), args.manifest.resolve()
    config = read_config(config_path)
    output = args.output.resolve() if args.output else project_root / config["analysis_directory"]
    values = analyze(project_root, config_path, manifest_path)
    write_outputs(output, *values)
    print(json.dumps(values[0], indent=2, sort_keys=True))
    return 0 if values[0]["status"] == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
