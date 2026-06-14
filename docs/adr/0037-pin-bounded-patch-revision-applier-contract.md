# Pin bounded Patch Revision applier contract

MetaCrucible applies bounded Patch Revisions per ADR 0027 / ADR 0032:
default behavior edit budget = 4, routing cap = 1, routing changes
require explicit human confirmation, and a same-range merge that
produces text outside the mutable range blocks the round without
mutating artifact bytes.

**Consequences**

- The selection loop in `run_optimizer_pipeline` clips the
  selected set to `RANKED_EDIT_BUDGET (= 4)` per round; a fifth
  suggestion lands in `rejected` with the budget reason and the
  `MUTABLE_RANGE_CONFLICT_BLOCKER` id.
- The default-reject routing gate stays: a routing edit without
  `human_confirmed` is rejected with `ROUTING_HITL_UNCONFIRMED_BLOCKER`;
  a second routing edit is rejected with
  `ROUTING_CAP_EXCEEDED_BLOCKER`. The cap is unchanged at 1.
- The same-range LLM merge path treats `fits_in_range=False` as
  the signal to mark `RangeMergePlan.merge_outside_mutable_range=True`
  so the round blocks before `apply_patch_revision`. An empty
  per-range replacement is rejected as a schema-validation failure.
- `MUTABLE_RANGE_CONFLICT_BLOCKER` remains the stable blocker id
  for merge-outside-range / budget-exceeded / range-overlap /
  routing-contradiction blockers.

**References**

- ADR 0006 (workspace masking + boundary reporting).
- ADR 0019 (provider config contract).
- ADR 0020 (MVP CLI surface).
- ADR 0027 (routing surface cap; default-reject routing).
- ADR 0032 (optimizer pipeline contract; same-range LLM merge).
- Issue #33 (closed, optimizer MVP).
- Issue #34 (this ADR; bounded Patch Revision applier).
