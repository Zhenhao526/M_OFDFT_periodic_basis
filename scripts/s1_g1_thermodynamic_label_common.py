#!/usr/bin/env python3
"""Fail-closed helpers for the S1-G1 thermodynamic-label audit.

This module is deliberately additive.  The accepted S1 parsers and audit
helpers are treated as immutable evidence and are not modified here.
"""

from __future__ import annotations

import hashlib
import csv
import io
import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Sequence


PROTOCOL_REVISION = "S1-G1-THERMODYNAMIC-LABEL-AUDIT-R1"
CUBE_PRECISION = 17
RY_TO_EV = 13.605693122994
DENSITY_D1_LIMIT = 0.005
DENSITY_D2_LIMIT = 0.005
DERIVATIVE_DG_LIMIT = 0.01
DERIVATIVE_RMS_EV_LIMIT = 0.005
IDENTITY_RESIDUAL_EV_PER_ATOM_LIMIT = Decimal("1e-8")
ELECTRON_RELATIVE_ERROR_LIMIT = 1.0e-10
AUDIT_IDS = tuple(f"S1-20260806-{number:03d}" for number in range(1, 41))
PILOT_IDS = (
    "S1-20260806-024",
    "S1-20260806-036",
    "S1-20260806-031",
    "S1-20260806-039",
)
K_GATE_COMPLETION_IDS = (
    "S1-20260806-021",
    "S1-20260806-035",
    "S1-20260806-027",
    "S1-20260806-037",
    "S1-20260806-028",
    "S1-20260806-038",
    "S1-20260806-034",
    "S1-20260806-040",
)
K_GATE_EXECUTION_IDS = PILOT_IDS + K_GATE_COMPLETION_IDS
EXECUTION_ORDER = K_GATE_EXECUTION_IDS + tuple(
    experiment_id for experiment_id in AUDIT_IDS if experiment_id not in K_GATE_EXECUTION_IDS
)

MANIFEST_FIELDS = (
    "execution_index",
    "execution_phase",
    "experiment_id",
    "input_directory",
    "material",
    "volume_ratio",
    "smearing_level",
    "smearing_sigma_ry",
    "kmesh",
    "run_role",
    "source_experiment_id",
    "source_run_path",
    "source_run_tree_oid",
    "reference_experiment_id",
    "common_quarter_partner_id",
    "dense_standard_scalar_source_id",
    "suffix",
    "input_sha256",
    "stru_sha256",
    "kpt_sha256",
    "metadata_sha256",
    "pseudopotential",
    "pseudopotential_sha256",
    "source_input_sha256",
    "source_stru_sha256",
    "source_kpt_sha256",
    "source_metadata_sha256",
    "source_result_path",
    "source_result_sha256",
    "source_log_path",
    "source_log_sha256",
    "expected_electrons",
    "atom_count",
    "cube_precision",
    "density_basename",
    "potential_basename",
)

_FLOAT = re.compile(r"^[+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?$")
_ENERGY_ROW = re.compile(
    r"^\s*(E_[A-Za-z_]+(?:\([^\s()]+\))?)\s+"
    r"([+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?)\s+"
    r"([+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?)\s*$"
)
_FINAL_ENERGY = re.compile(r"!FINAL_ETOT_IS\s+([-+0-9.eE]+)\s+eV")
_PRESSURE = re.compile(r"#TOTAL-PRESSURE#.*?:\s*([-+0-9.eE]+)\s+kbar")
_ELECTRONS = re.compile(r"Autoset the number of electrons\s*=\s*([-+0-9.eE]+)")
_ATOM_COUNT = re.compile(r"TOTAL ATOM NUMBER\s*=\s*([0-9]+)")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def read_regular_bytes(path: Path | str) -> bytes:
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), f"not a regular file: {path}")
    return path.read_bytes()


def read_regular_text(path: Path | str) -> str:
    return read_regular_bytes(path).decode("utf-8", errors="strict")


def sha256_regular_file(path: Path | str) -> str:
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), f"not a regular file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json_bytes(payload: object) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def manifest_bytes(rows: Iterable[dict[str, object]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer, fieldnames=MANIFEST_FIELDS, delimiter="\t", lineterminator="\n"
    )
    writer.writeheader()
    for row in rows:
        unexpected = set(row) - set(MANIFEST_FIELDS)
        require(not unexpected, f"unexpected manifest fields: {sorted(unexpected)}")
        writer.writerow({field: row.get(field, "") for field in MANIFEST_FIELDS})
    return buffer.getvalue().encode("utf-8")


