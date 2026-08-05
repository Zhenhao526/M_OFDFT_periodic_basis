# G0 acceptance record

- Decision: `accepted`
- Date: 2026-08-05
- Scope: internal numerical research
- Evidence experiment: `S0-20260805-003`
- Reproducible-state tag: `s0-clean-recovery-20260805`

## Decision table

| G0 criterion | Required | Observed | Result |
|---|---:|---:|---|
| Environment lock, versions, and Git record | 100% | Version/package/CMake/system snapshots and Git history present | pass |
| Pseudopotential and benchmark-input SHA-256 | 100% | Al/Mg LPP and smoke inputs covered | pass |
| Clean environment restore plus smoke | <60 min, first attempt | 23.46 s from locked archive through tests | pass |
| Repeated-run energy difference | <0.1 meV/atom | 0.0 meV/atom | pass |
| Automated test entry point | zero errors | 2/2 unit tests passed | pass |
| Progress handoff fields | complete | state, next action, tag, experiment, and blockers recorded | pass |

## Clean recovery evidence

- Runtime archive: `/home/shenwei01/M_OFDFT_runtime_20260805.tar.gz`
- Archive size: 443 MB compressed; restored prefix approximately 1.3 GB.
- Archive SHA-256: `5fbfa016d88dea9e691dc67c914aa69b4c933d452a41435e1f9db4766dad6bdd`.
- Archive construction time: 68.70 s. This is a packaging measurement, not part of a normal installation.
- Empty recovery target: `/home/shenwei01/M_OFDFT_recovery_S0_20260805_001`.
- Restore time reported by script: 9 s; external wall time including validation: 11.13 s.
- Unit tests plus two-repeat smoke wall time: 12.33 s.
- Conservative restore-plus-test total: 23.46 s.
- Recovered ABACUS SHA-256: `2d68a57c7b25608b3550854dabc2e63601eeca956bf185ad7d0967052bdbb4ba`.
- Dynamic-library audit: 32 libraries resolved from the new prefix and zero from the original baseline prefix.
- Existing-target overwrite test: refused with exit code 2 as required.

The baseline prefix has no Conda metadata. This gate therefore validates a locked
binary recovery, not a source rebuild or a fresh dependency solve. The distinction
is explicit in the README and must be retained in publications.

## Numerical evidence

- Code commit used by the run: `f0efae6e6a269d9030e63c04e26b70dff0a3e254`.
- Worktree at run start: clean.
- Runtime prefix recorded by metadata: the new recovery target.
- Both fcc Al WT calculations converged.
- Total energies: `-228.73364097028113 eV` and `-228.73364097028113 eV`.
- Difference: `0.0 meV/atom` for four atoms.

Evidence files are stored under `runs/S0-20260805-003/`, including input hashes,
metadata, parsed result, restore result, archive checksum, and the dynamic-library
resolution report.

## Restrictions carried forward

G0 acceptance permits internal S1 work. It does not grant redistribution rights.
The project license and third-party LPP redistribution terms remain unresolved;
public release is prohibited until those items are accepted. The node01 host also
cannot currently reach GitHub directly, so the verified Git-bundle relay remains
the required synchronization path.
