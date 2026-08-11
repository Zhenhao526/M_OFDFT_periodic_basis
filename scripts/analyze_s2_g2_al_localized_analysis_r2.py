#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

import analyze_s2_g2_al_localized_r1 as r1a
import s2_g2_al_localized_common_r1 as r1c

CONFIG_REL = Path("config/S2_g2_al_localized_analysis_r2.json")
ANALYZER_REL = Path("scripts/analyze_s2_g2_al_localized_analysis_r2.py")
BASE_COMMIT = "66ca1d76c87551f5c09d8ef81791b75f0479e89e"


def load_config(root: Path) -> dict:
    return json.loads((root / CONFIG_REL).read_text())


def validate_config(config: dict) -> None:
    r1c.require(config["schema_version"] == 1, "schema differs")
    r1c.require(config["protocol_revision"] == "S2-G2-AL-LOCALIZED-ANALYSIS-20260811-R2", "protocol differs")
    r1c.require(config["base_commit"] == BASE_COMMIT and config["new_solver_run_count"] == 0, "revision denominator differs")
    r1c.require(config["source"]["r1_preregistration_commit"] == "28883776ca47d5a31568cd9a99f2e994df813f88", "source preregistration differs")
    r1c.require(config["disposition"]["g2_overall_accepted"] is False, "G2 may not close here")


def parse_cube_108(path: Path, exact_cell, expected_positions):
    lines = path.read_text().splitlines()
    header = lines[2].split()
    nat = abs(int(header[0]))
    origin = np.asarray([float(x) for x in header[1:4]], dtype=float)
    r1c.require(nat == 108 and float(np.max(np.abs(origin))) == 0.0, "108-atom cube header differs")
    counts, axes = [], []
    for offset in range(3):
        fields = lines[3 + offset].split()
        counts.append(int(fields[0])); axes.append([float(x) for x in fields[1:4]])
    counts = np.asarray(counts, dtype=int); axes = np.asarray(axes, dtype=float)
    r1c.require(bool(np.all(counts > 0)), "cube counts differ")
    cell = np.asarray(exact_cell, dtype=float)
    # ABACUS prints each cube step with six decimals; after multiplication by
    # 96 the strict component-wise rounding envelope is 4.8e-5 bohr.
    r1c.require(float(np.max(np.abs(axes * counts[:, None] - cell))) < 5e-5, "cube axes differ")
    expected = np.asarray(expected_positions, dtype=float)
    inverse = np.linalg.inv(cell)
    for index in range(nat):
        fields = lines[6 + index].split()
        r1c.require(int(fields[0]) == 13 and abs(float(fields[1]) - 3.0) < 1e-12, "cube atom identity differs")
        printed = np.asarray([float(x) for x in fields[2:5]], dtype=float)
        delta = printed @ inverse - expected[index]
        delta -= np.rint(delta)
        r1c.require(float(np.linalg.norm(delta @ cell)) < 2e-5, "cube atom position differs")
    values = np.asarray([float(x) for line in lines[6 + nat:] for x in line.split()], dtype=float)
    r1c.require(values.size == int(np.prod(counts)), "cube value denominator differs")
    r1c.require(bool(np.all(np.isfinite(values))) and float(values.min()) >= 0.0, "cube density differs")
    return counts, values.reshape(tuple(int(x) for x in counts))


def inventory(state: Path) -> tuple[list[dict], int, str]:
    rows = []
    for path in sorted(item for item in state.rglob("*") if item.is_file()):
        r1c.require(not path.is_symlink(), "state contains symlink")
        rows.append(r1c.file_identity(path, state))
    size = sum(row["size_bytes"] for row in rows)
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return rows, size, digest


def recovered_reference(root: Path, r1_config: dict, r2_config: dict):
    source = r2_config["source"]
    closure_path = root / source["r1_closure_path"]
    r1c.require(r1c.sha256_path(closure_path) == source["r1_closure_sha256"], "closure SHA differs")
    closure = json.loads(closure_path.read_text())
    state = Path(source["formal_state_root"])
    rows, size, digest = inventory(state)
    r1c.require(rows == closure["state_inventory"] and size == closure["state_size_bytes"] and digest == closure["state_inventory_sha256"], "R1 state inventory differs")
    r1c.require(closure["status"] == "preserved_parser_false_negative_no_retry" and closure["solver_return_code"] == 0, "R1 closure differs")
    experiment_id = r1_config["reference"]["experiment_id"]
    run = state / "runs" / experiment_id
    failure = json.loads((state / "failures" / f"{experiment_id}.json").read_text())
    r1c.require(failure["stage"] == "parser" and failure["message"] == "ValueError: cube atom count/origin differs", "R1 failure identity differs")
    metadata = json.loads((run / "input_metadata.json").read_text())
    old_parser = r1c.pilot.parse_cube
    try:
        r1c.pilot.parse_cube = lambda path, cell: parse_cube_108(path, cell, metadata["geometry"]["fractional_positions"])
        result = r1c.parse_reference_run(run, r1_config)
    finally:
        r1c.pilot.parse_cube = old_parser
    terminal = {"status": "accepted", "attempted": 1, "accepted": 1, "failed": 0, "retried": 0, "runner_return_code": 0, "experiment_ids": [experiment_id]}
    return run, result, terminal, closure


