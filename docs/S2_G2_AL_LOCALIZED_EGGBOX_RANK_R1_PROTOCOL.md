# S2 G2 Al localized-reference, egg-box, and rank-continuity R1 protocol

This revision freezes the already selected `r08_eta100_complementary` candidate and creates one genuinely new 108-atom KS-NL reference.  The reference is a 3x3x3 conventional fcc Al supercell with one deterministically selected central atom displaced by +0.05 Angstrom along Cartesian x.  It is not a periodic tiling of the one-atom density.

The only solver ID is `S2-20260811-001`.  It runs ABACUS PBE/3e KS-NL at Gamma with 40/160 Ry, finite-difference smearing, 180 bands, forces, stress, and a charge-density cube.  The execution is pinned to node05 and sixteen registered physical cores.  A formal attempt is never retried under this revision.

After the independent reference is accepted, analysis is non-self-consistent.  The 108-atom complementary basis contains one charge channel, 1308 real frozen low-G functions, and 864 atom-centred radial functions: 2173 columns in total.  Periodic Gaussian overlaps and the low-G complement are evaluated in reciprocal space, so the full 108-atom Gram spectrum is computed without materialising a multi-terabyte dense grid-by-basis matrix.

Rank continuity uses five positions of the same local atom from -0.05 to +0.05 Angstrom.  The integer G set, basis dimension, retained rank, and retained pivot set are frozen.  Egg-box continuity translates the localized reference density and every ion together through eight phases of one grid interval by an exact Fourier phase.  The registered tests are energy peak-to-peak below 1 meV/atom and maximum cyclic central-difference pseudo-force below 0.002 eV/Angstrom.

The existing density, component-energy, fixed-WT, electron-number, condition-number, and coefficient-fraction gates are unchanged.  Passing this pilot closes only the Al localized-reference/egg-box/rank-continuity subcoverage.  Low-q response, Mg, G2c performance, and G2 overall remain open.

Git topology is base -> implementation -> config/input-only preregistration -> external one-attempt execution -> output-only evidence.  Raw state is immutable.  Final committed validation must replay the source chain, solver identity, cube integral, 108-atom geometry, full rank path, Fourier translation, operator energies, and every registered output byte.
