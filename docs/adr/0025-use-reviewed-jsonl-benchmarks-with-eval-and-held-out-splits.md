# Use reviewed JSONL benchmarks with eval and held-out splits

MetaCrucible benchmarks are strict typed JSONL files: the first record describes benchmark-level settings and subsequent records describe evaluation cases. Baseline creation requires at least one human-reviewed eval case and one human-reviewed held-out case, because optimization needs a selection signal and acceptance needs a regression guard.

**Consequences**

- Empty benchmark files created by `init` are valid containers but not runnable benchmarks.
- Generated cases may be recorded but cannot participate in optimization until a human reviewer promotes them to reviewed cases.
- Acceptance uses a binary gate first: eval must show at least one `FAIL→PASS` and no `PASS→FAIL`; held-out must show no `PASS→FAIL`. Optional scores can add epsilon-based improvement/regression checks but never override binary regressions.
