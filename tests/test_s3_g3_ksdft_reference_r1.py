#!/usr/bin/env python3
from __future__ import annotations
import sys,unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"scripts"))
import s3_g3_ksdft_reference_common_r1 as c

def config(): return c.load(ROOT)
def rows():
    ks=[];cand=[]
    for v,ke,ce in ((.995,-10.0,-100.0),(1.0,-10.1,-100.1),(1.005,-10.2,-100.2)):
        ks.append({"volume_ratio":v,"pressure_gpa":0.0,"thermodynamic_labels_ev_per_atom":{"E_ec":ke}});cand.append({"volume_ratio":v,"energy_ev_per_atom":ce,"energy_hartree":ce/27.211386245988,"volume_bohr3":112.0})
    return ks,cand
class TestReference(unittest.TestCase):
 def test_01_matrix_and_scope(self): c.validate_config(config())
 def test_02_fourier_constant_conserved(self):
    x=np.full((4,4,4),.25);y=c.fourier_resample(x,7);self.assertEqual(y.shape,(7,7,7));self.assertLess(np.max(np.abs(y-.25)),1e-14)
 def test_03_metrics_accept_and_density_fail(self):
    cfg=config();ks,cand=rows();kd={v:np.ones((4,4,4)) for v in (.995,1.0,1.005)};cd={v:np.ones((4,4,4))*1.001 for v in kd};hist={"pressure_gpa":0.0,"thermodynamic_labels_ev_per_atom":{"E_ec":-10.1}};m=c.scientific_metrics(cfg,ks,cand,kd,cd,hist);self.assertTrue(all(x["accepted"] for x in m["density_rows"]));self.assertTrue(all(x["accepted"] for x in m["anchored_energy_rows"]))
    cd[1.0]=np.ones((4,4,4))*2;self.assertFalse(c.scientific_metrics(cfg,ks,cand,kd,cd,hist)["scientific_gate_accepted"])
 def test_04_absolute_energy_offset_cancels(self):
    cfg=config();ks,cand=rows();kd={v:np.ones((2,2,2)) for v in (.995,1.0,1.005)};hist={"pressure_gpa":0.0,"thermodynamic_labels_ev_per_atom":{"E_ec":-10.1}};m=c.scientific_metrics(cfg,ks,cand,kd,kd,hist);self.assertTrue(all(x["absolute_difference_mev_per_atom"]<1e-8 for x in m["anchored_energy_rows"]))
 def test_05_source_contract(self): self.assertTrue(c.source_exact(ROOT,config()))
if __name__=="__main__":unittest.main()
