#!/usr/bin/env python3
"""Shared fail-closed helpers for the Al-domain three-layer follow-up."""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from s1_g1_three_layer_common import (  # re-use the preregistered UPF/runtime primitives
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


PROTOCOL_REVISION = "S1-G1-THREE-LAYER-AL-DOMAIN-FOLLOWUP-20260810-R1"
CONFIG_PATH = Path("config/S1_g1_three_layer_al_domain_followup_r1.json")
MANIFEST_PATH = Path("config/S1_g1_three_layer_al_domain_followup_r1_manifest.tsv")
PROTOCOL_PATH = Path("docs/S1_G1_THREE_LAYER_AL_DOMAIN_FOLLOWUP_R1_PROTOCOL.md")
ID_SEQUENCE = tuple(f"S1-20260810-{number:03d}" for number in range(319, 327))
MANIFEST_FIELDS = (
    "execution_index",
    "experiment_id",
    "phase",
    "role",
    "geometry_source_id",
    "geometry_source_path",
    "geometry_source_stru_sha256",
    "geometry_payload_sha256",
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
    raise ValueError("Al follow-up project root not found")


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
    return rows


def geometry_payload_bytes(stru_bytes: bytes) -> bytes:
    marker = b"LATTICE_CONSTANT\n"
    require(stru_bytes.count(marker) == 1, "STRU must contain one LATTICE_CONSTANT marker")
    return marker + stru_bytes.split(marker, 1)[1]


def replace_species_for_nlpp(source: bytes) -> bytes:
    old = b"Al 26.9815385 al.gga.psp blps\n"
    new = b"Al 26.9815385 Al_std.upf upf201\n"
    require(source.count(old) == 1, "strain source ATOMIC_SPECIES row differs")
    output = source.replace(old, new, 1)
    require(geometry_payload_bytes(output) == geometry_payload_bytes(source), "geometry payload changed")
    return output


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
    "CONFIG_PATH",
    "ID_SEQUENCE",
    "MANIFEST_PATH",
    "PROTOCOL_PATH",
    "PROTOCOL_REVISION",
    "atomic_write",
    "canonical_json_bytes",
    "file_identity",
    "find_project_root",
    "geometry_payload_bytes",
    "git",
    "load_config",
    "load_manifest",
    "parse_cpu_list",
    "parse_input_values",
    "parse_kmesh",
    "protocol_implementation_commit",
    "read_json",
    "read_text",
    "replace_species_for_nlpp",
    "require",
    "require_clean_tree",
    "require_tracked_matches_head",
    "sha256_bytes",
    "sha256_file",
    "validate_pseudo",
    "write_if_identical_or_absent",
]
