# S2/G2 atomic_fft Stage A R1 protocol

This analysis-only revision evaluates exactly `r08_atomic_fft` and `r10_atomic_fft` against the committed one-atom Al KS-NL density. No electronic-structure solver or density optimizer is run.

Both candidates are charge-constrained fits of normalized periodic atom-centred Gaussian radial functions. The radial exponents are inherited verbatim from the registered R2 convergence ladder. The reference density, pseudopotential, runtime and DFTpy operators are byte-bound to the earlier accepted evidence.

Promotion to Stage B requires every original one-atom gate: electron-number relative error below `1e-10`, density relative L2 below `1%`, density floor at least `-1e-12 e/bohr^3`, condition number below `1e8`, and absolute Hartree, local-pseudopotential, XC, their combined error, and fixed-WT error each below `10 meV/atom`.

The result may promote zero, one, or two candidates. Failure does not relax a gate and does not start Stage B, G2c, S3, Mg work or a solver. Git closure is base -> implementation -> config-only preregistration -> output-only evidence.
