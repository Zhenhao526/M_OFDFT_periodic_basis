import copy,json,unittest
from pathlib import Path
import numpy as np
import s3_g3_al_multistructure_common_r1 as c
ROOT=Path(__file__).resolve().parents[1]
class T(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.cfg=c.load(ROOT);c.validate_config(ROOT,cls.cfg)
 def test_01_denominator(self):self.assertEqual((len(self.cfg["formal_cases"]),self.cfg["formal_cases"][0]["experiment_id"],self.cfg["formal_cases"][-1]["experiment_id"]),(80,"S3-20260812-101","S3-20260812-180"))
 def test_02_geometry_determinants(self):
  for row in self.cfg["formal_cases"]:
   expected=row["geometry_parameter"] if row["geometry_kind"]=="isotropic_volume" else 1.0;self.assertAlmostEqual(np.linalg.det(c.geometry_transform(row)),expected,places=13)
 def test_03_summarize_acceptance(self):
  runs=[]
  for row in self.cfg["formal_cases"]:
   item={**c.row_identity(row),"status":"accepted","energy_ev_per_atom":-10.0+(0.015 if row["route"]=="coefficient_23_function" else 0.0),"electron_number_absolute_error":0.0,"negative_density_fraction":0.0,"minimum_density_electron_per_bohr3":0.0,"projected_gradient_metric_hartree":5e-7,"gradient_finite_difference":{"accepted":True},"accepted_steps_monotonic":True,"full_grid_euler_residual_hartree":5e-5}
   if row["route"]=="coefficient_23_function":item["density_relative_l2_vs_full_grid"]=0.003
   runs.append(item)
  s=c.summarize(self.cfg,runs);self.assertTrue(s["scientific_gate_accepted"]);self.assertEqual(len(s["coefficient_runs"]),60);self.assertFalse(s["g3_overall_closed"])
 def test_04_energy_gate_strict(self):
  runs=[]
  for row in self.cfg["formal_cases"]:
   item={**c.row_identity(row),"status":"accepted","energy_ev_per_atom":-10.0+(0.020001 if row["route"]=="coefficient_23_function" else 0.0),"electron_number_absolute_error":0.0,"negative_density_fraction":0.0,"minimum_density_electron_per_bohr3":0.0,"projected_gradient_metric_hartree":5e-7,"gradient_finite_difference":{"accepted":True},"accepted_steps_monotonic":True,"full_grid_euler_residual_hartree":0.0}
   if row["route"]=="coefficient_23_function":item["density_relative_l2_vs_full_grid"]=0.003
   runs.append(item)
  self.assertFalse(c.summarize(self.cfg,runs)["scientific_gate_accepted"])
 def test_05_other_gate_unchanged(self):
  bad=copy.deepcopy(self.cfg);bad["acceptance"]["projected_gradient_metric_hartree_strict_lt"]=2e-6
  with self.assertRaises(ValueError):c.validate_config(ROOT,bad)
 def test_06_scope(self):
  self.assertEqual(self.cfg["acceptance"]["full_grid_reference_energy_difference_abs_strict_lt_mev_per_atom"],20.0);self.assertFalse(self.cfg["scope"]["mg_enabled"])
 def test_07_fixed_basis_all_geometries(self):
  for row in self.cfg["formal_cases"][::4]:
   basis=c.build_basis(self.cfg,c.cell_for_row(ROOT,self.cfg,row),(40,40,40));self.assertEqual(basis["tangent"].shape,(64000,22));self.assertGreater(float(basis["raw_gram_eigenvalues"][0]),0.0)
 def test_08_worker_array_shape_interface(self):
  row=self.cfg["formal_cases"][-4];basis=c.build_basis(self.cfg,c.cell_for_row(ROOT,self.cfg,row),np.zeros((40,40,40)));self.assertEqual(tuple(basis["counts"]),(40,40,40))
if __name__=="__main__":unittest.main()
