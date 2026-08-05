from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNNER = PROJECT_ROOT / "scripts" / "run_s1_manifest.sh"


class RunS1ManifestTest(unittest.TestCase):
    def test_stdin_consuming_worker_does_not_swallow_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory) / "repository"
            scripts = repository / "scripts"
            scripts.mkdir(parents=True)
            shutil.copy2(RUNNER, scripts / RUNNER.name)
            worker = scripts / "run_s1_single.sh"
            worker.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "while IFS= read -r _; do :; done\n"
                "mkdir -p \"runs/$1\"\n"
                "printf '{}\\n' > \"runs/$1/result.json\"\n",
                encoding="utf-8",
            )
            worker.chmod(0o755)
            manifest = repository / "manifest.tsv"
            manifest.write_text(
                "experiment_id\tinput_directory\tmaterial\tseries_id\tvolume_ratio\n"
                "S1-20260805-901\tinputs/one\tal\tofdft\t0.9\n"
                "S1-20260805-902\tinputs/two\tal\tofdft\t1.0\n",
                encoding="utf-8",
            )
            self._git(repository, "init")
            self._git(repository, "config", "user.name", "Unit Test")
            self._git(repository, "config", "user.email", "unit@example.invalid")
            self._git(repository, "add", ".")
            self._git(repository, "commit", "-m", "fixture")

            completed = subprocess.run(
                [str(scripts / RUNNER.name), str(manifest)],
                cwd=repository,
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue((repository / "runs/S1-20260805-901/result.json").is_file())
            self.assertTrue((repository / "runs/S1-20260805-902/result.json").is_file())
            self.assertIn("DONE S1-20260805-902", completed.stdout)

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