def read_manifest(path: Path | str) -> list[dict[str, str]]:
    path = Path(path)
    text = read_regular_text(path)
    with io.StringIO(text, newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        require(
            tuple(reader.fieldnames or ()) == MANIFEST_FIELDS,
            f"unexpected thermodynamic-label manifest header: {path}",
        )
        rows = list(reader)
    require(
        all(None not in row and all(value is not None for value in row.values()) for row in rows),
        f"malformed thermodynamic-label manifest row: {path}",
    )
    return rows


def _input_key(line: str) -> str:
    content = line.split("#", 1)[0].strip()
    return content.split(maxsplit=1)[0].lower() if content else ""


def parse_input_text(source: bytes | str) -> dict[str, tuple[str, ...]]:
    text = source.decode("utf-8") if isinstance(source, bytes) else source
    values: dict[str, tuple[str, ...]] = {}
    first_content = True
    for raw in text.splitlines():
        content = raw.split("#", 1)[0].strip()
        if not content:
            continue
        if first_content:
            require(content == "INPUT_PARAMETERS", "INPUT must begin with INPUT_PARAMETERS")
            first_content = False
            continue
        fields = content.split()
        key = fields[0].lower()
        require(key not in values, f"duplicate INPUT key: {key}")
        values[key] = tuple(fields[1:])
    require(not first_content, "empty INPUT")
    return values


def derive_label_input(
    source: bytes,
    *,
    suffix: str,
    smearing_sigma_ry: str,
    cube_precision: int = CUBE_PRECISION,
) -> bytes:
    """Change only suffix/sigma and add the two registered output controls."""

    require(re.fullmatch(r"[A-Za-z0-9_.-]+", suffix) is not None, "invalid suffix")
    sigma = _decimal(smearing_sigma_ry, "smearing sigma")
    require(sigma > 0, "smearing sigma must be positive")
    require(isinstance(cube_precision, int) and cube_precision >= 10, "invalid precision")
    lines = source.decode("utf-8", errors="strict").splitlines(keepends=True)
    parsed = parse_input_text(source)
    for forbidden in ("out_chg", "out_pot"):
        require(forbidden not in parsed, f"source INPUT already contains {forbidden}")
    require("suffix" in parsed, "source INPUT lacks suffix")
    require("smearing_sigma" in parsed, "source INPUT lacks smearing_sigma")
    output: list[str] = []
    changed = {"suffix": 0, "smearing_sigma": 0}
    for line in lines:
        key = _input_key(line)
        newline = "\n" if line.endswith("\n") else ""
        if key == "suffix":
            output.append(f"suffix {suffix}{newline}")
            output.append(f"out_chg 1 {cube_precision}{newline}")
            output.append(f"out_pot 1 {cube_precision}{newline}")
            changed[key] += 1
        elif key == "smearing_sigma":
            output.append(f"smearing_sigma {smearing_sigma_ry}{newline}")
            changed[key] += 1
        else:
            output.append(line)
    require(changed == {"suffix": 1, "smearing_sigma": 1}, "non-unique INPUT fields")
    derived = "".join(output).encode("utf-8")
    validate_derived_input(
        source,
        derived,
        suffix=suffix,
        smearing_sigma_ry=smearing_sigma_ry,
        cube_precision=cube_precision,
    )
    return derived


def validate_derived_input(
    source: bytes,
    derived: bytes,
    *,
    suffix: str,
    smearing_sigma_ry: str,
    cube_precision: int = CUBE_PRECISION,
) -> None:
    before = parse_input_text(source)
    after = parse_input_text(derived)
    allowed = {"suffix", "smearing_sigma", "out_chg", "out_pot"}
    require(set(after) == set(before) | {"out_chg", "out_pot"}, "INPUT key set changed")
    for key in set(before) - allowed:
        require(after[key] == before[key], f"unregistered INPUT field changed: {key}")
    require(after["suffix"] == (suffix,), "derived suffix differs")
    require(after["smearing_sigma"] == (smearing_sigma_ry,), "derived sigma differs")
    require(after["out_chg"] == ("1", str(cube_precision)), "out_chg differs")
    require(after["out_pot"] == ("1", str(cube_precision)), "out_pot differs")


def parse_kpt_text(source: bytes | str) -> tuple[int, int, int]:
    text = source.decode("utf-8") if isinstance(source, bytes) else source
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    require(len(lines) == 4, "KPT must contain exactly four non-empty lines")
    require(lines[0] == "K_POINTS" and lines[1] == "0", "unsupported KPT header")
    require(lines[2].lower() == "gamma", "KPT must use Gamma mesh syntax")
    fields = lines[3].split()
    require(len(fields) == 6, "KPT mesh row must have six integers")
    try:
        values = tuple(int(value) for value in fields)
    except ValueError as error:
        raise ValueError("KPT mesh row is not integral") from error
    require(all(value > 0 for value in values[:3]), "KPT dimensions must be positive")
    require(values[3:] == (0, 0, 0), "KPT shifts must be zero")
    return values[:3]


def derive_kpt(source: bytes, mesh: Sequence[int]) -> bytes:
    old_mesh = parse_kpt_text(source)
    require(len(mesh) == 3, "k mesh must contain three values")
    new_mesh = tuple(int(value) for value in mesh)
    require(all(value > 0 for value in new_mesh), "k mesh values must be positive")
    # Canonical form removes whitespace as an unregistered source of variation.
    output = (
        "K_POINTS\n0\nGamma\n"
        f"{new_mesh[0]} {new_mesh[1]} {new_mesh[2]} 0 0 0\n"
    ).encode("utf-8")
    require(parse_kpt_text(output) == new_mesh, "derived KPT validation failed")
    if new_mesh == old_mesh:
        # A canonical rewrite is allowed, but its semantic mesh must be identical.
        require(parse_kpt_text(source) == parse_kpt_text(output), "KPT changed")
    return output


def validate_local_pseudopotential(path: Path | str) -> dict[str, object]:
    path = Path(path)
    lines = read_regular_text(path).splitlines()
    matches: list[tuple[int, ...]] = []
    for line in lines:
        if "nproj" not in line.lower():
            continue
        prefix = line.lower().split("nproj", 1)[0].split()
        try:
            values = tuple(int(value) for value in prefix)
        except ValueError as error:
            raise ValueError(f"invalid nproj row in {path}") from error
        matches.append(values)
    require(len(matches) == 1, f"expected one nproj row in {path}")
    require(matches[0] and all(value == 0 for value in matches[0]), "nonlocal projectors present")
    return {
        "path": str(path),
        "sha256": sha256_regular_file(path),
        "nproj_values": list(matches[0]),
        "local_only": True,
    }


def _decimal(value: str | int | float | Decimal, label: str) -> Decimal:
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"invalid {label}: {value}") from error
    require(parsed.is_finite(), f"non-finite {label}")
    return parsed


