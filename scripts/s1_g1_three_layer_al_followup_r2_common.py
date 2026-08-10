#!/usr/bin/env python3
"""Shared fail-closed helpers for the Al-domain follow-up R2."""

from __future__ import annotations

import csv
import io
import math
import re
from pathlib import Path

from s1_g1_three_layer_common import (
    atomic_write,
    canonical_json_bytes,
    file_identity,
    git,
    parse_input_values,
    parse_kmesh,
    read_json,
    read_text,
    require,
    require_clean_tree,
    require_tracked_matches_head,
    sha256_bytes,
    sha256_file,
    validate_pseudo,
    write_if_identical_or_absent,
)


PROTOCOL_REVISION = "S1-G1-THREE-LAYER-AL-DOMAIN-FOLLOWUP-20260810-R2"
CONFIG_PATH = Path("config/S1_g1_three_layer_al_domain_followup_r2.json")
MANIFEST_PATH = Path("config/S1_g1_three_layer_al_domain_followup_r2_manifest.tsv")
PROTOCOL_PATH = Path("docs/S1_G1_THREE_LAYER_AL_DOMAIN_FOLLOWUP_R2_PROTOCOL.md")
ID_SEQUENCE = tuple(f"S1-20260810-{number:03d}" for number in range(335, 343))
STRAIN_IDS = ID_SEQUENCE[:4]
ENDPOINT_IDS = ID_SEQUENCE[4:]
MANIFEST_FIELDS = (
    "execution_index",
    "experiment_id",
    "phase",
    "role",
    "construction_base_id",
    "construction_base_path",
    "construction_base_stru_sha256",
    "registered_geometry_id",
    "registered_geometry_path",
    "registered_geometry_stru_sha256",
    "geometry_payload_sha256",
    "accepted_common_id",
    "volume_ratio",
    "ecutwfc_ry",
    "ecutrho_ry",
    "kmesh",
    "atom_count",
    "expected_electrons",
    "pseudo_basename",
    "pseudo_sha256",
    "suffix",
)


def find_project_root(start: Path | None = None) -> Path:
    candidate = (start or Path.cwd()).resolve()
    for path in (candidate, *candidate.parents):
        if (path / ".git").exists() and (path / CONFIG_PATH).is_file():
            return path
    raise ValueError("Al follow-up R2 project root not found")


def load_config(project_root: Path) -> dict:
    payload = read_json(project_root / CONFIG_PATH)
    require(isinstance(payload, dict), "config must be an object")
    require(payload.get("protocol_revision") == PROTOCOL_REVISION, "protocol revision differs")
    require(payload.get("formal_ids") == list(ID_SEQUENCE), "formal ID list differs")
    return payload


