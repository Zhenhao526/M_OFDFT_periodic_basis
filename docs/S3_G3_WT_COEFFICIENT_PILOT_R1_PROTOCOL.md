# S3/G3 fixed WT coefficient-space pilot R1

This revision changes only the G2-to-S3 decision. The 23-function `r08_eta100_complementary` candidate is accepted as an Al density representation, while the committed G2c result remains an evidence-valid performance rejection. No acceleration or memory-saving claim is made. G2c is an independent engineering limitation and no longer blocks this fixed-WT variational pilot.

## Registered scientific scope

The pilot is limited to one Al atom at the registered V100 geometry and fixed WT KEDF (`alpha=beta=5/6`), PBE, Hartree and the exact local component of the registered `Al_std.upf`. It can accept only `accepted_s3_al_v100_fixed_WT_coefficient_pilot`; it cannot close G3, authorize S4, establish Mg transfer, or reverse the G2c performance rejection.

At every coefficient objective and gradient evaluation, the 23 coefficients reconstruct the complete real-space density. The same DFTpy Hartree, local pseudopotential, PBE XC and WT paths then evaluate that density on the full grid, including FFTs. No coefficient-space surrogate, precomputed energy quadratic form, fixed potential or tiled density is allowed. The fresh full-grid reference uses DFTpy's `sqrt(rho)` optimization with the identical functional, pseudopotential, cell and grid.

The constant density fixes three electrons. The other 22 columns are frozen charge-neutral tangent functions, orthonormal under the voxel-weighted L2 metric. For a coefficient gradient `g=B^T W v`, the reported invariant stationarity metric is `||g||_2/sqrt(V)` in Hartree. Ten fixed-seed charge-neutral central finite differences at the uniform interior point must agree with the analytic gradient to relative error below `1e-5`.

The two routes receive byte-identical feasible physical initial densities: uniform, the closest nonnegative charge-conserving projection of the exact-pseudopotential atomic superposition, and the corresponding projection of a frozen 5% lowest-nonzero-G perturbation. Density clipping is forbidden. The negative-density fraction is the integrated negative electron count divided by three, not the fraction of grid points. Accepted coefficient steps must be energy-monotone within `1e-10 Ha`.

## Registered 29-case matrix

Three full-grid uniform runs at `24^3`, `32^3` and `40^3` establish the formal grid. The core matrix uses `40^3`, volume ratios `0.995`, `1.000`, `1.005`, both routes and all three initial densities (18 runs). Four additional volumes per route (`0.9975`, `1.0025`, `0.990`, `1.010`) complete central-difference pressure platforms at volume deltas `0.0025`, `0.005`, `0.01` (8 runs). There are 29 fresh IDs, `S3-20260812-001` through `S3-20260812-029`, in one fresh state.

Pressure is not taken from the coefficient-density stress tensor because that omits the representation Pulay term. For each route it is computed from independently self-consistent total energies,

`P(delta) = -[E(V0(1+delta))-E(V0(1-delta))]/[2 delta V0]`,

using the registered Hartree/bohr3-to-GPa factor. The direct DFTpy stress is retained only as a diagnostic and for the 32-to-40 grid check.

## Hard gates and evidence disposition

Hard gates are: electron-number absolute error `<1e-10`; integrated negative-density fraction `<1e-8`; no negative final grid value; invariant stationarity metric `<1e-6 Ha` for both the coefficient solution and the full-grid Euler equation; three-initialization energy spread `<1 meV/atom` independently at every core volume and route; absolute coefficient/full-grid energy difference `<10 meV/atom`; coefficient energy not below the full-grid variational reference by more than `0.1 meV/atom`; density relative L2 `<1.5%`; and coefficient/full-grid finite-difference pressure difference `<0.2 GPa` at all three deltas. Each route's three pressure estimates must have pairwise spread `<0.02 GPa`. The 32-to-40 full-grid change must be `<1 meV/atom` in energy and `<0.02 GPa` in diagnostic pressure. All formal attempts must have zero retry.

Every formal attempt reconstructs the full grid and calls the common FFT functional. A solver or orchestration failure writes an immutable `failed_no_retry` terminal and preserves attempted, accepted and unattempted IDs. Scientific rejection after operational completion is an evidence-valid closure. Acceptance keeps `g3_overall_closed=false` and `s4_authorized=false`; the next task is a separately registered Al/Mg multistructure expansion.

Git topology is base -> implementation -> config-only preregistration -> output-only evidence. The runner uses exclusive state creation and immutable per-run/terminal inventories. Independent audit of the exact preregistration commit is required before the formal state may be created.
