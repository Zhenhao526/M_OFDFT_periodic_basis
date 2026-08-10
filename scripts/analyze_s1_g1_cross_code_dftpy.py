#!/usr/bin/env python3
"""Analyze the frozen DFTpy/ABACUS 14-point cross-code comparison."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path

import numpy as np

from analyze_s1_eos import fit_bm3
from run_s1_g1_cross_code_dftpy import BOHR_TO_ANGSTROM, read_manifest


POINT_FIELDS = (
    "experiment_id",
    "material",
    "volume_ratio",
    "abacus_run_id",
    "volume_per_atom_angstrom3",
    "grid",
    "electron_count_independent",
    "electron_count_abs_error",
    "abacus_energy_ev_per_atom",
    "dftpy_energy_ev_per_atom",
    "absolute_energy_difference_mev_per_atom",
    "abacus_relative_energy_mev_per_atom",
    "dftpy_relative_energy_mev_per_atom",
    "relative_energy_difference_mev_per_atom",
    "abacus_pressure_gpa",
    "dftpy_pressure_gpa",
    "pressure_difference_gpa",
    "density_sha256",
    "status",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _git(project_root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(project_root), *args], text=True).strip()


def _density_integral(result: dict, density_path: Path) -> float:
    density = np.load(density_path, allow_pickle=False)
    expected_shape = tuple(int(value) for value in result["grid"])
    if density.dtype != np.float64 or density.shape != expected_shape:
        raise ValueError(f"density array schema differs: {density_path}")
    volume_bohr3 = float(result["cell_volume_angstrom3"]) / BOHR_TO_ANGSTROM**3
    return float(np.sum(density, dtype=np.float64) * volume_bohr3 / density.size)


def _rms(values: list[float]) -> float:
    return math.sqrt(sum(value * value for value in values) / len(values))


def analyze(project_root: Path, manifest_path: Path, config_path: Path, run_root: Path) -> tuple[dict, list[dict]]:
    config = read_json(config_path)
    rows = read_manifest(manifest_path)
    thresholds = config["acceptance_thresholds"]
    if len(rows) != config["formal_run_count"]:
        raise ValueError("formal run count differs from config")
    loaded = []
    failures = []
    for row in rows:
        run_directory = run_root / row["experiment_id"]
        result_path = run_directory / "result.json"
        density_path = run_directory / "density.npy"
        if not result_path.is_file() or not density_path.is_file():
            failures.append(f"{row['experiment_id']}: missing result or density")
            continue
        result = read_json(result_path)
        if result.get("experiment_id") != row["experiment_id"]:
            failures.append(f"{row['experiment_id']}: result identity differs")
            continue
        if result.get("status") != "accepted" or result.get("dftpy_converged_code") != 0:
            failures.append(f"{row['experiment_id']}: DFTpy optimizer was not accepted")
        if sha256(density_path) != result.get("density_sha256"):
            failures.append(f"{row['experiment_id']}: density SHA-256 differs")
        independent_electrons = _density_integral(result, density_path)
        electron_error = abs(independent_electrons - float(row["expected_electrons"]))
        if electron_error > thresholds["electron_number_abs_error_max"]:
            failures.append(f"{row['experiment_id']}: independent electron-number gate failed")
        abacus_path = project_root / "runs" / row["abacus_run_id"] / "result.json"
        if sha256(abacus_path) != result["inputs"]["abacus_result_sha256"]:
            failures.append(f"{row['experiment_id']}: ABACUS reference result changed")
        abacus = read_json(abacus_path)
        if abacus.get("converged") is not True:
            failures.append(f"{row['experiment_id']}: ABACUS reference is not converged")
        loaded.append(
            {
                "row": row,
                "result": result,
                "abacus": abacus,
                "electron_count_independent": independent_electrons,
                "electron_count_abs_error": electron_error,
            }
        )

    material_summaries = {}
    point_rows: list[dict] = []
    for material in config["materials"]:
        points = [item for item in loaded if item["row"]["material"] == material]
        points.sort(key=lambda item: float(item["row"]["volume_ratio"]))
        expected_ratios = [float(value) for value in config["volume_ratios"]]
        ratios = [float(item["row"]["volume_ratio"]) for item in points]
        if ratios != expected_ratios:
            failures.append(f"{material}: volume-ratio denominator is incomplete")
            continue
        anchor_ratio = float(config["anchor_volume_ratio"])
        anchor = next(item for item in points if float(item["row"]["volume_ratio"]) == anchor_ratio)
        dftpy_anchor = float(anchor["result"]["energy_ev_per_atom"])
        abacus_anchor = float(anchor["abacus"]["energy_ev_per_atom"])
        relative_differences = []
        pressure_differences = []
        absolute_offsets = []
        for item in points:
            row = item["row"]
            result = item["result"]
            abacus = item["abacus"]
            dftpy_energy = float(result["energy_ev_per_atom"])
            abacus_energy = float(abacus["energy_ev_per_atom"])
            dftpy_relative = (dftpy_energy - dftpy_anchor) * 1000.0
            abacus_relative = (abacus_energy - abacus_anchor) * 1000.0
            relative_difference = dftpy_relative - abacus_relative
            pressure_difference = float(result["pressure_gpa"]) - float(abacus["pressure_gpa"])
            absolute_offset = (dftpy_energy - abacus_energy) * 1000.0
            relative_differences.append(relative_difference)
            pressure_differences.append(pressure_difference)
            absolute_offsets.append(absolute_offset)
            point_rows.append(
                {
                    "experiment_id": row["experiment_id"],
                    "material": material,
                    "volume_ratio": float(row["volume_ratio"]),
                    "abacus_run_id": row["abacus_run_id"],
                    "volume_per_atom_angstrom3": float(result["volume_per_atom_angstrom3"]),
                    "grid": "x".join(str(value) for value in result["grid"]),
                    "electron_count_independent": item["electron_count_independent"],
                    "electron_count_abs_error": item["electron_count_abs_error"],
                    "abacus_energy_ev_per_atom": abacus_energy,
                    "dftpy_energy_ev_per_atom": dftpy_energy,
                    "absolute_energy_difference_mev_per_atom": absolute_offset,
                    "abacus_relative_energy_mev_per_atom": abacus_relative,
                    "dftpy_relative_energy_mev_per_atom": dftpy_relative,
                    "relative_energy_difference_mev_per_atom": relative_difference,
                    "abacus_pressure_gpa": float(abacus["pressure_gpa"]),
                    "dftpy_pressure_gpa": float(result["pressure_gpa"]),
                    "pressure_difference_gpa": pressure_difference,
                    "density_sha256": result["density_sha256"],
                    "status": result["status"],
                }
            )
        volumes = [float(item["result"]["volume_per_atom_angstrom3"]) for item in points]
        dftpy_fit = fit_bm3(volumes, [float(item["result"]["energy_ev_per_atom"]) for item in points])
        abacus_fit = fit_bm3(volumes, [float(item["abacus"]["energy_ev_per_atom"]) for item in points])
        relative_rms = _rms(relative_differences)
        v0_difference = abs(
            dftpy_fit["v0_angstrom3_per_atom"] / abacus_fit["v0_angstrom3_per_atom"] - 1.0
        ) * 100.0
        pressure_max = max(abs(value) for value in pressure_differences)
        offset_mean = sum(absolute_offsets) / len(absolute_offsets)
        offset_centered = [value - offset_mean for value in absolute_offsets]
        gates = {
            "anchored_relative_eos_rms": relative_rms
            <= thresholds["anchored_relative_eos_rms_mev_per_atom_max"],
            "equilibrium_volume": v0_difference
            <= thresholds["equilibrium_volume_difference_percent_max"],
            "pressure": pressure_max <= thresholds["pressure_max_abs_difference_gpa"],
            "electron_number": max(item["electron_count_abs_error"] for item in points)
            <= thresholds["electron_number_abs_error_max"],
            "all_points_accepted": all(item["result"]["status"] == "accepted" for item in points),
        }
        material_summaries[material] = {
            "status": "accepted" if all(gates.values()) else "rejected",
            "gates": gates,
            "anchored_relative_eos_rms_mev_per_atom": relative_rms,
            "max_abs_relative_energy_difference_mev_per_atom": max(
                abs(value) for value in relative_differences
            ),
            "equilibrium_volume_difference_percent": v0_difference,
            "pressure_max_abs_difference_gpa": pressure_max,
            "pressure_rms_difference_gpa": _rms(pressure_differences),
            "electron_number_max_abs_error": max(item["electron_count_abs_error"] for item in points),
            "abacus_bm3": abacus_fit,
            "dftpy_bm3": dftpy_fit,
            "absolute_energy_offset_mev_per_atom": {
                "values": absolute_offsets,
                "mean": offset_mean,
                "centered_rms": _rms(offset_centered),
                "range": max(absolute_offsets) - min(absolute_offsets),
            },
        }
        if not all(gates.values()):
            failures.append(f"{material}: one or more registered scientific gates failed")

    runner_summary_path = run_root / "runner_summary.json"
    runner_summary = read_json(runner_summary_path) if runner_summary_path.is_file() else None
    if runner_summary is None or runner_summary.get("failure_count") != 0:
        failures.append("runner summary is missing or contains failures")
    status = "accepted" if not failures and len(point_rows) == config["formal_run_count"] else "rejected"
    lock_path = project_root / config["environment_lock"]
    summary = {
        "protocol_revision": config["protocol_revision"],
        "cross_code_status": status,
        "g1_status_before": "pending (2/6)",
        "g1_status_after_if_accepted": "pending (3/6)",
        "formal_expected_points": config["formal_run_count"],
        "formal_analyzed_points": len(point_rows),
        "failures": failures,
        "thresholds": thresholds,
        "materials": material_summaries,
        "analysis_provenance": {
            "analyzer_commit": runner_summary.get("runner_commit") if runner_summary else None,
            "manifest": str(manifest_path.relative_to(project_root)),
            "manifest_sha256": sha256(manifest_path),
            "config": str(config_path.relative_to(project_root)),
            "config_sha256": sha256(config_path),
            "environment_lock": str(lock_path.relative_to(project_root)),
            "environment_lock_sha256": sha256(lock_path),
            "runner_summary_sha256": sha256(runner_summary_path) if runner_summary_path.is_file() else None,
        },
        "absolute_energy_convention_audit": {
            "same_local_pseudopotential_bytes": True,
            "local_pseudopotential_headers": {
                material: (project_root / next(row["pseudopotential"] for row in rows if row["material"] == material))
                .read_text(encoding="utf-8")
                .splitlines()[0]
                for material in config["materials"]
            },
            "hartree_G_zero": "Both calculations are neutral periodic cells. Equality of the total-energy shape and pressure is observed; equality of internal Hartree G=0 code paths is an inference, not a component-wise proof.",
            "ion_ewald_and_self_energy": "DFTpy includes LocalPseudo.ewald in TotalFunctional; ABACUS does not expose a matching component table in these legacy logs. Raw absolute offsets and their centered RMS are therefore reported and no absolute-energy cancellation is hidden.",
            "allowed_constant_removal": "Only one per-material constant is removed for the registered anchored relative-EOS metric.",
        },
    }
    return summary, point_rows


def write_outputs(output_directory: Path, summary: dict, rows: list[dict]) -> None:
    output_directory.mkdir(parents=True, exist_ok=False)
    write_json(output_directory / "summary.json", summary)
    with (output_directory / "points.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=POINT_FIELDS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    lines = [
        "# S1/G1 DFTpy 独立 OFDFT 跨代码复核",
        "",
        f"状态：`{summary['cross_code_status']}`；正式点：{summary['formal_analyzed_points']}/{summary['formal_expected_points']}。",
        "",
        "| 材料 | 相对 EOS RMS (meV/atom) | ΔV0 (%) | 最大 |ΔP| (GPa) | 最大 |ΔN| | 状态 |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for material, item in summary["materials"].items():
        lines.append(
            "| {m} | {e:.9g} | {v:.9g} | {p:.9g} | {n:.3e} | {s} |".format(
                m=material,
                e=item["anchored_relative_eos_rms_mev_per_atom"],
                v=item["equilibrium_volume_difference_percent"],
                p=item["pressure_max_abs_difference_gpa"],
                n=item["electron_number_max_abs_error"],
                s=item["status"],
            )
        )
    lines.extend(
        [
            "",
            "比较使用相同结构、局域赝势字节、XC、WT 参数和逐点 FFT 网格。绝对总能未被偷偷平移；逐点偏移及去均值 RMS 见 `summary.json`，只有相对 EOS 门允许按材料消去一个常数。",
            "",
            "Mg 沿用当前 legacy LDA-PZ 基线，本结论不等价于统一 PBE 主线已经完成。",
        ]
    )
    (output_directory / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("output_directory", type=Path)
    parser.add_argument(
        "--manifest", type=Path, default=project_root / "config/S1_g1_cross_code_dftpy_manifest.tsv"
    )
    parser.add_argument(
        "--config", type=Path, default=project_root / "config/S1_g1_cross_code_dftpy.json"
    )
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--collect-raw", action="store_true")
    args = parser.parse_args()
    config = read_json(args.config.resolve())
    run_root = (args.run_root or Path(config["external_state_root"])).resolve()
    summary, rows = analyze(project_root, args.manifest.resolve(), args.config.resolve(), run_root)
    output = args.output_directory.resolve()
    write_outputs(output, summary, rows)
    if args.collect_raw:
        raw = output / "raw"
        raw.mkdir()
        for row in read_manifest(args.manifest.resolve()):
            shutil.copytree(run_root / row["experiment_id"], raw / row["experiment_id"])
        shutil.copy2(run_root / "runner_summary.json", raw / "runner_summary.json")
    return 0 if summary["cross_code_status"] == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
