from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import analyze_s1_g1_thermodynamic_label_audit_r1 as ANALYZER  # noqa: E402
import generate_s1_g1_thermodynamic_label_audit_r1 as GENERATOR  # noqa: E402
import s1_g1_thermodynamic_label_common as COMMON  # noqa: E402
import validate_s1_g1_thermodynamic_label_audit_r1 as VALIDATOR  # noqa: E402


class S1G1ThermodynamicLabelAuditR1Test(unittest.TestCase):
    def _write_structure(self, directory: Path, element: str = "Al") -> Path:
        pseudo = "al.gga.psp" if element == "Al" else "mg.gga.psp"
        mass = "26.9815" if element == "Al" else "24.305"
        path = directory / "STRU"
        path.write_text(
            "ATOMIC_SPECIES\n"
            f"{element} {mass} {pseudo}\n\n"
            "LATTICE_CONSTANT\n1.0\n\n"
            "LATTICE_VECTORS\n1 0 0\n0 1 0\n0 0 1\n\n"
            f"ATOMIC_POSITIONS\nDirect\n{element}\n0.0\n1\n0 0 0\n",
            encoding="utf-8",
        )
        return path

    def _write_cube(
        self,
        path: Path,
        values: tuple[float, float],
        *,
        atomic_number: int = 13,
        valence: float = 3.0,
    ) -> Path:
        path.write_text(
            "Ionic_Step 1 Cubefile created from ABACUS. Inner loop is z, followed by y and x\n"
            "1 # number of spin directions\n"
            "1 0.0 0.0 0.0\n"
            "2 0.5 0.0 0.0\n"
            "1 0.0 1.0 0.0\n"
            "1 0.0 0.0 1.0\n"
            f"{atomic_number} {valence:.1f} 0.0 0.0 0.0\n"
            f"{values[0]:.17e} {values[1]:.17e}\n",
            encoding="utf-8",
        )
        return path

    def _write_failure_inventory(self, run: Path, experiment_id: str) -> None:
        output = run / VALIDATOR.FAILURE_INVENTORY_NAME
        files = []
        for path in sorted(
            run.rglob("*"), key=lambda value: str(value.relative_to(run))
        ):
            if path == output or not path.is_file():
                continue
            files.append(
                {
                    "path": str(path.relative_to(run)),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "size_bytes": path.stat().st_size,
                }
            )
        output.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "protocol_revision": VALIDATOR.PROTOCOL_REVISION,
                    "experiment_id": experiment_id,
                    "files": files,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    def test_common_schema_and_execution_order_are_single_authority(self) -> None:
        self.assertIs(VALIDATOR.MANIFEST_FIELDS, COMMON.MANIFEST_FIELDS)
        self.assertEqual(VALIDATOR.RUN_IDS, COMMON.AUDIT_IDS)
        self.assertEqual(VALIDATOR.EXECUTION_ORDER, COMMON.EXECUTION_ORDER)
        self.assertEqual(VALIDATOR.PILOT_IDS, COMMON.PILOT_IDS)
        self.assertEqual(VALIDATOR.K_GATE_IDS, COMMON.K_GATE_EXECUTION_IDS)
        plan = GENERATOR.build_plan()
        by_id = {row["experiment_id"]: row for row in plan}
        self.assertEqual(by_id["S1-20260806-024"]["execution_phase"], "P0")
        self.assertEqual(by_id["S1-20260806-021"]["execution_phase"], "P1")
        self.assertEqual(by_id["S1-20260806-001"]["execution_phase"], "P2")

    def test_source_semantic_contract_is_independently_frozen(self) -> None:
        self.assertEqual(len(VALIDATOR.SOURCE_SEMANTIC_CONTRACT), 7)
        self.assertEqual(
            VALIDATOR.SOURCE_ARCHIVE_SHA256,
            GENERATOR.SOURCE_ARCHIVE_SHA256,
        )
        observed = tuple(
            (str(spec["relative_path"]), str(spec["sha256"]), tuple(spec["markers"]))
            for spec in GENERATOR.SOURCE_SEMANTIC_SPECS
        )
        self.assertEqual(VALIDATOR.SOURCE_SEMANTIC_CONTRACT, observed)

    def test_real_log_labels_and_total_decomposition_recompute(self) -> None:
        run = PROJECT_ROOT / "runs/S1-20260805-085"
        log = next(run.glob("OUT.*/running_scf.log"))
        raw = log.read_text(encoding="utf-8")
        parsed = COMMON.parse_thermodynamic_log(raw, expected_atom_count=1)
        labels = {"thermodynamic_labels": COMMON.json_safe(parsed)}
        result = json.loads((run / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(
            VALIDATOR._thermodynamic_failures(labels, result, raw, 1),
            [],
        )
        tampered = json.loads(json.dumps(labels))
        tampered["thermodynamic_labels"]["energy_labels_ev_per_cell"]["E_Hartree"] += 1.0
        failures = VALIDATOR._thermodynamic_failures(tampered, result, raw, 1)
        self.assertTrue(any("F=Eone+EH+Exc+EEwald+m" in value for value in failures))

    def test_field_metrics_use_finer_integrated_n_and_project_constant_gauge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            structure = self._write_structure(directory)
            rho_coarse = self._write_cube(directory / "rho_coarse.cube", (2.01, 3.99))
            rho_reference = self._write_cube(directory / "rho_reference.cube", (2.0, 4.0))
            pot_coarse = self._write_cube(directory / "pot_coarse.cube", (5.0, 7.0))
            pot_reference = self._write_cube(directory / "pot_reference.cube", (0.0, 2.0))
            result = VALIDATOR.field_metrics(
                rho_coarse,
                rho_reference,
                pot_coarse,
                pot_reference,
                structure_path=structure,
                expected_electron_count=3.0,
            )
            self.assertTrue(result["accepted"])
            self.assertAlmostEqual(result["reference_electrons"], 3.0)
            self.assertAlmostEqual(result["d1"], 1.0 / 300.0)
            self.assertAlmostEqual(result["dg"], 0.0)
            self.assertAlmostEqual(result["rms_g_ev"], 0.0)
            with self.assertRaisesRegex(ValueError, "electron integral failed"):
                VALIDATOR.field_metrics(
                    rho_coarse,
                    rho_reference,
                    pot_coarse,
                    pot_reference,
                    structure_path=structure,
                    expected_electron_count=4.0,
                )

    def test_full_cube_geometry_accepts_al_mg_and_rejects_axis_or_atom_tampering(self) -> None:
        for element, atomic_number, valence in (("Al", 13, 3.0), ("Mg", 12, 2.0)):
            with self.subTest(element=element), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                structure = self._write_structure(directory, element)
                cube_path = self._write_cube(
                    directory / "valid.cube",
                    (2.0, 4.0),
                    atomic_number=atomic_number,
                    valence=valence,
                )
                cube = COMMON.parse_abacus_cube(
                    cube_path,
                    quantity="density",
                    units="electron/bohr^3",
                    structure_path=structure,
                )
                VALIDATOR.validate_cube_geometry_against_stru(cube, structure)

                axis_path = directory / "axis_tampered.cube"
                axis_text = cube_path.read_text(encoding="utf-8").replace(
                    "2 0.5 0.0 0.0\n1 0.0 1.0 0.0",
                    "2 0.25 0.0 0.0\n1 0.0 2.0 0.0",
                )
                axis_path.write_text(axis_text, encoding="utf-8")
                axis_cube = COMMON.parse_abacus_cube(
                    axis_path,
                    quantity="density",
                    units="electron/bohr^3",
                    structure_path=structure,
                )
                with self.assertRaisesRegex(ValueError, "axis/grid"):
                    VALIDATOR.validate_cube_geometry_against_stru(
                        axis_cube, structure
                    )

                atom_path = directory / "atom_tampered.cube"
                atom_text = cube_path.read_text(encoding="utf-8").replace(
                    f"{atomic_number} {valence:.1f} 0.0 0.0 0.0",
                    f"{atomic_number + 1} {valence:.1f} 0.0 0.0 0.0",
                )
                atom_path.write_text(atom_text, encoding="utf-8")
                atom_cube = COMMON.parse_abacus_cube(
                    atom_path,
                    quantity="density",
                    units="electron/bohr^3",
                    structure_path=structure,
                )
                with self.assertRaisesRegex(ValueError, "identity/position"):
                    VALIDATOR.validate_cube_geometry_against_stru(
                        atom_cube, structure
                    )

    def test_half_quarter_pair_is_exact_and_fail_closed(self) -> None:
        quarter_id = "S1-20260806-021"
        rows = [
            {
                "experiment_id": quarter_id,
                "reference_experiment_id": "S1-20260806-007",
                "material": "al",
                "volume_ratio": "0.90",
            }
        ]
        with (
            mock.patch.object(VALIDATOR, "_find_output", return_value=Path("field.cube")),
            mock.patch.object(VALIDATOR, "expected_electrons", return_value=(3.0, {})),
            mock.patch.object(
                VALIDATOR,
                "field_metrics",
                return_value={"d1": 0.0, "d2": 0.0, "dg": 0.0, "rms_g_ev": 0.0, "accepted": True},
            ),
        ):
            payload = VALIDATOR.evaluate_half_quarter_pair(
                PROJECT_ROOT, rows, quarter_id, require_committed=False
            )
        self.assertEqual(payload["half_experiment_id"], "S1-20260806-007")
        self.assertTrue(payload["accepted"])
        with self.assertRaisesRegex(ValueError, "021--034"):
            VALIDATOR.evaluate_half_quarter_pair(
                PROJECT_ROOT, rows, "S1-20260806-035", require_committed=False
            )

    def test_pilot_gate_contains_exactly_the_two_registered_material_pairs(self) -> None:
        rows = [
            {"experiment_id": experiment_id, "material": material}
            for experiment_id, material in (
                ("S1-20260806-024", "al"),
                ("S1-20260806-036", "al"),
                ("S1-20260806-031", "mg"),
                ("S1-20260806-039", "mg"),
            )
        ]
        result_values = iter(
            (
                {
                    "zero_temp_extrapolated_energy_ev_per_atom": 1.0,
                    "pressure_gpa": 0.0,
                },
                {
                    "zero_temp_extrapolated_energy_ev_per_atom": 1.001,
                    "pressure_gpa": 0.01,
                },
                {
                    "zero_temp_extrapolated_energy_ev_per_atom": 2.0,
                    "pressure_gpa": 0.0,
                },
                {
                    "zero_temp_extrapolated_energy_ev_per_atom": 2.001,
                    "pressure_gpa": 0.01,
                },
            )
        )
        with (
            mock.patch.object(VALIDATOR, "expected_electrons", return_value=(3.0, {})),
            mock.patch.object(VALIDATOR, "_find_output", return_value=Path("field.cube")),
            mock.patch.object(
                VALIDATOR,
                "field_metrics",
                return_value={"accepted": True},
            ),
            mock.patch.object(
                VALIDATOR, "_row_result", side_effect=lambda *_args: next(result_values)
            ),
        ):
            payload = VALIDATOR.evaluate_pilot_gate(
                PROJECT_ROOT, rows, require_committed=False
            )
        self.assertEqual(payload["pair_count"], 2)
        self.assertEqual(
            [
                (row["material"], row["common_experiment_id"], row["extra_experiment_id"])
                for row in payload["pairs"]
            ],
            [
                ("al", "S1-20260806-024", "S1-20260806-036"),
                ("mg", "S1-20260806-031", "S1-20260806-039"),
            ],
        )
        self.assertTrue(payload["accepted"])

    def test_adjacent_curve_gates_are_strict_and_raw_v100_anchored(self) -> None:
        def points(offsets: tuple[float, ...]) -> list[dict[str, object]]:
            return [
                {
                    "volume_ratio": ratio,
                    "e_ec_ev_per_atom": energy,
                }
                for ratio, energy in zip(ANALYZER.RATIOS, offsets)
            ]

        coarse = points((0.004, 0.002, 0.001, 0.0, 0.001, 0.002, 0.004))
        fine = points((0.005, 0.003, 0.002, 0.001, 0.002, 0.003, 0.005))
        result = ANALYZER.compare_adjacent(
            coarse,
            fine,
            {"v0_angstrom3_per_atom": 16.0},
            {"v0_angstrom3_per_atom": 16.01},
        )
        self.assertTrue(result["accepted"])
        boundary = points((0.006, 0.003, 0.002, 0.0, 0.002, 0.003, 0.006))
        result = ANALYZER.compare_adjacent(
            coarse,
            boundary,
            {"v0_angstrom3_per_atom": 16.0},
            {"v0_angstrom3_per_atom": 16.0},
        )
        self.assertEqual(result["max_anchored_energy_difference_mev_per_atom"], 2.0)
        self.assertFalse(result["energy_accepted"])

    def test_authoritative_status_forbids_same_id_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            accepted = {
                "schema_version": 1,
                "protocol_revision": VALIDATOR.PROTOCOL_REVISION,
                "experiment_id": "S1-20260806-001",
                "status": "accepted",
                "authoritative_for_r1": True,
                "retry_policy": "new_protocol_revision_and_new_experiment_ids_only",
                "workflow_exit_code": 0,
                "parser_exit_code": 0,
                "core_validator_exit_code": 0,
            }
            path = directory / VALIDATOR.STATUS_NAME
            path.write_text(json.dumps(accepted), encoding="utf-8")
            self.assertIsNone(
                VALIDATOR._status_failure(path, "S1-20260806-001", accepted=True)
            )
            accepted["retry_policy"] = "archive_then_retry_same_id"
            path.write_text(json.dumps(accepted), encoding="utf-8")
            self.assertIn(
                "retry_policy",
                VALIDATOR._status_failure(path, "S1-20260806-001", accepted=True),
            )

            failed_base = {
                "schema_version": 1,
                "protocol_revision": VALIDATOR.PROTOCOL_REVISION,
                "experiment_id": "S1-20260806-001",
                "status": "indeterminate",
                "authoritative_for_r1": True,
                "retry_policy": "new_protocol_revision_and_new_experiment_ids_only",
            }
            for stage, codes in (
                ("workflow", (1, 97, 97)),
                ("thermodynamic_label_parser", (0, 3, 97)),
                ("core_validator", (0, 0, 4)),
            ):
                valid_failed = {
                    **failed_base,
                    "workflow_exit_code": codes[0],
                    "parser_exit_code": codes[1],
                    "core_validator_exit_code": codes[2],
                    "failure_stage": stage,
                }
                path.write_text(json.dumps(valid_failed), encoding="utf-8")
                self.assertIsNone(
                    VALIDATOR._status_failure(
                        path, "S1-20260806-001", accepted=False
                    )
                )

            failed = {
                **failed_base,
                "workflow_exit_code": 0,
                "parser_exit_code": 3,
                "core_validator_exit_code": 4,
                "failure_stage": "thermodynamic_label_parser",
            }
            path.write_text(json.dumps(failed), encoding="utf-8")
            self.assertIn(
                "stage/exit tuple",
                VALIDATOR._status_failure(
                    path, "S1-20260806-001", accepted=False
                ),
            )

    def test_core_and_analyzer_capability_classification_is_fail_closed(self) -> None:
        experiment_id = "S1-20260806-001"
        capability = VALIDATOR.classify_core_failures(
            experiment_id, ["expected exactly one regular pot.cube"]
        )
        numerical = VALIDATOR.classify_core_failures(
            experiment_id, ["standard replay equivalence failed"]
        )
        self.assertEqual(capability["status"], "indeterminate")
        self.assertEqual(numerical["status"], "rejected")
        self.assertTrue(
            ANALYZER.has_capability_or_evidence_failure(
                ["missing or symbolic thermodynamic_labels.json"]
            )
        )
        for message in (
            "potential cube basename is not pot.cube",
            "invalid cube spin count",
            "cube axis/grid does not reconstruct the STRU lattice",
            "cube/STRU atom mismatch",
            "cube atom identity/position differs from STRU",
            "cube axes disagree with STRU cell volume",
        ):
            with self.subTest(message=message):
                self.assertEqual(
                    VALIDATOR.classify_core_failures(
                        experiment_id, [message]
                    )["status"],
                    "indeterminate",
                )
                self.assertTrue(
                    ANALYZER.has_capability_or_evidence_failure([message])
                )

    def test_parser_cube_output_contract_failures_are_indeterminate(self) -> None:
        experiment_id = "S1-20260806-001"
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            diagnostic = run / "thermodynamic_label_parser.stderr.txt"
            for message in (
                "potential cube basename is not pot.cube",
                "invalid cube spin count",
                "cube axes disagree with STRU cell volume",
                "cube axis/grid does not reconstruct the STRU lattice",
                "cube/STRU atom mismatch",
                "cube atom identity/position differs from STRU",
            ):
                with self.subTest(message=message):
                    diagnostic.write_text(message + "\n", encoding="utf-8")
                    payload = VALIDATOR.classify_noncore_failure(
                        run,
                        experiment_id,
                        "thermodynamic_label_parser",
                        1,
                        diagnostic,
                    )
                    self.assertEqual(payload["status"], "indeterminate")
                    self.assertEqual(
                        payload["failure_class"],
                        "thermodynamic_label_parser_capability_failure",
                    )

            diagnostic.write_text(
                "thermodynamic identity residual exceeded\n", encoding="utf-8"
            )
            numerical = VALIDATOR.classify_noncore_failure(
                run,
                experiment_id,
                "thermodynamic_label_parser",
                1,
                diagnostic,
            )
            self.assertEqual(numerical["status"], "rejected")

    def test_workflow_runtime_classification_uses_values_not_json_key_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            (run / "mpi_runtime_audit").mkdir()
            diagnostic = run / "outer_workflow_failure.txt"
            diagnostic.write_text("run_s1_single workflow exit code: 96\n", encoding="utf-8")
            run_status = {
                "schema_version": 2,
                "status": "rejected",
                "result_json_present": True,
                "result_converged": True,
                "runtime_audit_json_present": True,
                "runtime_audit_status": "rejected",
            }
            (run / "run_status.json").write_text(
                json.dumps(run_status), encoding="utf-8"
            )
            (run / "result.json").write_text(
                json.dumps({"converged": True}), encoding="utf-8"
            )
            audit = {
                "schema_version": 2,
                "protocol": "runtime_relocation_equivalence",
                "status": "rejected",
                "failure_reasons": ["unexpected_mapped_object_count:1"],
                "timeout_triggered": False,
                "launcher_exit_code": 0,
                "rank_handshake_status": "accepted",
                "rank_pids": {str(rank): rank + 10 for rank in range(4)},
                "transient_mapping_patterns": [],
                "counterpart_missing_count": 0,
                **{
                    field: (1 if field == "unexpected_mapped_object_count" else 0)
                    for field in VALIDATOR._RUNTIME_REJECTION_COUNT_FIELDS
                },
            }
            audit_path = run / "mpi_runtime_audit/audit.json"
            audit_path.write_text(json.dumps(audit), encoding="utf-8")
            positive = VALIDATOR.classify_noncore_failure(
                run,
                "S1-20260806-001",
                "workflow",
                96,
                diagnostic,
            )
            self.assertEqual(positive["status"], "rejected")

            audit["unexpected_mapped_object_count"] = 0
            audit["failure_reasons"] = ["counterpart_missing_count:0"]
            audit_path.write_text(json.dumps(audit), encoding="utf-8")
            zero_only = VALIDATOR.classify_noncore_failure(
                run,
                "S1-20260806-001",
                "workflow",
                96,
                diagnostic,
            )
            self.assertEqual(zero_only["status"], "indeterminate")

    def test_failed_run_classification_inventory_and_head_binding(self) -> None:
        experiment_id = "S1-20260806-001"
        row = {
            "experiment_id": experiment_id,
            "input_directory": "inputs/unused",
            "pseudopotential": "unused.psp",
        }
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            run = project / "runs" / experiment_id
            run.mkdir(parents=True)
            diagnostic = run / "outer_workflow_failure.txt"
            diagnostic.write_text(
                "run_s1_single workflow exit code: 1\n", encoding="utf-8"
            )
            run_status = {
                "schema_version": 2,
                "status": "rejected",
                "setup_completed": False,
                "result_json_present": False,
                "runtime_audit_json_present": False,
            }
            (run / "run_status.json").write_text(
                json.dumps(run_status), encoding="utf-8"
            )
            classification = VALIDATOR.classify_noncore_failure(
                run, experiment_id, "workflow", 1, diagnostic
            )
            (run / VALIDATOR.FAILURE_CLASS_NAME).write_text(
                json.dumps(classification, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            status = {
                "schema_version": 1,
                "protocol_revision": VALIDATOR.PROTOCOL_REVISION,
                "experiment_id": experiment_id,
                "status": "indeterminate",
                "authoritative_for_r1": True,
                "retry_policy": "new_protocol_revision_and_new_experiment_ids_only",
                "workflow_exit_code": 1,
                "parser_exit_code": 97,
                "core_validator_exit_code": 97,
                "failure_stage": "workflow",
            }
            (run / VALIDATOR.STATUS_NAME).write_text(
                json.dumps(status, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            self._write_failure_inventory(run, experiment_id)
            self.assertEqual(
                VALIDATOR.validate_failed_r1_run(
                    project, row, require_committed=False
                ),
                [],
            )

            subprocess.run(["git", "init", "-q", str(project)], check=True)
            subprocess.run(
                ["git", "-C", str(project), "config", "user.email", "audit@example.invalid"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(project), "config", "user.name", "Audit Test"],
                check=True,
            )
            subprocess.run(["git", "-C", str(project), "add", "runs"], check=True)
            subprocess.run(
                ["git", "-C", str(project), "commit", "-qm", "failure"], check=True
            )
            self.assertEqual(
                VALIDATOR.validate_failed_r1_run(
                    project, row, require_committed=True
                ),
                [],
            )

            classification["failure_reasons"] = ["forged reason"]
            (run / VALIDATOR.FAILURE_CLASS_NAME).write_text(
                json.dumps(classification, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            (run / VALIDATOR.FAILURE_INVENTORY_NAME).unlink()
            self._write_failure_inventory(run, experiment_id)
            errors = VALIDATOR.validate_failed_r1_run(
                project, row, require_committed=True
            )
            self.assertTrue(
                any("classification is not reproducible" in error for error in errors)
            )
            self.assertTrue(any("differs from HEAD" in error for error in errors))

    def test_failure_classifier_populates_specific_protocol_ledgers(self) -> None:
        categories = ANALYZER.classify_run_failures(
            [
                "INPUT SHA-256 mismatch",
                "thermodynamic identity failed",
                "electron-number integration failed",
                "KMP runtime contract rejected",
                "standard replay equivalence failed",
            ]
        )
        self.assertTrue(
            {
                "input_hash",
                "thermodynamic_identity",
                "electron_number",
                "runtime_kmp",
                "replay_equivalence",
            }.issubset(categories)
        )

    def test_runner_separates_accepted_gate_rejection_from_failed_attempt_archive(self) -> None:
        text = (SCRIPTS / "run_s1_g1_thermodynamic_label_audit_r1.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("--require-pilot-gate", text)
        self.assertIn("--require-k-gate", text)
        self.assertIn("--require-half-quarter-pair", text)
        self.assertIn("--require-adjacent-eos al standard half", text)
        self.assertIn("--require-adjacent-eos mg half quarter", text)
        loop = text[text.index("for ((offset") :]
        new_attempt = loop[loop.index('commit_run "$experiment_id"') :]
        self.assertLess(
            new_attempt.index('commit_run "$experiment_id"'),
            new_attempt.index('run_barriers "$experiment_id"'),
        )
        barrier_function = text[text.index("run_barriers()") : text.index("for ((offset")]
        self.assertNotIn("archive_and_stop", barrier_function)
        self.assertIn("archive_and_stop", loop)
        self.assertIn("R1 forbids retry", text)


if __name__ == "__main__":
    unittest.main()
