#!/usr/bin/env python3
"""Validate the preregistered S1-G1 thermodynamic-label audit R1.

The validator is deliberately usable at three different moments in the
workflow: before any calculation, immediately after one run, and after the
complete 40-run matrix has been committed.  Registration, run evidence, phase
barriers, and final acceptance are therefore separate checks rather than one
best-effort pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import validate_s1_mpi_prefix_equivalence as runtime_validation
from analyze_s1_eos import fit_bm3
from parse_s1_g1_thermodynamic_labels import parse_run as parse_label_run
from s1_electron_number_common import (
    expected_electrons,
    find_single_log,
    integrate_cube,
    parse_charge_grid,
    parse_stru,
    read_json,
    scientific_equivalence,
    sha256,
)
from s1_g1_kmp_runtime_contract import validate_kmp_runtime_contract
from s1_g1_thermodynamic_label_common import (
    AUDIT_IDS,
    DENSITY_D1_LIMIT,
    DENSITY_D2_LIMIT,
    DERIVATIVE_DG_LIMIT,
    DERIVATIVE_RMS_EV_LIMIT,
    ELECTRON_RELATIVE_ERROR_LIMIT,
    EXECUTION_ORDER as COMMON_EXECUTION_ORDER,
    K_GATE_COMPLETION_IDS,
    K_GATE_EXECUTION_IDS,
    MANIFEST_FIELDS,
    IDENTITY_RESIDUAL_EV_PER_ATOM_LIMIT,
    PILOT_IDS as COMMON_PILOT_IDS,
    compare_density_fields,
    compare_potential_derivative_fields,
    json_safe,
    parse_abacus_cube,
    parse_kpt_text,
    parse_thermodynamic_log,
    read_manifest as read_label_manifest,
    validate_derived_input,
)


PROTOCOL_REVISION = "S1-G1-THERMODYNAMIC-LABEL-AUDIT-R1"
CONFIG_PATH = Path("config/S1_g1_thermodynamic_label_audit_r1.json")
MANIFEST_PATH = Path("config/S1_g1_thermodynamic_label_audit_r1_manifest.tsv")
PROTOCOL_PATH = Path("docs/S1_G1_THERMODYNAMIC_LABEL_AUDIT_R1_PROTOCOL.md")
PARSER_PATH = Path("scripts/parse_s1_g1_thermodynamic_labels.py")
LABEL_NAME = "thermodynamic_labels.json"
EVIDENCE_NAME = "g1_thermodynamic_label_audit_r1.json"
STATUS_NAME = "thermodynamic_label_status.json"
FAILURE_CLASS_NAME = "thermodynamic_label_failure_classification.json"
FAILURE_INVENTORY_NAME = "thermodynamic_label_failure_artifact_inventory.json"

RUN_IDS = AUDIT_IDS
PILOT_IDS = COMMON_PILOT_IDS
P1_REMAINDER_IDS = K_GATE_COMPLETION_IDS
K_GATE_IDS = K_GATE_EXECUTION_IDS
P2_IDS = tuple(experiment_id for experiment_id in RUN_IDS if experiment_id not in K_GATE_IDS)
EXECUTION_ORDER = COMMON_EXECUTION_ORDER

STANDARD_REPLAY_IDS = tuple(f"S1-20260806-{value:03d}" for value in range(1, 7))
HALF_IDS = tuple(f"S1-20260806-{value:03d}" for value in range(7, 21))
COMMON_QUARTER_IDS = tuple(f"S1-20260806-{value:03d}" for value in range(21, 35))
EXTRA_QUARTER_IDS = tuple(f"S1-20260806-{value:03d}" for value in range(35, 41))
ANCHOR_RATIOS = (0.9, 1.0, 1.1)
EOS_RATIOS = (0.9, 0.94, 0.97, 1.0, 1.03, 1.06, 1.1)
BOHR_TO_ANGSTROM = 0.529177210903
CUBE_GEOMETRY_ABSOLUTE_TOLERANCE_BOHR = 5.0e-5

EXPECTED_SOURCE_IDS = {
    **{
        f"S1-20260806-{new:03d}": f"S1-20260805-{old:03d}"
        for new, old in zip(range(1, 4), (85, 88, 91))
    },
    **{
        f"S1-20260806-{new:03d}": f"S1-20260805-{old:03d}"
        for new, old in zip(range(4, 7), (106, 109, 112))
    },
}

SOURCE_ROOT = Path(
    "/home/shenwei01/wt_melting_restore_20260724/integrated/abacus_source"
)
SOURCE_ARCHIVE = Path("/home/shenwei01/abacus_wt_build_source_20260724.tar.gz")
SOURCE_ARCHIVE_SHA256 = "7c8522d4085cbac6c9bc454155873b3f21973d2e709fe9dba4c6fd25f7c885a3"
SOURCE_SEMANTIC_CONTRACT = (
    (
        "source/source_io/module_parameter/read_input_item_output.cpp",
        "cd23b6e31258f0e0d0a0079e4669169627b5a3510c9b80f024ddc3737a10295a",
        (
            'Input_Item item("out_pot")',
            "on real space grids (in Ry)",
            "item.read_value = [](const Input_Item& item, Parameter& para)",
            "para.input.out_pot[1] = std::stoi(item.str_values[1])",
        ),
    ),
    (
        "source/source_io/module_ctrl/ctrl_output_fp.cpp",
        "fa5e8f5b109aba4fdba54bdac5b12c283ea4dfd3455647e7f23e657f212e5de8",
        (
            "PARAM.inp.out_pot[0] == 1",
            'PARAM.globalv.global_out_dir + "pot"',
            "pelec->pot->get_eff_v(is)",
            "PARAM.inp.out_pot[1]",
        ),
    ),
    (
        "source/source_estate/elecstate_print.cpp",
        "0b24914596b30f6236129efc61eff1d9dd7821ddc985680371a5b2b746421046",
        (
            'titles.push_back("E_KohnSham")',
            'titles.push_back("E_KS(sigma->0)")',
            "elec.f_en.etot - elec.f_en.demet / (2 + n_order)",
            'titles.push_back("E_entropy(-TS)")',
            'titles.push_back("E_localpp")',
            'titles.push_back("E_Fermi")',
        ),
    ),
    (
        "source/source_estate/elecstate_energy.cpp",
        "64f1600934aedf2cb4f9896b4e94aa59d0d5d224f9567198d250f9b57630af40",
        (
            "this->f_en.e_local_pp = get_local_pp_energy();",
            "this->pot->get_fixed_v()",
            "this->pot->get_eff_v(0)",
        ),
    ),
    (
        "source/source_estate/elecstate_energy_terms.cpp",
        "15c4de5cc9b4b88c57dac12d3c83a570442e07c6b8a5940d1820cb41996651a3",
        (
            "double ElecState::get_local_pp_energy()",
            "this->pot->get_fixed_v()",
            "this->charge->rho[is]",
        ),
    ),
    (
        "source/source_estate/fp_energy.cpp",
        "2fbf4a9a2d3b047b81fe39fedf3a3ff53a9b1a77b8b1be211d5be63f060d6b44",
        (
            "double fenergy::calculate_etot()",
            "etot = eband + deband",
            "+ hartree_energy + demet + descf",
        ),
    ),
    (
        "source/source_estate/module_pot/potential_new.cpp",
        "d6d8c1a1d4d9d81ef32152318c21d72fd57fb1b71ae27829f9533240f8e87b3e",
        (
            "void Potential::cal_fixed_v(double* vl_pseudo)",
            "this->v_eff_fixed.data()",
            "this->get_eff_v(i)",
            "components[i]->cal_v_eff(chg, ucell, v_eff)",
        ),
    ),
)

_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_EXPERIMENT = re.compile(r"S1-[0-9]{8}-[0-9]{3}\Z")


def _git(project_root: Path, *args: str, text: bool = True) -> str | bytes:
    output = subprocess.check_output(
        ["git", "-C", str(project_root), *args], text=text
    )
    return output.strip() if text else output


def _relative(project_root: Path, path: Path) -> str:
    return str(path.resolve().relative_to(project_root.resolve()))


def _is_ancestor(project_root: Path, ancestor: str, descendant: str) -> bool:
    return (
        subprocess.run(
            [
                "git",
                "-C",
                str(project_root),
                "merge-base",
                "--is-ancestor",
                ancestor,
                descendant,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )


def _introduction_commits(project_root: Path, relative: str) -> list[str]:
    output = str(
        _git(
            project_root,
            "log",
            "--no-renames",
            "--format=%H",
            "--diff-filter=A",
            "--",
            relative,
        )
    )
    return output.splitlines() if output else []


def _introduction_commit(project_root: Path, relative: str) -> str:
    commits = _introduction_commits(project_root, relative)
    if len(commits) != 1:
        raise ValueError(f"expected one introduction commit for {relative}")
    return commits[0]


def _latest_introduction_commit(project_root: Path, relative: str) -> str:
    commits = _introduction_commits(project_root, relative)
    if not commits:
        raise ValueError(f"expected an introduction commit for {relative}")
    return commits[0]


def _commit_changed_paths(project_root: Path, commit: str) -> set[str]:
    raw = subprocess.check_output(
        [
            "git",
            "-C",
            str(project_root),
            "diff-tree",
            "--no-commit-id",
            "--no-renames",
            "--name-only",
            "-r",
            "-z",
            commit,
        ]
    )
    return {item.decode("utf-8") for item in raw.split(b"\0") if item}


def _blob_at(project_root: Path, revision: str, relative: str) -> bytes:
    return _git(
        project_root, "cat-file", "blob", f"{revision}:{relative}", text=False
    )


def _tree_oid(project_root: Path, revision: str, relative: str) -> str:
    value = str(_git(project_root, "rev-parse", f"{revision}:{relative}"))
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError(f"invalid Git object for {revision}:{relative}")
    return value


def _tracked_head_failure(project_root: Path, relative: str) -> str | None:
    path = project_root / relative
    if not path.is_file() or path.is_symlink():
        return f"not a regular non-symbolic file: {relative}"
    if subprocess.run(
        ["git", "-C", str(project_root), "ls-files", "--error-unmatch", "--", relative],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode:
        return f"not tracked: {relative}"
    if subprocess.run(
        ["git", "-C", str(project_root), "diff", "--quiet", "HEAD", "--", relative]
    ).returncode:
        return f"differs from HEAD: {relative}"
    return None


def _safe_project_path(project_root: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ValueError(f"{label} must be a non-empty project-relative path")
    root = project_root.resolve()
    path = (root / value).resolve(strict=False)
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{label} resolves outside the project: {value}") from error
    current = root
    for component in Path(value).parts:
        if component in {"", "."}:
            continue
        current = current / component
        if current.is_symlink():
            raise ValueError(f"{label} traverses a symbolic link: {value}")
    return path


def _read_manifest(path: Path) -> list[dict[str, str]]:
    return read_label_manifest(path)


def _parse_float(value: object, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} is not numeric") from error
    if not math.isfinite(number):
        raise ValueError(f"{label} is not finite")
    return number


def _parse_kmesh(value: object) -> tuple[int, int, int]:
    if isinstance(value, str):
        fields = re.split(r"[xX, ]+", value.strip())
        fields = [field for field in fields if field]
    elif isinstance(value, list):
        fields = value
    else:
        raise ValueError("kmesh must be a string or list")
    if len(fields) != 3:
        raise ValueError("kmesh must contain three values")
    try:
        mesh = tuple(int(field) for field in fields)
    except (TypeError, ValueError) as error:
        raise ValueError("kmesh contains a non-integer") from error
    if any(value <= 0 for value in mesh):
        raise ValueError("kmesh values must be positive")
    return mesh  # type: ignore[return-value]


def _input_tokens(path: Path) -> dict[str, tuple[str, ...]]:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"INPUT is not a regular file: {path}")
    rows: dict[str, tuple[str, ...]] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line == "INPUT_PARAMETERS":
            continue
        fields = line.split()
        key = fields[0].lower()
        if key in rows:
            raise ValueError(f"duplicate INPUT keyword {key}: {path}")
        rows[key] = tuple(fields[1:])
    return rows


def _kpt_mesh(path: Path) -> tuple[int, int, int]:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"KPT is not a regular file: {path}")
    return parse_kpt_text(path.read_bytes())


def _role(row: dict[str, str]) -> str:
    text = f"{row.get('run_role', '')} {row.get('smearing_level', '')}".lower()
    if "standard" in text and "replay" in text:
        return "standard_replay"
    if "extra" in text and "quarter" in text:
        return "extra_quarter"
    if "quarter" in text:
        return "common_quarter"
    if "half" in text:
        return "half"
    raise ValueError(f"cannot classify run role for {row.get('experiment_id')}")


def _expected_role(experiment_id: str) -> str:
    if experiment_id in STANDARD_REPLAY_IDS:
        return "standard_replay"
    if experiment_id in HALF_IDS:
        return "half"
    if experiment_id in COMMON_QUARTER_IDS:
        return "common_quarter"
    if experiment_id in EXTRA_QUARTER_IDS:
        return "extra_quarter"
    raise ValueError(f"experiment is outside R1: {experiment_id}")


def _expected_material_ratio(experiment_id: str) -> tuple[str, float]:
    number = int(experiment_id.rsplit("-", 1)[1])
    if 1 <= number <= 3:
        return "al", ANCHOR_RATIOS[number - 1]
    if 4 <= number <= 6:
        return "mg", ANCHOR_RATIOS[number - 4]
    if 7 <= number <= 13:
        return "al", EOS_RATIOS[number - 7]
    if 14 <= number <= 20:
        return "mg", EOS_RATIOS[number - 14]
    if 21 <= number <= 27:
        return "al", EOS_RATIOS[number - 21]
    if 28 <= number <= 34:
        return "mg", EOS_RATIOS[number - 28]
    if 35 <= number <= 37:
        return "al", ANCHOR_RATIOS[number - 35]
    if 38 <= number <= 40:
        return "mg", ANCHOR_RATIOS[number - 38]
    raise ValueError(f"experiment is outside R1: {experiment_id}")


def _expected_mesh(material: str, role: str) -> tuple[int, int, int]:
    if material == "al":
        return (32, 32, 32) if role == "extra_quarter" else (28, 28, 28)
    if material == "mg":
        return (28, 28, 18) if role == "extra_quarter" else (24, 24, 16)
    raise ValueError(f"unsupported material {material}")


def _expected_sigma(role: str) -> float:
    return {
        "standard_replay": 0.00734986,
        "half": 0.00367493,
        "common_quarter": 0.001837465,
        "extra_quarter": 0.001837465,
    }[role]


def _expected_common_partner(experiment_id: str) -> str:
    number = int(experiment_id.rsplit("-", 1)[1])
    if 35 <= number <= 37:
        partner = (21, 24, 27)[number - 35]
    elif 38 <= number <= 40:
        partner = (28, 31, 34)[number - 38]
    else:
        return ""
    return f"S1-20260806-{partner:03d}"


def _validate_input_derivation(
    project_root: Path, row: dict[str, str], errors: list[str]
) -> None:
    experiment_id = row["experiment_id"]
    prefix = f"{experiment_id}:"
    try:
        input_directory = _safe_project_path(
            project_root, row["input_directory"], f"{prefix} input_directory"
        )
        source_directory = _safe_project_path(
            project_root, row["source_run_path"], f"{prefix} source_run_path"
        )
        if source_directory != project_root / "runs" / row["source_experiment_id"]:
            errors.append(f"{prefix} source run path is not canonical")
        registered = {
            "INPUT": row["input_sha256"],
            "STRU": row["stru_sha256"],
            "KPT": row["kpt_sha256"],
            "metadata.json": row["metadata_sha256"],
        }
        for name, digest in registered.items():
            path = input_directory / name
            if not path.is_file() or path.is_symlink():
                errors.append(f"{prefix} missing or symbolic derived {name}")
            elif not _HEX64.fullmatch(digest) or sha256(path) != digest:
                errors.append(f"{prefix} derived {name} SHA-256 mismatch")
        source_registered = {
            "INPUT": row["source_input_sha256"],
            "STRU": row["source_stru_sha256"],
            "KPT": row["source_kpt_sha256"],
            "input_metadata.json": row["source_metadata_sha256"],
        }
        for name, digest in source_registered.items():
            path = source_directory / name
            if not path.is_file() or path.is_symlink():
                errors.append(f"{prefix} missing or symbolic source {name}")
            elif not _HEX64.fullmatch(digest) or sha256(path) != digest:
                errors.append(f"{prefix} source {name} SHA-256 mismatch")
        if (input_directory / "STRU").read_bytes() != (
            source_directory / "STRU"
        ).read_bytes():
            errors.append(f"{prefix} STRU differs from registered source")

        validate_derived_input(
            (source_directory / "INPUT").read_bytes(),
            (input_directory / "INPUT").read_bytes(),
            suffix=row["suffix"],
            smearing_sigma_ry=row["smearing_sigma_ry"],
            cube_precision=int(row["cube_precision"]),
        )
        if _kpt_mesh(input_directory / "KPT") != _parse_kmesh(row["kmesh"]):
            errors.append(f"{prefix} KPT mesh differs from manifest")
        source_mesh = _kpt_mesh(source_directory / "KPT")
        derived_mesh = _kpt_mesh(input_directory / "KPT")
        if source_mesh != derived_mesh and _role(row) != "extra_quarter":
            errors.append(f"{prefix} KPT differs outside the registered extra-dense axis")
        if source_mesh == derived_mesh and _role(row) == "extra_quarter":
            errors.append(f"{prefix} extra-dense KPT did not change")

        metadata = read_json(input_directory / "metadata.json")
        expected_metadata = {
            "protocol_revision": PROTOCOL_REVISION,
            "experiment_id": row["experiment_id"],
            "material": row["material"],
            "volume_ratio": row["volume_ratio"],
            "smearing_level": row["smearing_level"],
            "smearing_sigma_ry": row["smearing_sigma_ry"],
            "run_role": row["run_role"],
            "source_experiment_id": row["source_experiment_id"],
            "reference_experiment_id": row["reference_experiment_id"],
            "common_quarter_partner_id": row["common_quarter_partner_id"],
            "pseudopotential": row["pseudopotential"],
            "expected_electrons": row["expected_electrons"],
            "atom_count": row["atom_count"],
            "cube_precision": row["cube_precision"],
            "density_basename": "chg.cube",
            "potential_basename": "pot.cube",
        }
        for key, expected in expected_metadata.items():
            if str(metadata.get(key, "")) != expected:
                errors.append(f"{prefix} derived metadata {key} mismatch")
        if metadata.get("kmesh") != list(_parse_kmesh(row["kmesh"])):
            errors.append(f"{prefix} derived metadata kmesh mismatch")

        pseudo = project_root / "assets" / "pseudo" / row["pseudopotential"]
        if (
            Path(row["pseudopotential"]).name != row["pseudopotential"]
            or not pseudo.is_file()
            or pseudo.is_symlink()
            or not _HEX64.fullmatch(row["pseudopotential_sha256"])
            or sha256(pseudo) != row["pseudopotential_sha256"]
        ):
            errors.append(f"{prefix} pseudopotential identity mismatch")
        for path_key, hash_key, label in (
            ("source_result_path", "source_result_sha256", "source result"),
            ("source_log_path", "source_log_sha256", "source log"),
        ):
            source_path = _safe_project_path(
                project_root, row[path_key], f"{prefix} {label}"
            )
            if (
                not source_path.is_file()
                or source_path.is_symlink()
                or not _HEX64.fullmatch(row[hash_key])
                or sha256(source_path) != row[hash_key]
            ):
                errors.append(f"{prefix} {label} identity mismatch")
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        errors.append(f"{prefix} input derivation validation failed: {error}")


def _validate_manifest_rows(
    project_root: Path, rows: list[dict[str, str]], config: dict, errors: list[str]
) -> None:
    ids = tuple(row.get("experiment_id", "") for row in rows)
    if ids != RUN_IDS:
        errors.append("manifest rows must be the exact 40 numeric R1 IDs in order")
    by_id = {row.get("experiment_id", ""): row for row in rows}
    for experiment_id in RUN_IDS:
        row = by_id.get(experiment_id)
        if row is None:
            continue
        prefix = f"{experiment_id}:"
        try:
            role = _role(row)
            expected_role = _expected_role(experiment_id)
            material, ratio = _expected_material_ratio(experiment_id)
            if role != expected_role:
                errors.append(f"{prefix} role differs from frozen mapping")
            if row["material"] != material:
                errors.append(f"{prefix} material differs from frozen mapping")
            if _parse_float(row["volume_ratio"], "volume ratio") != ratio:
                errors.append(f"{prefix} volume ratio differs from frozen mapping")
            if _parse_kmesh(row["kmesh"]) != _expected_mesh(material, role):
                errors.append(f"{prefix} k mesh differs from frozen mapping")
            if _parse_float(row["smearing_sigma_ry"], "sigma") != _expected_sigma(role):
                errors.append(f"{prefix} sigma differs from frozen mapping")
            if row["common_quarter_partner_id"] != _expected_common_partner(experiment_id):
                errors.append(f"{prefix} common-dense partner differs from frozen mapping")
            if role == "standard_replay" and row["source_experiment_id"] != EXPECTED_SOURCE_IDS[experiment_id]:
                errors.append(f"{prefix} standard source differs from frozen mapping")
            if not _EXPERIMENT.fullmatch(row["source_experiment_id"]):
                errors.append(f"{prefix} source experiment ID is invalid")
            expected_dense_number = (
                85 + EOS_RATIOS.index(ratio)
                if material == "al"
                else 106 + EOS_RATIOS.index(ratio)
            )
            expected_dense_id = f"S1-20260805-{expected_dense_number:03d}"
            if row["dense_standard_scalar_source_id"] != expected_dense_id:
                errors.append(f"{prefix} dense-standard scalar source differs")
            if row["source_experiment_id"] != expected_dense_id:
                errors.append(f"{prefix} immutable source differs from dense-standard source")
            if role in {"standard_replay", "half"}:
                expected_reference = expected_dense_id
            elif role == "common_quarter":
                expected_reference = (
                    f"S1-20260806-{int(experiment_id.rsplit('-', 1)[1]) - 14:03d}"
                )
            else:
                expected_reference = _expected_common_partner(experiment_id)
            if row["reference_experiment_id"] != expected_reference:
                errors.append(f"{prefix} scientific reference partner differs")
            expected_phase = (
                "P0" if experiment_id in PILOT_IDS else
                "P1" if experiment_id in P1_REMAINDER_IDS else "P2"
            )
            if row["execution_phase"] != expected_phase:
                errors.append(f"{prefix} execution phase differs")
            expected_index = EXECUTION_ORDER.index(experiment_id) + 1
            if row["execution_index"] != str(expected_index):
                errors.append(f"{prefix} execution index differs")
            expected_electrons_value, expected_atom_count = (
                ("3", "1") if material == "al" else ("4", "2")
            )
            if (
                row["expected_electrons"] != expected_electrons_value
                or row["atom_count"] != expected_atom_count
                or row["cube_precision"] != "17"
                or row["density_basename"] != "chg.cube"
                or row["potential_basename"] != "pot.cube"
            ):
                errors.append(f"{prefix} output/electron registration differs")
            source_result = _safe_project_path(
                project_root, row["source_result_path"], f"{prefix} source result"
            )
            source_log = _safe_project_path(
                project_root, row["source_log_path"], f"{prefix} source log"
            )
            source_run = project_root / "runs" / row["source_experiment_id"]
            if source_result != source_run / "result.json":
                errors.append(f"{prefix} source result is not canonical")
            if source_log.parent.parent != source_run or source_log.name != "running_scf.log":
                errors.append(f"{prefix} source log is not canonical")
            for label, path in (("source result", source_result), ("source log", source_log)):
                if not path.is_file() or path.is_symlink():
                    errors.append(f"{prefix} missing or symbolic {label}")
        except (KeyError, TypeError, ValueError) as error:
            errors.append(f"{prefix} manifest mapping validation failed: {error}")
        _validate_input_derivation(project_root, row, errors)

    if config.get("registered_experiment_ids") != list(RUN_IDS):
        errors.append("config registered experiment IDs differ from exact R1 denominator")
    if config.get("execution_order") != list(EXECUTION_ORDER):
        errors.append("config execution order differs from P0/P1/P2")


def _validate_config_contract(
    project_root: Path,
    config: dict,
    rows: list[dict[str, str]],
    errors: list[str],
) -> None:
    """Independently pin every decision-bearing preregistration subtree."""

    expected_execution = {
        "rank_count": 4,
        "pilot_ids": list(PILOT_IDS),
        "k_gate_completion_ids": list(P1_REMAINDER_IDS),
        "k_gate_execution_prefix": list(K_GATE_IDS),
        "phase_barriers_fail_closed": True,
        "stop_after_first_preserved_failure": True,
        "absolute_deadline_watchdog_seconds": 7200,
    }
    expected_axes = {
        "volume_ratios": ["0.90", "0.94", "0.97", "1.00", "1.03", "1.06", "1.10"],
        "smearing_sigma_ry": {
            "standard": "0.00734986",
            "half": "0.00367493",
            "quarter": "0.001837465",
        },
        "common_dense_kmesh": {"al": [28, 28, 28], "mg": [24, 24, 16]},
        "extra_dense_kmesh": {"al": [32, 32, 32], "mg": [28, 28, 18]},
    }
    expected_output = {
        "out_chg": [1, 17],
        "out_pot": [1, 17],
        "density_basename": "chg.cube",
        "potential_basename": "pot.cube",
        "spin_count": 1,
        "potential_units": "Ry",
    }
    expected_semantics = {
        "F": "E_KohnSham=!FINAL_ETOT_IS",
        "m": "E_entropy(-TS)<=0",
        "U": "F-m",
        "E_ec": "F-m/2; finite-smearing estimator only",
        "T_sU": "E_one_elec-E_localpp; nproj=0 local-BLPS scope",
        "F_s": "T_sU+m",
        "field_bundle": "{rho_sigma,F_s_sigma,g_sigma}",
        "g_sigma": "-(v_eff_eV-unweighted_cell_average_v_eff_eV)",
        "zero_temperature_exact_claim": False,
    }
    expected_acceptance = {
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
            "F", "m", "U", "E_ec", "E_one_elec", "E_localpp", "T_sU",
            "F_s", "E_Hartree", "E_xc", "E_Ewald", "mu",
        ],
        "all_required_labels_must_be_finite": True,
        "entropy_minus_ts_must_be_nonpositive": True,
        "ordering_within_parser_precision": "F<=E_ec<=U",
        "thermodynamic_identity_residual_ev_per_atom_strictly_less_than": str(
            IDENTITY_RESIDUAL_EV_PER_ATOM_LIMIT
        ),
        "electron_number_relative_error_strictly_less_than": ELECTRON_RELATIVE_ERROR_LIMIT,
        "eos_fits": {
            "fit_count_exact": 6,
            "points_per_fit_exact": 7,
            "unique_volume_ratios_required": expected_axes["volume_ratios"],
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
        "required_empty_failure_id_lists": list(
            (
                "completion", "source_integrity", "input_hash", "scf",
                "thermodynamic_identity", "electron_number", "eos_fit",
                "adjacent_smearing_energy", "equilibrium_volume",
                "replay_equivalence", "density", "derivative", "k_gate",
                "runtime_kmp",
            )
        ),
    }
    expected_contract = {
        "schema_version": 1,
        "preregistration_date": "2026-08-06",
        "base_evidence_commit": "a28082cfc49d9a76c8eaaa406786029ebe791ba6",
        "scope": "G1 third-smearing / dense-k thermodynamic-label audit only",
        "input_root": "inputs/s1/g1_thermodynamic_label_audit_r1",
        "rank_count": 4,
        "execution": expected_execution,
        "numerical_axes": expected_axes,
        "output_contract": expected_output,
        "thermodynamic_semantics": expected_semantics,
        "acceptance": expected_acceptance,
        "formal_preregistration_commit_scope": {
            "include_exactly": [
                str(CONFIG_PATH),
                str(MANIFEST_PATH),
                "inputs/s1/g1_thermodynamic_label_audit_r1",
            ],
            "implementation_must_be_in_parent_commit": True,
            "run_or_analysis_evidence_allowed": False,
        },
    }
    for key, expected in expected_contract.items():
        if config.get(key) != expected:
            errors.append(f"config frozen contract differs: {key}")

    run_matrix = config.get("run_matrix")
    if not isinstance(run_matrix, list) or len(run_matrix) != 40:
        errors.append("config run matrix differs")
    else:
        matrix_by_id = {
            str(item.get("experiment_id", "")): item
            for item in run_matrix
            if isinstance(item, dict)
        }
        for row in rows:
            item = matrix_by_id.get(row["experiment_id"])
            expected = {
                "experiment_id": row["experiment_id"],
                "material": row["material"],
                "volume_ratio": row["volume_ratio"],
                "volume_token": f"v{round(float(row['volume_ratio']) * 100):03d}",
                "smearing_level": row["smearing_level"],
                "smearing_sigma_ry": row["smearing_sigma_ry"],
                "kmesh": list(_parse_kmesh(row["kmesh"])),
                "run_role": row["run_role"],
                "source_experiment_id": row["source_experiment_id"],
                "dense_standard_scalar_source_id": row["dense_standard_scalar_source_id"],
                "suffix": row["suffix"],
                "execution_index": int(row["execution_index"]),
                "execution_phase": row["execution_phase"],
                "reference_experiment_id": row["reference_experiment_id"],
                "common_quarter_partner_id": row["common_quarter_partner_id"],
            }
            if item != expected:
                errors.append(f"config/manifest run-matrix mismatch: {row['experiment_id']}")

    expected_energy_points: list[dict[str, object]] = []
    for material, source_first, half_first, quarter_first in (
        ("al", 85, 7, 21),
        ("mg", 106, 14, 28),
    ):
        for index, ratio in enumerate(expected_axes["volume_ratios"]):
            expected_energy_points.append(
                {
                    "material": material,
                    "volume_ratio": ratio,
                    "smearing_level": "standard",
                    "source_kind": "immutable_reused_R8_scalar",
                    "experiment_id": f"S1-20260805-{source_first + index:03d}",
                }
            )
        for level, first in (("half", half_first), ("quarter", quarter_first)):
            for index, ratio in enumerate(expected_axes["volume_ratios"]):
                expected_energy_points.append(
                    {
                        "material": material,
                        "volume_ratio": ratio,
                        "smearing_level": level,
                        "source_kind": "new_registered_run",
                        "experiment_id": f"S1-20260806-{first + index:03d}",
                    }
                )
    expected_energy_matrix = {
        "expected_count": 42,
        "reused_dense_standard_count": 14,
        "new_dense_half_count": 14,
        "new_dense_quarter_count": 14,
        "points": expected_energy_points,
    }
    if config.get("energy_matrix") != expected_energy_matrix:
        errors.append("config 42-point energy matrix differs")

    endpoint_ids = {
        "standard": [*STANDARD_REPLAY_IDS],
        "half": [
            "S1-20260806-007", "S1-20260806-010", "S1-20260806-013",
            "S1-20260806-014", "S1-20260806-017", "S1-20260806-020",
        ],
        "quarter": [
            "S1-20260806-021", "S1-20260806-024", "S1-20260806-027",
            "S1-20260806-028", "S1-20260806-031", "S1-20260806-034",
        ],
    }
    expected_field_groups = {
        "three_width_endpoint_count": 18,
        "by_smearing": endpoint_ids,
        "extra_dense_quarter_count": 6,
        "extra_dense_quarter_ids": list(EXTRA_QUARTER_IDS),
    }
    if config.get("field_label_groups") != expected_field_groups:
        errors.append("config field-label groups differ")

    r2_path = project_root / "config/S1_electron_number_audit_r2.json"
    try:
        if sha256(r2_path) != "bfed0e87451ccf60ff8239b5ad25d6a4e62e52e037d560ffa3dcdb4d08a83624":
            errors.append("accepted R2 runtime config SHA-256 differs")
        r2 = read_json(r2_path)
        for key in ("runtime", "runtime_audit", "kmp_contract", "rank_count"):
            if config.get(key) != r2.get(key):
                errors.append(f"R1 runtime closure differs from accepted R2: {key}")
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as error:
        errors.append(f"accepted R2 runtime closure failed: {error}")

    expected_upstream_paths = {
        "config/S1_baseline_protocol.json",
        "config/S1_non_equilibrium_convergence.json",
        "config/S1_non_equilibrium_run_manifest.tsv",
        "analysis/s1/core_eos_20260805/summary.json",
        "analysis/s1/non_equilibrium_convergence_20260805/summary.json",
        "config/S1_electron_number_audit_r2.json",
    }
    upstream = config.get("upstream_evidence")
    if not isinstance(upstream, dict) or set(upstream) != expected_upstream_paths:
        errors.append("upstream evidence closure differs")
    else:
        base = str(config["base_evidence_commit"])
        for relative, registration in upstream.items():
            try:
                if not isinstance(registration, dict) or registration.get("path") != relative:
                    raise ValueError("registration object/path differs")
                raw = str(_git(project_root, "ls-tree", base, "--", relative))
                metadata, observed = raw.split("\t", 1)
                mode, object_type, oid = metadata.split()
                if observed != relative or object_type != "blob":
                    raise ValueError("base object is not the expected blob")
                path = project_root / relative
                if (
                    registration.get("git_mode") != mode
                    or registration.get("blob_oid") != oid
                    or registration.get("sha256") != sha256(path)
                    or _blob_at(project_root, base, relative) != path.read_bytes()
                ):
                    raise ValueError("upstream blob/hash/mode differs")
            except (FileNotFoundError, KeyError, TypeError, ValueError, subprocess.CalledProcessError) as error:
                errors.append(f"upstream evidence failed {relative}: {error}")
    if config.get("runtime_source") != (
        upstream.get("config/S1_electron_number_audit_r2.json")
        if isinstance(upstream, dict)
        else None
    ):
        errors.append("runtime_source does not bind the accepted R2 config")

    source_semantics = config.get("source_semantics")
    expected_conclusions = {
        "potential_basename_nspin1": "pot.cube",
        "potential_quantity": "final local effective KS potential get_eff_v(0)",
        "potential_units": "Ry",
        "potential_precision_argument": "out_pot[1]",
        "entropy_corrected_estimator": "E_KS(sigma->0)=etot-demet/(2+n_order)",
        "localpp": "integral rho(r)*v_fixed(r) for local pseudopotential",
        "kinetic_identity_scope": "nproj=0 current local-BLPS setting only",
    }
    if not isinstance(source_semantics, dict) or source_semantics.get(
        "semantic_conclusions"
    ) != expected_conclusions:
        errors.append("source-semantics conclusions differ")
    else:
        source_files = source_semantics.get("files")
        archive = source_semantics.get("source_archive")
        expected_source_files = [
            {
                "path": str(SOURCE_ROOT / relative),
                "sha256": expected_hash,
                "required_markers": list(markers),
                "validated": True,
            }
            for relative, expected_hash, markers in SOURCE_SEMANTIC_CONTRACT
        ]
        if (
            source_semantics.get("source_root") != str(SOURCE_ROOT)
            or source_files != expected_source_files
        ):
            errors.append("source-semantics file closure differs")
        else:
            for item in source_files:
                try:
                    path = Path(str(item["path"]))
                    markers = item["required_markers"]
                    if (
                        sha256(path) != item["sha256"]
                    ):
                        errors.append(f"source semantic file differs: {path}")
                        continue
                    text = path.read_text(encoding="utf-8", errors="strict")
                    if any(not isinstance(marker, str) or marker not in text for marker in markers):
                        errors.append(f"source semantic marker differs: {path}")
                except (FileNotFoundError, KeyError, TypeError, ValueError) as error:
                    errors.append(f"source semantic validation failed: {error}")
        try:
            if (
                archive
                != {
                    "path": str(SOURCE_ARCHIVE),
                    "sha256": SOURCE_ARCHIVE_SHA256,
                    "validated": True,
                }
                or sha256(SOURCE_ARCHIVE) != SOURCE_ARCHIVE_SHA256
            ):
                errors.append("source archive semantic anchor differs")
        except (FileNotFoundError, KeyError, TypeError, ValueError):
            errors.append("source archive semantic anchor is unavailable")


def _validate_implementation(
    project_root: Path, config: dict, errors: list[str], *, require_committed: bool
) -> None:
    implementation = config.get("implementation")
    modes = config.get("implementation_git_modes")
    if not isinstance(implementation, dict) or not implementation:
        errors.append("implementation closure is missing")
        return
    if not isinstance(modes, dict) or set(modes) != set(implementation):
        errors.append("implementation Git-mode closure differs")
        return
    required = {
        str(PROTOCOL_PATH),
        str(PARSER_PATH),
        "scripts/validate_s1_g1_thermodynamic_label_audit_r1.py",
        "scripts/analyze_s1_g1_thermodynamic_label_audit_r1.py",
        "scripts/run_s1_g1_thermodynamic_label_audit_r1.sh",
    }
    if not required.issubset(implementation):
        errors.append("implementation closure omits a required R1 path")
    for relative, expected_sha in implementation.items():
        try:
            path = _safe_project_path(project_root, relative, "implementation path")
        except ValueError as error:
            errors.append(f"implementation path invalid: {error}")
            continue
        if not path.is_file() or path.is_symlink():
            errors.append(f"implementation is missing or symbolic: {relative}")
            continue
        if not isinstance(expected_sha, str) or sha256(path) != expected_sha:
            errors.append(f"implementation SHA-256 differs: {relative}")
        if require_committed:
            failure = _tracked_head_failure(project_root, relative)
            if failure:
                errors.append(f"implementation {failure}")
                continue
            entries = str(_git(project_root, "ls-files", "--stage", "--", relative)).splitlines()
            if len(entries) != 1:
                errors.append(f"implementation is not tracked exactly once: {relative}")
                continue
            mode = entries[0].split(maxsplit=1)[0]
            if modes.get(relative) != mode or mode not in {"100644", "100755"}:
                errors.append(f"implementation Git mode differs: {relative}")


def _validate_preregistration(
    project_root: Path, config: dict, rows: list[dict[str, str]], errors: list[str]
) -> str | None:
    try:
        prereg = _introduction_commit(project_root, str(CONFIG_PATH))
        if _introduction_commit(project_root, str(MANIFEST_PATH)) != prereg:
            errors.append("config and manifest were not introduced together")
        expected_inputs = {
            f"{row['input_directory']}/{name}"
            for row in rows
            for name in ("INPUT", "STRU", "KPT", "metadata.json")
        }
        expected_scope = {str(CONFIG_PATH), str(MANIFEST_PATH), *expected_inputs}
        if _commit_changed_paths(project_root, prereg) != expected_scope:
            errors.append(
                "preregistration scope is not exactly config+manifest+complete input tree"
            )
        parent = str(_git(project_root, "rev-parse", f"{prereg}^"))
        if config.get("generated_from_commit") != parent:
            errors.append("preregistration parent differs from generated_from_commit")
        for relative in (str(CONFIG_PATH), str(MANIFEST_PATH)):
            if _blob_at(project_root, prereg, relative) != (project_root / relative).read_bytes():
                errors.append(f"{relative} differs from its preregistration blob")
        implementation = config.get("implementation", {})
        if isinstance(implementation, dict):
            for relative in implementation:
                if _blob_at(project_root, parent, relative) != (
                    project_root / relative
                ).read_bytes():
                    errors.append(f"implementation differs from preregistration parent: {relative}")
        for pseudo_name in sorted({row["pseudopotential"] for row in rows}):
            relative = f"assets/pseudo/{pseudo_name}"
            parent_blob = _blob_at(project_root, parent, relative)
            if (
                parent_blob != _blob_at(project_root, "HEAD", relative)
                or parent_blob != (project_root / relative).read_bytes()
            ):
                errors.append(f"pseudopotential asset changed after preregistration parent: {relative}")
            registered_hashes = {
                row["pseudopotential_sha256"]
                for row in rows
                if row["pseudopotential"] == pseudo_name
            }
            if registered_hashes != {sha256(project_root / relative)}:
                errors.append(f"manifest pseudopotential hash differs: {relative}")
        for row in rows:
            experiment_id = row["experiment_id"]
            if str(_git(project_root, "ls-tree", "-r", parent, "--", f"runs/{experiment_id}")):
                errors.append(f"run existed before preregistration: {experiment_id}")
            if str(_git(project_root, "ls-tree", "-r", parent, "--", row["input_directory"])):
                errors.append(f"generated input existed before preregistration: {experiment_id}")
            if _tree_oid(project_root, prereg, row["input_directory"]) != _tree_oid(
                project_root, "HEAD", row["input_directory"]
            ):
                errors.append(f"generated input changed after preregistration: {experiment_id}")
            for name in ("INPUT", "STRU", "KPT", "metadata.json"):
                relative = f"{row['input_directory']}/{name}"
                if _introduction_commit(project_root, relative) != prereg:
                    errors.append(f"input was not introduced by preregistration: {relative}")
                if _blob_at(project_root, prereg, relative) != (
                    project_root / relative
                ).read_bytes():
                    errors.append(f"input differs from preregistration blob: {relative}")
            source_path = row["source_run_path"]
            base = str(config["base_evidence_commit"])
            source_oid = _tree_oid(project_root, base, source_path)
            if source_oid != row["source_run_tree_oid"]:
                errors.append(f"registered source tree OID differs: {experiment_id}")
            if (
                source_oid != _tree_oid(project_root, parent, source_path)
                or source_oid != _tree_oid(project_root, "HEAD", source_path)
            ):
                errors.append(f"registered source run changed: {experiment_id}")
            source_blob_contract = (
                (f"{source_path}/INPUT", "source_input_sha256"),
                (f"{source_path}/STRU", "source_stru_sha256"),
                (f"{source_path}/KPT", "source_kpt_sha256"),
                (f"{source_path}/input_metadata.json", "source_metadata_sha256"),
                (row["source_result_path"], "source_result_sha256"),
                (row["source_log_path"], "source_log_sha256"),
                (
                    f"{source_path}/{row['pseudopotential']}",
                    "pseudopotential_sha256",
                ),
            )
            for relative, hash_key in source_blob_contract:
                base_blob = _blob_at(project_root, base, relative)
                if (
                    base_blob != _blob_at(project_root, parent, relative)
                    or base_blob != _blob_at(project_root, "HEAD", relative)
                    or hashlib.sha256(base_blob).hexdigest() != row[hash_key]
                ):
                    errors.append(f"registered source evidence changed: {relative}")
        return prereg
    except (FileNotFoundError, KeyError, subprocess.CalledProcessError, ValueError) as error:
        errors.append(f"preregistration binding failed: {error}")
        return None


def _active_introduction(
    project_root: Path, experiment_id: str, errors: list[str]
) -> str | None:
    run = project_root / "runs" / experiment_id
    metadata = run / "experiment_metadata.json"
    status = run / STATUS_NAME
    if not run.exists():
        return None
    if not run.is_dir() or run.is_symlink():
        errors.append(f"active run is invalid: {experiment_id}")
        return None
    marker = metadata
    if not metadata.is_file() or metadata.is_symlink():
        marker = status
    if not marker.is_file() or marker.is_symlink():
        errors.append(f"active run lacks metadata/failure-status marker: {experiment_id}")
        return None
    try:
        return _latest_introduction_commit(
            project_root, _relative(project_root, marker)
        )
    except ValueError as error:
        errors.append(f"cannot bind active run {experiment_id}: {error}")
        return None


def _archive_events(
    project_root: Path, experiment_id: str, errors: list[str]
) -> list[tuple[str, str]]:
    root = project_root / "failed_runs" / "runtime_relocation" / experiment_id
    if not root.exists():
        return []
    if not root.is_dir() or root.is_symlink():
        errors.append(f"failed-archive root is invalid: {experiment_id}")
        return []
    events: list[tuple[str, str]] = []
    for attempt in sorted(root.iterdir(), key=lambda path: path.name):
        match = re.fullmatch(r"attempt-([0-9a-f]{12})", attempt.name)
        if not attempt.is_dir() or attempt.is_symlink() or match is None:
            errors.append(f"non-canonical failed archive: {experiment_id}/{attempt.name}")
            continue
        try:
            failure_commit = str(
                _git(project_root, "rev-parse", f"{match.group(1)}^{{commit}}")
            )
            archive_commit = _introduction_commit(
                project_root,
                f"failed_runs/runtime_relocation/{experiment_id}/{attempt.name}",
            )
            if str(_git(project_root, "rev-parse", f"{archive_commit}^")) != failure_commit:
                errors.append(f"archive is not adjacent to failed commit: {experiment_id}")
            run_relative = f"runs/{experiment_id}"
            archive_relative = (
                f"failed_runs/runtime_relocation/{experiment_id}/{attempt.name}"
            )
            failure_tree = _tree_oid(project_root, failure_commit, run_relative)
            archive_tree = _tree_oid(project_root, archive_commit, archive_relative)
            head_tree = _tree_oid(project_root, "HEAD", archive_relative)
            if not failure_tree == archive_tree == head_tree:
                errors.append(
                    f"failed-attempt tree changed while archiving: {experiment_id}"
                )
            failure_paths = _commit_changed_paths(project_root, failure_commit)
            run_prefix = f"{run_relative}/"
            if not failure_paths or any(
                not path.startswith(run_prefix) for path in failure_paths
            ):
                errors.append(
                    f"failed run commit is not independently scoped: {experiment_id}"
                )
            archive_paths = _commit_changed_paths(project_root, archive_commit)
            archive_prefix = f"{archive_relative}/"
            if not archive_paths or any(
                not (
                    path.startswith(run_prefix)
                    or path.startswith(archive_prefix)
                )
                for path in archive_paths
            ):
                errors.append(
                    f"failed archive commit scope differs: {experiment_id}"
                )
            if subprocess.run(
                [
                    "git", "-C", str(project_root), "cat-file", "-e",
                    f"{archive_commit}:{run_relative}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode == 0:
                errors.append(
                    f"failed run was copied instead of moved to archive: {experiment_id}"
                )
            events.append((attempt.name, failure_commit))
        except (subprocess.CalledProcessError, ValueError) as error:
            errors.append(f"cannot bind failed archive {experiment_id}: {error}")
    return events


def _validate_execution_history(
    project_root: Path, prereg: str, errors: list[str]
) -> None:
    active: dict[str, str] = {}
    events: dict[str, list[tuple[str, str]]] = {experiment_id: [] for experiment_id in RUN_IDS}
    for experiment_id in RUN_IDS:
        introduction = _active_introduction(project_root, experiment_id, errors)
        if introduction:
            active[experiment_id] = introduction
            events[experiment_id].append(("active", introduction))
            changed = _commit_changed_paths(project_root, introduction)
            prefix = f"runs/{experiment_id}/"
            if not changed or any(not path.startswith(prefix) for path in changed):
                errors.append(
                    f"accepted run commit is not independently scoped: {experiment_id}"
                )
        archives = _archive_events(project_root, experiment_id, errors)
        events[experiment_id].extend(archives)
        if introduction and archives:
            errors.append(f"R1 forbids an active retry after an archived failure: {experiment_id}")
        for label, commit in events[experiment_id]:
            if commit == prereg or not _is_ancestor(project_root, prereg, commit):
                errors.append(f"execution event is not after preregistration: {experiment_id}/{label}")

    for index, experiment_id in enumerate(EXECUTION_ORDER):
        for label, commit in events[experiment_id]:
            for predecessor in EXECUTION_ORDER[:index]:
                predecessor_commit = active.get(predecessor)
                if predecessor_commit is None:
                    errors.append(
                        f"execution order violation: {experiment_id}/{label} precedes accepted {predecessor}"
                    )
                elif predecessor_commit == commit:
                    errors.append(
                        f"execution order violation: {predecessor} and {experiment_id} share a commit"
                    )
                elif not _is_ancestor(project_root, predecessor_commit, commit):
                    errors.append(
                        f"execution ancestry violation: {predecessor} is not before {experiment_id}/{label}"
                    )


def validate_registration(
    project_root: Path,
    config_path: Path,
    manifest_path: Path,
    *,
    require_committed: bool,
) -> tuple[dict, list[dict[str, str]], dict[str, object]]:
    project_root = project_root.resolve()
    errors: list[str] = []
    if config_path.resolve() != (project_root / CONFIG_PATH).resolve():
        errors.append("config must use its canonical project path")
    if manifest_path.resolve() != (project_root / MANIFEST_PATH).resolve():
        errors.append("manifest must use its canonical project path")
    if not config_path.is_file() or config_path.is_symlink():
        raise ValueError("thermodynamic-label config is missing or symbolic")
    config = read_json(config_path)
    rows = _read_manifest(manifest_path)
    if config.get("protocol_revision") != PROTOCOL_REVISION:
        errors.append("config protocol revision mismatch")
    if config.get("status") != "preregistered":
        errors.append("config status is not preregistered pending execution")
    manifest_registration = config.get("manifest")
    if not isinstance(manifest_registration, dict):
        errors.append("config manifest registration is missing")
        manifest_registration = {}
    if manifest_registration.get("path") != str(MANIFEST_PATH):
        errors.append("config manifest path mismatch")
    if manifest_registration.get("sha256") != sha256(manifest_path):
        errors.append("config manifest SHA-256 mismatch")
    _validate_manifest_rows(project_root, rows, config, errors)
    _validate_config_contract(project_root, config, rows, errors)
    _validate_implementation(
        project_root, config, errors, require_committed=require_committed
    )
    prereg: str | None = None
    if require_committed:
        for relative in (str(CONFIG_PATH), str(MANIFEST_PATH)):
            failure = _tracked_head_failure(project_root, relative)
            if failure:
                errors.append(f"preregistration {failure}")
        prereg = _validate_preregistration(project_root, config, rows, errors)
        if prereg:
            _validate_execution_history(project_root, prereg, errors)
    if errors:
        raise ValueError(
            "S1 G1 thermodynamic-label R1 registration validation failed:\n- "
            + "\n- ".join(errors)
        )
    return config, rows, {"preregistration_commit": prereg}


def field_metrics(
    coarse_density: Path,
    reference_density: Path,
    coarse_potential: Path,
    reference_potential: Path,
    *,
    structure_path: Path,
    expected_electron_count: float,
) -> dict[str, float | bool]:
    """Compute the four preregistered density/derivative field metrics."""

    if not math.isfinite(expected_electron_count) or expected_electron_count <= 0:
        raise ValueError("electron count must be finite and positive")
    density_coarse = parse_abacus_cube(
        coarse_density,
        quantity="density",
        units="electron/bohr^3",
        structure_path=structure_path,
    )
    density_reference = parse_abacus_cube(
        reference_density,
        quantity="density",
        units="electron/bohr^3",
        structure_path=structure_path,
    )
    potential_coarse = parse_abacus_cube(
        coarse_potential,
        quantity="local_effective_potential",
        units="Ry",
        structure_path=structure_path,
    )
    potential_reference = parse_abacus_cube(
        reference_potential,
        quantity="local_effective_potential",
        units="Ry",
        structure_path=structure_path,
    )
    for cube in (
        density_coarse,
        density_reference,
        potential_coarse,
        potential_reference,
    ):
        validate_cube_geometry_against_stru(cube, structure_path)
    # The registered denominator is the independently integrated finer field,
    # not the nominal valence count.  The nominal count is only an independent
    # fail-closed cross-check on that integral.
    density = compare_density_fields(density_reference, density_coarse)
    derivative = compare_potential_derivative_fields(
        potential_reference, potential_coarse
    )
    integrated = float(density["reference_electrons"])
    relative_error = abs(integrated - expected_electron_count) / expected_electron_count
    if not relative_error < 1.0e-10:
        raise ValueError("reference density electron integral failed")
    return {
        "d1": float(density["d1"]),
        "d2": float(density["d2"]),
        "dg": float(derivative["dg"]),
        "rms_g_ev": float(derivative["absolute_rms_ev"]),
        "reference_electrons": integrated,
        "reference_electron_relative_error": relative_error,
        "accepted": density["accepted"] is True and derivative["accepted"] is True,
    }


def validate_cube_geometry_against_stru(cube: object, structure_path: Path) -> None:
    """Bind rounded cube axes and atom rows to the full registered STRU geometry."""

    structure = parse_stru(structure_path)
    cell = [
        [structure.lattice_constant_bohr * value for value in vector]
        for vector in structure.lattice_vectors
    ]
    dimensions = tuple(int(value) for value in cube.dimensions)  # type: ignore[attr-defined]
    axes = cube.axis_steps_bohr  # type: ignore[attr-defined]
    for axis_index in range(3):
        for component in range(3):
            observed = float(axes[axis_index][component]) * dimensions[axis_index]
            expected = cell[axis_index][component]
            if abs(observed - expected) > CUBE_GEOMETRY_ABSOLUTE_TOLERANCE_BOHR:
                raise ValueError("cube axis/grid does not reconstruct the STRU lattice")

    lines = [
        line.strip()
        for line in structure_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    try:
        cursor = lines.index("ATOMIC_POSITIONS") + 1
        coordinate_mode = lines[cursor].lower()
        cursor += 1
    except (ValueError, IndexError) as error:
        raise ValueError("cannot locate STRU atomic positions") from error
    if coordinate_mode != "direct":
        raise ValueError("R1 cube geometry requires registered Direct coordinates")
    expected_atoms: list[tuple[float, float, float, float, float]] = []
    species_identity = {"Al": (13.0, 3.0), "Mg": (12.0, 2.0)}
    while cursor < len(lines):
        species = lines[cursor].split()[0]
        cursor += 1
        if species not in structure.species_counts or species not in species_identity:
            raise ValueError(f"unsupported STRU species in cube binding: {species}")
        cursor += 1  # per-species magnetization
        try:
            count = int(lines[cursor].split()[0])
        except (IndexError, ValueError) as error:
            raise ValueError("invalid STRU atom count") from error
        cursor += 1
        atomic_number, valence = species_identity[species]
        for raw in lines[cursor : cursor + count]:
            fields = raw.split()
            if len(fields) < 3:
                raise ValueError("invalid STRU atom coordinate")
            direct = [float(value) for value in fields[:3]]
            cartesian = tuple(
                sum(direct[axis] * cell[axis][component] for axis in range(3))
                for component in range(3)
            )
            expected_atoms.append((atomic_number, valence, *cartesian))
        cursor += count
    observed_atoms = cube.atom_rows  # type: ignore[attr-defined]
    if len(observed_atoms) != len(expected_atoms):
        raise ValueError("cube/STRU atom-row count differs")
    for observed, expected in zip(observed_atoms, expected_atoms):
        if len(observed) != 5 or any(
            abs(float(observed[index]) - expected[index])
            > CUBE_GEOMETRY_ABSOLUTE_TOLERANCE_BOHR
            for index in range(5)
        ):
            raise ValueError("cube atom identity/position differs from STRU")


def _find_output(run: Path, basename: str) -> Path:
    paths = sorted(run.glob(f"OUT.*/{basename}"))
    if len(paths) != 1 or not paths[0].is_file() or paths[0].is_symlink():
        raise ValueError(f"expected exactly one regular {basename} in {run}")
    return paths[0]


def _label_thermodynamics(labels: dict) -> dict[str, float]:
    source = labels.get("thermodynamic_labels")
    if not isinstance(source, dict):
        raise ValueError("thermodynamic_labels object is missing")
    energy = source.get("energy_labels_ev_per_cell")
    if not isinstance(energy, dict):
        raise ValueError("energy_labels_ev_per_cell object is missing")
    exact_keys = (
        "F", "m", "U", "E_ec", "E_one_elec", "E_localpp", "T_sU",
        "F_s", "E_Hartree", "E_xc", "E_Ewald", "mu",
    )
    if set(energy) != set(exact_keys):
        raise ValueError("thermodynamic energy-label key set differs")
    return {key: _parse_float(energy[key], key) for key in exact_keys}


def _thermodynamic_failures(
    labels: dict, result: dict, raw_log: str, atom_count: int
) -> list[str]:
    errors: list[str] = []
    try:
        reparsed = json_safe(
            parse_thermodynamic_log(raw_log, expected_atom_count=atom_count)
        )
        if labels.get("thermodynamic_labels") != reparsed:
            errors.append("thermodynamic label file differs from raw-log recomputation")
        values = _label_thermodynamics(labels)
        f_value = values["F"]
        m_value = values["m"]
        u_value = values["U"]
        eec_value = values["E_ec"]
        tsu = values["T_sU"]
        fs_value = values["F_s"]
        identities = {
            "U=F-m": abs(u_value - (f_value - m_value)) / atom_count,
            "Eec=F-m/2": abs(eec_value - (f_value - m_value / 2.0)) / atom_count,
            "TsU=Eone-Elocal": abs(
                tsu
                - (
                    values["E_one_elec"] - values["E_localpp"]
                )
            )
            / atom_count,
            "Fs=TsU+m": abs(fs_value - (tsu + m_value)) / atom_count,
            "F=Eone+EH+Exc+EEwald+m": abs(
                f_value
                - (
                    values["E_one_elec"]
                    + values["E_Hartree"]
                    + values["E_xc"]
                    + values["E_Ewald"]
                    + m_value
                )
            )
            / atom_count,
        }
        for name, residual in identities.items():
            if not math.isfinite(residual) or not residual < 1.0e-8:
                errors.append(f"thermodynamic identity failed {name}: {residual}")
        if m_value > 0:
            errors.append("entropy_minus_ts is positive")
        tolerance = 5.0e-10 * max(1.0, abs(f_value), abs(u_value))
        if eec_value < f_value - tolerance or eec_value > u_value + tolerance:
            errors.append("F <= Eec <= U ordering failed")
        result_pairs = {
            "F": "free_energy_ev",
            "m": "entropy_minus_ts_ev",
            "U": "internal_energy_ev",
            "E_ec": "zero_temp_extrapolated_energy_ev",
        }
        for label_key, result_key in result_pairs.items():
            result_value = result.get(result_key)
            if not isinstance(result_value, (int, float)) or not math.isfinite(result_value):
                errors.append(f"result.json lacks finite {result_key}")
            elif abs(values[label_key] - float(result_value)) / atom_count >= 1.0e-8:
                errors.append(f"label/result mismatch for {label_key}")
    except (KeyError, TypeError, ValueError) as error:
        errors.append(f"thermodynamic-label validation failed: {error}")
    return errors


@contextmanager
def _runtime_science_policy(enabled: bool) -> Iterator[None]:
    """Keep the frozen runtime validator while disabling its unrelated gate.

    The generic replay validator hard-codes scientific identity for its own R8
    replay use case.  Half/quarter runs intentionally change sigma, so their
    scientific gates are evaluated by this protocol instead.  Runtime,
    namespace, input archival, status, and Git-chain validation remain the
    unmodified accepted implementation.
    """

    if enabled:
        yield
        return
    original = runtime_validation.equivalence_tier
    runtime_validation.equivalence_tier = lambda *_args, **_kwargs: {
        "scientific_tolerance_passed": True
    }
    try:
        yield
    finally:
        runtime_validation.equivalence_tier = original


def _runtime_row(row: dict[str, str]) -> dict[str, str]:
    return {
        **row,
        "replay_experiment_id": row["experiment_id"],
        # The accepted runtime validator insists on reparsing a completed
        # reference even when its science gate is disabled.  P0's scientific
        # half partner has intentionally not run yet, so use the immutable
        # source solely as that legacy parser carrier.  Manifest partner
        # semantics remain untouched and are checked independently below.
        "reference_experiment_id": row["source_experiment_id"],
        "solver": "ksdft",
    }


def _status_failure(path: Path, experiment_id: str, *, accepted: bool) -> str | None:
    if not path.is_file() or path.is_symlink():
        return f"missing or symbolic {STATUS_NAME}"
    try:
        payload = read_json(path)
    except (ValueError, json.JSONDecodeError) as error:
        return f"invalid {STATUS_NAME}: {error}"
    common = {
        "schema_version": 1,
        "protocol_revision": PROTOCOL_REVISION,
        "experiment_id": experiment_id,
        "authoritative_for_r1": True,
        "retry_policy": "new_protocol_revision_and_new_experiment_ids_only",
    }
    for key, value in common.items():
        if payload.get(key) != value:
            return f"authoritative status {key} differs"
    if accepted:
        expected = {
            **common,
            "status": "accepted",
            "workflow_exit_code": 0,
            "parser_exit_code": 0,
            "core_validator_exit_code": 0,
        }
        return None if payload == expected else "accepted authoritative status differs"
    allowed_keys = {
        *common,
        "status",
        "workflow_exit_code",
        "parser_exit_code",
        "core_validator_exit_code",
        "failure_stage",
    }
    workflow_code = payload.get("workflow_exit_code")
    parser_code = payload.get("parser_exit_code")
    core_code = payload.get("core_validator_exit_code")
    codes = [workflow_code, parser_code, core_code]
    stage = payload.get("failure_stage")
    if (
        set(payload) != allowed_keys
        or payload.get("status") not in {"rejected", "indeterminate"}
        or not all(isinstance(value, int) and not isinstance(value, bool) for value in codes)
        or stage not in {"workflow", "thermodynamic_label_parser", "core_validator"}
    ):
        return "failed authoritative status model differs"
    stage_codes_are_coherent = (
        (stage == "workflow" and workflow_code != 0 and parser_code == 97 and core_code == 97)
        or (
            stage == "thermodynamic_label_parser"
            and workflow_code == 0
            and parser_code != 0
            and core_code == 97
        )
        or (
            stage == "core_validator"
            and workflow_code == 0
            and parser_code == 0
            and core_code != 0
        )
    )
    if not stage_codes_are_coherent:
        return "failed authoritative status stage/exit tuple differs"
    return None


def classify_core_failures(
    experiment_id: str, failures: list[str]
) -> dict[str, object]:
    text = "\n".join(failures).lower()
    capability_markers = (
        "missing", "symbolic", "not a regular", "unavailable", "cannot ",
        "malformed", "invalid json", "raw-log reparse failed", "sha-256 mismatch",
        "differs from head", "not tracked", "input comparison failed",
        "expected exactly one", "ambiguous", "grid differs", "geometry differs",
        "value count differs", "non-finite", "zero projected", "zero density",
        "basename is not", "invalid cube", "does not reconstruct", "cube/stru",
        "atom identity/position differs", "disagree with stru",
    )
    categories: list[str] = []
    mapping = (
        ("input_hash", ("input", "sha-256", "pseudopotential", "metadata")),
        ("scf", ("converg", "running_scf", "raw log")),
        ("thermodynamic_identity", ("thermodynamic", "identity", "entropy")),
        ("electron_number", ("electron",)),
        ("replay_equivalence", ("standard replay equivalence", "scientific equivalence")),
        ("runtime_kmp", ("runtime", "kmp", "namespace", "mapping", "strace", "audit")),
    )
    for category, markers in mapping:
        if any(marker in text for marker in markers):
            categories.append(category)
    capability = any(marker in text for marker in capability_markers)
    return {
        "schema_version": 1,
        "protocol_revision": PROTOCOL_REVISION,
        "experiment_id": experiment_id,
        "failure_component": "core_validator",
        "status": "indeterminate" if capability else "rejected",
        "failure_class": (
            "capability_or_evidence_failure"
            if capability
            else "complete_numerical_or_runtime_contract_rejection"
        ),
        "failure_categories": sorted(set(categories)) or ["unclassified_core_validator"],
        "failure_reasons": failures,
        "retry_policy": "new_protocol_revision_and_new_experiment_ids_only",
    }


_RUNTIME_REJECTION_COUNT_FIELDS = (
    "old_prefix_successful_access_count",
    "old_prefix_exec_success_count",
    "old_prefix_mapped_object_count",
    "unknown_old_prefix_failed_probe_count",
    "unexpected_mapped_object_count",
    "unhashed_regular_mapped_object_count",
    "registered_probe_count_mismatch_count",
    "ambiguous_exec_result_count",
)


def _optional_json_object(path: Path) -> dict[str, object] | None:
    if not path.is_file() or path.is_symlink():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _complete_runtime_contract_rejection(
    run_status: dict[str, object] | None,
    result: dict[str, object] | None,
    audit: dict[str, object] | None,
) -> bool:
    """Recognize only a complete, structured runtime-contract rejection.

    Field *names* such as ``ambiguous_exec_result_count`` and
    ``counterpart_missing_count`` are deliberately never scanned as text.  A
    zero-valued diagnostic is not a capability failure and is not, by itself,
    a rejection.  Conversely, a truncated audit cannot be upgraded from
    indeterminate merely because its top-level status says ``rejected``.
    """

    if not (
        isinstance(run_status, dict)
        and run_status.get("schema_version") == 2
        and run_status.get("status") == "rejected"
        and run_status.get("result_json_present") is True
        and run_status.get("result_converged") is True
        and run_status.get("runtime_audit_json_present") is True
        and run_status.get("runtime_audit_status") == "rejected"
        and isinstance(result, dict)
        and result.get("converged") is True
        and isinstance(audit, dict)
        and audit.get("schema_version") == 2
        and audit.get("protocol") == "runtime_relocation_equivalence"
        and audit.get("status") == "rejected"
        and isinstance(audit.get("failure_reasons"), list)
        and bool(audit["failure_reasons"])
        and all(
            isinstance(reason, str) and bool(reason)
            for reason in audit["failure_reasons"]
        )
        and isinstance(audit.get("timeout_triggered"), bool)
        and isinstance(audit.get("launcher_exit_code"), int)
        and not isinstance(audit.get("launcher_exit_code"), bool)
        and audit.get("rank_handshake_status") in {"accepted", "rejected"}
        and isinstance(audit.get("rank_pids"), dict)
        and set(audit["rank_pids"]) == {"0", "1", "2", "3"}
        and isinstance(audit.get("transient_mapping_patterns"), list)
        and all(isinstance(value, str) for value in audit["transient_mapping_patterns"])
    ):
        return False
    counts: list[int] = []
    for field in _RUNTIME_REJECTION_COUNT_FIELDS:
        value = audit.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return False
        counts.append(value)
    return bool(
        any(value > 0 for value in counts)
        or audit["timeout_triggered"] is True
        or audit["launcher_exit_code"] != 0
        or audit["rank_handshake_status"] == "rejected"
    )


def classify_noncore_failure(
    run: Path,
    experiment_id: str,
    stage: str,
    component_exit: int,
    diagnostic_path: Path | None = None,
) -> dict[str, object]:
    """Recompute a workflow/parser failure decision from preserved artifacts."""

    if stage not in {"workflow", "thermodynamic_label_parser"}:
        raise ValueError(f"unsupported non-core failure stage: {stage}")
    if not isinstance(component_exit, int) or isinstance(component_exit, bool) or component_exit == 0:
        raise ValueError("non-core failure component exit must be a nonzero integer")
    if diagnostic_path is None:
        diagnostic_path = run / (
            "outer_workflow_failure.txt"
            if stage == "workflow"
            else "thermodynamic_label_parser.stderr.txt"
        )
    text = (
        diagnostic_path.read_text(encoding="utf-8", errors="replace")
        if diagnostic_path.is_file() and not diagnostic_path.is_symlink()
        else ""
    )
    reasons = [line for line in text.splitlines() if line.strip()]
    reasons.append(f"{stage}_exit_code={component_exit}")
    if stage == "workflow":
        run_status = _optional_json_object(run / "run_status.json")
        result = _optional_json_object(run / "result.json")
        audit = _optional_json_object(run / "mpi_runtime_audit/audit.json")
        for label, value in (
            ("run_status", run_status),
            ("result", result),
            ("runtime_audit", audit),
        ):
            if value is not None:
                reasons.append(f"{label}={json.dumps(value, sort_keys=True)}")
        complete_scf_rejection = bool(
            isinstance(run_status, dict)
            and run_status.get("schema_version") == 2
            and run_status.get("status") == "rejected"
            and run_status.get("result_json_present") is True
            and run_status.get("result_converged") is False
            and isinstance(result, dict)
            and result.get("converged") is False
        )
        complete_runtime_rejection = _complete_runtime_contract_rejection(
            run_status, result, audit
        )
        rejected = complete_scf_rejection or complete_runtime_rejection
        status = "rejected" if rejected else "indeterminate"
        failure_class = (
            "complete_numerical_or_runtime_contract_rejection"
            if rejected
            else "workflow_or_runtime_capability_failure"
        )
        categories = ["scf"] if complete_scf_rejection else ["runtime_kmp"]
    else:
        lowered = text.lower()
        capability_markers = (
            "missing", "expected exactly one", "ambiguous", "malformed", "truncated",
            "not a regular", "symbolic", "grid differs", "geometry differs",
            "value count differs", "cannot ", "unavailable", "non-finite",
            "zero projected", "zero density", "basename is not", "invalid cube",
            "does not reconstruct", "cube/stru", "atom identity/position differs",
            "disagree with stru",
        )
        capability = any(marker in lowered for marker in capability_markers)
        status = "indeterminate" if capability else "rejected"
        failure_class = (
            "thermodynamic_label_parser_capability_failure"
            if capability
            else "complete_numerical_or_runtime_contract_rejection"
        )
        categories = []
        for category, markers in (
            ("thermodynamic_identity", ("thermodynamic", "identity", "entropy")),
            ("electron_number", ("electron", "density integration")),
            ("density", ("density", "chg.cube")),
            ("derivative", ("potential", "pot.cube", "projected")),
        ):
            if any(marker in lowered for marker in markers):
                categories.append(category)
        if not categories:
            categories = ["thermodynamic_label_parser"]
    return {
        "schema_version": 1,
        "protocol_revision": PROTOCOL_REVISION,
        "experiment_id": experiment_id,
        "failure_component": stage,
        "status": status,
        "failure_class": failure_class,
        "failure_categories": sorted(set(categories)),
        "failure_reasons": reasons,
        "retry_policy": "new_protocol_revision_and_new_experiment_ids_only",
    }


def _write_exclusive_json(path: Path, payload: dict[str, object]) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError(f"refusing to overwrite failure evidence: {path}")
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def validate_failed_r1_run(
    project_root: Path,
    row: dict[str, str],
    *,
    require_committed: bool,
    directory: Path | None = None,
) -> list[str]:
    experiment_id = row["experiment_id"]
    run = directory or project_root / "runs" / experiment_id
    errors: list[str] = []
    if not run.is_dir() or run.is_symlink():
        return [f"{experiment_id}: missing or symbolic failed-attempt directory"]
    status_error = _status_failure(run / STATUS_NAME, experiment_id, accepted=False)
    if status_error:
        errors.append(f"{experiment_id}: {status_error}")
    try:
        authoritative_status = read_json(run / STATUS_NAME)
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        authoritative_status = {}
    if not isinstance(authoritative_status, dict):
        authoritative_status = {}
    stage = authoritative_status.get("failure_stage")

    status: dict[str, object] = {}
    run_status = run / "run_status.json"
    if not run_status.is_file() or run_status.is_symlink():
        errors.append(f"{experiment_id}: missing or symbolic run_status.json")
    else:
        try:
            parsed_status = read_json(run_status)
            if not isinstance(parsed_status, dict):
                raise ValueError("run_status root is not an object")
            status = parsed_status
            if status.get("schema_version") != 2:
                errors.append(f"{experiment_id}: failed run_status schema differs")
        except (ValueError, json.JSONDecodeError) as error:
            errors.append(f"{experiment_id}: invalid run_status.json: {error}")

    classification_path = run / FAILURE_CLASS_NAME
    if not classification_path.is_file() or classification_path.is_symlink():
        errors.append(f"{experiment_id}: missing or symbolic failure classification")
        classification = {}
    else:
        try:
            classification = read_json(classification_path)
            if not isinstance(classification, dict):
                raise ValueError("classification root is not an object")
            reasons = classification.get("failure_reasons")
            if not isinstance(reasons, list) or not reasons or not all(
                isinstance(value, str) and value for value in reasons
            ):
                errors.append(f"{experiment_id}: failure reasons are incomplete")
            else:
                expected_classification: dict[str, object] | None = None
                if stage == "core_validator":
                    expected_classification = classify_core_failures(
                        experiment_id, reasons
                    )
                elif stage in {"workflow", "thermodynamic_label_parser"}:
                    component_exit = authoritative_status.get(
                        "workflow_exit_code"
                        if stage == "workflow"
                        else "parser_exit_code"
                    )
                    if isinstance(component_exit, int) and not isinstance(
                        component_exit, bool
                    ):
                        expected_classification = classify_noncore_failure(
                            run, experiment_id, stage, component_exit
                        )
                if (
                    expected_classification is None
                    or classification != expected_classification
                ):
                    errors.append(
                        f"{experiment_id}: failure classification is not reproducible"
                    )
            if classification.get("failure_component") != stage:
                errors.append(
                    f"{experiment_id}: classification failure component differs"
                )
            if classification.get("status") != authoritative_status.get("status"):
                errors.append(f"{experiment_id}: classification/status decision differs")
        except (ValueError, json.JSONDecodeError) as error:
            errors.append(f"{experiment_id}: invalid failure classification: {error}")
            classification = {}
    source = project_root / row["input_directory"]
    setup_completed = status.get("setup_completed") is True
    for run_name, source_name in (
        ("INPUT", "INPUT"),
        ("STRU", "STRU"),
        ("KPT", "KPT"),
        ("input_metadata.json", "metadata.json"),
    ):
        path = run / run_name
        if not path.exists() and not path.is_symlink() and not setup_completed:
            continue
        if not path.exists() and setup_completed:
            errors.append(f"{experiment_id}: completed setup lacks {run_name}")
            continue
        if not path.is_file() or path.is_symlink():
            errors.append(f"{experiment_id}: invalid failed input {run_name}")
            continue
        expected = (source / source_name).read_bytes()
        if run_name == "INPUT":
            expected = runtime_validation.normalized_run_input(expected)
        if path.read_bytes() != expected:
            errors.append(f"{experiment_id}: failed input differs: {run_name}")
    pseudo = run / row["pseudopotential"]
    if setup_completed and not pseudo.exists() and not pseudo.is_symlink():
        errors.append(f"{experiment_id}: completed setup lacks pseudopotential")
    elif pseudo.exists() or pseudo.is_symlink():
        asset = project_root / "assets" / "pseudo" / row["pseudopotential"]
        if not pseudo.is_file() or pseudo.is_symlink() or pseudo.read_bytes() != asset.read_bytes():
            errors.append(f"{experiment_id}: failed pseudopotential differs")
    if setup_completed:
        for relative in ("experiment_metadata.json", "INPUT_SHA256SUMS", "resource_usage.txt"):
            path = run / relative
            if not path.is_file() or path.is_symlink():
                errors.append(f"{experiment_id}: completed attempt lacks {relative}")
    if status.get("result_json_present") is True:
        if not (run / "result.json").is_file() or (run / "result.json").is_symlink():
            errors.append(f"{experiment_id}: result-presence claim differs")
        logs = sorted(run.glob("OUT.*/running_scf.log"))
        if len(logs) != 1 or logs[0].is_symlink():
            errors.append(f"{experiment_id}: failed attempt raw-log closure differs")
    if status.get("runtime_audit_json_present") is True:
        for relative in (
            "mpi_runtime_audit/audit.json",
            "mpi_runtime_audit/objects.tsv",
            "mpi_runtime_audit/namespace/host_status.json",
            "mpi_runtime_audit/counterpart_audit.json",
        ):
            path = run / relative
            if not path.is_file() or path.is_symlink():
                errors.append(f"{experiment_id}: runtime evidence claim lacks {relative}")
    if stage == "core_validator":
        for relative in (
            LABEL_NAME,
            "core_validator.stdout.txt",
            "core_validator.stderr.txt",
        ):
            path = run / relative
            if not path.is_file() or path.is_symlink():
                errors.append(f"{experiment_id}: core failure lacks {relative}")
        core_stderr = run / "core_validator.stderr.txt"
        classified_reasons = classification.get("failure_reasons")
        if (
            core_stderr.is_file()
            and not core_stderr.is_symlink()
            and isinstance(classified_reasons, list)
        ):
            stderr_text = core_stderr.read_text(encoding="utf-8", errors="replace")
            if any(
                isinstance(reason, str) and reason not in stderr_text
                for reason in classified_reasons
            ):
                errors.append(
                    f"{experiment_id}: core diagnostic omits classified failure reason"
                )
    elif stage == "thermodynamic_label_parser":
        for relative in (
            "thermodynamic_label_parser.stdout.txt",
            "thermodynamic_label_parser.stderr.txt",
        ):
            path = run / relative
            if not path.is_file() or path.is_symlink():
                errors.append(f"{experiment_id}: parser failure lacks {relative}")
    elif stage == "workflow":
        diagnostic = run / "outer_workflow_failure.txt"
        if not diagnostic.is_file() or diagnostic.is_symlink():
            errors.append(
                f"{experiment_id}: workflow failure lacks outer_workflow_failure.txt"
            )

    inventory_path = run / FAILURE_INVENTORY_NAME
    if not inventory_path.is_file() or inventory_path.is_symlink():
        errors.append(f"{experiment_id}: missing failure artifact inventory")
    else:
        try:
            inventory = read_json(inventory_path)
            expected_files = []
            for path in sorted(run.rglob("*"), key=lambda value: str(value.relative_to(run))):
                if path == inventory_path:
                    continue
                if path.is_symlink():
                    raise ValueError(f"symbolic failure artifact: {path.relative_to(run)}")
                if path.is_file():
                    expected_files.append(
                        {
                            "path": str(path.relative_to(run)),
                            "sha256": sha256(path),
                            "size_bytes": path.stat().st_size,
                        }
                    )
            expected_inventory = {
                "schema_version": 1,
                "protocol_revision": PROTOCOL_REVISION,
                "experiment_id": experiment_id,
                "files": expected_files,
            }
            if inventory != expected_inventory:
                errors.append(f"{experiment_id}: failure artifact inventory differs")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            errors.append(f"{experiment_id}: invalid failure artifact inventory: {error}")
    if require_committed:
        try:
            artifacts = sorted(
                (
                    path
                    for path in run.rglob("*")
                    if path.is_file() and not path.is_symlink()
                ),
                key=lambda path: str(path.relative_to(run)),
            )
            for artifact in artifacts:
                relative = _relative(project_root, artifact)
                failure = _tracked_head_failure(project_root, relative)
                if failure:
                    errors.append(f"{experiment_id}: {failure}")
        except (OSError, ValueError) as error:
            errors.append(
                f"{experiment_id}: cannot bind failure artifacts to HEAD: {error}"
            )
    return errors


def replay_evidence(
    project_root: Path,
    config: dict,
    row: dict[str, str],
    *,
    require_committed: bool,
    require_replay_status: bool,
) -> tuple[dict[str, object], list[str]]:
    experiment_id = row["experiment_id"]
    run = project_root / "runs" / experiment_id
    role = _role(row)
    errors: list[str] = []
    with _runtime_science_policy(role == "standard_replay"):
        errors.extend(
            runtime_validation.validate_replay_run(
                project_root,
                config,
                _runtime_row(row),
                require_committed=require_committed,
                # replay_status.json belongs to the older retryable R2
                # workflow and is never authoritative for this no-retry R1.
                require_replay_status=False,
            )
        )
    payload: dict[str, object] = {}
    try:
        labels_path = run / LABEL_NAME
        if not labels_path.is_file() or labels_path.is_symlink():
            raise ValueError(f"missing or symbolic {LABEL_NAME}")
        labels = read_json(labels_path)
        reparsed_labels = parse_label_run(
            run,
            config_path=project_root / CONFIG_PATH,
            manifest_path=project_root / MANIFEST_PATH,
        )
        if labels != reparsed_labels:
            errors.append(f"{experiment_id}: {LABEL_NAME} differs from recomputation")
        result = read_json(run / "result.json")
        metadata = read_json(run / "input_metadata.json")
        if result.get("converged") is not True:
            errors.append(f"{experiment_id}: run did not converge")
        log = find_single_log(run)
        text = log.read_text(encoding="utf-8", errors="strict")
        if "#SCF IS CONVERGED#" not in text or "#SCF IS NOT CONVERGED#" in text:
            errors.append(f"{experiment_id}: raw log convergence markers failed")
        atom_count = int(metadata["atom_count"])
        errors.extend(
            f"{experiment_id}: {failure}"
            for failure in _thermodynamic_failures(labels, result, text, atom_count)
        )

        charge = _find_output(run, "chg.cube")
        potential = _find_output(run, "pot.cube")
        grid = parse_charge_grid(log)
        charge_payload = parse_abacus_cube(
            charge,
            quantity="thermal_density",
            units="electron/bohr^3",
            structure_path=run / "STRU",
            expected_grid=grid,
        )
        potential_payload = parse_abacus_cube(
            potential,
            quantity="local_effective_ks_potential",
            units="Ry",
            structure_path=run / "STRU",
            expected_grid=grid,
        )
        validate_cube_geometry_against_stru(charge_payload, run / "STRU")
        validate_cube_geometry_against_stru(potential_payload, run / "STRU")
        if charge_payload.geometry_signature != potential_payload.geometry_signature:
            errors.append(f"{experiment_id}: charge/potential cube geometry differs")
        expected, derivation = expected_electrons(run)
        integration = integrate_cube(charge, run / "STRU", expected, grid)
        if integration.get("accepted") is not True:
            errors.append(f"{experiment_id}: electron-number integration failed")

        contract = config["kmp_contract"]
        kmp = validate_kmp_runtime_contract(
            run,
            expected_libomp_path=contract["libomp"]["path"],
            expected_libomp_realpath=contract["libomp"]["realpath"],
            expected_libomp_sha256=contract["libomp"]["sha256"],
            require_registered_mapping_pattern=True,
        )
        if kmp.get("accepted") is not True:
            errors.append(f"{experiment_id}: KMP runtime contract rejected")

        equivalence: dict[str, object] | None = None
        if role == "standard_replay":
            source_result = read_json(project_root / row["source_result_path"])
            equivalence = scientific_equivalence(source_result, result)
            delta_eec = abs(
                float(result["zero_temp_extrapolated_energy_ev_per_atom"])
                - float(source_result["zero_temp_extrapolated_energy_ev_per_atom"])
            ) * 1000.0
            delta_f = abs(
                float(result["free_energy_ev_per_atom"])
                - float(source_result["free_energy_ev_per_atom"])
            ) * 1000.0
            delta_pressure = abs(
                float(result["pressure_gpa"]) - float(source_result["pressure_gpa"])
            )
            equivalence["delta_entropy_corrected_energy_mev_per_atom"] = delta_eec
            equivalence["delta_free_energy_mev_per_atom"] = delta_f
            equivalence["delta_pressure_gpa"] = delta_pressure
            equivalence["accepted"] = (
                delta_eec < 0.1
                and delta_f < 0.1
                and delta_pressure < 0.02
            )
            if equivalence["accepted"] is not True:
                errors.append(f"{experiment_id}: standard replay equivalence failed")

        payload = {
            "schema_version": 1,
            "protocol_revision": PROTOCOL_REVISION,
            "status": "accepted" if not errors else "rejected",
            "experiment_id": experiment_id,
            "source_experiment_id": row["source_experiment_id"],
            "run_role": role,
            "thermodynamic_labels_path": f"runs/{experiment_id}/{LABEL_NAME}",
            "thermodynamic_labels_sha256": sha256(labels_path),
            "charge_density": {
                "path": _relative(project_root, charge),
                "sha256": charge_payload.sha256,
                "dimensions": list(grid),
            },
            "local_effective_potential": {
                "path": _relative(project_root, potential),
                "sha256": potential_payload.sha256,
                "dimensions": list(grid),
                "unit": "Ry",
            },
            "expected_electron_derivation": derivation,
            "electron_number_integration": integration,
            "standard_replay_equivalence": equivalence,
            "kmp_runtime_contract": kmp,
            "provenance": {
                "config_path": str(CONFIG_PATH),
                "config_sha256": sha256(project_root / CONFIG_PATH),
                "manifest_path": str(MANIFEST_PATH),
                "manifest_sha256": sha256(project_root / MANIFEST_PATH),
                "preregistration_commit": _introduction_commit(
                    project_root, str(CONFIG_PATH)
                ),
                "replay_code_commit": read_json(run / "experiment_metadata.json")[
                    "code_commit"
                ],
            },
        }
        if require_replay_status:
            status_path = run / STATUS_NAME
            if not status_path.is_file() or status_path.is_symlink():
                errors.append(f"{experiment_id}: missing or symbolic {STATUS_NAME}")
            else:
                status = read_json(status_path)
                expected_status = {
                    "schema_version": 1,
                    "protocol_revision": PROTOCOL_REVISION,
                    "experiment_id": experiment_id,
                    "status": "accepted",
                    "authoritative_for_r1": True,
                    "retry_policy": "new_protocol_revision_and_new_experiment_ids_only",
                    "workflow_exit_code": 0,
                    "parser_exit_code": 0,
                    "core_validator_exit_code": 0,
                }
                if status != expected_status:
                    errors.append(f"{experiment_id}: authoritative status differs")
                elif require_committed:
                    failure = _tracked_head_failure(
                        project_root, f"runs/{experiment_id}/{STATUS_NAME}"
                    )
                    if failure:
                        errors.append(f"{experiment_id}: {failure}")
        evidence_path = run / EVIDENCE_NAME
        if evidence_path.exists() or evidence_path.is_symlink():
            if not evidence_path.is_file() or evidence_path.is_symlink():
                errors.append(f"{experiment_id}: evidence is not a regular file")
            elif read_json(evidence_path) != payload:
                errors.append(f"{experiment_id}: evidence differs from recomputation")
            elif require_committed:
                failure = _tracked_head_failure(
                    project_root, f"runs/{experiment_id}/{EVIDENCE_NAME}"
                )
                if failure:
                    errors.append(f"{experiment_id}: {failure}")
        elif require_committed or require_replay_status:
            errors.append(f"{experiment_id}: missing {EVIDENCE_NAME}")
    except (
        FileNotFoundError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
    ) as error:
        errors.append(f"{experiment_id}: run evidence validation failed: {error}")
    return payload, errors


def _write_evidence(path: Path, payload: dict[str, object]) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError(f"refusing to overwrite run evidence: {path}")
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _row_result(project_root: Path, row: dict[str, str]) -> dict:
    return read_json(project_root / "runs" / row["experiment_id"] / "result.json")


def evaluate_pilot_gate(
    project_root: Path,
    rows: list[dict[str, str]],
    *,
    require_committed: bool,
) -> dict[str, object]:
    """Evaluate the P0-only V0 energy and field barrier."""

    by_id = {row["experiment_id"]: row for row in rows}
    pairs = (
        ("S1-20260806-024", "S1-20260806-036"),
        ("S1-20260806-031", "S1-20260806-039"),
    )
    payloads: list[dict[str, object]] = []
    for common_id, extra_id in pairs:
        if require_committed:
            for experiment_id in (common_id, extra_id):
                failure = _tracked_head_failure(
                    project_root, f"runs/{experiment_id}/{EVIDENCE_NAME}"
                )
                if failure:
                    raise ValueError(f"pilot gate {experiment_id}: {failure}")
        common_run = project_root / "runs" / common_id
        extra_run = project_root / "runs" / extra_id
        expected, _ = expected_electrons(common_run)
        fields = field_metrics(
            _find_output(common_run, "chg.cube"),
            _find_output(extra_run, "chg.cube"),
            _find_output(common_run, "pot.cube"),
            _find_output(extra_run, "pot.cube"),
            structure_path=common_run / "STRU",
            expected_electron_count=expected,
        )
        common_result = _row_result(project_root, by_id[common_id])
        extra_result = _row_result(project_root, by_id[extra_id])
        delta_eec = abs(
            float(extra_result["zero_temp_extrapolated_energy_ev_per_atom"])
            - float(common_result["zero_temp_extrapolated_energy_ev_per_atom"])
        ) * 1000.0
        delta_pressure = abs(
            float(extra_result["pressure_gpa"])
            - float(common_result["pressure_gpa"])
        )
        payloads.append(
            {
                "material": by_id[common_id]["material"],
                "common_experiment_id": common_id,
                "extra_experiment_id": extra_id,
                "absolute_energy_difference_mev_per_atom": delta_eec,
                "absolute_pressure_difference_gpa_diagnostic": delta_pressure,
                "field_metrics": fields,
                "accepted": delta_eec < 2.0 and fields["accepted"] is True,
            }
        )
    return {
        "pair_count": len(payloads),
        "pairs": payloads,
        "accepted": len(payloads) == 2
        and all(payload["accepted"] is True for payload in payloads),
    }


def evaluate_half_quarter_pair(
    project_root: Path,
    rows: list[dict[str, str]],
    quarter_experiment_id: str,
    *,
    require_committed: bool,
) -> dict[str, object]:
    """Evaluate one seven-point hard field pair as soon as it is decidable."""

    if quarter_experiment_id not in COMMON_QUARTER_IDS:
        raise ValueError("half-quarter pair requires a common-quarter ID 021--034")
    quarter_number = int(quarter_experiment_id.rsplit("-", 1)[1])
    half_id = f"S1-20260806-{quarter_number - 14:03d}"
    by_id = {row["experiment_id"]: row for row in rows}
    quarter_row = by_id[quarter_experiment_id]
    if quarter_row["reference_experiment_id"] != half_id:
        raise ValueError("manifest half-quarter scientific partner differs")
    for experiment_id in (half_id, quarter_experiment_id):
        if require_committed:
            failure = _tracked_head_failure(
                project_root, f"runs/{experiment_id}/{EVIDENCE_NAME}"
            )
            if failure:
                raise ValueError(f"half-quarter pair {experiment_id}: {failure}")
    half_run = project_root / "runs" / half_id
    quarter_run = project_root / "runs" / quarter_experiment_id
    expected, _ = expected_electrons(quarter_run)
    fields = field_metrics(
        _find_output(half_run, "chg.cube"),
        _find_output(quarter_run, "chg.cube"),
        _find_output(half_run, "pot.cube"),
        _find_output(quarter_run, "pot.cube"),
        structure_path=quarter_run / "STRU",
        expected_electron_count=expected,
    )
    return {
        "half_experiment_id": half_id,
        "quarter_experiment_id": quarter_experiment_id,
        "material": quarter_row["material"],
        "volume_ratio": float(quarter_row["volume_ratio"]),
        "field_metrics": fields,
        "accepted": fields["accepted"] is True,
    }


def _eos_point(project_root: Path, experiment_id: str, ratio: float) -> dict[str, float]:
    run = project_root / "runs" / experiment_id
    structure = parse_stru(run / "STRU")
    atom_count = sum(structure.species_counts.values())
    log = find_single_log(run)
    thermo = parse_thermodynamic_log(
        log.read_text(encoding="utf-8", errors="strict"),
        expected_atom_count=atom_count,
    )
    labels = thermo["energy_labels_ev_per_atom"]
    if not isinstance(labels, dict):
        raise ValueError(f"{experiment_id}: missing per-atom energy labels")
    return {
        "volume_ratio": ratio,
        "volume_per_atom_angstrom3": (
            structure.volume_bohr3 * BOHR_TO_ANGSTROM**3 / atom_count
        ),
        "e_ec_ev_per_atom": float(labels["E_ec"]),
    }


def _eos_fit_gate(points: list[dict[str, float]]) -> tuple[dict, list[str]]:
    failures: list[str] = []
    ordered = sorted(points, key=lambda point: point["volume_per_atom_angstrom3"])
    if len(ordered) != 7 or {point["volume_ratio"] for point in ordered} != set(EOS_RATIOS):
        return {}, ["series_does_not_contain_seven_frozen_ratios"]
    fit = fit_bm3(
        [point["volume_per_atom_angstrom3"] for point in ordered],
        [point["e_ec_ev_per_atom"] for point in ordered],
    )
    if not (
        ordered[0]["volume_per_atom_angstrom3"]
        < float(fit["v0_angstrom3_per_atom"])
        < ordered[-1]["volume_per_atom_angstrom3"]
    ):
        failures.append("fitted_v0_not_strictly_inside_sampled_interval")
    if not float(fit["b0_gpa"]) > 0.0:
        failures.append("bulk_modulus_not_strictly_positive")
    if not float(fit["max_abs_residual_mev_per_atom"]) < 1.0:
        failures.append("maximum_fit_residual_not_strictly_below_1_mev")
    return fit, failures


def evaluate_adjacent_eos_gate(
    project_root: Path,
    rows: list[dict[str, str]],
    material: str,
    coarse_level: str,
    fine_level: str,
    *,
    require_committed: bool,
) -> dict[str, object]:
    """Evaluate one material/adjacent-smearing EOS gate at first decidability."""

    if material not in {"al", "mg"} or (coarse_level, fine_level) not in {
        ("standard", "half"),
        ("half", "quarter"),
    }:
        raise ValueError("unsupported adjacent EOS gate")
    first_source = 85 if material == "al" else 106
    first_half = 7 if material == "al" else 14
    first_quarter = 21 if material == "al" else 28
    ids_by_level = {
        "standard": tuple(
            f"S1-20260805-{first_source + index:03d}" for index in range(7)
        ),
        "half": tuple(
            f"S1-20260806-{first_half + index:03d}" for index in range(7)
        ),
        "quarter": tuple(
            f"S1-20260806-{first_quarter + index:03d}" for index in range(7)
        ),
    }
    by_id = {row["experiment_id"]: row for row in rows}
    for level in (coarse_level, fine_level):
        if level == "standard":
            continue
        for experiment_id in ids_by_level[level]:
            if require_committed:
                failure = _tracked_head_failure(
                    project_root, f"runs/{experiment_id}/{EVIDENCE_NAME}"
                )
                if failure:
                    raise ValueError(f"adjacent EOS {experiment_id}: {failure}")
            if by_id[experiment_id]["material"] != material:
                raise ValueError(f"adjacent EOS material differs: {experiment_id}")
    series: dict[str, dict[str, object]] = {}
    for level in (coarse_level, fine_level):
        points = [
            _eos_point(project_root, experiment_id, ratio)
            for experiment_id, ratio in zip(ids_by_level[level], EOS_RATIOS)
        ]
        fit, fit_failures = _eos_fit_gate(points)
        series[level] = {"points": points, "fit": fit, "fit_failures": fit_failures}
    coarse = series[coarse_level]
    fine = series[fine_level]
    if coarse["fit_failures"] or fine["fit_failures"]:
        return {
            "material": material,
            "coarse_level": coarse_level,
            "fine_level": fine_level,
            "series": series,
            "accepted": False,
        }
    coarse_points = {
        float(point["volume_ratio"]): point for point in coarse["points"]  # type: ignore[union-attr]
    }
    fine_points = {
        float(point["volume_ratio"]): point for point in fine["points"]  # type: ignore[union-attr]
    }
    coarse_v100 = float(coarse_points[1.0]["e_ec_ev_per_atom"])
    fine_v100 = float(fine_points[1.0]["e_ec_ev_per_atom"])
    anchored = []
    for ratio in EOS_RATIOS:
        difference = abs(
            (float(fine_points[ratio]["e_ec_ev_per_atom"]) - fine_v100)
            - (float(coarse_points[ratio]["e_ec_ev_per_atom"]) - coarse_v100)
        ) * 1000.0
        anchored.append({"volume_ratio": ratio, "difference_mev_per_atom": difference})
    max_energy = max(float(row["difference_mev_per_atom"]) for row in anchored)
    coarse_v0 = float(coarse["fit"]["v0_angstrom3_per_atom"])  # type: ignore[index]
    fine_v0 = float(fine["fit"]["v0_angstrom3_per_atom"])  # type: ignore[index]
    delta_volume = abs(fine_v0 - coarse_v0) / coarse_v0 * 100.0
    return {
        "material": material,
        "coarse_level": coarse_level,
        "fine_level": fine_level,
        "series": series,
        "anchored_rows": anchored,
        "max_anchored_energy_difference_mev_per_atom": max_energy,
        "equilibrium_volume_difference_percent": delta_volume,
        "energy_accepted": max_energy < 2.0,
        "volume_accepted": delta_volume < 0.2,
        "accepted": max_energy < 2.0 and delta_volume < 0.2,
    }


def evaluate_k_gate(
    project_root: Path,
    rows: list[dict[str, str]],
    *,
    require_committed: bool,
) -> dict[str, object]:
    by_id = {row["experiment_id"]: row for row in rows}
    pairs = (
        ("S1-20260806-021", "S1-20260806-035"),
        ("S1-20260806-024", "S1-20260806-036"),
        ("S1-20260806-027", "S1-20260806-037"),
        ("S1-20260806-028", "S1-20260806-038"),
        ("S1-20260806-031", "S1-20260806-039"),
        ("S1-20260806-034", "S1-20260806-040"),
    )
    if len(pairs) != 6 or len(set(pairs)) != 6:
        raise ValueError("registered k gate must contain six unique pairs")
    pair_payloads: list[dict[str, object]] = []
    for common_id, extra_id in pairs:
        common_row = by_id[common_id]
        extra_row = by_id[extra_id]
        common_run = project_root / "runs" / common_id
        extra_run = project_root / "runs" / extra_id
        if require_committed:
            for experiment_id in (common_id, extra_id):
                failure = _tracked_head_failure(
                    project_root, f"runs/{experiment_id}/{EVIDENCE_NAME}"
                )
                if failure:
                    raise ValueError(f"k gate {experiment_id}: {failure}")
        expected, _ = expected_electrons(common_run)
        fields = field_metrics(
            _find_output(common_run, "chg.cube"),
            _find_output(extra_run, "chg.cube"),
            _find_output(common_run, "pot.cube"),
            _find_output(extra_run, "pot.cube"),
            structure_path=common_run / "STRU",
            expected_electron_count=expected,
        )
        common_result = _row_result(project_root, common_row)
        extra_result = _row_result(project_root, extra_row)
        absolute_energy = abs(
            float(extra_result["zero_temp_extrapolated_energy_ev_per_atom"])
            - float(common_result["zero_temp_extrapolated_energy_ev_per_atom"])
        ) * 1000.0
        pressure_difference = abs(
            float(extra_result["pressure_gpa"])
            - float(common_result["pressure_gpa"])
        )
        pair_payloads.append(
            {
                "material": common_row["material"],
                "volume_ratio": float(common_row["volume_ratio"]),
                "common_experiment_id": common_id,
                "extra_experiment_id": extra_id,
                "absolute_energy_difference_mev_per_atom": absolute_energy,
                "absolute_pressure_difference_gpa_diagnostic": pressure_difference,
                "field_metrics": fields,
            }
        )

    material_payloads: dict[str, dict[str, object]] = {}
    for material in ("al", "mg"):
        selected = [pair for pair in pair_payloads if pair["material"] == material]
        if len(selected) != 3 or {
            float(pair["volume_ratio"]) for pair in selected
        } != {0.9, 1.0, 1.1}:
            raise ValueError(f"{material}: k gate must contain three unique anchors")
        at_v100 = next(pair for pair in selected if pair["volume_ratio"] == 1.0)
        common_v100 = _row_result(project_root, by_id[str(at_v100["common_experiment_id"])])
        extra_v100 = _row_result(project_root, by_id[str(at_v100["extra_experiment_id"])])
        anchored: list[dict[str, float]] = []
        for pair in selected:
            common = _row_result(project_root, by_id[str(pair["common_experiment_id"])])
            extra = _row_result(project_root, by_id[str(pair["extra_experiment_id"])])
            value = abs(
                (
                    float(extra["zero_temp_extrapolated_energy_ev_per_atom"])
                    - float(extra_v100["zero_temp_extrapolated_energy_ev_per_atom"])
                )
                - (
                    float(common["zero_temp_extrapolated_energy_ev_per_atom"])
                    - float(common_v100["zero_temp_extrapolated_energy_ev_per_atom"])
                )
            ) * 1000.0
            anchored.append({"volume_ratio": float(pair["volume_ratio"]), "difference": value})
        max_anchored = max(row["difference"] for row in anchored)
        field_passed = all(
            pair["field_metrics"]["accepted"] is True  # type: ignore[index]
            for pair in selected
        )
        v100_abs = float(at_v100["absolute_energy_difference_mev_per_atom"])
        material_payloads[material] = {
            "v100_absolute_energy_difference_mev_per_atom": v100_abs,
            "max_anchored_energy_difference_mev_per_atom": max_anchored,
            "anchored_rows": anchored,
            "field_passed": field_passed,
            "accepted": v100_abs < 2.0 and max_anchored < 2.0 and field_passed,
        }
    return {
        "pair_count": len(pair_payloads),
        "pairs": pair_payloads,
        "materials": material_payloads,
        "accepted": len(pair_payloads) == 6
        and len(material_payloads) == 2
        and all(value["accepted"] is True for value in material_payloads.values()),
    }


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "manifest", nargs="?", type=Path, default=project_root / MANIFEST_PATH
    )
    parser.add_argument("--config", type=Path, default=project_root / CONFIG_PATH)
    parser.add_argument("--require-committed", action="store_true")
    parser.add_argument("--check-run-core")
    parser.add_argument("--check-run")
    parser.add_argument("--check-failure-run")
    parser.add_argument("--check-failure-archives")
    parser.add_argument("--write-run-evidence")
    parser.add_argument("--write-core-failure-evidence", action="store_true")
    parser.add_argument("--require-half-quarter-pair")
    parser.add_argument(
        "--require-adjacent-eos",
        nargs=3,
        metavar=("MATERIAL", "COARSE", "FINE"),
    )
    parser.add_argument("--require-pilot-gate", action="store_true")
    parser.add_argument("--require-k-gate", action="store_true")
    parser.add_argument("--require-all-runs", action="store_true")
    args = parser.parse_args()
    per_run_modes = [
        value
        for value in (
            args.check_run_core,
            args.check_run,
            args.check_failure_run,
            args.check_failure_archives,
            args.write_run_evidence,
        )
        if value
    ]
    if len(per_run_modes) > 1:
        parser.error("select at most one per-run mode")
    if args.write_core_failure_evidence and args.check_run_core is None:
        parser.error("--write-core-failure-evidence requires --check-run-core")
    config, rows, details = validate_registration(
        project_root,
        args.config.resolve(),
        args.manifest.resolve(),
        require_committed=args.require_committed,
    )
    by_id = {row["experiment_id"]: row for row in rows}
    checked: list[str] = []
    if per_run_modes:
        experiment_id = per_run_modes[0]
        if experiment_id not in by_id:
            raise ValueError(f"requested run is outside the R1 manifest: {experiment_id}")
        row = by_id[experiment_id]
        if args.check_failure_archives:
            chain_errors: list[str] = []
            events = _archive_events(project_root, experiment_id, chain_errors)
            if _active_introduction(project_root, experiment_id, chain_errors) is not None:
                chain_errors.append(f"{experiment_id}: active run remains after archive")
            if len(events) != 1:
                chain_errors.append(f"{experiment_id}: expected exactly one no-retry archive")
            failures = chain_errors
            if len(events) == 1:
                archive_directory = (
                    project_root
                    / "failed_runs/runtime_relocation"
                    / experiment_id
                    / events[0][0]
                )
                failures.extend(
                    validate_failed_r1_run(
                        project_root,
                        row,
                        require_committed=args.require_committed,
                        directory=archive_directory,
                    )
                )
            if failures:
                raise ValueError("failed-archive validation failed:\n- " + "\n- ".join(failures))
        elif args.check_failure_run:
            failures = validate_failed_r1_run(
                project_root,
                row,
                require_committed=args.require_committed,
            )
            if failures:
                raise ValueError("failed-run validation failed:\n- " + "\n- ".join(failures))
        else:
            payload, failures = replay_evidence(
                project_root,
                config,
                row,
                require_committed=args.require_committed,
                require_replay_status=args.check_run is not None,
            )
            if failures:
                if args.check_run_core and args.write_core_failure_evidence:
                    _write_exclusive_json(
                        project_root / "runs" / experiment_id / FAILURE_CLASS_NAME,
                        classify_core_failures(experiment_id, failures),
                    )
                raise ValueError("run validation failed:\n- " + "\n- ".join(failures))
            evidence = project_root / "runs" / experiment_id / EVIDENCE_NAME
            if args.write_run_evidence:
                _write_evidence(evidence, payload)
            elif args.check_run_core:
                pass
            elif not evidence.is_file() or evidence.is_symlink():
                raise ValueError(f"missing {EVIDENCE_NAME} for {experiment_id}")
        checked.append(experiment_id)

    half_quarter_pair: dict[str, object] | None = None
    if args.require_half_quarter_pair:
        quarter_id = args.require_half_quarter_pair
        if quarter_id not in COMMON_QUARTER_IDS:
            raise ValueError("--require-half-quarter-pair requires ID 021--034")
        half_id = f"S1-20260806-{int(quarter_id.rsplit('-', 1)[1]) - 14:03d}"
        for experiment_id in (half_id, quarter_id):
            _, failures = replay_evidence(
                project_root,
                config,
                by_id[experiment_id],
                require_committed=True,
                require_replay_status=True,
            )
            if failures:
                raise ValueError(
                    f"half-quarter run validation failed for {experiment_id}:\n- "
                    + "\n- ".join(failures)
                )
        half_quarter_pair = evaluate_half_quarter_pair(
            project_root, rows, quarter_id, require_committed=True
        )
        if half_quarter_pair["accepted"] is not True:
            raise ValueError(
                f"half-quarter field gate rejected for {quarter_id}: "
                + json.dumps(half_quarter_pair, sort_keys=True)
            )

    adjacent_eos: dict[str, object] | None = None
    if args.require_adjacent_eos:
        material, coarse_level, fine_level = args.require_adjacent_eos
        gate_levels = {coarse_level, fine_level} - {"standard"}
        for row in rows:
            role = _role(row)
            level = "half" if role == "half" else (
                "quarter" if role == "common_quarter" else ""
            )
            if row["material"] != material or level not in gate_levels:
                continue
            _, failures = replay_evidence(
                project_root,
                config,
                row,
                require_committed=True,
                require_replay_status=True,
            )
            if failures:
                raise ValueError(
                    f"adjacent-EOS run validation failed for {row['experiment_id']}:\n- "
                    + "\n- ".join(failures)
                )
        adjacent_eos = evaluate_adjacent_eos_gate(
            project_root,
            rows,
            material,
            coarse_level,
            fine_level,
            require_committed=True,
        )
        if adjacent_eos["accepted"] is not True:
            raise ValueError(
                f"adjacent EOS gate rejected: {material}/{coarse_level}->{fine_level}: "
                + json.dumps(adjacent_eos, sort_keys=True)
            )

    pilot_gate: dict[str, object] | None = None
    if args.require_pilot_gate:
        for experiment_id in PILOT_IDS:
            _, failures = replay_evidence(
                project_root,
                config,
                by_id[experiment_id],
                require_committed=True,
                require_replay_status=True,
            )
            if failures:
                raise ValueError(
                    f"pilot-gate run validation failed for {experiment_id}:\n- "
                    + "\n- ".join(failures)
                )
        pilot_gate = evaluate_pilot_gate(project_root, rows, require_committed=True)
        if pilot_gate["accepted"] is not True:
            raise ValueError(
                "registered P0 pilot gate was rejected: "
                + json.dumps(pilot_gate, sort_keys=True)
            )

    k_gate: dict[str, object] | None = None
    if args.require_k_gate:
        for experiment_id in K_GATE_IDS:
            _, failures = replay_evidence(
                project_root,
                config,
                by_id[experiment_id],
                require_committed=True,
                require_replay_status=True,
            )
            if failures:
                raise ValueError(
                    f"k-gate run validation failed for {experiment_id}:\n- "
                    + "\n- ".join(failures)
                )
        k_gate = evaluate_k_gate(project_root, rows, require_committed=True)
        if k_gate["accepted"] is not True:
            raise ValueError(
                "registered low-smearing k gate was rejected: "
                + json.dumps(k_gate, sort_keys=True)
            )

    if args.require_all_runs:
        for experiment_id in RUN_IDS:
            _, failures = replay_evidence(
                project_root,
                config,
                by_id[experiment_id],
                require_committed=True,
                require_replay_status=True,
            )
            if failures:
                raise ValueError(
                    f"all-run validation failed for {experiment_id}:\n- "
                    + "\n- ".join(failures)
                )
            checked.append(experiment_id)
        if k_gate is None:
            k_gate = evaluate_k_gate(project_root, rows, require_committed=True)
        if k_gate["accepted"] is not True:
            raise ValueError(
                "all-run validation found a rejected k gate: "
                + json.dumps(k_gate, sort_keys=True)
            )

    print(
        json.dumps(
            {
                "protocol_revision": PROTOCOL_REVISION,
                "registered_run_count": 40,
                "main_eos_scalar_count": 42,
                "checked_run_ids": checked,
                "half_quarter_pair": half_quarter_pair,
                "adjacent_eos": adjacent_eos,
                "pilot_gate": pilot_gate,
                "k_gate": k_gate,
                "config_sha256": sha256(args.config),
                "manifest_sha256": sha256(args.manifest),
                "preregistration_commit": details["preregistration_commit"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
