# S1 G1 Al acceptance policy R1

## Purpose

This is an analysis-only policy revision authorized on 2026-08-11. It changes the Al KS-L versus KS-NL equilibrium-volume acceptance limit from 0.5% to 1.0%. It does not run a solver, modify an external state tree, or rewrite any historical result.

## Immutable sources

- R3 final evidence `73b358926dc4e1a576209b3e58c4e86f25ffe9ec` remains `evidence_valid_scientific_gate_rejected` under its preregistered 0.5% limit.
- R5 final evidence `54019437539808c91f6c2768e3afcae08ea50439` remains the accepted 8/8 strain and endpoint replay.
- The integration base is the exact two-parent merge of those commits. Both sources are replayed from committed bytes before applying this new policy.

## Revised hard gate

The only changed hard limit is `|delta V0| <= 1.0%`. The bulk-modulus, BM3 residual, anchored-curve, strain, endpoint k-point, cutoff, and pressure limits are unchanged. New solver runs are forbidden and must remain zero.

## Accepted scope

Passing this revision closes G1 item 6 only for the Al ABACUS same-engine registered PP-pair scheme-plus-construction suitability bound. It does not close D-026 or a second independent KS/QE implementation. Mg remains diagnostic-only. OF-L versus KS-L remains an accepted error portrait rather than a physical-accuracy claim. The failed HQLPP/QE binding smoke is preserved as no-retry, contributes zero, and does not affect this disposition. Projector-only causality and G4 force/stress remain open.

## Immutable Git topology

`integration base -> implementation -> config-only preregistration -> four-file output-only evidence`.

The committed validator must reconstruct all four outputs byte-for-byte and return `accepted_g1_6_of_6`, `6/6`, and `new_solver_run_count=0`.
