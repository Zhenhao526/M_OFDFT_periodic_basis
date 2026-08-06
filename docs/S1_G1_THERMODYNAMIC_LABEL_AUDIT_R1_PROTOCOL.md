# S1 G1 third-smearing / dense-k thermodynamic-label audit R1 protocol

Status: `protocol_frozen` (execution state is authoritative in the committed
configuration and analysis)
Protocol date: `2026-08-06`
Scope: close only the G1 third-smearing / dense-k thermodynamic-label item
Forbidden inference: this protocol does not define an exact zero-temperature
density, potential, or kinetic-functional derivative.

## 1. Scientific question and decision boundary

The accepted S1-R7 and S1-R8 calculations establish two-smearing EOS and
standard-smearing k-point convergence, but they do not establish that a scalar
entropy correction, a finite-smearing density, and a finite-smearing potential
belong to one thermodynamic label convention. This audit therefore asks:

1. do the Al and Mg entropy-corrected EOS curves remain stable upon the two
   successive refinements `sigma -> sigma/2 -> sigma/4` on one common dense
   k mesh;
2. is that common dense mesh still adequate at `sigma/4` at compressed,
   reference, and expanded volumes;
3. do the density and the fixed-electron-number projected noninteracting
   free-energy derivative converge from `sigma/2` to `sigma/4`; and
4. can every retained scalar and field label be assigned to an explicit
   finite-temperature Mermin functional without calling an extrapolated scalar
   an exact zero-temperature label.

Acceptance closes this one G1 item only. Overall G1 changes from `1/6` to
`2/6`; S2 and ML remain blocked by the other four G1 items. A scalar-energy
pass cannot compensate for a failed or missing density/derivative gate.

## 2. Immutable references and common numerical setting

### 2.1 Structures and volume points

- Al: one-atom fcc primitive cell, initial conventional `a0 = 4.05 A`,
  `V0 = 16.60753125 A^3/atom`.
- Mg: two-atom hcp primitive cell, `a0 = 3.2094 A`, `c0 = 5.2108 A`, fixed
  initial `c/a`, `V0 = 23.240889031550665 A^3/atom`.
- Volume ratios, in fixed order: `0.90, 0.94, 0.97, 1.00, 1.03, 1.06, 1.10`.
- A cell vector is scaled by `volume_ratio^(1/3)` exactly as in S1-R7/R8.
  No relaxation, axial optimization, displacement, or strain may replace a
  registered structure.

### 2.2 Physics and numerical controls

- Code: the same accepted ABACUS `v3.11.0-beta.5` build and registered
  relocated/private-namespace runtime used by the accepted S1 G1
  electron-number R2 audit.
- Basis and solver: plane-wave KSDFT, `symmetry = 0`.
- Pseudopotentials and XC:
  - Al: `al.gga.psp`, BLPS local pseudopotential, PBE;
  - Mg: `mg.lda.lps`, BLPS local pseudopotential, LDA-PZ.
- Cutoffs: `ecutwfc = 40 Ry`, `ecutrho = 160 Ry`.
- SCF: `scf_thr = 1e-10`, `scf_nmax = 200`, CG eigensolver, Fermi-Dirac
  smearing, Broyden mixing with beta `0.4`.
- Common dense k meshes:
  - Al: `28 x 28 x 28`;
  - Mg: `24 x 24 x 16`.
- Extra-dense `sigma/4` k meshes:
  - Al: `32 x 32 x 32`;
  - Mg: `28 x 28 x 18`.
- Smearing values:
  - standard `sigma = 0.00734986 Ry`;
  - half `sigma/2 = 0.00367493 Ry`;
  - quarter `sigma/4 = 0.001837465 Ry`.

The exact ABACUS, MPI launcher, OpenMP runtime, pseudopotential, configuration,
manifest, and generated-input SHA-256 values must be frozen in committed
configuration and manifest files before the first registered run. A path or
hash change requires a new protocol revision.

### 2.3 Reused dense-standard scalar sources

The standard-smearing common-dense EOS scalar series is reused without
rerunning. Its immutable sources are:

