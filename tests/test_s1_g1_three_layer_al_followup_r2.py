#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from analyze_s1_g1_three_layer_al_followup_r2 import evaluate_gates  # noqa: E402
from parse_s1_g1_three_layer_al_followup_r2 import (  # noqa: E402
    parse_eig_occ,
    parse_force_stress,
    require_zero_cube_origin,
)
from run_s1_g1_three_layer_al_followup_r2 import (  # noqa: E402
    acquire_core_locks,
    validate_core_reservation_ack,
    verify_accepted_source,
)
from s1_g1_three_layer_al_followup_r2_common import (  # noqa: E402
    canonical_json_bytes,
    geometry_payload_bytes,
    parse_cpu_list,
    render_strain_from_base,
    sha256_bytes,
    verify_strain_geometry,
)


class AlDomainFollowupR2Tests(unittest.TestCase):
    def config(self) -> dict:
        return {
            "protocol_revision": "S1-G1-THREE-LAYER-AL-DOMAIN-FOLLOWUP-20260810-R2",
            "acceptance": {
                "electron_relative_error_strictly_less_than": 1e-10,
                "last_band_occupation_strictly_less_than": 1e-8,
                "stress_symmetry_abs_kbar_max": 1e-10,
                "stress_trace_pressure_abs_kbar_strictly_less_than": 1e-6,
                "strain_anchored_difference_mev_per_atom_max": 20.0,
                "endpoint_anchored_k_difference_mev_per_atom_strictly_less_than": 2.0,
                "endpoint_anchored_cutoff_difference_mev_per_atom_strictly_less_than": 1.0,
                "endpoint_cutoff_pressure_difference_gpa_strictly_less_than": 0.02,
            },
        }

    @staticmethod
    def base_stru() -> bytes:
        return (
            "ATOMIC_SPECIES\nAl 26.9815385 al.gga.psp blps\n\n"
            "LATTICE_CONSTANT\n1.8897261254578281\n\nLATTICE_VECTORS\n"
            "0.0000000000000000 2.0249999999999999 2.0249999999999999\n"
            "2.0249999999999999 0.0000000000000000 2.0249999999999999\n"
            "2.0249999999999999 2.0249999999999999 0.0000000000000000\n\n"
            "ATOMIC_POSITIONS\nDirect\n\nAl\n0.0\n1\n"
            "0.0000000000000000 0.0000000000000000 0.0000000000000000 1 1 1\n"
        ).encode()

    def test_strain_is_rebuilt_from_043_with_a_f_transpose(self) -> None:
        f = [[1.005, 0.0, 0.0], [0.0, (1.005) ** -0.5, 0.0], [0.0, 0.0, (1.005) ** -0.5]]
        output = render_strain_from_base(self.base_stru(), f)
        reference = output.replace(b"Al_std.upf upf201", b"al.gga.psp blps")
        gate = verify_strain_geometry(self.base_stru(), output, reference, f)
        self.assertTrue(gate["accepted"])
        self.assertAlmostEqual(gate["determinant_f"], 1.0)
        self.assertIn(b"2.0199564056179566", output)
        self.assertIn(b"2.0351249999999999", output)
        self.assertEqual(geometry_payload_bytes(output), geometry_payload_bytes(reference))

    def test_wrong_strain_amplitude_or_direct_coordinate_fails(self) -> None:
        f = [[1.0, 0.005, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        output = render_strain_from_base(self.base_stru(), f)
        wrong_reference = output.replace(b"0.0101250000000000", b"0.0202500000000000")
        with self.assertRaises(ValueError):
            verify_strain_geometry(self.base_stru(), output, wrong_reference, f)
        changed_direct = output.replace(b"0.0000000000000000 1 1 1", b"0.1000000000000000 1 1 1")
        with self.assertRaises(ValueError):
            verify_strain_geometry(self.base_stru(), changed_direct, output, f)
        with self.assertRaises(ValueError):
            render_strain_from_base(self.base_stru(), [[1.01, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])

    def test_cube_origin_is_explicit_exact_zero_gate(self) -> None:
        self.assertEqual(require_zero_cube_origin([0.0, -0.0, 0.0]), 0.0)
        with self.assertRaises(ValueError):
            require_zero_cube_origin([1e-16, 0.0, 0.0])

    def test_cpu_list_parser(self) -> None:
        self.assertEqual(parse_cpu_list("0-2,5,40-43"), {0, 1, 2, 5, 40, 41, 42, 43})

    def test_eig_occ_ne_and_last_band_gates(self) -> None:
        content = """1 # ionic step
 spin=1 k-point=1/2 Cartesian=0 0 0 (1 plane wave)
 1 -1.0 1.5
 2 2.0 0.0
 spin=1 k-point=2/2 Cartesian=0 0 0 (1 plane wave)
 1 -1.0 1.5
 2 2.0 0.0
"""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "eig_occ.txt"
            path.write_text(content)
            result = parse_eig_occ(path, 2, 3.0, self.config())
        self.assertTrue(result["accepted"])
        self.assertEqual(result["row_count"], 4)

    def test_force_stress_trace_gate(self) -> None:
        text = """#TOTAL-FORCE (eV/Angstrom)#
       Al1 0.0 0.0 0.0
 #TOTAL-STRESS (kbar)#
 1.0 0.0 0.0
 0.0 2.0 0.0
 0.0 0.0 3.0
 #TOTAL-PRESSURE# (EXCLUDE KINETIC PART OF IONS): 2.0 kbar
"""
        self.assertTrue(parse_force_stress(text, 1, 2.0, self.config())["accepted"])

    def test_accepted_source_binds_marker_result_and_evidence(self) -> None:
        protocol = "SOURCE-R1"
        runner = "a" * 40
        experiment_id = "S1-20260810-301"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = root / "runs" / experiment_id
            run.mkdir(parents=True)
            evidence = b"registered evidence\n"
            (run / "INPUT").write_bytes(evidence)
            result = {"schema_version": 1, "protocol_revision": protocol, "status": "accepted", "experiment_id": experiment_id, "evidence_files": [{"path": "INPUT", "sha256": sha256_bytes(evidence), "size_bytes": len(evidence)}]}
            (run / "result.json").write_bytes(canonical_json_bytes(result))
            (run / "runner_return.json").write_bytes(canonical_json_bytes({"schema_version": 1, "experiment_id": experiment_id, "return_code": 0}))
            marker = {"schema_version": 1, "protocol_revision": protocol, "status": "accepted", "experiment_id": experiment_id, "runner_commit": runner, "result_sha256": sha256_bytes((run / "result.json").read_bytes())}
            (root / "accepted").mkdir()
            marker_path = root / "accepted" / f"{experiment_id}.json"
            marker_path.write_bytes(canonical_json_bytes(marker))
            session = {"protocol_revision": protocol, "runner_commit": runner}
            self.assertTrue(verify_accepted_source(root, experiment_id, session)["accepted"])
            marker["result_sha256"] = "0" * 64
            marker_path.write_bytes(canonical_json_bytes(marker))
            with self.assertRaises(ValueError):
                verify_accepted_source(root, experiment_id, session)

    def test_core_ack_and_lock_are_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {"protocol_revision": "P", "runtime": {"required_hostname": "node01", "physical_core_ids": [40, 41, 42, 43], "core_lock_root": str(root / "locks")}}
            ack = {"schema_version": 1, "status": "exclusive_core_reservation_acknowledged", "protocol_revision": "P", "hostname": "node01", "physical_core_ids": [40, 41, 42, 43], "conflicting_workflows_checked": True, "single_runner_exclusive_use": True, "acknowledged_by": "test"}
            path = root / "ack.json"
            path.write_bytes(canonical_json_bytes(ack))
            self.assertTrue(validate_core_reservation_ack(path, config)["accepted"])
            handles, proof = acquire_core_locks(config)
            try:
                self.assertEqual(len(proof), 4)
                with self.assertRaises(ValueError):
                    acquire_core_locks(config)
            finally:
                for handle in handles:
                    handle.close()
            ack["conflicting_workflows_checked"] = False
            path.write_bytes(canonical_json_bytes(ack))
            with self.assertRaises(ValueError):
                validate_core_reservation_ack(path, config)

    def test_galileo_uses_327_328_and_strict_endpoint_limits(self) -> None:
        def result(value: float, pressure: float = 0.0) -> dict:
            return {"thermodynamic_labels_ev_per_atom": {"E_ec": value}, "pressure_gpa": pressure}

        parent = {
            "S1-20260810-301": result(-10.000), "S1-20260810-302": result(-9.900),
            "S1-20260810-303": result(-9.800), "S1-20260810-327": result(-9.950),
            "S1-20260810-328": result(-9.940),
        }
        new = {
            **{f"S1-20260810-{number}": result(-9.980) for number in range(335, 339)},
            "S1-20260810-339": result(-9.850), "S1-20260810-340": result(-9.750, 0.01),
            "S1-20260810-341": result(-9.840), "S1-20260810-342": result(-9.740, 0.01),
        }
        local = {"S1-20260807-043": -20.000}
        local.update({f"S1-20260810-{number}": -19.980 for number in range(204, 208)})
        gates, _ = evaluate_gates(new, parent, local, self.config())
        self.assertEqual(gates["status"], "accepted")
        new["S1-20260810-339"] = result(-9.848)
        gates, _ = evaluate_gates(new, parent, local, self.config())
        self.assertEqual(gates["endpoints"]["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
