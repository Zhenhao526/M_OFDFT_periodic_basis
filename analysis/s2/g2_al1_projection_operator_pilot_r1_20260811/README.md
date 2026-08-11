# S2/G2 Al one-atom projection and operator pilot R1

Disposition: `evidence_valid_no_candidate_passes_all_g2a_pilot_gates`.

This is committed analysis-only evidence over the frozen Al KS-NL density; no solver or density optimizer was run.

| candidate | status | density L2 | condition | combined H+ext+XC (meV/atom) | WT error (meV/atom) | failed gates |
|---|---|---:|---:|---:|---:|---|
| pw_fft_reference | accepted_reference | 0 | 1 | 0 | 0 | — |
| atomic_fft | rejected_pilot | 0.06666867 | 18307.7493 | 74.4552916 | 527.791975 | density_l2,nonnegative_density,hartree,external,xc,combined_hartree_external_xc,fixed_kedf |
| atomic_low_g_explicit | rejected_pilot | 0.00577099253 | 101280.175 | 0.364300789 | 15.037873 | fixed_kedf |
| atomic_low_g_complementary | rejected_pilot | 0.00577099253 | 6493.93376 | 0.364300871 | 15.0378733 | fixed_kedf |

Next action: `new_revision_expand_basis_or_low_g_without_modifying_this_pilot`.
