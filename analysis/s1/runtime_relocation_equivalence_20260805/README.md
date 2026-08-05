# S1-R8 runtime-relocation six-point equivalence replay

- Status: `accepted`
- Recommended action: `close_runtime_relocation_equivalence_and_keep_s1_r8_conclusion`
- Scientific gates passed: 6/6
- R8 replacement conclusions unchanged: 6/6
- Runtime audits accepted: 6/6

The energy and pressure gates are strict: `|dE| < 0.1 meV/atom` and `|dP| < 0.02 GPa`. KS energy uses the logged `E_KS(sigma->0)` entropy-corrected estimator; OF uses `!FINAL_ETOT_IS`.

Old-prefix accounting does not claim zero attempts. Inside the private mount namespace, exactly 22 ENOENT events are preregistered per point: 10 classid events (launcher plus four ranks), four rank ucx.conf probes, and eight rank opens of the hidden old prefix. Successful old access/exec, an unknown failed probe, an old mapping, or an unexpected mapping rejects the replay.

## Points

| replay | reference | tier | dE (meV/atom) | dP (GPa) | R8 unchanged |
|---|---|---:|---:|---:|---:|
| S1-20260805-113 | S1-20260805-074 | storage_exact | 0E-16 | 0E-7 | True |
| S1-20260805-114 | S1-20260805-081 | storage_exact | 0E-10 | 0E-7 | True |
| S1-20260805-115 | S1-20260805-088 | storage_exact | 0E-10 | 0E-7 | True |
| S1-20260805-116 | S1-20260805-095 | storage_exact | 0E-17 | 0E-7 | True |
| S1-20260805-117 | S1-20260805-102 | storage_exact | 0E-11 | 0E-7 | True |
| S1-20260805-118 | S1-20260805-109 | storage_exact | 0E-11 | 0E-7 | True |
