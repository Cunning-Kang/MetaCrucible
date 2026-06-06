# Use SkillOpt-style rank-and-clip with Darwin review profiles

MetaCrucible follows the SkillOpt optimization shape: reflect over eval evidence, propose multiple edit suggestions, rank and clip them by edit budget, merge selected edits per mutable range, and evaluate one candidate per round. Darwin-derived rubrics are used as supplemental static review profiles rather than the primary acceptance gate.

**Consequences**

- The default behavior edit budget is 4, routing edit budget is capped at 1, and routing changes require explicit confirmation.
- Optimizer context uses eval-split evidence only; held-out evidence is never fed back into the optimizer.
- Static review profiles combine rule checks and LLM review, with runtime-neutrality, routing-surface safety, secret/privacy risk, and generalized Darwin skill quality profiles available as versioned built-ins.
