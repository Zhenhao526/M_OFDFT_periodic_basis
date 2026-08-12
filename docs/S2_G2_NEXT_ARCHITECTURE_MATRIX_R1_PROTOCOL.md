# S2/G2 next architecture candidate matrix R1

This revision returns S2 to the registered four-route competition after the 23-function `r08_eta100` periodic space was eliminated. It preregisters the next matrix only; it starts no solver and performs no scientific acceptance run.

The PW/FFT route remains the noncompressive reference. The atomic/FFT route re-enters with new eight- and ten-radial ladders and must first pass the original one-atom gates. The explicit low-G route retains the historically accepted `r04_eta160` and `r06_eta200` members. The complementary route retains `r10_eta130`, `r04_eta160`, and `r06_eta200`. The eliminated `r08_eta100` explicit and complementary gauges span the same failed 23-function space and cannot re-enter by changing gauge.

Promoted candidates use the existing independent localized 108-atom KS-NL reference, five geometries, 128³/144³ grids, and sixteen translation phases. All electron, density, component-energy, fixed-WT, full-rank, condition, retained-margin, 15-degree fixed-subspace, eggbox, pseudoforce, and cross-grid gates remain unchanged. G2c, S3, Mg, per-geometry rank selection, and gate relaxation remain disabled.

After all G2a/G2b gates, the compressed winner is selected by projected 108-atom basis size, then condition number, then lexical ID. This ordering does not authorize G2c; performance is a later gate.
