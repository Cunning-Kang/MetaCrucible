# Ship CLI first with a Skill wrapper

MetaCrucible's MVP exposes a CLI as the primary execution surface and a Skill wrapper as the agent-facing UX. The CLI keeps optimization runs testable and reproducible, while the wrapper lets agent runtimes drive the same workflow without making the Skill itself the source of truth.
