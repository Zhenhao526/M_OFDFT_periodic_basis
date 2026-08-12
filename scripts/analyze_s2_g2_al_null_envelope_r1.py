#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np

import analyze_s2_g2_al_joint_svd_r1 as joint
import analyze_s2_g2_al_localized_dense_grid_r1 as dense
import analyze_s2_g2_al_localized_r1 as r1
import analyze_s2_g2_al_localized_analysis_r2 as r2
import s2_g2_al_localized_common_r1 as common

CONFIG_REL = Path("config/S2_g2_al_null_envelope_r1.json")
BASE_COMMIT = "3c85d239c3f13c0df3ae7120e2436fa8ee2c1d32"


def load_config(root: Path) -> dict:
    return json.loads((root / CONFIG_REL).read_text())


def git(root: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    common.require(p.returncode == 0, f"git {' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout.strip()


def validate_config(config: dict) -> None:
    common.require(config["schema_version"] == 1, "schema differs")
    common.require(config["protocol_revision"] == "S2-G2-AL-NULL-ENVELOPE-20260812-R1", "protocol differs")
    common.require(config["base_commit"] == BASE_COMMIT and config["new_solver_run_count"] == 0, "revision denominator differs")
    analysis = config["analysis"]
    common.require(analysis["target_grids"] == [128, 144] and analysis["local_subspace_count"] == 10, "source denominator differs")
    common.require(analysis["local_bad_subspace_dimension"] == 2, "local dimension differs")
    common.require(analysis["envelope_dimension_scan"] == list(range(2, 21)), "dimension scan differs")
    common.require(config["acceptance"]["coverage_principal_angle_max_degrees"] == 15.0, "angle gate differs")
    common.require(config["acceptance"]["maximum_allowed_fixed_envelope_dimension"] == 2, "decision limit differs")


def validate_sources(root: Path, config: dict) -> tuple[dict, dict, dict]:
    source = config["source"]
    head = git(root, "rev-parse", "HEAD")
    common.require(subprocess.run(["git", "merge-base", "--is-ancestor", source["joint_svd_evidence_commit"], head], cwd=root).returncode == 0, "joint-SVD evidence is not an ancestor")
    for path_key, sha_key in (("joint_svd_config_path", "joint_svd_config_sha256"), ("joint_svd_analyzer_path", "joint_svd_analyzer_sha256"), ("joint_svd_summary_path", "joint_svd_summary_sha256")):
        path = root / source[path_key]
        common.require(path.is_file() and not path.is_symlink(), f"source missing: {path}")
        common.require(common.sha256_path(path) == source[sha_key], f"source SHA differs: {path}")
    historical = json.loads((root / source["joint_svd_summary_path"]).read_text())
    common.require(historical["status"] == "evidence_valid_scientific_gate_rejected" and historical["selected_candidate_id"] is None, "joint-SVD status differs")
    common.require(all(row["failed_gates"] == ["condition", "discarded_mode_alignment", "full_reduced_rank", "retained_margin"] for row in historical["candidates"]), "joint-SVD failure set differs")
    joint_config = json.loads((root / source["joint_svd_config_path"]).read_text())
    r1_config, reference, recovered = joint.validate_sources(root, joint_config)
    return r1_config, reference, recovered


def build_envelope(local_spaces: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray, str]:
    common.require(len(local_spaces) == 10 and all(space.shape[1] == 2 for space in local_spaces), "local-space denominator differs")
    concatenated = np.concatenate(local_spaces, axis=1)
    left, singular, _ = np.linalg.svd(concatenated, full_matrices=False)
    checksum = common.sha256_bytes(np.asarray(left, dtype="<f8").tobytes(order="C"))
    return left, singular, checksum


def scan_dimensions(local_spaces: list[np.ndarray], envelope: np.ndarray, dimensions: list[int], threshold: float) -> tuple[list[dict], list[dict], int]:
    scan, coverage = [], []
    selected = None
    for dimension in dimensions:
        fixed = envelope[:, :dimension]
        rows = []
        for index, local in enumerate(local_spaces):
            angles = dense.principal_angles_degrees(fixed, local)
            row = {"dimension": dimension, "local_index": index, "maximum_angle_degrees": max(angles), "principal_angles_degrees": angles}
            rows.append(row); coverage.append(row)
        maximum = max(row["maximum_angle_degrees"] for row in rows)
        accepted = maximum <= threshold
        scan.append({"dimension": dimension, "maximum_angle_degrees": maximum, "all_local_spaces_covered": accepted})
        if accepted and selected is None:
            selected = dimension
    common.require(selected is not None, "dimension scan does not span all local spaces")
    return scan, coverage, selected


def build_analysis(root: Path, config: dict) -> tuple[dict, dict[str, bytes]]:
    validate_config(config)
    r1_config, reference, recovered = validate_sources(root, config)
    cell = np.asarray(reference["cell_bohr"], dtype=float)
    vectors = r1.source_low_g(root, r1_config, cell)
    paths = r2.exact_rank_position_sets(recovered["metadata"], r1_config)
    local_spaces, local_rows = [], []
    for grid in config["analysis"]["target_grids"]:
        grams, _, _ = common.atomic_complement_grams(cell, np.asarray([grid] * 3), paths, r1_config["source"]["alpha_bohr_minus2"], vectors, r1_config["projection"]["interpolation_order"])
        for displacement, gram in zip(config["analysis"]["rank_displacements_angstrom"], grams):
            values, eigenvectors, _ = dense.normalized_spectrum(gram)
            local_spaces.append(eigenvectors[:, :2])
            local_rows.append({"grid": grid, "displacement_angstrom": displacement, "bottom_eigenvalues": [float(x) for x in values[:4]]})
    envelope, singular, checksum = build_envelope(local_spaces)
    scan, coverage, required = scan_dimensions(local_spaces, envelope, config["analysis"]["envelope_dimension_scan"], config["acceptance"]["coverage_principal_angle_max_degrees"])
    for row in coverage:
        row.update(local_rows[row["local_index"]])
    allowed = required <= config["acceptance"]["maximum_allowed_fixed_envelope_dimension"]
    disposition = "retain_for_fixed_subspace_revision" if allowed else "eliminate_current_23_function_periodic_expansion_return_to_four_route_architecture_competition"
    summary = {
        "schema_version": 1, "protocol_revision": config["protocol_revision"],
        "status": "accepted_null_envelope_diagnostic", "evidence_valid": True, "new_solver_run_count": 0,
        "local_subspace_count": 10, "local_bad_subspace_dimension": 2,
        "joint_envelope_rank": int(np.sum(singular > singular[0] * 1e-12)),
        "joint_envelope_singular_values": [float(x) for x in singular], "fixed_envelope_sha256": checksum,
        "coverage_principal_angle_max_degrees": config["acceptance"]["coverage_principal_angle_max_degrees"],
        "minimum_required_fixed_envelope_dimension": required,
        "maximum_allowed_fixed_envelope_dimension": config["acceptance"]["maximum_allowed_fixed_envelope_dimension"],
        "current_23_function_periodic_expansion_retained": allowed,
        "decision": disposition, "g2_overall_accepted": False,
    }
    scan_lines = ["dimension\tmaximum_angle_degrees\tall_local_spaces_covered"]
    scan_lines += [f"{row['dimension']}\t{row['maximum_angle_degrees']}\t{row['all_local_spaces_covered']}" for row in scan]
    coverage_lines = ["dimension\tlocal_index\tgrid\tdisplacement_angstrom\tmaximum_angle_degrees\tprincipal_angles_degrees\tbottom_eigenvalues"]
    for row in coverage:
        coverage_lines.append("\t".join(str(row[key]) if key not in ("principal_angles_degrees", "bottom_eigenvalues") else json.dumps(row[key], separators=(",", ":")) for key in ("dimension", "local_index", "grid", "displacement_angstrom", "maximum_angle_degrees", "principal_angles_degrees", "bottom_eigenvalues")))
    readme = f"""# S2/G2 Al local-null-envelope diagnostic\n\nStatus: `accepted_null_envelope_diagnostic`.\n\nThe ten local bottom-two normalized-Gram eigenspaces require a fixed envelope of dimension **{required}** to cover every local space within 15 degrees. The registered maximum was 2.\n\nDecision: `{disposition}`. G2 remains open and no solver was started.\n""".encode()
    outputs = {"README.md": readme, "dimension_scan.tsv": ("\n".join(scan_lines) + "\n").encode(), "local_coverage.tsv": ("\n".join(coverage_lines) + "\n").encode(), "summary.json": common.canonical_json(summary)}
    common.require(sorted(outputs) == sorted(config["output_files"]), "output denominator differs")
    return summary, outputs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(); root = args.project_root.resolve(); config = load_config(root)
    summary, outputs = build_analysis(root, config)
    target = root / config["execution"]["analysis_root"]
    if not args.dry_run:
        common.require(not target.exists(), "analysis root exists")
        target.mkdir(parents=True)
        for name, data in outputs.items(): (target / name).write_bytes(data)
    print(json.dumps({"status": summary["status"], "minimum_required_fixed_envelope_dimension": summary["minimum_required_fixed_envelope_dimension"], "decision": summary["decision"], "new_solver_run_count": 0, "output_written": not args.dry_run}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
