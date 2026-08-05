from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import generate_s1_non_equilibrium_inputs as INPUTS  # noqa: E402
import generate_s1_non_equilibrium_manifest as MANIFEST  # noqa: E402
import validate_s1_non_equilibrium_manifest as VALIDATOR  # noqa: E402


CONFIG_PATH = PROJECT_ROOT / "config" / "S1_non_equilibrium_convergence.json"
MANIFEST_PATH = PROJECT_ROOT / "config" / "S1_non_equilibrium_run_manifest.tsv"


class S1NonEquilibriumGenerationTest(unittest.TestCase):
    def test_frozen_id_block_builds_42_unique_entries(self) -> None:
        config = json.loads(CONFIG_PATH.read_text())
        entries = MANIFEST.build_entries(config)
        self.assertEqual(len(entries), 42)
        self.assertEqual(entries[0]["experiment_id"], "S1-20260805-071")
        self.assertEqual(entries[-1]["experiment_id"], "S1-20260805-112")
        self.assertEqual(len({entry["experiment_id"] for entry in entries}), 42)
        self.assertEqual(
            {entry["series_id"] for entry in entries},
            {"ofdft_next_cutoff", "ksdft_next_cutoff", "ksdft_next_kmesh"},
        )

    def test_generated_matrix_changes_only_registered_axis(self) -> None:
        config = json.loads(CONFIG_PATH.read_text())
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "r8"
            self.assertEqual(INPUTS.generate(PROJECT_ROOT, CONFIG_PATH, output), 42)
            metadata_paths = sorted(output.rglob("metadata.json"))
            self.assertEqual(len(metadata_paths), 42)
            for metadata_path in metadata_paths:
                metadata = json.loads(metadata_path.read_text())
                reference = json.loads(
                    (
                        PROJECT_ROOT
                        / "runs"
                        / metadata["baseline_experiment_id"]
                        / "input_metadata.json"
                    ).read_text()
                )
                self.assertEqual(metadata["stru_sha256"], reference["stru_sha256"])
                self.assertEqual(metadata["solver"], reference["solver"])
                self.assertEqual(
                    metadata["pseudopotential_sha256"], reference["pseudopotential_sha256"]
                )
                self.assertEqual(metadata["smearing_sigma_ry"], reference["smearing_sigma_ry"])
                if metadata["comparison_axis"] == "cutoff":
                    self.assertEqual(metadata["kmesh"], reference["kmesh"])
                    self.assertGreater(metadata["ecutwfc_ry"], reference["ecutwfc_ry"])
                else:
                    self.assertEqual(metadata["ecutwfc_ry"], reference["ecutwfc_ry"])
                    self.assertNotEqual(metadata["kmesh"], reference["kmesh"])

    def test_checked_in_manifest_validates_against_core_results(self) -> None:
        payload = VALIDATOR.validate(PROJECT_ROOT, CONFIG_PATH, MANIFEST_PATH)
        self.assertEqual(payload["experiment_count"], 42)
        self.assertEqual(payload["first_experiment_id"], "S1-20260805-071")
        self.assertEqual(payload["last_experiment_id"], "S1-20260805-112")

    def test_manifest_metadata_hash_tampering_is_rejected(self) -> None:
        lines = MANIFEST_PATH.read_text().splitlines()
        fields = lines[1].split("\t")
        fields[-1] = "0" * 64
        lines[1] = "\t".join(fields)
        with tempfile.TemporaryDirectory() as temporary:
            tampered = Path(temporary) / "manifest.tsv"
            tampered.write_text("\n".join(lines) + "\n")
            with self.assertRaisesRegex(ValueError, "metadata SHA-256 mismatch"):
                VALIDATOR.validate(PROJECT_ROOT, CONFIG_PATH, tampered)


if __name__ == "__main__":
    unittest.main()
