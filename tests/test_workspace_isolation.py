"""TDD behavior tests for issue #13: workspace isolation / masking / boundary report.

Pins ADR 0031 (\"Pin workspace masking and boundary reporting\"). The
module under test is :mod:`metacrucible.workspace_isolation`. It owns
three related jobs:

  1. ``plan_workspace_mask(source, ...)`` — produce a copy-on-write
     boundary report that classifies every path under ``source`` as
     ``allow`` / ``mask`` / ``deny`` and records the reason. The
     deny set is pinned by ADR 0031: ``.git``, ``.metacrucible``,
     evidence/cache directories, default-denied hidden files, DB
     files, key material, env files, dependency caches, and files
     matching the built-in secret deny rules. An explicit reviewed
     support-file allowlist may include hidden files, but deny rules
     always win.
  2. ``validate_strict_read_paths(boundary)`` — if the execution
     boundary declares ``strict_read_paths: true``, the runtime
     adapter cannot approximate read paths by workspace masking, so
     the case BLOCKS with the pinned ``strict-read-path-unsupported``
     blocker id (ADR 0031).
  3. ``validate_no_isolation(*, confirmed, interactive, env_override)`` —
     the ``--no-isolation`` CLI flag must require an explicit
     ``--confirm-no-isolation`` confirmation, and in non-interactive
     mode the flag must abort unless an explicit env-var override
     (``METACRUCIBLE_ALLOW_NO_ISOLATION=1``) authorizes it. The
     standalone module is callable from any runner; the CLI wires
     argparse and stdin/TTY into the same gate.

The tests cover the four acceptance criteria from issue #13:

  * AC1 — git / .metacrucible / hidden / env / key material / DB /
    dependency cache / secret-pattern files are masked.
  * AC2 — strict read-path enforcement BLOCKS the run.
  * AC3 — ``--no-isolation`` without explicit confirmation BLOCKS.
  * AC4 — non-interactive ``--no-isolation`` aborts unless the
    caller sets the explicit env-var override.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ISOLATION_MODULE = "metacrucible.workspace_isolation"

#: Stable blocker ids the module must emit on each failure mode.
#: These strings are the machine contract: tests, the optimizer
#: pipeline, and downstream automation all branch on them verbatim.
#: Adding a new id is a contract change; renaming an existing id is a
#: breaking change and must be paired with a migration plan.
EXPECTED_BLOCKERS: dict[str, str] = {
    "strict_read_path_unsupported": "workspace-strict-read-path-unsupported",
    "no_isolation_confirmation_required": (
        "workspace-no-isolation-confirmation-required"
    ),
    "no_isolation_non_interactive": "workspace-no-isolation-non-interactive",
}


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _blocker_ids(payload: Any) -> list[str]:
    """Return the list of blocker ids in a result, or empty if none."""
    if not isinstance(payload, dict):
        return []
    blockers = payload.get("blockers", [])
    if not isinstance(blockers, list):
        return []
    out: list[str] = []
    for blocker in blockers:
        if isinstance(blocker, dict) and isinstance(blocker.get("id"), str):
            out.append(blocker["id"])
    return out


def _expect_ok(payload: Any, *, context: str) -> None:
    """Assert ``payload`` is a clean result with no blockers."""
    assert isinstance(payload, dict), (
        f"{context} must return a dict; got {type(payload).__name__}"
    )
    assert payload.get("ok") is True, (
        f"{context} must report ok=True; got payload={payload!r}"
    )
    assert _blocker_ids(payload) == [], (
        f"{context} must not emit blockers; got "
        f"blocker_ids={_blocker_ids(payload)!r}"
    )


def _expect_blocked(payload: Any, *, context: str) -> None:
    """Assert ``payload`` is a blocked result with at least one blocker."""
    assert isinstance(payload, dict), (
        f"{context} must return a dict; got {type(payload).__name__}"
    )
    assert payload.get("ok") is False, (
        f"{context} must report ok=False; got payload={payload!r}"
    )
    assert _blocker_ids(payload), (
        f"{context} must emit at least one blocker; got payload={payload!r}"
    )


def _expect_blocker(
    payload: Any, blocker_id: str, *, context: str = ""
) -> str:
    """Assert ``blocker_id`` is present in ``payload`` blockers; return msg."""
    ids = _blocker_ids(payload)
    assert blocker_id in ids, (
        f"{context} must emit blocker id {blocker_id!r}; "
        f"got blocker_ids={ids!r}"
    )
    for blocker in payload.get("blockers", []):
        if isinstance(blocker, dict) and blocker.get("id") == blocker_id:
            message = blocker.get("message", "")
            assert isinstance(message, str) and message, (
                f"{context} blocker {blocker_id!r} must carry a non-empty "
                f"message; got message={message!r}"
            )
            return message
    return ""  # unreachable; the assert above fails first


def _write(path: Path, content: str = "") -> None:
    """Create ``path`` (parents included) and write ``content``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_source(tmp_path: Path) -> Path:
    """Build a representative canonical source tree for masking tests.

    The tree covers every ADR 0031 deny category so a single helper
    can drive the full AC1 test set:

      - ``.git/`` — git metadata (must mask)
      - ``.metacrucible/`` — metacrucible envelope (must mask)
      - ``.env`` — env file (must mask)
      - ``.hidden.txt`` — default-denied hidden file (must mask)
      - ``secrets/id_rsa`` — key material (must mask)
      - ``data/app.sqlite`` — DB file (must mask)
      - ``node_modules/lib/index.js`` — dependency cache (must mask)
      - ``.pytest_cache/`` — evidence/cache dir (must mask)
      - ``src/main.py`` — clean allow path (must allow)
    """
    source = tmp_path / "source"
    _write(source / ".git" / "HEAD", "ref: refs/heads/main\n")
    _write(source / ".metacrucible" / "envelope.json", "{}\n")
    _write(source / ".env", "API_KEY=plaintext-realtoken-1234567890abcdef\n")
    _write(source / ".hidden.txt", "hidden support file\n")
    _write(source / "secrets" / "id_rsa", "-----BEGIN RSA PRIVATE KEY-----\n")
    _write(source / "data" / "app.sqlite", "binary-db-bytes")
    _write(source / "node_modules" / "lib" / "index.js", "module.exports = 1\n")
    _write(source / ".pytest_cache" / "v" / "cache.json", "{}\n")
    _write(source / "src" / "main.py", "print('hello')\n")
    _write(
        source / "fixtures" / "fake_secret.txt",
        "AKIAIOSFODNN7EXAMPLE\n",  # AWS-shaped fake
    )
    return source


