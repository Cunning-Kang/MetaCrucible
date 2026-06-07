"""Pin workspace masking and boundary reporting (ADR 0031, Issue #13).

ADR 0031 ("Pin workspace masking and boundary reporting") pins the
copy-on-write workspace algorithm: first build a masked prepared
workspace from the canonical source and reviewed support files, then
create an independent per-case workspace and overlay reviewed
fixtures after masking. Workspace masking, temporary home directories,
and sanitized environments are the MVP enforcement mechanism for read
boundaries, and runtime-level gaps are reported explicitly rather
than silently downgraded.

This module is the focused workspace helper for ADR 0031 and Issue
#13. It owns three related jobs:

  1. :func:`plan_workspace_mask` — produce a copy-on-write boundary
     report that classifies every path under ``source`` as
     ``allow`` / ``mask`` / ``deny`` and records the reason. The
     deny set is pinned by ADR 0031: ``.git``, ``.metacrucible``,
     evidence / cache directories, default-denied hidden files, DB
     files, key material, env files, dependency caches, and files
     matching the built-in secret deny rules. An explicit reviewed
     support-file allowlist may include hidden files, but deny rules
     always win.
  2. :func:`validate_strict_read_paths` — if the execution boundary
     declares ``strict_read_paths: true``, the runtime adapter
     cannot approximate read paths by workspace masking, so the case
     BLOCKS with the pinned ``strict-read-path-unsupported`` blocker
     id.
  3. :func:`validate_no_isolation` — the ``--no-isolation`` CLI flag
     must require an explicit ``--confirm-no-isolation`` confirmation,
     and in non-interactive mode the flag must abort unless the caller
     sets the explicit env-var override
     (``METACRUCIBLE_ALLOW_NO_ISOLATION=1``).

Result shape
------------

All three public functions return a dict of the same shape used by
:mod:`metacrucible.argv_normalize` and the ``init --check`` validator:

  - ``ok`` (bool) — ``True`` iff validation passes.
  - ``blockers`` (list[dict]) — empty when ``ok`` is ``True``;
    otherwise each entry is ``{"id": <blocker_id>, "message": <human>}``.
  - ``mask_decisions`` (list[dict]) — only on
    :func:`plan_workspace_mask` success. Each entry is
    ``{"path", "decision", "reason"}``.
  - ``summary`` (dict) — only on :func:`plan_workspace_mask` success.
    Carries ``{"allowed": int, "masked": int, "denied": int}`` so a
    reviewer can scan the report at a glance.

References
----------
- ADR 0031 (workspace masking and boundary reporting).
- ADR 0028 (portable execution boundary normalization).
- Issue #13 acceptance criteria.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping, Sequence

__all__ = [
    "EXPECTED_BLOCKERS",
    "STRICT_READ_PATH_UNSUPPORTED_BLOCKER",
    "NO_ISOLATION_CONFIRMATION_REQUIRED_BLOCKER",
    "NO_ISOLATION_NON_INTERACTIVE_BLOCKER",
    "NO_ISOLATION_ENV_OVERRIDE",
    "plan_workspace_mask",
    "validate_strict_read_paths",
    "validate_no_isolation",
]


# --------------------------------------------------------------------------- #
# Stable blocker ids                                                          #
# --------------------------------------------------------------------------- #
#
# Machine contract: the optimizer pipeline, the runtime adapter, and
# downstream automation branch on these exact strings. Adding a new
# id is a contract change; renaming an existing id is a breaking
# change and must be paired with a migration plan.

STRICT_READ_PATH_UNSUPPORTED_BLOCKER: str = (
    "workspace-strict-read-path-unsupported"
)
NO_ISOLATION_CONFIRMATION_REQUIRED_BLOCKER: str = (
    "workspace-no-isolation-confirmation-required"
)
NO_ISOLATION_NON_INTERACTIVE_BLOCKER: str = (
    "workspace-no-isolation-non-interactive"
)


EXPECTED_BLOCKERS: dict[str, str] = {
    "strict_read_path_unsupported": STRICT_READ_PATH_UNSUPPORTED_BLOCKER,
    "no_isolation_confirmation_required": (
        NO_ISOLATION_CONFIRMATION_REQUIRED_BLOCKER
    ),
    "no_isolation_non_interactive": NO_ISOLATION_NON_INTERACTIVE_BLOCKER,
}


# --------------------------------------------------------------------------- #
# Env-var override                                                            #
# --------------------------------------------------------------------------- #

#: Explicit out-of-band env-var override that authorizes a
#: non-interactive ``--no-isolation`` invocation. The override is an
#: audit-friendly escape hatch: the caller must set the variable on
#: the wrapper command so the override shows up in the process
#: environment and the run log. The override does NOT replace the
#: ``--confirm-no-isolation`` human-confirmation flag.
NO_ISOLATION_ENV_OVERRIDE: str = "METACRUCIBLE_ALLOW_NO_ISOLATION"


# --------------------------------------------------------------------------- #
# ADR 0031 default deny rules                                                  #
# --------------------------------------------------------------------------- #
#
# Every entry in this section is part of the MVP contract: these
# paths are never copied into a prepared workspace, regardless of
# the support-file allowlist.

#: Top-level directory names that must be masked. ``.git``,
#: ``.metacrucible``, evidence / cache directories (``__pycache__``,
#: ``.pytest_cache``, ``.cache``), dependency caches
#: (``node_modules``), and virtualenvs / build outputs (``.venv``,
#: ``.tox``, ``dist``, ``build``) are all part of the deny set.
DENY_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        ".metacrucible",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".cache",
        ".venv",
        ".tox",
        "dist",
        "build",
    }
)

#: File basenames that must be masked. These are the canonical
#: env-file names the MVP always denies, even on the support-file
#: allowlist.
DENY_HIDDEN_ENV_FILES: frozenset[str] = frozenset(
    {
        ".env",
        ".envrc",
    }
)

#: Key-material filenames the MVP always denies. The list mirrors
#: the common SSH / PKI basenames a reviewer recognizes.
DENY_KEY_MATERIAL_BASENAMES: frozenset[str] = frozenset(
    {
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
    }
)

#: File extensions that mark a file as key material or a PKCS#12 /
#: PEM bundle. Always denied.
DENY_KEY_MATERIAL_EXTENSIONS: tuple[str, ...] = (
    ".pem",
    ".key",
    ".p12",
    ".pfx",
)

#: Database file extensions the MVP always denies.
DENY_DB_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".sqlite",
        ".sqlite3",
        ".db",
        ".db3",
        ".s3db",
    }
)

#: Substrings in a filename that mark it as a secret-pattern file.
#: The set is intentionally narrow: the names below are
#: high-confidence indicators a reviewer recognizes on sight. A
#: generic name like ``recipe.txt`` is unaffected.
DENY_SECRET_FILENAME_SUBSTRINGS: tuple[str, ...] = (
    "secret",
    "credential",
    "private_key",
    "apikey",
    "access_key",
)


# --------------------------------------------------------------------------- #
# Internal: result helpers                                                    #
# --------------------------------------------------------------------------- #


def _blocker(blocker_id: str, message: str) -> dict[str, str]:
    """Return a single ``{id, message}`` blocker entry."""
    return {"id": blocker_id, "message": message}


def _ok_result(extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build a clean ``ok=True`` result, optionally with extra payload."""
    result: dict[str, Any] = {"ok": True, "blockers": []}
    if extra:
        result.update(extra)
    return result


