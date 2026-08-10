# S1 G1 displacement/strain reference analysis R2

Analysis-only R2 accepted 15/15 frozen R1 finite-temperature KS calculations and 7/7 signed pairs; no solver ID was rerun. All runs include complete thermodynamic labels, forces, stress, 17-digit density and potential cubes, and an independently integrated electron-number check. Central differences are diagnostics only and are not a G4 acceptance result.

R1 analysis was rejected as a false negative because S1-20260810-203's cube legally wrapped an atom across periodic boundaries. R2 reduces cube/STRU atom-coordinate differences by the lattice minimum image while retaining the absolute lattice-axis gate.

Runtime note: these runs use the frozen old-prefix ABACUS binary `2d68a57c...`, not the R4 relocated binary `438c74b9...`; they are therefore not runtime-byte-identical to R4. The prior six-point old→relocated closure in `analysis/s1/runtime_relocation_equivalence_20260805/summary.json` (commit `a01ac707e8e4d2604ea01a947d9c32738aa264df`) was storage-exact at all six points and supports scientific equivalence only.
