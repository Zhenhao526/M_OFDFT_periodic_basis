#!/usr/bin/env python3
"""Shared fail-closed helpers for the immutable S1/G1 continuation R2."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable


PROTOCOL_REVISION = "S1-G1-THREE-LAYER-CONTINUATION-20260810-R2"
CONFIG_PATH = Path("config/S1_g1_three_layer_continuation_r2.json")
MANIFEST_PATH = Path("config/S1_g1_three_layer_continuation_r2_manifest.tsv")
MANIFEST_FIELDS = (
    "execution_index",
    "experiment_id",
    "phase",
    "requirement",
    "material",
    "volume_ratio",
    "role",
    "ecutwfc_ry",
    "ecutrho_ry",
    "kmesh",
    "atom_count",
    "expected_electrons",
    "pseudo_basename",
    "pseudo_sha256",
    "suffix",
)
ID_PATTERN = re.compile(r"S1-20260810-(?:32[7-9]|33[0-4])\Z")
FLOAT_PATTERN = re.compile(
    r"[+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?"
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path | str) -> str:
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), f"not a regular file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_text(path: Path | str) -> str:
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), f"not a regular file: {path}")
    return path.read_text(encoding="utf-8")


def read_json(path: Path | str) -> object:
    return json.loads(read_text(path))


def canonical_json_bytes(payload: object) -> bytes:
    return (json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )


def atomic_write(path: Path, data: bytes, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.close(descriptor)
        fsync_directory(path.parent)
        return
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_if_identical_or_absent(path: Path, data: bytes) -> None:
    if path.exists():
        require(path.is_file() and not path.is_symlink(), f"unsafe existing path: {path}")
        require(path.read_bytes() == data, f"existing generated file differs: {path}")
        return
    atomic_write(path, data, exclusive=True)


def load_config(project_root: Path, path: Path = CONFIG_PATH) -> dict:
    payload = read_json(project_root / path)
    require(isinstance(payload, dict), "config must be an object")
    require(payload.get("protocol_revision") == PROTOCOL_REVISION, "protocol mismatch")
    return payload


def load_manifest(project_root: Path, path: Path = MANIFEST_PATH) -> list[dict[str, str]]:
    text = read_text(project_root / path)
    with io.StringIO(text, newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        require(tuple(reader.fieldnames or ()) == MANIFEST_FIELDS, "manifest header differs")
        rows = list(reader)
    require(len(rows) == 8, "manifest must contain exactly 8 rows")
    ids: list[str] = []
    for expected_index, row in enumerate(rows, start=1):
        require(None not in row and all(value is not None for value in row.values()), "malformed row")
        require(int(row["execution_index"]) == expected_index, "execution index differs")
        experiment_id = row["experiment_id"]
        require(ID_PATTERN.fullmatch(experiment_id) is not None, f"invalid ID: {experiment_id}")
        ids.append(experiment_id)
        require(row["material"] in {"al", "mg"}, "invalid material")
        require(row["phase"] in {"al_eos", "mg_required"}, "invalid phase")
        require(int(row["ecutrho_ry"]) == 4 * int(row["ecutwfc_ry"]), "ecut ratio differs")
        kmesh = parse_kmesh(row["kmesh"])
        require(all(value > 0 for value in kmesh), "invalid kmesh")
        require(float(row["volume_ratio"]) in {0.90, 0.94, 0.97, 1.00, 1.03, 1.06, 1.10}, "invalid volume")
    require(len(set(ids)) == 8, "duplicate experiment ID")
    require(ids == [f"S1-20260810-{number:03d}" for number in range(327, 335)], "ID sequence differs")
    return rows


def parse_kmesh(value: str) -> tuple[int, int, int]:
    fields = value.split("x")
    require(len(fields) == 3, f"invalid kmesh: {value}")
    return tuple(int(field) for field in fields)  # type: ignore[return-value]


def find_project_root(start: Path | None = None) -> Path:
    candidate = (start or Path.cwd()).resolve()
    for path in (candidate, *candidate.parents):
        if (path / ".git").exists() and (path / CONFIG_PATH).is_file():
            return path
    raise ValueError("project root not found")


def git(project_root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=project_root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip()


def require_clean_tree(project_root: Path) -> str:
    status = git(project_root, "status", "--porcelain=v1", "--untracked-files=all")
    require(status == "", f"working tree must be clean, found: {status[:500]}")
    head = git(project_root, "rev-parse", "HEAD")
    require(len(head) == 40, "invalid git HEAD")
    return head


def require_tracked_matches_head(project_root: Path, paths: Iterable[Path | str]) -> None:
    for item in paths:
        relative = Path(item).as_posix()
        require((project_root / relative).is_file(), f"missing registered file: {relative}")
        listed = git(project_root, "ls-files", "--error-unmatch", "--", relative)
        require(listed == relative, f"untracked registered file: {relative}")
        worktree = sha256_file(project_root / relative)
        blob = subprocess.run(
            ["git", "show", f"HEAD:{relative}"],
            cwd=project_root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
        require(worktree == sha256_bytes(blob), f"registered file differs from HEAD: {relative}")


def file_identity(path: Path, *, relative_to: Path | None = None) -> dict[str, object]:
    shown = path.relative_to(relative_to).as_posix() if relative_to is not None else str(path)
    return {"path": shown, "sha256": sha256_file(path), "size_bytes": path.stat().st_size}


def phase_rows(rows: list[dict[str, str]], phase: str) -> list[dict[str, str]]:
    selected = [row for row in rows if row["phase"] == phase]
    expected = {"al_eos": 6, "mg_required": 2}
    require(phase in expected, f"unknown phase: {phase}")
    require(len(selected) == expected[phase], f"phase row count differs: {phase}")
    return selected


def parse_input_values(path: Path) -> dict[str, tuple[str, ...]]:
    values: dict[str, tuple[str, ...]] = {}
    first = True
    for raw in read_text(path).splitlines():
        content = raw.split("#", 1)[0].strip()
        if not content:
            continue
        if first:
            require(content == "INPUT_PARAMETERS", "INPUT header differs")
            first = False
            continue
        fields = content.split()
        key = fields[0].lower()
        require(key not in values, f"duplicate INPUT key: {key}")
        values[key] = tuple(fields[1:])
    require(not first, "empty INPUT")
    return values


def parse_upf_header(path: Path) -> dict[str, object]:
    text = read_text(path)
    version_match = re.search(r'<UPF\s+version="([^"]+)"', text)
    header_match = re.search(r"<PP_HEADER\b(.*?)(?:/>|>)", text, re.DOTALL)
    require(version_match is not None and header_match is not None, "missing UPF2 header")
    attributes = dict(re.findall(r'(\w+)="([^"]*)"', header_match.group(1)))
    required = {"pseudo_type", "functional", "z_valence", "number_of_proj", "core_correction"}
    require(required <= set(attributes), f"UPF header lacks {sorted(required - set(attributes))}")
    beta_blocks = re.findall(r"<PP_BETA\.\d+\b(.*?)>", text, re.DOTALL)
    beta_count = len(beta_blocks)
    beta_angular_momenta: list[int] = []
    for block in beta_blocks:
        angular = re.search(r'angular_momentum="([0-9]+)"', block)
        require(angular is not None, "PP_BETA lacks angular_momentum")
        beta_angular_momenta.append(int(angular.group(1)))
    dij_present = re.search(r"<PP_DIJ\b", text) is not None
    return {
        "upf_version": version_match.group(1),
        "pseudo_type": attributes["pseudo_type"],
        "functional": attributes["functional"],
        "z_valence": float(attributes["z_valence"]),
        "number_of_proj": int(attributes["number_of_proj"]),
        "core_correction": attributes["core_correction"].strip().upper() in {"T", "TRUE", "1"},
        "pp_beta_element_count": beta_count,
        "beta_angular_momenta": beta_angular_momenta,
        "expanded_nonlocal_projectors_per_atom": sum(2 * value + 1 for value in beta_angular_momenta),
        "pp_dij_present": dij_present,
    }


def validate_pseudo(path: Path, material: str, config: dict) -> dict[str, object]:
    expected = config["pseudodojo"]["materials"][material]
    require(sha256_file(path) == expected["sha256"], f"{material} PP SHA differs")
    header = parse_upf_header(path)
    for key in ("upf_version", "pseudo_type", "functional", "core_correction"):
        require(header[key] == expected[key], f"{material} PP {key} differs")
    require(abs(header["z_valence"] - float(expected["z_valence"])) < 1e-12, "zval differs")
    require(header["number_of_proj"] == int(expected["number_of_proj_per_atom"]), "nproj differs")
    require(
        header["expanded_nonlocal_projectors_per_atom"]
        == int(expected["expanded_nonlocal_projectors_per_atom"]),
        "expanded nonlocal projector count differs",
    )
    require(header["pp_beta_element_count"] == header["number_of_proj"], "PP_BETA count differs")
    require(bool(header["pp_dij_present"]), "PP_DIJ missing")
    return {"basename": path.name, "sha256": sha256_file(path), **header}
