# Use rule checks and independent control-plane LLM judgments

MetaCrucible's MVP execution evaluation supports deterministic rule checks and LLM judgments. Judgments and static reviews are control-plane calls through configured Anthropic or OpenAI-compatible providers, independent from the target agent runtime; repeated or independent judgments can be configured for stricter median-style scoring, but deterministic checks remain the cheapest and preferred judgment method when available.
