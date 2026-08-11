#!/usr/bin/env python3
"""Unit and negative regressions for the analysis-only R5 closure."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import analyze_s1_g1_three_layer_al_followup_analysis_r5 as analyzer
import validate_s1_g1_three_layer_al_followup_analysis_r5 as validator
from s1_g1_three_layer_al_followup_r4_common import load_config, sha256_file


class AnalysisR5Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(ROOT)
        expected = cls.config["pseudodojo"]["materials"]["al"]
        cls.minimal = {
            "basename": expected["basename"],
            "expanded_nonlocal_projectors_per_atom": expected["expanded_nonlocal_projectors_per_atom"],
            "format": "upf201",
            "sha256": expected["sha256"],
            "z_valence": expected["z_valence"],
        }
        cls.expected_metrics = {
            "strain_absolute_difference_mev_per_atom": {
                "S1-20260810-351": 0.23832079999408506,
                "S1-20260810-352": 0.23433039999787297,
                "S1-20260810-353": 0.004608300010033872,
                "S1-20260810-354": 0.004608400004713076,
            },
            "endpoints": {
                "v090": {
                    "anchored_k_difference_mev_per_atom": 0.008772699999326505,
                    "anchored_cutoff_difference_mev_per_atom": 0.11227989999440524,
                    "cutoff_pressure_difference_gpa": 0.004055200000001591,
                },
                "v110": {
                    "anchored_k_difference_mev_per_atom": 0.0917003000040495,
                    "anchored_cutoff_difference_mev_per_atom": 0.03827180000826047,
                    "cutoff_pressure_difference_gpa": 0.002397900000000952,
                },
            },
            "comparison": "exact_binary64_replay",
            "gate_row_count": 10,
            "overall_status": "accepted",
        }

    def test_minimal_pseudo_mapping_positive(self) -> None:
        mapping = analyzer.validate_minimal_input_pseudo_identity(
            copy.deepcopy(self.minimal), "al", self.config,
        )
        self.assertTrue(mapping["accepted"])
        self.assertEqual(
            mapping["mapping"],
            "legacy_minimal_input_pseudo_schema_to_frozen_config_upstream_identity",
        )
        self.assertEqual(mapping["upstream_commit"], self.config["pseudodojo"]["commit"])
        self.assertEqual(mapping["upstream_url"], self.config["pseudodojo"]["materials"]["al"]["url"])

    def test_minimal_pseudo_rejects_legacy_upstream_fields(self) -> None:
        mutated = copy.deepcopy(self.minimal)
        mutated["upstream_commit"] = self.config["pseudodojo"]["commit"]
        with self.assertRaisesRegex(ValueError, "minimal input pseudo schema differs"):
            analyzer.validate_minimal_input_pseudo_identity(mutated, "al", self.config)

    def test_minimal_pseudo_rejects_missing_key(self) -> None:
        mutated = copy.deepcopy(self.minimal)
        del mutated["format"]
        with self.assertRaisesRegex(ValueError, "minimal input pseudo schema differs"):
            analyzer.validate_minimal_input_pseudo_identity(mutated, "al", self.config)

    def test_minimal_pseudo_rejects_wrong_sha(self) -> None:
        mutated = copy.deepcopy(self.minimal)
        mutated["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "minimal input pseudo values differ"):
            analyzer.validate_minimal_input_pseudo_identity(mutated, "al", self.config)

    def test_pseudo_schema_dispatch_is_source_specific(self) -> None:
        run_dir = Path("/nonexistent")
        result = {"material": "al"}
        with (
            patch.object(analyzer, "verify_new_pseudo_identity_closure_r5", return_value={"mode": "minimal"}) as minimal,
            patch.object(analyzer, "verify_pseudo_identity_closure", return_value={"mode": "full"}) as full,
        ):
            self.assertEqual(
                analyzer.select_replay_pseudo_identity_closure(
                    run_dir, result, self.config, explicit_minimal_input_schema=True,
                ),
                {"mode": "minimal"},
            )
            self.assertEqual(
                analyzer.select_replay_pseudo_identity_closure(
                    run_dir, result, self.config, explicit_minimal_input_schema=False,
                ),
                {"mode": "full"},
            )
            minimal.assert_called_once()
            full.assert_called_once_with(run_dir, result, self.config, require_run_body=False)

    def test_execution_rejects_descendant(self) -> None:
        analyzer.require_exact_preregistration_head("a" * 40, "a" * 40)
        with self.assertRaisesRegex(ValueError, "exact preregistration"):
            analyzer.require_exact_preregistration_head("b" * 40, "a" * 40)

    def test_implementation_diff_exact_five_additions(self) -> None:
        paths = list(analyzer.EXPECTED_IMPLEMENTATION_PATHS)
        rows = [["A", path] for path in paths]
        analyzer.require_exact_implementation_diff(rows, paths)
        mutations = [
            [["M", paths[0]], *rows[1:]],
            rows + [["A", "scripts/extra.py"]],
            rows[:-1],
        ]
        for mutation in mutations:
            with self.assertRaisesRegex(ValueError, "exactly five A-only"):
                analyzer.require_exact_implementation_diff(mutation, paths)
        with self.assertRaisesRegex(ValueError, "registered implementation path"):
            analyzer.require_exact_implementation_diff(rows, paths + ["scripts/extra.py"])

    def test_evidence_only_diff_positive_and_negative(self) -> None:
        prefix = analyzer.ANALYSIS_ROOT.as_posix() + "/"
        tracked = [prefix + "summary.json", prefix + "gates.tsv"]
        validator.require_evidence_only_diff(
            [["A", tracked[0]], ["A", tracked[1]]], tracked, prefix,
        )
        bad_rows = [
            [["M", tracked[0]], ["A", tracked[1]]],
            [["A", "scripts/evil.py"]],
            [["A", tracked[0]], ["A", tracked[1]], ["A", prefix + "extra"]],
        ]
        bad_tracked = [tracked, ["scripts/evil.py"], tracked]
        for rows, observed in zip(bad_rows, bad_tracked):
            with self.assertRaises(ValueError):
                validator.require_evidence_only_diff(rows, observed, prefix)

    def test_extension_key_denominator(self) -> None:
        self.assertEqual(
            analyzer.EXPECTED_EXTENSION_KEYS,
            {
                "r2_operational_failure_closure",
                "r3_operational_failure_closure",
                "r3_parser_regression",
            },
        )

    def test_scientific_metric_exact_replay_positive_and_negative(self) -> None:
        strain_rows = [
            {
                "experiment_id": experiment_id,
                "absolute_difference_mev_per_atom": value,
            }
            for experiment_id, value in self.expected_metrics["strain_absolute_difference_mev_per_atom"].items()
        ]
        endpoint_rows = [
            {"endpoint": endpoint, **metrics}
            for endpoint, metrics in self.expected_metrics["endpoints"].items()
        ]
        gate_rows = [
            {
                "gate": "strain",
                "point": experiment_id,
                "metric": "absolute_scheme_and_construction_difference_mev_per_atom",
                "value": value,
                "accepted": True,
            }
            for experiment_id, value in self.expected_metrics["strain_absolute_difference_mev_per_atom"].items()
        ]
        gate_rows.extend(
            {
                "gate": "endpoint",
                "point": endpoint,
                "metric": metric,
                "value": value,
                "accepted": True,
            }
            for endpoint, metrics in self.expected_metrics["endpoints"].items()
            for metric, value in metrics.items()
        )
        closure_diagnostic = {
            "build_analysis_row_count": 10,
            "endpoint_v090": self.expected_metrics["endpoints"]["v090"],
            "endpoint_v110": self.expected_metrics["endpoints"]["v110"],
            "gate_status": "accepted",
            "maximum_strain_absolute_difference_mev_per_atom": 0.23832079999408506,
        }
        summary = {
            "status": "accepted",
            "galileo_gates": {
                "status": "accepted",
                "strain": {"rows": strain_rows},
                "endpoints": {"rows": endpoint_rows},
            },
        }
        r5_config = {
            "expected_scientific_metrics": copy.deepcopy(self.expected_metrics),
            "r4_analyzer_false_negative_closure": {
                "scientific_diagnostic": closure_diagnostic,
            },
        }
        observed = analyzer.verify_expected_scientific_metrics(summary, gate_rows, r5_config)
        self.assertTrue(observed["accepted"])
        for target, mutation in (
            ("strain", 0.5),
            ("endpoint", 0.5),
        ):
            mutated = copy.deepcopy(summary)
            if target == "strain":
                mutated["galileo_gates"]["strain"]["rows"][0]["absolute_difference_mev_per_atom"] = mutation
            else:
                mutated["galileo_gates"]["endpoints"]["rows"][0]["anchored_k_difference_mev_per_atom"] = mutation
            with self.assertRaises(ValueError):
                analyzer.verify_expected_scientific_metrics(mutated, gate_rows, r5_config)

    def test_historical_r4_analyzer_blob_and_closure(self) -> None:
        closure_path = (
            ROOT
            / "orchestration/s1/g1_three_layer_al_domain_followup_r4_20260810"
            / "analyzer_false_negative_closure.json"
        )
        closure = json.loads(closure_path.read_text())
        historical = ROOT / closure["analyzer_identity"]["path"]
        self.assertEqual(sha256_file(historical), closure["analyzer_identity"]["sha256"])
        blob = subprocess.run(
            ["git", "hash-object", historical],
            cwd=ROOT, check=True, text=True, stdout=subprocess.PIPE,
        ).stdout.strip()
        self.assertEqual(blob, closure["analyzer_identity"]["git_blob"])
        self.assertEqual(closure["analyzer_exit_code"], 1)
        for label, relative in analyzer.R4_INPUT_PATHS.items():
            path = ROOT / relative
            self.assertEqual(sha256_file(path), closure["r4_input_identities"][f"{label}_sha256"])
            blob = subprocess.run(
                ["git", "hash-object", path],
                cwd=ROOT, check=True, text=True, stdout=subprocess.PIPE,
            ).stdout.strip()
            self.assertEqual(blob, closure["r4_input_identities"][f"{label}_git_blob"])


if __name__ == "__main__":
    unittest.main()
