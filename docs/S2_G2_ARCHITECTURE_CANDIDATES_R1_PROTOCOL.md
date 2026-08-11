# S2/G2 Architecture Candidates R1 Protocol

Status: `implementation_pending_preregistration`

This revision starts S2 without altering any accepted G1 evidence. It registers the architecture competition and the data contract only; it starts no solver, performs no coefficient optimization, and makes no G2 scientific claim.

## Scope

- Material: fcc Al only. Mg remains disabled until Al passes G2a and G2b.
- Cells: 1-atom primitive, 32-atom 2x2x2 conventional, and 108-atom 3x3x3 conventional cells. The 32/108 mappings are frozen as integer matrices acting on the registered primitive cell.
- Reference: the committed Al KS-NL equilibrium density from G1. Perfect supercell tiling is an invariance/performance diagnostic, not a substitute for the later 108-atom localized-perturbation reference.
- No ML and no self-consistent coefficient optimization are permitted in R1.

## Four-route competition

1. `pw_fft_reference`: the non-compressive registered grid/FFT baseline.
2. `atomic_fft`: atom-centered density coefficients, reconstructed on the same grid for long-range operators.
3. `atomic_low_g_explicit`: compensated zero-average atom functions plus unique G=0 and fixed low-G channels, including all metric cross blocks.
4. `atomic_low_g_complementary`: atom functions projected out of the registered low-G subspace, or an equivalent smooth range separation.

The explicit hybrid route is not the presumed winner. It is stopped if it is more than 20% slower than FFT and has no twofold peak-memory benefit at common accuracy.

## Gate order

### G2a — representation and operators

Register electron number, density L2, Hartree/external/XC component errors, fixed-KEDF non-SCF energy, Hartree symmetry/positive-semidefiniteness, direct-FFT equivalence, equivalent-supercell consistency, and signed low-q response.

### G2b — gauge, rank, and geometry continuity

Register a fixed basis/rank/G set, a 100x retained-spectrum safety gap, decade threshold scan, zero rank/pivot/G-set changes under displacement and +/-0.5% strain, and the egg-box energy/pseudoforce test.

### G2c — compression and performance

Only after G2a/G2b, compare 32/108/256-atom wall time and peak memory at common error. Representation compression alone is not a performance pass.

## R1 topology and no-run rule

The implementation commit is a direct child of G1/S1 handoff commit `751692ef878df5a80707ee6dd73dd03dd81b3a05`. A preregistration commit may change only the registration fields in the config. Until that preregistration validates, the external state and S2 analysis root must remain absent and all twelve case IDs have zero solver contribution.

## Failure boundary

Failure of an atomic route is retained as a result, not repaired by changing rank per geometry. If every atom-centered route fails accuracy, continuity, or compression, G2 closes as a reproducible negative result and the project turns to multiresolution grids or finite elements instead of entering ML.
