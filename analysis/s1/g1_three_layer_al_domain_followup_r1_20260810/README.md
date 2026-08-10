# S1/G1 Al-domain follow-up R1 closure

Status: `superseded_before_execution`.

The preregistration at `0785b61c3a414b3c1f31865d31c60093fee849a9` is
preserved.  No state directory was created for this follow-up, no attempt marker
was written, and no solver was started for IDs `S1-20260810-319..326`.

The required parent endpoint runs 307 and 308 cannot become available: the
parent R1 host connection ended after run 306 was accepted and before the P0
phase marker was committed.  The parent external state remains frozen with
accepted runs 301--306 only; runs 307--318 must not be executed in that R1.
Consequently this follow-up cannot satisfy its preregistered parent gate and is
closed without execution.  Its eight IDs remain consumed and must never be
reused or retried.

The machine-readable closure in `superseded_before_execution.json` records the
observed state identities.  A new protocol revision with fresh IDs and a fresh
external state is required for any continuation.