def exact_rank_position_sets(metadata: dict, config: dict):
    geometry = metadata["geometry"]
    undisplaced = np.asarray(geometry["undisplaced_fractional_positions"], dtype=float)
    atom = int(geometry["localized_atom_index_zero_based"])
    registered = np.asarray(geometry["direct_displacement"], dtype=float)
    registered_angstrom = float(config["reference"]["displacement_cartesian_angstrom"][0])
    rows = []
    for displacement in config["projection"]["localized_rank_displacements_angstrom"]:
        positions = undisplaced.copy()
        positions[atom] = (positions[atom] + registered * (float(displacement) / registered_angstrom)) % 1.0
        rows.append(positions)
    r1c.require(bool(np.array_equal(rows[-1], np.asarray(geometry["fractional_positions"], dtype=float))), "exact rank endpoint differs")
    return rows


def build_analysis(root: Path, config: dict) -> tuple[dict, dict[str, bytes]]:
    validate_config(config)
    source = config["source"]
    r1_config_path = root / source["r1_config_path"]
    r1c.require(r1c.sha256_path(r1_config_path) == source["r1_config_sha256"], "R1 config SHA differs")
    r1c.require(r1c.sha256_path(root / "scripts/analyze_s2_g2_al_localized_r1.py") == source["r1_analyzer_sha256"], "R1 analyzer SHA differs")
    r1c.require(r1c.sha256_path(root / "scripts/s2_g2_al_localized_common_r1.py") == source["r1_common_sha256"], "R1 common SHA differs")
    r1_config = json.loads(r1_config_path.read_text())
    recovered = recovered_reference(root, r1_config, config)
    run, result, terminal, closure = recovered
    metadata = json.loads((run / "input_metadata.json").read_text())
    old_cube = r1a.pilot.parse_cube
    old_validate = r1a.validate_reference_state
    old_git = r1a.git
    old_positions = r1a.position_sets
    old_sha256_path = r1a.sha256_path
    try:
        r1a.pilot.parse_cube = lambda path, cell: parse_cube_108(path, cell, metadata["geometry"]["fractional_positions"])
        r1a.validate_reference_state = lambda _root, _config: (run, result, terminal)
        r1a.position_sets = lambda _reference, _config: exact_rank_position_sets(metadata, _config)
        missing_terminal = Path(r1_config["execution"]["state_root"]) / "terminal.json"
        r1a.sha256_path = lambda path: source["r1_closure_sha256"] if Path(path) == missing_terminal else old_sha256_path(Path(path))
        def frozen_git(path, *args):
            if args == ("rev-parse", "HEAD"):
                return config["implementation_commit"]
            return old_git(path, *args)
        r1a.git = frozen_git
        summary, outputs = r1a.build_analysis(root, r1_config)
    finally:
        r1a.pilot.parse_cube = old_cube
        r1a.validate_reference_state = old_validate
        r1a.position_sets = old_positions
        r1a.sha256_path = old_sha256_path
        r1a.git = old_git
    summary["protocol_revision"] = config["protocol_revision"]
    summary["new_solver_run_count"] = 0
    summary["source_solver_run_count"] = 1
    summary["r1_operational_disposition"] = closure["status"]
    summary["g2_overall_accepted"] = False
    outputs["summary.json"] = r1c.canonical_json(summary)
    reference_identity = json.loads(outputs["reference_identity.json"])
    reference_identity.pop("state_terminal_sha256", None)
    reference_identity["analysis_recovery"] = {"revision": config["protocol_revision"], "r1_closure_sha256": source["r1_closure_sha256"], "new_solver_run_count": 0}
    outputs["reference_identity.json"] = r1c.canonical_json(reference_identity)
    runtime = json.loads(outputs["runtime.json"])
    runtime["analysis_revision"] = {"protocol_revision": config["protocol_revision"], "implementation_commit": config["implementation_commit"], "analyzer_sha256": r1c.sha256_path(root / ANALYZER_REL), "source_r1_preregistration_commit": source["r1_preregistration_commit"]}
    outputs["runtime.json"] = r1c.canonical_json(runtime)
    outputs["README.md"] += ("\n## Analysis-only recovery\n\nR1 solver raw was accepted after strict 108-atom cube replay; no solver was rerun. The R1 single-atom parser failure remains preserved.\n").encode()
    r1c.require(sorted(outputs) == sorted(config["output_files"]), "R2 output denominator differs")
    return summary, outputs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = args.project_root.resolve(); config = load_config(root)
    summary, outputs = build_analysis(root, config)
    target = root / config["analysis_root"]
    if not args.dry_run:
        r1c.require(not target.exists(), "R2 analysis root exists")
        target.mkdir(parents=True)
        for name, data in outputs.items(): (target / name).write_bytes(data)
    print(json.dumps({"status": summary["status"], "failed_gates": summary["failed_gates"], "new_solver_run_count": 0, "output_written": not args.dry_run}, sort_keys=True))
    return 0


if __name__ == "__main__": raise SystemExit(main())
