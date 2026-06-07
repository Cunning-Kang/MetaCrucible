---
name: metacrucible
description: SKELETON wrapper around the `metacrucible` CLI stub. Loads the workbench CLI into an agent runtime; complete UX is tracked separately.
---

# `metacrucible` skill (SKELETON)

> **Status: SKELETON.** This wrapper is an explicit stub for Issue #3.
> It exists to let an agent runtime load the `metacrucible` CLI and
> forward user requests to it. The complete UX (rich guidance,
> subcommand discovery, error mapping, structured tool inputs) is
> **tracked separately** and is **out of scope** for this skeleton.

## What this wrapper does

This Skill is a thin wrapper that hands work to the `metacrucible`
console script (the package's `__main__:main` entry point). The Skill
itself implements no optimization or evaluation logic — it only
forwards calls to the CLI stub.

## Usage

Invoke the CLI stub through the Skill by running the `metacrucible`
console script (or the equivalent `python -m metacrucible` form):

```sh
# Show the help banner; this is the supported entry point of the stub.
metacrucible --help

# Module form, useful when the console script is not on PATH.
python -m metacrucible --help
```

The CLI stub currently supports:

- `metacrucible --help` — print the usage banner.
- `metacrucible --version` — print the package version.
- bare `metacrucible` — print a short banner and a pointer to `--help`.

All other subcommands (`review`, `bootstrap`, `optimize`, `synthesize`,
`inspect`, `init`, `baseline create`, `evaluate`) are **not implemented
yet** in the stub and will be added in later waves per `docs/roadmap.md`.

## Scope

This Skill wrapper is intentionally minimal:

- It does not parse the user's request — the agent runtime should.
- It does not shape arguments into CLI flags — the agent runtime
  should pass them through verbatim.
- It does not interpret exit codes — the CLI's stable exit-code
  contract is documented in ADR 0035.

The complete UX (request routing, structured tool inputs, error
recovery, rich examples, evaluation-case handoff) is tracked
separately and is **out of scope** for Issue #3.
