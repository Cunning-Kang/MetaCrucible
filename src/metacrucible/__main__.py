"""Console entrypoint for the ``metacrucible`` command.

Exposes :func:`main` as the ``metacrucible`` console script (declared
in ``pyproject.toml`` under ``[project.scripts]``) and is also invokable
as ``python -m metacrucible``. This module intentionally exposes only
the skeleton surface (``--help`` / ``--version``); the MVP subcommands
from ADR 0035 (``review``, ``bootstrap``, ``optimize``, ``synthesize``,
``inspect``, ``init``, ``baseline create``, ``evaluate``) land in later
waves per ``docs/roadmap.md``.
"""
from __future__ import annotations

import argparse
import sys
from typing import Sequence

from . import __version__

__all__ = ["main"]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="metacrucible",
        description=(
            "MetaCrucible: a workbench for improving portable agent "
            "capabilities through repeatable optimization, evaluation, "
            "and review loops."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"metacrucible {__version__}",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the ``metacrucible`` console script.

    Returns the process exit code: ``0`` for the no-arg banner and
    ``--help``/``--version`` paths. Argparse's ``--version`` and
    ``--help`` actions raise ``SystemExit`` to terminate; we catch
    those here so callers (including the console-script wrapper and
    unit tests) get a clean integer return value.
    """
    parser = _build_parser()
    args_list = list(sys.argv[1:] if argv is None else argv)
    if not args_list:
        # Bare invocation: print a short banner so the CLI is useful
        # out of the box even before the MVP subcommands land.
        print(f"metacrucible {__version__}")
        print(
            "A workbench for improving portable agent capabilities. "
            "Run 'metacrucible --help' for usage."
        )
        return 0
    try:
        parser.parse_args(args_list)
    except SystemExit as exc:
        code = exc.code
        # argparse uses None for a clean --help/--version exit.
        return 0 if code is None else int(code)
    return 0


if __name__ == "__main__":
    sys.exit(main())
