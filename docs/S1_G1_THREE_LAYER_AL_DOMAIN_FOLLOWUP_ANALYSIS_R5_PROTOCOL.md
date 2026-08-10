# S1/G1 Al-domain follow-up analysis-only R5 protocol

Status: implementation pending preregistration.

Analysis revision: `S1-G1-THREE-LAYER-AL-DOMAIN-FOLLOWUP-ANALYSIS-20260810-R5`.

## Scope

R5 performs no solver calculation and registers no new experiment ID. It replays the immutable R4 state
`/home/shenwei01/.local/state/m_ofdft/s1_g1_three_layer_al_domain_followup_r4_20260810`,
whose terminal is accepted for S1-20260810-351 through 358 (8 attempted, 8 accepted, failed/retried 0,
runner return code 0). The R4 state, R1/R2/R3 states, and pseudo cache are read-only inputs.

R5 closes two deterministic false negatives in the frozen R4 analyzer:

1. The R4 runner froze `r2_operational_failure_closure`,
   `r3_operational_failure_closure`, and `r3_parser_regression` in
   `session.parent_source_identity`; the R4 analyzer omitted those three keys from its reconstructed
   dictionary before exact whole-dictionary comparison.
2. Executed R4 input metadata uses the frozen minimal pseudo schema
   `basename, expanded_nonlocal_projectors_per_atom, format, sha256, z_valence`; the R4 analysis
   closure incorrectly required two fields that this schema never contained:
   `upstream_commit` and `upstream_url`.

The historical R4 analyzer is immutable and remains identified by SHA-256
`4ad34ec8e207bc66b7e4f652b3fbb3570567efcb23d599f9a312afd3d846a83e`
and Git blob `b28e5ce1665adb40f3d0320b5817dea0ee669446`.
R5 verifies those bytes both in the current clean tree and at the exact closure commit. It likewise
requires the R4 config, manifest, and runner to equal their closure-recorded SHA-256/Git-blob identities
and their bytes at runner commit `0c60b0613c5c180f05256ad160d287fe4a5bae8a`.

## Hard gates

R5 must independently recompute the two omitted operational closures and the complete 343 parser
fixture regression from committed evidence and read-only external state. The resulting three-key
extension must have exactly those three keys, must not overlap the base reconstructed identity, must
equal the corresponding values frozen in the R4 session, and is then added before retaining exact
whole-dictionary equality. Deleting keys, subset comparison, or accepting a boolean summary is forbidden.

For every 351–358 run, the minimal input pseudo dictionary must have exactly the five frozen keys and
values. R5 explicitly maps it to the committed R4 config repository, upstream commit, canonical URL,
Git blob, and SHA. It then requires equality among runtime metadata, result, and
`pseudo_identity.json`; fully validates the external cache UPF SHA/header/projectors; replays the raw
parser with the exact cache body in a temporary directory when the committed snapshot omits UPF bytes;
and checks the runtime projector count. This strict minimal-schema path is enabled only for 351–358;
327–332 retain the frozen full-upstream-identity closure. Missing cache, an extra legacy field, a missing
field, a wrong value, or any identity mismatch fails closed. UPF bodies are never committed.

All existing R4 gates remain unchanged: 8/8 exact terminal and marker/result/return/metadata maps;
complete raw parser replay; cube origin/full axes/electron identity; force/stress/pressure; geometry
reconstruction; four strain anchored differences at or below 20 meV/atom; and both endpoint strict
k, cutoff, and pressure bounds.
The four individual strain values, all six endpoint values, ten-row denominator, and accepted status are
frozen in the R5 preregistration. R5 requires exact binary64 replay and independently requires their
maximum/endpoints to equal the scientific diagnostic recorded by the R4 false-negative closure.

## Git and execution topology

Four commits (`98752ed…`, `7adc430…`, `68b0c89…`, and `bacb256…`) are explicitly superseded before
analysis execution. The first had a source-schema dispatch defect; the second corrected that science path but
had the wrong direct-parent topology; the third and its config-only preregistration did not hard-lock the exact
closure-to-implementation file set and complete R4 source/scientific identities. None may be an ancestor of the
final preregistration. The final R5 implementation must instead be a direct unique child of the R4
false-negative closure, and its closure diff must be exactly five A-only paths: this protocol, the superseded
note, analyzer, validator, and test module. The registered implementation identities must equal exactly that
five-path set. The preregistration commit must have that final implementation as its unique parent and add
exactly one path:
`config/S1_g1_three_layer_al_domain_followup_analysis_r5.json`.

The analyzer can execute only at the exact clean preregistration commit and only when the R5 output
root is absent. A descendant or merge commit is rejected. The sole permitted mutation is creation of
`analysis/s1/g1_three_layer_al_domain_followup_analysis_r5_20260810`; no external state is written.

After successful collection, the evidence commit must have the preregistration as its unique parent
and contain additions only under that output root. The committed validator must independently replay
the snapshot, require byte-exact summary/gates/README/revision output, forbid UPF files and symlinks,
and require the Git tree to equal the filesystem evidence set.

## Acceptance

Acceptance requires:

- R4 terminal 8/8, failed/retried 0, return code 0;
- exact R4 state inventory and closure commit/blob/SHA;
- exact three-key parent reconstruction followed by whole-dictionary equality;
- 8/8 explicit pseudo-schema mappings plus full raw/cache validation;
- ten scientific gate rows and overall scientific status `accepted`;
- no solver invocation and no new solver state;
- committed evidence-only child topology; and
- `validate_s1_g1_three_layer_al_followup_analysis_r5.py --require-committed` exit 0.
