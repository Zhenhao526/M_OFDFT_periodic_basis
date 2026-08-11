#!/usr/bin/env python3
"""Build deterministic Galileo strain and endpoint gates for the Al follow-up."""

from __future__ import annotations

import argparse
import csv
import io
import json
import shutil
from pathlib import Path

from s1_g1_three_layer_al_followup_common import (
    atomic_write,
    canonical_json_bytes,
    find_project_root,
    geometry_payload_bytes,
    load_config,
    load_manifest,
    read_json,
    require,
    sha256_bytes,
    sha256_file,
)


STRAIN_MAP = {
    "S1-20260810-319": "S1-20260810-204",
    "S1-20260810-320": "S1-20260810-205",
    "S1-20260810-321": "S1-20260810-206",
    "S1-20260810-322": "S1-20260810-207",
}
PARENT_IDS = ("S1-20260810-301", "S1-20260810-302", "S1-20260810-303", "S1-20260810-307", "S1-20260810-308")
GATE_FIELDS = ("gate", "point", "metric", "value", "limit", "inequality", "accepted")


def _read_object(path: Path) -> dict:
    payload = read_json(path)
    require(isinstance(payload, dict), f"JSON root must be object: {path}")
    return payload


def verify_new_result(run_dir: Path, experiment_id: str, config: dict) -> dict:
    result = _read_object(run_dir / "result.json")
    require(result.get("status") == "accepted" and result.get("experiment_id") == experiment_id, f"new result rejected: {experiment_id}")
    require(result.get("protocol_revision") == config["protocol_revision"], "new result protocol differs")
    require(result.get("hard_gates") and all(result["hard_gates"].values()), f"per-point hard gate failed: {experiment_id}")
    require(result["runtime_nonlocal_projectors_total"] == 18, "runtime projector count differs")
    require(result["independent_electron_identity"]["accepted"] is True, "electron identity differs")
    require(result["band_occupation"]["accepted"] is True, "band gate differs")
    require(result["cube_geometry"]["accepted"] is True, "cube geometry differs")
    require(result["mechanics"]["accepted"] is True, "mechanics gate differs")
    for identity in result["evidence_files"]:
        path = run_dir / identity["path"]
        require(path.is_file() and not path.is_symlink(), f"missing new evidence: {path}")
        require(path.stat().st_size == identity["size_bytes"] and sha256_file(path) == identity["sha256"], f"new evidence identity differs: {path}")
    runner = _read_object(run_dir / "runner_return.json")
    require(runner.get("return_code") == 0, "runner return differs")
    return result


def verify_parent_result(run_dir: Path, experiment_id: str, config: dict) -> dict:
    result = _read_object(run_dir / "result.json")
    require(result.get("status") == "accepted" and result.get("experiment_id") == experiment_id, f"parent result rejected: {experiment_id}")
    require(result["runtime_nonlocal_projectors_total"] == 18, "parent projector count differs")
    require(result["pseudo_identity"]["sha256"] == config["pseudodojo"]["materials"]["al"]["sha256"], "parent pseudo differs")
    return result


def energy(result: dict) -> float:
    return float(result["thermodynamic_labels_ev_per_atom"]["E_ec"])


def local_reference_energy(project_root: Path, experiment_id: str) -> tuple[float, dict]:
    if experiment_id == "S1-20260807-043":
        path = project_root / "runs/S1-20260807-043/result.json"
        payload = _read_object(path)
        require(payload.get("converged") is True, "local anchor 043 not converged")
        value = float(payload["zero_temp_extrapolated_energy_ev_per_atom"])
    else:
        path = project_root / f"analysis/s1/g1_displacement_strain_reference_analysis_r2_20260810/raw/{experiment_id}/analysis_result.json"
        payload = _read_object(path)
        require(payload.get("status") == "accepted", f"local strain reference rejected: {experiment_id}")
        value = float(payload["thermodynamic"]["energy_labels_ev_per_atom"]["E_ec"])
    return value, {"path": path.relative_to(project_root).as_posix(), "sha256": sha256_file(path), "git_blob": __import__("subprocess").run(["git", "rev-parse", f"HEAD:{path.relative_to(project_root).as_posix()}"], cwd=project_root, check=True, text=True, stdout=__import__("subprocess").PIPE).stdout.strip()}


