from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import analyze_s1_g1_thermodynamic_label_audit_r2 as ANALYZER  # noqa: E402
import validate_s1_g1_thermodynamic_label_audit_r2 as VALIDATOR  # noqa: E402


class S1G1ThermodynamicLabelAuditR2AnalysisTest(unittest.TestCase):
    def test_logical_series_partition_is_exact(self) -> None:
        observed = set()
        for material in ("al", "mg"):
            for level in ("half", "quarter"):
                values = ANALYZER._logical_series(material, level)
                self.assertEqual(len(values), 7)
                self.assertFalse(observed.intersection(values))
                observed.update(values)
        self.assertEqual(observed, set(VALIDATOR.HALF_LOGICAL_IDS + VALIDATOR.COMMON_QUARTER_LOGICAL_IDS))

    def test_complete_mocked_partition_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            config_path = temporary / "config.json"
            manifest_path = temporary / "manifest.tsv"
            config_path.write_text("{}\n", encoding="utf-8")
            manifest_path.write_text("x\n", encoding="utf-8")
            output = temporary / "analysis"
            replay_registration_paths: list[tuple[Path, Path]] = []

            def replay(_root, _config, _rows, logical, **kwargs):
                replay_registration_paths.append(
                    (
                        kwargs["scientific_config_path"],
                        kwargs["scientific_manifest_path"],
                    )
                )
                payload = {
                    "electron_number_integration": {"accepted": True},
                    "kmp_runtime_contract": {
                        "accepted": True,
                        "lifecycle_count": 4,
                        "successful_syscall_count": 12,
                    },
                }
                if logical in VALIDATOR.STANDARD_LOGICAL_IDS:
                    payload["standard_replay_equivalence"] = {"accepted": True}
                return payload, []

            def point(_run, *, experiment_id, material, level, volume_ratio):
                return {
                    "experiment_id": experiment_id,
                    "material": material,
                    "smearing_level": level,
                    "volume_ratio": volume_ratio,
                    "volume_per_atom_angstrom3": 10.0 * volume_ratio,
                    "e_ec_ev_per_atom": (volume_ratio - 1.0) ** 2,
                    "free_energy_ev_per_atom": (volume_ratio - 1.0) ** 2 - 0.01,
                    "entropy_minus_ts_ev_per_atom": -0.02,
                    "pressure_gpa": 0.0,
                }

            fit = {
                "v0_angstrom3_per_atom": 10.0,
                "b0_gpa": 50.0,
                "max_abs_residual_mev_per_atom": 0.0,
            }
            comparison = {
                "energy_accepted": True,
                "volume_accepted": True,
                "accepted": True,
            }
            fields = {"d1": 0.0, "d2": 0.0, "dg": 0.0, "rms_g_ev": 0.0, "accepted": True}
            k_pairs = [
                {
                    "coarse_experiment_id": f"c{index}",
                    "reference_experiment_id": f"r{index}",
                    "coarse_logical_experiment_id": f"lc{index}",
                    "reference_logical_experiment_id": f"lr{index}",
                    "field_metrics": fields,
                }
                for index in range(6)
            ]
            mapping = dict(VALIDATOR.LOGICAL_TO_EFFECTIVE_ID)
            with ExitStack() as stack:
                stack.enter_context(
                    mock.patch.object(ANALYZER, "validate_registration", return_value=({}, [], {}))
                )
                stack.enter_context(
                    mock.patch.object(ANALYZER, "_manifest_from_bytes", return_value=[])
                )
                stack.enter_context(
                    mock.patch.object(
                        ANALYZER,
                        "logical_effective_id",
                        side_effect=lambda _c, logical: mapping[logical],
                    )
                )
                stack.enter_context(
                    mock.patch.object(ANALYZER, "replay_effective_evidence", side_effect=replay)
                )
                stack.enter_context(mock.patch.object(ANALYZER, "_point_from_run", side_effect=point))
                stack.enter_context(mock.patch.object(ANALYZER, "_fit", return_value=(fit, [])))
                stack.enter_context(
                    mock.patch.object(ANALYZER, "compare_adjacent", return_value=comparison)
                )
                stack.enter_context(
                    mock.patch.object(ANALYZER, "_field_pair", side_effect=lambda *_args: dict(fields))
                )
                stack.enter_context(
                    mock.patch.object(
                        ANALYZER,
                        "evaluate_k_gate",
                        return_value={"accepted": True, "pairs": k_pairs},
                    )
                )
                summary = ANALYZER.analyze(
                    temporary,
                    config_path,
                    manifest_path,
                    output,
                    require_committed=False,
                )
            self.assertEqual(
                summary["audit_status"],
                "accepted",
                msg=json.dumps(
                    {"failures": summary["failure_ids"], "counts": summary["exact_counts"]},
                    sort_keys=True,
                ),
            )
            self.assertEqual(summary["accepted_reused_r1_count"], 10)
            self.assertEqual(summary["accepted_new_r2_count"], 30)
            self.assertEqual(summary["main_eos_scalar_count"], 42)
            self.assertEqual(summary["overall_protocol_status"], "pending_supervisor_completion")
            self.assertEqual(summary["g1_status"], "pending (1/6)")
            self.assertEqual(summary["authorized_scope"], "no_G1_advancement")
            persisted = json.loads((output / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["runtime_kmp_aggregate"]["rank_lifecycle_count"], 160)
            self.assertEqual(persisted["runtime_kmp_aggregate"]["successful_syscall_count"], 480)
            self.assertEqual(len(replay_registration_paths), 40)
            self.assertEqual(len(set(replay_registration_paths)), 1)
            snapshot_config, snapshot_manifest = replay_registration_paths[0]
            self.assertFalse(snapshot_config.exists())
            self.assertFalse(snapshot_manifest.exists())


if __name__ == "__main__":
    unittest.main()