@pytest.fixture(scope="module")
def workspace_isolation() -> Any:
    """Import the workspace_isolation module; fail (red step) if absent."""
    try:
        return importlib.import_module(WORKSPACE_ISOLATION_MODULE)
    except ImportError as exc:
        pytest.fail(
            f"workspace_isolation module {WORKSPACE_ISOLATION_MODULE!r} "
            f"is not implemented yet (Issue #13 red step). Expected "
            f"module exposing: plan_workspace_mask, "
            f"validate_strict_read_paths, validate_no_isolation, "
            f"and the EXPECTED_BLOCKERS ids. ImportError: {exc}"
        )


# --------------------------------------------------------------------------- #
# Module surface                                                              #
# --------------------------------------------------------------------------- #


def test_workspace_isolation_module_exposes_required_surface(
    workspace_isolation: Any,
) -> None:
    """AC1+AC2+AC3+AC4: the public surface must exist (TDD red step gate)."""
    for name in (
        "EXPECTED_BLOCKERS",
        "plan_workspace_mask",
        "validate_strict_read_paths",
        "validate_no_isolation",
        "STRICT_READ_PATH_UNSUPPORTED_BLOCKER",
        "NO_ISOLATION_CONFIRMATION_REQUIRED_BLOCKER",
        "NO_ISOLATION_NON_INTERACTIVE_BLOCKER",
    ):
        assert hasattr(workspace_isolation, name), (
            f"{WORKSPACE_ISOLATION_MODULE!r} must expose {name!r} "
            f"(Issue #13); got attributes "
            f"{sorted(a for a in dir(workspace_isolation) if not a.startswith('_'))!r}"
        )


def test_workspace_isolation_blocker_ids_match_pinned_contract(
    workspace_isolation: Any,
) -> None:
    """AC2+AC3+AC4: every blocker id the tests branch on must exist."""
    blockers = workspace_isolation.EXPECTED_BLOCKERS
    assert isinstance(blockers, dict), (
        f"EXPECTED_BLOCKERS must be a dict; got {type(blockers).__name__}"
    )
    for key, expected in EXPECTED_BLOCKERS.items():
        assert blockers.get(key) == expected, (
            f"EXPECTED_BLOCKERS[{key!r}] must equal {expected!r}; "
            f"got {blockers.get(key)!r}"
        )


