# S2/G2 Al one-atom basis convergence R2

Status: `implementation_pending_preregistration`

This analysis-only revision follows the committed negative pilot `269fe3a9`. It does not alter that evidence, relax any gate, start a solver, optimize a density, enable Mg, or advance to a larger cell.

## Frozen convergence matrix

The same committed Al KS-NL density, 24x24x24 grid, pseudopotential local component, and DFTpy operator stack are reused. Four nested even-tempered radial ladders (4, 6, 8, and 10 periodic s Gaussians) are crossed with six discrete low-G shells (`eta=q/(2kF)` cutoffs 1.0, 1.3, 1.5, 1.6, 1.8, and 2.0). Every point is evaluated in both the explicit and complementary gauges, giving 48 compressed candidates plus the PW/FFT reference.

The explicit and complementary members of a pair span the same registered space. Pairwise density and operator agreement is a hard mechanical check; only the complementary member is eligible for architecture selection because it removes the registered low-G block from the atomic block. The preferred candidate is the eligible passing member with the smallest basis, then the lowest normalized-overlap condition number, then lexical candidate ID.

## Unchanged gates

Electron number, density L2, positivity, condition number, Hartree, external, XC, combined three-operator error, and fixed WT KEDF error retain the R1 thresholds. In particular, the fixed-WT limit remains strictly below 10 meV/atom. The pre-registration development probe is excluded from formal evidence; formal output is rebuilt from the frozen source density.

## Disposition and next gate

Both a passing and a non-passing matrix are evidence-valid outcomes. A selected candidate closes only the Al one-atom representation/operator convergence substep. It must next be registered in a new revision for 32- and 108-atom geometry/continuity checks before G2c or S3 can begin.

## Git topology

The implementation is a direct child of `9c774e8c30142c1351f43cda7b61c145ff4964c1`. Preregistration may modify only the registration fields in the config. The evidence commit may add exactly the six registered files under `analysis/s2/g2_al1_basis_convergence_r2_20260811/`; committed validation reconstructs them byte-for-byte.
