from __future__ import annotations

import csv
import io
import json
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import generate_s1_g1_thermodynamic_label_audit_r2 as GENERATOR  # noqa: E402
from s1_g1_thermodynamic_label_common import (  # noqa: E402
    AUDIT_IDS as R1_AUDIT_IDS,
    MANIFEST_FIELDS,
    canonical_json_bytes,
    parse_input_text,
    sha256_bytes,
)


class S1G1ThermodynamicLabelAuditR2GeneratorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.r1_config, cls.r1_rows = GENERATOR.load_r1_registration(PROJECT_ROOT)
        cls.logical_matrix, cls.new_matrix = GENERATOR.build_plan(
            PROJECT_ROOT, cls.r1_config, cls.r1_rows
        )
        cls.artifacts, cls.manifest_rows = GENERATOR.derive_registration_artifacts(
            PROJECT_ROOT, cls.new_matrix, cls.r1_rows
        )

    def test_namespace_is_exact_ten_reused_plus_thirty_new(self) -> None:
        expected_logical_suffixes = (
            34,
            40,
            *range(1, 21),
            22,
            23,
            25,
            26,
            29,
            30,
            32,
            33,
        )
        self.assertEqual(
            tuple(GENERATOR.NEW_TO_LOGICAL), GENERATOR.R2_AUDIT_IDS
        )
        self.assertEqual(
            tuple(GENERATOR.NEW_TO_LOGICAL.values()),
            tuple(f"S1-20260806-{value:03d}" for value in expected_logical_suffixes),
        )
        self.assertEqual(len(GENERATOR.R1_REUSED_AUDIT_IDS), 10)
        self.assertEqual(len(GENERATOR.R2_AUDIT_IDS), 30)
        self.assertEqual(set(GENERATOR.LOGICAL_TO_EFFECTIVE_ID), set(R1_AUDIT_IDS))
        self.assertEqual(len(set(GENERATOR.LOGICAL_TO_EFFECTIVE_ID.values())), 40)
        self.assertEqual(
            set(GENERATOR.LOGICAL_TO_EFFECTIVE_ID.values()),
            set(GENERATOR.R1_REUSED_AUDIT_IDS) | set(GENERATOR.R2_AUDIT_IDS),
        )
        self.assertEqual(GENERATOR.EXECUTION_ORDER, GENERATOR.R2_AUDIT_IDS)

    def test_frozen_r1_registration_and_input_tree_are_exact(self) -> None:
        self.assertEqual(
            self.r1_config["protocol_revision"],
            "S1-G1-THERMODYNAMIC-LABEL-AUDIT-R1",
        )
        self.assertEqual(len(self.r1_rows), 40)
        self.assertEqual(
            sha256_bytes(
                GENERATOR._git_blob(
                    PROJECT_ROOT,
                    GENERATOR.R1_PREREGISTRATION_COMMIT,
                    GENERATOR.R1_CONFIG_PATH.as_posix(),
                )
            ),
            GENERATOR.R1_CONFIG_SHA256,
        )
        self.assertEqual(
            GENERATOR._git_object_id(
                PROJECT_ROOT,
                GENERATOR.R1_PREREGISTRATION_COMMIT,
                GENERATOR.R1_INPUT_ROOT.as_posix(),
                "tree",
            ),
            GENERATOR.R1_INPUT_ROOT_TREE_OID,
        )

    def test_plan_preserves_logical_slots_and_uses_p1_then_p2(self) -> None:
        self.assertEqual(len(self.logical_matrix), 40)
        self.assertEqual(len(self.new_matrix), 30)
        by_logical = {
            row["logical_experiment_id"]: row for row in self.logical_matrix
        }
        self.assertEqual(
            by_logical["S1-20260806-034"]["effective_experiment_id"],
            "S1-20260806-041",
        )
        self.assertEqual(
            by_logical["S1-20260806-024"]["effective_experiment_id"],
            "S1-20260806-024",
        )
        self.assertEqual(
            by_logical["S1-20260806-024"]["evidence_origin"], "r1_reused"
        )
        self.assertEqual(
            by_logical["S1-20260806-034"]["evidence_origin"], "r2_executed"
        )
        self.assertEqual(
            [row["execution_phase"] for row in self.new_matrix[:2]], ["P1", "P1"]
        )
        self.assertEqual(
            {row["execution_phase"] for row in self.new_matrix[2:]}, {"P2"}
        )
        self.assertEqual(
            self.new_matrix[0]["effective_reference_experiment_id"],
            "S1-20260806-062",
        )
        self.assertEqual(
            self.new_matrix[1]["effective_common_quarter_partner_id"],
            "S1-20260806-041",
        )

    def test_derived_inputs_change_only_registered_provenance_fields(self) -> None:
        r1_by_id = {row["experiment_id"]: row for row in self.r1_rows}
        for plan in self.new_matrix:
            new_id = str(plan["experiment_id"])
            logical_id = str(plan["logical_experiment_id"])
            old_row = r1_by_id[logical_id]
            old_root = (
                f"{GENERATOR.R1_INPUT_ROOT.as_posix()}/{logical_id}"
            )
            new_root = f"{GENERATOR.INPUT_ROOT.as_posix()}/{new_id}"
            old_input = GENERATOR._git_blob(
                PROJECT_ROOT,
                GENERATOR.R1_PREREGISTRATION_COMMIT,
                f"{old_root}/INPUT",
            )
            old_stru = GENERATOR._git_blob(
                PROJECT_ROOT,
                GENERATOR.R1_PREREGISTRATION_COMMIT,
                f"{old_root}/STRU",
            )
            old_kpt = GENERATOR._git_blob(
                PROJECT_ROOT,
                GENERATOR.R1_PREREGISTRATION_COMMIT,
                f"{old_root}/KPT",
            )
            old_metadata = json.loads(
                GENERATOR._git_blob(
                    PROJECT_ROOT,
                    GENERATOR.R1_PREREGISTRATION_COMMIT,
                    f"{old_root}/metadata.json",
                )
            )
            new_input = self.artifacts[f"{new_root}/INPUT"]
            new_metadata = json.loads(self.artifacts[f"{new_root}/metadata.json"])
            self.assertEqual(self.artifacts[f"{new_root}/STRU"], old_stru)
            self.assertEqual(self.artifacts[f"{new_root}/KPT"], old_kpt)
            old_parsed = parse_input_text(old_input)
            new_parsed = parse_input_text(new_input)
            self.assertEqual(
                {key: value for key, value in old_parsed.items() if key != "suffix"},
                {key: value for key, value in new_parsed.items() if key != "suffix"},
            )
            self.assertEqual(new_parsed["suffix"], (plan["suffix"],))
            changed = {
                key
                for key in set(old_metadata) | set(new_metadata)
                if old_metadata.get(key) != new_metadata.get(key)
            }
            self.assertEqual(
                changed, {"experiment_id", "protocol_revision", "suffix"}
            )
            self.assertEqual(new_metadata["experiment_id"], new_id)
            self.assertEqual(new_metadata["protocol_revision"], GENERATOR.PROTOCOL_REVISION)
            self.assertEqual(new_metadata["dataset_kind"], old_metadata["dataset_kind"])
            self.assertEqual(old_row["reference_experiment_id"], new_metadata["reference_experiment_id"])

    def test_manifest_keeps_r1_schema_and_binds_all_derived_hashes(self) -> None:
        encoded = GENERATOR._manifest_bytes(self.manifest_rows)
        with io.StringIO(encoded.decode("utf-8"), newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            self.assertEqual(tuple(reader.fieldnames or ()), MANIFEST_FIELDS)
            observed = list(reader)
        self.assertEqual(len(observed), 30)
        self.assertEqual(
            tuple(row["experiment_id"] for row in observed),
            GENERATOR.EXECUTION_ORDER,
        )
        self.assertEqual(observed[0]["execution_phase"], "P1")
        self.assertEqual(observed[2]["execution_phase"], "P2")
        for row in observed:
            root = row["input_directory"]
            self.assertEqual(
                row["input_sha256"], sha256_bytes(self.artifacts[f"{root}/INPUT"])
            )
            self.assertEqual(
                row["stru_sha256"], sha256_bytes(self.artifacts[f"{root}/STRU"])
            )
            self.assertEqual(
                row["kpt_sha256"], sha256_bytes(self.artifacts[f"{root}/KPT"])
            )
            self.assertEqual(
                row["metadata_sha256"],
                sha256_bytes(self.artifacts[f"{root}/metadata.json"]),
            )

    def test_r1_bridge_binds_ten_accepted_trees_and_failed_archive(self) -> None:
        bridge = GENERATOR.build_r1_bridge(
            PROJECT_ROOT, self.r1_config, self.r1_rows
        )
        self.assertEqual(
            bridge["preregistration_commit"], GENERATOR.R1_PREREGISTRATION_COMMIT
        )
        self.assertEqual(bridge["input_root"]["tree_oid"], GENERATOR.R1_INPUT_ROOT_TREE_OID)
        self.assertEqual(tuple(bridge["reused_runs"]), GENERATOR.R1_REUSED_AUDIT_IDS)
        self.assertEqual(len(bridge["reused_runs"]), 10)
        for experiment_id, anchor in bridge["reused_runs"].items():
            self.assertEqual(
                anchor["introduction_commit"],
                GENERATOR.EXPECTED_REUSED_RUNS[experiment_id]["introduction_commit"],
            )
            self.assertEqual(
                anchor["tree_oid"],
                GENERATOR.EXPECTED_REUSED_RUNS[experiment_id]["tree_oid"],
            )
            self.assertEqual(len(anchor["artifacts"]), 8)
        failure = bridge["failed_run"]
        self.assertEqual(failure["failure_commit"], GENERATOR.R1_FAILURE_COMMIT)
        self.assertEqual(failure["archive_commit"], GENERATOR.R1_ARCHIVE_COMMIT)
        self.assertEqual(failure["failure_tree_oid"], GENERATOR.R1_FAILURE_TREE_OID)
        self.assertEqual(failure["archive_tree_oid"], GENERATOR.R1_FAILURE_TREE_OID)
        self.assertEqual(len(failure["artifacts"]), 71)
        self.assertEqual(failure["classification"]["status"], "indeterminate")
        self.assertEqual(failure["classification"]["accepted_scientific_denominator_contribution"], 0)

    def test_marker_and_detachment_contract_are_frozen(self) -> None:
        self.assertEqual(
            GENERATOR.SUPERVISOR_STATE_DIRECTORY,
            "/home/shenwei01/.local/state/m_ofdft/"
            "g1_thermodynamic_label_audit_r2_20260806",
        )
        self.assertEqual(
            GENERATOR.ATTEMPT_LEDGER_ROOT.as_posix(),
            "orchestration/s1/g1_thermodynamic_label_audit_r2_20260806/attempts",
        )
        self.assertEqual(len(GENERATOR.ATTEMPT_MARKER_REQUIRED_KEYS), 18)
        self.assertEqual(
            set(GENERATOR.ATTEMPT_MARKER_REQUIRED_KEYS),
            {
                "schema_version",
                "protocol_revision",
                "experiment_id",
                "logical_experiment_id",
                "status",
                "retry_policy",
                "created_utc",
                "config_path",
                "config_sha256",
                "manifest_path",
                "manifest_sha256",
                "git_head_before_attempt",
                "supervisor_state_directory",
                "supervisor_launch_path",
                "supervisor_launch_sha256",
                "supervisor_pid",
                "supervisor_start_time_ticks",
                "boot_id",
            },
        )

    def test_supervisor_completion_and_barrier_failure_contracts_are_frozen(self) -> None:
        self.assertEqual(
            GENERATOR.SUPERVISOR_COMPLETION_PATH.as_posix(),
            "orchestration/s1/g1_thermodynamic_label_audit_r2_20260806/"
            "supervisor_completion.json",
        )
        self.assertEqual(
            GENERATOR.BARRIER_FAILURE_ROOT.as_posix(),
            "orchestration/s1/g1_thermodynamic_label_audit_r2_20260806/"
            "barrier_failures",
        )
        self.assertEqual(len(GENERATOR.SUPERVISOR_COMPLETION_REQUIRED_KEYS), 24)
        self.assertEqual(
            set(GENERATOR.SUPERVISOR_COMPLETION_REQUIRED_KEYS),
            {
                "schema_version",
                "protocol_revision",
                "status",
                "created_utc",
                "config_path",
                "config_sha256",
                "manifest_path",
                "manifest_sha256",
                "git_head_before_completion",
                "supervisor_state_directory",
                "supervisor_launch_path",
                "supervisor_launch_sha256",
                "supervisor_terminal_path",
                "supervisor_terminal_sha256",
                "supervisor_journal_path",
                "supervisor_journal_sha256",
                "supervisor_pid",
                "supervisor_start_time_ticks",
                "boot_id",
                "runner_exit_code",
                "analysis_path",
                "analysis_sha256",
                "analysis_audit_status",
                "final_acceptance_policy",
            },
        )
        self.assertEqual(len(GENERATOR.BARRIER_FAILURE_REQUIRED_KEYS), 18)
        self.assertEqual(
            set(GENERATOR.BARRIER_FAILURE_REQUIRED_KEYS),
            {
                "schema_version",
                "protocol_revision",
                "status",
                "created_utc",
                "barrier_name",
                "experiment_id",
                "logical_experiment_id",
                "command_argv",
                "exit_code",
                "config_path",
                "config_sha256",
                "manifest_path",
                "manifest_sha256",
                "git_head_before_failure",
                "supervisor_state_directory",
                "supervisor_launch_path",
                "supervisor_launch_sha256",
                "retry_policy",
            },
        )

    def test_prepare_copies_all_scientific_and_runtime_gates_from_r1(self) -> None:
        prepared = GENERATOR.prepare(PROJECT_ROOT)
        config = prepared["config"]
        self.assertEqual(config["status"], "preregistered")
        self.assertEqual(config["registered_experiment_ids"], list(GENERATOR.R2_AUDIT_IDS))
        self.assertEqual(config["execution_order"], list(GENERATOR.EXECUTION_ORDER))
        self.assertEqual(config["acceptance"], self.r1_config["acceptance"])
        for key in (
            "numerical_axes",
            "output_contract",
            "thermodynamic_semantics",
            "runtime",
            "runtime_audit",
            "kmp_contract",
            "rank_count",
        ):
            self.assertEqual(config[key], self.r1_config[key])
        self.assertEqual(config["manifest"]["row_count"], 30)
        self.assertEqual(config["input_derivation"]["derived_file_count"], 120)
        self.assertEqual(
            config["execution"]["detachment_attestation_path"],
            GENERATOR.DETACHMENT_ATTESTATION_PATH.as_posix(),
        )
        self.assertEqual(
            config["execution"]["attempt_marker"]["required_keys_exact"],
            list(GENERATOR.ATTEMPT_MARKER_REQUIRED_KEYS),
        )
        self.assertEqual(
            config["execution"]["attempt_marker"]["external_basename_template"],
            "{experiment_id}.json",
        )
        completion = config["execution"]["supervisor_completion_contract"]
        self.assertEqual(completion["scientific_analysis_status"], "accepted")
        self.assertEqual(
            completion["overall_protocol_status_before_completion"],
            "pending_supervisor_completion",
        )
        self.assertTrue(
            completion["overall_acceptance_requires_committed_supervisor_completion"]
        )
        self.assertTrue(completion["final_acceptance_requires_validator_revalidation"])
        self.assertEqual(
            completion["allowed_post_completion_commit_paths_exact"],
            list(GENERATOR.POST_TERMINAL_DOCUMENTATION_PATHS),
        )
        self.assertEqual(
            completion["required_keys_exact"],
            list(GENERATOR.SUPERVISOR_COMPLETION_REQUIRED_KEYS),
        )
        barrier = config["execution"]["barrier_failure_contract"]
        self.assertEqual(
            barrier["required_keys_exact"],
            list(GENERATOR.BARRIER_FAILURE_REQUIRED_KEYS),
        )
        self.assertTrue(barrier["exact_scope_commit_required"])
        self.assertTrue(barrier["stop_immediately_after_failure_commit"])
        self.assertEqual(
            barrier["allowed_post_failure_commit_paths_exact"],
            list(GENERATOR.POST_TERMINAL_DOCUMENTATION_PATHS),
        )
        self.assertTrue(config["execution"]["atomic_supervisor_evidence_publish_required"])
        self.assertTrue(config["execution"]["runner_live_parent_binding_required"])
        self.assertEqual(
            config["execution"]["runner_parent_binding_fields"],
            [
                "state_directory",
                "supervisor_pid",
                "supervisor_start_time_ticks",
                "boot_id",
                "launch_sha256",
                "go_sha256",
            ],
        )
        ambient = config["execution"]["ambient_environment"]
        self.assertEqual(
            ambient["keys_exact"],
            list(GENERATOR.FROZEN_AMBIENT_ENVIRONMENT_KEYS),
        )
        self.assertEqual(
            ambient["values_exact"],
            GENERATOR.FROZEN_AMBIENT_ENVIRONMENT_VALUES,
        )
        self.assertEqual(
            ambient["canonical_values_sha256"],
            sha256_bytes(
                canonical_json_bytes(GENERATOR.FROZEN_AMBIENT_ENVIRONMENT_VALUES)
            ),
        )
        self.assertTrue(ambient["mutating_launcher_exact_match_required"])
        self.assertTrue(ambient["python_no_user_site_required"])
        self.assertTrue(ambient["validator_subprocess_explicit_environment_required"])
        self.assertTrue(ambient["supervisor_subprocess_explicit_environment_required"])
        self.assertEqual(
            ambient["runner_additional_binding_keys_exact"],
            list(GENERATOR.RUNNER_BINDING_ENVIRONMENT_KEYS),
        )
        self.assertTrue(ambient["runner_registered_bash_required"])
        self.assertEqual(
            config["new_run_matrix"][0]["r1_manifest_row_sha256"],
            sha256_bytes(canonical_json_bytes(self.r1_rows[33])),
        )


if __name__ == "__main__":
    unittest.main()
