# Build benchmarks from reviewed human and generated cases

MetaCrucible benchmarks may include reviewed, generated, and disabled evaluation cases in a strict typed JSONL file. Generated cases can be stored for audit and future use, but they cannot drive optimization until a human reviewer promotes them to `status:"reviewed"`, assigns `split:"eval"` or `split:"held_out"`, and records review provenance; disabled cases remain in the benchmark for audit but are skipped by evaluation and optimization.
