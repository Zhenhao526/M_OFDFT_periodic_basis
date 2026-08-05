# G0 acceptance record

- Decision: `accepted` for locked-archive recovery and internal numerical use
- Runtime-isolation subdecision: `accepted` for the registered R2 relocated/private-namespace execution profile
- Date: 2026-08-05
- Scope: internal numerical research
- Evidence experiment: `S0-20260805-003`
- Reproducible-state tag: `s0-clean-recovery-20260805`

## Runtime-isolation erratum (2026-08-05)

The original acceptance record used the recovered ABACUS executable's `ldd`
resolution report as evidence for “zero old-prefix references.” That observation
was real but narrower than the claim made from it: it covered the ABACUS dynamic
libraries resolved by `ldd`, not the complete MPI/PRRTE/UCX process and file-access
chain.

Later whole-runtime tracing found that the recovered `mpirun` can invoke the
old-prefix `prterun` through its compiled prefix, and a stricter runtime probe
also observed successful accesses to the old runtime root while that root
remained visible. Consequently:

- the claims that `S0-20260805-003` proved whole-runtime isolation or a hermetic
  archive are withdrawn;
- the runtime-isolation subcriterion is `paused`;
- the archive hash, restore time, ABACUS `ldd` observation, converged energies,
  and 0.0 meV/atom repeated-run difference remain valid observations;
- no runtime-relocation replay had passed at the time of this correction.

The isolation subcriterion may be reconsidered only after the protocol is fixed
and frozen, the relocated ABACUS passes an S1-074 smoke inside a private
user/mount namespace that hides the old runtime root, and mapped experiments
S1-20260805-113 through 118 pass both strict runtime tracing and numerical/R8
replacement gates.

## Runtime-isolation resolution (2026-08-05)

The predeclared reconsideration conditions are now satisfied. The managed
`S1-RUNTIME-SMOKE-20260805-074` evidence was committed at `92e513f`, the formal
six-point preregistration at `9a0fd7d`, replay results S1-20260805-113 through
118 ended at `ce51927`, and the committed analysis at `a01ac70` is `accepted`.

All six formal points are `storage_exact` relative to their registered
references. Scientific gates passed 6/6, runtime audits passed 6/6, and the six
R8 conclusions remained `accepted` after replacement. At every point, exactly
22 registered old-prefix probes failed with `ENOENT`; successful old-prefix
accesses, successful old-prefix execution, old-prefix mappings, unknown failed
probes, and registered-probe count mismatches were all zero.

The accepted scope is deliberately narrow: protocol R2 on node01, four MPI
ranks, the registered relocated ABACUS, and the private user/mount/PID namespace
launcher with strict tracing. It does not retroactively turn the original S0
archive into a generally hermetic runtime, prove a source rebuild, or generalize
to unregistered hosts, binaries, launch paths, or rank counts.

## Decision table

| G0 criterion | Required | Observed | Result |
|---|---:|---:|---|
| Environment lock, versions, and Git record | 100% | Version/package/CMake/system snapshots and Git history present | pass |
| Pseudopotential and benchmark-input SHA-256 | 100% | Al/Mg LPP and smoke inputs covered | pass |
| Locked-archive restore plus numerical smoke | <60 min, first attempt | 23.46 s from locked archive through tests | pass |
| Repeated-run energy difference | <0.1 meV/atom | 0.0 meV/atom | pass |
| Automated test entry point | zero errors | 2/2 unit tests passed | pass |
| Progress handoff fields | complete | state, next action, tag, experiment, and blockers recorded | pass |
| Whole-runtime old-prefix isolation | zero successful old-prefix access, execution, or mapping | Registered R2 profile: managed 074 plus 6/6 formal points accepted; each formal point has 22/22 registered ENOENT probes and zero successful old access/exec, old mapping, unknown probe, or count mismatch | pass in registered relocated/private-namespace scope |

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
- ABACUS `ldd` audit: 32 libraries resolved from the new prefix and zero from the original baseline prefix.
- Existing-target overwrite test: refused with exit code 2 as required.

The baseline prefix has no Conda metadata. This gate therefore validates a locked
binary recovery, not a source rebuild or a fresh dependency solve. The distinction
is explicit in the README and must be retained in publications.

The `ldd` count above must not be described as a whole-runtime access audit.
It did not cover the launcher selected by OpenMPI/PRRTE, runtime file probes,
memory maps made after process startup, or failed/successful path accesses.

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

## Correction protocol completed

- Original ABACUS SHA-256:
  `2d68a57c7b25608b3550854dabc2e63601eeca956bf185ad7d0967052bdbb4ba`.
- Relocated candidate SHA-256:
  `438c74b9ada4c8df15ffbb66da6755907dfd2a3812ecf868fafd4d7dc4db62e1`.
- The candidate keeps the same ELF Build ID, `NEEDED` entries, and load layout;
  the observed byte differences are confined to the RUNPATH string/padding slot,
  changing the absolute old-prefix RUNPATH to `$ORIGIN/../conda_prefix/lib`.
- The accepted launcher uses an unprivileged private user/mount/PID namespace and
  masks `/home/shenwei01/wt_melting_runtime_20260724` inside that namespace.
- Acceptance requires zero successful old-prefix accesses, zero old-prefix
  execution, zero old-prefix mappings, zero unknown failed probes, and exact
  registered-probe counts under strict tracing.
- The fixed sequence completed as protocol/code hardening → managed S1-074
  namespace smoke (`92e513f`) → preregistration (`9a0fd7d`) → six mapped replays
  (`8ad4ea8`, `a96896b`, `9800067`, `ce7da88`, `12d2867`, `ce51927`) → accepted
  analysis (`a01ac70`).

## Restrictions carried forward

The retained numerical/recovery portion of G0 permits internal S1 analysis. The
registered relocated/private-namespace execution profile may now be described
as whole-runtime old-prefix isolated under protocol R2, but the original archive
alone must not be called generally hermetic. G0 does not grant redistribution rights.
The project license and third-party LPP redistribution terms remain unresolved;
public release is prohibited until those items are accepted. The node01 host also
cannot currently reach GitHub directly, so the verified Git-bundle relay remains
the required synchronization path.
