---
status: superseded by ADR-0005
---

# Use a canonical artifact format

MetaCrucible originally planned to use its own canonical capability artifact format and adapt it to each supported agent runtime. This decision is superseded because runtime-native Skill and subagent sources are the canonical artifacts, while artifact envelopes only describe evaluation, mutation, and review metadata; the MVP evaluates those sources through the Claude Code runtime adapter rather than normalizing them into a separate source format.
