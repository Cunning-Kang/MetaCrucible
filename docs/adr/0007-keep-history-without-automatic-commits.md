# Keep revision history without automatic commits

MetaCrucible records accepted artifact revisions and optimization history itself, but does not create commits automatically. Baseline creation and optimization use content digests rather than git commits as the source of truth; unrelated dirty files block baseline and optimize by default unless the user explicitly allows them, and any accepted candidate is applied only after the current artifact, envelope, benchmark, and harness identities still match the baseline.
