#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import io
import json
import subprocess
from pathlib import Path

BASE_COMMIT = "751692ef878df5a80707ee6dd73dd03dd81b3a05"
CONFIG_REL = Path("config/S2_g2_architecture_candidates_r1.json")
PROTOCOL_REL = Path("docs/S2_G2_ARCHITECTURE_CANDIDATES_R1_PROTOCOL.md")
MANIFEST_REL = Path("config/S2_g2_architecture_candidates_r1_manifest.tsv")
CASE_ROOT_REL = Path("orchestration/s2/g2_architecture_candidates_r1/cases")
OUTPUT_ROOT_REL = Path("analysis/s2/g2_architecture_candidates_r1_20260811")
CANDIDATE_ORDER = (
    "pw_fft_reference",
    "atomic_fft",
    "atomic_low_g_explicit",
    "atomic_low_g_complementary",
)
CELL_SPECS = (
    (1, "primitive_equilibrium", ((1, 0, 0), (0, 1, 0), (0, 0, 1)), "committed_ks_nl_equilibrium_cube"),
    (32, "two_by_two_by_two_conventional_fcc", ((-2, 2, 2), (2, -2, 2), (2, 2, -2)), "periodic_supercell_tiling_invariance_only"),
    (108, "three_by_three_by_three_conventional_fcc", ((-3, 3, 3), (3, -3, 3), (3, 3, -3)), "periodic_supercell_tiling_invariance_only"),
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def canonical_json(payload: object) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(root: Path, *args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
    )
    if check and completed.returncode != 0:
        raise ValueError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def load_config(root: Path) -> dict:
    return json.loads((root / CONFIG_REL).read_text())


def determinant3(matrix: list[list[int]] | tuple[tuple[int, ...], ...]) -> int:
    a, b, c = matrix
    return (
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - a[1] * (b[0] * c[2] - b[2] * c[0])
        + a[2] * (b[0] * c[1] - b[1] * c[0])
    )


def expected_cases(config: dict) -> list[dict]:
    cases: list[dict] = []
    serial = 1
    cells = {int(row["atom_count"]): row for row in config["cell_ladder"]}
    for candidate_id in CANDIDATE_ORDER:
        for atom_count, _, _, _ in CELL_SPECS:
            cell = cells[atom_count]
            cases.append(
                {
                    "case_id": f"S2-G2-20260811-{serial:03d}",
                    "phase": "architecture_registration",
                    "material": "Al",
                    "atom_count": atom_count,
                    "cell_role": cell["cell_role"],
                    "supercell_matrix": cell["supercell_matrix"],
                    "candidate_id": candidate_id,
                    "reference_density_role": cell["reference_density_role"],
                    "is_reference": candidate_id == "pw_fft_reference",
                    "local_perturbation_reference_required_later": atom_count == 108,
                    "solver_started": False,
                    "scientific_result": False,
                }
            )
            serial += 1
    return cases


def manifest_bytes(config: dict) -> bytes:
    columns = (
        "case_id",
        "phase",
        "material",
        "atom_count",
        "cell_role",
        "candidate_id",
        "reference_density_role",
        "is_reference",
        "local_perturbation_reference_required_later",
        "solver_started",
        "scientific_result",
    )
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    for case in expected_cases(config):
        row = dict(case)
        row.pop("supercell_matrix")
        for key in ("is_reference", "local_perturbation_reference_required_later", "solver_started", "scientific_result"):
            row[key] = str(row[key]).lower()
        writer.writerow(row)
    return stream.getvalue().encode()


def case_metadata_bytes(config: dict, case: dict) -> bytes:
    payload = {
        **case,
        "protocol_revision": config["protocol_revision"],
        "g1_read_only": True,
        "reference_density_sha256": config["reference_density"]["sha256"],
        "registered_acceptance": config["acceptance"],
        "registered_rank_contract": config["rank_contract"],
    }
    return canonical_json(payload)


def registered_artifacts(config: dict) -> dict[Path, bytes]:
    artifacts = {MANIFEST_REL: manifest_bytes(config)}
    for case in expected_cases(config):
        artifacts[CASE_ROOT_REL / case["case_id"] / "metadata.json"] = case_metadata_bytes(config, case)
    return artifacts


def validate_config(config: dict) -> None:
    require(config["schema_version"] == 1, "schema version differs")
    require(config["protocol_revision"] == "S2-G2-ARCHITECTURE-CANDIDATES-20260811-R1", "protocol differs")
    require(config["base_commit"] == BASE_COMMIT, "base commit differs")
    require(config["stage"] == "S2" and config["gate"] == "G2", "stage/gate differs")
    scope = config["scope"]
    require(scope["material"] == "Al", "R1 must be Al-only")
    require(scope["mg_enabled"] is False, "Mg must remain disabled")
    require(scope["ml_enabled"] is False, "ML must remain disabled")
    require(scope["self_consistent_coefficient_optimization_enabled"] is False, "S3 optimization leaked into S2")
    require(scope["current_subgates"] == ["G2a", "G2b"], "subgate order differs")
    require(tuple(config["candidate_order"]) == CANDIDATE_ORDER, "candidate order differs")
    require(set(config["candidates"]) == set(CANDIDATE_ORDER), "candidate denominator differs")
    require(config["candidates"]["pw_fft_reference"]["may_be_selected_as_compressed_winner"] is False, "reference winner role differs")
    require(config["candidates"]["atomic_low_g_explicit"]["g0_convention"] == "g0_is_unique_charge_channel", "explicit G0 convention differs")
    require("projected_out" in config["candidates"]["atomic_low_g_complementary"]["density_representation"], "complementary route is not disjoint")
    require(config["low_g_contract"]["eta_q_over_2kf"] == [0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0], "low-G response ladder differs")
    require(config["low_g_contract"]["signed_amplitudes"] == [-0.01, 0.01], "signed response amplitudes differ")
    require(config["rank_contract"]["per_geometry_hard_rank_selection_forbidden"] is True, "per-geometry rank changes enabled")
    require(config["rank_contract"]["maximum_effective_condition_number"] == 1e8, "condition-number gate differs")
    require(config["rank_contract"]["minimum_retained_eigenvalue_over_threshold"] == 100.0, "spectral gap differs")
    gates = config["acceptance"]
    require(gates["electron_number_relative_error_strict_lt"] == 1e-10, "electron gate differs")
    require(gates["equilibrium_density_relative_l2_strict_lt"] == 0.01, "equilibrium L2 gate differs")
    require(gates["perturbed_density_relative_l2_p95_strict_lt"] == 0.02, "perturbed L2 gate differs")
    require(gates["effective_coefficient_fraction_max"] == 0.3, "compression gate differs")
    require(gates["eggbox_energy_peak_to_peak_max_mev_per_atom"] == 1.0, "egg-box energy gate differs")
    require(gates["eggbox_pseudoforce_max_ev_per_angstrom"] == 0.002, "egg-box force gate differs")
    cells = config["cell_ladder"]
    require(len(cells) == 3, "cell denominator differs")
    observed = []
    for cell, expected in zip(cells, CELL_SPECS):
        atom_count, role, matrix, reference_role = expected
        require(cell["atom_count"] == atom_count, "cell atom count differs")
        require(cell["cell_role"] == role, "cell role differs")
        require(tuple(tuple(row) for row in cell["supercell_matrix"]) == matrix, "supercell matrix differs")
        require(abs(determinant3(cell["supercell_matrix"])) == atom_count, "supercell determinant differs")
        require(cell["reference_density_role"] == reference_role, "reference density role differs")
        observed.append(atom_count)
    require(observed == [1, 32, 108], "cell ladder differs")
    execution = config["execution_contract"]
    require(execution["registered_case_count"] == 12, "case count differs")
    require(execution["new_solver_run_count"] == 0, "R1 cannot start a solver")
    require(execution["r1_deliverable"] == "architecture_and_data_contract_preregistration_only", "R1 deliverable differs")


def validate_sources(root: Path, config: dict) -> None:
    boundary = config["g1_source_boundary"]
    require(boundary["g1_is_read_only"] is True, "G1 source is not read-only")
    head = git(root, "rev-parse", "HEAD")
    for key in ("g1_final_evidence_commit", "r3_density_source_commit", "r5_followup_source_commit"):
        commit = boundary[key]
        completed = subprocess.run(["git", "merge-base", "--is-ancestor", commit, head], cwd=root, check=False)
        require(completed.returncode == 0, f"source commit is not an ancestor: {key}")
    density = root / config["reference_density"]["path"]
    structure = root / config["reference_density"]["structure_path"]
    require(density.is_file() and not density.is_symlink(), "reference density missing or symlinked")
    require(structure.is_file() and not structure.is_symlink(), "reference structure missing or symlinked")
    require(density.stat().st_size == config["reference_density"]["size_bytes"], "reference density size differs")
    require(sha256_path(density) == config["reference_density"]["sha256"], "reference density SHA differs")
    require(sha256_path(structure) == config["reference_density"]["structure_sha256"], "reference structure SHA differs")
    source_commit = boundary["r5_followup_source_commit"]
    source_bytes = subprocess.run(
        ["git", "show", f"{source_commit}:{config['reference_density']['path']}"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout
    require(sha256_bytes(source_bytes) == config["reference_density"]["sha256"], "committed source density differs")


def validate_registered_artifacts(root: Path, config: dict) -> None:
    expected = registered_artifacts(config)
    for relative, data in expected.items():
        path = root / relative
        require(path.is_file() and not path.is_symlink(), f"registered artifact missing: {relative}")
        require(path.read_bytes() == data, f"registered artifact differs: {relative}")
    case_root = root / CASE_ROOT_REL
    actual = sorted(path.relative_to(root) for path in case_root.glob("*/metadata.json"))
    expected_paths = sorted(relative for relative in expected if relative != MANIFEST_REL)
    require(actual == expected_paths, "case artifact denominator differs")


def validate_absence(root: Path, config: dict) -> None:
    execution = config["execution_contract"]
    require(not Path(execution["formal_state_root"]).exists(), "formal S2 state already exists")
    require(not (root / execution["analysis_root"]).exists(), "S2 analysis root already exists")


def validate_content(root: Path, config: dict) -> None:
    validate_config(config)
    validate_sources(root, config)
    validate_registered_artifacts(root, config)
    validate_absence(root, config)
