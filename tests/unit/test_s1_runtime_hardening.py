from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ACTIVATE = PROJECT_ROOT / "environment" / "activate.sh"
RUN_SINGLE = PROJECT_ROOT / "scripts" / "run_s1_single.sh"
RUN_MANIFEST = PROJECT_ROOT / "scripts" / "run_s1_non_equilibrium_manifest.sh"


class S1RuntimeHardeningTest(unittest.TestCase):
    def test_mpi_component_prefixes_default_to_recovery_prefix(self) -> None:
        values = self._source_activate({"M_OFDFT_PREFIX": "/tmp/recovery-prefix"})
        self.assertEqual(
            values,
            ["/tmp/recovery-prefix", "/tmp/recovery-prefix", "/tmp/recovery-prefix"],
        )

    def test_explicit_mpi_component_prefixes_are_preserved(self) -> None:
        values = self._source_activate(
            {
                "M_OFDFT_PREFIX": "/tmp/recovery-prefix",
                "OPAL_PREFIX": "/tmp/opal",
                "PRTE_PREFIX": "/tmp/prte",
                "PMIX_PREFIX": "/tmp/pmix",
            }
        )
        self.assertEqual(values, ["/tmp/opal", "/tmp/prte", "/tmp/pmix"])

    def test_runner_records_launcher_and_prefix_provenance(self) -> None:
        script = RUN_SINGLE.read_text()
        for field in (
            '"mpirun_path"',
            '"mpirun_sha256"',
            '"OPAL_PREFIX"',
            '"PRTE_PREFIX"',
            '"PMIX_PREFIX"',
        ):
            self.assertIn(field, script)
        checksum = script.index('sha256sum INPUT STRU KPT "$pseudopotential"')
        execution = script.index('/usr/bin/time -v "$mpirun"')
        self.assertLess(checksum, execution)
        self.assertNotIn('sha256sum "$run_directory/INPUT"', script)

    def test_runner_writes_portable_checksum_and_runtime_metadata_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            scripts = repository / "scripts"
            environment_directory = repository / "environment"
            input_directory = repository / "inputs/candidate"
            pseudo_directory = repository / "assets/pseudo"
            prefix_bin = repository / "prefix/bin"
            for directory in (
                scripts,
                environment_directory,
                input_directory,
                pseudo_directory,
                prefix_bin,
            ):
                directory.mkdir(parents=True, exist_ok=True)
            shutil.copy2(RUN_SINGLE, scripts / RUN_SINGLE.name)
            shutil.copy2(ACTIVATE, environment_directory / ACTIVATE.name)
            (input_directory / "INPUT").write_text(
                "INPUT_PARAMETERS\npseudo_dir ../../assets/pseudo\n"
            )
            (input_directory / "STRU").write_text("structure\n")
            (input_directory / "KPT").write_text("kpoints\n")
            (input_directory / "metadata.json").write_text(
                json.dumps({"pseudopotential": "al.gga.psp"}) + "\n"
            )
            (pseudo_directory / "al.gga.psp").write_text("pseudo\n")
            self._write_fake_gnu_tools(prefix_bin)
            self._git(repository, "init")
            self._git(repository, "config", "user.name", "Unit Test")
            self._git(repository, "config", "user.email", "unit@example.invalid")
            self._git(repository, "add", ".")
            self._git(repository, "commit", "-m", "fixture")

            completed = subprocess.run(
                [
                    str(scripts / RUN_SINGLE.name),
                    "S1-20260805-901",
                    str(input_directory),
                ],
                cwd=repository,
                env={
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "M_OFDFT_PREFIX": str(repository / "prefix"),
                    "M_OFDFT_RUNTIME": str(repository),
                    "M_OFDFT_ABACUS": "/usr/bin/true",
                    "M_OFDFT_MPIRUN": "/usr/bin/true",
                },
                check=False,
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(completed.returncode, 0)
            run = repository / "runs/S1-20260805-901"
            self.assertTrue(
                (run / "experiment_metadata.json").is_file(),
                f"rc={completed.returncode}\nstdout={completed.stdout}\nstderr={completed.stderr}\n"
                f"files={sorted(str(path.relative_to(repository)) for path in repository.rglob('*'))}",
            )
            metadata = json.loads((run / "experiment_metadata.json").read_text())
            expected_mpirun = Path("/usr/bin/true").resolve()
            self.assertEqual(metadata["mpirun_path"], str(expected_mpirun))
            self.assertEqual(
                metadata["mpirun_sha256"],
                hashlib.sha256(expected_mpirun.read_bytes()).hexdigest(),
            )
            for key in ("OPAL_PREFIX", "PRTE_PREFIX", "PMIX_PREFIX"):
                self.assertEqual(metadata[key], str(repository / "prefix"))
            checksum_lines = (run / "INPUT_SHA256SUMS").read_text().splitlines()
            self.assertEqual(
                {line.split(maxsplit=1)[1] for line in checksum_lines},
                {"INPUT", "STRU", "KPT", "al.gga.psp"},
            )

    def test_manifest_resume_uses_full_archive_validation(self) -> None:
        script = RUN_MANIFEST.read_text()
        self.assertIn("_read_refined_point", script)
        self.assertIn("provenance-invalid", script)

    def test_modified_shell_scripts_parse(self) -> None:
        subprocess.run(
            [
                "/bin/bash",
                "-n",
                str(ACTIVATE),
                str(RUN_SINGLE),
                str(RUN_MANIFEST),
            ],
            check=True,
            text=True,
            capture_output=True,
        )

    @staticmethod
    def _source_activate(overrides: dict[str, str]) -> list[str]:
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "M_OFDFT_RUNTIME": "/tmp/runtime",
            **overrides,
        }
        completed = subprocess.run(
            [
                "/bin/bash",
                "-c",
                f'source "{ACTIVATE}"; printf "%s\\n" "$OPAL_PREFIX" "$PRTE_PREFIX" "$PMIX_PREFIX"',
            ],
            env=environment,
            check=True,
            text=True,
            capture_output=True,
        )
        return completed.stdout.splitlines()

    @staticmethod
    def _write_fake_gnu_tools(prefix_bin: Path) -> None:
        sed = prefix_bin / "sed"
        sed.write_text(
            "#!/bin/sh\n"
            "/usr/bin/python3 - \"$3\" <<'PY'\n"
            "import sys\n"
            "from pathlib import Path\n"
            "path = Path(sys.argv[1])\n"
            "path.write_text('\\n'.join('pseudo_dir .' if line.startswith('pseudo_dir ') "
            "else line for line in path.read_text().splitlines()) + '\\n')\n"
            "PY\n"
        )
        sed.chmod(0o755)
        sha256sum = prefix_bin / "sha256sum"
        sha256sum.write_text(
            "#!/usr/bin/python3\n"
            "import hashlib, sys\n"
            "from pathlib import Path\n"
            "for name in sys.argv[1:]:\n"
            "    print(f'{hashlib.sha256(Path(name).read_bytes()).hexdigest()}  {name}')\n"
        )
        sha256sum.chmod(0o755)

    @staticmethod
    def _git(repository: Path, *arguments: str) -> None:
        subprocess.run(
            ["git", *arguments],
            cwd=repository,
            check=True,
            text=True,
            capture_output=True,
        )


if __name__ == "__main__":
    unittest.main()
