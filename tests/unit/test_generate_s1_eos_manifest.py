from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "generate_s1_eos_manifest.py"
SPEC = importlib.util.spec_from_file_location("generate_s1_eos_manifest", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class GenerateS1EosManifestTest(unittest.TestCase):
    def test_builds_42_unique_experiments(self) -> None:
        config = {
            "materials": {"al": {}, "mg": {}},
            "volume_ratios": [0.90, 0.94, 0.97, 1.00, 1.03, 1.06, 1.10],
        }
        entries = MODULE.build_entries(config, "20260805", 29)
        self.assertEqual(len(entries), 42)
        self.assertEqual(entries[0]["experiment_id"], "S1-20260805-029")
        self.assertEqual(entries[-1]["experiment_id"], "S1-20260805-070")
        self.assertEqual(len({entry["experiment_id"] for entry in entries}), 42)
        self.assertEqual(
            {entry["series_id"] for entry in entries},
            {"ofdft", "ksdft_standard", "ksdft_half"},
        )


if __name__ == "__main__":
    unittest.main()
