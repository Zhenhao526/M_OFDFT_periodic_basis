# R1 analysis closure: rejected false negative

The R1 solver execution reached an accepted 15/15 terminal with no failed or retried ID. Its original analyzer cannot be finalized: S1-20260810-203 is represented by a legal periodic image in the ABACUS cube, while the R1 atom-coordinate check used raw Cartesian subtraction.

This rejection applies to the R1 analysis implementation, not to the frozen solver data. The external state remains read-only and no ID may be rerun. Analysis-only R2 replaces the atom comparison with a lattice minimum image and revalidates all 15 frozen runs.
