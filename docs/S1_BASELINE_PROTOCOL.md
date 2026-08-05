# S1 plane-wave baseline protocol

Status: `eos_candidate_parameters_frozen` at S1-R7. Values are frozen for the
pre-registered EOS matrix but are not final production parameters until
non-equilibrium convergence and G1 checks pass.

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
| Al | KSDFT | 40, 60, 80 | 12³, 16³, 20³, 24³, 28³ | 0.00734986, 0.00367493 |
| Mg | KSDFT | 40, 60, 80 | 12x12x8, 16x16x10, 20x20x12, 24x24x16 | 0.00734986, 0.00367493 |

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
6. Run fourteen unique structures (Al/Mg x seven volumes), with OFDFT,
   standard-sigma KSDFT, and half-sigma KSDFT at each structure: 42 calculations
   and six seven-point curves in total. Then fit and validate metadata.
7. Cross-check one Al and one Mg point with an independent program or
   implementation. No such second program is currently installed on node01;
   this is a recorded G1 dependency, not silently waived.

The pre-registered core matrix is `config/S1_eos_run_manifest.tsv`. Execute it
from a clean worktree with
`scripts/run_s1_manifest.sh config/S1_eos_run_manifest.tsv`; every point is
committed independently and the runner stops after preserving the first failed
point. After all 42 points converge, run `scripts/analyze_s1_eos.py` on the 42
run directories. The analyzer requires six complete seven-point BM3 fits,
`<1 meV/atom` maximum fit residual, and the strict double-sigma limits above.
Finite-smearing pressure is retained as a diagnostic because the primary KS
fit observable is the zero-temperature extrapolated energy.

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
- `S1-R2`, 2026-08-05: Al and Mg KSDFT cutoff scans both selected 40 Ry at
  `V/V0=1.00`. The candidate KS reference was therefore changed from 60/240 Ry
  to 40/160 Ry before generating k-point and smearing scans. This is a
  one-axis-at-a-time protocol update, not final G1 parameter freezing.
- The two-point smearing scan at `V/V0=1.00` is a convergence and absolute
  energy-shift diagnostic only. A single volume cannot establish either the
  relative EOS energy change or the required `<0.2%` equilibrium-volume
  change; full smearing acceptance remains pending until EOS fits exist at
  both sigmas.
- `S1-R3`, 2026-08-05: the planned Al 12³/16³/20³ k-point scan produced
  adjacent energy changes of 3.662659 and 4.432064 meV/atom, so no pair met
  the `<2 meV/atom` gate. The failed gate is retained in
  `analysis/s1/al_ksdft_kpoint_20260805/`; 24³ was added as the next denser
  confirmation point before any further calculation.
- `S1-R4`, 2026-08-05: Mg 12x12x8→16x16x10 passed at 0.225665 meV/atom,
  but 16x16x10→20x20x12 then failed at 2.035276 meV/atom. To prevent an
  isolated early pass from hiding later metallic k-point oscillation, a mesh
  is now recommended only if every subsequently sampled adjacent refinement
  also passes. A 24x24x16 confirmation mesh was added before analysis.
- `S1-R5`, 2026-08-05: tail-stable k-point analysis selected 20³ for Al and
  20x20x12 for Mg. These meshes replaced the initial 16³ and 16x16x10
  references before generating smearing inputs. G1 remains open because the
  smearing equilibrium-volume check and EOS are not complete.
- `S1-R6`, 2026-08-05: source and output audit established that
  `!FINAL_ETOT_IS` is the finite-smearing Helmholtz free energy, while ABACUS
  separately reports `E_KS(sigma->0)`. The parser now preserves free energy,
  `-TS`, internal energy, and the zero-temperature extrapolated energy. The
  latter is frozen as the primary 0 K EOS observable. Re-evaluating k-point
  data with this observable gives 2.024876 meV/atom for Al 20³→24³, just above
  the strict `<2` gate, so the prior Al recommendation is reopened and 28³ is
  added before EOS. Mg remains tail-stable at 20x20x12.
- `S1-R7`, 2026-08-05: Al 28³ (`S1-20260805-028`) converged and changed the
  zero-temperature extrapolated energy by 0.822250 meV/atom relative to 24³;
  Al therefore freezes at 24³ and Mg at 20x20x12 for EOS candidates. Candidate
  cutoffs are Al/Mg WT 20/30 Ry and Al/Mg KS 40 Ry. The EOS matrix contains 14
  OFDFT, 14 standard-sigma KSDFT, and 14 half-sigma KSDFT calculations. The
  primary KS observable is `E_KS(sigma->0)`; raw free energy, `-TS`, internal
  energy, and finite-smearing pressure remain audit fields. For each sigma,
  relative energies are anchored at `V/V0=1.00`; the maximum pointwise curve
  difference must be `<2 meV/atom`, and fitted equilibrium volumes must differ
  by `<0.2%`. This core EOS does not by itself close the later non-equilibrium
  cutoff/k-mesh, density-integral, or independent-program G1 checks.

## Completed convergence evidence

### Al WT cutoff at `V/V0 = 1.00`

