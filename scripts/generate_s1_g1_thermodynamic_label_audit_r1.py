#!/usr/bin/env python3
"""Generate the frozen S1-G1 thermodynamic-label R1 preregistration.

The default is a read-only validation/dry run.  ``--write`` is intentionally
stricter: it requires a clean repository whose complete implementation closure
is already committed, and it exclusively creates the config, manifest, and
content-addressed 40-run input tree.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from s1_electron_number_common import expected_electrons, parse_stru
from s1_g1_thermodynamic_label_common import (
    AUDIT_IDS,
    CUBE_PRECISION,
    DENSITY_D1_LIMIT,
    DENSITY_D2_LIMIT,
    DERIVATIVE_DG_LIMIT,
    DERIVATIVE_RMS_EV_LIMIT,
    ELECTRON_RELATIVE_ERROR_LIMIT,
    EXECUTION_ORDER,
    IDENTITY_RESIDUAL_EV_PER_ATOM_LIMIT,
    K_GATE_COMPLETION_IDS,
    K_GATE_EXECUTION_IDS,
    PILOT_IDS,
    PROTOCOL_REVISION,
    canonical_json_bytes,
    derive_kpt,
    derive_label_input,
    manifest_bytes,
    parse_input_text,
    parse_kpt_text,
    parse_thermodynamic_log,
    read_regular_bytes,
    read_regular_text,
    require,
    sha256_bytes,
    sha256_regular_file,
    validate_local_pseudopotential,
)


BASE_EVIDENCE_COMMIT = "a28082cfc49d9a76c8eaaa406786029ebe791ba6"
CONFIG_PATH = Path("config/S1_g1_thermodynamic_label_audit_r1.json")
MANIFEST_PATH = Path(
    "config/S1_g1_thermodynamic_label_audit_r1_manifest.tsv"
)
INPUT_ROOT = Path("inputs/s1/g1_thermodynamic_label_audit_r1")
PROTOCOL_PATH = Path("docs/S1_G1_THERMODYNAMIC_LABEL_AUDIT_R1_PROTOCOL.md")
R8_CONFIG = Path("config/S1_non_equilibrium_convergence.json")
R8_MANIFEST = Path("config/S1_non_equilibrium_run_manifest.tsv")
BASELINE_CONFIG = Path("config/S1_baseline_protocol.json")
CORE_SUMMARY = Path("analysis/s1/core_eos_20260805/summary.json")
R8_SUMMARY = Path("analysis/s1/non_equilibrium_convergence_20260805/summary.json")
R2_RUNTIME_CONFIG = Path("config/S1_electron_number_audit_r2.json")

DEFAULT_SOURCE_ROOT = Path(
    "/home/shenwei01/wt_melting_restore_20260724/integrated/abacus_source"
)
DEFAULT_SOURCE_ARCHIVE = Path("/home/shenwei01/abacus_wt_build_source_20260724.tar.gz")
SOURCE_ARCHIVE_SHA256 = "7c8522d4085cbac6c9bc454155873b3f21973d2e709fe9dba4c6fd25f7c885a3"

SOURCE_SEMANTIC_SPECS: tuple[dict[str, object], ...] = (
    {
        "relative_path": "source/source_io/module_parameter/read_input_item_output.cpp",
        "sha256": "cd23b6e31258f0e0d0a0079e4669169627b5a3510c9b80f024ddc3737a10295a",
        "markers": (
            'Input_Item item("out_pot")',
            "on real space grids (in Ry)",
            "item.read_value = [](const Input_Item& item, Parameter& para)",
            "para.input.out_pot[1] = std::stoi(item.str_values[1])",
        ),
    },
    {
        "relative_path": "source/source_io/module_ctrl/ctrl_output_fp.cpp",
        "sha256": "fa5e8f5b109aba4fdba54bdac5b12c283ea4dfd3455647e7f23e657f212e5de8",
        "markers": (
            "PARAM.inp.out_pot[0] == 1",
            'PARAM.globalv.global_out_dir + "pot"',
            "pelec->pot->get_eff_v(is)",
            "PARAM.inp.out_pot[1]",
        ),
    },
    {
        "relative_path": "source/source_estate/elecstate_print.cpp",
        "sha256": "0b24914596b30f6236129efc61eff1d9dd7821ddc985680371a5b2b746421046",
        "markers": (
            'titles.push_back("E_KohnSham")',
            'titles.push_back("E_KS(sigma->0)")',
            "elec.f_en.etot - elec.f_en.demet / (2 + n_order)",
            'titles.push_back("E_entropy(-TS)")',
            'titles.push_back("E_localpp")',
            'titles.push_back("E_Fermi")',
        ),
    },
    {
        "relative_path": "source/source_estate/elecstate_energy.cpp",
        "sha256": "64f1600934aedf2cb4f9896b4e94aa59d0d5d224f9567198d250f9b57630af40",
        "markers": (
            "this->f_en.e_local_pp = get_local_pp_energy();",
            "this->pot->get_fixed_v()",
            "this->pot->get_eff_v(0)",
        ),
    },
    {
        "relative_path": "source/source_estate/elecstate_energy_terms.cpp",
        "sha256": "15c4de5cc9b4b88c57dac12d3c83a570442e07c6b8a5940d1820cb41996651a3",
        "markers": (
            "double ElecState::get_local_pp_energy()",
            "this->pot->get_fixed_v()",
            "this->charge->rho[is]",
        ),
    },
    {
        "relative_path": "source/source_estate/fp_energy.cpp",
        "sha256": "2fbf4a9a2d3b047b81fe39fedf3a3ff53a9b1a77b8b1be211d5be63f060d6b44",
        "markers": (
            "double fenergy::calculate_etot()",
            "etot = eband + deband",
            "+ hartree_energy + demet + descf",
        ),
    },
    {
        "relative_path": "source/source_estate/module_pot/potential_new.cpp",
        "sha256": "d6d8c1a1d4d9d81ef32152318c21d72fd57fb1b71ae27829f9533240f8e87b3e",
        "markers": (
            "void Potential::cal_fixed_v(double* vl_pseudo)",
            "this->v_eff_fixed.data()",
            "this->get_eff_v(i)",
            "components[i]->cal_v_eff(chg, ucell, v_eff)",
        ),
    },
)

NEW_IMPLEMENTATION_PATHS = (
    str(PROTOCOL_PATH),
    "scripts/s1_g1_thermodynamic_label_common.py",
    "scripts/generate_s1_g1_thermodynamic_label_audit_r1.py",
    "scripts/parse_s1_g1_thermodynamic_labels.py",
    "scripts/validate_s1_g1_thermodynamic_label_audit_r1.py",
    "scripts/analyze_s1_g1_thermodynamic_label_audit_r1.py",
    "scripts/run_s1_g1_thermodynamic_label_audit_r1.sh",
    "tests/unit/test_s1_g1_thermodynamic_label_common.py",
    "tests/unit/test_s1_g1_thermodynamic_label_audit_r1.py",
)
NEW_EXECUTABLE_IMPLEMENTATION_PATHS = {
    "scripts/run_s1_g1_thermodynamic_label_audit_r1.sh",
}

_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
RATIOS = ("0.90", "0.94", "0.97", "1.00", "1.03", "1.06", "1.10")
RATIO_TOKENS = ("v090", "v094", "v097", "v100", "v103", "v106", "v110")
SMEARING = {
    "standard": "0.00734986",
    "half": "0.00367493",
    "quarter": "0.001837465",
}
COMMON_MESH = {"al": (28, 28, 28), "mg": (24, 24, 16)}
EXTRA_MESH = {"al": (32, 32, 32), "mg": (28, 28, 18)}
SOURCE_IDS = {
    "al": tuple(f"S1-20260805-{value:03d}" for value in range(85, 92)),
    "mg": tuple(f"S1-20260805-{value:03d}" for value in range(106, 113)),
}


def _git(project_root: Path, *arguments: str, text: bool = True) -> str | bytes:
    result = subprocess.check_output(
        ["git", "-C", str(project_root), *arguments], text=text
    )
    return result.strip() if text else result


def _project_root(path: Path) -> Path:
    root = path.resolve()
    git_root = Path(str(_git(root, "rev-parse", "--show-toplevel"))).resolve()
    require(root == git_root, "project root differs from Git top level")
    return root


def _relative(project_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError as error:
        raise ValueError(f"output lies outside project root: {path}") from error


def _regular_git_entry(project_root: Path, commit: str, relative: str) -> tuple[str, str]:
    raw = subprocess.check_output(
        ["git", "-C", str(project_root), "ls-tree", "-z", commit, "--", relative]
    )
    records = [record for record in raw.split(b"\0") if record]
    require(len(records) == 1, f"expected one Git entry: {commit}:{relative}")
    metadata, observed_path = records[0].split(b"\t", 1)
    mode, object_type, object_id = metadata.decode("ascii").split()
    require(observed_path.decode("utf-8") == relative, "Git entry path differs")
    require(object_type == "blob" and mode in {"100644", "100755"}, "not a Git blob")
    return mode, object_id


def _file_anchor(project_root: Path, relative: Path, commit: str = "HEAD") -> dict[str, str]:
    path = project_root / relative
    mode, oid = _regular_git_entry(project_root, commit, relative.as_posix())
    committed = _git(project_root, "show", f"{commit}:{relative.as_posix()}", text=False)
    require(committed == read_regular_bytes(path), f"working file differs: {relative}")
    return {
        "path": relative.as_posix(),
        "sha256": sha256_regular_file(path),
        "blob_oid": oid,
        "git_mode": mode,
    }


def _source_tree_oid(project_root: Path, experiment_id: str) -> str:
    relative = f"runs/{experiment_id}"
    oid = str(_git(project_root, "rev-parse", f"{BASE_EVIDENCE_COMMIT}:{relative}"))
    require(_HEX40.fullmatch(oid) is not None, "invalid source tree OID")
    require(str(_git(project_root, "cat-file", "-t", oid)) == "tree", "source is not tree")
    return oid


def _require_base_anchor(project_root: Path) -> str:
    full = str(_git(project_root, "rev-parse", BASE_EVIDENCE_COMMIT))
    require(full == BASE_EVIDENCE_COMMIT, "base evidence commit differs")
    result = subprocess.run(
        ["git", "-C", str(project_root), "merge-base", "--is-ancestor", BASE_EVIDENCE_COMMIT, "HEAD"]
    )
    require(result.returncode == 0, "base evidence commit is not an ancestor of HEAD")
    return str(_git(project_root, "rev-parse", "HEAD"))


def _require_clean(project_root: Path) -> None:
    status = str(
        _git(project_root, "status", "--porcelain=v1", "--untracked-files=all")
    )
    require(status == "", "formal preregistration requires a completely clean worktree")


def _implementation_paths(r2_config: dict[str, object]) -> tuple[str, ...]:
    inherited = r2_config.get("implementation")
    require(isinstance(inherited, dict) and inherited, "R2 implementation closure is missing")
    return tuple(dict.fromkeys((*inherited.keys(), *NEW_IMPLEMENTATION_PATHS)))


def _implementation_closure(
    project_root: Path, paths: Iterable[str], *, formal: bool
) -> tuple[dict[str, str], dict[str, str]]:
    hashes: dict[str, str] = {}
    modes: dict[str, str] = {}
    for relative in paths:
        path = project_root / relative
        require(path.is_file() and not path.is_symlink(), f"missing implementation: {relative}")
        hashes[relative] = sha256_regular_file(path)
        if formal:
            mode, _ = _regular_git_entry(project_root, "HEAD", relative)
            committed = _git(project_root, "show", f"HEAD:{relative}", text=False)
            require(committed == read_regular_bytes(path), f"implementation differs from HEAD: {relative}")
        else:
            mode = "100755" if os.access(path, os.X_OK) else "100644"
        if relative in NEW_EXECUTABLE_IMPLEMENTATION_PATHS:
            require(
                mode == "100755" and os.access(path, os.X_OK),
                f"required implementation is not executable: {relative}",
            )
        modes[relative] = mode
    return hashes, modes


def _validate_r8_source_manifest(project_root: Path) -> None:
    with (project_root / R8_MANIFEST).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    selected = {
        row["experiment_id"]: row
        for row in rows
        if row.get("experiment_id") in {*SOURCE_IDS["al"], *SOURCE_IDS["mg"]}
    }
    require(set(selected) == {*SOURCE_IDS["al"], *SOURCE_IDS["mg"]}, "R8 source IDs differ")
    for material in ("al", "mg"):
        for index, experiment_id in enumerate(SOURCE_IDS[material]):
            row = selected[experiment_id]
            require(row.get("material") == material, f"R8 material differs: {experiment_id}")
            require(row.get("series_id") == "ksdft_next_kmesh", f"R8 series differs: {experiment_id}")
            require(row.get("comparison_axis") == "kmesh", f"R8 axis differs: {experiment_id}")
            require(
                Decimal(str(row.get("volume_ratio"))) == Decimal(RATIOS[index]),
                f"R8 ratio differs: {experiment_id}",
            )


def validate_source_semantics(
    source_root: Path,
    archive_path: Path,
    *,
    specs: Iterable[dict[str, object]] = SOURCE_SEMANTIC_SPECS,
    archive_sha256: str = SOURCE_ARCHIVE_SHA256,
) -> dict[str, object]:
    evidence: list[dict[str, object]] = []
    for spec in specs:
        relative = str(spec["relative_path"])
        path = source_root / relative
        expected_hash = str(spec["sha256"])
        markers = tuple(str(value) for value in spec["markers"])
        require(sha256_regular_file(path) == expected_hash, f"source SHA-256 differs: {relative}")
        text = read_regular_text(path)
        missing = [marker for marker in markers if marker not in text]
        require(not missing, f"source semantic markers missing in {relative}: {missing}")
        evidence.append(
            {
                "path": str(path),
                "sha256": expected_hash,
                "required_markers": list(markers),
                "validated": True,
            }
        )
    require(
        sha256_regular_file(archive_path) == archive_sha256,
        "ABACUS source archive SHA-256 differs",
    )
    return {
        "source_root": str(source_root),
        "files": evidence,
        "source_archive": {
            "path": str(archive_path),
            "sha256": archive_sha256,
            "validated": True,
        },
        "semantic_conclusions": {
            "potential_basename_nspin1": "pot.cube",
            "potential_quantity": "final local effective KS potential get_eff_v(0)",
            "potential_units": "Ry",
            "potential_precision_argument": "out_pot[1]",
            "entropy_corrected_estimator": "E_KS(sigma->0)=etot-demet/(2+n_order)",
            "localpp": "integral rho(r)*v_fixed(r) for local pseudopotential",
            "kinetic_identity_scope": "nproj=0 current local-BLPS setting only",
        },
    }


def _plan_row(
    number: int,
    material: str,
    ratio_index: int,
    smearing_level: str,
    run_role: str,
    mesh: tuple[int, int, int],
) -> dict[str, object]:
    experiment_id = f"S1-20260806-{number:03d}"
    source_id = SOURCE_IDS[material][ratio_index]
    ratio = RATIOS[ratio_index]
    token = RATIO_TOKENS[ratio_index]
    suffix_level = (
        "extraquarter"
        if run_role == "extra_dense_quarter_k_anchor"
        else smearing_level.replace("_", "")
    )
    suffix = f"g1tlr1_{material}_{token}_{suffix_level}"
    return {
        "experiment_id": experiment_id,
        "material": material,
        "volume_ratio": ratio,
        "volume_token": token,
        "ratio_index": ratio_index,
        "smearing_level": smearing_level,
        "smearing_sigma_ry": SMEARING[smearing_level],
        "kmesh": mesh,
        "run_role": run_role,
        "source_experiment_id": source_id,
        "dense_standard_scalar_source_id": source_id,
        "suffix": suffix,
    }


def build_plan() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    number = 1
    for material in ("al", "mg"):
        for ratio_index in (0, 3, 6):
            rows.append(
                _plan_row(
                    number,
                    material,
                    ratio_index,
                    "standard",
                    "dense_standard_field_replay",
                    COMMON_MESH[material],
                )
            )
            number += 1
    for material in ("al", "mg"):
        for ratio_index in range(7):
            rows.append(
                _plan_row(
                    number,
                    material,
                    ratio_index,
                    "half",
                    "common_dense_half_eos",
                    COMMON_MESH[material],
                )
            )
            number += 1
    for material in ("al", "mg"):
        for ratio_index in range(7):
            rows.append(
                _plan_row(
                    number,
                    material,
                    ratio_index,
                    "quarter",
                    "common_dense_quarter_eos",
                    COMMON_MESH[material],
                )
            )
            number += 1
    for material in ("al", "mg"):
        for ratio_index in (0, 3, 6):
            rows.append(
                _plan_row(
                    number,
                    material,
                    ratio_index,
                    "quarter",
                    "extra_dense_quarter_k_anchor",
                    EXTRA_MESH[material],
                )
            )
            number += 1
    require(tuple(row["experiment_id"] for row in rows) == AUDIT_IDS, "40-run ID matrix differs")
    execution_indices = {
        experiment_id: index for index, experiment_id in enumerate(EXECUTION_ORDER, 1)
    }
    by_axes = {
        (str(row["material"]), str(row["volume_ratio"]), str(row["smearing_level"]), str(row["run_role"])): str(row["experiment_id"])
        for row in rows
    }
    for row in rows:
        experiment_id = str(row["experiment_id"])
        row["execution_index"] = execution_indices[experiment_id]
        row["execution_phase"] = (
            "P0" if experiment_id in PILOT_IDS else
            "P1" if experiment_id in K_GATE_COMPLETION_IDS else
            "P2"
        )
        material = str(row["material"])
        ratio = str(row["volume_ratio"])
        role = str(row["run_role"])
        if role == "dense_standard_field_replay":
            row["reference_experiment_id"] = row["source_experiment_id"]
            row["common_quarter_partner_id"] = ""
        elif role == "common_dense_half_eos":
            row["reference_experiment_id"] = row["source_experiment_id"]
            row["common_quarter_partner_id"] = ""
        elif role == "common_dense_quarter_eos":
            row["reference_experiment_id"] = by_axes[(material, ratio, "half", "common_dense_half_eos")]
            row["common_quarter_partner_id"] = ""
        else:
            partner = by_axes[(material, ratio, "quarter", "common_dense_quarter_eos")]
            row["reference_experiment_id"] = partner
            row["common_quarter_partner_id"] = partner
    require(tuple(EXECUTION_ORDER[:12]) == K_GATE_EXECUTION_IDS, "k-gate prefix differs")
    return rows


def _source_record(project_root: Path, experiment_id: str) -> dict[str, object]:
    run = project_root / "runs" / experiment_id
    require(run.is_dir() and not run.is_symlink(), f"missing source run: {experiment_id}")
    source_files = ("INPUT", "STRU", "KPT", "input_metadata.json", "result.json")
    for basename in source_files:
        relative = f"runs/{experiment_id}/{basename}"
        committed = _git(project_root, "show", f"{BASE_EVIDENCE_COMMIT}:{relative}", text=False)
        require(committed == read_regular_bytes(run / basename), f"source differs from base: {relative}")
    metadata = json.loads(read_regular_text(run / "input_metadata.json"))
    result = json.loads(read_regular_text(run / "result.json"))
    require(isinstance(metadata, dict) and isinstance(result, dict), "source JSON differs")
    require(result.get("converged") is True, f"source is not converged: {experiment_id}")
    pseudo_name = str(metadata.get("pseudopotential"))
    require(Path(pseudo_name).name == pseudo_name, "source pseudopotential is not basename")
    pseudo_path = run / pseudo_name
    pseudo_relative = f"runs/{experiment_id}/{pseudo_name}"
    committed_pseudo = _git(
        project_root, "show", f"{BASE_EVIDENCE_COMMIT}:{pseudo_relative}", text=False
    )
    require(committed_pseudo == read_regular_bytes(pseudo_path), "source pseudopotential differs")
    asset = project_root / "assets" / "pseudo" / pseudo_name
    require(read_regular_bytes(asset) == read_regular_bytes(pseudo_path), "pseudo asset differs")
    local_pseudo = validate_local_pseudopotential(pseudo_path)
    log_paths = sorted(run.glob("OUT.*/running_scf.log"))
    require(len(log_paths) == 1, f"source log count differs: {experiment_id}")
    log = log_paths[0]
    relative_log = log.relative_to(project_root).as_posix()
    committed_log = _git(
        project_root, "show", f"{BASE_EVIDENCE_COMMIT}:{relative_log}", text=False
    )
    require(committed_log == read_regular_bytes(log), "source log differs from base")
    structure = parse_stru(run / "STRU")
    atom_count = sum(structure.species_counts.values())
    thermodynamics = parse_thermodynamic_log(
        read_regular_text(log), expected_atom_count=atom_count
    )
    expected, derivation = expected_electrons(run)
    require(float(metadata.get("expected_electrons", -1)) == expected, "source electron count differs")
    return {
        "experiment_id": experiment_id,
        "run_path": f"runs/{experiment_id}",
        "run_tree_oid": _source_tree_oid(project_root, experiment_id),
        "metadata": metadata,
        "result": result,
        "input_sha256": sha256_regular_file(run / "INPUT"),
        "stru_sha256": sha256_regular_file(run / "STRU"),
        "kpt_sha256": sha256_regular_file(run / "KPT"),
        "metadata_sha256": sha256_regular_file(run / "input_metadata.json"),
        "result_path": f"runs/{experiment_id}/result.json",
        "result_sha256": sha256_regular_file(run / "result.json"),
        "log_path": relative_log,
        "log_sha256": sha256_regular_file(log),
        "pseudo_name": pseudo_name,
        "pseudo_sha256": sha256_regular_file(pseudo_path),
        "pseudo_validation": local_pseudo,
        "atom_count": atom_count,
        "expected_electrons": expected,
        "expected_electron_derivation": derivation,
        "thermodynamic_source_validation": {
            "F_ev_per_atom": float(thermodynamics["energy_labels_ev_per_atom"]["F"]),
            "E_ec_ev_per_atom": float(thermodynamics["energy_labels_ev_per_atom"]["E_ec"]),
            "legacy_energy_observable_remap": "zero_temp_extrapolated_energy -> entropy_corrected_estimator",
            "zero_temperature_exact_claim": False,
        },
    }


def _metadata(row: dict[str, object], source: dict[str, object]) -> bytes:
    payload = {
        "protocol_revision": PROTOCOL_REVISION,
        "dataset_kind": "g1_thermodynamic_label_audit_r1",
        "candidate_status": "preregistered",
        "experiment_id": row["experiment_id"],
        "material": row["material"],
        "volume_ratio": row["volume_ratio"],
        "smearing_level": row["smearing_level"],
        "smearing_sigma_ry": row["smearing_sigma_ry"],
        "kmesh": list(row["kmesh"]),
        "run_role": row["run_role"],
        "source_experiment_id": row["source_experiment_id"],
        "reference_experiment_id": row["reference_experiment_id"],
        "common_quarter_partner_id": row["common_quarter_partner_id"],
        "dense_standard_scalar_source_id": row["dense_standard_scalar_source_id"],
        "suffix": row["suffix"],
        "solver": "ksdft",
        "pseudopotential": source["pseudo_name"],
        "pseudopotential_sha256": source["pseudo_sha256"],
        "expected_electrons": format(float(source["expected_electrons"]), ".17g"),
        "atom_count": str(source["atom_count"]),
        "cube_precision": str(CUBE_PRECISION),
        "density_basename": "chg.cube",
        "potential_basename": "pot.cube",
        "rank_count": 4,
        "energy_scalar_semantics": "finite_smearing_entropy_corrected_estimator_not_exact_zero_temperature",
        "field_bundle_semantics": "finite_temperature_Mermin_{rho_sigma,F_s_sigma,g_sigma}",
        "zero_temperature_exact_claim": False,
    }
    return canonical_json_bytes(payload)


def _build_artifacts(
    project_root: Path,
    input_root: Path,
    plan: list[dict[str, object]],
    sources: dict[str, dict[str, object]],
) -> tuple[dict[str, bytes], list[dict[str, object]]]:
    artifacts: dict[str, bytes] = {}
    manifest_rows: list[dict[str, object]] = []
    for row in plan:
        source = sources[str(row["source_experiment_id"])]
        source_run = project_root / str(source["run_path"])
        source_metadata = source["metadata"]
        require(isinstance(source_metadata, dict), "source metadata differs")
        require(source_metadata.get("material") == row["material"], "source material differs")
        require(source_metadata.get("solver") == "ksdft", "source solver differs")
        require(source_metadata.get("series_id") == "ksdft_next_kmesh", "source series differs")
        require(source_metadata.get("smearing_method") == "fd", "source smearing method differs")
        require(
            abs(
                float(source_metadata.get("volume_ratio"))
                - float(row["volume_ratio"])
            )
            <= 1.0e-12,
            "source volume ratio differs",
        )
        require(
            tuple(source_metadata.get("kmesh", ())) == COMMON_MESH[str(row["material"])],
            "source common-dense k mesh differs",
        )
        require(
            Decimal(str(source_metadata.get("smearing_sigma_ry")))
            == Decimal(SMEARING["standard"]),
            "source standard smearing differs",
        )
        input_data = derive_label_input(
            read_regular_bytes(source_run / "INPUT"),
            suffix=str(row["suffix"]),
            smearing_sigma_ry=str(row["smearing_sigma_ry"]),
        )
        source_input = parse_input_text(read_regular_bytes(source_run / "INPUT"))
        expected_input = {
            "calculation": ("scf",),
            "esolver_type": ("ksdft",),
            "basis_type": ("pw",),
            "symmetry": ("0",),
            "pseudo_dir": (".",),
            "ecutwfc": ("40",),
            "ecutrho": ("160",),
            "scf_nmax": ("200",),
            "scf_thr": ("1e-10",),
            "ks_solver": ("cg",),
            "smearing_method": ("fd",),
            "smearing_sigma": (SMEARING["standard"],),
            "mixing_type": ("broyden",),
            "mixing_beta": ("0.4",),
        }
        for key, expected_value in expected_input.items():
            require(source_input.get(key) == expected_value, f"source INPUT {key} differs")
        require("out_chg" not in source_input and "out_pot" not in source_input, "source already outputs fields")
        parsed_derived_input = parse_input_text(input_data)
        require(
            parsed_derived_input.get("pseudo_dir") == (".",),
            "derived INPUT must retain local pseudo_dir .",
        )
        source_mesh = parse_kpt_text(read_regular_bytes(source_run / "KPT"))
        desired_mesh = tuple(int(value) for value in row["kmesh"])
        kpt_data = (
            read_regular_bytes(source_run / "KPT")
            if source_mesh == desired_mesh
            else derive_kpt(read_regular_bytes(source_run / "KPT"), desired_mesh)
        )
        stru_data = read_regular_bytes(source_run / "STRU")
        pseudo_name = str(source["pseudo_name"])
        pseudo_data = read_regular_bytes(source_run / pseudo_name)
        metadata_data = _metadata(row, source)
        directory = input_root / str(row["experiment_id"])
        file_data = {
            "INPUT": input_data,
            "STRU": stru_data,
            "KPT": kpt_data,
            "metadata.json": metadata_data,
        }
        for basename, data in file_data.items():
            artifacts[(directory / basename).as_posix()] = data
        manifest_rows.append(
            {
                "execution_index": row["execution_index"],
                "execution_phase": row["execution_phase"],
                "experiment_id": row["experiment_id"],
                "input_directory": directory.as_posix(),
                "material": row["material"],
                "volume_ratio": row["volume_ratio"],
                "smearing_level": row["smearing_level"],
                "smearing_sigma_ry": row["smearing_sigma_ry"],
                "kmesh": "x".join(str(value) for value in desired_mesh),
                "run_role": row["run_role"],
                "source_experiment_id": row["source_experiment_id"],
                "source_run_path": source["run_path"],
                "source_run_tree_oid": source["run_tree_oid"],
                "reference_experiment_id": row["reference_experiment_id"],
                "common_quarter_partner_id": row["common_quarter_partner_id"],
                "dense_standard_scalar_source_id": row["dense_standard_scalar_source_id"],
                "suffix": row["suffix"],
                "input_sha256": sha256_bytes(input_data),
                "stru_sha256": sha256_bytes(stru_data),
                "kpt_sha256": sha256_bytes(kpt_data),
                "metadata_sha256": sha256_bytes(metadata_data),
                "pseudopotential": pseudo_name,
                "pseudopotential_sha256": sha256_bytes(pseudo_data),
                "source_input_sha256": source["input_sha256"],
                "source_stru_sha256": source["stru_sha256"],
                "source_kpt_sha256": source["kpt_sha256"],
                "source_metadata_sha256": source["metadata_sha256"],
                "source_result_path": source["result_path"],
                "source_result_sha256": source["result_sha256"],
                "source_log_path": source["log_path"],
                "source_log_sha256": source["log_sha256"],
                "expected_electrons": format(float(source["expected_electrons"]), ".17g"),
                "atom_count": source["atom_count"],
                "cube_precision": CUBE_PRECISION,
                "density_basename": "chg.cube",
                "potential_basename": "pot.cube",
            }
        )
    return artifacts, manifest_rows


def _energy_matrix(plan: list[dict[str, object]]) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for material in ("al", "mg"):
        for index, ratio in enumerate(RATIOS):
            entries.append(
                {
                    "material": material,
                    "volume_ratio": ratio,
                    "smearing_level": "standard",
                    "source_kind": "immutable_reused_R8_scalar",
                    "experiment_id": SOURCE_IDS[material][index],
                }
            )
        for level, role in (
            ("half", "common_dense_half_eos"),
            ("quarter", "common_dense_quarter_eos"),
        ):
            for ratio in RATIOS:
                match = next(
                    row for row in plan
                    if row["material"] == material
                    and row["volume_ratio"] == ratio
                    and row["run_role"] == role
                )
                entries.append(
                    {
                        "material": material,
                        "volume_ratio": ratio,
                        "smearing_level": level,
                        "source_kind": "new_registered_run",
                        "experiment_id": match["experiment_id"],
                    }
                )
    require(len(entries) == 42, "energy matrix must contain 42 points")
    return entries


def prepare(
    project_root: Path,
    *,
    output_config: Path = CONFIG_PATH,
    output_manifest: Path = MANIFEST_PATH,
    input_root: Path = INPUT_ROOT,
    source_root: Path = DEFAULT_SOURCE_ROOT,
    source_archive: Path = DEFAULT_SOURCE_ARCHIVE,
    source_semantic_specs: Iterable[dict[str, object]] = SOURCE_SEMANTIC_SPECS,
    source_archive_sha256: str = SOURCE_ARCHIVE_SHA256,
    write: bool = False,
) -> dict[str, object]:
    project_root = _project_root(project_root)
    generated_from_commit = _require_base_anchor(project_root)
    output_config_rel = Path(_relative(project_root, project_root / output_config))
    output_manifest_rel = Path(_relative(project_root, project_root / output_manifest))
    input_root_rel = Path(_relative(project_root, project_root / input_root))
    if write:
        require(output_config_rel == CONFIG_PATH, "formal config path must be canonical")
        require(output_manifest_rel == MANIFEST_PATH, "formal manifest path must be canonical")
        require(input_root_rel == INPUT_ROOT, "formal input root must be canonical")
        require(
            source_root.resolve() == DEFAULT_SOURCE_ROOT,
            "formal source root must be the registered absolute path",
        )
        require(
            source_archive.resolve() == DEFAULT_SOURCE_ARCHIVE,
            "formal source archive must be the registered absolute path",
        )
        require(
            source_semantic_specs is SOURCE_SEMANTIC_SPECS
            and source_archive_sha256 == SOURCE_ARCHIVE_SHA256,
            "formal source semantic specification must be the registered default",
        )
    for output in (project_root / output_config_rel, project_root / output_manifest_rel, project_root / input_root_rel):
        require(not output.exists(), f"refusing to overwrite frozen output: {output}")
    if write:
        _require_clean(project_root)

    upstream_paths = (
        BASELINE_CONFIG,
        R8_CONFIG,
        R8_MANIFEST,
        CORE_SUMMARY,
        R8_SUMMARY,
        R2_RUNTIME_CONFIG,
    )
    upstream = {
        path.as_posix(): _file_anchor(project_root, path, BASE_EVIDENCE_COMMIT)
        for path in upstream_paths
    }
    r2_config = json.loads(read_regular_text(project_root / R2_RUNTIME_CONFIG))
    require(isinstance(r2_config, dict), "R2 runtime configuration is not an object")
    require(
        sha256_regular_file(project_root / R2_RUNTIME_CONFIG)
        == "bfed0e87451ccf60ff8239b5ad25d6a4e62e52e037d560ffa3dcdb4d08a83624",
        "R2 runtime configuration SHA-256 differs",
    )
    implementation, implementation_modes = _implementation_closure(
        project_root, _implementation_paths(r2_config), formal=write
    )
    inherited_implementation = r2_config["implementation"]
    inherited_modes = r2_config["implementation_git_modes"]
    require(isinstance(inherited_implementation, dict), "R2 implementation differs")
    require(isinstance(inherited_modes, dict), "R2 implementation modes differ")
    for relative, expected_hash in inherited_implementation.items():
        require(
            implementation[relative] == expected_hash,
            f"accepted inherited implementation changed: {relative}",
        )
        require(
            implementation_modes[relative] == inherited_modes.get(relative),
            f"accepted inherited implementation mode changed: {relative}",
        )
    source_semantics = validate_source_semantics(
        source_root,
        source_archive,
        specs=source_semantic_specs,
        archive_sha256=source_archive_sha256,
    )
    _validate_r8_source_manifest(project_root)
    plan = build_plan()
    sources = {
        experiment_id: _source_record(project_root, experiment_id)
        for experiment_id in (*SOURCE_IDS["al"], *SOURCE_IDS["mg"])
    }
    artifacts, rows = _build_artifacts(project_root, input_root_rel, plan, sources)
    encoded_manifest = manifest_bytes(rows)

    endpoint_levels = {
        level: [
            row["experiment_id"] for row in plan
            if row["volume_ratio"] in {"0.90", "1.00", "1.10"}
            and row["smearing_level"] == level
            and row["run_role"] != "extra_dense_quarter_k_anchor"
        ]
        for level in ("standard", "half", "quarter")
    }
    require(all(len(values) == 6 for values in endpoint_levels.values()), "endpoint labels differ")
    extra_ids = [
        row["experiment_id"] for row in plan
        if row["run_role"] == "extra_dense_quarter_k_anchor"
    ]
    require(len(extra_ids) == 6, "extra-dense group differs")
    config: dict[str, object] = {
        "schema_version": 1,
        "protocol_revision": PROTOCOL_REVISION,
        "status": "preregistered",
        "preregistration_date": "2026-08-06",
        "generated_from_commit": generated_from_commit,
        "base_evidence_commit": BASE_EVIDENCE_COMMIT,
        "scope": "G1 third-smearing / dense-k thermodynamic-label audit only",
        "registered_experiment_ids": list(AUDIT_IDS),
        "execution_order": list(EXECUTION_ORDER),
        "execution": {
            "rank_count": 4,
            "pilot_ids": list(PILOT_IDS),
            "k_gate_completion_ids": list(K_GATE_COMPLETION_IDS),
            "k_gate_execution_prefix": list(K_GATE_EXECUTION_IDS),
            "phase_barriers_fail_closed": True,
            "stop_after_first_preserved_failure": True,
            "absolute_deadline_watchdog_seconds": r2_config["runtime_audit"]["absolute_deadline_watchdog_seconds"],
        },
        "run_matrix": [
            {
                key: (list(value) if key == "kmesh" else value)
                for key, value in row.items()
                if key != "ratio_index"
            }
            for row in plan
        ],
        "energy_matrix": {
            "expected_count": 42,
            "reused_dense_standard_count": 14,
            "new_dense_half_count": 14,
            "new_dense_quarter_count": 14,
            "points": _energy_matrix(plan),
        },
        "field_label_groups": {
            "three_width_endpoint_count": 18,
            "by_smearing": endpoint_levels,
            "extra_dense_quarter_count": 6,
            "extra_dense_quarter_ids": extra_ids,
        },
        "numerical_axes": {
            "volume_ratios": list(RATIOS),
            "smearing_sigma_ry": SMEARING,
            "common_dense_kmesh": {key: list(value) for key, value in COMMON_MESH.items()},
            "extra_dense_kmesh": {key: list(value) for key, value in EXTRA_MESH.items()},
        },
        "output_contract": {
            "out_chg": [1, CUBE_PRECISION],
            "out_pot": [1, CUBE_PRECISION],
            "density_basename": "chg.cube",
            "potential_basename": "pot.cube",
            "spin_count": 1,
            "potential_units": "Ry",
        },
        "thermodynamic_semantics": {
            "F": "E_KohnSham=!FINAL_ETOT_IS",
            "m": "E_entropy(-TS)<=0",
            "U": "F-m",
            "E_ec": "F-m/2; finite-smearing estimator only",
            "T_sU": "E_one_elec-E_localpp; nproj=0 local-BLPS scope",
            "F_s": "T_sU+m",
            "field_bundle": "{rho_sigma,F_s_sigma,g_sigma}",
            "g_sigma": "-(v_eff_eV-unweighted_cell_average_v_eff_eV)",
            "zero_temperature_exact_claim": False,
        },
        "acceptance": {
            "completion": {
                "accepted_registered_runs_exact": 40,
                "registered_runs_exact": 40,
                "converged_scf_runs_exact": 40,
                "complete_finite_thermodynamic_label_runs_exact": 40,
                "selected_scalar_points_exact": 42,
                "expected_scalar_points_exact": 42,
                "half_to_quarter_field_pairs_accepted_exact": 14,
                "half_to_quarter_field_pairs_expected_exact": 14,
                "quarter_k_field_pairs_accepted_exact": 6,
                "quarter_k_field_pairs_expected_exact": 6,
            },
            "required_thermodynamic_labels": [
                "F", "m", "U", "E_ec", "E_one_elec", "E_localpp",
                "T_sU", "F_s", "E_Hartree", "E_xc", "E_Ewald", "mu",
            ],
            "all_required_labels_must_be_finite": True,
            "entropy_minus_ts_must_be_nonpositive": True,
            "ordering_within_parser_precision": "F<=E_ec<=U",
            "thermodynamic_identity_residual_ev_per_atom_strictly_less_than": str(IDENTITY_RESIDUAL_EV_PER_ATOM_LIMIT),
            "electron_number_relative_error_strictly_less_than": ELECTRON_RELATIVE_ERROR_LIMIT,
            "eos_fits": {
                "fit_count_exact": 6,
                "points_per_fit_exact": 7,
                "unique_volume_ratios_required": list(RATIOS),
                "equilibrium_volume_strictly_inside_sampled_interval": True,
                "bulk_modulus_strictly_positive": True,
                "maximum_absolute_fit_residual_mev_per_atom_strictly_less_than": 1.0,
                "energy_observable": "E_ec_per_atom",
                "fit_model": "third_order_Birch_Murnaghan",
            },
            "adjacent_smearing": {
                "comparison_count_exact": 4,
                "comparisons": [
                    {"material": material, "coarse": coarse, "fine": fine}
                    for material in ("al", "mg")
                    for coarse, fine in (("standard", "half"), ("half", "quarter"))
                ],
                "raw_v100_anchored_curve_max_abs_difference_mev_per_atom_strictly_less_than": 2.0,
                "equilibrium_volume_relative_change_percent_strictly_less_than": 0.2,
                "bm3_smoothing_may_replace_raw_curve_gate": False,
            },
            "dense_standard_replay_equivalence": {
                "pair_count_exact": 6,
                "only_suffix_and_field_output_controls_may_differ": True,
                "absolute_E_ec_difference_mev_per_atom_strictly_less_than": 0.1,
                "absolute_F_difference_mev_per_atom_strictly_less_than": 0.1,
                "absolute_pressure_difference_gpa_strictly_less_than": 0.02,
            },
            "quarter_k_gate": {
                "P0_v100_material_pair_count_exact": 2,
                "P0_v100_absolute_E_ec_difference_mev_per_atom_strictly_less_than": 2.0,
                "P0_v100_all_four_field_metrics_required": True,
                "P1_material_curve_count_exact": 2,
                "P1_anchor_ratios": ["0.90", "1.00", "1.10"],
                "P1_v100_anchored_curve_max_abs_difference_mev_per_atom_strictly_less_than": 2.0,
                "P1_common_extra_field_pairs_accepted_exact": 6,
                "P1_common_extra_field_pairs_expected_exact": 6,
            },
            "density_d1_strictly_less_than": DENSITY_D1_LIMIT,
            "density_d2_strictly_less_than": DENSITY_D2_LIMIT,
            "derivative_dg_strictly_less_than": DERIVATIVE_DG_LIMIT,
            "derivative_absolute_rms_ev_strictly_less_than": DERIVATIVE_RMS_EV_LIMIT,
            "cube_geometry_absolute_tolerance_bohr": "0.00005",
            "low_density_mask": None,
            "density_d1_definition": "voxel_volume*sum(abs(rho_cmp-rho_ref))/N_ref",
            "density_d2_definition": "sqrt(sum(delta_rho^2)/sum(rho_ref^2))",
            "potential_comparison_order": {
                "half_to_quarter": "quarter_is_reference",
                "common_quarter_to_extra_dense": "extra_dense_is_reference",
            },
            "runtime_kmp_aggregate": {
                "accepted_runs_exact": 40,
                "expected_runs_exact": 40,
                "accepted_rank_lifecycles_exact": 160,
                "expected_rank_lifecycles_exact": 160,
                "successful_lifecycle_syscalls_exact": 480,
                "expected_successful_lifecycle_syscalls_exact": 480,
                "all_anomaly_counts_exact": 0,
                "zero_count_categories": [
                    "successful_old_prefix_access",
                    "successful_old_prefix_execution",
                    "old_prefix_mapped_objects",
                    "unknown_probes",
                    "unexpected_mapped_objects",
                    "unhashed_accepted_mappings",
                    "incomplete_lifecycles",
                    "duplicate_rank_evidence",
                    "ambiguous_exec_results",
                    "counterpart_missing",
                    "counterpart_byte_mismatch",
                ],
            },
            "required_empty_failure_id_lists": [
                "completion",
                "source_integrity",
                "input_hash",
                "scf",
                "thermodynamic_identity",
                "electron_number",
                "eos_fit",
                "adjacent_smearing_energy",
                "equilibrium_volume",
                "replay_equivalence",
                "density",
                "derivative",
                "k_gate",
                "runtime_kmp",
            ],
        },
        "formal_preregistration_commit_scope": {
            "include_exactly": [
                output_config_rel.as_posix(),
                output_manifest_rel.as_posix(),
                input_root_rel.as_posix(),
            ],
            "implementation_must_be_in_parent_commit": True,
            "run_or_analysis_evidence_allowed": False,
        },
        "manifest": {
            "path": output_manifest_rel.as_posix(),
            "sha256": sha256_bytes(encoded_manifest),
            "row_count": 40,
        },
        "input_root": input_root_rel.as_posix(),
        "upstream_evidence": upstream,
        "source_runs": {
            experiment_id: {
                key: value for key, value in source.items()
                if key not in {"metadata", "result", "expected_electron_derivation"}
            }
            for experiment_id, source in sources.items()
        },
        "source_semantics": source_semantics,
        "runtime_source": upstream[R2_RUNTIME_CONFIG.as_posix()],
        "runtime": deepcopy(r2_config["runtime"]),
        "runtime_audit": deepcopy(r2_config["runtime_audit"]),
        "kmp_contract": deepcopy(r2_config["kmp_contract"]),
        "rank_count": r2_config["rank_count"],
        "implementation": implementation,
        "implementation_git_modes": implementation_modes,
    }
    encoded_config = canonical_json_bytes(config)
    result: dict[str, object] = {
        "config": config,
        "config_bytes": encoded_config,
        "manifest_rows": rows,
        "manifest_bytes": encoded_manifest,
        "artifacts": artifacts,
        "output_config": output_config_rel.as_posix(),
        "output_manifest": output_manifest_rel.as_posix(),
        "input_root": input_root_rel.as_posix(),
    }
    if write:
        _write(project_root, result)
    return result


def _write(project_root: Path, prepared: dict[str, object]) -> None:
    config_path = project_root / str(prepared["output_config"])
    manifest_path = project_root / str(prepared["output_manifest"])
    input_root = project_root / str(prepared["input_root"])
    config_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    input_root.mkdir(parents=True, exist_ok=False)
    artifacts = prepared["artifacts"]
    require(isinstance(artifacts, dict), "internal artifact map differs")
    for relative, data in sorted(artifacts.items()):
        path = project_root / str(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        require(isinstance(data, bytes), "internal artifact is not bytes")
        with path.open("xb") as handle:
            handle.write(data)
    with manifest_path.open("xb") as handle:
        handle.write(prepared["manifest_bytes"])
    with config_path.open("xb") as handle:
        handle.write(prepared["config_bytes"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--output-manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--input-root", type=Path, default=INPUT_ROOT)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--source-archive", type=Path, default=DEFAULT_SOURCE_ARCHIVE)
    parser.add_argument("--write", action="store_true")
    arguments = parser.parse_args()
    prepared = prepare(
        arguments.project_root,
        output_config=arguments.output_config,
        output_manifest=arguments.output_manifest,
        input_root=arguments.input_root,
        source_root=arguments.source_root,
        source_archive=arguments.source_archive,
        write=arguments.write,
    )
    summary = {
        "status": "written" if arguments.write else "dry_run_validated",
        "config_path": prepared["output_config"],
        "config_sha256": sha256_bytes(prepared["config_bytes"]),
        "manifest_path": prepared["output_manifest"],
        "manifest_sha256": sha256_bytes(prepared["manifest_bytes"]),
        "run_count": len(prepared["manifest_rows"]),
        "input_file_count": len(prepared["artifacts"]),
    }
    print(canonical_json_bytes(summary).decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
