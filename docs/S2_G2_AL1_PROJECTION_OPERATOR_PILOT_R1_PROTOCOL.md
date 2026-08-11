# S2/G2 Al one-atom projection and operator pilot R1

Status: `implementation_pending_preregistration`

This is an analysis-only pilot over the committed Al KS-NL equilibrium density. It starts no ABACUS, DFTpy optimizer, or other self-consistent solver. The development probe used to debug the deterministic kernel is not formal evidence; thresholds remain those inherited from the S2 architecture registration.

## Frozen comparison

The four registered routes are evaluated on the same 24x24x24 grid and exact primitive cell:

1. the unmodified PW/FFT density reference;
2. six periodic minimum-image s Gaussian functions with an exact electron-number KKT constraint;
3. zero-average atomic functions plus a unique constant channel and all real low-G modes with `q/(2 kF) <= 1`;
4. the same low-G block with the atomic block projected into its orthogonal complement.

The explicit and complementary routes span the same pilot space. Their density and energy should therefore agree; their conditioning is expected to differ. This is a gauge/conditioning test, not two independent accuracy samples.

## Operators

Every reconstructed density is evaluated without optimization using DFTpy 2.2.0: Hartree, the local component of the exact registered `Al_std.upf`, PBE XC, and fixed WT KEDF (`alpha=beta=5/6`). Ion-ion energy is constant and excluded from representation errors.

## Gates and disposition

Electron number, density L2, non-negativity, normalized overlap condition number, three component errors, their combined error, and fixed-WT error are evaluated independently. The evidence is valid whether a candidate passes or none passes. Failure of this first basis/low-G ladder does not reject G2; it requires a new revision and forbids tuning the registered R1 output in place.

## Git and evidence topology

The implementation is a direct child of main handoff `0c0cf82b6b051e4f0dbf421911569ef5f8298afd`. Preregistration modifies only the config registration fields. The formal evidence commit may add exactly five files beneath `analysis/s2/g2_al1_projection_operator_pilot_r1_20260811/`; committed validation reconstructs them byte-for-byte with the frozen runtime.