# --------------------------------------------------------------------------- #
# AC1 — masking covers git / .metacrucible / hidden / env / key / DB / caches #
# --------------------------------------------------------------------------- #


def _masked_paths(report: dict[str, Any]) -> set[str]:
    """Return the set of relative path strings classified as mask|deny."""
    out: set[str] = set()
    for decision in report.get("mask_decisions", []):
        if not isinstance(decision, dict):
            continue
        decision_kind = decision.get("decision")
        path = decision.get("path")
        if decision_kind in {"mask", "deny"} and isinstance(path, str):
            out.add(path)
    return out


def _allowed_paths(report: dict[str, Any]) -> set[str]:
    """Return the set of relative path strings classified as allow."""
    out: set[str] = set()
    for decision in report.get("mask_decisions", []):
        if not isinstance(decision, dict):
            continue
        if decision.get("decision") == "allow" and isinstance(
            decision.get("path"), str
        ):
            out.add(decision["path"])
    return out


def test_plan_workspace_mask_masks_git_directory(
    workspace_isolation: Any, tmp_path: Path
) -> None:
    """AC1: ``.git`` is masked (ADR 0031: git metadata must not be copied)."""
    source = _make_source(tmp_path / "src")
    report = workspace_isolation.plan_workspace_mask(source)
    assert isinstance(report, dict), (
        f"plan_workspace_mask must return a dict; got {type(report).__name__}"
    )
    _expect_ok(report, context="plan_workspace_mask(canonical source)")
    masked = _masked_paths(report)
    assert ".git" in masked, (
        f".git must be masked (ADR 0031); got masked={sorted(masked)!r}"
    )


def test_plan_workspace_mask_masks_metacrucible_directory(
    workspace_isolation: Any, tmp_path: Path
) -> None:
    """AC1: ``.metacrucible`` is masked (ADR 0031)."""
    source = _make_source(tmp_path / "src")
    report = workspace_isolation.plan_workspace_mask(source)
    masked = _masked_paths(report)
    assert ".metacrucible" in masked, (
        f".metacrucible must be masked (ADR 0031); "
        f"got masked={sorted(masked)!r}"
    )


def test_plan_workspace_mask_masks_hidden_default_denied_files(
    workspace_isolation: Any, tmp_path: Path
) -> None:
    """AC1: default-denied hidden files (e.g. ``.hidden.txt``) are masked.

    ADR 0031: dotfiles and hidden support files are not copied by
    default; explicit reviewed allowlists may include them.
    """
    source = _make_source(tmp_path / "src")
    report = workspace_isolation.plan_workspace_mask(source)
    masked = _masked_paths(report)
    assert ".hidden.txt" in masked, (
        f"default-denied hidden files must be masked (ADR 0031); "
        f"got masked={sorted(masked)!r}"
    )


def test_plan_workspace_mask_masks_env_files(
    workspace_isolation: Any, tmp_path: Path
) -> None:
    """AC1: env files (``.env``) are masked (ADR 0031)."""
    source = _make_source(tmp_path / "src")
    report = workspace_isolation.plan_workspace_mask(source)
    masked = _masked_paths(report)
    assert ".env" in masked, (
        f".env must be masked (ADR 0031); got masked={sorted(masked)!r}"
    )


def test_plan_workspace_mask_masks_key_material(
    workspace_isolation: Any, tmp_path: Path
) -> None:
    """AC1: key material files are masked (ADR 0031)."""
    source = _make_source(tmp_path / "src")
    report = workspace_isolation.plan_workspace_mask(source)
    masked = _masked_paths(report)
    assert any(
        path.endswith("id_rsa") for path in masked
    ), (
        f"RSA key material must be masked (ADR 0031); "
        f"got masked={sorted(masked)!r}"
    )


def test_plan_workspace_mask_masks_db_files(
    workspace_isolation: Any, tmp_path: Path
) -> None:
    """AC1: database files (``.sqlite``) are masked (ADR 0031)."""
    source = _make_source(tmp_path / "src")
    report = workspace_isolation.plan_workspace_mask(source)
    masked = _masked_paths(report)
    assert any(
        path.endswith("app.sqlite") for path in masked
    ), (
        f"SQLite db files must be masked (ADR 0031); "
        f"got masked={sorted(masked)!r}"
    )


