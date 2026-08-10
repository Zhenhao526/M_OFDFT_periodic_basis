#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
import json
import os
from types import SimpleNamespace
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from analyze_s1_g1_three_layer_r1 import p0_metrics  # noqa: E402
from generate_s1_g1_three_layer_continuation_r2 import generate  # noqa: E402
from parse_s1_g1_three_layer_continuation_r2 import parse_eig_occ, parse_stress, validate_cube_atoms  # noqa: E402
from recover_s1_g1_three_layer_continuation_r2 import source_snapshot  # noqa: E402
from run_s1_g1_three_layer_continuation_r2 import inspect_core_collisions  # noqa: E402
from s1_g1_three_layer_continuation_r2_common import (  # noqa: E402
    load_config,
    load_manifest,
    parse_upf_header,
    sha256_file,
)


class ThreeLayerContinuationR2Tests(unittest.TestCase):
    def test_manifest_and_generated_inputs_are_deterministic(self) -> None:
        rows = load_manifest(PROJECT_ROOT)
        self.assertEqual([row["experiment_id"] for row in rows], [f"S1-20260810-{value:03d}" for value in range(327, 335)])
        first = {path: sha256_file(path) for directory in generate(PROJECT_ROOT) for path in directory.iterdir() if path.is_file()}
        second = {path: sha256_file(path) for directory in generate(PROJECT_ROOT) for path in directory.iterdir() if path.is_file()}
        self.assertEqual(first, second)

    def test_upf2_header_parser_counts_expanded_projectors(self) -> None:
        content = """<UPF version=\"2.0.1\">
<PP_HEADER pseudo_type=\"NC\" functional=\"PBE\" z_valence=\"3.0\" number_of_proj=\"2\" core_correction=\"T\"/>
<PP_NONLOCAL><PP_BETA.1 angular_momentum=\"0\"></PP_BETA.1><PP_BETA.2 angular_momentum=\"1\"></PP_BETA.2><PP_DIJ></PP_DIJ></PP_NONLOCAL>
</UPF>\n"""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "test.upf"
            path.write_text(content, encoding="utf-8")
            header = parse_upf_header(path)
        self.assertEqual(header["number_of_proj"], 2)
        self.assertEqual(header["pp_beta_element_count"], 2)
        self.assertEqual(header["expanded_nonlocal_projectors_per_atom"], 4)
        self.assertTrue(header["pp_dij_present"])

    def test_p0_metrics_remain_strict(self) -> None:
        config = load_config(PROJECT_ROOT)
        def point(energy: float, pressure: float) -> dict:
            return {"thermodynamic_labels_ev_per_atom": {"E_ec": energy}, "pressure_gpa": pressure}
        results = {
            "S1-20260810-301": point(-10.0, 0.0), "S1-20260810-302": point(-9.9990, 0.0), "S1-20260810-303": point(-9.9995, 0.01),
            "S1-20260810-304": point(-20.0, 0.0), "S1-20260810-305": point(-19.9990, 0.0), "S1-20260810-306": point(-19.9995, 0.01),
        }
        self.assertEqual(p0_metrics(results, config)["status"], "accepted")
        results["S1-20260810-303"] = point(-9.9980, 0.01)
        self.assertEqual(p0_metrics(results, config)["status"], "rejected")

    def test_eig_occ_multiplication_and_last_band_gates(self) -> None:
        config = load_config(PROJECT_ROOT)
        text = """1 # ionic step
 spin=1 k-point=1/2 Cartesian=0 0 0 (1 plane wave)
 1 -1.0 1.0
 2 2.0 0.0
 spin=1 k-point=2/2 Cartesian=.1 .1 .1 (1 plane wave)
 1 -1.0 2.0
 2 2.0 0.0
"""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "eig_occ.txt"
            path.write_text(text, encoding="utf-8")
            parsed = parse_eig_occ(path, 2, 3.0, config)
        self.assertTrue(parsed["accepted"])
        self.assertEqual(parsed["weighted_occupation_sum"], 3.0)

    def test_stress_trace_gate(self) -> None:
        config = load_config(PROJECT_ROOT)
        text = """              Stress_x             Stress_y             Stress_z
 ----------------------------------------------------------------
 -1.0 0.0 0.0
 0.0 -2.0 0.0
 0.0 0.0 -3.0
"""
        parsed = parse_stress(text, Decimal("-2.0"), config)
        self.assertTrue(parsed["accepted"])

    def test_stress_symmetry_gate_rejects_asymmetric_tensor(self) -> None:
        config = load_config(PROJECT_ROOT)
        text = """              Stress_x             Stress_y             Stress_z
 ----------------------------------------------------------------
 -1.0 0.1 0.0
 0.0 -2.0 0.0
 0.0 0.0 -3.0
"""
        with self.assertRaisesRegex(ValueError, "symmetry"):
            parse_stress(text, Decimal("-2.0"), config)

    def test_cube_full_lattice_gate_rejects_determinant_preserving_axis_swap(self) -> None:
        config = load_config(PROJECT_ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            stru = Path(temporary) / "STRU"
            stru.write_text(
                """ATOMIC_SPECIES
Al 26.9815385 Al_std.upf upf201

LATTICE_CONSTANT
2.0

LATTICE_VECTORS
1 0 0
0 1 0
0 0 1

ATOMIC_POSITIONS
Direct
Al
0.0
1
0 0 0 0 0 0
""",
                encoding="utf-8",
            )
            base = {
                "origin_bohr": (0.0, 0.0, 0.0),
                "dimensions": (2, 2, 2),
                "atom_rows": ((13.0, 3.0, 0.0, 0.0, 0.0),),
            }
            accepted = SimpleNamespace(**base, axis_steps_bohr=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)))
            self.assertTrue(validate_cube_atoms(accepted, stru, "al", {"z_valence": 3.0}, config)["accepted"])
            swapped = SimpleNamespace(**base, axis_steps_bohr=((0.0, 1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)))
            with self.assertRaisesRegex(ValueError, "lattice components"):
                validate_cube_atoms(swapped, stru, "al", {"z_valence": 3.0}, config)

    def test_physical_core_collision_finds_hyperthread_sibling(self) -> None:
        config = json.loads(json.dumps(load_config(PROJECT_ROOT)))
        config["runtime"]["physical_socket_id"] = 0
        config["runtime"]["physical_core_ids"] = [30]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sys_cpu = root / "sys"
            proc = root / "proc"
            sys_cpu.mkdir()
            proc.mkdir()
            (sys_cpu / "online").write_text("30,106\n", encoding="ascii")
            for cpu in (30, 106):
                topology = sys_cpu / f"cpu{cpu}" / "topology"
                topology.mkdir(parents=True)
                (topology / "physical_package_id").write_text("0\n", encoding="ascii")
                (topology / "core_id").write_text("30\n", encoding="ascii")
                (topology / "thread_siblings_list").write_text("30,106\n", encoding="ascii")
            process = proc / "100"
            task = process / "task" / "100"
            task.mkdir(parents=True)
            (process / "status").write_text(f"Uid:\t{os.getuid()}\t{os.getuid()}\t{os.getuid()}\t{os.getuid()}\n", encoding="utf-8")
            (process / "cmdline").write_bytes(b"foreign-solver\0")
            (task / "status").write_text("Cpus_allowed_list:\t106\n", encoding="utf-8")
            observed = inspect_core_collisions(config, sys_cpu_root=sys_cpu, proc_root=proc, excluded_pids={1, os.getpid()})
        self.assertFalse(observed["accepted"])
        self.assertEqual(observed["collisions"][0]["overlap_logical_cpus"], [106])

    def test_source_snapshot_uses_absolute_sha256sum_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "a").write_bytes(b"a")
            (root / "b").write_bytes(b"bb")
            snapshot = source_snapshot(root)
        self.assertEqual(snapshot["file_count"], 2)
        self.assertEqual(snapshot["regular_file_bytes"], 3)
        self.assertEqual(len(snapshot["absolute_path_sha256sum_list_digest"]), 64)


if __name__ == "__main__":
    unittest.main()
