# Support Claude Code and oh-my-pi first

MetaCrucible initially targets Claude Code and oh-my-pi through a single Claude Code-compatible runtime adapter. oh-my-pi reads the same `.claude/skills/` and `.claude/agents/` locations as Claude Code, so one adapter covers both runtimes in the MVP. A separate `.omp/`-native adapter is deferred until a need arises that the shared layout cannot cover. Codex support remains deferred.
