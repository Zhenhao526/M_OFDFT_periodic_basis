#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], check=True, text=True, stdout=subprocess.PIPE).stdout.strip())
sys.path.insert(0, str(ROOT / "scripts"))

import analyze_s1_g1_al_acceptance_policy_r1 as policy
import validate_s1_g1_al_acceptance_policy_r1 as validator


class AcceptancePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads((ROOT / policy.CONFIG_PATH).read_text())

    def test_old_gate_rejects_and_new_gate_accepts(self) -> None:
        metrics = deepcopy(self.config["expected_metrics"])
        old = deepcopy(self.config["acceptance"])
        old["equilibrium_volume_difference_percent_max"] = 0.5
        with self.assertRaisesRegex(ValueError, "hard gates rejected"):
            policy.evaluate_metrics(metrics, old)
        checks = policy.evaluate_metrics(metrics, self.config["acceptance"])
        self.assertTrue(all(checks.values()))

    def test_scope_keeps_independent_qe_open(self) -> None:
        policy.verify_scope(self.config["scope_decision"])
        tampered = deepcopy(self.config["scope_decision"])
        tampered["qe_control_closed"] = True
        with self.assertRaisesRegex(ValueError, "semantic limit"):
            policy.verify_scope(tampered)

    def test_threshold_tamper_fails(self) -> None:
        tampered = deepcopy(self.config)
        tampered["acceptance"]["equilibrium_volume_difference_percent_max"] = 0.5
        with self.assertRaisesRegex(ValueError, "hard gates rejected"):
            policy.evaluate_metrics(tampered["expected_metrics"], tampered["acceptance"])

    def test_committed_validator_contains_full_parent_chain_replay(self) -> None:
        source = (ROOT / "scripts/validate_s1_g1_al_acceptance_policy_r1.py").read_text()
        self.assertIn("evidence parent is not config-only preregistration", source)
        self.assertIn("evidence parent changed non-registration content", source)
        self.assertIn("registered implementation/base topology differs", source)

    def test_sibling_config_threshold_tamper_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="g1_policy_chain_") as temporary:
            repo = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
            (repo / "base.txt").write_text("base\n")
            subprocess.run(["git", "add", "base.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=True)
            base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, stdout=subprocess.PIPE).stdout.strip()
            config_path = repo / policy.CONFIG_PATH
            config_path.parent.mkdir()
            pending = {
                "status": "implementation_pending_preregistration",
                "registration": {"implementation_commit": "__FREEZE_IMPLEMENTATION_COMMIT__", "integration_base_commit": base},
                "topology": {"implementation_added_paths": [policy.CONFIG_PATH.as_posix()]},
            }
            config_path.write_text(json.dumps(pending, sort_keys=True) + "\n")
            subprocess.run(["git", "add", policy.CONFIG_PATH.as_posix()], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "implementation"], cwd=repo, check=True)
            implementation = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, stdout=subprocess.PIPE).stdout.strip()
            prereg = deepcopy(pending)
            prereg["status"] = "preregistered"
            prereg["registration"]["implementation_commit"] = implementation
            config_path.write_text(json.dumps(prereg, sort_keys=True) + "\n")
            subprocess.run(["git", "add", policy.CONFIG_PATH.as_posix()], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "valid prereg"], cwd=repo, check=True)
            valid_prereg = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, stdout=subprocess.PIPE).stdout.strip()
            self.assertEqual(validator.validate_registered_chain(repo, prereg, valid_prereg), implementation)
            subprocess.run(["git", "checkout", "-q", implementation], cwd=repo, check=True)
            tampered = deepcopy(prereg)
            tampered["unauthorized_threshold"] = 99.0
            config_path.write_text(json.dumps(tampered, sort_keys=True) + "\n")
            subprocess.run(["git", "add", policy.CONFIG_PATH.as_posix()], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "tampered sibling"], cwd=repo, check=True)
            bad_prereg = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, stdout=subprocess.PIPE).stdout.strip()
            with self.assertRaisesRegex(ValueError, "non-registration content"):
                validator.validate_registered_chain(repo, tampered, bad_prereg)

    def test_full_committed_source_replay(self) -> None:
        summary, rows = policy.build_analysis(ROOT, self.config)
        self.assertEqual(summary["status"], "accepted_g1_6_of_6")
        self.assertEqual(summary["g1"], {"status": "accepted", "accepted_subitems": 6, "required_subitems": 6})
        self.assertEqual(summary["new_solver_run_count"], 0)
        self.assertEqual(len(rows), 17)
        self.assertEqual(sorted(policy.rendered_outputs(ROOT, self.config)), sorted(policy.OUTPUT_NAMES))


if __name__ == "__main__":
    unittest.main()
