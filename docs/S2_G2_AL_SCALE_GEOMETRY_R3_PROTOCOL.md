# S2/G2 Al 32/108-atom scale and geometry pilot R3

Status: `implementation_pending_preregistration`

This analysis-only revision freezes the selected one-atom candidate `r08_eta100_complementary`: eight even-tempered radial functions per atom, the unique constant channel, and all real low-G channels with `q/(2kF) <= 1`. No basis parameter, gate, rank cutoff, density optimizer, or solver is changed.

## Scientific boundary

The architecture registration defines the perfect 32- and 108-atom cells as periodic-tiling invariance diagnostics. They do not supply independent large-cell KS densities. Accordingly, this pilot recomputes the selected representation and four registered operators for the equilibrium primitive density and six frozen coordinate-scaled +/-0.5% deformation diagnostics, then proves exact periodic tiling into the registered 32/108 supercells using lattice cosets.

This can test deterministic geometry construction, fixed primitive rank, fixed integer low-G sets, electron-number scaling, per-atom operator extensivity, and coefficient-count scaling. It cannot validate a localized 108-atom perturbation, egg-box force, or the full large-cell Gram condition. Those limitations remain false in the formal summary and prevent G2 overall acceptance.

## Frozen geometries and gates

The seven cases are equilibrium, isotropic +/-0.5%, volume-preserving tetragonal +/-0.5%, and xy shear +/-0.5%. Reference values remain fixed in fractional coordinates and are scaled by `1/det(F)` to preserve three electrons. The selected complementary projection is rebuilt with the same eight exponents and the same seven primitive half-space G vectors; geometry-dependent rank or G-set changes are forbidden.

The original electron, density, positivity, condition, component-energy, combined-energy, fixed-WT, equivalent-supercell, and coefficient-fraction gates remain unchanged. The 32/108 integer G sets are frozen once from their equilibrium cells and reused byte-for-byte for every registered deformation; they are never reselected from the instantaneous `eta` values. Whether a hypothetical reselection would cross the cutoff is recorded as a diagnostic and cannot change columns or rank.

## Evidence topology

The implementation is a direct child of `4298896727256ef60a72fdf0ee6ae904dabdaafc`. Preregistration may modify only the config registration fields. Formal evidence may add exactly six files below `analysis/s2/g2_al_scale_geometry_r3_20260811/`, and committed validation reconstructs them byte-for-byte. A valid rejection is retained without retry or in-place tuning.
