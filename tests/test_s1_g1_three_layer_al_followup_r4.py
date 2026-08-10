#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from analyze_s1_g1_three_layer_al_followup_r4 import copy_run_snapshot, evaluate_gates  # noqa: E402
from parse_s1_g1_three_layer_al_followup_r4 import (  # noqa: E402
    parse_eig_occ,
    parse_force_stress,
    require_zero_cube_origin,
    validate_followup_metadata_schema,
)
from run_s1_g1_three_layer_al_followup_r4 import (  # noqa: E402
    acquire_core_locks,
    evidence_only_formalization_identity,
    preregistration_identity,
    validate_core_reservation_ack,
    verify_accepted_source,
    verify_parent_sources,
    verify_pseudo_identity_closure,
)
from run_s1_g1_three_layer_al_followup_r4_binding_smoke import validate_rank_payloads  # noqa: E402
from s1_g1_three_layer_al_followup_r4_rank_wrapper import validate_current_rank  # noqa: E402
from s1_g1_three_layer_al_followup_r4_common import (  # noqa: E402
    canonical_json_bytes,
    geometry_payload_bytes,
    parse_cpu_list,
    render_strain_from_base,
    sha256_bytes,
    sha256_file,
    verify_strain_geometry,
)


class AlDomainFollowupR4Tests(unittest.TestCase):
    def config(self) -> dict:
        return {
            "protocol_revision": "S1-G1-THREE-LAYER-AL-DOMAIN-FOLLOWUP-20260810-R4",
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

    def test_role_only_metadata_maps_in_memory_and_legacy_mix_fails(self) -> None:
        protocol = "S1-G1-THREE-LAYER-AL-DOMAIN-FOLLOWUP-20260810-R4"
        role_only = {
            "schema_version": 1,
            "protocol_revision": protocol,
            "experiment_id": "S1-20260810-351",
            "phase": "strain",
            "role": "al_tetragonal_plus",
        }
        mapped, proof = validate_followup_metadata_schema(role_only, protocol)
        self.assertNotIn("requirement", role_only)
        self.assertEqual(mapped["requirement"], role_only["role"])
        self.assertTrue(proof["accepted"])
        self.assertFalse(proof["legacy_requirement_was_present_in_raw"])
        with self.assertRaises(ValueError):
            validate_followup_metadata_schema({**role_only, "requirement": role_only["role"]}, protocol)
        without_role = {key: value for key, value in role_only.items() if key != "role"}
        with self.assertRaises(ValueError):
            validate_followup_metadata_schema(without_role, protocol)
        with self.assertRaises(ValueError):
            validate_followup_metadata_schema({**role_only, "phase": "endpoint"}, protocol)

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
        wrong_axis = [[1.0, 0.0, 0.0], [0.005, 1.0, 0.0], [0.0, 0.0, 1.0]]
        with self.assertRaises(ValueError):
            verify_strain_geometry(self.base_stru(), output, output, wrong_axis)
        opposite_sign = [[1.0, -0.005, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        with self.assertRaises(ValueError):
            verify_strain_geometry(self.base_stru(), output, output, opposite_sign)

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
        with self.assertRaises(ValueError):
            parse_force_stress(text.replace("0.0 2.0 0.0", "0.1 2.0 0.0"), 1, 2.0, self.config())

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

    def test_new_source_chain_binds_attempt_session_runner_and_registered_files(self) -> None:
        protocol = "FOLLOWUP-R4"
        runner = "a" * 40
        config_sha = "b" * 64
        manifest_sha = "c" * 64
        experiment_id = "S1-20260810-351"
        common = {
            "protocol_revision": protocol,
            "experiment_id": experiment_id,
            "runner_commit": runner,
            "config_sha256": config_sha,
            "manifest_sha256": manifest_sha,
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = root / "runs" / experiment_id
            run.mkdir(parents=True)
            (run / "INPUT").write_bytes(b"input\n")
            result = {
                "schema_version": 1, "protocol_revision": protocol, "status": "accepted",
                "experiment_id": experiment_id,
                "orchestration_identity": dict(common),
                "evidence_files": [{"path": "INPUT", "sha256": sha256_file(run / "INPUT"), "size_bytes": 6}],
            }
            (run / "result.json").write_bytes(canonical_json_bytes(result))
            (run / "runner_return.json").write_bytes(canonical_json_bytes({"schema_version": 1, "return_code": 0, **common}))
            (run / "metadata.json").write_bytes(canonical_json_bytes({"schema_version": 1, **common}))
            (root / "attempts").mkdir()
            attempt_path = root / "attempts" / f"{experiment_id}.json"
            attempt_path.write_bytes(canonical_json_bytes({"schema_version": 1, "status": "formal_attempt_started", **common}))
            (root / "accepted").mkdir()
            marker = {"schema_version": 1, "status": "accepted", "result_sha256": sha256_file(run / "result.json"), **common}
            (root / "accepted" / f"{experiment_id}.json").write_bytes(canonical_json_bytes(marker))
            session = {"schema_version": 1, "protocol_revision": protocol, "runner_commit": runner, "config_sha256": config_sha, "manifest_sha256": manifest_sha}
            identity = verify_accepted_source(
                root, experiment_id, session, require_complete_orchestration=True,
                expected_config_sha256=config_sha, expected_manifest_sha256=manifest_sha,
            )
            self.assertTrue(identity["attempt_marker_sha256"])
            attempt = json.loads(attempt_path.read_text())
            attempt["config_sha256"] = "0" * 64
            attempt_path.write_bytes(canonical_json_bytes(attempt))
            with self.assertRaises(ValueError):
                verify_accepted_source(
                    root, experiment_id, session, require_complete_orchestration=True,
                    expected_config_sha256=config_sha, expected_manifest_sha256=manifest_sha,
                )

    def test_external_pseudo_identity_replay_fails_without_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = root / "run"
            run.mkdir()
            sha = "d" * 64
            identity = {
                "basename": "Al_std.upf", "sha256": sha, "upf_version": "2.0.1",
                "pseudo_type": "NC", "functional": "PBE", "z_valence": 3.0,
                "number_of_proj": 6, "core_correction": True,
                "pp_beta_element_count": 6, "beta_angular_momenta": [0, 0, 1, 1, 2, 2],
                "expanded_nonlocal_projectors_per_atom": 18, "pp_dij_present": True,
            }
            contract = {
                "basename": "Al_std.upf",
                "url": "https://raw.githubusercontent.com/PseudoDojo/ONCVPSP-PBE-SR/" + "e" * 40 + "/Al/Al_std.upf",
                "sha256": sha, "git_blob_sha1": "f" * 40, "upf_version": "2.0.1",
                "pseudo_type": "NC", "functional": "PBE", "z_valence": 3.0,
                "number_of_proj_per_atom": 6, "expanded_nonlocal_projectors_per_atom": 18,
                "core_correction": True,
            }
            metadata = {
                "pseudo": {"basename": "Al_std.upf", "sha256": sha, "upstream_commit": "e" * 40, "upstream_url": contract["url"]},
                "pseudo_runtime_identity": identity,
            }
            (run / "metadata.json").write_bytes(canonical_json_bytes(metadata))
            (run / "pseudo_identity.json").write_bytes(canonical_json_bytes(identity))
            config = {
                "external_pseudo_cache": str(root / "missing-cache"),
                "pseudodojo": {"repository": "PseudoDojo/ONCVPSP-PBE-SR", "commit": "e" * 40, "materials": {"al": contract}},
            }
            result = {"material": "al", "atom_count": 1, "runtime_nonlocal_projectors_total": 18, "pseudo_identity": identity}
            with self.assertRaises(ValueError):
                verify_pseudo_identity_closure(run, result, config, require_run_body=False)

    def test_snapshot_copies_enhanced_union_but_excludes_upf_body(self) -> None:
        experiment_id = "S1-20260810-301"
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            destination = Path(temporary) / "destination"
            run = source / "runs" / experiment_id
            run.mkdir(parents=True)
            files = {"INPUT": b"input\n", "OUT.test/eig_occ.txt": b"eig\n", "Al_std.upf": b"pseudo\n"}
            for relative, content in files.items():
                path = run / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            result = {
                "evidence_files": [{"path": "INPUT", "sha256": sha256_file(run / "INPUT"), "size_bytes": 6}],
            }
            (run / "result.json").write_bytes(canonical_json_bytes(result))
            (run / "runner_return.json").write_bytes(canonical_json_bytes({"return_code": 0}))
            (source / "accepted").mkdir()
            (source / "accepted" / f"{experiment_id}.json").write_bytes(canonical_json_bytes({"status": "accepted"}))
            (source / "attempts").mkdir()
            (source / "attempts" / f"{experiment_id}.json").write_bytes(canonical_json_bytes({"status": "formal_attempt_started"}))
            enhanced = [
                {"path": relative, "sha256": sha256_file(run / relative), "size_bytes": (run / relative).stat().st_size}
                for relative in ("OUT.test/eig_occ.txt", "Al_std.upf")
            ]
            copy_run_snapshot(source, destination, experiment_id, include_attempt=True, additional_evidence=enhanced)
            self.assertTrue((destination / "runs" / experiment_id / "OUT.test/eig_occ.txt").is_file())
            self.assertFalse((destination / "runs" / experiment_id / "Al_std.upf").exists())

    def test_parent_gate_requires_recovery_and_endpoint_phase_closure(self) -> None:
        old_protocol = "OLD-R1"
        continuation_protocol = "CONT-R2"
        old_runner = "a" * 40
        continuation_runner = "b" * 40
        pseudo_sha = "c" * 64

        def write_source(root: Path, experiment_id: str, protocol: str, runner: str, *, al: bool) -> dict:
            run = root / "runs" / experiment_id
            run.mkdir(parents=True)
            input_bytes = f"input {experiment_id}\n".encode()
            stru_bytes = b"registered endpoint geometry\n" if experiment_id in {"S1-20260810-327", "S1-20260810-328"} else f"stru {experiment_id}\n".encode()
            (run / "INPUT").write_bytes(input_bytes)
            (run / "STRU").write_bytes(stru_bytes)
            evidence = [
                {"path": "INPUT", "sha256": sha256_bytes(input_bytes), "size_bytes": len(input_bytes)},
                {"path": "STRU", "sha256": sha256_bytes(stru_bytes), "size_bytes": len(stru_bytes)},
            ]
            result = {
                "schema_version": 1, "protocol_revision": protocol, "status": "accepted",
                "experiment_id": experiment_id, "evidence_files": evidence,
                "runtime_nonlocal_projectors_total": 18,
                "pseudo_identity": {"sha256": pseudo_sha if al else "d" * 64},
            }
            (run / "result.json").write_bytes(canonical_json_bytes(result))
            (run / "runner_return.json").write_bytes(canonical_json_bytes({"schema_version": 1, "experiment_id": experiment_id, "return_code": 0}))
            marker = {
                "schema_version": 1, "protocol_revision": protocol, "status": "accepted",
                "experiment_id": experiment_id, "runner_commit": runner,
                "result_sha256": sha256_file(run / "result.json"),
            }
            (root / "accepted").mkdir(exist_ok=True)
            (root / "accepted" / f"{experiment_id}.json").write_bytes(canonical_json_bytes(marker))
            (root / "attempts").mkdir(exist_ok=True)
            (root / "attempts" / f"{experiment_id}.json").write_bytes(canonical_json_bytes({"schema_version": 1, "experiment_id": experiment_id, "status": "formal_attempt_started"}))
            return verify_accepted_source(root, experiment_id, {"protocol_revision": protocol, "runner_commit": runner})

        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            old = Path(temporary) / "old"
            continuation = Path(temporary) / "continuation"
            old.mkdir()
            continuation.mkdir()
            old_session = {"schema_version": 1, "protocol_revision": old_protocol, "runner_commit": old_runner, "status": "active"}
            (old / "session.json").write_bytes(canonical_json_bytes(old_session))
            recovery_ids = [f"S1-20260810-{number:03d}" for number in range(301, 307)]
            inventory = []
            for experiment_id in recovery_ids:
                identity = write_source(old, experiment_id, old_protocol, old_runner, al=experiment_id in recovery_ids[:3])
                result = json.loads((old / "runs" / experiment_id / "result.json").read_text())
                inventory.append({
                    "experiment_id": experiment_id,
                    "attempt_sha256": sha256_file(old / "attempts" / f"{experiment_id}.json"),
                    "accepted_sha256": identity["accepted_marker_sha256"],
                    "accepted_result_sha256": identity["result_sha256"],
                    "result_sha256": identity["result_sha256"],
                    "runner_return_sha256": identity["runner_return_sha256"],
                    "runner_return_code": 0,
                    "r1_parser_byte_exact_replay": True,
                    "enhanced_raw_gates": {
                        "accepted": True, "affinity": {"accepted": True}, "cube_geometry": {"accepted": True},
                        "stress_trace_gate": {"accepted": True}, "eig_occupations": {"accepted": True},
                        "evidence_files": result["evidence_files"],
                    },
                    "status": "accepted_source_evidence",
                })
            for experiment_id in (f"S1-20260810-{number:03d}" for number in range(327, 333)):
                write_source(continuation, experiment_id, continuation_protocol, continuation_runner, al=True)
            barrier = {
                "schema_version": 2, "protocol_revision": continuation_protocol, "status": "accepted",
                "source_operational_status": "incomplete_missing_phase_marker_indeterminate_late_orchestration_disconnect",
                "source_operational_phase_accepted": False, "scientific_p0_recovery_status": "accepted",
                "source_state_root": str(old), "source_session_sha256": sha256_file(old / "session.json"),
                "source_runner_commit": old_runner, "source_snapshot": {"file_count": 1}, "source_git_identities": [],
                "accepted_source_ids": recovery_ids, "accepted_source_count": 6, "new_run_count": 0,
                "permanently_unexecuted_source_ids": [f"S1-20260810-{number:03d}" for number in range(307, 319)],
                "p0_metrics": {"status": "accepted"}, "per_run_recovery": inventory,
                "scope": {"r1_phase_marker_reconstructed": False}, "continuation_prereg_commit": continuation_runner,
                "config_sha256": "e" * 64, "manifest_sha256": "f" * 64,
            }
            (continuation / "barriers").mkdir()
            (continuation / "barriers" / "r1_p0_recovery.json").write_bytes(canonical_json_bytes(barrier))
            continuation_session = {
                "schema_version": 1, "protocol_revision": continuation_protocol, "runner_commit": continuation_runner,
                "status": "active", "recovery_prereg_commit": continuation_runner,
                "recovery_source_runner_commit": old_runner,
                "recovery_barrier_sha256": sha256_file(continuation / "barriers" / "r1_p0_recovery.json"),
                "config_sha256": "e" * 64, "manifest_sha256": "f" * 64,
            }
            (continuation / "session.json").write_bytes(canonical_json_bytes(continuation_session))
            phase = {
                "schema_version": 1, "protocol_revision": continuation_protocol, "runner_commit": continuation_runner,
                "status": "accepted", "phase": "al_eos",
                "accepted_ids": [f"S1-20260810-{number:03d}" for number in range(327, 333)], "accepted_count": 6,
                "session_sha256": sha256_file(continuation / "session.json"),
                "config_sha256": "e" * 64, "manifest_sha256": "f" * 64,
                "recovery_barrier_sha256": sha256_file(continuation / "barriers" / "r1_p0_recovery.json"),
                "accepted_result_sha256": {
                    f"S1-20260810-{number:03d}": sha256_file(continuation / "runs" / f"S1-20260810-{number:03d}" / "result.json")
                    for number in range(327, 333)
                },
            }
            (continuation / "phases").mkdir()
            phase_path = continuation / "phases" / "al_eos.json"
            phase_path.write_bytes(canonical_json_bytes(phase))
            rows = []
            for common_id, relative in (("S1-20260810-327", "registered/v090/STRU"), ("S1-20260810-328", "registered/v110/STRU")):
                registered = project / relative
                registered.parent.mkdir(parents=True, exist_ok=True)
                registered.write_bytes(b"registered endpoint geometry\n")
                rows.append({"accepted_common_id": common_id, "registered_geometry_path": relative, "registered_geometry_stru_sha256": sha256_file(registered)})
            config = {
                "pseudodojo": {"materials": {"al": {"sha256": pseudo_sha}}},
                "source_states": {
                    "r1_p0": {"external_state_root": str(old), "protocol_revision": old_protocol, "runner_commit": old_runner, "session_sha256": sha256_file(old / "session.json"), "operational_status": "incomplete_missing_phase_marker_indeterminate_late_orchestration_disconnect", "source_snapshot": {"file_count": 1}, "source_git_paths": [], "required_accepted_ids": recovery_ids, "permanently_unexecuted_ids": [f"S1-20260810-{number:03d}" for number in range(307, 319)]},
                    "continuation_r2": {"external_state_root": str(continuation), "protocol_revision": continuation_protocol, "preregistration_commit": continuation_runner, "recovery_formalization_commit": continuation_runner, "recovery_barrier_sha256": sha256_file(continuation / "barriers" / "r1_p0_recovery.json"), "recovery_barrier_relative_path": "barriers/r1_p0_recovery.json", "versioned_recovery_barrier_path": "unused-in-no-project-test", "config_path": "unused", "manifest_path": "unused", "endpoint_phase_marker_relative_path": "phases/al_eos.json", "endpoint_phase": "al_eos", "endpoint_phase_accepted_ids": [f"S1-20260810-{number:03d}" for number in range(327, 333)], "required_recovery_ids": recovery_ids, "required_accepted_ids": ["S1-20260810-327", "S1-20260810-328"]},
                },
            }
            with (
                patch("run_s1_g1_three_layer_al_followup_r4.replay_continuation_al_raw", return_value={"accepted": True}),
                patch("run_s1_g1_three_layer_al_followup_r4.verify_pseudo_identity_closure", return_value={"accepted": True}),
                patch("run_s1_g1_three_layer_al_followup_r4.verify_continuation_phase_preflight", return_value={"accepted": True}),
            ):
                self.assertTrue(verify_parent_sources(config)["ready"])
                original_result_sha = phase["accepted_result_sha256"]["S1-20260810-327"]
                phase["accepted_result_sha256"]["S1-20260810-327"] = "0" * 64
                phase_path.write_bytes(canonical_json_bytes(phase))
                with self.assertRaises(ValueError):
                    verify_parent_sources(config)
                phase["accepted_result_sha256"]["S1-20260810-327"] = original_result_sha
                phase["accepted_count"] = 1
                phase_path.write_bytes(canonical_json_bytes(phase))
                with self.assertRaises(ValueError):
                    verify_parent_sources(config)

    def test_core_ack_and_lock_are_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            logical = [30, 31, 32, 33, 106, 107, 108, 109]
            runtime = {
                "required_hostname": "node01",
                "required_physical_package_id": 0,
                "primary_os_logical_cpu_ids_by_rank": [30, 31, 32, 33],
                "sysfs_core_id_by_rank": [30, 31, 32, 33],
                "thread_siblings_by_rank": [[30, 106], [31, 107], [32, 108], [33, 109]],
                "reserved_os_logical_cpu_ids": logical,
                "core_lock_root": str(root / "locks"),
            }
            config = {"protocol_revision": "P", "runtime": runtime}
            ack = {
                "schema_version": 1,
                "status": "exclusive_core_reservation_acknowledged",
                "protocol_revision": "P",
                "hostname": "node01",
                "physical_package_id": 0,
                "primary_os_logical_cpu_ids_by_rank": [30, 31, 32, 33],
                "sysfs_core_id_by_rank": [30, 31, 32, 33],
                "thread_siblings_by_rank": [[30, 106], [31, 107], [32, 108], [33, 109]],
                "reserved_os_logical_cpu_ids": logical,
                "conflicting_workflows_checked": True,
                "single_runner_exclusive_use": True,
                "acknowledged_by": "test",
            }
            path = root / "ack.json"
            path.write_bytes(canonical_json_bytes(ack))
            self.assertTrue(validate_core_reservation_ack(path, config)["accepted"])
            handles, proof = acquire_core_locks(config)
            try:
                self.assertEqual(len(proof), 8)
                with self.assertRaises(ValueError):
                    acquire_core_locks(config)
            finally:
                for handle in handles:
                    handle.close()
            ack["conflicting_workflows_checked"] = False
            path.write_bytes(canonical_json_bytes(ack))
            with self.assertRaises(ValueError):
                validate_core_reservation_ack(path, config)

    def test_rank_wrapper_distinguishes_os_cpu_package_core_and_siblings(self) -> None:
        config = {
            "protocol_revision": "P",
            "runtime": {
                "rank_count": 4,
                "required_hostname": "node01",
                "required_physical_package_id": 0,
                "primary_os_logical_cpu_ids_by_rank": [30, 31, 32, 33],
                "sysfs_core_id_by_rank": [30, 31, 32, 33],
                "thread_siblings_by_rank": [[30, 106], [31, 107], [32, 108], [33, 109]],
            },
        }
        topology = {
            30: {"os_logical_cpu_id": 30, "physical_package_id": 0, "sysfs_core_id": 30, "thread_siblings": [30, 106]},
            106: {"os_logical_cpu_id": 106, "physical_package_id": 0, "sysfs_core_id": 30, "thread_siblings": [30, 106]},
        }
        with (
            patch("s1_g1_three_layer_al_followup_r4_rank_wrapper.socket.gethostname", return_value="node01"),
            patch("s1_g1_three_layer_al_followup_r4_rank_wrapper.os.sched_getaffinity", return_value={30, 106}),
            patch("s1_g1_three_layer_al_followup_r4_rank_wrapper.read_topology", side_effect=lambda cpu: topology[cpu]),
        ):
            payload = validate_current_rank(config, 0, 0, "smoke")
        self.assertEqual(payload["primary_os_logical_cpu_id"], 30)
        self.assertEqual(payload["physical_package_id"], 0)
        self.assertEqual(payload["sysfs_core_id"], 30)
        self.assertEqual(payload["thread_siblings"], [30, 106])

        wrong_package = {cpu: {**row, "physical_package_id": 1} for cpu, row in topology.items()}
        with (
            patch("s1_g1_three_layer_al_followup_r4_rank_wrapper.socket.gethostname", return_value="node01"),
            patch("s1_g1_three_layer_al_followup_r4_rank_wrapper.os.sched_getaffinity", return_value={30, 106}),
            patch("s1_g1_three_layer_al_followup_r4_rank_wrapper.read_topology", side_effect=lambda cpu: wrong_package[cpu]),
            self.assertRaises(ValueError),
        ):
            validate_current_rank(config, 0, 0, "smoke")

        wrong_core = {cpu: {**row, "sysfs_core_id": 2} for cpu, row in topology.items()}
        with (
            patch("s1_g1_three_layer_al_followup_r4_rank_wrapper.socket.gethostname", return_value="node01"),
            patch("s1_g1_three_layer_al_followup_r4_rank_wrapper.os.sched_getaffinity", return_value={30, 106}),
            patch("s1_g1_three_layer_al_followup_r4_rank_wrapper.read_topology", side_effect=lambda cpu: wrong_core[cpu]),
            self.assertRaises(ValueError),
        ):
            validate_current_rank(config, 0, 0, "smoke")

    def test_smoke_rank_denominator_and_topology_are_fail_closed(self) -> None:
        config = {
            "runtime": {
                "rank_count": 4,
                "required_hostname": "node01",
                "required_physical_package_id": 0,
                "primary_os_logical_cpu_ids_by_rank": [30, 31, 32, 33],
                "sysfs_core_id_by_rank": [30, 31, 32, 33],
                "thread_siblings_by_rank": [[30, 106], [31, 107], [32, 108], [33, 109]],
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "ranks").mkdir()
            for rank in range(4):
                payload = {
                    "accepted": True,
                    "mode": "smoke",
                    "rank": rank,
                    "local_rank": rank,
                    "hostname": "node01",
                    "os_logical_cpu_affinity": config["runtime"]["thread_siblings_by_rank"][rank],
                    "primary_os_logical_cpu_id": config["runtime"]["primary_os_logical_cpu_ids_by_rank"][rank],
                    "physical_package_id": 0,
                    "sysfs_core_id": config["runtime"]["sysfs_core_id_by_rank"][rank],
                    "thread_siblings": config["runtime"]["thread_siblings_by_rank"][rank],
                    "config_sha256": sha256_file(PROJECT_ROOT / "config/S1_g1_three_layer_al_domain_followup_r4.json"),
                    "rank_wrapper_sha256": sha256_file(PROJECT_ROOT / "scripts/s1_g1_three_layer_al_followup_r4_rank_wrapper.py"),
                }
                (root / "ranks" / f"rank_{rank:03d}.json").write_bytes(canonical_json_bytes(payload))
            self.assertEqual(len(validate_rank_payloads(root, config)), 4)
            bad = json.loads((root / "ranks" / "rank_002.json").read_text())
            bad["sysfs_core_id"] = 2
            (root / "ranks" / "rank_002.json").write_bytes(canonical_json_bytes(bad))
            with self.assertRaises(ValueError):
                validate_rank_payloads(root, config)

    def test_preregistration_rejects_malicious_descendant_and_merge_parent(self) -> None:
        def run(repo: Path, *arguments: str) -> str:
            return subprocess.run(
                ["git", *arguments], cwd=repo, check=True, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            ).stdout.strip()

        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            run(repo, "init", "-q")
            run(repo, "config", "user.name", "R4 Test")
            run(repo, "config", "user.email", "r4-test@example.invalid")
            code_paths = [
                "scripts/run_s1_g1_three_layer_al_followup_r4_binding_smoke.py",
                "scripts/run_s1_g1_three_layer_al_followup_r4.py",
                "scripts/s1_g1_three_layer_al_followup_r4_rank_wrapper.py",
            ]
            formal_ids = [f"S1-TEST-{index}" for index in range(8)]
            prereg_paths = [
                "config/S1_g1_three_layer_al_domain_followup_r4.json",
                "docs/S1_G1_THREE_LAYER_AL_DOMAIN_FOLLOWUP_R4_PROTOCOL.md",
                *(f"inputs/r4/{experiment_id}/metadata.json" for experiment_id in formal_ids),
            ]
            for relative in [*code_paths, *prereg_paths]:
                path = repo / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"implementation:{relative}\n")
            run(repo, "add", ".")
            run(repo, "commit", "-q", "-m", "implementation")
            implementation = run(repo, "rev-parse", "HEAD")
            for relative in prereg_paths:
                (repo / relative).write_text(f"preregistration:{relative}\n")
            run(repo, "add", ".")
            run(repo, "commit", "-q", "-m", "preregister")
            prereg = run(repo, "rev-parse", "HEAD")
            config = {
                "implementation_commit": implementation,
                "input_root": "inputs/r4",
                "formal_ids": formal_ids,
            }
            self.assertTrue(preregistration_identity(repo, config, prereg)["accepted"])

            evidence_path = "orchestration/s1/r4/binding_smoke.json"
            run(repo, "checkout", "-q", "-b", "formal-ok", prereg)
            evidence = repo / evidence_path
            evidence.parent.mkdir(parents=True, exist_ok=True)
            evidence.write_text("accepted smoke\n")
            run(repo, "add", evidence_path)
            run(repo, "commit", "-q", "-m", "formalize smoke")
            formal = run(repo, "rev-parse", "HEAD")
            self.assertTrue(evidence_only_formalization_identity(repo, formal, prereg, evidence_path)["accepted"])

            run(repo, "checkout", "-q", "-b", "malicious", prereg)
            wrapper = repo / code_paths[-1]
            wrapper.write_text("malicious clean descendant\n")
            run(repo, "add", code_paths[-1])
            run(repo, "commit", "-q", "-m", "malicious descendant")
            with self.assertRaises(ValueError):
                preregistration_identity(repo, config, run(repo, "rev-parse", "HEAD"))

            run(repo, "checkout", "-q", "-b", "formal-side", prereg)
            (repo / "formal-side.txt").write_text("second formal parent\n")
            run(repo, "add", "formal-side.txt")
            run(repo, "commit", "-q", "-m", "formal side")
            run(repo, "checkout", "-q", "-b", "formal-merge", prereg)
            run(repo, "merge", "-q", "--no-ff", "formal-side", "-m", "formal merge parent")
            with self.assertRaises(ValueError):
                evidence_only_formalization_identity(repo, run(repo, "rev-parse", "HEAD"), prereg, evidence_path)

            run(repo, "checkout", "-q", "-b", "side", implementation)
            (repo / "side.txt").write_text("second parent\n")
            run(repo, "add", "side.txt")
            run(repo, "commit", "-q", "-m", "side")
            run(repo, "checkout", "-q", "-b", "mergecase", implementation)
            run(repo, "merge", "-q", "--no-ff", "side", "-m", "merge parent")
            with self.assertRaises(ValueError):
                preregistration_identity(repo, config, run(repo, "rev-parse", "HEAD"))

    def test_galileo_uses_327_328_and_strict_endpoint_limits(self) -> None:
        def result(value: float, pressure: float = 0.0) -> dict:
            return {"thermodynamic_labels_ev_per_atom": {"E_ec": value}, "pressure_gpa": pressure}

        parent = {
            "S1-20260810-301": result(-10.000), "S1-20260810-302": result(-9.900),
            "S1-20260810-303": result(-9.800), "S1-20260810-327": result(-9.950),
            "S1-20260810-328": result(-9.940),
        }
        new = {
            **{f"S1-20260810-{number}": result(-9.980) for number in range(351, 355)},
            "S1-20260810-355": result(-9.850), "S1-20260810-356": result(-9.750, 0.01),
            "S1-20260810-357": result(-9.840), "S1-20260810-358": result(-9.740, 0.01),
        }
        local = {"S1-20260807-043": -20.000}
        local.update({f"S1-20260810-{number}": -19.980 for number in range(204, 208)})
        gates, _ = evaluate_gates(new, parent, local, self.config())
        self.assertEqual(gates["status"], "accepted")
        new["S1-20260810-355"] = result(-9.848)
        gates, _ = evaluate_gates(new, parent, local, self.config())
        self.assertEqual(gates["endpoints"]["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
