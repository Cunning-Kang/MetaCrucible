# Store light artifact history in the repo and heavy evidence in user state

MetaCrucible stores lightweight per-artifact revision history beside the artifact, but stores heavy evidence bundles, run indexes, and evaluation caches under `~/.metacrucible/`. This keeps repo-side audit records small and shareable while keeping transcripts, normalized events, cache entries, and redaction-sensitive evidence out of the working tree unless explicitly exported.
