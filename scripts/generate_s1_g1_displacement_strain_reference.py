#!/usr/bin/env python3
"""Generate the frozen 15-point S1-G1 displacement/strain reference set."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import subprocess
from pathlib import Path


PROTOCOL = "S1-G1-DISPLACEMENT-STRAIN-REFERENCE-R1"
BASE_COMMIT = "f10008dd7a6e05cb8bf23f1ffd10e99e19b6083d"
INPUT_ROOT = Path("inputs/s1/g1_displacement_strain_reference_r1")
CONFIG_PATH = Path("config/S1_g1_displacement_strain_reference_r1.json")
MANIFEST_PATH = Path("config/S1_g1_displacement_strain_reference_r1_manifest.tsv")
STATE_ROOT = "/home/shenwei01/.local/state/m_ofdft/g1_displacement_strain_reference_r1_20260810"
ANALYSIS_ROOT = "analysis/s1/g1_displacement_strain_reference_r1_20260810"
RUN_IDS = tuple(f"S1-20260810-{number:03d}" for number in range(201, 216))
PARENT = {
    "al": {
        "id": "S1-20260807-043",
        "tree": "f6aebad35880164176afe032f91e0de65d2aa456",
        "pseudo": "al.gga.psp",
        "pseudo_sha256": "d76ceac60058e230eac514fc419269433a33b199eb6abfa7d6ab43cde248bd1d",
        "kmesh": [28, 28, 28],
        "electrons_per_atom": 3,
    },
    "mg": {
        "id": "S1-20260807-045",
        "tree": "6ffbe064f97f60e3f12782f49824740a854c7422",
        "pseudo": "mg.lda.lps",
        "pseudo_sha256": "4b964580cfbd798708299bb0e0baef2f437983d29e6d7040f63b7532038b4259",
        "kmesh": [24, 24, 16],
        "electrons_per_atom": 2,
    },
}
MANIFEST_FIELDS = (
    "execution_index", "experiment_id", "material", "perturbation_kind",
    "pair_id", "sign", "amplitude", "input_directory", "source_experiment_id",
    "source_run_tree_oid", "atom_count", "expected_electrons", "kmesh", "suffix",
    "input_sha256", "stru_sha256", "kpt_sha256", "metadata_sha256",
    "pseudopotential", "pseudopotential_sha256",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def canonical_json(payload: object) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def git_bytes(root: Path, relative: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), "show", f"{BASE_COMMIT}:{relative}"])


def determinant(matrix: list[list[float]]) -> float:
    a, b, c = matrix
    return (
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - a[1] * (b[0] * c[2] - b[2] * c[0])
        + a[2] * (b[0] * c[1] - b[1] * c[0])
    )


def inverse(matrix: list[list[float]]) -> list[list[float]]:
    a, b, c = matrix
    det = determinant(matrix)
    require(abs(det) > 1.0e-14, "singular lattice")
    return [
        [(b[1]*c[2]-b[2]*c[1])/det, (a[2]*c[1]-a[1]*c[2])/det, (a[1]*b[2]-a[2]*b[1])/det],
        [(b[2]*c[0]-b[0]*c[2])/det, (a[0]*c[2]-a[2]*c[0])/det, (a[2]*b[0]-a[0]*b[2])/det],
        [(b[0]*c[1]-b[1]*c[0])/det, (a[1]*c[0]-a[0]*c[1])/det, (a[0]*b[1]-a[1]*b[0])/det],
    ]


def row_times_matrix(row: list[float], matrix: list[list[float]]) -> list[float]:
    return [sum(row[k] * matrix[k][j] for k in range(3)) for j in range(3)]


def apply_deformation(lattice: list[list[float]], deformation: list[list[float]]) -> list[list[float]]:
    # Cartesian column convention r' = F r. Lattice vectors are stored as rows,
    # hence A' = A F^T.
    return [[sum(deformation[i][j] * vector[j] for j in range(3)) for i in range(3)] for vector in lattice]


def parse_parent_stru(data: bytes) -> dict[str, object]:
    lines = [line.strip() for line in data.decode().splitlines() if line.strip()]
    def section(name: str) -> int:
        matches = [i for i, line in enumerate(lines) if line == name]
        require(len(matches) == 1, f"parent STRU section differs: {name}")
        return matches[0]
    isp, ilc, ilv, ipos = (section(name) for name in (
        "ATOMIC_SPECIES", "LATTICE_CONSTANT", "LATTICE_VECTORS", "ATOMIC_POSITIONS"
    ))
    require(isp < ilc < ilv < ipos, "parent STRU section order differs")
    species_fields = lines[isp + 1].split()
    require(len(species_fields) == 4, "parent species row differs")
    lattice_constant = float(lines[ilc + 1])
    lattice = [[float(value) for value in lines[ilv + i].split()] for i in (1, 2, 3)]
    require(lines[ipos + 1].lower() == "direct", "parent positions are not Direct")
    label = lines[ipos + 2]
    magnetization = lines[ipos + 3]
    count = int(lines[ipos + 4])
    positions = [[float(value) for value in lines[ipos + 5 + i].split()[:3]] for i in range(count)]
    require(label == species_fields[0] and len(positions) == count, "parent atom block differs")
    return {
        "species_fields": species_fields,
        "lattice_constant": lattice_constant,
        "lattice": lattice,
        "label": label,
        "magnetization": magnetization,
        "positions": positions,
    }


def derive_input(data: bytes, suffix: str) -> bytes:
    output: list[str] = []
    changed = 0
    for line in data.decode().splitlines():
        fields = line.split()
        if fields and fields[0].lower() == "suffix":
            output.append(f"suffix {suffix}")
            changed += 1
        else:
            output.append(line)
    require(changed == 1, "parent INPUT suffix is not unique")
    derived = ("\n".join(output) + "\n").encode()
    parsed = {}
    for line in output[1:]:
        fields = line.split()
        if fields:
            parsed[fields[0].lower()] = fields[1:]
    expected = {
        "ecutwfc": ["40"], "ecutrho": ["160"], "scf_thr": ["1e-10"],
        "smearing_method": ["fd"], "smearing_sigma": ["0.001837465"],
        "cal_force": ["1"], "cal_stress": ["1"],
        "out_chg": ["1", "17"], "out_pot": ["1", "17"],
        "symmetry": ["0"], "esolver_type": ["ksdft"], "basis_type": ["pw"],
    }
    for key, value in expected.items():
        require(parsed.get(key) == value, f"frozen INPUT field differs: {key}")
    require(parsed.get("suffix") == [suffix], "derived suffix differs")
    return derived


def render_stru(parent: dict[str, object], lattice: list[list[float]], positions: list[list[float]]) -> bytes:
    species = parent["species_fields"]
    assert isinstance(species, list)
    lines = [
        "ATOMIC_SPECIES", " ".join(species), "", "LATTICE_CONSTANT",
        f"{float(parent['lattice_constant']):.16f}", "", "LATTICE_VECTORS",
    ]
    lines.extend(" ".join(f"{value:.16f}" for value in row) for row in lattice)
    lines.extend(["", "ATOMIC_POSITIONS", "Direct", "", str(parent["label"]),
                  str(parent["magnetization"]), str(len(positions))])
    lines.extend(" ".join(f"{value:.16f}" for value in row) + " 1 1 1" for row in positions)
    return ("\n".join(lines) + "\n").encode()


def deformation(kind: str, signed_amplitude: float) -> list[list[float]]:
    identity = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    s = signed_amplitude
    if kind == "al_tetragonal":
        q = (1.0 + s) ** -0.5
        return [[1.0 + s, 0.0, 0.0], [0.0, q, 0.0], [0.0, 0.0, q]]
    if kind == "mg_axial":
        q = (1.0 + s) ** -0.5
        return [[q, 0.0, 0.0], [0.0, q, 0.0], [0.0, 0.0, 1.0 + s]]
    if kind == "al_shear_xy":
        identity[0][1] = s
        return identity
    if kind == "mg_shear_xz":
        identity[0][2] = s
        return identity
    raise ValueError(f"unsupported deformation: {kind}")


def case_definitions() -> list[dict[str, object]]:
    return [
        {"id": "S1-20260810-201", "material": "al", "kind": "al_2x1x1_base", "pair": "", "sign": "0", "amplitude": 0.0},
        {"id": "S1-20260810-202", "material": "al", "kind": "al_2x1x1_displacement_x", "pair": "al_displacement_x", "sign": "+", "amplitude": 0.010},
        {"id": "S1-20260810-203", "material": "al", "kind": "al_2x1x1_displacement_x", "pair": "al_displacement_x", "sign": "-", "amplitude": 0.010},
        {"id": "S1-20260810-204", "material": "al", "kind": "al_tetragonal", "pair": "al_tetragonal", "sign": "+", "amplitude": 0.005},
        {"id": "S1-20260810-205", "material": "al", "kind": "al_tetragonal", "pair": "al_tetragonal", "sign": "-", "amplitude": 0.005},
        {"id": "S1-20260810-206", "material": "al", "kind": "al_shear_xy", "pair": "al_shear_xy", "sign": "+", "amplitude": 0.005},
        {"id": "S1-20260810-207", "material": "al", "kind": "al_shear_xy", "pair": "al_shear_xy", "sign": "-", "amplitude": 0.005},
        {"id": "S1-20260810-208", "material": "mg", "kind": "mg_displacement_basal_x", "pair": "mg_displacement_basal_x", "sign": "+", "amplitude": 0.010},
        {"id": "S1-20260810-209", "material": "mg", "kind": "mg_displacement_basal_x", "pair": "mg_displacement_basal_x", "sign": "-", "amplitude": 0.010},
        {"id": "S1-20260810-210", "material": "mg", "kind": "mg_displacement_c_z", "pair": "mg_displacement_c_z", "sign": "+", "amplitude": 0.010},
        {"id": "S1-20260810-211", "material": "mg", "kind": "mg_displacement_c_z", "pair": "mg_displacement_c_z", "sign": "-", "amplitude": 0.010},
        {"id": "S1-20260810-212", "material": "mg", "kind": "mg_axial", "pair": "mg_axial", "sign": "+", "amplitude": 0.005},
        {"id": "S1-20260810-213", "material": "mg", "kind": "mg_axial", "pair": "mg_axial", "sign": "-", "amplitude": 0.005},
        {"id": "S1-20260810-214", "material": "mg", "kind": "mg_shear_xz", "pair": "mg_shear_xz", "sign": "+", "amplitude": 0.005},
        {"id": "S1-20260810-215", "material": "mg", "kind": "mg_shear_xz", "pair": "mg_shear_xz", "sign": "-", "amplitude": 0.005},
    ]


def materialize(root: Path) -> tuple[dict[str, bytes], list[dict[str, object]], dict[str, object]]:
    require(git(root, "rev-parse", BASE_COMMIT) == BASE_COMMIT, "base commit is unavailable")
    parents: dict[str, dict[str, object]] = {}
    for material, registration in PARENT.items():
        parent_id = str(registration["id"])
        actual_tree = git(root, "rev-parse", f"{BASE_COMMIT}:runs/{parent_id}")
        require(actual_tree == registration["tree"], f"{material} parent tree differs")
        prefix = f"runs/{parent_id}"
        parents[material] = {
            "registration": registration,
            "input": git_bytes(root, f"{prefix}/INPUT"),
            "stru": parse_parent_stru(git_bytes(root, f"{prefix}/STRU")),
            "kpt": git_bytes(root, f"{prefix}/KPT"),
        }
        pseudo = (root / "assets/pseudo" / str(registration["pseudo"])).read_bytes()
        require(sha256(pseudo) == registration["pseudo_sha256"], f"{material} pseudo differs")

    files: dict[str, bytes] = {}
    rows: list[dict[str, object]] = []
    config_cases: list[dict[str, object]] = []
    for index, case in enumerate(case_definitions(), 1):
        experiment_id = str(case["id"])
        material = str(case["material"])
        kind = str(case["kind"])
        sign = str(case["sign"])
        magnitude = float(case["amplitude"])
        signed = magnitude if sign == "+" else -magnitude if sign == "-" else 0.0
        parent = parents[material]
        structure = parent["stru"]
        assert isinstance(structure, dict)
        lattice = [list(row) for row in structure["lattice"]]  # type: ignore[index]
        positions = [list(row) for row in structure["positions"]]  # type: ignore[index]
        displacement_cart = [0.0, 0.0, 0.0]
        displacement_fractional = [0.0, 0.0, 0.0]
        F = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]

        if kind.startswith("al_2x1x1"):
            lattice[0] = [2.0 * value for value in lattice[0]]
            positions = [[0.0, 0.0, 0.0], [0.5, 0.0, 0.0]]
            if "displacement" in kind:
                displacement_cart = [signed, 0.0, 0.0]
                displacement_fractional = row_times_matrix(displacement_cart, inverse(lattice))
                positions[1] = [positions[1][i] + displacement_fractional[i] for i in range(3)]
        elif "displacement" in kind:
            axis = 0 if "basal_x" in kind else 2
            displacement_cart[axis] = signed
            displacement_fractional = row_times_matrix(displacement_cart, inverse(lattice))
            positions[1] = [positions[1][i] + displacement_fractional[i] for i in range(3)]
        else:
            F = deformation(kind, signed)
            lattice = apply_deformation(lattice, F)

        require(determinant(lattice) > 0, f"nonpositive lattice for {experiment_id}")
        atom_count = len(positions)
        registration = PARENT[material]
        expected_electrons = atom_count * int(registration["electrons_per_atom"])
        suffix = f"g1dsr1_{material}_{experiment_id[-3:]}"
        input_bytes = derive_input(parent["input"], suffix)  # type: ignore[arg-type]
        stru_bytes = render_stru(structure, lattice, positions)
        kpt_bytes = parent["kpt"]  # type: ignore[assignment]
        lattice_constant = float(structure["lattice_constant"])
        lattice_bohr = [[value * lattice_constant for value in row] for row in lattice]
        positions_bohr = [
            [sum(frac[k] * lattice_bohr[k][j] for k in range(3)) for j in range(3)]
            for frac in positions
        ]
        metadata = {
            "protocol_revision": PROTOCOL,
            "experiment_id": experiment_id,
            "material": material,
            "perturbation_kind": kind,
            "pair_id": case["pair"],
            "sign": sign,
            "amplitude": magnitude,
            "signed_amplitude": signed,
            "amplitude_units": "angstrom" if "displacement" in kind else "dimensionless",
            "deformation_gradient_cartesian_column_convention": F,
            "deformation_action": "r_prime=F*r; row-lattice A_prime=A*F^T; Direct coordinates unchanged for strain",
            "displacement_cartesian_angstrom": displacement_cart,
            "displacement_fractional": displacement_fractional,
            "source_experiment_id": registration["id"],
            "source_run_tree_oid": registration["tree"],
            "base_commit": BASE_COMMIT,
            "atom_count": atom_count,
            "expected_electrons": expected_electrons,
            "solver": "ksdft",
            "finite_temperature_semantics": "Mermin; entropy-corrected E_ec is not an exact zero-temperature label",
            "zero_temperature_exact_claim": False,
            "kmesh": registration["kmesh"],
            "ecutwfc_ry": 40,
            "ecutrho_ry": 160,
            "smearing_sigma_ry": 0.001837465,
            "scf_threshold": 1.0e-10,
            "cube_precision": 17,
            "pseudopotential": registration["pseudo"],
            "pseudopotential_sha256": registration["pseudo_sha256"],
            "suffix": suffix,
            "expected_lattice_vectors_bohr": lattice_bohr,
            "expected_cartesian_positions_bohr": positions_bohr,
        }
        directory = INPUT_ROOT / experiment_id
        payloads = {
            directory / "INPUT": input_bytes,
            directory / "STRU": stru_bytes,
            directory / "KPT": kpt_bytes,
            directory / "metadata.json": canonical_json(metadata),
        }
        for path, data in payloads.items():
            files[str(path)] = data
        row = {
            "execution_index": index,
            "experiment_id": experiment_id,
            "material": material,
            "perturbation_kind": kind,
            "pair_id": case["pair"],
            "sign": sign,
            "amplitude": f"{magnitude:.3f}",
            "input_directory": str(directory),
            "source_experiment_id": registration["id"],
            "source_run_tree_oid": registration["tree"],
            "atom_count": atom_count,
            "expected_electrons": expected_electrons,
            "kmesh": "x".join(str(v) for v in registration["kmesh"]),
            "suffix": suffix,
            "input_sha256": sha256(input_bytes),
            "stru_sha256": sha256(stru_bytes),
            "kpt_sha256": sha256(kpt_bytes),
            "metadata_sha256": sha256(payloads[directory / "metadata.json"]),
            "pseudopotential": registration["pseudo"],
            "pseudopotential_sha256": registration["pseudo_sha256"],
        }
        rows.append(row)
        config_cases.append(metadata)

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=MANIFEST_FIELDS, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    manifest_bytes = buffer.getvalue().encode()
    config = {
        "protocol_revision": PROTOCOL,
        "status": "preregistered",
        "base_commit": BASE_COMMIT,
        "state_root": STATE_ROOT,
        "analysis_root": ANALYSIS_ROOT,
        "run_ids_exact": list(RUN_IDS),
        "run_count_exact": 15,
        "pair_count_exact": 7,
        "execution": {
            "hostname_exact": "node01",
            "mpi_ranks_exact": 4,
            "rank_logical_cpus_exact": [40, 41, 42, 43],
            "retry_policy": "none; every ID is single-use",
            "attempt_marker": "O_CREAT|O_EXCL before solver",
            "abacus_path": "/home/shenwei01/wt_melting_runtime_20260724/build-abacus-wt-cpu/source/abacus_pw_para",
            "abacus_sha256": "2d68a57c7b25608b3550854dabc2e63601eeca956bf185ad7d0967052bdbb4ba",
            "activation_script": "environment/activate.sh",
        },
        "acceptance": {
            "registered_runs_exact": 15,
            "accepted_runs_exact": 15,
            "central_difference_pairs_exact": 7,
            "thermodynamic_labels_exact": ["F", "m", "U", "E_ec", "E_one_elec", "E_localpp", "T_sU", "F_s", "E_Hartree", "E_xc", "E_Ewald", "mu"],
            "thermodynamic_identity_residual_ev_per_atom_strictly_less_than": 1.0e-8,
            "electron_number_relative_error_strictly_less_than": 1.0e-10,
            "cube_geometry_absolute_tolerance_bohr": 5.0e-5,
            "cube_output_precision_exact": 17,
            "forces_and_stress_must_be_finite_and_complete": True,
            "central_difference_response_is_diagnostic_only": True,
            "g4_acceptance_claim": False,
        },
        "parents": PARENT,
        "cases": config_cases,
    }
    files[str(MANIFEST_PATH)] = manifest_bytes
    files[str(CONFIG_PATH)] = canonical_json(config)
    return files, rows, config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    files, _, _ = materialize(root)
    if args.write:
        require(not git(root, "status", "--porcelain"), "--write requires a clean worktree")
        for relative, data in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        print(f"wrote {len(files)} frozen files for {len(RUN_IDS)} runs")
        return 0
    failures = []
    for relative, expected in files.items():
        path = root / relative
        if not path.is_file() or path.is_symlink() or path.read_bytes() != expected:
            failures.append(relative)
    if failures:
        raise SystemExit(f"generated files differ or are missing: {failures}")
    print(f"validated {len(files)} frozen files for {len(RUN_IDS)} runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