def test_plan_workspace_mask_masks_dependency_caches(
    workspace_isolation: Any, tmp_path: Path
) -> None:
    """AC1: dependency caches (e.g. ``node_modules``) are masked (ADR 0031)."""
    source = _make_source(tmp_path / "src")
    report = workspace_isolation.plan_workspace_mask(source)
    masked = _masked_paths(report)
    assert "node_modules" in masked, (
        f"node_modules must be masked as a dependency cache (ADR 0031); "
        f"got masked={sorted(masked)!r}"
    )


def test_plan_workspace_mask_masks_secret_pattern_files(
    workspace_isolation: Any, tmp_path: Path
) -> None:
    """AC1: files matching secret deny rules are masked (ADR 0031)."""
    source = _make_source(tmp_path / "src")
    report = workspace_isolation.plan_workspace_mask(source)
    masked = _masked_paths(report)
    # AWS-shaped key fixture is the canonical example of a
    # high-confidence secret pattern the built-in library catches.
    assert any(
        path.endswith("fake_secret.txt") for path in masked
    ), (
        f"files matching secret deny rules must be masked (ADR 0031); "
        f"got masked={sorted(masked)!r}"
    )


def test_plan_workspace_mask_masks_evidence_or_cache_dirs(
    workspace_isolation: Any, tmp_path: Path
) -> None:
    """AC1: evidence / cache directories are masked (ADR 0031)."""
    source = _make_source(tmp_path / "src")
    report = workspace_isolation.plan_workspace_mask(source)
    masked = _masked_paths(report)
    assert ".pytest_cache" in masked, (
        f"evidence / cache directories must be masked (ADR 0031); "
        f"got masked={sorted(masked)!r}"
    )


def test_plan_workspace_mask_allows_clean_canonical_source(
    workspace_isolation: Any, tmp_path: Path
) -> None:
    """AC1: a clean file under ``src/`` is allowed (not masked)."""
    source = _make_source(tmp_path / "src")
    report = workspace_isolation.plan_workspace_mask(source)
    allowed = _allowed_paths(report)
    assert any(
        path.endswith("main.py") for path in allowed
    ), (
        f"clean canonical source must be allowed; "
        f"got allowed={sorted(allowed)!r}"
    )


def test_plan_workspace_mask_allows_explicit_support_file_override(
    workspace_isolation: Any, tmp_path: Path
) -> None:
    """AC1: an explicit reviewed support-file allowlist includes hidden files.

    ADR 0031: dotfiles and hidden support files are not copied by
    default; explicit reviewed support-file allowlists may include
    them, but deny rules always win.
    """
    source = _make_source(tmp_path / "src")
    report = workspace_isolation.plan_workspace_mask(
        source, support_files=[".hidden.txt"]
    )
    masked = _masked_paths(report)
    allowed = _allowed_paths(report)
    assert ".hidden.txt" not in masked, (
        f"explicit support-file allowlist must permit hidden files; "
        f"got masked={sorted(masked)!r}"
    )
    assert ".hidden.txt" in allowed, (
        f"explicitly allowed support file must appear in allowed set; "
        f"got allowed={sorted(allowed)!r}"
    )


def test_plan_workspace_mask_support_file_override_does_not_override_deny(
    workspace_isolation: Any, tmp_path: Path
) -> None:
    """AC1: deny rules always win, even when a file is on the allowlist.

    ADR 0031: ``.git``, ``.metacrucible``, evidence / cache dirs,
    DB files, key material, env files, dependency caches, and secret
    patterns are never copied. An allowlist entry for one of these
    is a no-op (still masked).
    """
    source = _make_source(tmp_path / "src")
    report = workspace_isolation.plan_workspace_mask(
        source, support_files=[".git", "secrets/id_rsa", ".env"]
    )
    masked = _masked_paths(report)
    assert ".git" in masked, (
        f"deny rules must always win; .git must still be masked even "
        f"with an allowlist entry; got masked={sorted(masked)!r}"
    )
    assert ".env" in masked, (
        f"deny rules must always win; .env must still be masked even "
        f"with an allowlist entry; got masked={sorted(masked)!r}"
    )
    assert any(path.endswith("id_rsa") for path in masked), (
        f"key material must remain masked even with an allowlist entry; "
        f"got masked={sorted(masked)!r}"
    )


