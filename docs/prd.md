# MetaCrucible MVP Product Requirements

## Primary user

A single developer optimizing their own Skills and subagents across Claude Code and oh-my-pi runtimes. Multi-user and team scenarios are out of scope for the MVP.

## MVP user stories

### F1. Review

The user runs `metacrucible review <path>` against an existing capability artifact to get a one-shot diagnostic.

Acceptance:
- Input artifact exists and its frontmatter parses.
- Static Review runs the Darwin 9-dimension rubric and prints per-dimension scores plus the weakest dimensions.
- Execution Evaluation runs when a reviewed Benchmark is present; otherwise Static Review still completes with a warning that Execution was skipped.
- The artifact on disk is not modified.
- Output defaults to human-readable; `--json` emits the same content in a stable machine-readable shape.

### F2. Bootstrap

The user runs `metacrucible bootstrap <path>` to generate evaluation case drafts for an existing artifact.

Acceptance:
- Input artifact exists.
- Generated Evaluation Cases are written to the artifact's benchmark file with an envelope status of `generated`.
- A `BOOTSTRAP_PENDING_REVIEW` sentinel is present so the file is visibly pending until reviewed.
- Bootstrap does not enter optimization.
- Human review can clear the sentinel and update the envelope status; the case becomes a normal Evaluation Case.

### F3. Optimize

The user runs `metacrucible optimize <path>` to improve an existing artifact against a reviewed benchmark.

Acceptance:
- Optimization refuses to start if any Evaluation Case still has `status: generated` or the bootstrap sentinel is present, and points the user to `bootstrap`.
- A baseline is recorded before any revision.
- Optimization Rounds run propose → apply → evaluate → Acceptance Decision.
- Acceptance requires strict Eval Split improvement and no Held-Out Split regression.
- Best artifact, revision history, and per-round Evidence Bundles are written under `.metacrucible/`.
- MetaCrucible does not create git commits automatically; user owns version control.
- Output is human-readable by default and `--json` switchable.

### F4. Synthesize

The user runs `metacrucible synthesize "<capability need>"` (or `--from spec.md`) to create a new capability artifact.

Acceptance:
- A draft canonical source is produced and a baseline is recorded.
- Generated Evaluation Cases are produced for the draft and held pending review (same sentinel + envelope mechanism as F2).
- After human review, optimization runs automatically using the same loop as F3.
- The synthesized artifact is not considered accepted until at least one Optimization Round has produced an Acceptance Decision that passes the gate.
- Failure to reach acceptance after configured stopping conditions surfaces an `aborted` outcome with diagnostic evidence.

### F5. Inspect

The user runs `metacrucible inspect <path>` to read prior optimization state.

Acceptance:
- Input artifact exists.
- Output shows revision history, acceptance decisions, evidence bundle index, and current best revision id.
- No files are modified.

## Non-goals (MVP)

- Multi-runtime adaptation beyond the shared `.claude/` layout used by Claude Code and oh-my-pi.
- Concurrent optimization of multiple artifacts.
- Automatic git commits or branch management.
- Hard cost caps (cost is recorded in `state.json` only).
- Codex adapter or `.omp/`-native adapter.
- Full SkillOpt feature set, including slow updates and meta-skills.
- Retrieval-style benchmarks (`qrels`).
- Default sandboxing for target execution.
- More than two independent LLM judges per evaluation.
- Public marketplace, registry, or web UI.
- Resume of interrupted runs without user confirmation.

## Reference mapping

- Engineering flow template: GBrain.
- Optimization algorithm basis: Microsoft SkillOpt.
- Static review rubric: Darwin 9-dimension SkillLens-derived rubric.
- Runtime layout: Claude Code / oh-my-pi shared `.claude/` layout.
- See `docs/adr/` for binding architectural decisions.
