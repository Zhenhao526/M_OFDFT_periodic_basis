# S2/G2 Al localized dense-grid R1 protocol

This analysis-only revision reuses the immutable 108-atom KS-NL reference from
`S2-20260811-001`. It starts no electronic-structure solver. The registered
96-cubed rejection and the post-hoc 128-cubed diagnostic remain unchanged.

Before execution, the revision freezes two target real-space grids, 128 cubed
and 144 cubed, sixteen sub-grid translation phases, the original 23-function
candidate, all five localized-displacement geometries, and every acceptance
threshold. The reference density is periodically Fourier-resampled from the
registered 96-cubed cube. The candidate projection, Gram matrix, eigenspectrum,
rank, density fit, and operator energies are rebuilt independently on each
target grid; the 96-cubed selected density is not reused.

The former exact-rank requirement is replaced prospectively, not
retrospectively, by a stable numerical-subspace contract: total effective rank
must be at least 2171 of 2173, deficiency at most two, pathwise rank span at
most one, condition number below 1e8, and the smallest retained eigenvalue at
least 100 cutoff units above the production threshold. The bottom-two
normalized-Gram eigenspaces are compared by principal angles along the five
geometry points and between the two grids. Adjacent-geometry and cross-grid
maximum angles are fixed at 15 and 5 degrees respectively.

For each grid, reference, selected, and selected-minus-reference translation
energies are evaluated at sixteen phases. The reference and excess maximum
pseudoforces must each be no more than 0.002 eV/Angstrom; selected absolute
pseudoforce must be no more than 0.004 eV/Angstrom. Density, electron number,
component energies, fixed-WT total energy, and eggbox energy retain their prior
limits. Cross-grid force and energy-amplitude changes are separately bounded.

Git topology is base -> implementation -> config-only preregistration ->
output-only evidence. Execution is single-shot and refuses an existing output
or external state root. A scientific rejection is valid evidence and must not
be retried or converted into acceptance by changing this revision.

The first preregistration `05ac59d207f019b3ad5a186f3805c3ef142e4bfc`
was superseded before execution because two unrelated ABACUS jobs began using
its registered CPUs 60--67. It created no state and no analysis output. This
replacement keeps every scientific byte and threshold unchanged and registers
four otherwise idle physical cores through their complete SMT sibling sets:
36/112, 37/113, 74/150, and 75/151.
