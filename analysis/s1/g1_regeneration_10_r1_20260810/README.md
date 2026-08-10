# G1 regeneration-10 R1 failure closure

R1 is frozen and rejected: 001--003 accepted, 004 consumed and failed the runtime-affinity instrumentation gate, and 005--010 were not attempted. The 004 solver returned zero and its scalar scientific comparison was exact, but all four short-lived ranks had empty /proc cmdline at the sampled instant. Their /proc comm values identify ABACUS and all four recorded affinities are 20-23. The registered classifier required argv[0], so this is an instrumentation false negative, not permission to reinterpret or retry R1.

All R1 IDs 001--010 contribute zero to the regeneration gate and must never be retried. Closure requires R2 IDs 011--020 in a fresh state with deterministic per-rank O_EXCL proof and ACK.
