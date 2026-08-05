# S1 plane-wave baseline protocol

Status: `candidate_not_converged`. Values in the configuration are starting
points for convergence scans, not accepted production parameters.

## Scope and structures

- Core systems: one-atom primitive fcc Al and two-atom primitive hcp Mg.
- Initial lattice constants: Al `a0 = 4.05 A`; Mg `a0 = 3.2094 A`,
  `c0 = 5.2108 A`.
- EOS ratios: `0.90, 0.94, 0.97, 1.00, 1.03, 1.06, 1.10 V0`.
- Each cell vector is multiplied by `volume_ratio^(1/3)`. Mg keeps the initial
  `c/a` during this first isotropic EOS; axial relaxation is a separately named
  experiment and cannot replace the seven-point set.
- OFDFT and KSDFT use the same local pseudopotential and XC for each material:
  Al BLPS/PBE and Mg BLPS/LDA-PZ. Comparisons across different pseudopotentials
  are not accepted as OFDFT-versus-KSDFT errors.

## Candidate convergence matrix

| System | Solver | Cutoff scan (Ry) | k-point scan | Smearing scan (Ry) |
|---|---|---|---|---|
| Al | WT-OFDFT | 20, 30, 40, 60 | Gamma | n/a |
| Mg | WT-OFDFT | 30, 40, 60, 80 | Gamma | n/a |
| Al | KSDFT | 40, 60, 80 | 12³, 16³, 20³ | 0.00734986, 0.00367493 |
| Mg | KSDFT | 40, 60, 80 | 12x12x8, 16x16x10, 20x20x12 | 0.00734986, 0.00367493 |

For each scan, vary one axis at a time at `V/V0 = 1.00`. A setting is accepted
only when the next denser setting changes relative energy by `<1 meV/atom` and
pressure by `<0.02 GPa` for OFDFT, and the next k mesh changes KSDFT relative
energy by `<2 meV/atom`. Smearing acceptance follows G1: halving sigma changes
equilibrium volume by `<0.2%` and relative energy by `<2 meV/atom`.

## Fixed numerical conventions

- ABACUS `v3.11.0-beta.5`, plane-wave basis, symmetry disabled.
- WT parameters: `alpha = beta = 5/6`, `rho0 = 0`, `of_method = tn`,
  `of_conv = both`, `of_tole = 1e-7`, `of_tolp = 1e-6`.
- KSDFT: `scf_thr = 1e-10`, `scf_nmax = 200`, CG eigensolver, Fermi-Dirac
  smearing, Broyden mixing with beta `0.4`.
- `ecutrho = 4 * ecutwfc` in every candidate.
- Stored units and energy references follow `docs/S0_REPRODUCIBILITY_PROTOCOL.md`.
- Every generated job records the configuration SHA-256, pseudopotential
  SHA-256, exact cell, volume, atom count, solver, cutoff, k mesh, smearing,
  code commit, and run status.

## Execution order and gates

1. Generate and checksum all candidate inputs.
2. Run Al WT cutoff scan at `V0`; parse energy, pressure, electron number, and
   convergence state.
3. Run Mg WT cutoff scan.
4. Run Al and Mg KS cutoff/k-point/smearing scans.
5. Freeze accepted settings in a new configuration revision before EOS runs.
6. Run the fourteen EOS points, then fit and validate metadata completeness.
7. Cross-check one Al and one Mg point with an independent program or
   implementation. No such second program is currently installed on node01;
   this is a recorded G1 dependency, not silently waived.

Generated inputs are candidates until the convergence results are committed.
S2 must not start before every G1 acceptance item has evidence.

## Protocol revisions

- `S1-R1`, 2026-08-05: the initial 60 Ry Al WT run (`S1-20260805-004`)
  reached the 200-iteration limit with a stationary potential norm
  `1.9681e-7`, while energy and pressure were stable. The initial candidate
  tolerances (`1e-8`, `1e-7`) were stricter than the audited S0/legacy WT
  settings and produced a numerical plateau. Before any retry, tolerances were
  changed to `of_tole=1e-7`, `of_tolp=1e-6`; G1 energy and pressure acceptance
  thresholds were not changed. The failed run remains in the experiment ledger.
