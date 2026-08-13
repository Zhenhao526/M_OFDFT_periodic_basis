# S3/G3 Al iso_v097 optimizer recovery R5

This is a new, no-retry four-case revision. It does not rewrite the committed R3 rejection. One fresh full-grid reference and all three coefficient initializations are recomputed for `iso_v097`; all scientific thresholds, the 23-function basis, 40^3 grid, functional, pseudopotential, and registered physical initial densities remain unchanged.

The only algorithmic change is a preregistered feasible trust-radius Armijo-BFGS method. Directions are radius limited; near the non-negativity boundary their outward components are projected away. Repeated negligible energy changes reset the inverse Hessian and shrink the trust radius. Uniform must still begin from the exact registered uniform density: continuation, warm starts from another initialization, and silent replacement are forbidden. Full trajectories and recovery events are evidence.

Acceptance requires all four operational results, all three coefficient statuses accepted, the original electron/nonnegative/gradient/FD/monotonic/20-meV/1.5%-L2/variational gates, and the original three-initialization spread below 1 meV/atom. Passing closes only the Al twenty-structure coefficient subgate by combining this result with the 19 structures already accepted in R3; G3 remains open and S4 remains unauthorized.
