# MetaCrucible MVP Roadmap

Five waves. Each wave lands independently and later waves build on earlier ones.

## W1. Core skeleton

- `pyproject.toml`, `src/metacrucible/`, `tests/`, `skills/metacrucible/`.
- Artifact parser: frontmatter + body, mutable range identification.
- Runtime adapter for the Claude Code / oh-my-pi shared `.claude/` layout.
- Storage layer: `.metacrucible/` per artifact, `~/.metacrucible/` global.
- Envelope and state schema foundations.

## W2. Evaluation

- Static Review implementing the Darwin 9-dimension rubric.
- Execution Evaluation: deterministic rule checks plus two independent LLM judges.
- Benchmark loader with envelope status and `BOOTSTRAP_PENDING_REVIEW` sentinel.
- `metacrucible review` and `metacrucible bootstrap` commands.

## W3. Optimization loop

- Optimization Round implementation: propose, apply, evaluate, Acceptance Decision.
- Patch Revision as the default revision mode.
- Acceptance logic: strict Eval Split improvement and no Held-Out Split regression.
- Stopping Conditions: max rounds, consecutive non-acceptance, sub-threshold improvement, runtime cap.
- `metacrucible optimize` command.

## W4. Synthesis and inspect

- `metacrucible synthesize` command.
- Draft artifact generation, baseline, generated evaluation cases, human review gate, then automatic optimization through the same loop as W3.
- `metacrucible inspect` command for revision history, acceptance decisions, and evidence index.

## W5. Testing, documentation, and release

- Unit tests for pure logic (parser, patcher, acceptance, rule checks).
- Integration tests using real LLMs against small benchmarks.
- CI replay harness for recorded LLM responses.
- Smoke tests for CLI usability.
- README, CONTRIBUTING, and usage docs.
- PyPI release.
