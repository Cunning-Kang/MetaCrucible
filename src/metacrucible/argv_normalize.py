"""Normalize ``execution_boundary.allowedTools`` and ``target_commands`` (Issue #11).

Pins ADR 0028 ("Portable ``execution_boundary.target_commands`` map only
to exact Claude Code ``Bash(...)`` allow strings in the MVP; unsupported
tools, broad wildcards, unsafe shell metacharacters, path traversal, or
strict read-path enforcement requirements block the run. ``target_commands``
are accepted only as conservative argv arrays and normalized into display
commands; complex shell behavior must be moved into reviewed wrapper files.").

Public surface
--------------

The module exposes three top-level functions plus a small set of
machine-stable constants:

* :data:`REVIEWED_TOOL_NAMES` — the small vocabulary of Claude Code
  tool names the MVP adapter accepts. Any other tool name (and any
  wildcard) is rejected by :func:`validate_allowed_tools`.
* :data:`EXPECTED_BLOCKERS` — a mapping from friendly key to the
  stable blocker-id string each failure mode emits. Tests and
  downstream automation branch on these verbatim.
* :func:`validate_allowed_tools` — accept a list of tool names,
  return a result dict with ``ok`` and ``blockers``.
* :func:`validate_target_commands` — accept a list of argv arrays,
  return a result dict with ``ok`` and ``blockers``.
* :func:`normalize_execution_boundary` — full job: validate the
  whole boundary mapping and, on success, produce the
  ``Bash(<argv0> <argv1> ...)`` allow strings the runtime adapter
  feeds verbatim to ``--allow-tools``.

Result shape
------------

All three validators return a dict of the same shape used by
``init --check``, ``promote``, and :func:`metacrucible.preflight.check_*_preflight`:

  - ``ok`` (bool) — ``True`` iff validation passes.
  - ``blockers`` (list[dict]) — empty when ``ok`` is ``True``;
    otherwise each entry is ``{"id": <blocker_id>, "message": <human>}``.
  - ``allowed_strings`` (list[str]) — only present on
    :func:`normalize_execution_boundary` success. Always exactly
    the ``Bash(...)`` form for every argv the boundary listed.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

__all__ = [
    "REVIEWED_TOOL_NAMES",
    "EXPECTED_BLOCKERS",
    "ALLOWED_TOOL_UNSUPPORTED_BLOCKER",
    "ALLOWED_TOOL_WILDCARD_BLOCKER",
    "TARGET_COMMAND_WILDCARD_BLOCKER",
    "TARGET_COMMAND_METACHAR_BLOCKER",
    "TARGET_COMMAND_PATH_TRAVERSAL_BLOCKER",
    "TARGET_COMMAND_EMPTY_BLOCKER",
    "TARGET_COMMAND_NOT_BASH_ALLOW_FORM_BLOCKER",
    "validate_allowed_tools",
    "validate_target_commands",
    "normalize_execution_boundary",
]


# --------------------------------------------------------------------------- #
# Reviewed tool vocabulary (ADR 0028)                                         #
# --------------------------------------------------------------------------- #
#
# The Claude Code MVP adapter accepts exactly this small set of tool
# names in ``execution_boundary.allowedTools``. Anything else is
# rejected with a stable blocker id; wildcards are rejected with a
# separate blocker id so a reviewer can tell "I asked for everything"
# apart from "I asked for a name that doesn't exist".
#
# Adding a name here is a contract change: downstream code that
# branches on the blocker ids must be updated in lockstep. Renaming
# or removing an existing name is a breaking change.

REVIEWED_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "Read",
        "Write",
        "Edit",
        "MultiEdit",
        "Glob",
        "Grep",
        "Bash",
        "NotebookEdit",
        "WebFetch",
        "WebSearch",
    }
)


# --------------------------------------------------------------------------- #
# Stable blocker ids                                                          #
# --------------------------------------------------------------------------- #
#
# Machine contract: the optimizer pipeline and the runtime adapter
# branch on these exact strings. Adding a new id is a contract
# change; renaming an existing id is a breaking change and must
# be paired with a migration plan.

ALLOWED_TOOL_UNSUPPORTED_BLOCKER: str = "execution-boundary-allowed-tool-unsupported"
ALLOWED_TOOL_WILDCARD_BLOCKER: str = "execution-boundary-allowed-tool-wildcard"
TARGET_COMMAND_WILDCARD_BLOCKER: str = "execution-boundary-target-command-wildcard"
TARGET_COMMAND_METACHAR_BLOCKER: str = "execution-boundary-target-command-metachar"
TARGET_COMMAND_PATH_TRAVERSAL_BLOCKER: str = (
    "execution-boundary-target-command-path-traversal"
)
TARGET_COMMAND_EMPTY_BLOCKER: str = "execution-boundary-target-command-empty"
#: Reserved for future use: a ``target_commands`` entry that
#: produces an allow string that is not the exact ``Bash(...)``
#: form. The MVP normalizer only emits ``Bash(...)``; this id is
#: the contract a future strict verifier can raise if a non-``Bash``
#: allow string ever sneaks in.
TARGET_COMMAND_NOT_BASH_ALLOW_FORM_BLOCKER: str = (
    "execution-boundary-target-command-not-bash-allow-form"
)


EXPECTED_BLOCKERS: dict[str, str] = {
    "allowed_tool_unsupported": ALLOWED_TOOL_UNSUPPORTED_BLOCKER,
    "allowed_tool_wildcard": ALLOWED_TOOL_WILDCARD_BLOCKER,
    "target_command_wildcard": TARGET_COMMAND_WILDCARD_BLOCKER,
    "target_command_metachar": TARGET_COMMAND_METACHAR_BLOCKER,
    "target_command_path_traversal": TARGET_COMMAND_PATH_TRAVERSAL_BLOCKER,
    "target_command_empty": TARGET_COMMAND_EMPTY_BLOCKER,
    "target_command_not_bash_allow_form": TARGET_COMMAND_NOT_BASH_ALLOW_FORM_BLOCKER,
}


# --------------------------------------------------------------------------- #
# Internal: classification helpers                                            #
# --------------------------------------------------------------------------- #

#: Shell metacharacters that must never appear in a conservative
#: argv token. The set is intentionally narrow: punctuation that
#: has no shell meaning (``-``, ``.``, ``,``, ``=``, ``/`` when
#: not at the start of a path) is left alone. ``~`` is special:
#: it is a metachar when it appears mid-token (e.g. ``a~``) but
#: triggers the path-traversal rule when it appears at the start
#: of a path-shaped token (``~/foo``). The path-traversal check
#: runs first to give ``~`` at start its stricter interpretation.
_SHELL_METACHARS: frozenset[str] = frozenset(
    "|&;><$`~!\\(){}\n"
)

#: Path-traversal prefix patterns. A token that starts with any of
#: these is a path-traversal attempt (``..foo``, ``/etc/passwd``,
#: ``~/secrets``) and is rejected before the metachar scan so a
#: reviewer can distinguish "you tried to escape the workspace"
#: from "you typed a metachar by accident".
_PATH_TRAVERSAL_PREFIXES: tuple[str, ...] = ("..", "/", "~")

#: Substring used to detect embedded ``..`` segments in longer
#: path-shaped tokens (``foo/../bar``).
_EMBEDDED_DOTDOT: str = "/.."


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


def _classify_argv_token(
    token: Any, *, index_path: str
) -> tuple[bool, dict[str, str] | None]:
    """Classify a single argv token; return ``(ok, blocker)``.

    The check is ordered so a path-traversal attempt is reported as
    such (stricter) rather than as a metachar, and a metachar
    attempt is reported as such rather than as a wildcard. An
    empty string token is treated as a valid (zero-length) argv
    argument — empty *argvs* are rejected at the array level, not
    the token level, so a case like ``["echo", ""]`` is allowed.
    """
    if not isinstance(token, str):
        return False, _blocker(
            TARGET_COMMAND_METACHAR_BLOCKER,
            (
                f"{index_path} must be a string token; "
                f"got {type(token).__name__} (Issue #11 AC1)"
            ),
        )
    if token == "":
        return True, None
    # Path-traversal: leading prefix or embedded /.. segments.
    if any(token.startswith(prefix) for prefix in _PATH_TRAVERSAL_PREFIXES):
        return False, _blocker(
            TARGET_COMMAND_PATH_TRAVERSAL_BLOCKER,
            (
                f"{index_path}={token!r} is a path-traversal attempt; "
                "leading '..', '/', or '~' tokens are blocked "
                "(Issue #11 AC1)"
            ),
        )
    if _EMBEDDED_DOTDOT in token:
        return False, _blocker(
            TARGET_COMMAND_PATH_TRAVERSAL_BLOCKER,
            (
                f"{index_path}={token!r} embeds '/..' which is a "
                "path-traversal attempt (Issue #11 AC1)"
            ),
        )
    # Metacharacter: any single shell-meaningful character in the token.
    for ch in token:
        if ch in _SHELL_METACHARS:
            return False, _blocker(
                TARGET_COMMAND_METACHAR_BLOCKER,
                (
                    f"{index_path}={token!r} contains shell "
                    f"metacharacter {ch!r}; conservative argv must be "
                    "metachar-free (Issue #11 AC1)"
                ),
            )
    # Wildcards: '*' / '?' / '[...]'. Bracket form requires BOTH
    # '[' and ']' so a stray ']' or '[' in a flag value does not
    # raise a false positive.
    if "*" in token or "?" in token:
        return False, _blocker(
            TARGET_COMMAND_WILDCARD_BLOCKER,
            (
                f"{index_path}={token!r} contains a glob wildcard; "
                "broad wildcards in target_commands are blocked "
                "(Issue #11 AC1)"
            ),
        )
    if "[" in token and "]" in token:
        return False, _blocker(
            TARGET_COMMAND_WILDCARD_BLOCKER,
            (
                f"{index_path}={token!r} contains a glob bracket "
                "pattern; bracket wildcards in target_commands are "
                "blocked (Issue #11 AC1)"
            ),
        )
    return True, None


# --------------------------------------------------------------------------- #
# Public: validate_allowed_tools                                              #
# --------------------------------------------------------------------------- #


def validate_allowed_tools(
    allowed_tools: Sequence[Any] | None,
) -> dict[str, Any]:
    """Validate a portable ``allowedTools`` list against the reviewed set.

    Returns a result dict with the standard ``ok`` / ``blockers``
    shape. A non-list input is rejected; a list with at least one
    non-string entry is rejected; a list with at least one
    wildcard token is rejected with the wildcard blocker; a list
    with at least one non-reviewed name is rejected with the
    unsupported-tool blocker. The check stops at the first failure
    so the message is unambiguous for the reviewer.

    The list is allowed to be empty (no tool permission requested).
    """
    if allowed_tools is None:
        return _blocked_result(
            [
                _blocker(
                    ALLOWED_TOOL_UNSUPPORTED_BLOCKER,
                    (
                        "allowedTools must be a list; got None (Issue #11 AC2)"
                    ),
                )
            ]
        )
    if not isinstance(allowed_tools, (list, tuple)):
        return _blocked_result(
            [
                _blocker(
                    ALLOWED_TOOL_UNSUPPORTED_BLOCKER,
                    (
                        f"allowedTools must be a list; got "
                        f"{type(allowed_tools).__name__} (Issue #11 AC2)"
                    ),
                )
            ]
        )
    for index, tool in enumerate(allowed_tools):
        if not isinstance(tool, str):
            return _blocked_result(
                [
                    _blocker(
                        ALLOWED_TOOL_UNSUPPORTED_BLOCKER,
                        (
                            f"allowedTools[{index}] must be a string; "
                            f"got {type(tool).__name__} (Issue #11 AC2)"
                        ),
                    )
                ]
            )
        if "*" in tool or "?" in tool or "[" in tool:
            return _blocked_result(
                [
                    _blocker(
                        ALLOWED_TOOL_WILDCARD_BLOCKER,
                        (
                            f"allowedTools[{index}]={tool!r} is a "
                            "wildcard; broad wildcards in allowedTools "
                            "are blocked (Issue #11 AC2)"
                        ),
                    )
                ]
            )
        if tool not in REVIEWED_TOOL_NAMES:
            return _blocked_result(
                [
                    _blocker(
                        ALLOWED_TOOL_UNSUPPORTED_BLOCKER,
                        (
                            f"allowedTools[{index}]={tool!r} is not in the "
                            "reviewed Claude Code tool set "
                            f"{sorted(REVIEWED_TOOL_NAMES)!r} (Issue #11 AC2)"
                        ),
                    )
                ]
            )
    return _ok_result()


# --------------------------------------------------------------------------- #
# Public: validate_target_commands                                            #
# --------------------------------------------------------------------------- #


def validate_target_commands(
    target_commands: Sequence[Any] | None,
) -> dict[str, Any]:
    """Validate a portable ``target_commands`` list of conservative argvs.

    Each element of ``target_commands`` must be a non-empty list of
    string tokens. Every token must be free of shell metacharacters,
    path-traversal prefixes, and glob wildcards. The check stops at
    the first failure so the message is unambiguous for the
    reviewer; the failed token is reported with its full index path
    (``target_commands[i][j]``).
    """
    if target_commands is None:
        return _blocked_result(
            [
                _blocker(
                    TARGET_COMMAND_EMPTY_BLOCKER,
                    (
                        "target_commands must be a list; got None "
                        "(Issue #11 AC1)"
                    ),
                )
            ]
        )
    if not isinstance(target_commands, (list, tuple)):
        return _blocked_result(
            [
                _blocker(
                    TARGET_COMMAND_EMPTY_BLOCKER,
                    (
                        f"target_commands must be a list; got "
                        f"{type(target_commands).__name__} (Issue #11 AC1)"
                    ),
                )
            ]
        )
    for cmd_index, argv in enumerate(target_commands):
        if not isinstance(argv, (list, tuple)):
            return _blocked_result(
                [
                    _blocker(
                        TARGET_COMMAND_EMPTY_BLOCKER,
                        (
                            f"target_commands[{cmd_index}] must be a list "
                            f"of string tokens; got {type(argv).__name__} "
                            "(Issue #11 AC1)"
                        ),
                    )
                ]
            )
        if len(argv) == 0:
            return _blocked_result(
                [
                    _blocker(
                        TARGET_COMMAND_EMPTY_BLOCKER,
                        (
                            f"target_commands[{cmd_index}] is an empty "
                            "argv array; conservative target_commands must "
                            "list at least one command (Issue #11 AC1)"
                        ),
                    )
                ]
            )
        for token_index, token in enumerate(argv):
            ok, blocker = _classify_argv_token(
                token, index_path=f"target_commands[{cmd_index}][{token_index}]"
            )
            if not ok and blocker is not None:
                return _blocked_result([blocker])
    return _ok_result()


# --------------------------------------------------------------------------- #
# Public: normalize_execution_boundary                                        #
# --------------------------------------------------------------------------- #


def _argv_to_bash_allow_string(argv: Sequence[str]) -> str:
    """Render a clean argv as the exact ``Bash(<argv joined>)`` allow string.

    The MVP invariant (ADR 0028) is: every clean ``target_commands``
    entry produces an exact ``Bash(...)`` allow string. A
    space-separated join preserves argv order and keeps ``--key=value``
    forms intact. Reviewers and the runtime adapter consume the
    rendered string verbatim, so the format is part of the public
    contract and must not change without an ADR revision.
    """
    return "Bash(" + " ".join(str(token) for token in argv) + ")"


def normalize_execution_boundary(
    boundary: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Validate the whole execution boundary and emit ``Bash(...)`` strings.

    On success, the returned dict has ``ok=True`` and
    ``allowed_strings`` is the list of ``Bash(<argv joined>)``
    strings, one per entry of ``target_commands`` in input order.
    On failure, the dict has ``ok=False``, at least one blocker,
    and ``allowed_strings`` is the empty list (so a blocked
    normalize never leaks partial allow strings).
    """
    if boundary is None or not isinstance(boundary, Mapping):
        return _blocked_result(
            [
                _blocker(
                    ALLOWED_TOOL_UNSUPPORTED_BLOCKER,
                    (
                        "execution_boundary must be a mapping; got "
                        f"{type(boundary).__name__} (Issue #11)"
                    ),
                )
            ],
            extra={"allowed_strings": []},
        )
    # Pull the two pinned fields. Unknown extra keys are accepted
    # silently: future ADR revisions may add new boundary fields
    # (read_paths, network_policy, ...) and the MVP normalizer
    # must be forward-compatible.
    allowed_tools_raw = boundary.get("allowed_tools", [])
    target_commands_raw = boundary.get("target_commands", [])

    tool_result = validate_allowed_tools(allowed_tools_raw)
    if not tool_result["ok"]:
        return _blocked_result(
            tool_result["blockers"],
            extra={"allowed_strings": []},
        )

    cmd_result = validate_target_commands(target_commands_raw)
    if not cmd_result["ok"]:
        return _blocked_result(
            cmd_result["blockers"],
            extra={"allowed_strings": []},
        )

    allowed_strings = [
        _argv_to_bash_allow_string(argv) for argv in target_commands_raw
    ]
    return _ok_result(extra={"allowed_strings": allowed_strings})
