from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PROJECT_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from s1_g1_kmp_runtime_contract import (  # noqa: E402
    KMP_PATTERN,
    PROTOCOL_REVISION,
    validate_kmp_runtime_contract,
)


OBJECT_HEADER = (
    "pid",
    "role",
    "rank",
    "mapped_path",
    "loaded_realpath",
    "loaded_sha256",
    "classification",
)
LIBOMP_PATH = "/recovery/conda_prefix/lib/libomp.so"
LIBOMP_REALPATH = "/recovery/conda_prefix/lib/libomp.so"
LIBOMP_SHA256 = "a" * 64


class _Fixture:
    def __init__(
        self,
        root: Path,
        *,
        capture_kmp_mappings: bool = False,
        register_kmp_pattern: bool = True,
    ) -> None:
        self.run = root / "run"
        self.audit = self.run / "mpi_runtime_audit"
        self.trace = self.audit / "strace"
        self.trace.mkdir(parents=True)
        self.rank_pids = {rank: 4101 + rank for rank in range(4)}
        (self.audit / "audit.json").write_text(
            json.dumps(
                {
                    "rank_pids": {
                        str(rank): pid for rank, pid in self.rank_pids.items()
                    },
                    "transient_mapping_patterns": (
                        [r"^/SYSV[0-9A-Fa-f]+$", KMP_PATTERN]
                        if register_kmp_pattern
                        else [r"^/SYSV[0-9A-Fa-f]+$"]
                    ),
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        self.rows: list[dict[str, str]] = []
        for rank, pid in self.rank_pids.items():
            path = self.kmp_path(rank)
            (self.trace / f"trace.{pid}").write_text(
                "\n".join(
                    (
                        f'openat(AT_FDCWD, "{path}", '
                        "O_RDWR|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC, 0666) = 86",
                        f'openat(AT_FDCWD, "{path}", '
                        "O_RDONLY|O_NOFOLLOW|O_CLOEXEC) = 8",
                        f'unlink("{path}") = 0',
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            self.rows.append(
                {
                    "pid": str(pid),
                    "role": "rank",
                    "rank": str(rank),
                    "mapped_path": LIBOMP_PATH,
                    "loaded_realpath": LIBOMP_REALPATH,
                    "loaded_sha256": LIBOMP_SHA256,
                    "classification": "recovery_runtime",
                }
            )
            if capture_kmp_mappings:
                self.rows.append(
                    {
                        "pid": str(pid),
                        "role": "rank",
                        "rank": str(rank),
                        "mapped_path": path,
                        "loaded_realpath": path,
                        "loaded_sha256": "",
                        "classification": "transient_system",
                    }
                )
        (self.trace / "trace.9999").write_text(
            'stat("/etc/ld.so.cache", {st_mode=S_IFREG|0644}) = 0\n',
            encoding="utf-8",
        )
        self.write_objects()

    def kmp_path(self, rank: int) -> str:
        return f"/dev/shm/__KMP_REGISTERED_LIB_{self.rank_pids[rank]}_0"

    def trace_path(self, rank: int) -> Path:
        return self.trace / f"trace.{self.rank_pids[rank]}"

    def write_objects(self) -> None:
        with (self.audit / "objects.tsv").open(
            "w", newline="", encoding="utf-8"
        ) as handle:
            writer = csv.DictWriter(
                handle, fieldnames=OBJECT_HEADER, delimiter="\t", lineterminator="\n"
            )
            writer.writeheader()
            writer.writerows(self.rows)

    def validate(self, *, require_registered_mapping_pattern: bool = True) -> dict:
        return validate_kmp_runtime_contract(
            self.run,
            LIBOMP_PATH,
            LIBOMP_REALPATH,
            LIBOMP_SHA256,
            require_registered_mapping_pattern=require_registered_mapping_pattern,
        )


class S1G1KMPRuntimeContractTest(unittest.TestCase):
    def test_accepts_exact_four_rank_lifecycle_without_captured_kmp_maps(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = _Fixture(Path(temporary))
            payload = fixture.validate()
        self.assertTrue(payload["accepted"])
        self.assertEqual(payload["protocol_revision"], PROTOCOL_REVISION)
        self.assertEqual(payload["rank_count"], 4)
        self.assertEqual(payload["lifecycle_count"], 4)
        self.assertEqual(payload["successful_syscall_count"], 12)
        self.assertEqual(payload["captured_mapping_count"], 0)
        self.assertEqual(payload["libomp_mapping_count"], 4)
        self.assertEqual(payload["libomp_path"], LIBOMP_PATH)
        self.assertEqual(payload["libomp_sha256"], LIBOMP_SHA256)

    def test_accepts_one_captured_transient_kmp_mapping_per_rank(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = _Fixture(Path(temporary), capture_kmp_mappings=True)
            payload = fixture.validate()
        self.assertEqual(payload["captured_mapping_count"], 4)
        self.assertEqual(
            [row["captured_mapping_count"] for row in payload["ranks"]],
            [1, 1, 1, 1],
        )

    def test_explicit_r1_bridge_requires_absent_pattern_and_keeps_hard_gates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = _Fixture(Path(temporary), register_kmp_pattern=False)
            payload = fixture.validate(require_registered_mapping_pattern=False)
        self.assertTrue(payload["accepted"])
        self.assertEqual(payload["contract_mode"], "r1_calibration_bridge")
        self.assertFalse(payload["registered_mapping_pattern_required"])
        self.assertEqual(payload["successful_syscall_count"], 12)
        self.assertEqual(payload["captured_mapping_count"], 0)

    def test_r1_bridge_accepts_only_legacy_unexpected_captured_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = _Fixture(
                Path(temporary),
                capture_kmp_mappings=True,
                register_kmp_pattern=False,
            )
            for row in fixture.rows:
                if "__KMP_REGISTERED_LIB_" in row["mapped_path"]:
                    row["classification"] = "unexpected"
            fixture.write_objects()
            payload = fixture.validate(require_registered_mapping_pattern=False)
        self.assertEqual(payload["captured_mapping_count"], 4)

    def test_registered_and_bridge_modes_cannot_be_selected_implicitly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            r1_fixture = _Fixture(
                Path(temporary) / "r1", register_kmp_pattern=False
            )
            with self.assertRaisesRegex(ValueError, "must append"):
                r1_fixture.validate()
            r2_fixture = _Fixture(Path(temporary) / "r2")
            with self.assertRaisesRegex(ValueError, "must predate"):
                r2_fixture.validate(require_registered_mapping_pattern=False)

    def test_kmp_regex_rejects_out_of_bounds_pid_uid_and_suffixes(self) -> None:
        pattern = re.compile(KMP_PATTERN)
        self.assertIsNotNone(pattern.fullmatch("/dev/shm/__KMP_REGISTERED_LIB_1_0"))
        self.assertIsNotNone(pattern.fullmatch("/dev/shm/__KMP_REGISTERED_LIB_10_0"))
        for value in (
            "/dev/shm/__KMP_REGISTERED_LIB_0_0",
            "/dev/shm/__KMP_REGISTERED_LIB_01_0",
            "/dev/shm/__KMP_REGISTERED_LIB_1_1",
            "/dev/shm/__KMP_REGISTERED_LIB_1_0.extra",
            "/tmp/__KMP_REGISTERED_LIB_1_0",
        ):
            with self.subTest(value=value):
                self.assertIsNone(pattern.fullmatch(value))

        with tempfile.TemporaryDirectory() as temporary:
            fixture = _Fixture(Path(temporary))
            path = fixture.trace_path(0)
            path.write_text(
                path.read_text().replace(fixture.kmp_path(0), "/dev/shm/__KMP_REGISTERED_LIB_4101_1")
            )
            with self.assertRaisesRegex(ValueError, "out of contract"):
                fixture.validate()

    def test_rejects_kmp_path_pid_that_differs_from_rank_pid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = _Fixture(Path(temporary))
            path = fixture.trace_path(0)
            path.write_text(
                path.read_text().replace(fixture.kmp_path(0), fixture.kmp_path(1))
            )
            with self.assertRaisesRegex(ValueError, "path PID"):
                fixture.validate()

    def test_rejects_any_extra_kmp_event_across_trace_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = _Fixture(Path(temporary))
            support = fixture.trace / "trace.9999"
            support.write_text(
                support.read_text()
                + f'stat("{fixture.kmp_path(0)}", {{st_mode=S_IFREG|0666}}) = 0\n'
            )
            with self.assertRaisesRegex(ValueError, "non-rank trace PID"):
                fixture.validate()

    def test_rejects_wrong_create_flags_or_mode(self) -> None:
        replacements = (
            (
                "O_RDWR|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC",
                "O_RDWR|O_CREAT|O_EXCL|O_CLOEXEC",
            ),
            (", 0666) = 86", ", 0600) = 86"),
            (
                "O_RDONLY|O_NOFOLLOW|O_CLOEXEC",
                "O_RDONLY|O_CLOEXEC",
            ),
        )
        for index, (old, new) in enumerate(replacements):
            with self.subTest(replacement=new), tempfile.TemporaryDirectory() as temporary:
                fixture = _Fixture(Path(temporary) / str(index))
                path = fixture.trace_path(0)
                path.write_text(path.read_text().replace(old, new, 1))
                with self.assertRaisesRegex(ValueError, "unsupported or unsuccessful"):
                    fixture.validate()

    def test_rejects_unsuccessful_unlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = _Fixture(Path(temporary))
            path = fixture.trace_path(0)
            path.write_text(
                path.read_text().replace(
                    f'unlink("{fixture.kmp_path(0)}") = 0',
                    f'unlink("{fixture.kmp_path(0)}") = -1 ENOENT (No such file or directory)',
                )
            )
            with self.assertRaisesRegex(ValueError, "unsupported or unsuccessful"):
                fixture.validate()

    def test_rejects_libomp_sha_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = _Fixture(Path(temporary))
            fixture.rows[0]["loaded_sha256"] = "b" * 64
            fixture.write_objects()
            with self.assertRaisesRegex(ValueError, "libomp.so SHA-256 differs"):
                fixture.validate()

    def test_shim_patch_is_process_local_and_restores_r1_module(self) -> None:
        probe = f"""
import json
import sys
from pathlib import Path
sys.path.insert(0, {str(SCRIPTS)!r})
import runtime_relocation_audit_launcher as r1
import s1_mpi_prefix_equivalence_common as common
import runtime_relocation_audit_launcher_g1_r2 as shim
from s1_g1_kmp_runtime_contract import KMP_PATTERN

before_patterns = r1.TRANSIENT_MAPPING_PATTERNS
before_common = common.TRANSIENT_MAPPING_PATTERNS
before_classify = r1.classify_mapping
observed = {{}}
def fake_main():
    observed["patterns"] = list(r1.TRANSIENT_MAPPING_PATTERNS)
    path = Path("/dev/shm/__KMP_REGISTERED_LIB_77_0")
    observed["classification"] = r1.classify_mapping(
        path, path, Path("/old"), Path("/recovery")
    )
    return 17
r1.main = fake_main
code = shim.main()
print(json.dumps({{
    "code": code,
    "during_patterns": observed["patterns"],
    "during_classification": observed["classification"],
    "only_appended": tuple(observed["patterns"][:-1]) == before_patterns,
    "r1_patterns_restored": r1.TRANSIENT_MAPPING_PATTERNS == before_patterns,
    "r1_classify_restored": r1.classify_mapping is before_classify,
    "common_unchanged": common.TRANSIENT_MAPPING_PATTERNS == before_common,
    "common_has_kmp": KMP_PATTERN in common.TRANSIENT_MAPPING_PATTERNS,
}}))
"""
        completed = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=PROJECT_ROOT,
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["code"], 17)
        self.assertEqual(payload["during_patterns"][-1], KMP_PATTERN)
        self.assertEqual(payload["during_patterns"].count(KMP_PATTERN), 1)
        self.assertEqual(payload["during_classification"], "transient_system")
        self.assertTrue(payload["only_appended"])
        self.assertTrue(payload["r1_patterns_restored"])
        self.assertTrue(payload["r1_classify_restored"])
        self.assertTrue(payload["common_unchanged"])
        self.assertFalse(payload["common_has_kmp"])


if __name__ == "__main__":
    unittest.main()