def load_manifest(project_root: Path) -> list[dict[str, str]]:
    with io.StringIO(read_text(project_root / MANIFEST_PATH), newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        require(tuple(reader.fieldnames or ()) == MANIFEST_FIELDS, "manifest header differs")
        rows = list(reader)
    require(len(rows) == len(ID_SEQUENCE), "manifest must contain exactly eight rows")
    ids: list[str] = []
    for expected_index, row in enumerate(rows, 1):
        require(None not in row and all(value is not None for value in row.values()), "malformed manifest row")
        require(int(row["execution_index"]) == expected_index, "execution index differs")
        ids.append(row["experiment_id"])
        require(row["phase"] in {"strain", "endpoint"}, "invalid phase")
        require(int(row["atom_count"]) == 1, "Al primitive cell must contain one atom")
        require(abs(float(row["expected_electrons"]) - 3.0) < 1e-12, "expected electrons differs")
        require(int(row["ecutrho_ry"]) == 4 * int(row["ecutwfc_ry"]), "ecutrho/ecutwfc differs")
        require(parse_kmesh(row["kmesh"]) in {(28, 28, 28), (32, 32, 32)}, "unexpected k mesh")
        require(row["pseudo_basename"] == "Al_std.upf", "pseudo basename differs")
    require(tuple(ids) == ID_SEQUENCE and len(set(ids)) == 8, "formal ID sequence differs")
    require([row["phase"] for row in rows] == ["strain"] * 4 + ["endpoint"] * 4, "phase order differs")
    require([row["accepted_common_id"] for row in rows] == [""] * 4 + ["S1-20260810-327"] * 2 + ["S1-20260810-328"] * 2, "continuation common-ID mapping differs")
    return rows


def geometry_payload_bytes(stru_bytes: bytes) -> bytes:
    marker = b"LATTICE_CONSTANT\n"
    require(stru_bytes.count(marker) == 1, "STRU must contain one LATTICE_CONSTANT marker")
    return marker + stru_bytes.split(marker, 1)[1]


def atomic_positions_payload_bytes(stru_bytes: bytes) -> bytes:
    marker = b"ATOMIC_POSITIONS\n"
    require(stru_bytes.count(marker) == 1, "STRU must contain one ATOMIC_POSITIONS marker")
    return marker + stru_bytes.split(marker, 1)[1]


def replace_species_for_nlpp(source: bytes) -> bytes:
    old = b"Al 26.9815385 al.gga.psp blps\n"
    new = b"Al 26.9815385 Al_std.upf upf201\n"
    require(source.count(old) == 1, "construction base ATOMIC_SPECIES row differs")
    output = source.replace(old, new, 1)
    require(geometry_payload_bytes(output) == geometry_payload_bytes(source), "species replacement changed geometry")
    return output


def determinant3(matrix: list[list[float]]) -> float:
    require(len(matrix) == 3 and all(len(row) == 3 for row in matrix), "matrix must be 3x3")
    return (
        matrix[0][0] * (matrix[1][1] * matrix[2][2] - matrix[1][2] * matrix[2][1])
        - matrix[0][1] * (matrix[1][0] * matrix[2][2] - matrix[1][2] * matrix[2][0])
        + matrix[0][2] * (matrix[1][0] * matrix[2][1] - matrix[1][1] * matrix[2][0])
    )


def matrix_a_times_f_transpose(a: list[list[float]], f: list[list[float]]) -> list[list[float]]:
    require(len(a) == len(f) == 3 and all(len(row) == 3 for row in (*a, *f)), "A and F must be 3x3")
    return [[math.fsum(a[i][k] * f[j][k] for k in range(3)) for j in range(3)] for i in range(3)]


def stru_lattice_vectors(stru_bytes: bytes) -> list[list[float]]:
    text = stru_bytes.decode("utf-8")
    marker = "LATTICE_VECTORS\n"
    require(text.count(marker) == 1, "STRU must contain one LATTICE_VECTORS marker")
    rows = text.split(marker, 1)[1].splitlines()[:3]
    require(len(rows) == 3, "STRU lattice row count differs")
    matrix = [[float(value) for value in row.split()] for row in rows]
    require(all(len(row) == 3 and all(math.isfinite(value) for value in row) for row in matrix), "invalid STRU lattice")
    return matrix


def render_strain_from_base(base_bytes: bytes, deformation_gradient: list[list[float]]) -> bytes:
    """Construct A'=A F^T from the 043 base while retaining Direct bytes."""
    require(abs(determinant3(deformation_gradient) - 1.0) < 1e-12, "strain F must be isochoric")
    base = replace_species_for_nlpp(base_bytes)
    base_lattice = stru_lattice_vectors(base)
    derived_lattice = matrix_a_times_f_transpose(base_lattice, deformation_gradient)
    text = base.decode("utf-8")
    marker = "LATTICE_VECTORS\n"
    prefix, suffix = text.split(marker, 1)
    lines = suffix.splitlines(keepends=True)
    require(len(lines) >= 3 and all(line.endswith("\n") for line in lines[:3]), "base lattice layout differs")
    new_rows = [" ".join(f"{value:.16f}" for value in row) + "\n" for row in derived_lattice]
    output = (prefix + marker + "".join(new_rows + lines[3:])).encode("utf-8")
    require(atomic_positions_payload_bytes(output) == atomic_positions_payload_bytes(base), "Direct coordinates changed during construction")
    return output


def verify_strain_geometry(
    base_bytes: bytes,
    derived_bytes: bytes,
    registered_reference_bytes: bytes,
    deformation_gradient: list[list[float]],
) -> dict:
    """Independently rebuild physical strain; do not trust source metadata."""
    base_lattice = stru_lattice_vectors(base_bytes)
    derived_lattice = stru_lattice_vectors(derived_bytes)
    expected = matrix_a_times_f_transpose(base_lattice, deformation_gradient)
    det_f = determinant3(deformation_gradient)
    matrix_error = max(abs(derived_lattice[i][j] - expected[i][j]) for i in range(3) for j in range(3))
    direct_unchanged = atomic_positions_payload_bytes(derived_bytes) == atomic_positions_payload_bytes(base_bytes)
    reference_payload_equal = geometry_payload_bytes(derived_bytes) == geometry_payload_bytes(registered_reference_bytes)
    require(abs(det_f - 1.0) < 1e-12, "reconstructed F determinant differs from one")
    require(matrix_error < 5e-15, "derived A differs from independently reconstructed A F^T")
    require(direct_unchanged, "Direct coordinate bytes changed")
    require(reference_payload_equal, "independently constructed geometry differs from registered 204-207 reference")
    return {
        "convention": "lattice row matrix A; A_prime=A@F.T; Direct coordinates unchanged",
        "deformation_gradient": deformation_gradient,
        "determinant_f": det_f,
        "maximum_lattice_matrix_error": matrix_error,
        "direct_coordinate_bytes_unchanged": direct_unchanged,
        "registered_geometry_payload_byte_identical": reference_payload_equal,
        "accepted": True,
    }


def parse_cpu_list(value: str) -> set[int]:
    cpus: set[int] = set()
    for field in value.strip().split(","):
        if not field:
            continue
        if "-" in field:
            start, stop = (int(item) for item in field.split("-", 1))
            require(start <= stop, "invalid CPU range")
            cpus.update(range(start, stop + 1))
        else:
            cpus.add(int(field))
    return cpus


def protocol_implementation_commit(project_root: Path) -> str:
    text = read_text(project_root / PROTOCOL_PATH)
    matches = re.findall(r"实现提交：`([0-9a-f]{40})`", text)
    require(len(matches) == 1, "protocol implementation commit is not frozen")
    return matches[0]


__all__ = [
    "CONFIG_PATH", "ENDPOINT_IDS", "ID_SEQUENCE", "MANIFEST_PATH", "PROTOCOL_PATH",
    "PROTOCOL_REVISION", "STRAIN_IDS", "atomic_positions_payload_bytes", "atomic_write",
    "canonical_json_bytes", "determinant3", "file_identity", "find_project_root", "geometry_payload_bytes",
    "git", "load_config", "load_manifest", "matrix_a_times_f_transpose", "parse_cpu_list",
    "parse_input_values", "parse_kmesh", "protocol_implementation_commit", "read_json", "read_text",
    "render_strain_from_base", "replace_species_for_nlpp", "require", "require_clean_tree",
    "require_tracked_matches_head", "sha256_bytes", "sha256_file", "stru_lattice_vectors",
    "validate_pseudo", "verify_strain_geometry", "write_if_identical_or_absent",
]