def test_plan_workspace_mask_reports_summary_counts(
    workspace_isolation: Any, tmp_path: Path
) -> None:
    """AC1: the report carries a ``summary`` with allowed/masked/denied counts."""
    source = _make_source(tmp_path / "src")
    report = workspace_isolation.plan_workspace_mask(source)
    summary = report.get("summary")
    assert isinstance(summary, dict), (
        f"plan_workspace_mask must report a summary; got {summary!r}"
    )
    for key in ("allowed", "masked", "denied"):
        assert isinstance(summary.get(key), int), (
            f"summary.{key} must be an int; got {summary.get(key)!r}"
        )
    # We added eight distinct masked entries in _make_source; the
    # report must agree or the masking rule is silently broken.
    assert summary["masked"] >= 1, (
        f"summary.masked must be > 0 on the canonical test source; "
        f"got summary={summary!r}"
    )


def test_plan_workspace_mask_decisions_carry_human_reason(
    workspace_isolation: Any, tmp_path: Path
) -> None:
    """AC1: every mask decision carries a non-empty ``reason`` for the reviewer."""
    source = _make_source(tmp_path / "src")
    report = workspace_isolation.plan_workspace_mask(source)
    for decision in report.get("mask_decisions", []):
        if not isinstance(decision, dict):
            continue
        if decision.get("decision") in {"mask", "deny"}:
            reason = decision.get("reason")
            assert isinstance(reason, str) and reason, (
                f"every mask decision must carry a non-empty reason; "
                f"got decision={decision!r}"
            )


# --------------------------------------------------------------------------- #
# AC2 — unsupported strict read-path produces BLOCKED                          #
# --------------------------------------------------------------------------- #


def test_validate_strict_read_paths_blocks_when_strict_true(
    workspace_isolation: Any,
) -> None:
    """AC2: a boundary that declares ``strict_read_paths: true`` BLOCKS."""
    result = workspace_isolation.validate_strict_read_paths(
        {"strict_read_paths": True}
    )
    _expect_blocked(
        result, context="validate_strict_read_paths(strict=True)"
    )
    _expect_blocker(
        result,
        EXPECTED_BLOCKERS["strict_read_path_unsupported"],
        context="strict read-path",
    )


def test_validate_strict_read_paths_passes_when_strict_false(
    workspace_isolation: Any,
) -> None:
    """AC2 (positive): ``strict_read_paths: false`` is a normal case."""
    result = workspace_isolation.validate_strict_read_paths(
        {"strict_read_paths": False}
    )
    _expect_ok(result, context="validate_strict_read_paths(strict=False)")


def test_validate_strict_read_paths_passes_when_strict_missing(
    workspace_isolation: Any,
) -> None:
    """AC2 (positive): boundary without ``strict_read_paths`` is normal."""
    result = workspace_isolation.validate_strict_read_paths(
        {"allowed_tools": ["Bash"]}
    )
    _expect_ok(result, context="validate_strict_read_paths(no key)")


def test_validate_strict_read_paths_passes_when_boundary_none(
    workspace_isolation: Any,
) -> None:
    """AC2 (positive): a missing boundary is a normal (non-blocking) case."""
    result = workspace_isolation.validate_strict_read_paths(None)
    _expect_ok(result, context="validate_strict_read_paths(None)")


def test_validate_strict_read_paths_rejects_non_mapping(
    workspace_isolation: Any,
) -> None:
    """AC2 (defensive): a non-mapping boundary input BLOCKS.

    The whole point of the gate is to refuse a strict declaration; a
    non-mapping input is a type error and must not silently pass.
    """
    result = workspace_isolation.validate_strict_read_paths("not-a-mapping")
    _expect_blocked(
        result, context="validate_strict_read_paths(string)"
    )


# --------------------------------------------------------------------------- #
# AC3 / AC4 — --no-isolation gate                                              #
# --------------------------------------------------------------------------- #


def test_validate_no_isolation_blocks_without_confirmation(
    workspace_isolation: Any,
) -> None:
    """AC3: ``--no-isolation`` without explicit confirmation BLOCKS."""
    result = workspace_isolation.validate_no_isolation(
        confirmed=False, interactive=True
    )
    _expect_blocked(result, context="validate_no_isolation(no confirm)")
    _expect_blocker(
        result,
        EXPECTED_BLOCKERS["no_isolation_confirmation_required"],
        context="no-isolation confirmation missing",
    )