| Material | Ratio order | Source experiment IDs |
|---|---|---|
| Al | 0.90, 0.94, 0.97, 1.00, 1.03, 1.06, 1.10 | `S1-20260805-085`--`S1-20260805-091` |
| Mg | 0.90, 0.94, 0.97, 1.00, 1.03, 1.06, 1.10 | `S1-20260805-106`--`S1-20260805-112` |

The source logs already contain the scalar thermodynamic components required
below. Their historical metadata key `zero_temp_extrapolated_energy` is an
immutable legacy spelling. The R1 analyzer must map it semantically to
`entropy_corrected_estimator`; it must not rewrite the source runs or describe
the value as exact zero-temperature energy.

Only six dense-standard field replays are required because fields enter the
standard-to-half comparison only as a three-anchor diagnostic. All hard field
acceptance uses the newly generated half-to-quarter seven-point pairs and the
quarter-to-extra-dense three-anchor pairs. Replaying the other eight standard
points would add no hard comparison, no fitted scalar information, and no new
bracketing state; their scalar data and full logs are already present in the
immutable source series.

## 3. Thermodynamic and field-label semantics

For a converged KS run, define the following quantities from the final ABACUS
energy table. All scalar energies are retained both per cell and per atom.

```text
F    = E_KohnSham = !FINAL_ETOT_IS
m    = E_entropy(-TS)                         (m <= 0)
U    = F - m
E_ec = F - m/2 = (F + U)/2                    (Fermi-Dirac only)
T_sU = E_one_elec - E_localpp                 (current local BLPS only)
F_s  = T_sU + m
mu   = E_Fermi
```

`E_ec` is the ABACUS entropy-corrected estimator. It is the primary scalar EOS
observable only for continuity with accepted S1-R7/R8. It is not an exact
zero-temperature energy and has no right to inherit a finite-smearing density
or potential as a supposed zero-temperature field.

For these fully local BLPS calculations, `out_pot 1 17` provides the final
local KS effective potential

```text
v_eff(r) = v_localPP(r) + v_H(r) + v_xc(r)
```

in Ry. With the final thermal density `rho(r)` from `out_chg 1 17`, the
thermodynamically consistent noninteracting label bundle is

```text
{rho_sigma(r), F_s^sigma, g_sigma(r)}
g_sigma(r) = P_N[mu_sigma - v_eff_sigma(r)]
P_N[f](r)  = f(r) - (1/Omega) integral_Omega f(r) dr
```

`P_N` removes the chemical-potential constant mode appropriate to
electron-number-conserving density variations. `g_sigma` is the projected
derivative of the finite-temperature noninteracting free energy `F_s`, not the
strict zero-temperature derivative of `T_s`. Because projection removes the
constant, `mu` must still be archived but cannot be used to bypass the gauge
projection.

If this audit is accepted, the authoritative field bundle is the common-dense
`sigma/4` finite-temperature Mermin bundle. The half-to-quarter differences are
its measured residual smearing uncertainty. Future analytic forces and finite
differences must be formulated for the same total Helmholtz free energy `F`.
Using `E_ec` instead requires a separately implemented and accepted consistent
derivative protocol.

## 4. Frozen 40-run matrix

Every new run uses four MPI ranks, `out_chg 1 17`, and `out_pot 1 17`.
Every run must produce a high-precision `chg.cube` and exactly one final
single-spin potential cube at the source-verified basename `pot.cube`. A
different or ambiguous potential basename is an output-capability failure and
requires a protocol revision; a validator must not choose silently between
multiple files.

### 4.1 Dense-standard field replays

