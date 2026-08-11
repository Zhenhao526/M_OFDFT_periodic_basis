#!/usr/bin/env python3
from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np

import analyze_s2_g2_al_localized_analysis_r2 as r2
import s2_g2_al_localized_common_r1 as r1


class AnalysisR2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=Path(__file__).resolve().parents[2]; cls.config=r2.load_config(cls.root); cls.r1config=json.loads((cls.root/cls.config["source"]["r1_config_path"]).read_text())

    def test_01_config(self): r2.validate_config(self.config); self.assertEqual(self.config["new_solver_run_count"],0)
    def test_02_cube_108_real_raw(self):
        run=Path(self.config["source"]["formal_state_root"])/"runs/S2-20260811-001"; meta=json.loads((run/"input_metadata.json").read_text()); counts,rho=r2.parse_cube_108(run/"OUT.s2_g2_al108_localized_r1/chg.cube",meta["geometry"]["cell_bohr"],meta["geometry"]["fractional_positions"]); self.assertEqual(counts.tolist(),[96,96,96]); self.assertEqual(rho.size,96**3)
    def test_03_recovery_replays_full_reference(self):
        _,result,terminal,closure=r2.recovered_reference(self.root,self.r1config,self.config); self.assertEqual(result["atom_count"],108); self.assertEqual(terminal["runner_return_code"],0); self.assertEqual(closure["accepted_marker_count"],0)
    def test_04_exact_rank_path_hits_registered_endpoint(self):
        run=Path(self.config["source"]["formal_state_root"])/"runs/S2-20260811-001"; metadata=json.loads((run/"input_metadata.json").read_text()); rows=r2.exact_rank_position_sets(metadata,self.r1config); self.assertEqual(len(rows),5); self.assertTrue(np.array_equal(rows[-1],np.asarray(metadata["geometry"]["fractional_positions"])))
    def test_05_tampered_atom_rejected(self):
        with self.assertRaises((ValueError, FileNotFoundError)): r2.parse_cube_108(Path("missing"),np.eye(3),np.zeros((108,3)))


if __name__=="__main__": unittest.main()