def _determinant(matrix: Sequence[Sequence[Decimal]]) -> Decimal:
    require(len(matrix) == 3 and all(len(row) == 3 for row in matrix), "expected 3x3")
    a, b, c = matrix
    return (
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - a[1] * (b[0] * c[2] - b[2] * c[0])
        + a[2] * (b[0] * c[1] - b[1] * c[0])
    )


@dataclass(frozen=True)
class CubeField:
    path: Path
    sha256: str
    quantity: str
    units: str
    spin_count: int
    atom_count: int
    origin_bohr: tuple[Decimal, Decimal, Decimal]
    dimensions: tuple[int, int, int]
    axis_steps_bohr: tuple[tuple[Decimal, Decimal, Decimal], ...]
    atom_rows: tuple[tuple[Decimal, ...], ...]
    values: tuple[float, ...]
    value_tokens: tuple[str, ...]
    grid_count: int
    cell_volume_bohr3: float
    voxel_volume_bohr3: float

    @property
    def geometry_signature(self) -> tuple[object, ...]:
        return (
            self.spin_count,
            self.atom_count,
            self.origin_bohr,
            self.dimensions,
            self.axis_steps_bohr,
            self.atom_rows,
        )


def parse_abacus_cube(
    path: Path | str,
    *,
    quantity: str,
    units: str,
    structure_path: Path | str | None = None,
    expected_grid: Sequence[int] | None = None,
) -> CubeField:
    path = Path(path)
    lines = read_regular_text(path).splitlines()
    require(len(lines) >= 7, "truncated ABACUS cube")
    require("Cubefile created from ABACUS" in lines[0], "cube producer marker differs")
    spin_fields = lines[1].split()
    require(spin_fields, "missing cube spin row")
    try:
        spin_count = int(spin_fields[0])
    except ValueError as error:
        raise ValueError("invalid cube spin count") from error
    require(spin_count == 1, "thermodynamic audit requires nspin=1")
    try:
        origin_fields = lines[2].split()
        require(len(origin_fields) == 4, "invalid cube origin row")
        atom_count = int(origin_fields[0])
        origin = tuple(_decimal(value, "cube origin") for value in origin_fields[1:])
        dimensions: list[int] = []
        axes: list[tuple[Decimal, Decimal, Decimal]] = []
        for index in (3, 4, 5):
            fields = lines[index].split()
            require(len(fields) == 4, "invalid cube axis row")
            dimensions.append(int(fields[0]))
            axes.append(tuple(_decimal(value, "cube axis") for value in fields[1:]))
    except (IndexError, ValueError) as error:
        if isinstance(error, ValueError) and str(error).startswith("invalid cube"):
            raise
        raise ValueError(f"invalid cube header: {error}") from error
    require(atom_count > 0, "cube atom count must be positive")
    require(all(value > 0 for value in dimensions), "cube grid dimensions must be positive")
    require(all(abs(value) <= Decimal("1e-12") for value in origin), "nonzero cube origin")
    grid = tuple(dimensions)
    if expected_grid is not None:
        require(grid == tuple(int(value) for value in expected_grid), "cube grid differs")
    atom_rows: list[tuple[Decimal, ...]] = []
    for line in lines[6 : 6 + atom_count]:
        fields = line.split()
        require(len(fields) == 5 and all(_FLOAT.fullmatch(value) for value in fields), "invalid cube atom row")
        atom_rows.append(tuple(_decimal(value, "cube atom value") for value in fields))
    value_tokens = tuple(" ".join(lines[6 + atom_count :]).split())
    grid_count = math.prod(grid)
    require(len(value_tokens) == grid_count, "cube value count differs from grid")
    require(all(_FLOAT.fullmatch(token) for token in value_tokens), "malformed cube value")
    values = tuple(float(token) for token in value_tokens)
    require(all(math.isfinite(value) for value in values), "non-finite cube value")
    header_volume = abs(_determinant(axes)) * Decimal(grid_count)
    require(header_volume > 0, "nonpositive cube cell volume")
    cell_volume = float(header_volume)
    if structure_path is not None:
        # Importing preserves the independently accepted STRU parser unchanged.
        from s1_electron_number_common import parse_stru

        structure = parse_stru(Path(structure_path))
        require(atom_count == sum(structure.species_counts.values()), "cube/STRU atom mismatch")
        relative = abs(cell_volume - structure.volume_bohr3) / structure.volume_bohr3
        require(relative < 1.0e-4, "cube axes disagree with STRU cell volume")
        cell_volume = structure.volume_bohr3
    return CubeField(
        path=path,
        sha256=sha256_regular_file(path),
        quantity=quantity,
        units=units,
        spin_count=spin_count,
        atom_count=atom_count,
        origin_bohr=origin,  # type: ignore[arg-type]
        dimensions=grid,  # type: ignore[arg-type]
        axis_steps_bohr=tuple(axes),
        atom_rows=tuple(atom_rows),
        values=values,
        value_tokens=value_tokens,
        grid_count=grid_count,
        cell_volume_bohr3=cell_volume,
        voxel_volume_bohr3=cell_volume / grid_count,
    )


