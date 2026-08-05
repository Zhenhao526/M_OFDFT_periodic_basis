# S1-R7 core EOS acceptance

Status: `core_eos_status = accepted`; full `G1 = pending`.

## Scope and completeness

- Experiments: `S1-20260805-029` through `S1-20260805-070`.
- Matrix: two materials x seven volumes x (OFDFT, standard-sigma KSDFT,
  half-sigma KSDFT) = 42 calculations and six seven-point curves.
- Completeness: 42/42 selected, 42/42 SCF converged, zero failed attempts.
- KS fit observable: ABACUS `E_KS(sigma->0)`; OFDFT uses total energy.
- Fit: third-order Birch-Murnaghan in `eV/atom` and `angstrom^3/atom`.

An independent read-only audit rechecked every raw SCF marker, parsed energy
and pressure, all 168 archived input checksums, configuration/binary/potential
identity, and standard/half-sigma structure pairing. It reproduced the six
fits and both smearing comparisons below.

## BM3 fits

| Series | Veq (angstrom^3/atom) | B0 (GPa) | B0 prime | Maximum residual (meV/atom) |
|---|---:|---:|---:|---:|
| Al OFDFT | 16.769532 | 78.54084 | 4.73322 | 0.017078 |
| Al KS standard sigma | 16.577151 | 76.81301 | 4.86962 | 0.009400 |
| Al KS half sigma | 16.581735 | 76.77398 | 4.80230 | 0.009216 |
| Mg OFDFT, fixed c/a | 21.359027 | 37.10384 | 3.94245 | 0.002468 |
| Mg KS standard sigma, fixed c/a | 21.174945 | 38.66294 | 4.01146 | 0.003896 |
| Mg KS half sigma, fixed c/a | 21.181682 | 38.52501 | 3.98232 | 0.002822 |

All fitted equilibrium volumes lie strictly inside the sampled range, all
bulk moduli are positive, and every maximum residual is below the registered
`1 meV/atom` limit.

## Double-smearing acceptance

Each curve is independently anchored at `V/V0 = 1.00` before comparison.
The limits are strict: maximum pointwise relative-energy difference
`<2 meV/atom` and fitted-equilibrium-volume difference `<0.2%`.

| Material | Maximum curve difference (meV/atom) | Volume ratio | Veq difference | Result |
|---|---:|---:|---:|---|
| Al | 0.135259 | 0.90 | 0.027655% | accepted |
| Mg | 0.205258 | 0.90 | 0.031817% | accepted |

## OFDFT versus standard-sigma KSDFT baseline

These are reference diagnostics, not G1 acceptance gates. Energies are
independently anchored at `V/V0 = 1.00`; signed volume and bulk-modulus values
are `(OFDFT / KSDFT - 1) * 100`.

| Material | Maximum relative-curve difference (meV/atom) | Veq signed difference | B0 signed difference |
|---|---:|---:|---:|
| Al | 13.064922 | +1.160521% | +2.249400% |
| Mg | 4.819330 | +0.869340% | -4.032544% |

## Mg lower-interval diagnostic

The discrete lowest sampled energy for all three Mg curves is the v090 point.
This is diagnostic, not a failure: each continuous BM3 minimum lies strictly
between v090 and v094, finite-smearing pressure changes sign in that same
interval, and the maximum fit residual is only 0.0025--0.0039 meV/atom. The
independent audit also found only about 0.0026% maximum Veq drift in valid
leave-one-out fits. A hard failure remains appropriate if fitted Veq reaches
or leaves the sampled range, curvature is non-positive, or the residual gate
fails.

## Reproduction and provenance

From the repository root:

```bash
scripts/analyze_s1_eos.py analysis/s1/core_eos_20260805 \
  $(awk 'NR > 1 {print "runs/" $1}' config/S1_eos_run_manifest.tsv)
```

Machine-readable results are in `summary.json`; all selected points are in
`points.tsv`. The summary records analyzer commit `5487fd4`, analyzer script
SHA-256, configuration SHA-256, the single ABACUS binary SHA-256, and every
input code commit. The input matrix itself is fixed by
`config/S1_eos_run_manifest.tsv`.

## Why G1 remains pending

Core EOS and double-smearing acceptance do not close these registered items:

1. non-equilibrium next-cutoff and next-k-mesh relative-energy checks;
2. integrated electron-number validation (not the nominal input count);
3. an independent-program Al/Mg cross-check;
4. the ten-case regeneration failure-rate check.

Do not start S2 solely from this core-EOS acceptance.
