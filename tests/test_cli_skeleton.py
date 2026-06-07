"""Tests for Issue #3: CLI ``--help`` surface.

These tests pin the public help behavior asserted by Issue #3:

  - ``metacrucible --help`` exits 0 and prints a usable usage banner.
  - ``python -m metacrucible --help`` behaves the same as the console
    script entry point.
  - The help output advertises the project name and the ``--version``
    flag so the existing skeleton stays discoverable.

The implementation under test lives in ``src/metacrucible/__main__.py``
and is already wired in by Issue #2 — these tests lock the help path
in place so a future change cannot silently break it.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_help(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "metacrucible", *argv],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


def test_help_exits_zero() -> None:
    """``metacrucible --help`` must terminate with exit code 0."""
    result = _run_help(["--help"])
    assert result.returncode == 0, (
        f"`metacrucible --help` failed (rc={result.returncode}):\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_help_prints_usage_block() -> None:
    """Help output must look like a real argparse usage block."""
    result = _run_help(["--help"])
    assert "usage:" in result.stdout, (
        f"--help output must contain a 'usage:' line; got {result.stdout!r}"
    )
    assert "metacrucible" in result.stdout, (
        f"--help output must mention the program name 'metacrucible'; "
        f"got {result.stdout!r}"
    )


def test_help_advertises_version_flag() -> None:
    """``--version`` must appear in the help output so the flag is discoverable."""
    result = _run_help(["--help"])
    assert "--version" in result.stdout, (
        f"--help output must list the --version flag; got {result.stdout!r}"
    )


def test_help_describes_project_purpose() -> None:
    """The help description should mention MetaCrucible's purpose, not just a stub."""
    result = _run_help(["--help"])
    lowered = result.stdout.lower()
    assert "metacrucible" in lowered, (
        f"--help output must mention 'metacrucible' in the description; "
        f"got {result.stdout!r}"
    )
    # The description string is wired into argparse by `_build_parser`;
    # this assertion guards against a future regression that drops it.
    assert "optimization" in lowered or "evaluation" in lowered, (
        f"--help output should describe the workbench purpose; "
        f"got {result.stdout!r}"
    )


def test_help_writes_to_stdout_not_stderr() -> None:
    """Argparse writes --help to stdout; the CLI must not redirect it."""
    result = _run_help(["--help"])
    assert result.stdout, "--help must produce stdout output"
    # argparse --help itself only writes to stdout; a stray print() to
    # stderr would suggest the help path has been hijacked.
    assert "usage:" not in result.stderr, (
        f"--help banner must not be duplicated on stderr; "
        f"got stderr={result.stderr!r}"
    )


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_flag_aliases_both_work(flag: str) -> None:
    """Both ``-h`` and ``--help`` must exit 0 and emit the usage block."""
    result = _run_help([flag])
    assert result.returncode == 0, (
        f"`metacrucible {flag}` failed (rc={result.returncode}):\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    assert "usage:" in result.stdout, (
        f"`metacrucible {flag}` must print a usage block; "
        f"got {result.stdout!r}"
    )
