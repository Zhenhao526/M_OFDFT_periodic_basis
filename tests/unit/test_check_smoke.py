from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_smoke.py"
SPEC = importlib.util.spec_from_file_location("check_smoke", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class CheckSmokeTest(unittest.TestCase):
    def _write_log(self, root: Path, repeat: str, energy: float) -> None:
        output = root / repeat / "OUT.al_fcc_wt_smoke"
        output.mkdir(parents=True)
        (output / "running_scf.log").write_text(
            f"#SCF IS CONVERGED#\n!FINAL_ETOT_IS {energy:.12f} eV\n",
            encoding="utf-8",
        )

    def test_evaluate_passes_reproducible_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_log(root, "repeat1", -100.0)
            self._write_log(root, "repeat2", -100.0000002)
            result = MODULE.evaluate(root, natoms=4, tolerance_mev_per_atom=0.1)
            self.assertTrue(result["passed"])
            self.assertLess(result["difference_mev_per_atom"], 0.1)

    def test_read_energy_rejects_unconverged_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "running_scf.log"
            path.write_text("!FINAL_ETOT_IS -1.0 eV\n", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                MODULE.read_energy(path)


if __name__ == "__main__":
    unittest.main()

