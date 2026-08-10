# G1 fixed ten-case single-command regeneration R2

Status: **accepted**. The immutable denominator is
registered/attempted/completed/accepted =
10/10/10/10;
failed/missing/skipped/retried =
0/0/0/0.

## Hard gates

- PASS: terminal_accepted_runner_return_code_zero
- PASS: fixed_denominator_and_no_retry
- PASS: exact_case_command_order
- PASS: all_source_and_replay_scientific_gates
- PASS: all_runtime_rank_proof_ack_gates
- PASS: all_runtime_command_environment_gates
- PASS: stored_runtime_analysis_matches_independent_reparse
- PASS: source_tree_and_hash_contract
- PASS: thermodynamic_label_rows_exact

## Worst observed differences

- Energy: 0 meV/atom.
- Pressure: 0 GPa.
- Thermodynamic label: 0 meV/atom (or meV for mu).
- Certified electron relative error: 5.45432006066e-12.
- Density D1/D2: 0 / 0.
- Potential derivative dg/RMS: 0 / 0 eV.

Raw cube SHA equality is diagnostic only. All field decisions use the registered
geometry-aware density and gauge-projected potential metrics. Finite-smearing
labels are not represented as exact zero-temperature quantities.