| New ID | Material | Ratio | K mesh | Sigma (Ry) | Immutable source |
|---|---|---:|---|---:|---|
| `S1-20260806-001` | Al | 0.90 | 28x28x28 | 0.00734986 | `S1-20260805-085` |
| `S1-20260806-002` | Al | 1.00 | 28x28x28 | 0.00734986 | `S1-20260805-088` |
| `S1-20260806-003` | Al | 1.10 | 28x28x28 | 0.00734986 | `S1-20260805-091` |
| `S1-20260806-004` | Mg | 0.90 | 24x24x16 | 0.00734986 | `S1-20260805-106` |
| `S1-20260806-005` | Mg | 1.00 | 24x24x16 | 0.00734986 | `S1-20260805-109` |
| `S1-20260806-006` | Mg | 1.10 | 24x24x16 | 0.00734986 | `S1-20260805-112` |

### 4.2 Common-dense half-smearing EOS

| New IDs | Material | Ratios in ID order | K mesh | Sigma (Ry) |
|---|---|---|---|---:|
| `S1-20260806-007`--`013` | Al | 0.90, 0.94, 0.97, 1.00, 1.03, 1.06, 1.10 | 28x28x28 | 0.00367493 |
| `S1-20260806-014`--`020` | Mg | 0.90, 0.94, 0.97, 1.00, 1.03, 1.06, 1.10 | 24x24x16 | 0.00367493 |

### 4.3 Common-dense quarter-smearing EOS

| New IDs | Material | Ratios in ID order | K mesh | Sigma (Ry) |
|---|---|---|---|---:|
| `S1-20260806-021`--`027` | Al | 0.90, 0.94, 0.97, 1.00, 1.03, 1.06, 1.10 | 28x28x28 | 0.001837465 |
| `S1-20260806-028`--`034` | Mg | 0.90, 0.94, 0.97, 1.00, 1.03, 1.06, 1.10 | 24x24x16 | 0.001837465 |

### 4.4 Extra-dense quarter-smearing anchors

| New ID | Material | Ratio | K mesh | Sigma (Ry) | Common-dense partner |
|---|---|---:|---|---:|---|
| `S1-20260806-035` | Al | 0.90 | 32x32x32 | 0.001837465 | `S1-20260806-021` |
| `S1-20260806-036` | Al | 1.00 | 32x32x32 | 0.001837465 | `S1-20260806-024` |
| `S1-20260806-037` | Al | 1.10 | 32x32x32 | 0.001837465 | `S1-20260806-027` |
| `S1-20260806-038` | Mg | 0.90 | 28x28x18 | 0.001837465 | `S1-20260806-028` |
| `S1-20260806-039` | Mg | 1.00 | 28x28x18 | 0.001837465 | `S1-20260806-031` |
| `S1-20260806-040` | Mg | 1.10 | 28x28x18 | 0.001837465 | `S1-20260806-034` |

The two reference axes are orthogonal: the main EOS changes smearing at one
fixed common-dense mesh, while the three-anchor k gate changes only the k mesh
at fixed `sigma/4`. No comparison may mix changes in both axes.

## 5. Gated execution order

The committed manifest is authoritative for row hashes and exact execution
order, and its phases must implement the following barriers.

### Phase P0: four output and V0 k pilots

Run only, in the registered pilot order:

```text
S1-20260806-024  Al common-dense quarter, V0
S1-20260806-036  Al extra-dense quarter, V0
S1-20260806-031  Mg common-dense quarter, V0
S1-20260806-039  Mg extra-dense quarter, V0
```

All four must pass input/hash, SCF, density, potential, thermodynamic identity,
runtime, electron-number, V0 absolute-energy k, and V0 field k gates. The
remaining 36 runs are forbidden if any pilot is missing, indeterminate, or
failed.

### Phase P1: complete the three-anchor k gate

After all four P0 pilots pass, execute the other eight registered k-gate
objects in this exact order:

```text
S1-20260806-021  Al common-dense quarter, 0.90 V0
S1-20260806-035  Al extra-dense quarter, 0.90 V0
S1-20260806-027  Al common-dense quarter, 1.10 V0
S1-20260806-037  Al extra-dense quarter, 1.10 V0
S1-20260806-028  Mg common-dense quarter, 0.90 V0
S1-20260806-038  Mg extra-dense quarter, 0.90 V0
S1-20260806-034  Mg common-dense quarter, 1.10 V0
S1-20260806-040  Mg extra-dense quarter, 1.10 V0
```