def require_same_cube_geometry(reference: CubeField, comparison: CubeField) -> None:
    require(reference.geometry_signature == comparison.geometry_signature, "cube geometry differs")
    require(
        math.isclose(
            reference.cell_volume_bohr3,
            comparison.cell_volume_bohr3,
            rel_tol=0.0,
            abs_tol=1.0e-12,
        ),
        "cube cell volumes differ",
    )


def compare_density_fields(reference: CubeField, comparison: CubeField) -> dict[str, object]:
    require(reference.units == comparison.units == "electron/bohr^3", "density units differ")
    require_same_cube_geometry(reference, comparison)
    delta = [candidate - base for base, candidate in zip(reference.values, comparison.values)]
    voxel = reference.voxel_volume_bohr3
    electrons = voxel * math.fsum(reference.values)
    require(math.isfinite(electrons) and electrons > 0, "invalid reference electron integral")
    ref_square = math.fsum(value * value for value in reference.values)
    require(ref_square > 0, "zero density reference norm")
    d1 = voxel * math.fsum(abs(value) for value in delta) / electrons
    d2 = math.sqrt(math.fsum(value * value for value in delta) / ref_square)
    linf = max(abs(value) for value in delta)
    return {
        "reference_sha256": reference.sha256,
        "comparison_sha256": comparison.sha256,
        "d1": d1,
        "d2": d2,
        "linf_electron_per_bohr3": linf,
        "reference_electrons": electrons,
        "d1_limit": DENSITY_D1_LIMIT,
        "d2_limit": DENSITY_D2_LIMIT,
        "accepted": d1 < DENSITY_D1_LIMIT and d2 < DENSITY_D2_LIMIT,
        "low_density_mask": None,
    }


