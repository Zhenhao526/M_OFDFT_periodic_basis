#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from analyze_s1_g1_three_layer_analysis_r3 import (  # noqa: E402
    CAPTURE_ARTIFACTS,
    inventory_from_entries,
    rejection_signatures,
    require_no_upf_body,
    source_inventory,
    validate_capture_invocation,
)


def write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


class ThreeLayerAnalysisR3Tests(unittest.TestCase):
    def test_exact_scientific_rejection_signature(self) -> None:
        summary = {
            "p0_recovery": {"al_hard": {"status": "accepted"}},
            "al_three_layer_eos": {
                "fits": {name: {"status": "accepted"} for name in ("ks_nl", "ks_l", "of_l")},
                "ks_l_vs_ks_nl": {
                    "equilibrium_volume_difference_percent": 0.766,
                    "bulk_modulus_difference_percent": 2.466,
                },
            },
        }
        config = {"acceptance": {"al_ksl_vs_ksnl_equilibrium_volume_difference_percent_max": 0.5, "al_ksl_vs_ksnl_bulk_modulus_difference_percent_max": 10.0}}
        self.assertEqual(rejection_signatures(summary, config), ["al_ks_l_vs_ks_nl_equilibrium_volume_difference_percent"])

    def test_extra_failure_is_not_hidden(self) -> None:
        summary = {
            "p0_recovery": {"al_hard": {"status": "rejected"}},
            "al_three_layer_eos": {
                "fits": {"ks_nl": {"status": "rejected"}, "ks_l": {"status": "accepted"}, "of_l": {"status": "accepted"}},
                "ks_l_vs_ks_nl": {"equilibrium_volume_difference_percent": 0.8, "bulk_modulus_difference_percent": 12.0},
            },
        }
        config = {"acceptance": {"al_ksl_vs_ksnl_equilibrium_volume_difference_percent_max": 0.5, "al_ksl_vs_ksnl_bulk_modulus_difference_percent_max": 10.0}}
        self.assertEqual(len(rejection_signatures(summary, config)), 4)

    def test_inventory_is_path_and_byte_exact(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            write(root / "b", b"two\n")
            write(root / "a", b"one\n")
            inventory = source_inventory(root)
            self.assertEqual([row["path"] for row in inventory["files"]], ["a", "b"])
            self.assertEqual(inventory, inventory_from_entries(inventory["files"]))

    def test_upf_filename_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            write(root / "raw/Al_std.upf", b"not even a real UPF")
            with self.assertRaises(ValueError):
                require_no_upf_body(root)

    def test_upf_body_signature_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            write(root / "raw/pseudo.dat", b"<UPF version=\"2.0.1\">\n")
            with self.assertRaises(ValueError):
                require_no_upf_body(root)

    def _capture_fixture(self, root: Path) -> tuple[dict, list[dict], dict, Path]:
        source = root / "analysis"
        write(source / "summary.json", b'{"status":"rejected"}\n')
        write(source / "orchestration/terminal.json", b'{"status":"accepted"}\n')
        write(source / "points.tsv", b"x\n")
        before = source_inventory(source)
        write(source / CAPTURE_ARTIFACTS[0], b"out\n")
        write(source / CAPTURE_ARTIFACTS[1], b"err\n")
        analyzer = {"path": "scripts/a.py", "git_blob_oid": "1" * 40, "sha256": "2" * 64, "size_bytes": 12}
        r2_config_identity = {"path": "config/r2.json", "git_blob_oid": "3" * 40, "sha256": "4" * 64, "size_bytes": 13}
        dependencies = [analyzer, r2_config_identity]
        temporary = "/tmp/g1_three_layer_r2_capture_fixture"
        invocation = {
            "schema_version": 2,
            "protocol_revision": "S1-G1-THREE-LAYER-ANALYSIS-20260810-R3",
            "status": "captured_expected_scientific_rejection",
            "argv": ["/usr/bin/python3", "-s", "-B", "scripts/a.py", "--project-root", ".", "--collect"],
            "capture_cwd": "/capture",
            "cwd": "/r2",
            "r2_runner_commit": "5" * 40,
            "capture_implementation_commit": "6" * 40,
            "capture_preregistered_commit": "7" * 40,
            "capture_config_path": "config/S1_g1_three_layer_analysis_r3.json",
            "capture_config_sha256": "8" * 64,
            "registration_changed_paths": ["config/S1_g1_three_layer_analysis_r3.json"],
            "capture_script": {"path": "scripts/c.py", "git_blob_oid": "9" * 40, "sha256": "a" * 64, "size_bytes": 99},
            "r2_dependencies": dependencies,
            "analyzer": analyzer,
            "r2_config": r2_config_identity,
            "terminal": {"path": "/state/terminal.json", "sha256": hashlib.sha256((source / "orchestration/terminal.json").read_bytes()).hexdigest(), "size_bytes": (source / "orchestration/terminal.json").stat().st_size},
            "exit_code": 2,
            "expected_summary_status": "rejected",
            "summary_sha256": hashlib.sha256((source / "summary.json").read_bytes()).hexdigest(),
            "stdout_sha256": hashlib.sha256((source / CAPTURE_ARTIFACTS[0]).read_bytes()).hexdigest(),
            "stdout_size_bytes": (source / CAPTURE_ARTIFACTS[0]).stat().st_size,
            "stderr_sha256": hashlib.sha256((source / CAPTURE_ARTIFACTS[1]).read_bytes()).hexdigest(),
            "stderr_size_bytes": (source / CAPTURE_ARTIFACTS[1]).stat().st_size,
            "analyzer_output_inventory_before_capture_artifacts": before,
            "capture_added_artifacts": list(CAPTURE_ARTIFACTS),
            "subprocess_environment": {
                "HOME": "/home/x", "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
                "PYTHONPATH": "/r2/scripts", "TMPDIR": temporary, "PYTHONPYCACHEPREFIX": temporary + "/pycache",
            },
            "started_utc": "2026-08-10T00:00:00.000000Z",
            "finished_utc": "2026-08-10T00:00:01.000000Z",
            "duration_seconds": 1.0,
            "interpretation": "exit 2 is the preregistered scientific gate rejection, not an execution failure",
        }
        invocation_path = source / CAPTURE_ARTIFACTS[2]
        write(invocation_path, (json.dumps(invocation, sort_keys=True, separators=(",", ":")) + "\n").encode())
        config = {
            "protocol_revision": "S1-G1-THREE-LAYER-ANALYSIS-20260810-R3",
            "capture": {
                "python": "/usr/bin/python3", "python_args": ["-s", "-B"], "analyzer_path": "scripts/a.py",
                "analyzer_sha256": "2" * 64, "r2_config_path": "config/r2.json", "r2_config_sha256": "4" * 64,
                "r2_runner_commit": "5" * 40, "r2_worktree": "/r2",
                "implementation_commit": "6" * 40, "capture_script_path": "scripts/c.py", "capture_script_sha256": "a" * 64,
                "minimal_environment": {"HOME": "/home/x", "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"},
            },
            "source_r2": {
                "r2_worktree": "/r2", "capture_worktree": "/capture", "runner_commit": "5" * 40,
                "capture_implementation_commit": "6" * 40, "capture_preregistered_commit": "7" * 40,
                "capture_config_sha256": "8" * 64, "capture_script_sha256": "a" * 64,
                "terminal_sha256": invocation["terminal"]["sha256"], "analysis_expected_exit_code": 2,
                "summary_sha256": invocation["summary_sha256"], "analysis_stdout_sha256": invocation["stdout_sha256"],
                "analysis_stderr_sha256": invocation["stderr_sha256"],
            },
            "expected_rejection": {"r2_summary_status": "rejected"},
        }
        config["source_r2"]["analysis_invocation_sha256"] = hashlib.sha256(invocation_path.read_bytes()).hexdigest()
        return config, dependencies, {"external_state_root": "/state"}, source

    def test_capture_invocation_full_positive(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            config, dependencies, r2_config, source = self._capture_fixture(Path(name))
            validate_capture_invocation(source, config, source_inventory(source), dependencies, r2_config)

    def test_capture_invocation_status_mutation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            config, dependencies, r2_config, source = self._capture_fixture(Path(name))
            path = source / CAPTURE_ARTIFACTS[2]
            payload = json.loads(path.read_text())
            payload["status"] = "accepted"
            write(path, (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode())
            config["source_r2"]["analysis_invocation_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaises(ValueError):
                validate_capture_invocation(source, config, source_inventory(source), dependencies, r2_config)

    def test_capture_invocation_argv_mutation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            config, dependencies, r2_config, source = self._capture_fixture(Path(name))
            path = source / CAPTURE_ARTIFACTS[2]
            payload = json.loads(path.read_text())
            payload["argv"][-1] = "--not-collect"
            write(path, (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode())
            config["source_r2"]["analysis_invocation_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaises(ValueError):
                validate_capture_invocation(source, config, source_inventory(source), dependencies, r2_config)

    def test_capture_preinventory_mutation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            config, dependencies, r2_config, source = self._capture_fixture(Path(name))
            path = source / CAPTURE_ARTIFACTS[2]
            payload = json.loads(path.read_text())
            payload["analyzer_output_inventory_before_capture_artifacts"]["file_count"] += 1
            write(path, (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode())
            config["source_r2"]["analysis_invocation_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaises(ValueError):
                validate_capture_invocation(source, config, source_inventory(source), dependencies, r2_config)

    def test_capture_unregistered_key_fails(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            config, dependencies, r2_config, source = self._capture_fixture(Path(name))
            path = source / CAPTURE_ARTIFACTS[2]
            payload = json.loads(path.read_text())
            payload["unregistered"] = True
            write(path, (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode())
            config["source_r2"]["analysis_invocation_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaises(ValueError):
                validate_capture_invocation(source, config, source_inventory(source), dependencies, r2_config)

    def test_capture_duration_timestamp_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            config, dependencies, r2_config, source = self._capture_fixture(Path(name))
            path = source / CAPTURE_ARTIFACTS[2]
            payload = json.loads(path.read_text())
            payload["duration_seconds"] = 2.0
            write(path, (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode())
            config["source_r2"]["analysis_invocation_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaises(ValueError):
                validate_capture_invocation(source, config, source_inventory(source), dependencies, r2_config)


if __name__ == "__main__":
    unittest.main()