def verify_geometry_bindings(project_root: Path, config: dict) -> list[dict]:
    rows = load_manifest(project_root)
    output: list[dict] = []
    for row in rows:
        generated = project_root / config["input_root"] / row["experiment_id"] / "STRU"
        source = project_root / row["geometry_source_path"]
        require(sha256_file(source) == row["geometry_source_stru_sha256"], "geometry source SHA differs")
        require(sha256_bytes(geometry_payload_bytes(source.read_bytes())) == row["geometry_payload_sha256"], "source geometry payload differs")
        require(geometry_payload_bytes(generated.read_bytes()) == geometry_payload_bytes(source.read_bytes()), "generated geometry payload differs")
        complete = generated.read_bytes() == source.read_bytes()
        require(complete == (row["phase"] == "endpoint"), "complete STRU byte-binding mode differs")
        output.append({
            "experiment_id": row["experiment_id"],
            "source_experiment_id": row["geometry_source_id"],
            "source_stru_sha256": row["geometry_source_stru_sha256"],
            "geometry_payload_sha256": row["geometry_payload_sha256"],
            "complete_stru_byte_identical": complete,
            "geometry_payload_byte_identical": True,
        })
    return output


def evaluate_gates(new: dict[str, dict], parent: dict[str, dict], local: dict[str, float], config: dict) -> tuple[dict, list[dict]]:
    strain_limit = float(config["acceptance"]["strain_anchored_difference_mev_per_atom_max"])
    strain_rows: list[dict] = []
    gate_rows: list[dict] = []
    for new_id, local_id in STRAIN_MAP.items():
        nl_response = energy(new[new_id]) - energy(parent["S1-20260810-301"])
        local_response = local[local_id] - local["S1-20260807-043"]
        delta = (nl_response - local_response) * 1000.0
        accepted = abs(delta) <= strain_limit
        row = {
            "experiment_id": new_id,
            "local_reference_id": local_id,
            "nl_anchor_id": "S1-20260810-301",
            "local_anchor_id": "S1-20260807-043",
            "nl_strain_response_mev_per_atom": nl_response * 1000.0,
            "local_strain_response_mev_per_atom": local_response * 1000.0,
            "anchored_difference_mev_per_atom": delta,
            "absolute_difference_mev_per_atom": abs(delta),
            "maximum_allowed_mev_per_atom": strain_limit,
            "inequality": "less_than_or_equal",
            "accepted": accepted,
        }
        strain_rows.append(row)
        gate_rows.append({"gate": "strain", "point": new_id, "metric": "absolute_anchored_difference_mev_per_atom", "value": abs(delta), "limit": strain_limit, "inequality": "<=", "accepted": accepted})
    k_limit = float(config["acceptance"]["endpoint_anchored_k_difference_mev_per_atom_strictly_less_than"])
    cutoff_limit = float(config["acceptance"]["endpoint_anchored_cutoff_difference_mev_per_atom_strictly_less_than"])
    pressure_limit = float(config["acceptance"]["endpoint_cutoff_pressure_difference_gpa_strictly_less_than"])
    endpoint_specs = {
        "v090": ("S1-20260810-307", "S1-20260810-323", "S1-20260810-324"),
        "v110": ("S1-20260810-308", "S1-20260810-325", "S1-20260810-326"),
    }
    endpoint_rows: list[dict] = []
    for label, (common_id, extra_id, high_id) in endpoint_specs.items():
        common_shape = energy(parent[common_id]) - energy(parent["S1-20260810-301"])
        extra_shape = energy(new[extra_id]) - energy(parent["S1-20260810-302"])
        high_shape = energy(new[high_id]) - energy(parent["S1-20260810-303"])
        k_delta = abs(extra_shape - common_shape) * 1000.0
        cutoff_delta = abs(high_shape - extra_shape) * 1000.0
        pressure_delta = abs(float(new[high_id]["pressure_gpa"]) - float(new[extra_id]["pressure_gpa"]))
        accepted = k_delta < k_limit and cutoff_delta < cutoff_limit and pressure_delta < pressure_limit
        endpoint_rows.append({
            "endpoint": label,
            "common_id": common_id,
            "normal_extra_k_id": extra_id,
            "high_extra_k_id": high_id,
            "v100_anchor_ids": ["S1-20260810-301", "S1-20260810-302", "S1-20260810-303"],
            "anchored_k_difference_mev_per_atom": k_delta,
            "anchored_cutoff_difference_mev_per_atom": cutoff_delta,
            "cutoff_pressure_difference_gpa": pressure_delta,
            "accepted": accepted,
        })
        for metric, value, limit in (("anchored_k_difference_mev_per_atom", k_delta, k_limit), ("anchored_cutoff_difference_mev_per_atom", cutoff_delta, cutoff_limit), ("cutoff_pressure_difference_gpa", pressure_delta, pressure_limit)):
            gate_rows.append({"gate": "endpoint", "point": label, "metric": metric, "value": value, "limit": limit, "inequality": "<", "accepted": value < limit})
    accepted = all(row["accepted"] for row in strain_rows + endpoint_rows)
    return {
        "status": "accepted" if accepted else "rejected",
        "strain": {"status": "accepted" if all(row["accepted"] for row in strain_rows) else "rejected", "point_count": 4, "rows": strain_rows},
        "endpoints": {"status": "accepted" if all(row["accepted"] for row in endpoint_rows) else "rejected", "endpoint_count": 2, "rows": endpoint_rows},
    }, gate_rows


