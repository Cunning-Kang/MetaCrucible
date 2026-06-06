# Isolate target execution and mask evaluation data

MetaCrucible runs every target evaluation case in an isolated workspace with a temporary home directory and sanitized environment. Benchmark files, evidence files, and real secret files are masked before target execution so the target cannot inspect expected answers or host credentials.

**Consequences**

- Checks run after the target in the same per-case workspace, but checks are executed by MetaCrucible rather than by the target agent.
- Target execution boundaries and check boundaries are separate; target commands are explicitly allowlisted and command checks have their own deterministic boundary.
- Boundary mapping or masking failures block the run; denied disallowed target actions can count as artifact failures, while actual leakage or outside writes block evidence as invalid.
