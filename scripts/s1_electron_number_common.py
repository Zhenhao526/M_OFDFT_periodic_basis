#!/usr/bin/env python3
"""Shared, fail-closed helpers for the S1 electron-number audit."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import struct
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Iterable


PROTOCOL_REVISION = "S1-G1-ELECTRON-NUMBER-R1"
RELATIVE_ERROR_LIMIT = 1.0e-10
ENERGY_LIMIT_MEV_PER_ATOM = 0.1
PRESSURE_LIMIT_GPA = 0.02
CUBE_PRECISION = 17
PRIMARY_IDS = tuple(f"S1-20260805-{value:03d}" for value in range(29, 113))
SUPPLEMENTAL_IDS = tuple(f"S1-20260805-{value:03d}" for value in range(113, 119))
TARGET_IDS = PRIMARY_IDS + SUPPLEMENTAL_IDS
PILOT_SOURCE_IDS = ("S1-20260805-113", "S1-20260805-116")
AUDIT_IDS = tuple(f"S1-20260805-{value:03d}" for value in range(119, 149))

MANIFEST_FIELDS = (
    "source_experiment_id",
    "scope",
    "material",
    "series_id",
    "solver",
    "volume_ratio",
    "expected_electrons",
    "density_mode",
    "density_path",
    "source_density_sha256",
    "audit_experiment_id",
    "input_directory",
    "derived_suffix",
    "input_sha256",
    "stru_sha256",
    "kpt_sha256",
    "metadata_sha256",
    "pseudopotential",
    "pseudopotential_sha256",
    "reference_experiment_id",
    "reference_result_path",
    "reference_result_sha256",
    "reference_log_path",
    "reference_log_sha256",
)

_FLOAT_RE = re.compile(
    r"^[+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?$"
)
_SCIENTIFIC_RE = re.compile(
    r"^[+-]?(?:(\d+)(?:\.(\d*))?|\.(\d+))[eE]([+-]?\d+)$"
)
_FFT_GRID_RE = re.compile(
    r"^\s*FFT grid for charge/potential\s*=\s*"
    r"\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]\s*$"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ordered_of_replay_sources(solver_by_source: dict[str, str]) -> tuple[str, ...]:
    """Return the frozen execution mapping, independent of manifest row order."""

    missing = [experiment_id for experiment_id in TARGET_IDS if experiment_id not in solver_by_source]
    if missing:
        raise ValueError(f"missing target solver registrations: {missing}")
    if any(solver_by_source[experiment_id] != "ofdft" for experiment_id in PILOT_SOURCE_IDS):
        raise ValueError("both frozen pilot sources must be OFDFT")
    output = (*PILOT_SOURCE_IDS, *(
        experiment_id
        for experiment_id in PRIMARY_IDS
        if solver_by_source[experiment_id] == "ofdft"
    ))
    if len(output) != len(AUDIT_IDS) or len(set(output)) != len(output):
        raise ValueError("unexpected OF replay source count or duplicate")
    return output


def read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return payload


def read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if tuple(reader.fieldnames or ()) != MANIFEST_FIELDS:
            raise ValueError(f"unexpected electron-audit manifest header: {path}")
        rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError(f"malformed electron-audit manifest row: {path}")
    return rows


def write_manifest(path: Path, rows: Iterable[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=MANIFEST_FIELDS, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in MANIFEST_FIELDS})


def determinant(matrix: list[list[float]]) -> float:
    if len(matrix) != 3 or any(len(row) != 3 for row in matrix):
        raise ValueError("expected a 3x3 matrix")
    return (
        matrix[0][0] * (matrix[1][1] * matrix[2][2] - matrix[1][2] * matrix[2][1])
        - matrix[0][1]
        * (matrix[1][0] * matrix[2][2] - matrix[1][2] * matrix[2][0])
        + matrix[0][2]
        * (matrix[1][0] * matrix[2][1] - matrix[1][1] * matrix[2][0])
    )


def _nonempty_lines(path: Path) -> list[str]:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"expected one regular, non-symbolic file: {path}")
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _section_index(lines: list[str], name: str) -> int:
    matches = [index for index, value in enumerate(lines) if value == name]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {name} section")
    return matches[0]


@dataclass(frozen=True)
class StructureInfo:
    lattice_constant_bohr: float
    lattice_vectors: list[list[float]]
    volume_bohr3: float
    volume_fraction: Fraction
    species_pseudopotentials: dict[str, str]
    species_counts: dict[str, int]


def parse_stru(path: Path) -> StructureInfo:
    lines = _nonempty_lines(path)
    species_index = _section_index(lines, "ATOMIC_SPECIES")
    lattice_constant_index = _section_index(lines, "LATTICE_CONSTANT")
    lattice_vectors_index = _section_index(lines, "LATTICE_VECTORS")
    positions_index = _section_index(lines, "ATOMIC_POSITIONS")
    if not (
        species_index < lattice_constant_index < lattice_vectors_index < positions_index
    ):
        raise ValueError("unexpected STRU section order")

    species_pseudopotentials: dict[str, str] = {}
    for line in lines[species_index + 1 : lattice_constant_index]:
        fields = line.split()
        if len(fields) < 3 or fields[0] in species_pseudopotentials:
            raise ValueError(f"invalid ATOMIC_SPECIES row: {line}")
        species_pseudopotentials[fields[0]] = fields[2]
    if not species_pseudopotentials:
        raise ValueError("STRU contains no atomic species")

    try:
        lattice_token = lines[lattice_constant_index + 1].split()[0]
        vector_tokens = [
            lines[lattice_vectors_index + offset].split() for offset in (1, 2, 3)
        ]
        lattice_constant = float(lattice_token)
        vectors = [[float(value) for value in row] for row in vector_tokens]
        lattice_fraction = Fraction(lattice_token)
        vector_fractions = [[Fraction(value) for value in row] for row in vector_tokens]
    except (IndexError, ValueError) as error:
        raise ValueError(f"invalid lattice in {path}: {error}") from error
    if not math.isfinite(lattice_constant) or lattice_constant <= 0:
        raise ValueError("invalid LATTICE_CONSTANT")
    if any(len(row) != 3 or not all(math.isfinite(value) for value in row) for row in vectors):
        raise ValueError("invalid LATTICE_VECTORS")
    volume_fraction = abs(determinant(vector_fractions)) * lattice_fraction**3
    volume = float(volume_fraction)
    if not math.isfinite(volume) or volume <= 0:
        raise ValueError("non-positive cell volume")

    cursor = positions_index + 2  # skip coordinate-mode line
    species_counts: dict[str, int] = {}
    while cursor < len(lines):
        label = lines[cursor].split()[0]
        cursor += 1
        if label not in species_pseudopotentials or label in species_counts:
            raise ValueError(f"invalid or duplicate ATOMIC_POSITIONS species: {label}")
        if cursor + 1 >= len(lines):
            raise ValueError(f"truncated ATOMIC_POSITIONS block for {label}")
        cursor += 1  # species magnetization line
        try:
            count = int(lines[cursor].split()[0])
        except ValueError as error:
            raise ValueError(f"invalid atom count for {label}") from error
        cursor += 1
        if count <= 0 or cursor + count > len(lines):
            raise ValueError(f"invalid coordinate count for {label}")
        for coordinate in lines[cursor : cursor + count]:
            values = coordinate.split()
            if len(values) < 3:
                raise ValueError(f"invalid coordinate row for {label}")
            try:
                xyz = [float(value) for value in values[:3]]
            except ValueError as error:
                raise ValueError(f"invalid coordinate row for {label}") from error
            if not all(math.isfinite(value) for value in xyz):
                raise ValueError(f"non-finite coordinate row for {label}")
        cursor += count
        species_counts[label] = count
    if set(species_counts) != set(species_pseudopotentials):
        raise ValueError("ATOMIC_SPECIES and ATOMIC_POSITIONS labels differ")
    return StructureInfo(
        lattice_constant_bohr=lattice_constant,
        lattice_vectors=vectors,
        volume_bohr3=volume,
        volume_fraction=volume_fraction,
        species_pseudopotentials=species_pseudopotentials,
        species_counts=species_counts,
    )


def pseudopotential_zion_fraction(path: Path) -> Fraction:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"expected one regular, non-symbolic pseudopotential: {path}")
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        lowered = line.lower()
        if "zatom" not in lowered or "zion" not in lowered:
            continue
        fields = line.split()
        if len(fields) < 2:
            break
        try:
            zion_fraction = Fraction(fields[1])
            zion = float(zion_fraction)
        except ValueError as error:
            raise ValueError(f"invalid zion row in {path}") from error
        if not math.isfinite(zion) or zion <= 0:
            raise ValueError(f"invalid zion in {path}")
        return zion_fraction
    raise ValueError(f"cannot find zatom/zion row in {path}")


def pseudopotential_zion(path: Path) -> float:
    return float(pseudopotential_zion_fraction(path))


def expected_electrons(run_directory: Path) -> tuple[float, dict[str, object]]:
    structure = parse_stru(run_directory / "STRU")
    components: dict[str, object] = {}
    total_fraction = Fraction(0)
    for label, count in sorted(structure.species_counts.items()):
        pseudo_name = structure.species_pseudopotentials[label]
        if Path(pseudo_name).name != pseudo_name:
            raise ValueError(f"pseudopotential name is not a basename: {pseudo_name}")
        pseudo_path = run_directory / pseudo_name
        zion_fraction = pseudopotential_zion_fraction(pseudo_path)
        zion = float(zion_fraction)
        contribution_fraction = count * zion_fraction
        contribution = float(contribution_fraction)
        total_fraction += contribution_fraction
        components[label] = {
            "atom_count": count,
            "pseudopotential": pseudo_name,
            "pseudopotential_sha256": sha256(pseudo_path),
            "zion": zion,
            "electron_contribution": contribution,
        }
    total = float(total_fraction)
    if not math.isfinite(total) or total_fraction <= 0:
        raise ValueError("invalid expected electron count")
    return total, {
        "cell_volume_bohr3": structure.volume_bohr3,
        "cell_volume_exact_fraction": {
            "numerator": structure.volume_fraction.numerator,
            "denominator": structure.volume_fraction.denominator,
        },
        "expected_electrons_exact_fraction": {
            "numerator": total_fraction.numerator,
            "denominator": total_fraction.denominator,
        },
        "components": components,
        "derivation": "sum_STRU_atom_count_times_local_pseudopotential_zion",
    }


def _input_key(line: str) -> str:
    content = line.split("#", 1)[0].strip()
    return content.split(maxsplit=1)[0].lower() if content else ""


def normalized_run_input(source: bytes) -> bytes:
    lines = source.decode("utf-8").splitlines(keepends=True)
    output: list[str] = []
    found = 0
    for line in lines:
        if _input_key(line) == "pseudo_dir":
            newline = "\n" if line.endswith("\n") else ""
            output.append("pseudo_dir ." + newline)
            found += 1
        else:
            output.append(line)
    if found != 1:
        raise ValueError("INPUT must contain exactly one pseudo_dir")
    return "".join(output).encode("utf-8")


def derive_output_input(source: bytes, suffix: str) -> bytes:
    lines = source.decode("utf-8").splitlines(keepends=True)
    if any(_input_key(line) == "out_chg" for line in lines):
        raise ValueError("source INPUT already contains out_chg")
    output: list[str] = []
    suffix_count = 0
    for line in lines:
        if _input_key(line) == "suffix":
            newline = "\n" if line.endswith("\n") else ""
            output.append(f"suffix {suffix}{newline}")
            output.append(f"out_chg 1 {CUBE_PRECISION}{newline}")
            suffix_count += 1
        else:
            output.append(line)
    if suffix_count != 1:
        raise ValueError("source INPUT must contain exactly one suffix")
    return "".join(output).encode("utf-8")


def parse_input_parameters(path: Path) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line == "INPUT_PARAMETERS":
            continue
        fields = line.split()
        key = fields[0].lower()
        if key in values:
            raise ValueError(f"duplicate INPUT key: {key}")
        values[key] = fields[1:]
    return values


def find_single_log(run_directory: Path) -> Path:
    logs = sorted(run_directory.glob("OUT.*/running_scf.log"))
    if len(logs) != 1 or logs[0].is_symlink() or not logs[0].is_file():
        raise ValueError(f"expected exactly one regular running_scf.log in {run_directory}")
    return logs[0]


def find_single_density(run_directory: Path, pattern: str) -> Path:
    files = sorted(run_directory.glob(f"OUT.*/{pattern}"))
    if len(files) != 1 or files[0].is_symlink() or not files[0].is_file():
        raise ValueError(f"expected exactly one {pattern} in {run_directory}")
    return files[0]


def parse_charge_grid(log_path: Path) -> tuple[int, int, int]:
    if not log_path.is_file() or log_path.is_symlink():
        raise ValueError("raw ABACUS log is not a regular, non-symbolic file")
    matches = [
        tuple(int(value) for value in match.groups())
        for line in log_path.read_text(encoding="utf-8", errors="strict").splitlines()
        if (match := _FFT_GRID_RE.fullmatch(line)) is not None
    ]
    if len(matches) != 1 or any(value <= 0 for value in matches[0]):
        raise ValueError("raw ABACUS log must contain exactly one positive charge FFT grid")
    return matches[0]


def _take(data: bytes, offset: int, fmt: str) -> tuple[tuple[object, ...], int]:
    size = struct.calcsize("<" + fmt)
    if offset + size > len(data):
        raise ValueError("truncated reciprocal-density restart")
    return struct.unpack_from("<" + fmt, data, offset), offset + size


def integrate_reciprocal_restart(
    density_path: Path, structure_path: Path, expected: float
) -> dict[str, object]:
    if not density_path.is_file() or density_path.is_symlink():
        raise ValueError("reciprocal-density restart is not a regular, non-symbolic file")
    if not math.isfinite(expected) or expected <= 0:
        raise ValueError("expected electron count must be finite and positive")
    data = density_path.read_bytes()
    offset = 0
    header, offset = _take(data, offset, "5i")
    marker, gamma_only, plane_wave_count, spin_count, closing_marker = header
    if marker != 3 or closing_marker != 3:
        raise ValueError("invalid reciprocal-density header markers")
    if gamma_only not in (0, 1) or plane_wave_count <= 0 or spin_count != 1:
        raise ValueError("invalid reciprocal-density header values")
    marker9, offset = _take(data, offset, "i")
    reciprocal, offset = _take(data, offset, "9d")
    marker9_end, offset = _take(data, offset, "i")
    if marker9 != (9,) or marker9_end != (9,) or not all(math.isfinite(v) for v in reciprocal):
        raise ValueError("invalid reciprocal-lattice record")
    miller_marker, offset = _take(data, offset, "i")
    if miller_marker != (3 * plane_wave_count,):
        raise ValueError("invalid Miller-index record marker")
    miller_flat, offset = _take(data, offset, f"{3 * plane_wave_count}i")
    miller_end, offset = _take(data, offset, "i")
    if miller_end != miller_marker:
        raise ValueError("Miller-index record markers differ")
    zero_indices = [
        index
        for index in range(plane_wave_count)
        if miller_flat[3 * index : 3 * index + 3] == (0, 0, 0)
    ]
    if len(zero_indices) != 1:
        raise ValueError("reciprocal density must contain exactly one G=0")
    zero_index = zero_indices[0]
    zero_values: list[tuple[float, float]] = []
    for _ in range(spin_count):
        rho_marker, offset = _take(data, offset, "i")
        if rho_marker != (plane_wave_count,):
            raise ValueError("invalid reciprocal-density value marker")
        values, offset = _take(data, offset, f"{2 * plane_wave_count}d")
        rho_end, offset = _take(data, offset, "i")
        if rho_end != rho_marker:
            raise ValueError("reciprocal-density value markers differ")
        real = float(values[2 * zero_index])
        imaginary = float(values[2 * zero_index + 1])
        if not math.isfinite(real) or not math.isfinite(imaginary):
            raise ValueError("non-finite G=0 coefficient")
        if abs(imaginary) > 1.0e-13 * max(1.0, abs(real)):
            raise ValueError("G=0 coefficient has a non-negligible imaginary part")
        zero_values.append((real, imaginary))
    if offset != len(data):
        raise ValueError("reciprocal-density restart has trailing bytes")

    structure = parse_stru(structure_path)
    integrated_fraction = (
        sum((Fraction.from_float(value[0]) for value in zero_values), Fraction(0))
        * structure.volume_fraction
    )
    expected_fraction = Fraction(str(expected))
    absolute_error_fraction = abs(integrated_fraction - expected_fraction)
    relative_error_fraction = absolute_error_fraction / expected_fraction
    integrated = float(integrated_fraction)
    relative_error = float(relative_error_fraction)
    certified_relative_error = relative_error
    return {
        "density_format": "abacus_reciprocal_restart_little_endian",
        "density_path": str(density_path),
        "density_sha256": sha256(density_path),
        "file_size_bytes": len(data),
        "gamma_only": bool(gamma_only),
        "plane_wave_count": plane_wave_count,
        "spin_count": spin_count,
        "g0_coefficients": [
            {"real": real, "imaginary": imaginary} for real, imaginary in zero_values
        ],
        "cell_volume_bohr3": structure.volume_bohr3,
        "integrated_electrons": integrated,
        "expected_electrons": expected,
        "absolute_error_electrons": float(absolute_error_fraction),
        "relative_error": relative_error,
        "numerical_absolute_error_bound": 0.0,
        "numerical_certification": "exact_fraction_from_binary64_G0_and_decimal_STRU",
        "certified_relative_error": certified_relative_error,
        "acceptance_limit": RELATIVE_ERROR_LIMIT,
        "accepted": relative_error_fraction < Fraction(1, 10**10),
    }


def _scientific_integer_and_exponent(token: str) -> tuple[int, int]:
    match = _SCIENTIFIC_RE.fullmatch(token)
    if match is None:
        raise ValueError(f"cube density token is not scientific notation: {token}")
    fractional = match.group(2) if match.group(2) is not None else match.group(3) or ""
    whole = match.group(1) or "0"
    sign = -1 if token.startswith("-") else 1
    coefficient = sign * int(whole + fractional)
    exponent = int(match.group(4)) - len(fractional)
    return coefficient, exponent


def _power_of_ten_fraction(exponent: int) -> Fraction:
    return Fraction(10**exponent, 1) if exponent >= 0 else Fraction(1, 10 ** (-exponent))


def _exact_scientific_sum_and_rounding_bound(tokens: list[str]) -> tuple[Fraction, Fraction]:
    parsed = [_scientific_integer_and_exponent(token) for token in tokens]
    minimum_exponent = min(exponent for _, exponent in parsed)
    scaled_sum = sum(
        coefficient * 10 ** (exponent - minimum_exponent)
        for coefficient, exponent in parsed
    )
    represented_sum = Fraction(scaled_sum) * _power_of_ten_fraction(minimum_exponent)
    exponent_counts: dict[int, int] = {}
    for _, exponent in parsed:
        exponent_counts[exponent] = exponent_counts.get(exponent, 0) + 1
    rounding_bound = sum(
        (Fraction(count, 2) * _power_of_ten_fraction(exponent)
         for exponent, count in exponent_counts.items()),
        Fraction(0),
    )
    return represented_sum, rounding_bound


def integrate_cube(
    density_path: Path,
    structure_path: Path,
    expected: float,
    expected_grid_dimensions: tuple[int, int, int],
) -> dict[str, object]:
    if not density_path.is_file() or density_path.is_symlink():
        raise ValueError("cube density is not a regular, non-symbolic file")
    if not math.isfinite(expected) or expected <= 0:
        raise ValueError("expected electron count must be finite and positive")
    lines = density_path.read_text(encoding="utf-8", errors="strict").splitlines()
    if len(lines) < 7 or "Cubefile created from ABACUS" not in lines[0]:
        raise ValueError("invalid ABACUS cube header")
    try:
        spin_count = int(lines[1].split()[0])
        origin_fields = lines[2].split()
        if len(origin_fields) != 4:
            raise ValueError("cube origin row must contain exactly four fields")
        atom_count = int(origin_fields[0])
        origin = [float(value) for value in origin_fields[1:]]
        axes = []
        axis_fractions = []
        dimensions = []
        for index in (3, 4, 5):
            fields = lines[index].split()
            if len(fields) != 4:
                raise ValueError("cube grid row must contain exactly four fields")
            dimensions.append(int(fields[0]))
            axes.append([float(value) for value in fields[1:4]])
            axis_fractions.append([Fraction(value) for value in fields[1:4]])
    except (IndexError, ValueError) as error:
        raise ValueError(f"invalid cube header: {error}") from error
    if (
        spin_count != 1
        or atom_count <= 0
        or any(value <= 0 for value in dimensions)
        or not all(math.isfinite(value) for value in origin)
    ):
        raise ValueError("unexpected spin, atom, or grid count in cube")
    if any(len(row) != 3 or not all(math.isfinite(v) for v in row) for row in axes):
        raise ValueError("invalid cube grid axes")
    structure = parse_stru(structure_path)
    if atom_count != sum(structure.species_counts.values()):
        raise ValueError("cube atom count differs from STRU")
    if tuple(dimensions) != expected_grid_dimensions:
        raise ValueError("cube grid dimensions differ from the raw ABACUS log")
    for atom_row in lines[6 : 6 + atom_count]:
        fields = atom_row.split()
        if len(fields) != 5 or not all(_FLOAT_RE.fullmatch(value) for value in fields):
            raise ValueError("invalid cube atom row")
        if not all(math.isfinite(float(value)) for value in fields):
            raise ValueError("cube atom row contains a non-finite value")
    value_tokens = " ".join(lines[6 + atom_count :]).split()
    grid_count = math.prod(dimensions)
    if len(value_tokens) != grid_count:
        raise ValueError(
            f"cube density count differs: observed {len(value_tokens)}, expected {grid_count}"
        )
    if not all(_FLOAT_RE.fullmatch(token) for token in value_tokens):
        raise ValueError("cube contains a malformed density token")
    values = [float(token) for token in value_tokens]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("cube contains non-finite density")
    header_volume_fraction = abs(determinant(axis_fractions)) * grid_count
    header_volume_relative_delta_fraction = (
        abs(header_volume_fraction - structure.volume_fraction) / structure.volume_fraction
    )
    header_volume = float(header_volume_fraction)
    header_volume_relative_delta = float(header_volume_relative_delta_fraction)
    if header_volume_relative_delta_fraction >= Fraction(1, 10_000):
        raise ValueError("cube grid axes are inconsistent with STRU volume")
    value_sum = math.fsum(values)
    represented_sum_fraction, token_rounding_sum_fraction = (
        _exact_scientific_sum_and_rounding_bound(value_tokens)
    )
    voxel_volume_fraction = structure.volume_fraction / grid_count
    integrated_fraction = represented_sum_fraction * voxel_volume_fraction
    rounding_bound_fraction = token_rounding_sum_fraction * voxel_volume_fraction
    expected_fraction = Fraction(str(expected))
    absolute_error_fraction = abs(integrated_fraction - expected_fraction)
    relative_error_fraction = absolute_error_fraction / expected_fraction
    certified_relative_error_fraction = (
        absolute_error_fraction + rounding_bound_fraction
    ) / expected_fraction
    integrated = float(integrated_fraction)
    rounding_bound = float(rounding_bound_fraction)
    absolute_error = float(absolute_error_fraction)
    relative_error = float(relative_error_fraction)
    certified_relative_error = float(certified_relative_error_fraction)
    return {
        "density_format": "abacus_cube_text_bohr_minus_3",
        "density_path": str(density_path),
        "density_sha256": sha256(density_path),
        "file_size_bytes": density_path.stat().st_size,
        "spin_count": spin_count,
        "atom_count": atom_count,
        "grid_dimensions": dimensions,
        "grid_count": grid_count,
        "grid_dimension_authority": "raw_running_scf_log",
        "cell_volume_bohr3": structure.volume_bohr3,
        "cube_header_volume_bohr3": header_volume,
        "cube_header_volume_relative_delta": header_volume_relative_delta,
        "volume_authority": "STRU_not_six_decimal_cube_axes",
        "integrated_electrons": integrated,
        "expected_electrons": expected,
        "absolute_error_electrons": absolute_error,
        "relative_error": relative_error,
        "text_rounding_absolute_error_bound": rounding_bound,
        "floating_absolute_error_bound": 0.0,
        "summation_algorithm": "exact_scaled_integer_decimal_sum_with_math_fsum_crosscheck",
        "math_fsum_value_sum": value_sum,
        "math_fsum_crosscheck_absolute_delta": abs(
            value_sum - float(represented_sum_fraction)
        ),
        "numerical_certification": "exact_fraction_for_printed_tokens_STRU_volume_and_rounding_bound",
        "certified_relative_error": certified_relative_error,
        "acceptance_limit": RELATIVE_ERROR_LIMIT,
        "accepted": certified_relative_error_fraction < Fraction(1, 10**10),
    }


def scientific_equivalence(source_result: dict, replay_result: dict) -> dict[str, object]:
    for label, payload in (("source", source_result), ("replay", replay_result)):
        if payload.get("converged") is not True:
            raise ValueError(f"{label} result is not converged")
    source_energy = float(source_result["energy_ev_per_atom"])
    replay_energy = float(replay_result["energy_ev_per_atom"])
    source_pressure = float(source_result["pressure_gpa"])
    replay_pressure = float(replay_result["pressure_gpa"])
    if not all(
        math.isfinite(value)
        for value in (source_energy, replay_energy, source_pressure, replay_pressure)
    ):
        raise ValueError("non-finite scientific observable")
    delta_energy = abs(replay_energy - source_energy) * 1000.0
    delta_pressure = abs(replay_pressure - source_pressure)
    accepted = (
        delta_energy < ENERGY_LIMIT_MEV_PER_ATOM
        and delta_pressure < PRESSURE_LIMIT_GPA
    )
    return {
        "delta_energy_mev_per_atom": delta_energy,
        "delta_pressure_gpa": delta_pressure,
        "energy_limit_mev_per_atom": ENERGY_LIMIT_MEV_PER_ATOM,
        "pressure_limit_gpa": PRESSURE_LIMIT_GPA,
        "accepted": accepted,
    }