def test_validate_no_isolation_passes_with_confirmation(
    workspace_isolation: Any,
) -> None:
    """AC3 (positive): explicit confirmation is honored."""
    result = workspace_isolation.validate_no_isolation(
        confirmed=True, interactive=True
    )
    _expect_ok(
        result, context="validate_no_isolation(confirmed + interactive)"
    )


def test_validate_no_isolation_blocks_non_interactive_without_env_override(
    workspace_isolation: Any,
) -> None:
    """AC4: non-interactive ``--no-isolation`` without override aborts."""
    result = workspace_isolation.validate_no_isolation(
        confirmed=True, interactive=False, env_override=None
    )
    _expect_blocked(
        result, context="validate_no_isolation(non-interactive, no env)"
    )
    _expect_blocker(
        result,
        EXPECTED_BLOCKERS["no_isolation_non_interactive"],
        context="non-interactive without env override",
    )


def test_validate_no_isolation_passes_non_interactive_with_env_override(
    workspace_isolation: Any,
) -> None:
    """AC4 (positive): env override authorizes a non-interactive call."""
    result = workspace_isolation.validate_no_isolation(
        confirmed=True,
        interactive=False,
        env_override="1",
    )
    _expect_ok(
        result,
        context="validate_no_isolation(non-interactive + env override)",
    )


def test_validate_no_isolation_env_override_does_not_replace_confirmation(
    workspace_isolation: Any,
) -> None:
    """AC4 (defensive): the env override cannot bypass the confirm gate.

    The env override explicitly authorizes running with isolation
    disabled in non-interactive mode, but it does NOT replace the
    human-confirmation requirement. A non-interactive caller that
    forgets ``--confirm-no-isolation`` still gets the confirmation
    blocker.
    """
    result = workspace_isolation.validate_no_isolation(
        confirmed=False,
        interactive=False,
        env_override="1",
    )
    _expect_blocked(
        result, context="validate_no_isolation(no confirm, env present)"
    )
    _expect_blocker(
        result,
        EXPECTED_BLOCKERS["no_isolation_confirmation_required"],
        context="env override cannot bypass confirmation",
    )


# --------------------------------------------------------------------------- #
# CLI integration — --no-isolation / --confirm-no-isolation                   #
# --------------------------------------------------------------------------- #


