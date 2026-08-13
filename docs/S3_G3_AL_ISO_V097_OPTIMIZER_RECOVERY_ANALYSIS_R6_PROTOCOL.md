# S3/G3 Al iso_v097 optimizer recovery analysis-only R6

R5 formal execution is immutable: 391–394 completed 4/4 with no operational failure or retry. Its analyzer completed full raw, density and deterministic optimizer replay, then failed only while serializing `gates.tsv`: `structure_id` is fixed at summary level but was incorrectly requested from each coefficient row.

R6 launches no solver. It revalidates the exact R5 preregistration, closure, terminal, session, state tree and every run; reruns the registered deterministic optimizer replay; invokes the unchanged R5 scientific summarizer; and changes only serialization by copying the fixed top-level `iso_v097` identity into each gate-table row. All thresholds, initial densities, basis functions, optimizer policy and scientific dispositions remain unchanged.

Topology is closure -> implementation -> config-only preregistration -> four-output evidence. R5 IDs and state must never be retried or modified. Even if the Al twenty-structure coefficient subgate is accepted, G3 remains open and S4 remains unauthorized.
