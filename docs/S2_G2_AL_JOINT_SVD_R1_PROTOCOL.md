# S2/G2 Al joint-SVD fixed-subspace pilot R1

This is a zero-new-solver, preregistered analysis revision. It addresses the dense-grid pilot's geometry-dependent one-to-two-dimensional rank loss without changing numerical gates.

Ten atomic-complement Gram matrices are rebuilt: five localized geometries on each of the registered 128³ and 144³ grids. A single diagonal scaling is computed from the mean diagonal of all ten matrices. Their commonly scaled matrices are averaged and diagonalized once. Two candidates are registered before execution: discard the bottom one or the bottom two joint modes. Each resulting transform is fixed across both grids and all geometries.

The selected candidate is the smallest discarded dimension that passes every gate. No per-geometry mode selection, post-hoc cutoff change, threshold relaxation, or retry is allowed. If neither candidate passes, the result is valid evidence with scientific rejection.

Every reduced Gram matrix must have full reduced rank at relative cutoffs 1e-8, 1e-9, and 1e-10, condition number below 1e8, and minimum retained eigenvalue at least 100 times the cutoff. The fixed discarded space must align with each local bottom-k space within 15 degrees. Density, component-energy, fixed-WT, electron-number, sixteen-phase eggbox/pseudoforce, and cross-grid gates are unchanged from the parent dense-grid protocol.

Git topology is base → five-file implementation → config-only preregistration → six-file evidence. Committed validation recomputes the full analysis and requires byte-identical outputs.
