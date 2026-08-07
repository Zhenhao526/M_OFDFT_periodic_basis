from __future__ import annotations

import hashlib
import json
import shutil
import stat
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import generate_s1_g1_thermodynamic_label_audit_r3 as GENERATOR  # noqa: E402
import parse_s1_g1_thermodynamic_labels_r3 as PARSER  # noqa: E402
from s1_g1_thermodynamic_label_common import canonical_json_bytes  # noqa: E402


class DownstreamInputValidationReached(RuntimeError):
    pass


class S1G1ThermodynamicLabelAuditR3ParserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        prepared = GENERATOR.prepare(PROJECT_ROOT)
        cls.config = json.loads(prepared["config_bytes"])
        cls.manifest_bytes = prepared["manifest_bytes"]
        cls.artifacts = prepared["artifacts"]
        cls.manifest_rows = prepared["manifest_rows"]

    def _write_registration(
        self,
        root: Path,
        *,
        config: dict[str, object] | None = None,
        manifest_bytes: bytes | None = None,
    ) -> tuple[Path, Path]:
        manifest = manifest_bytes if manifest_bytes is not None else self.manifest_bytes
        payload = deepcopy(config if config is not None else self.config)
        registration = payload["manifest"]
        assert isinstance(registration, dict)
        registration["sha256"] = hashlib.sha256(manifest).hexdigest()
        config_path = root / "config.json"
        manifest_path = root / "manifest.tsv"
        config_path.write_bytes(canonical_json_bytes(payload))
        manifest_path.write_bytes(manifest)
        return config_path, manifest_path

    def _write_real_generated_input_run(
        self, root: Path, *, include_archived_output: bool
    ) -> Path:
        row = self.manifest_rows[0]
        run = root / row["experiment_id"]
        run.mkdir()
        input_root = row["input_directory"]
        for source_name, target_name in (
            ("INPUT", "INPUT"),
            ("STRU", "STRU"),
            ("KPT", "KPT"),
            ("metadata.json", "input_metadata.json"),
        ):
            (run / target_name).write_bytes(
                self.artifacts[f"{input_root}/{source_name}"]
            )
        pseudo = PROJECT_ROOT / "assets/pseudo" / row["pseudopotential"]
        (run / row["pseudopotential"]).write_bytes(pseudo.read_bytes())
        if include_archived_output:
            archive = (
                PROJECT_ROOT
                / "failed_runs/runtime_relocation/S1-20260806-041/attempt-ff26667f881e"
            )
            output = next(archive.glob("OUT.*"))
            shutil.copytree(output, run / output.name)
        return run

    def test_real_generated_forty_row_registration_contract_passes_without_r1_registration(self) -> None:
        original_ids = PARSER.r1_parser.AUDIT_IDS
        original_revision = PARSER.r1_parser.PROTOCOL_REVISION
        original_validator = PARSER.r1_parser._validate_registration
        with tempfile.TemporaryDirectory() as directory:
            config, manifest = self._write_registration(Path(directory))
            with mock.patch.object(
                PARSER.r1_parser,
                "_validate_registration",
                side_effect=AssertionError("legacy R1 registration was called"),
            ) as legacy_registration:
                result = PARSER.validate_registration_contract(
                    config_path=config, manifest_path=manifest
                )
            legacy_registration.assert_not_called()
        self.assertEqual(result["protocol_revision"], PARSER.PROTOCOL_REVISION)
        self.assertEqual(
            tuple(result["registered_experiment_ids"]), PARSER.R3_AUDIT_IDS
        )
        self.assertEqual(PARSER.r1_parser.AUDIT_IDS, original_ids)
        self.assertEqual(PARSER.r1_parser.PROTOCOL_REVISION, original_revision)
        self.assertIs(PARSER.r1_parser._validate_registration, original_validator)

    def test_production_parse_crosses_unmocked_registration_and_input_validation(self) -> None:
        original_validator = PARSER.r1_parser._validate_registration
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, manifest = self._write_registration(root)
            run = self._write_real_generated_input_run(
                root, include_archived_output=False
            )
            with mock.patch.object(
                PARSER.r1_parser,
                "_single_regular",
                side_effect=DownstreamInputValidationReached("registration passed"),
            ) as downstream:
                with self.assertRaisesRegex(
                    DownstreamInputValidationReached, "registration passed"
                ):
                    PARSER.parse_run(
                        run, config_path=config, manifest_path=manifest
                    )
        downstream.assert_called_once()
        self.assertIs(PARSER.r1_parser._validate_registration, original_validator)

    def test_full_parser_replay_with_archived_numeric_fixture_never_reuses_r2_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, manifest = self._write_registration(root)
            run = self._write_real_generated_input_run(
                root, include_archived_output=True
            )
            payload = PARSER.parse_run(
                run, config_path=config, manifest_path=manifest
            )
        self.assertEqual(payload["status"], "accepted")
        self.assertEqual(payload["experiment_id"], "S1-20260807-001")
        self.assertEqual(payload["protocol_revision"], PARSER.PROTOCOL_REVISION)
        self.assertEqual(
            payload["metadata"]["protocol_revision"], PARSER.PROTOCOL_REVISION
        )
        self.assertFalse(
            payload["parser_reuse_contract"]["registration_runtime_global_mutation"]
        )
        self.assertFalse(
            payload["parser_reuse_contract"]["r1_evidence_reinterpretation"]
        )

    def test_parse_run_rejects_bad_registration_before_input_or_science(self) -> None:
        config_payload = deepcopy(self.config)
        order = list(PARSER.R3_AUDIT_IDS)
        order[0], order[1] = order[1], order[0]
        config_payload["execution_order"] = order
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, manifest = self._write_registration(
                root, config=config_payload
            )
            run = self._write_real_generated_input_run(
                root, include_archived_output=False
            )
            with mock.patch.object(PARSER, "_validate_r3_inputs") as inputs:
                with self.assertRaisesRegex(
                    ValueError, "configuration execution order differs"
                ):
                    PARSER.parse_run(
                        run, config_path=config, manifest_path=manifest
                    )
        inputs.assert_not_called()

    def test_parse_run_accepts_distinct_byte_identical_scientific_registration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, manifest = self._write_registration(root)
            scientific_config = root / "scientific_config.json"
            scientific_manifest = root / "scientific_manifest.tsv"
            scientific_config.write_bytes(config.read_bytes())
            scientific_manifest.write_bytes(manifest.read_bytes())
            run = self._write_real_generated_input_run(
                root, include_archived_output=False
            )
            with mock.patch.object(
                PARSER.r1_parser,
                "_single_regular",
                side_effect=DownstreamInputValidationReached("sealed registration passed"),
            ):
                with self.assertRaisesRegex(
                    DownstreamInputValidationReached, "sealed registration passed"
                ):
                    PARSER.parse_run(
                        run,
                        config_path=config,
                        manifest_path=manifest,
                        scientific_config_path=scientific_config,
                        scientific_manifest_path=scientific_manifest,
                    )

    def test_materialization_is_read_only_and_never_mutates_r1_bindings(self) -> None:
        original_ids = PARSER.r1_parser.AUDIT_IDS
        original_revision = PARSER.r1_parser.PROTOCOL_REVISION
        original_validator = PARSER.r1_parser._validate_registration
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, manifest = self._write_registration(root)
            config_bytes, manifest_bytes, _, _ = PARSER._bound_registration_bytes(
                config, manifest, None, None
            )
            with PARSER._materialized_registration(
                config_bytes, manifest_bytes
            ) as (materialized_config, materialized_manifest):
                parsed, rows = PARSER._validate_r3_registration(
                    materialized_config, materialized_manifest
                )
                self.assertEqual(stat.S_IMODE(materialized_config.stat().st_mode), 0o400)
                self.assertEqual(stat.S_IMODE(materialized_manifest.stat().st_mode), 0o400)
                self.assertEqual(parsed["protocol_revision"], PARSER.PROTOCOL_REVISION)
                self.assertEqual(len(rows), 40)
        self.assertEqual(PARSER.r1_parser.AUDIT_IDS, original_ids)
        self.assertEqual(PARSER.r1_parser.PROTOCOL_REVISION, original_revision)
        self.assertIs(PARSER.r1_parser._validate_registration, original_validator)

    def _assert_execution_order_rejected(self, order: list[str]) -> None:
        config = deepcopy(self.config)
        config["execution_order"] = order
        with tempfile.TemporaryDirectory() as directory:
            config_path, manifest_path = self._write_registration(
                Path(directory), config=config
            )
            with self.assertRaisesRegex(
                ValueError, "configuration execution order differs"
            ):
                PARSER.validate_registration_contract(
                    config_path=config_path, manifest_path=manifest_path
                )

    def test_execution_order_with_thirty_nine_ids_is_rejected(self) -> None:
        self._assert_execution_order_rejected(list(PARSER.R3_AUDIT_IDS[:-1]))

    def test_execution_order_with_forty_one_ids_is_rejected(self) -> None:
        self._assert_execution_order_rejected(
            [*PARSER.R3_AUDIT_IDS, PARSER.R3_AUDIT_IDS[-1]]
        )

    def test_execution_order_with_duplicate_id_is_rejected(self) -> None:
        order = list(PARSER.R3_AUDIT_IDS)
        order[-1] = order[0]
        self._assert_execution_order_rejected(order)

    def test_execution_order_with_same_ids_but_swapped_order_is_rejected(self) -> None:
        order = list(PARSER.R3_AUDIT_IDS)
        order[0], order[1] = order[1], order[0]
        self._assert_execution_order_rejected(order)

    def test_manifest_execution_index_mutation_is_rejected(self) -> None:
        lines = self.manifest_bytes.decode("utf-8").splitlines()
        fields = lines[0].split("\t")
        first = lines[1].split("\t")
        first[fields.index("execution_index")] = "2"
        lines[1] = "\t".join(first)
        mutated = ("\n".join(lines) + "\n").encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            config, manifest = self._write_registration(
                Path(directory), manifest_bytes=mutated
            )
            with self.assertRaisesRegex(
                ValueError, "manifest execution indices differ"
            ):
                PARSER.validate_registration_contract(
                    config_path=config, manifest_path=manifest
                )

    def test_different_sealed_registration_bytes_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, manifest = self._write_registration(root)
            sealed_config = root / "sealed_config.json"
            sealed_manifest = root / "sealed_manifest.tsv"
            sealed_config.write_bytes(config.read_bytes() + b" ")
            sealed_manifest.write_bytes(manifest.read_bytes())
            with self.assertRaisesRegex(
                ValueError,
                "sealed scientific registration differs from canonical provenance",
            ):
                PARSER.validate_registration_contract(
                    config_path=config,
                    manifest_path=manifest,
                    scientific_config_path=sealed_config,
                    scientific_manifest_path=sealed_manifest,
                )


if __name__ == "__main__":
    unittest.main()