| Cutoff (Ry) | Selected experiment | Converged | Delta to next (meV/atom) | Pressure delta to next (GPa) |
|---:|---|---|---:|---:|
| 20 | `S1-20260805-002` | yes | 0.011269 | 0.0000990 |
| 30 | `S1-20260805-001` | yes | 0.001121 | 0.0000202 |
| 40 | `S1-20260805-003` | yes | 0.000108 | 0.0000148 |
| 60 | `S1-20260805-005` | yes | — | — |

All adjacent comparisons are below `1 meV/atom` and `0.02 GPa`; 20 Ry is the
minimum passing candidate at the equilibrium point. `S1-20260805-004` is a
retained nonconverged 60 Ry attempt preceding S1-R1. The 20 Ry recommendation
remains candidate status until EOS and pressure behavior away from `V0` are
checked; no production parameter is silently frozen from a single point.

### Mg WT cutoff at `V/V0 = 1.00`

| Cutoff (Ry) | Experiment | Converged | Delta to next (meV/atom) | Pressure delta to next (GPa) |
|---:|---|---|---:|---:|
| 30 | `S1-20260805-006` | yes | 0.00000651 | 0.0000070 |
| 40 | `S1-20260805-007` | yes | 0.00000140 | 0.0000071 |
| 60 | `S1-20260805-008` | yes | 0.00000145 | 0.0000512 |
| 80 | `S1-20260805-009` | yes | — | — |

All four Mg attempts converged and every adjacent comparison passes. The
minimum scanned value, 30 Ry, is the V0 candidate. As for Al, this remains
provisional until non-equilibrium EOS points confirm relative-energy behavior.

### Al KSDFT cutoff at `V/V0 = 1.00`

| Cutoff (Ry) | Experiment | Converged | Delta to next (meV/atom) | Pressure delta to next (GPa) |
|---:|---|---|---:|---:|
| 40 | `S1-20260805-010` | yes | 0.125921 | 0.0064297 |
| 60 | `S1-20260805-011` | yes | 0.009981 | 0.0000603 |
| 80 | `S1-20260805-012` | yes | — | — |

All points converged. The minimum scanned value, 40 Ry, passes the adjacent
energy and pressure thresholds and is the reference for the Al k-point scan.

### Mg KSDFT cutoff at `V/V0 = 1.00`

| Cutoff (Ry) | Experiment | Converged | Delta to next (meV/atom) | Pressure delta to next (GPa) |
|---:|---|---|---:|---:|
| 40 | `S1-20260805-013` | yes | 0.001405 | 0.0000296 |
| 60 | `S1-20260805-014` | yes | 0.000050 | 0.0000014 |
| 80 | `S1-20260805-015` | yes | — | — |

All points converged. The minimum scanned value, 40 Ry, passes the adjacent
energy and pressure thresholds and is the reference for the Mg k-point scan.

### Al KSDFT k-point scan at `V/V0 = 1.00`

| Mesh | Experiment | Delta to next (meV/atom) | Tail stable |
|---|---|---:|---|
| 12x12x12 | `S1-20260805-016` | 3.393612 | no |
| 16x16x16 | `S1-20260805-017` | 5.082681 | no |
| 20x20x20 | `S1-20260805-018` | 2.024876 | no |
| 24x24x24 | `S1-20260805-019` | 0.822250 | yes |
| 28x28x28 | `S1-20260805-028` | — | confirmation |

The original three-mesh plan failed. S1-R3 initially accepted 20x20x20 using
the finite-smearing free energy. S1-R6 freezes the zero-temperature
extrapolated energy for 0 K work; under that observable 20³→24³ narrowly fails.
The S1-R7 28³ confirmation passes, so 24³ is the first tail-stable Al mesh.

### Mg KSDFT k-point scan at `V/V0 = 1.00`

| Mesh | Experiment | Delta to next (meV/atom) | Tail stable |
|---|---|---:|---|
| 12x12x8 | `S1-20260805-020` | 0.813281 | no |
| 16x16x10 | `S1-20260805-021` | 3.174226 | no |
| 20x20x12 | `S1-20260805-022` | 0.101674 | yes |
| 24x24x16 | `S1-20260805-023` | — | confirmation |

The isolated first-pair pass is rejected by S1-R4. The accepted V0 reference
for the Mg smearing scan is 20x20x12.

### KSDFT smearing diagnostics at `V/V0 = 1.00`

| Material | Standard sigma experiment | Half sigma experiment | Zero-T shift (meV/atom) | Free-energy shift (meV/atom) | Diagnostic complete | G1 accepted |
|---|---|---|---:|---:|---|---|
| Al | `S1-20260805-024` | `S1-20260805-025` | 0.000889 | 4.634969 | yes | no, EOS pending |
| Mg | `S1-20260805-026` | `S1-20260805-027` | 0.082773 | 4.824088 | yes | no, EOS pending |

All four calculations converged and reported the nominal electron count. The
free-energy and zero-temperature absolute shifts above are deliberately not
compared with the `<2 meV/atom` relative-energy gate: the gate applies after
subtracting the common `V/V0=1.00` reference within each sigma series. Both
sigma values must therefore be carried into the EOS step for final acceptance.
