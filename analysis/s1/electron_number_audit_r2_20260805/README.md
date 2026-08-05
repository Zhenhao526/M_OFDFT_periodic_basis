# S1 G1 incremental electron-number audit R2

- Status: `accepted`
- Coverage: `90/90`
- OF evidence split: R1 reused `11/11` + R2 executed `19/19`
- Accepted points: `90/90`
- KMP rank lifecycles: `120/120`
- Successful KMP lifecycle syscalls: `360/360`
- Maximum certified relative error: `1.0127696865884852e-11` at `S1-20260805-044`
- Strict per-point limit: `<1e-10`
- OF scientific-equivalence failures: `0`

The archived failed R1 attempt for `S1-20260805-130` is retained only as
root-cause provenance and contributes zero points to the 90-point acceptance
denominator. The accepted current R2 run for that registered ID is counted
exactly once.

KS densities are independently integrated from the reciprocal-space `G=0`
coefficient. OF densities are independently integrated from `out_chg 1 17`
cube values using the `STRU` cell volume. All 30 accepted OF runs also satisfy
the raw create/read/unlink KMP lifecycle contract for four ranks.

This report closes only the G1 electron-number item when accepted. The other
five G1 items and the complete G1 gate remain pending.