Together with P0, these form the complete common-quarter versus extra-dense
comparison at `0.90, 1.00, 1.10 V0`. The remaining 28 runs are forbidden until
both material k gates pass all anchored-energy and field criteria.

### Phase P2: complete the main thermodynamic-label matrix

Only after P1 acceptance, execute:

- the six dense-standard field replays `001`--`006`;
- all fourteen dense-half points `007`--`020`; and
- the eight not-yet-run inner quarter points `022`, `023`, `025`, `026`,
  `029`, `030`, `032`, `033`.

Each accepted result is committed independently. The runner stops after
preserving the first failed attempt. It must not skip a failed row and continue
to improve the completion fraction.

## 6. Input, output, and provenance invariants

Before execution, a committed validator must prove for every new row:

1. the experiment ID, input directory, material, structure, ratio, series,
   k mesh, sigma, and source/partner IDs match Section 4 exactly;
2. structure and pseudopotential hashes match their immutable S1 source;
3. all non-axis physics and numerical settings match the frozen setting;
4. every new INPUT contains exactly one `out_chg 1 17` and exactly one
   `out_pot 1 17` control;
5. the six standard replays differ scientifically from their sources only in
   suffix and output controls;
6. the half/quarter pairs differ only in sigma;
7. the common-quarter/extra-dense pairs differ only in k mesh;
8. no run directory or failed-attempt archive already conflicts with a new ID;
9. configuration, manifest, generated INPUT, KPT, STRU, pseudopotential,
   executable, launcher, runtime library, and protocol hashes are recorded;
10. the worktree is clean except for explicitly registered user-owned
    untracked artifacts, which may not be staged, deleted, or treated as run
    output.

The preregistration commit must contain exactly the canonical configuration,
the canonical manifest, and the complete generated
`inputs/s1/g1_thermodynamic_label_audit_r1/` tree. The implementation files
are frozen in its parent commit. No run or failed-attempt path may be added in
the preregistration commit, and every registered input blob must remain
byte-identical to that commit throughout execution.

For every completed run, the committed validator must require:

- one final `running_scf.log`, `result.json`, `input_metadata.json`,
  `experiment_metadata.json`, `resource_usage.txt`, and input checksum list;
- one finite high-precision `chg.cube` and one finite high-precision
  `pot.cube` on the final FFT grid;
- cube cell, axes, grid dimensions, point counts, STRU, and raw log to agree;
- all expected scalar energy-table fields to be present and finite;
- the raw input and all recorded hashes to remain unchanged since
  preregistration; and
- the registered runtime/KMP contract to be accepted.

Cube integration uses the STRU cell volume as authority; rounded textual cube
axes may be cross-checked but may not replace the STRU volume. Potential values
are converted from Ry to eV before derivative metrics are evaluated.

## 7. Registered metrics and hard gates

Every inequality below is strict. Equality to a threshold fails.

### 7.1 Completion, convergence, thermodynamic identities, and electron number

- New runs: exactly `40/40` accepted registered runs.
- Main EOS: exactly `42/42` selected scalar points, consisting of 14 immutable
  dense-standard sources plus 28 new dense-half/quarter points.
- Every new run must contain `#SCF IS CONVERGED#` and no contradictory
  nonconvergence marker.
- For each new run and each reused dense-standard source, require the final
  values of `F`, `m`, `U`, `E_ec`, `E_one_elec`, `E_localpp`, `T_sU`, `F_s`,
  `E_Hartree`, `E_xc`, `E_Ewald`, and `mu` to be finite and complete.
- Require `m <= 0` and `F <= E_ec <= U` within the parser's declared numerical
  precision.
- Per-atom residuals of `U = F - m`, `E_ec = F - m/2`,
  `T_sU = E_one_elec - E_localpp`, `F_s = T_sU + m`, and the applicable total
  free-energy decomposition must each be `< 1e-8 eV/atom`.
- Every one of the 40 new high-precision density cubes must have independently
  integrated electron-number relative error `< 1e-10` against 3 electrons for
  one-atom Al and 4 electrons for two-atom Mg.

