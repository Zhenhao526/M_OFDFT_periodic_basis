from __future__ import annotations

import hashlib
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import parse_s1_g1_thermodynamic_labels_r2 as PARSER  # noqa: E402


class S1G1ThermodynamicLabelAuditR2ParserTest(unittest.TestCase):
    def test_wrapper_scopes_and_restores_r1_registration_namespace(self) -> None:
        original_ids = PARSER.r1_parser.AUDIT_IDS
        original_revision = PARSER.r1_parser.PROTOCOL_REVISION
        observed = {}

        def fake_parse(_run, *, config_path, manifest_path):
            observed["ids"] = PARSER.r1_parser.AUDIT_IDS
            observed["revision"] = PARSER.r1_parser.PROTOCOL_REVISION
            observed["config"] = config_path
            observed["manifest"] = manifest_path
            observed["config_mode"] = stat.S_IMODE(config_path.stat().st_mode)
            observed["manifest_mode"] = stat.S_IMODE(manifest_path.stat().st_mode)
            return {
                "protocol_revision": PARSER.PROTOCOL_REVISION,
                "experiment_id": PARSER.R2_AUDIT_IDS[0],
                "registration": {
                    "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
                    "manifest_sha256": hashlib.sha256(
                        manifest_path.read_bytes()
                    ).hexdigest(),
                },
            }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            manifest = root / "manifest.tsv"
            config.write_bytes(b"{}\n")
            manifest.write_bytes(b"header\n")
            with mock.patch.object(PARSER.r1_parser, "parse_run", side_effect=fake_parse):
                payload = PARSER.parse_run(
                    Path("/tmp/run"),
                    config_path=config,
                    manifest_path=manifest,
                )
        self.assertEqual(observed["ids"], PARSER.R2_AUDIT_IDS)
        self.assertEqual(observed["revision"], PARSER.PROTOCOL_REVISION)
        self.assertEqual(observed["config_mode"], 0o400)
        self.assertEqual(observed["manifest_mode"], 0o400)
        self.assertEqual(payload["registration"]["config_path"], str(config))
        self.assertEqual(payload["registration"]["manifest_path"], str(manifest))
        self.assertEqual(PARSER.r1_parser.AUDIT_IDS, original_ids)
        self.assertEqual(PARSER.r1_parser.PROTOCOL_REVISION, original_revision)
        self.assertEqual(payload["schema_revision"], "S1-G1-THERMODYNAMIC-LABELS-R2")
        self.assertFalse(payload["parser_reuse_contract"]["r1_evidence_reinterpretation"])


if __name__ == "__main__":
    unittest.main()