def build_analysis(project_root: Path, config: dict, new_runs_root: Path, parent_runs_root: Path) -> tuple[dict, list[dict]]:
    new = {experiment_id: verify_new_result(new_runs_root / experiment_id, experiment_id, config) for experiment_id in config["formal_ids"]}
    runner_commits = {
        _read_object(new_runs_root / experiment_id / "metadata.json").get("runner_commit")
        for experiment_id in config["formal_ids"]
    }
    require(len(runner_commits) == 1 and None not in runner_commits, "new-run runner commit differs")
    runner_commit = next(iter(runner_commits))
    parent = {experiment_id: verify_parent_result(parent_runs_root / experiment_id, experiment_id, config) for experiment_id in PARENT_IDS}
    local: dict[str, float] = {}
    identities: list[dict] = []
    for experiment_id in ("S1-20260807-043", *STRAIN_MAP.values()):
        local[experiment_id], identity = local_reference_energy(project_root, experiment_id)
        identities.append(identity)
    gates, gate_rows = evaluate_gates(new, parent, local, config)
    geometry = verify_geometry_bindings(project_root, config)
    status = "accepted" if gates["status"] == "accepted" else "rejected"
    summary = {
        "schema_version": 1,
        "protocol_revision": config["protocol_revision"],
        "status": status,
        "scope_status": "accepted_al_domain_followup" if status == "accepted" else "rejected_al_domain_followup",
        "registered_run_count": 8,
        "accepted_run_count": 8,
        "failed_missing_skipped_retried_count": 0,
        "runner_commit": runner_commit,
        "all_per_point_hard_gates_accepted": all(all(result["hard_gates"].values()) for result in new.values()),
        "geometry_bindings": geometry,
        "galileo_gates": gates,
        "parent_reference_ids": list(PARENT_IDS),
        "parent_runner_commit": config["parent_three_layer_r1"]["runner_commit"],
        "legacy_local_reference_identities": identities,
        "semantic_limits": {
            "second_independent_KS_engine_closed": False,
            "D_026_QE_scope_closed": False,
            "G4_force_or_stress_closed": False,
            "absolute_cross_PP_energy_gate_applied": False,
            "zero_temperature_exact_claim": False,
        },
    }
    return summary, gate_rows