def _blocked_result(
    blockers: list[dict[str, str]],
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a blocked ``ok=False`` result, optionally with extra payload."""
    result: dict[str, Any] = {"ok": False, "blockers": list(blockers)}
    if extra:
        result.update(extra)
    return result


# --------------------------------------------------------------------------- #
# Internal: path classification                                               #
# --------------------------------------------------------------------------- #


def _is_in_deny_dir(relpath: str) -> bool:
    """True if any path component of ``relpath`` is in :data:`DENY_DIR_NAMES`."""
    parts = relpath.replace("\\", "/").split("/")
    return any(part in DENY_DIR_NAMES for part in parts)


def _is_key_material(name: str) -> bool:
    """True if ``name`` matches the key-material deny rules."""
    if name in DENY_KEY_MATERIAL_BASENAMES:
        return True
    return any(name.endswith(ext) for ext in DENY_KEY_MATERIAL_EXTENSIONS)


def _is_db_file(name: str) -> bool:
    """True if ``name`` has a database extension."""
    return any(name.endswith(ext) for ext in DENY_DB_EXTENSIONS)


def _is_secret_pattern(name: str) -> bool:
    """True if ``name`` matches the high-confidence secret-pattern rules."""
    lowered = name.lower()
    return any(pat in lowered for pat in DENY_SECRET_FILENAME_SUBSTRINGS)


def _is_deny_name(name: str) -> bool:
    """True if ``name`` is denied regardless of where it appears.

    Combines env files, key material, db files, and secret patterns.
    These rules always win — they are never overridden by the
    support-file allowlist.
    """
    if name in DENY_HIDDEN_ENV_FILES:
        return True
    if _is_key_material(name):
        return True
    if _is_db_file(name):
        return True
    if _is_secret_pattern(name):
        return True
    return False


def _classify_top_level(
    relpath: str, *, name: str, is_dir: bool
) -> tuple[str, str] | None:
    """Classify ``relpath`` and return ``(decision, reason)`` or ``None``.

    Returns ``None`` when the path needs the parent-dir / hidden /
    allowlist rules to be applied by :func:`plan_workspace_mask`.
    """
    # 1. Hard deny rules — always mask, never overridden.
    if is_dir and name in DENY_DIR_NAMES:
        return (
            "mask",
            (
                f"directory {name!r} is on the ADR 0031 deny list "
                f"(git / .metacrucible / evidence / cache / "
                f"dependency cache / build output)"
            ),
        )
    if name in DENY_HIDDEN_ENV_FILES:
        return (
            "mask",
            f"file {name!r} is on the ADR 0031 env-file deny list",
        )
    if _is_key_material(name):
        return (
            "mask",
            f"file {name!r} matches the ADR 0031 key-material deny rule",
        )
    if _is_db_file(name):
        return (
            "mask",
            f"file {name!r} matches the ADR 0031 database-file deny rule",
        )
    if _is_secret_pattern(name):
        return (
            "mask",
            f"file {name!r} matches the ADR 0031 secret-pattern deny rule",
        )
    # 2. Hidden default-deny: any other dotfile is masked unless an
    #    explicit reviewed support-file allowlist includes it. The
    #    special names above are reported with their specific reason;
    #    generic dotfiles get a default-hidden reason.
    if name.startswith("."):
        return (
            "mask",
            f"hidden file {name!r} is default-denied by ADR 0031",
        )
    return None


# --------------------------------------------------------------------------- #
# Public: plan_workspace_mask                                                 #
# --------------------------------------------------------------------------- #


def plan_workspace_mask(
    source: Path | str,
    *,
    support_files: Sequence[str] | None = None,
    allowlist: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Plan copy-on-write masking; return a boundary report.

    The function does not touch the filesystem: it walks ``source``
    once and emits a list of decisions a reviewer can sign off on.
    The MVP runner is expected to consume the report and copy each
    ``allow`` path while skipping each ``mask`` / ``deny`` path.

    Parameters
    ----------
    source:
        Canonical source root to scan. Must be an existing directory.
    support_files:
        Optional list of repo-relative path strings the caller has
        explicitly reviewed and wants included. Per ADR 0031 the
        allowlist may include hidden files, but deny rules always
        win: an entry on the allowlist that is also a deny rule is a
        no-op.
    allowlist:
        Reserved for future use; accepted in the public signature so
        a future ADR revision can add an allowlist channel without
        breaking callers. Currently ``support_files`` is the MVP
        channel.

    Returns
    -------
    dict
        Standard result shape with ``ok=True``, ``blockers=[]``,
        ``mask_decisions=[...]`` and ``summary={allowed, masked,
        denied}``. A missing source dir yields an empty report rather
        than a blocker (the planning step is non-destructive; the
        downstream copy step is where a missing source should fail).
    """
    source_path = Path(source)
    support_set: set[str] = set(support_files or ())
    decisions: list[dict[str, str]] = []
    summary = {"allowed": 0, "masked": 0, "denied": 0}
    if not source_path.is_dir():
        return _ok_result(
            extra={"mask_decisions": list(decisions), "summary": dict(summary)}
        )
    # Use rglob('*') so the report includes both files and
    # directories; the boundary reporting is the canonical-source
    # surface a reviewer audits.
    for entry in sorted(source_path.rglob("*")):
        if entry == source_path:
            continue
        relpath = entry.relative_to(source_path).as_posix()
        name = entry.name
        is_dir = entry.is_dir()
        # 1. Files inside a denied directory are masked by the
        #    parent rule; the parent itself was reported on its own
        #    iteration. This is the "deny rules always win" property
        #    ADR 0031 requires.
        if _is_in_deny_dir(relpath):
            decisions.append(
                {
                    "path": relpath,
                    "decision": "mask",
                    "reason": (
                        f"path is inside a denied directory "
                        f"(ADR 0031 parent-rule)"
                    ),
                }
            )
            summary["masked"] += 1
            continue
        # 2. Apply top-level rules.
        classification = _classify_top_level(
            relpath, name=name, is_dir=is_dir
        )
        if classification is None:
            decision, reason = "allow", "path is on the allow list"
        else:
            decision, reason = classification
        # 3. Explicit reviewed support-file allowlist may promote a
        #    *generic* hidden file from mask -> allow. Deny rules
        #    always win, so we only honor the allowlist for hidden
        #    files that are NOT on the hard-deny list.
        if (
            decision == "mask"
            and relpath in support_set
            and name.startswith(".")
            and name not in DENY_HIDDEN_ENV_FILES
            and not _is_key_material(name)
            and not _is_db_file(name)
            and not _is_secret_pattern(name)
            and name not in DENY_DIR_NAMES
        ):
            decision = "allow"
            reason = (
                f"hidden file {name!r} is on the explicit reviewed "
                f"support-file allowlist (ADR 0031)"
            )
        if decision == "allow":
            summary["allowed"] += 1
        elif decision == "mask":
            summary["masked"] += 1
        else:
            summary["denied"] += 1
        decisions.append(
            {
                "path": relpath,
                "decision": decision,
                "reason": reason,
            }
        )
    return _ok_result(
        extra={"mask_decisions": list(decisions), "summary": dict(summary)}
    )


# --------------------------------------------------------------------------- #
# Public: validate_strict_read_paths                                          #
# --------------------------------------------------------------------------- #


def validate_strict_read_paths(
    boundary: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Block boundaries that declare ``strict_read_paths: true``.

    ADR 0031: Claude Code read-path limits are reported as warnings
    when approximated by workspace masking. If a case declares
    strict read-path enforcement, the Claude Code adapter blocks
    with an unsupported strict-read-path result.

    A missing boundary (``None``) is treated as a non-strict
    declaration and is a clean pass; a non-mapping input is a type
    error and BLOCKS with the same blocker id so a runner can branch
    on a single machine-stable string.
    """
    if boundary is None:
        return _ok_result()
    if not isinstance(boundary, Mapping):
        return _blocked_result(
            [
                _blocker(
                    STRICT_READ_PATH_UNSUPPORTED_BLOCKER,
                    (
                        "execution_boundary must be a mapping for the "
                        "strict-read-path check; got "
                        f"{type(boundary).__name__} (Issue #13 AC2)"
                    ),
                )
            ]
        )
    if boundary.get("strict_read_paths") is True:
        return _blocked_result(
            [
                _blocker(
                    STRICT_READ_PATH_UNSUPPORTED_BLOCKER,
                    (
                        "execution_boundary declares strict_read_paths=true; "
                        "the Claude Code MVP adapter cannot approximate "
                        "read paths by workspace masking, so the case "
                        "BLOCKS as unsupported (ADR 0031, Issue #13 AC2)"
                    ),
                )
            ]
        )
    return _ok_result()


# --------------------------------------------------------------------------- #
# Public: validate_no_isolation                                               #
# --------------------------------------------------------------------------- #


def _read_env_override() -> str | None:
    """Return the explicit env-var override or ``None`` if unset."""
    value = os.environ.get(NO_ISOLATION_ENV_OVERRIDE)
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped


def validate_no_isolation(
    *,
    confirmed: bool,
    interactive: bool,
    env_override: str | None = None,
) -> dict[str, Any]:
    """Gate ``--no-isolation`` calls; return ``ok=True`` iff all checks pass.

    AC3: a caller that passes ``--no-isolation`` must also pass
    ``--confirm-no-isolation`` (modeled as ``confirmed=True``).
    AC4: a non-interactive caller must also set the explicit
    ``METACRUCIBLE_ALLOW_NO_ISOLATION`` env-var override (modeled
    as a non-empty ``env_override``). The env override does NOT
    replace the confirmation flag: a non-interactive caller that
    forgets ``--confirm-no-isolation`` still gets the
    confirmation blocker.

    Parameters
    ----------
    confirmed:
        ``True`` iff the caller passed ``--confirm-no-isolation``.
    interactive:
        ``True`` iff stdin is a TTY (e.g. ``sys.stdin.isatty()``).
    env_override:
        Optional override value; when ``None`` the helper reads the
        :data:`NO_ISOLATION_ENV_OVERRIDE` environment variable. Tests
        pass an explicit value to avoid touching the real
        environment.
    """
    blockers: list[dict[str, str]] = []
    if not confirmed:
        blockers.append(
            _blocker(
                NO_ISOLATION_CONFIRMATION_REQUIRED_BLOCKER,
                (
                    "--no-isolation requires an explicit "
                    "--confirm-no-isolation flag; refusing to run "
                    "without the human-confirmed safety gate "
                    "(ADR 0031, Issue #13 AC3)"
                ),
            )
        )
    if env_override is None:
        env_override = _read_env_override()
    if not interactive and not env_override:
        blockers.append(
            _blocker(
                NO_ISOLATION_NON_INTERACTIVE_BLOCKER,
                (
                    f"non-interactive --no-isolation requires the explicit "
                    f"{NO_ISOLATION_ENV_OVERRIDE}=1 env-var override; "
                    "refusing to run without an out-of-band authorization "
                    "(ADR 0031, Issue #13 AC4)"
                ),
            )
        )
    if blockers:
        return _blocked_result(blockers)
    return _ok_result()
