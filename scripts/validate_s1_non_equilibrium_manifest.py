#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
from pathlib import Path

from generate_s1_non_equilibrium_manifest import build_entries, sha256


HEADER = (
    "experiment_id",
    "input_directory",
    "material",
    "series_id",
    "comparison_axis",
    "volume_ratio",
    "reference_experiment_id",
    "input_metadata_sha256",
)


def _project_path(project_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else project_root / path


def read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if tuple(reader.fieldnames or ()) != HEADER:
            raise ValueError(f"invalid S1-R8 manifest header: {reader.fieldnames}")
        return list(reader)


def _core_points(summary: dict) -> dict[tuple[str, str, float], dict]:
    points = {}
    for series_key, payload in summary["series"].items():
        material, series_id = series_key.split("/", 1)
        for point in payload.get("points", []):
            points[(material, series_id, round(float(point["volume_ratio"]), 12))] = point
    return points


def _same_scalar(left, right) -> bool:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-12)
    return left == right


def validate(project_root: Path, config_path: Path, manifest_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config_digest = sha256(config_path)
    rows = read_manifest(manifest_path)
    expected = build_entries(config)
    errors = []

    if len(rows) != len(expected):
        errors.append(f"expected {len(expected)} manifest rows, found {len(rows)}")
    if len({row["experiment_id"] for row in rows}) != len(rows):
        errors.append("duplicate experiment IDs in manifest")
    if len({row["input_directory"] for row in rows}) != len(rows):
        errors.append("duplicate input directories in manifest")

    reference = config["core_reference"]
    core_summary_path = _project_path(project_root, reference["summary_path"])
    if sha256(core_summary_path) != reference["summary_sha256"]:
        errors.append("core EOS summary SHA-256 mismatch")
    core_summary = json.loads(core_summary_path.read_text(encoding="utf-8"))
    core_points = _core_points(core_summary)
    baseline_config_path = _project_path(project_root, reference["baseline_config_path"])
    if sha256(baseline_config_path) != reference["baseline_config_sha256"]:
        errors.append("baseline config SHA-256 mismatch")

    expected_by_id = {entry["experiment_id"]: entry for entry in expected}
    invariant_keys = (
        "atom_count",
        "energy_observable",
        "material",
        "pseudopotential_sha256",
        "solver",
        "structure",
        "structure_id",
        "stru_sha256",
        "xc",
    )

    for row in rows:
        experiment_id = row["experiment_id"]
        logical = expected_by_id.get(experiment_id)
        if logical is None:
            errors.append(f"{experiment_id}: outside frozen ID block")
            continue
        for key in ("input_directory", "material", "series_id"):
            if row[key] != str(logical[key]):
                errors.append(f"{experiment_id}: manifest {key} differs from registration")
        ratio = round(float(row["volume_ratio"]), 12)
        if ratio != round(float(logical["volume_ratio"]), 12):
            errors.append(f"{experiment_id}: manifest volume ratio differs from registration")

        input_directory = project_root / row["input_directory"]
        metadata_path = input_directory / "metadata.json"
        for required in ("INPUT", "STRU", "KPT", "metadata.json"):
            if not (input_directory / required).is_file():
                errors.append(f"{experiment_id}: missing input {required}")
        if not metadata_path.is_file():
            continue
        if sha256(metadata_path) != row["input_metadata_sha256"]:
            errors.append(f"{experiment_id}: input metadata SHA-256 mismatch")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        spec = config["materials"][row["material"]][row["series_id"]]
        expected_metadata = {
            "comparison_axis": spec["comparison_axis"],
            "config_sha256": config_digest,
            "core_summary_sha256": reference["summary_sha256"],
            "dataset_kind": "eos_non_equilibrium_convergence",
            "ecutrho_ry": spec["ecutrho_ry"],
            "ecutwfc_ry": spec["ecutwfc_ry"],
            "kmesh": spec["kmesh"],
            "material": row["material"],
            "protocol_revision": config["protocol_revision"],
            "series_id": row["series_id"],
            "smearing_sigma_ry": spec["smearing_sigma_ry"],
            "solver": spec["solver"],
        }
        for key, value in expected_metadata.items():
            if not _same_scalar(metadata.get(key), value):
                errors.append(f"{experiment_id}: metadata {key} mismatch")
        if metadata.get("comparison_axis") != row["comparison_axis"]:
            errors.append(f"{experiment_id}: comparison axis mismatch")
        if round(float(metadata.get("volume_ratio", -1.0)), 12) != ratio:
            errors.append(f"{experiment_id}: metadata volume ratio mismatch")
        if metadata.get("baseline_experiment_id") != row["reference_experiment_id"]:
            errors.append(f"{experiment_id}: reference experiment mismatch")
        if metadata.get("baseline_series_id") != spec["baseline_series_id"]:
            errors.append(f"{experiment_id}: baseline series mismatch")
        if metadata.get("baseline_config_sha256") != reference["baseline_config_sha256"]:
            errors.append(f"{experiment_id}: baseline config provenance mismatch")
        if int(metadata.get("ecutrho_ry", -1)) != 4 * int(metadata.get("ecutwfc_ry", 0)):
            errors.append(f"{experiment_id}: ecutrho is not four times ecutwfc")

        core_point = core_points.get(
            (row["material"], spec["baseline_series_id"], ratio)
        )
        if core_point is None:
            errors.append(f"{experiment_id}: missing frozen core reference point")
            continue
        if core_point["experiment_id"] != row["reference_experiment_id"]:
            errors.append(f"{experiment_id}: manifest reference not in frozen core summary")
        reference_run = project_root / "runs" / row["reference_experiment_id"]
        reference_metadata_path = reference_run / "input_metadata.json"
        reference_result_path = reference_run / "result.json"
        if not reference_metadata_path.is_file() or not reference_result_path.is_file():
            errors.append(f"{experiment_id}: reference run is incomplete")
            continue
        tracked = subprocess.run(
            [
                "git",
                "-C",
                str(project_root),
                "ls-files",
                "--error-unmatch",
                str(reference_result_path.relative_to(project_root)),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if tracked.returncode != 0:
            errors.append(f"{experiment_id}: reference result is not committed")
        reference_metadata = json.loads(reference_metadata_path.read_text(encoding="utf-8"))
        reference_result = json.loads(reference_result_path.read_text(encoding="utf-8"))
        if not reference_result.get("converged"):
            errors.append(f"{experiment_id}: reference run did not converge")
        for key in invariant_keys:
            if not _same_scalar(metadata.get(key), reference_metadata.get(key)):
                errors.append(f"{experiment_id}: non-axis invariant {key} changed")
        if not _same_scalar(
            metadata.get("smearing_sigma_ry"), reference_metadata.get("smearing_sigma_ry")
        ):
            errors.append(f"{experiment_id}: smearing changed")
        if spec["comparison_axis"] == "cutoff":
            if metadata["kmesh"] != reference_metadata["kmesh"]:
                errors.append(f"{experiment_id}: cutoff series changed kmesh")
            if float(metadata["ecutwfc_ry"]) <= float(reference_metadata["ecutwfc_ry"]):
                errors.append(f"{experiment_id}: cutoff was not refined")
        elif spec["comparison_axis"] == "kmesh":
            if metadata["ecutwfc_ry"] != reference_metadata["ecutwfc_ry"]:
                errors.append(f"{experiment_id}: kmesh series changed cutoff")
            if metadata["kmesh"] == reference_metadata["kmesh"]:
                errors.append(f"{experiment_id}: kmesh was not refined")
        else:
            errors.append(f"{experiment_id}: unsupported comparison axis")

    if errors:
        raise ValueError("S1-R8 manifest validation failed:\n- " + "\n- ".join(errors))
    return {
        "experiment_count": len(rows),
        "first_experiment_id": rows[0]["experiment_id"],
        "last_experiment_id": rows[-1]["experiment_id"],
        "config_sha256": config_digest,
        "manifest_sha256": sha256(manifest_path),
    }


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "manifest",
        nargs="?",
        type=Path,
        default=project_root / "config" / "S1_non_equilibrium_run_manifest.tsv",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=project_root / "config" / "S1_non_equilibrium_convergence.json",
    )
    args = parser.parse_args()
    payload = validate(project_root, args.config.resolve(), args.manifest.resolve())
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