def compare_potential_derivative_fields(
    reference: CubeField, comparison: CubeField
) -> dict[str, object]:
    require(reference.units == comparison.units == "Ry", "potential units differ")
    require_same_cube_geometry(reference, comparison)
    ref_ev = [value * RY_TO_EV for value in reference.values]
    cmp_ev = [value * RY_TO_EV for value in comparison.values]
    ref_mean = math.fsum(ref_ev) / len(ref_ev)
    cmp_mean = math.fsum(cmp_ev) / len(cmp_ev)
    # Number-conserving derivative: g = -(v - unweighted cell average(v)).
    ref_g = [-(value - ref_mean) for value in ref_ev]
    cmp_g = [-(value - cmp_mean) for value in cmp_ev]
    delta = [candidate - base for base, candidate in zip(ref_g, cmp_g)]
    ref_square = math.fsum(value * value for value in ref_g)
    require(ref_square > 0, "zero projected derivative reference norm")
    dg = math.sqrt(math.fsum(value * value for value in delta) / ref_square)
    rms = math.sqrt(math.fsum(value * value for value in delta) / len(delta))
    linf = max(abs(value) for value in delta)
    return {
        "reference_sha256": reference.sha256,
        "comparison_sha256": comparison.sha256,
        "gauge": "g=-(potential_eV-unweighted_cell_average_potential_eV)",
        "reference_cell_average_potential_ev": ref_mean,
        "comparison_cell_average_potential_ev": cmp_mean,
        "dg": dg,
        "absolute_rms_ev": rms,
        "linf_ev": linf,
        "dg_limit": DERIVATIVE_DG_LIMIT,
        "absolute_rms_ev_limit": DERIVATIVE_RMS_EV_LIMIT,
        "accepted": dg < DERIVATIVE_DG_LIMIT and rms < DERIVATIVE_RMS_EV_LIMIT,
        "low_density_mask": None,
    }


def _last_decimal(matches: Iterable[str], label: str) -> Decimal:
    values = list(matches)
    require(values, f"missing {label}")
    return _decimal(values[-1], label)


def _half_quantum(value: Decimal) -> Decimal:
    return Decimal(5).scaleb(value.as_tuple().exponent - 1)


def _identity(
    name: str,
    lhs: Decimal,
    rhs: Decimal,
    operands: Sequence[Decimal],
    atom_count: int,
) -> dict[str, object]:
    residual = lhs - rhs
    residual_per_atom = residual / atom_count
    rounding_bound = _half_quantum(lhs) + sum(
        (_half_quantum(value) for value in operands), Decimal(0)
    )
    accepted = abs(residual_per_atom) < IDENTITY_RESIDUAL_EV_PER_ATOM_LIMIT
    return {
        "name": name,
        "lhs_ev_per_cell": str(lhs),
        "rhs_ev_per_cell": str(rhs),
        "residual_ev_per_cell": str(residual),
        "residual_ev_per_atom": str(residual_per_atom),
        "text_rounding_bound_ev_per_cell": str(rounding_bound),
        "acceptance_limit_ev_per_atom": str(IDENTITY_RESIDUAL_EV_PER_ATOM_LIMIT),
        "acceptance_inequality": "strict_less_than",
        "accepted": accepted,
    }


