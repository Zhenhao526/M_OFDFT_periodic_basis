# S1-R8 non-equilibrium cutoff and k-mesh convergence

Status: `accepted`. G1 remains `pending`.

All energy comparisons use the seven raw points after anchoring each curve at `V/V0=1.00`; BM3 fits are completeness/shape QA and do not replace the raw maximum.
For KSDFT the parsed ABACUS `E_KS(sigma->0)` field is reported as an entropy-corrected estimator, not as an exact zero-temperature label.

| Material | Series | Axis | Max anchored energy diff (meV/atom) | Max pressure diff (GPa) | Status |
|---|---|---|---:|---:|---|
| al | `ksdft_next_cutoff` | cutoff | 0.006794400 | 0.007118900 | `accepted` |
| al | `ksdft_next_kmesh` | kmesh | 0.392503700 | 0.030742800 | `accepted` |
| al | `ofdft_next_cutoff` | cutoff | 0.001538733 | 0.000170300 | `accepted` |
| mg | `ksdft_next_cutoff` | cutoff | 0.000705450 | 0.000102100 | `accepted` |
| mg | `ksdft_next_kmesh` | kmesh | 0.403149550 | 0.017168400 | `accepted` |
| mg | `ofdft_next_cutoff` | cutoff | 0.000007179 | 0.000043100 | `accepted` |

## G1 items still pending

- `integrated_electron_number_check_not_nominal_input_count`
- `third_smearing_or_ultradense_k_density_potential_derivative_label_audit`
- `independent_ofdft_cross_code_eos_pressure_check`
- `ks_nonlocal_to_ks_local_to_of_local_three_layer_validation`
- `small_displacement_strain_reference_density_and_energy_component_delivery`
- `ten_case_one_command_regeneration_failure_rate`
