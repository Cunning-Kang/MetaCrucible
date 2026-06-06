# Enforce minimal write surface and reviewed-case promotion

MetaCrucible writes only to explicit artifact-side metadata, benchmark, and history files, while target execution runs in isolated per-case workspaces constrained by execution boundaries. Generated evaluation cases cannot drive optimization until a human reviewer promotes them in the benchmark from `status:"generated"` to `status:"reviewed"`, assigns a split, and records `reviewed_by` plus `review_note`.