def parse_thermodynamic_log(
    text: str, *, expected_atom_count: int | None = None
) -> dict[str, object]:
    require("#SCF IS CONVERGED#" in text, "SCF did not converge")
    require("!!SCF IS NOT CONVERGED!!" not in text, "contradictory SCF status")
    energy_rows: dict[str, list[tuple[Decimal, Decimal]]] = {}
    for line in text.splitlines():
        match = _ENERGY_ROW.fullmatch(line)
        if match is None:
            continue
        energy_rows.setdefault(match.group(1), []).append(
            (_decimal(match.group(2), f"{match.group(1)} Ry"), _decimal(match.group(3), f"{match.group(1)} eV"))
        )

    for key in (
        "E_KohnSham",
        "E_KS(sigma->0)",
        "E_one_elec",
        "E_localpp",
        "E_Hartree",
        "E_xc",
        "E_Ewald",
        "E_entropy(-TS)",
        "E_Fermi",
    ):
        require(key in energy_rows and energy_rows[key], f"missing final {key}")

    final_f = _last_decimal(_FINAL_ENERGY.findall(text), "!FINAL_ETOT_IS")
    pressure_kbar = _last_decimal(_PRESSURE.findall(text), "total pressure")
    electrons = _last_decimal(_ELECTRONS.findall(text), "electron count")
    atom_count_matches = _ATOM_COUNT.findall(text)
    require(len(atom_count_matches) == 1, "expected exactly one TOTAL ATOM NUMBER")
    atom_count = int(atom_count_matches[0])
    require(atom_count > 0, "TOTAL ATOM NUMBER must be positive")
    if expected_atom_count is not None:
        require(
            atom_count == expected_atom_count,
            "raw log atom count differs from the registered structure",
        )
    kohn_sham = energy_rows["E_KohnSham"][-1][1]
    eec = energy_rows["E_KS(sigma->0)"][-1][1]
    one = energy_rows["E_one_elec"][-1][1]
    localpp = energy_rows["E_localpp"][-1][1]
    hartree = energy_rows["E_Hartree"][-1][1]
    xc = energy_rows["E_xc"][-1][1]
    ewald = energy_rows["E_Ewald"][-1][1]
    minus_ts = energy_rows["E_entropy(-TS)"][-1][1]
    mu = energy_rows["E_Fermi"][-1][1]
    internal = final_f - minus_ts
    ts = one - localpp
    fs = ts + minus_ts
    require(minus_ts <= 0, "E_entropy(-TS) must be nonpositive")
    require(final_f <= eec <= internal, "expected F <= E_ec <= U")
    identities = {
        "final_equals_kohn_sham": _identity(
            "F=E_KohnSham=!FINAL_ETOT_IS",
            final_f,
            kohn_sham,
            (kohn_sham,),
            atom_count,
        ),
        "internal_energy_definition": _identity(
            "U=F-m", internal, final_f - minus_ts, (final_f, minus_ts), atom_count
        ),
        "entropy_corrected": _identity(
            "E_ec=F-m/2",
            eec,
            final_f - minus_ts / 2,
            (final_f, minus_ts),
            atom_count,
        ),
        "kinetic_energy_definition": _identity(
            "T_sU=E_one_elec-E_localpp",
            ts,
            one - localpp,
            (one, localpp),
            atom_count,
        ),
        "noninteracting_free_energy_definition": _identity(
            "F_s=T_sU+m", fs, ts + minus_ts, (ts, minus_ts), atom_count
        ),
        "total_decomposition": _identity(
            "F=E_one+E_H+E_xc+E_Ewald+m",
            final_f,
            one + hartree + xc + ewald + minus_ts,
            (one, hartree, xc, ewald, minus_ts),
            atom_count,
        ),
    }
    require(all(row["accepted"] for row in identities.values()), "thermodynamic identity failed")
    labels = {
        "F": final_f,
        "m": minus_ts,
        "U": internal,
        "E_ec": eec,
        "E_one_elec": one,
        "E_localpp": localpp,
        "T_sU": ts,
        "F_s": fs,
        "E_Hartree": hartree,
        "E_xc": xc,
        "E_Ewald": ewald,
        "mu": mu,
    }
    return {
        "converged": True,
        "atom_count": atom_count,
        "energy_labels_ev_per_cell": labels,
        "energy_labels_ev_per_atom": {
            key: value / atom_count for key, value in labels.items()
        },
        "e_kohnsham_table_ev_per_cell": kohn_sham,
        "free_energy_ev": final_f,
        "entropy_minus_ts_ev": minus_ts,
        "internal_energy_ev": internal,
        "entropy_corrected_estimator_ev": eec,
        "one_electron_energy_ev": one,
        "local_pseudopotential_energy_ev": localpp,
        "kinetic_energy_ts_ev": ts,
        "noninteracting_free_energy_fs_ev": fs,
        "hartree_energy_ev": hartree,
        "xc_energy_ev": xc,
        "ewald_energy_ev": ewald,
        "chemical_potential_ev": mu,
        "pressure_kbar": pressure_kbar,
        "pressure_gpa": pressure_kbar / Decimal(10),
        "electron_count_reported": electrons,
        "identities": identities,
        "all_identity_residuals_strictly_below_limit": True,
        "zero_temperature_exact_claim": False,
        "entropy_corrected_estimator_semantics": "finite-smearing scalar estimator",
    }


def json_safe(value: object) -> object:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value
