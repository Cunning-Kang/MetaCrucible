# Use receipts and versioned evidence bundles

MetaCrucible records each baseline, evaluation, and optimization run with a thin `receipt.json` as the evidence entrypoint. The receipt binds artifact, benchmark, envelope, evaluation harness, optimizer harness, runtime adapter, model configuration, and result identities by stable hashes, while detailed normalized evidence lives in adjacent JSON/JSONL files.

**Consequences**

- Evidence bundles contain `receipt.json`, `summary.json`, normalized event streams, trajectory digests, checks, judgments, reviews, redaction reports, and optional raw evidence only when explicitly retained.
- Optimization runs also contain per-round receipts and SkillOpt-style round artifacts such as optimizer context, edit suggestions, ranked edits, merged replacements, apply reports, and candidate snapshots.
- Baseline validity is tied to evaluation-affecting hashes; optimizer changes rekey proposal/cache provenance but do not invalidate baselines.