def gate_tsv_bytes(rows: list[dict]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=GATE_FIELDS, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return output.getvalue().encode()


def readme_bytes(summary: dict) -> bytes:
    return ("\n".join([
        "# S1/G1 Al-domain 三层补充 R1",
        "",
        f"状态：`{summary['scope_status']}`；8/8 新点，失败/缺失/跳过/重试为 0。",
        "",
        f"四个 301/043 锚定 strain 门：`{summary['galileo_gates']['strain']['status']}`；两个端点的锚定 k/cutoff/pressure 门：`{summary['galileo_gates']['endpoints']['status']}`。",
        "",
        "所有新点同时通过 PBE/3e PP/projector18、Ne(log/cube/eig_occ)、NBANDS/last-occ、cube geometry、stress-pressure 和 rank-affinity 硬门。",
        "",
        "范围限制：本证据不是独立第二 KS/QE，也不关闭 G4；跨 PP 绝对总能不作验收门。",
    ]) + "\n").encode()


def write_analysis(output_root: Path, summary: dict, gate_rows: list[dict]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    atomic_write(output_root / "summary.json", canonical_json_bytes(summary))
    atomic_write(output_root / "gates.tsv", gate_tsv_bytes(gate_rows))
    atomic_write(output_root / "README.md", readme_bytes(summary))


def copy_exact(source: Path, destination: Path) -> None:
    require(source.is_file() and not source.is_symlink(), f"not a regular source: {source}")
    require(not destination.exists(), f"evidence destination exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    require(sha256_file(source) == sha256_file(destination), "evidence copy SHA differs")


def collect(project_root: Path, config: dict) -> tuple[Path, Path, Path]:
    state = Path(config["external_state_root"])
    terminal = _read_object(state / "terminal.json")
    require(terminal.get("status") == "accepted" and terminal.get("accepted_count") == 8 and terminal.get("runner_return_code") == 0, "follow-up terminal not accepted")
    parent_state = Path(config["parent_three_layer_r1"]["external_state_root"])
    analysis_root = project_root / config["analysis_root"]
    require(not analysis_root.exists(), "analysis root already exists")
    for experiment_id in config["formal_ids"]:
        source = state / "runs" / experiment_id
        metadata = _read_object(source / "metadata.json")
        names = ("INPUT", "STRU", "KPT", "input_metadata.json", "metadata.json", "pseudo_identity.json", "run.stdout", "run.stderr", "runner_return.json", "result.json")
        for name in names:
            copy_exact(source / name, analysis_root / "raw/new" / experiment_id / name)
        for name in ("running_scf.log", "chg.cube", "eig_occ.txt"):
            copy_exact(source / f"OUT.{metadata['suffix']}" / name, analysis_root / "raw/new" / experiment_id / f"OUT.{metadata['suffix']}" / name)
        for rank in range(4):
            copy_exact(source / "affinity" / f"rank_{rank:03d}.json", analysis_root / "raw/new" / experiment_id / "affinity" / f"rank_{rank:03d}.json")
        copy_exact(state / "attempts" / f"{experiment_id}.json", analysis_root / "orchestration/attempts" / f"{experiment_id}.json")
        copy_exact(state / "accepted" / f"{experiment_id}.json", analysis_root / "orchestration/accepted" / f"{experiment_id}.json")
    for experiment_id in PARENT_IDS:
        source = parent_state / "runs" / experiment_id
        for name in ("STRU", "metadata.json", "runner_return.json", "result.json"):
            copy_exact(source / name, analysis_root / "raw/parent" / experiment_id / name)
    copy_exact(state / "session.json", analysis_root / "orchestration/session.json")
    copy_exact(state / "terminal.json", analysis_root / "orchestration/terminal.json")
    copy_exact(parent_state / "session.json", analysis_root / "orchestration/parent_session.json")
    return analysis_root, analysis_root / "raw/new", analysis_root / "raw/parent"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--collect", action="store_true")
    parser.add_argument("--new-runs-root", type=Path)
    parser.add_argument("--parent-runs-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args()
    project_root = find_project_root(args.project_root)
    config = load_config(project_root)
    if args.collect:
        analysis_root, new_root, parent_root = collect(project_root, config)
    else:
        analysis_root = args.output_root or project_root / config["analysis_root"]
        new_root = args.new_runs_root or Path(config["external_state_root"]) / "runs"
        parent_root = args.parent_runs_root or Path(config["parent_three_layer_r1"]["external_state_root"]) / "runs"
    summary, rows = build_analysis(project_root, config, new_root.resolve(), parent_root.resolve())
    write_analysis(analysis_root.resolve(), summary, rows)
    print(json.dumps({"status": summary["status"], "scope_status": summary["scope_status"], "output_root": str(analysis_root)}, sort_keys=True))
    return 0 if summary["status"] == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
