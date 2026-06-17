---
type: SDD Run Record
title: "metacrucible synthesize (PRD F4)"
description: "Implements metacrucible synthesize end-to-end: parser, draft + baseline + generated cases, optimizer resume, and BLOCKED bundle writing for the evaluation stage."
sdd_version: "0.1"
status: ready-for-pr
source_type: issue
source_ref: "#41 https://github.com/Cunning-Kang/MetaCrucible/issues/41"
branch: "sdd/issue-41-metacrucible-synthesize"
base_sha: "b7a2da3e3da6f7d81b79568a1630001f43ea3913"
head_sha: "a558d8d8972f472210ac7e8a6fff8166efcff319"
created_at: "2026-06-17T16:54:38Z"
tags: [sdd, run-record, issue-41, prd-f4, synthesize]
---

## Summary

Implements PRD F4 `metacrucible synthesize` (issue #41) — a new public CLI
command that creates a NEW capability artifact workspace from either an
inline capability-need string or a spec file (`--from spec.md`).

The slice ships 5 task commits plus 1 integration-fix commit:

- `b301e66` — Task 1: parser shell + temporary `synthesize-not-implemented`
  BLOCKED placeholder (replaced by Tasks 2-4).
- `0e74786` — Task 2: real synthesis pipeline. Input resolution
  (inline need or `--from` spec, mutual exclusion, empty/missing
  guards), deterministic draft canonical source (Markdown Skill with
  YAML frontmatter), generated evaluation cases (1 eval + 1
  held_out, sentinel-popped), workspace writes (envelope.json with
  `source: 'synthesize'`, state.json with baseline mapping,
  benchmark.jsonl via `_atomic_write_jsonl`, 4 history events).
- `7e8134d` — Task 3: optimizer resume path. `load_synthesis_workspace`
  reads envelope/state/benchmark and resolves `artifact_path`;
  `benchmark_ready_for_optimization` returns (ready, blockers) using
  `load_benchmark`; `run_synthesis_optimizer` calls
  `run_optimizer_pipeline` with `call_fn=None`, `max_rounds`,
  `human_confirmed=False`, `routing_confirmation_preview=True`;
  `run_synthesize_command` branches: existing output with pending
  cases returns `draft_pending_review` + EXIT_OK (idempotent);
  reviewed cases call the optimizer and map ACCEPTED to
  `outcome: 'accepted'` + EXIT_OK or anything else to
  `outcome: 'aborted'` + EXIT_BLOCKED. Two history events
  bracket the optimizer call.
- `e471b1f` — Task 4: BLOCKED bundle writing for the evaluation
  stage. `_write_synthesize_blocked_bundle(blockers) -> dict[str, str]`
  uses `UserGlobalStorage` and `write_blocked_bundle` with
  `run_type='synthesize_evaluation_stage'` (the existing matrix
  slot in `blocked_bundles.py`). Best-effort: a write failure logs
  to stderr; in-memory payload is authoritative. Evidence refs
  merged into `payload['evidence_refs']` under namespaced
  `blocked_*` keys. Called only in the post-optimizer aborted
  branch.
- `b50bd71` — Task 5: regression gate (empty commit, verification
  only). Confirmed no `synthesize-not-implemented` runtime
  references remain (4 docstring/comment references only), no
  subprocess/venv shell-outs in tests, all tests pass.
- `a558d8d` — Integration fix: thread 3 F3 confirmation flags
  (`--allow-routing-revision`, `--allow-dirty-unrelated`,
  `--confirm-resume`) through `run_synthesis_optimizer` into
  `run_optimizer_pipeline`, mirroring `cmd_optimize`'s
  preview/apply cutover. Eliminates the cross-task integration
  gap found by the final global review (parser accepted the
  flags and advertised them as "aligned with optimize" but the
  resume path silently dropped them).

PRD F4 acceptance criteria coverage (test surface):

- AC1 "Generates draft artifact + baseline + generated cases":
  `test_synthesize_inline_need_creates_draft_pending_review_workspace`
  + `test_synthesize_from_spec_creates_same_pending_review_shape`.
- AC2 "Requires review/promote before generated cases drive
  optimize": `test_synthesize_keeps_pending_review_without_optimizer_call`.
- AC3 "At least one gated optimize round must pass for
  acceptance": `test_synthesize_reviewed_workspace_runs_optimizer_and_accepts`.
- AC4 "Otherwise returns aborted + diagnostics":
  `test_synthesize_reviewed_workspace_aborts_when_optimizer_rejects`
  + `test_synthesize_evaluation_stage_block_writes_blocked_bundle`.

Known follow-up (out of scope, tracked in `synthesize.py` DRIFT
NOTE comment near the `BOOTSTRAP_PENDING_REVIEW_FIELD` definition):

- The `BOOTSTRAP_PENDING_REVIEW` sentinel string is pinned in 3
  files (`__main__.py:265`, `synthesize.py:49`, hardcoded literal
  in `promote.promote_case`). A 4-file refactor should add the
  constant to `metacrucible.benchmark` (where `STATUS_GENERATED`,
  `SPLIT_EVAL`, `SPLIT_HELD_OUT` already live) and update all 3
  call sites in lockstep. This is a separate issue/ADR; the
  F4 slice correctly stays within its edit budget.

## Evidence

### Commits (chronological, base → head)

| SHA | Subject |
| --- | --- |
| b301e66 | Task 1: Add synthesize parser and BLOCKED placeholder (#41) |
| 0e74786 | Task 2: Implement synthesize pipeline (draft + baseline + generated cases) (#41) |
| 7e8134d | Task 3: Wire synthesize optimizer resume path (#41) |
| e471b1f | Task 4: Write synthesize evaluation-stage BLOCKED bundles (#41) |
| b50bd71 | Task 5: Full synthesize command integration and regression gate (#41) |
| a558d8d | Integration fix: thread 3 F3 confirmation flags through synthesize resume path (#41) |

### Test results (final verification, run in the worktree venv)

- `tests/test_synthesize_command.py`: **16 passed, 0 failed** (14 from the
  5 task slices + 2 from the integration fix).
- Focused init/optimize regressions
  (`-k "parser or subcommand_is_recognized or missing_workspace_argparse"`):
  **5 passed, 68 deselected, 0 failed**.
- Full suite: **757 passed, 1 skipped, 0 failed** across 36 files
  (~14s wall). The 1 skip is pre-existing and unrelated to F4.

### Branch diff scope

- `src/metacrucible/__main__.py` (+42 lines): parser shell,
  `cmd_synthesize` thin wrapper, `main()` dispatch.
- `src/metacrucible/synthesize.py` (+1269 lines, new leaf module):
  7 stable constants, 6+ helper functions
  (`resolve_synthesize_input`, `default_artifact_filename`,
  `build_draft_canonical_source`, `build_generated_cases`,
  `create_synthesis_workspace`, `load_synthesis_workspace`,
  `benchmark_ready_for_optimization`, `run_synthesis_optimizer`,
  `_synthesize_resume_branch`, `_emit_pending_review_payload`,
  `_write_synthesize_blocked_bundle`, `run_synthesize_command`),
  module-local `_now_iso`, `DRIFT NOTE` for the bootstrap
  sentinel consolidation follow-up.
- `tests/test_synthesize_command.py` (+1673 lines, new file):
  parser-contract tests, command-behavior tests, precondition
  tests, resume-path tests (accept / abort / pending), BLOCKED
  bundle test, integration-repair tests.
- Total: +2984, -0.
- All 3 files are in the per-task `Allowed to edit` / `Allowed to
  create` lists. No out-of-scope files. Controller-side deletion
  of `.sdd/plans/40-metacrucible-optimize.md` (a stale leftover
  from a prior worktree) is correctly NOT committed.

### Repair history (per task)

- **Task 1**: 1 quality-repair round. Findings 1, 2, 3 (Important
  + 2 Minor) addressed. Finding 1 (placeholder BLOCKED-emission
  had zero test coverage) closed by adding 2 public-main
  dispatcher tests. Finding 2 (help text embedded internal task
  language) closed by trimming + moving context to a code
  comment. Finding 3 (renamed dests un-tested) closed by adding
  default-false assertions + a renamed-dests flip-true test.
  Final status: **PASS**.
- **Task 2**: 1 quality-repair round. Finding 1 (Important
  drift-risk) verified as false-positive (the constant is NOT
  exported by `metacrucible.benchmark`; the actual drift is
  across 3 files including a hardcoded literal in `promote.py`).
  Partial fix: added a `DRIFT NOTE:` comment documenting the
  drift surface and the consolidation follow-up. Final status:
  **PASS**.
- **Task 3**: 1 quality-repair round. Findings 2, 3, 4 (3 Minor)
  addressed. Finding 1 (Important message-text change) verified
  as false-positive (test asserts only on blocker ID, not
  message text). Final status: **PASS**.
- **Task 4**: 1 spec+quality repair round. Spec Step 3 (helper
  signature deviation: dropped `now=` kwarg; added module-local
  `_now_iso()`). Quality Finding 1 (Important `chr(58+0)` no-op
  fixed by replacing with `.replace(':', '').replace('-', '')`
  byte-matching the optimize bundle format). Quality Findings
  2, 3 (test freeze, wrong patch target) fixed. Final status:
  **PASS**.
- **Task 5**: 0 repair rounds. Both reviews PASS on first
  attempt. No code changes (empty commit). Final status:
  **PASS**.
- **Global**: 1 integration-repair round. Finding 1 (Important
  cross-task gap on 3 confirmation flags) addressed by
  threading the flags through `run_synthesis_optimizer` into
  `run_optimizer_pipeline`, mirroring `cmd_optimize`'s
  preview/apply cutover. 2 new tests pin the threading.
  Touched acceptance-critical files (`synthesize.py` and
  `test_synthesize_command.py`); per the SDD checkpoint, spec
  re-reviews of Tasks 3 and 4 + global quality re-review all
  returned PASS. Final status: **PASS**.

### Per-task review verdicts (all PASS post-repair)

| Task | Spec | Quality | Quality repair rounds |
| --- | --- | --- | --- |
| 1 | PASS | PASS | 1/3 |
| 2 | PASS | PASS | 1/3 |
| 3 | PASS | PASS | 1/3 |
| 4 | PASS | PASS | 1/3 (after 1 spec repair) |
| 5 | PASS | PASS | 0/3 |
| Global | PASS | PASS | 1 (integration fix) |

### ADRs and contracts bound by this slice

- **ADR 0030** (receipt/evidence bundle v1 schema) — the
  evaluation-stage BLOCKED bundle (Task 4) follows the v1
  schema via the existing `write_blocked_bundle` helper
  (`blocked_bundles.py`).
- **ADR 0032** (optimizer pipeline contract) — `synthesize`'s
  resume path calls `run_optimizer_pipeline` with the same
  bounded settings as `optimize` (`call_fn=None`, `max_rounds`,
  `human_confirmed=False`, `routing_confirmation_preview=True`).
- **ADR 0035** (BLOCKED bundle policy) — the
  `synthesize_evaluation_stage` category is in
  `REQUIRES_BLOCKED_BUNDLE_CATEGORIES` and is the only
  synthesize-emitted category; the synthesis/draft stage is
  non-emitting (mirrors `bootstrap`).
- **ADR 0029** (benchmark jsonl v1 schema) — the generated
  case records follow the case-record partition shape
  (`record_type: case_eval` / `case_held_out` with
  `split: eval` / `held_out`).
- **CONTEXT.md** glossary — `Synthesis`, `Generated Evaluation
  Case`, `Acceptance Decision`, `Stopping Condition`,
  `Acceptance Gate`, `Mutable Range` are all used correctly
  in the implementation and tests.

## Runtime Plan snapshot

The full final canonical Plan is reproduced below. It records
each task's status, review verdicts, repair-round counts, and
commit SHA at the time the slice was completed.

```md
# Plan: metacrucible synthesize

## Source
Type: issue
Ref: #41 + PRD F4 `metacrucible synthesize`
Issue close mode: auto-on-merge

## Pipeline status
Status: complete
Branch: sdd/issue-41-metacrucible-synthesize
Worktree: /Users/cunning/Workspaces/heavy/.sdd-worktrees/MetaCrucible/issue-41-metacrucible-synthesize
Base: b7a2da3e3da6f7d81b79568a1630001f43ea3913

## Goal
Implement PRD F4 `metacrucible synthesize` as the public CLI path that creates a new capability artifact workspace from either an inline capability need or a source spec file, records a baseline, produces generated evaluation cases held pending review with the same sentinel/envelope mechanism as `bootstrap`, then resumes into the existing optimizer loop after human-reviewed cases are available.

## Acceptance criteria
- [ ] `metacrucible synthesize "<capability need>"` creates a workspace, writes a draft canonical source, writes `.metacrucible/envelope.json`, `.metacrucible/state.json`, `benchmark.jsonl`, and records baseline/synthesis history without requiring an existing artifact.
- [ ] `metacrucible synthesize --from spec.md` reads the spec file as the capability need and produces the same durable synthesis outputs; missing, empty, or conflicting input modes are rejected with stable blockers and no partial workspace.
- [ ] Generated Evaluation Cases are written with `status: "generated"` and `BOOTSTRAP_PENDING_REVIEW_FIELD` set to `true`, so existing benchmark loading and `optimize` preconditions hold them pending human review.
- [ ] When reviewed eval and held-out cases are already present and no generated/sentinel cases remain, `synthesize` automatically invokes the existing optimizer loop using the same bounded settings as `optimize` and reports the optimizer acceptance decision.
- [ ] The synthesized artifact is reported as accepted only when the optimizer returns an accepted Acceptance Decision; otherwise the outcome is `aborted` with diagnostic evidence after configured stopping conditions.
- [ ] Evaluation-stage blockers in `synthesize` write a minimal BLOCKED evidence bundle using the existing `synthesize_evaluation_stage` matrix slot from `blocked_bundles.py`.
- [ ] CLI output is human-readable by default, `--json` emits a stable machine-readable payload, and the command returns existing stable exit codes: `EXIT_OK` for accepted or draft-pending-review success, `EXIT_BLOCKED` for blocked/aborted preconditions or evaluation-stage blockers, and `EXIT_USER_ERROR` for argparse input errors.

## Assumptions
- The implementation may choose a deterministic default output workspace for inline needs when `--output` is not provided, but tests should prefer explicit `--output` to avoid current-working-directory ambiguity.
- Draft canonical source may be a minimal Skill-style Markdown capability artifact because PRD F4 requires a draft canonical source, not provider-quality content.
- Baseline recording for synthesis means durable baseline facts in `.metacrucible/state.json` and `.metacrucible/history.jsonl`; do not require the `baseline create` CLI to run as a subprocess.
- Deterministic draft source and generated cases satisfy this MVP slice; provider-backed synthesis is not part of this issue.

## Task 1: Add synthesize parser and command dispatch

### Goal
Expose `metacrucible synthesize` through argparse and dispatch it to a real command wrapper without creating synthesis outputs yet.

### Files

Allowed to edit:
- `src/metacrucible/__main__.py` — add synthesize parser flags, input-mode argparse validation, `cmd_synthesize(args)` thin wrapper, and `main()` dispatch.

Allowed to create:
- `tests/test_synthesize_command.py` — add parser and dispatch tests for the new public command.

Read-only context:
- `docs/prd.md` — PRD F4 acceptance text.
- `docs/adr/0035-pin-mvp-cli-surface-and-operational-behavior.md` — public command and BLOCKED bundle behavior.
- `tests/test_optimize_command.py` — parser and `cmd_*` test patterns.
- `tests/test_init_command.py` — CLI output and workspace assertion patterns.

### Steps
- [x] In `tests/test_synthesize_command.py`, add `test_synthesize_parser_accepts_inline_need` that imports `_build_parser`, parses `['synthesize', 'write a skill', '--output', str(tmp_path / 'skill')]`, and asserts `args.command == 'synthesize'`, `args.capability_need == 'write a skill'`, `args.from_spec is None`, `args.output` matches the supplied path, `args.max_rounds` is present, and `args.json is False`.
- [x] Add `test_synthesize_parser_accepts_from_spec_with_json` for `['synthesize', '--from', str(spec_path), '--output', str(tmp_path / 'skill'), '--json']` and assert `args.capability_need is None`, `args.from_spec` matches the spec path, and `args.json is True`.
- [x] Add argparse-error tests using `pytest.raises(SystemExit)` for both missing input and conflicting input; assert exit code is `2`.
- [x] In `src/metacrucible/__main__.py`, add `synthesize_parser = subparsers.add_parser('synthesize', ...)` beside the other public PRD commands.
- [x] Use a mutually exclusive group with optional positional `capability_need` and `--from` stored as `from_spec`.
- [x] Add `--output`, `--max-rounds` defaulting to `ROUND_BUDGET_DEFAULT`, `--allow-routing-revision`, `--allow-dirty-unrelated`, `--confirm-resume`, and `--json` flags.
- [x] Add a temporary `cmd_synthesize(args)` that emits the BLOCKED placeholder and returns `EXIT_BLOCKED` (REMOVED in Task 2).
- [x] Dispatch `args.command == 'synthesize'` to `cmd_synthesize(args)` in `main()`.

### Verification
Discovery: no
Commands:
- `/Users/cunning/Workspaces/heavy/.sdd-worktrees/MetaCrucible/issue-41-metacrucible-synthesize/.venv/bin/python -m pytest tests/test_synthesize_command.py -k "parser" -v`

Expected:
- parser tests pass, 0 failed

### Status
Status: pass
Spec review: pass
Quality review: pass
Spec repair rounds: 0/3
Quality repair rounds: 1/3
Commit: b301e6634033488880893c5207ed97638c81830b

## Task 2: Create draft workspace and pending generated cases

### Goal
Implement the non-optimizing synthesis path: a valid need or spec creates the draft canonical source, envelope/state, baseline/history records, and pending generated evaluation cases, then exits successfully with a draft-pending-review outcome.

### Files

Allowed to edit:
- `src/metacrucible/__main__.py` — keep `cmd_synthesize` a thin wrapper and add imports only if the wrapper requires them.
- `tests/test_synthesize_command.py` — add command tests for draft workspace creation, `--from`, and precondition blockers.

Allowed to create:
- `src/metacrucible/synthesize.py` — implement synthesis input resolution, draft source, generated cases, workspace writes, and command payloads.

Read-only context:
- `src/metacrucible/__main__.py` — `_emit`, `_now_iso`, `BENCHMARK_FILE_NAME`, `_default_state`, `_default_metadata_record`, `BOOTSTRAP_PENDING_REVIEW_FIELD`, `STATUS_GENERATED`, and command style.
- `src/metacrucible/storage.py` — `RepositoryStorage` envelope/state/history helpers.
- `src/metacrucible/promote.py` — `_atomic_write_jsonl` helper for benchmark writes.
- `src/metacrucible/benchmark.py` — generated case partition behavior.
- `tests/test_init_command.py` and `tests/test_optimize_command.py` — workspace and JSON output assertions.

### Steps
- [x] In `src/metacrucible/synthesize.py`, define the 7 stable constants.
- [x] Implement `resolve_synthesize_input`.
- [x] Implement `default_artifact_filename`.
- [x] Implement `build_draft_canonical_source`.
- [x] Implement `build_generated_cases` (1 eval + 1 held_out).
- [x] Implement `create_synthesis_workspace`.
- [x] Implement `run_synthesize_command` (precondition blockers + draft-pending-review outcome).
- [x] Add `test_synthesize_inline_need_creates_draft_pending_review_workspace`.
- [x] Add `test_synthesize_from_spec_creates_same_pending_review_shape`.
- [x] Add precondition tests (missing spec, empty spec, existing output).

### Verification
Discovery: no
Commands:
- `/Users/cunning/Workspaces/heavy/.sdd-worktrees/MetaCrucible/issue-41-metacrucible-synthesize/.venv/bin/python -m pytest tests/test_synthesize_command.py -k "draft_pending_review or from_spec or spec_missing or spec_empty or output_exists" -v`

Expected:
- selected tests pass, 0 failed

### Status
Status: pass
Spec review: pass
Quality review: pass
Spec repair rounds: 0/3
Quality repair rounds: 1/3
Commit: 0e74786ad99f44964d4ec7e66fdbd7a376de16c9

## Task 3: Resume reviewed synthesis into optimizer loop

### Goal
When a synthesis workspace already contains reviewed eval and held-out cases and no pending generated/sentinel cases, run the existing optimizer loop automatically and report acceptance, rejection, or aborted outcome correctly.

### Files

Allowed to edit:
- `src/metacrucible/synthesize.py` — add existing-workspace loading, benchmark readiness detection, optimizer invocation, and accepted/aborted payload mapping.
- `tests/test_synthesize_command.py` — add reviewed-workspace optimizer resume tests.

Allowed to create:
- (none)

Read-only context:
- `src/metacrucible/__main__.py` — `cmd_optimize` precondition flow, routing/resume flag behavior, and optimizer payload shape.
- `src/metacrucible/optimizer.py` — `run_optimizer_pipeline` result fields and accepted/rejected/blocked statuses.
- `src/metacrucible/benchmark.py` — reviewed eval and held-out eligibility rules.
- `tests/test_optimize_command.py` — monkeypatch patterns for `run_optimizer_pipeline` and `OptimizerPipelineResult`-compatible stubs.

### Steps
- [x] Add `_reviewed_synthesis_workspace` test helper.
- [x] Add `test_synthesize_reviewed_workspace_runs_optimizer_and_accepts`.
- [x] Add `test_synthesize_reviewed_workspace_aborts_when_optimizer_rejects`.
- [x] Add `test_synthesize_keeps_pending_review_without_optimizer_call`.
- [x] Implement `load_synthesis_workspace`.
- [x] Implement `benchmark_ready_for_optimization`.
- [x] Implement `run_synthesis_optimizer` (with integration-fix threading of 3 confirmation flags).
- [x] Wire `run_synthesize_command` dispatch (create-vs-resume branch + accepted/aborted outcome mapping).
- [x] Append `synthesis_optimizer_started` and `synthesis_finished` history events.

### Verification
Discovery: no
Commands:
- `/Users/cunning/Workspaces/heavy/.sdd-worktrees/MetaCrucible/issue-41-metacrucible-synthesize/.venv/bin/python -m pytest tests/test_synthesize_command.py -k "reviewed_workspace_runs_optimizer or reviewed_workspace_aborts or keeps_pending_review" -v`

Expected:
- selected tests pass, 0 failed

### Status
Status: pass
Spec review: pass
Quality review: pass
Spec repair rounds: 0/3
Quality repair rounds: 1/3
Commit: 7e8134d617ef83f4ceace5d11dd2b908d6614c63

## Task 4: Write synthesize evaluation-stage BLOCKED bundles

### Goal
Ensure synthesize evaluation-stage blockers produce the ADR 0035 minimal BLOCKED evidence bundle under run type `synthesize_evaluation_stage`.

### Files

Allowed to edit:
- `src/metacrucible/synthesize.py` — add synthesize evaluation-stage BLOCKED bundle writer and attach evidence refs to aborted optimizer payloads.
- `tests/test_synthesize_command.py` — add BLOCKED evidence bundle coverage for optimizer-stage failure.

Allowed to create:
- (none)

Read-only context:
- `src/metacrucible/blocked_bundles.py` — existing `REQUIRES_BLOCKED_BUNDLE_CATEGORIES` includes `synthesize_evaluation_stage`.
- `src/metacrucible/storage.py` — `UserGlobalStorage` evidence paths.
- `src/metacrucible/__main__.py` — `_write_optimize_blocked_bundle` and `_write_evaluate_blocked_bundle` best-effort pattern.
- `tests/test_optimize_command.py` — BLOCKED bundle assertions.

### Steps
- [x] Add `test_synthesize_evaluation_stage_block_writes_blocked_bundle`.
- [x] Define `SYNTHESIZE_EVALUATION_BLOCKED_BUNDLE_RUN_TYPE` and `SYNTHESIZE_BLOCKED_BUNDLE_RUN_ID_PREFIX`.
- [x] Implement `_write_synthesize_blocked_bundle` using `UserGlobalStorage` + `write_blocked_bundle`. Best-effort, module-local `_now_iso`, byte-matching optimize bundle run_id format.
- [x] Call only in post-optimizer aborted branch.
- [x] Merge returned evidence refs into `payload['evidence_refs']` under namespaced `blocked_*` keys.

### Verification
Discovery: no
Commands:
- `/Users/cunning/Workspaces/heavy/.sdd-worktrees/MetaCrucible/issue-41-metacrucible-synthesize/.venv/bin/python -m pytest tests/test_synthesize_command.py -k "blocked_bundle" -v`

Expected:
- selected tests pass, 0 failed

### Status
Status: pass
Spec review: pass
Quality review: pass
Spec repair rounds: 1/3
Quality repair rounds: 1/3
Commit: e471b1fa4e2ec0b91bbc6d7401c45e4e49bf5e23

## Task 5: Full synthesize command integration and regression gate

### Goal
Prove the completed synthesize command satisfies all PRD F4 acceptance criteria through the public command test file and does not regress existing init/optimize command behavior touched by parser and shared helper changes.

### Files

Allowed to edit:
- `src/metacrucible/__main__.py` — final parser/dispatcher fixes only if integration tests expose a synthesize dispatch regression.
- `src/metacrucible/synthesize.py` — final synthesize behavior fixes only if integration tests expose a PRD F4 failure.
- `tests/test_synthesize_command.py` — final test corrections only for assertions that conflict with implemented PRD F4 behavior.

Allowed to create:
- (none)

Read-only context:
- `tests/test_init_command.py` — parser/init regression surface.
- `tests/test_optimize_command.py` — optimize parser and wrapper regression surface.
- `docs/prd.md` and `docs/adr/0035-pin-mvp-cli-surface-and-operational-behavior.md` — final acceptance mapping.

### Steps
- [x] Confirm production code and tests contain no `synthesize-not-implemented` blocker id or message. (4 docstring/comment references remain; no runtime code.)
- [x] Confirm every command in `tests/test_synthesize_command.py` uses the worktree venv command path when it shells out; prefer direct function calls.
- [x] Run the synthesize command test file (16 passed, 0 failed).
- [x] Run focused parser/command regressions from init and optimize (5 passed, 68 deselected).
- [x] No regressions required changing existing test expectations.

### Verification
Discovery: no
Commands:
- `/Users/cunning/Workspaces/heavy/.sdd-worktrees/MetaCrucible/issue-41-metacrucible-synthesize/.venv/bin/python -m pytest tests/test_synthesize_command.py -v`
- `/Users/cunning/Workspaces/heavy/.sdd-worktrees/MetaCrucible/issue-41-metacrucible-synthesize/.venv/bin/python -m pytest tests/test_init_command.py tests/test_optimize_command.py -k "parser or subcommand_is_recognized or missing_workspace_argparse" -v`

Expected:
- all listed tests pass, 0 failed
- no production placeholder blockers remain for implemented `synthesize`

### Status
Status: pass
Spec review: pass
Quality review: pass
Spec repair rounds: 0/3
Quality repair rounds: 0/3
Commit: b50bd71f01fe2e63e31b911884ae647071f73b07

## Finalization

Final global review: pass
Final verification: pass
Integration fix:
  Status: pass
  Commit: a558d8d8972f472210ac7e8a6fff8166efcff319
  Affected tasks: 1, 2, 3, 4, 5 (integration fix; re-spec-reviewed)
  Review exemption: integration repair touches Task 1 (parser help + flag dests) and Task 3 (run_synthesis_optimizer signature) acceptance-critical files; re-run spec on Tasks 1 and 3 + global review after repair
Run Record: ready-for-pr
Finish decision: pending
PR URL: pending

## Final verification commands
- `/Users/cunning/Workspaces/heavy/.sdd-worktrees/MetaCrucible/issue-41-metacrucible-synthesize/.venv/bin/python -m pytest tests/test_synthesize_command.py -v`
- `/Users/cunning/Workspaces/heavy/.sdd-worktrees/MetaCrucible/issue-41-metacrucible-synthesize/.venv/bin/python -m pytest tests/test_init_command.py tests/test_optimize_command.py -k "parser or subcommand_is_recognized or missing_workspace_argparse" -v`

## Resume notes
- Start with Task 1.
- Use only named SDD roles for execution and review: implementer, spec-reviewer, code-quality-reviewer.
- Run tests with `/Users/cunning/Workspaces/heavy/.sdd-worktrees/MetaCrucible/issue-41-metacrucible-synthesize/.venv/bin/python -m pytest`, not bare `python3`.
- Reviews may need inline diffs if `.py` reads are gated in this worktree.
```

## Citations

- `docs/prd.md` (PRD F4 acceptance text — `metacrucible synthesize`)
- `docs/adr/0029-pin-benchmark-jsonl-v1-schema.md` (case-record
  partition shape, status field)
- `docs/adr/0030-pin-receipt-and-evidence-bundle-v1-schema.md`
  (receipt / summary / trajectory-digest trio, sibling-relative
  refs, summary allowlist)
- `docs/adr/0032-pin-optimizer-pipeline-contract.md`
  (`run_optimizer_pipeline` contract — `call_fn`, `max_rounds`,
  `human_confirmed`, `routing_confirmation_preview`)
- `docs/adr/0035-pin-mvp-cli-surface-and-operational-behavior.md`
  (public commands list includes `synthesize`; BLOCKED bundle
  matrix includes `synthesize_evaluation_stage`; minimal BLOCKED
  bundle scope)
- `docs/adr/0037-pin-bounded-patch-revision-applier-contract.md`
  (router / routing-surface semantics consulted by the
  preview/apply cutover in the integration fix)
- `CONTEXT.md` (domain glossary: `Synthesis`,
  `Generated Evaluation Case`, `Acceptance Decision`,
  `Stopping Condition`, `Acceptance Gate`, `Mutable Range`,
  `Routing Surface`)
- `.sdd/plans/41-metacrucible-synthesize.md` (the canonical Plan
  reproduced in the Runtime Plan snapshot above)
- `.sdd/plans/40-metacrucible-optimize.md` (prior slice's Plan,
  also a stale leftover in the worktree at slice start; deletion
  correctly NOT committed)
- `tests/test_blocked_bundle_policy.py` (pre-existing
  `test_matrix_includes_synthesize_evaluation_stage` test that
  pre-empted this slice; passes unchanged)
- `tests/test_optimize_command.py` (BLOCKED bundle assertion
  pattern mirrored in Task 4; `cmd_optimize` preview/apply
  cutover pattern mirrored in the integration fix)
- `src/metacrucible/blocked_bundles.py`
  (`REQUIRES_BLOCKED_BUNDLE_CATEGORIES` includes
  `synthesize_evaluation_stage`; `write_blocked_bundle` helper
  used by Task 4)
- `src/metacrucible/optimizer.py` (`run_optimizer_pipeline`
  entrypoint; `ROUND_BUDGET_DEFAULT` default)
- `src/metacrucible/benchmark.py` (`load_benchmark`,
  `STATUS_GENERATED`, `SPLIT_EVAL`, `SPLIT_HELD_OUT`,
  `BOOTSTRAP_PENDING_REVIEW_FIELD` — the constant is documented
  as the natural consolidation home by the DRIFT NOTE in
  `synthesize.py`)
- `src/metacrucible/storage.py` (`UserGlobalStorage.evidence_bundle_dir`,
  `write_receipt` / `write_summary` / `write_trajectory_digest`
  writers used by `write_blocked_bundle`)
- `src/metacrucible/promote.py` (`_atomic_write_jsonl` helper
  used by `create_synthesis_workspace`; the hardcoded literal
  `"BOOTSTRAP_PENDING_REVIEW"` in `promote_case` is the third
  pinning site for the sentinel — see DRIFT NOTE in
  `synthesize.py`)
- `src/metacrucible/__main__.py` (`_emit`, `_now_iso`,
  `BENCHMARK_FILE_NAME`, `_default_state`, `_default_metadata_record`,
  `BOOTSTRAP_PENDING_REVIEW_FIELD`, `STATUS_GENERATED`,
  `_write_optimize_blocked_bundle` / `_write_evaluate_blocked_bundle`
  pattern, `cmd_optimize` preview/apply cutover)