The magnitude and refinement ratios of `m`, and all finite-smearing pressures,
are mandatory diagnostics but are not additional hard smearing gates.

### 7.2 Six EOS fits and adjacent-smearing gates

For each material and each of `standard`, `half`, and `quarter`, fit a
seven-point BM3 curve using `E_ec` per atom. Each of the six fits requires:

- all seven unique registered ratios and exact structures;
- fitted equilibrium volume strictly inside the sampled interval;
- positive bulk modulus; and
- maximum absolute fit residual `< 1 meV/atom`.

For a series `s`, define the raw v100-anchored curve

```text
delta_E_s(r) = 1000 * [E_ec_s(r) - E_ec_s(1.00)]  meV/atom.
```

For each material, both adjacent refinements `standard -> half` and
`half -> quarter` independently require:

```text
max_r |delta_E_fine(r) - delta_E_coarse(r)| < 2 meV/atom
100 * |Veq_fine - Veq_coarse| / Veq_coarse < 0.2 percent.
```

Thus there are four independent material/refinement comparisons. Neither BM3
smoothing, RMS averaging, cross-material averaging, nor the standard-to-quarter
endpoint difference may replace the raw seven-point adjacent gates.

### 7.3 Dense-standard replay equivalence

Each of `001`--`006` is compared with its immutable source in Section 4.1.
All six require:

- identical structure, pseudopotential, XC, cutoffs, k mesh, sigma, and SCF
  controls, with only suffix and field-output controls changed;
- `|delta E_ec| < 0.1 meV/atom`;
- `|delta F| < 0.1 meV/atom`; and
- `|delta pressure| < 0.02 GPa`.

The replay field files are the standard-smearing inputs to the three-anchor
standard-to-half diagnostic. A replay failure cannot be replaced by reading a
field that the source run did not archive.

### 7.4 Field metric definitions and new pre-run thresholds

For two densities `rho_a` and the finer/reference density `rho_b` on the exact
same cell and FFT grid, define

```text
D1 = integral |rho_a-rho_b| dr / N
D2 = ||rho_a-rho_b||_2 / ||rho_b||_2
```

For their projected finite-temperature noninteracting free-energy derivatives,
define

```text
Dg     = ||g_a-g_b||_2 / ||g_b||_2
RMS_g  = sqrt[(1/Omega) integral |g_a-g_b|^2 dr].
```

Zero or non-finite denominators, grid mismatches, missing potential values, or
ambiguous gauges make the point indeterminate; they cannot be counted as zero
error.

Before any field metric is evaluated, every density and potential cube must be
bound to its registered `STRU`.  For each lattice direction, the cube axis
step multiplied by its grid count must reproduce every Cartesian component of
the `STRU` lattice vector; cube atom rows must reproduce the registered atomic
number, local-pseudopotential valence, ordering, and Cartesian position.  The
absolute comparison tolerance is frozen at `0.00005 bohr`, accommodating only
the six-decimal cube-header rounding.  A mismatch is an evidence/capability
failure, not a field-convergence pass.

The following field limits are new G1 thresholds frozen before any R1 run:

```text
D1    < 0.005       (0.5 percent)
D2    < 0.005       (0.5 percent)
Dg    < 0.01        (1 percent)
RMS_g < 0.005 eV    (5 meV)
```

They may not be weakened or reclassified as diagnostics after looking at R1
results. Any future threshold change requires a new protocol revision and new
experiment IDs.

### 7.5 Seven-point half-to-quarter field gate

For every registered volume of both materials, compare the common-dense half
run with its common-dense quarter partner:

| Material | Half IDs | Quarter IDs |
|---|---|---|
| Al | `007`--`013` | `021`--`027` |
| Mg | `014`--`020` | `028`--`034` |

All `14/14` pairs must pass all four field limits in Section 7.4. The
standard-to-half values at `0.90`, `1.00`, and `1.10 V0` are calculated from
`001`--`006` and the corresponding half runs, but are explicitly diagnostic;
they document the convergence trajectory and do not replace the hard finest
adjacent comparison.

