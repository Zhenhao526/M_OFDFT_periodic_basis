# S2/G2 Al local-null-envelope diagnostic R1

This preregistered, zero-new-solver analysis determines the minimum dimension of a single fixed subspace that covers the local bottom-two normalized-Gram eigenspaces from 128³/144³ and five localized geometries.

The ten local two-dimensional spaces are concatenated. Their left singular vectors define a nested, geometry- and grid-independent envelope. Dimensions 2 through 20 are scanned without changing the registered 15-degree principal-angle gate. The selected dimension is the first one for which every local bottom-two space has maximum principal angle at most 15 degrees.

The decision is fixed before execution: a required dimension at most two permits a later fixed-subspace revision; a required dimension above two eliminates the current 23-function periodic expansion and returns S2/G2 to the four-route architecture competition. This diagnostic does not rerun density, energy, pseudoforce, or any KS solver and cannot relax earlier gates.

Git topology is base → five-file implementation → config-only preregistration → four-file evidence. Committed validation rebuilds all ten Grams and requires byte-identical outputs.