def _run_metacrucible(
    argv: list[str], *, cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Invoke ``python -m metacrucible`` with ``argv`` inside ``cwd``."""
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    # Force a non-interactive stdin (pipe) so the test cannot hang
    # on a TTY prompt the runner doesn't expect.
    full_env.setdefault("PYTHONUNBUFFERED", "1")
    return subprocess.run(
        [sys.executable, "-m", "metacrucible", *argv],
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=full_env,
        stdin=subprocess.DEVNULL,
    )


def test_init_no_isolation_flag_is_recognized(tmp_path: Path) -> None:
    """``init --no-isolation`` must be recognized by argparse."""
    workspace = tmp_path / "ws-no-isolation"
    workspace.mkdir(parents=True, exist_ok=True)
    result = _run_metacrucible(
        ["init", str(workspace), "--no-isolation", "--confirm-no-isolation"],
        cwd=REPO_ROOT,
    )
    assert "unrecognized arguments" not in result.stderr, (
        f"`metacrucible init --no-isolation` is not a registered flag yet; "
        f"got stderr={result.stderr!r}"
    )


def test_init_no_isolation_without_confirmation_block_in_human_output(
    tmp_path: Path,
) -> None:
    """AC3 (CLI): ``init --no-isolation`` without --confirm exits nonzero.

    The human output must mention the ``no-isolation-confirmation-required``
    blocker id so an operator can map the failure to its fix.
    """
    workspace = tmp_path / "ws-no-iso-no-confirm"
    workspace.mkdir(parents=True, exist_ok=True)
    result = _run_metacrucible(
        ["init", str(workspace), "--no-isolation"], cwd=REPO_ROOT
    )
    assert result.returncode != 0, (
        f"`init --no-isolation` without --confirm must exit nonzero; "
        f"got rc=0 stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    combined = f"{result.stdout}\n{result.stderr}"
    assert (
        EXPECTED_BLOCKERS["no_isolation_confirmation_required"]
        in combined
    ), (
        f"human output must mention the "
        f"{EXPECTED_BLOCKERS['no_isolation_confirmation_required']!r} "
        f"blocker id (Issue #13 AC3); "
        f"got stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_init_no_isolation_aborts_in_non_interactive(
    tmp_path: Path,
) -> None:
    """AC4 (CLI): ``init --no-isolation --confirm-no-isolation`` in non-interactive.

    Subprocess runs with ``stdin=DEVNULL`` so the test simulates a
    non-interactive context. Without the explicit env override, the
    runner must abort with the ``no-isolation-non-interactive``
    blocker.
    """
    workspace = tmp_path / "ws-no-iso-noninteractive"
    workspace.mkdir(parents=True, exist_ok=True)
    result = _run_metacrucible(
        ["init", str(workspace), "--no-isolation", "--confirm-no-isolation"],
        cwd=REPO_ROOT,
    )
    assert result.returncode != 0, (
        f"non-interactive `init --no-isolation` must exit nonzero; "
        f"got rc=0 stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    combined = f"{result.stdout}\n{result.stderr}"
    assert (
        EXPECTED_BLOCKERS["no_isolation_non_interactive"] in combined
    ), (
        f"non-interactive output must mention the "
        f"{EXPECTED_BLOCKERS['no_isolation_non_interactive']!r} "
        f"blocker id (Issue #13 AC4); "
        f"got stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_init_no_isolation_with_env_override_succeeds(
    tmp_path: Path,
) -> None:
    """AC4 (positive CLI): env override + confirm authorizes non-interactive.

    Setting ``METACRUCIBLE_ALLOW_NO_ISOLATION=1`` plus
    ``--confirm-no-isolation`` lets a non-interactive caller bypass
    the abort gate and let ``init`` do the no-isolation work.
    """
    workspace = tmp_path / "ws-no-iso-env-ok"
    workspace.mkdir(parents=True, exist_ok=True)
    result = _run_metacrucible(
        ["init", str(workspace), "--no-isolation", "--confirm-no-isolation"],
        cwd=REPO_ROOT,
        env={"METACRUCIBLE_ALLOW_NO_ISOLATION": "1"},
    )
    assert result.returncode == 0, (
        f"`init --no-isolation --confirm-no-isolation` with env override "
        f"must exit 0; got rc={result.returncode} "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


# --------------------------------------------------------------------------- #
# argv_normalize integration — strict_read_paths is enforced end-to-end        #
# --------------------------------------------------------------------------- #


def test_normalize_execution_boundary_blocks_strict_read_paths(
    workspace_isolation: Any,
) -> None:
    """AC2 (integration): ``normalize_execution_boundary`` blocks strict.

    The boundary normalizer historically ignored unknown keys; the
    Issue #13 wiring plugs :func:`validate_strict_read_paths` into
    the same end-to-end normalize step so a case that declares
    ``strict_read_paths: true`` is BLOCKED even when its
    ``allowed_tools`` and ``target_commands`` are otherwise valid.
    """
    argv_normalize = importlib.import_module("metacrucible.argv_normalize")
    result = argv_normalize.normalize_execution_boundary(
        {
            "allowed_tools": ["Bash"],
            "target_commands": [["ls", "-la"]],
            "strict_read_paths": True,
        }
    )
    _expect_blocked(result, context="normalize(strict_read_paths=True)")
    _expect_blocker(
        result,
        EXPECTED_BLOCKERS["strict_read_path_unsupported"],
        context="normalize strict read-path",
    )
    allowed_strings = result.get("allowed_strings", [])
    assert allowed_strings == [], (
        f"a blocked normalize must not leak allowed_strings; "
        f"got {allowed_strings!r}"
    )


def test_normalize_execution_boundary_passes_without_strict_read_paths(
    workspace_isolation: Any,
) -> None:
    """AC2 (integration, positive): no strict_read_paths key — clean pass.

    Confirms the wiring is additive: a boundary without
    ``strict_read_paths`` (the common case) keeps the existing
    behavior end to end.
    """
    argv_normalize = importlib.import_module("metacrucible.argv_normalize")
    result = argv_normalize.normalize_execution_boundary(
        {
            "allowed_tools": ["Bash"],
            "target_commands": [["ls", "-la"]],
        }
    )
    _expect_ok(result, context="normalize(no strict_read_paths)")
    assert "Bash(ls -la)" in result.get("allowed_strings", []), (
        f"clean boundary must still produce the Bash(...) allow string; "
        f"got {result.get('allowed_strings')!r}"
    )