### 7.6 Three-anchor low-smearing k gate

At `sigma/4`, compare common-dense and extra-dense results at `0.90`, `1.00`,
and `1.10 V0`:

| Material | Common IDs | Extra-dense IDs |
|---|---|---|
| Al | `021`, `024`, `027` | `035`, `036`, `037` |
| Mg | `028`, `031`, `034` | `038`, `039`, `040` |

The four P0 V0 runs first require, for each material,

```text
|E_ec_extra(1.00) - E_ec_common(1.00)| < 2 meV/atom,
```

and all four field limits from Section 7.4 at V0. This is an absolute-energy
pilot and is not mislabeled as an anchored curve comparison.

After P1 completes the endpoints, define for each k series

```text
delta_E_k(r) = 1000 * [E_ec_k(r) - E_ec_k(1.00)]  meV/atom.
```

Each material then requires

```text
max over r in {0.90,1.00,1.10}
|delta_E_extra(r) - delta_E_common(r)| < 2 meV/atom,
```

and all `6/6` common/extra point pairs must pass `D1`, `D2`, `Dg`, and `RMS_g`.
Pressure differences are retained as diagnostics. Three points do not define a
new EOS fit or an extra-dense equilibrium-volume claim.

### 7.7 Runtime/KMP aggregate gate

Every new run uses exactly four ranks and must satisfy the accepted raw
create/read/unlink KMP lifecycle contract used by the G1 electron-number R2
audit. Final aggregate counts must be exactly:

```text
40/40 runs with accepted KMP contract
160/160 rank lifecycles
480/480 successful lifecycle syscalls
```

Successful old-prefix access/execution/mapping, unknown probes, unexpected
mapped objects, unhashed accepted mappings, incomplete lifecycles, or duplicate
rank evidence must all be zero. A legal registered short-lived KMP object is
accepted only when its raw create/read/unlink lifecycle and expected pattern
are all proved; sampler absence is not evidence.

## 8. Attempt accounting, failure handling, and final decision

- No registered run directory or accepted source is edited in place.
- A failed formal attempt is preserved in a content-addressed archive with
  input, output, status, resource, runtime, and failure-class evidence before
  any revision or retry.
- R1 never retries a failed experiment ID. Any retry requires a new protocol
  revision and new experiment IDs; an older runtime helper's same-ID retry
  field is legacy metadata and has no authority in this audit.
- The runner stops at the first failed point or failed phase barrier. It does
  not continue past P0 or P1 to obtain a more favorable completion fraction.
- A capability or evidence failure, including missing/ambiguous `pot.cube`,
  produces `indeterminate/paused`; it is not a scientific numerical pass.
- A complete numerical result that reaches or exceeds an energy, volume,
  density, derivative, fit, electron-number, replay, or runtime threshold is a
  protocol rejection.
- A failed common-dense/extra-dense k gate means that the registered common
  mesh is not certified at `sigma/4`. Selecting the extra mesh for later points
  or adding a still denser mesh requires a new protocol revision and new IDs.
- No failed point may be deleted, omitted from the ledger, averaged away,
  replaced by a BM3 value, or silently rerun under the same ID.
- The analyzer must emit explicit failure-ID lists for completion, source
  integrity, input/hash, SCF, thermodynamic identity, electron number, EOS fit,
  adjacent-smearing energy, equilibrium volume, replay equivalence, density,
  derivative, k gate, and runtime/KMP categories.

The audit is `accepted` only if every required list is empty and every exact
denominator and aggregate count above matches. Acceptance authorizes only:

1. recording the common-dense `sigma/4` finite-temperature Mermin bundle as the
   current low-smearing reference with measured half-to-quarter uncertainty;
2. closing this one thermodynamic-label G1 item; and
3. updating overall G1 to `pending (2/6)`.

It does not authorize an exact 0 K claim, S2, ML, the second-OF cross-code
check, the KS-NL -> KS-L -> OF-L check, displacement/strain references, or the
ten-case single-command regeneration gate.
