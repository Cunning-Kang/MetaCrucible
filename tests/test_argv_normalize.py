"""TDD behavior tests for issue #11: allowedTools / target_commands argv normalization.

The module under test is :mod:`metacrucible.argv_normalize`. It is
responsible for two related jobs that pin ADR 0028:

  1. ``execution_boundary.allowedTools`` lists are restricted to the
     reviewed tool set (Read, Write, Edit, MultiEdit, Glob, Grep,
     Bash, NotebookEdit, WebFetch, WebSearch). Wildcards and any
     other tool name are blocked with a stable blocker id.
  2. ``execution_boundary.target_commands`` are restricted to
     conservative argv arrays (list[list[str]]). Each argv must be
     free of shell metacharacters, path traversal, and wildcard
     expansion; offending commands are blocked. Surviving commands
     are converted to exact Claude Code ``Bash(...)`` allow strings
     so the runtime adapter can pass them verbatim to
     ``--allow-tools``.

The tests cover three acceptance criteria from issue #11:

  * AC1 — wildcards / metacharacters / path traversal are blocked
  * AC2 — unsupported tool requests are blocked
  * AC3 — target_commands only produce the reviewed ``Bash(...)``
    allow-string form
"""
from __future__ import annotations

import importlib
from typing import Any

import pytest


ARGV_NORMALIZE_MODULE = "metacrucible.argv_normalize"


# --------------------------------------------------------------------------- #
# Constants mirrored from the production module (kept in lockstep).          #
# --------------------------------------------------------------------------- #


#: Reviewed tool set the Claude Code MVP adapter accepts. The set
#: is the small machine-stable vocabulary pinned by ADR 0028; any
#: name outside it is a blocker.
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

#: Stable blocker ids the validator must emit on each failure mode.
#: These strings are part of the machine contract: tests, the
#: optimizer pipeline, and downstream automation all branch on
#: them verbatim. Adding a new id is a contract change; renaming
#: an existing id is a breaking change.
EXPECTED_BLOCKERS: dict[str, str] = {
    "allowed_tool_unsupported": "execution-boundary-allowed-tool-unsupported",
    "allowed_tool_wildcard": "execution-boundary-allowed-tool-wildcard",
    "target_command_wildcard": "execution-boundary-target-command-wildcard",
    "target_command_metachar": "execution-boundary-target-command-metachar",
    "target_command_path_traversal": "execution-boundary-target-command-path-traversal",
    "target_command_empty": "execution-boundary-target-command-empty",
    "target_command_not_bash_allow_form": "execution-boundary-target-command-not-bash-allow-form",
}


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _blocker_ids(payload: Any) -> list[str]:
    """Return the list of blocker ids in a normalize result, or empty if none."""
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


def _expect_blocker(payload: Any, blocker_id: str, *, context: str = "") -> str:
    """Assert ``blocker_id`` is present in ``payload`` blockers; return message."""
    ids = _blocker_ids(payload)
    assert blocker_id in ids, (
        f"{context} must emit blocker id {blocker_id!r}; got blocker_ids={ids!r}"
    )
    for blocker in payload.get("blockers", []):
        if isinstance(blocker, dict) and blocker.get("id") == blocker_id:
            message = blocker.get("message", "")
            assert isinstance(message, str) and message, (
                f"{context} blocker {blocker_id!r} must carry a non-empty message; "
                f"got message={message!r}"
            )
            return message
    return ""  # unreachable; the assert above fails first


def _expect_ok(payload: Any, *, context: str) -> None:
    """Assert ``payload`` is a clean result with no blockers."""
    assert isinstance(payload, dict), (
        f"{context} must return a dict; got {type(payload).__name__}"
    )
    assert payload.get("ok") is True, (
        f"{context} must report ok=True; got payload={payload!r}"
    )
    assert _blocker_ids(payload) == [], (
        f"{context} must not emit blockers; got blocker_ids={_blocker_ids(payload)!r}"
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


@pytest.fixture(scope="module")
def argv_normalize() -> Any:
    """Import the argv_normalize module; the test fails (red step) if absent."""
    try:
        return importlib.import_module(ARGV_NORMALIZE_MODULE)
    except ImportError as exc:
        pytest.fail(
            f"argv_normalize module {ARGV_NORMALIZE_MODULE!r} is not "
            f"implemented yet (Issue #11 red step). Expected module "
            f"exposing: normalize_execution_boundary, "
            f"validate_allowed_tools, validate_target_commands, "
            f"REVIEWED_TOOL_NAMES, and the EXPECTED_BLOCKERS ids. "
            f"ImportError: {exc}"
        )


# --------------------------------------------------------------------------- #
# Module surface                                                              #
# --------------------------------------------------------------------------- #


def test_argv_normalize_module_exposes_required_surface(argv_normalize: Any) -> None:
    """AC1+AC2+AC3: the public surface must exist (TDD red step gate)."""
    for name in (
        "REVIEWED_TOOL_NAMES",
        "EXPECTED_BLOCKERS",
        "normalize_execution_boundary",
        "validate_allowed_tools",
        "validate_target_commands",
    ):
        assert hasattr(argv_normalize, name), (
            f"{ARGV_NORMALIZE_MODULE!r} must expose {name!r} (Issue #11); "
            f"got attributes "
            f"{sorted(a for a in dir(argv_normalize) if not a.startswith('_'))!r}"
        )


def test_argv_normalize_reviewed_tool_set_matches_pinned_vocabulary(
    argv_normalize: Any,
) -> None:
    """AC2: the reviewed tool set must match the pinned vocabulary verbatim."""
    tool_set = argv_normalize.REVIEWED_TOOL_NAMES
    assert isinstance(tool_set, frozenset), (
        f"REVIEWED_TOOL_NAMES must be a frozenset; got {type(tool_set).__name__}"
    )
    assert tool_set == REVIEWED_TOOL_NAMES, (
        f"REVIEWED_TOOL_NAMES must match the pinned vocabulary; "
        f"expected {sorted(REVIEWED_TOOL_NAMES)!r}, "
        f"got {sorted(tool_set)!r}"
    )


def test_argv_normalize_blocker_ids_match_pinned_contract(
    argv_normalize: Any,
) -> None:
    """AC1+AC2+AC3: every blocker id the tests branch on must exist."""
    blockers = argv_normalize.EXPECTED_BLOCKERS
    assert isinstance(blockers, dict), (
        f"EXPECTED_BLOCKERS must be a dict; got {type(blockers).__name__}"
    )
    for key, expected in EXPECTED_BLOCKERS.items():
        assert blockers.get(key) == expected, (
            f"EXPECTED_BLOCKERS[{key!r}] must equal {expected!r}; got {blockers.get(key)!r}"
        )


# --------------------------------------------------------------------------- #
# AC2: allowedTools — supported tool names pass; unsupported / wildcards fail #
# --------------------------------------------------------------------------- #


def test_validate_allowed_tools_accepts_each_reviewed_tool(
    argv_normalize: Any,
) -> None:
    """AC2 (positive): every reviewed tool name passes validation."""
    for tool in REVIEWED_TOOL_NAMES:
        result = argv_normalize.validate_allowed_tools([tool])
        _expect_ok(result, context=f"validate_allowed_tools({tool!r})")


def test_validate_allowed_tools_accepts_multiple_reviewed_tools(
    argv_normalize: Any,
) -> None:
    """AC2 (positive): a multi-entry list of reviewed tools passes."""
    tools = ["Read", "Bash", "Glob", "Grep"]
    result = argv_normalize.validate_allowed_tools(tools)
    _expect_ok(result, context=f"validate_allowed_tools({tools!r})")


def test_validate_allowed_tools_accepts_empty_list(argv_normalize: Any) -> None:
    """AC2 (positive): an empty allowedTools list is a valid no-permission request."""
    result = argv_normalize.validate_allowed_tools([])
    _expect_ok(result, context="validate_allowed_tools([])")


def test_validate_allowed_tools_blocks_unsupported_tool(argv_normalize: Any) -> None:
    """AC2: a tool name outside the reviewed vocabulary must block."""
    result = argv_normalize.validate_allowed_tools(["RunShell"])
    _expect_blocked(result, context="validate_allowed_tools(['RunShell'])")
    _expect_blocker(
        result,
        EXPECTED_BLOCKERS["allowed_tool_unsupported"],
        context="unsupported tool",
    )


def test_validate_allowed_tools_blocks_unknown_tool_name(argv_normalize: Any) -> None:
    """AC2: an arbitrary non-Claude-Code tool name must block."""
    result = argv_normalize.validate_allowed_tools(["SendEmail"])
    _expect_blocked(result, context="validate_allowed_tools(['SendEmail'])")
    _expect_blocker(
        result,
        EXPECTED_BLOCKERS["allowed_tool_unsupported"],
        context="unknown tool",
    )


def test_validate_allowed_tools_blocks_wildcard(argv_normalize: Any) -> None:
    """AC2: a literal ``*`` wildcard in allowedTools must block."""
    result = argv_normalize.validate_allowed_tools(["*"])
    _expect_blocked(result, context="validate_allowed_tools(['*'])")
    _expect_blocker(
        result,
        EXPECTED_BLOCKERS["allowed_tool_wildcard"],
        context="wildcard tool",
    )


def test_validate_allowed_tools_blocks_glob_pattern(argv_normalize: Any) -> None:
    """AC2: a shell-style ``Bash*`` glob must block (not a reviewed name)."""
    result = argv_normalize.validate_allowed_tools(["Bash*"])
    _expect_blocked(result, context="validate_allowed_tools(['Bash*'])")
    _expect_blocker(
        result,
        EXPECTED_BLOCKERS["allowed_tool_wildcard"],
        context="glob pattern tool",
    )


def test_validate_allowed_tools_blocks_lowercase_tool_name(
    argv_normalize: Any,
) -> None:
    """AC2: Claude Code tool names are case-sensitive; lowercase is unsupported."""
    result = argv_normalize.validate_allowed_tools(["read"])
    _expect_blocked(result, context="validate_allowed_tools(['read'])")
    _expect_blocker(
        result,
        EXPECTED_BLOCKERS["allowed_tool_unsupported"],
        context="lowercase tool",
    )


# --------------------------------------------------------------------------- #
# AC1: target_commands — wildcards / metacharacters / path traversal blocked #
# --------------------------------------------------------------------------- #


def test_validate_target_commands_accepts_simple_argv(argv_normalize: Any) -> None:
    """AC1 (positive): a simple argv array with safe tokens passes."""
    result = argv_normalize.validate_target_commands([["ls", "-la"]])
    _expect_ok(result, context="validate_target_commands([['ls', '-la']])")


def test_validate_target_commands_accepts_multiple_commands(
    argv_normalize: Any,
) -> None:
    """AC1 (positive): a list of clean argv arrays passes."""
    result = argv_normalize.validate_target_commands(
        [["ls", "-la"], ["npm", "test"]]
    )
    _expect_ok(result, context="validate_target_commands(ls + npm)")


def test_validate_target_commands_accepts_empty_list(argv_normalize: Any) -> None:
    """AC1 (positive): an empty target_commands list is valid (no commands)."""
    result = argv_normalize.validate_target_commands([])
    _expect_ok(result, context="validate_target_commands([])")


def test_validate_target_commands_blocks_star_wildcard(argv_normalize: Any) -> None:
    """AC1: a ``*`` token in argv must block (glob expansion)."""
    result = argv_normalize.validate_target_commands([["rm", "*"]])
    _expect_blocked(result, context="rm *")
    _expect_blocker(
        result,
        EXPECTED_BLOCKERS["target_command_wildcard"],
        context="rm *",
    )


def test_validate_target_commands_blocks_question_mark_wildcard(
    argv_normalize: Any,
) -> None:
    """AC1: a ``?`` token in argv must block (glob single-char wildcard)."""
    result = argv_normalize.validate_target_commands([["ls", "file?.txt"]])
    _expect_blocked(result, context="ls file?.txt")
    _expect_blocker(
        result,
        EXPECTED_BLOCKERS["target_command_wildcard"],
        context="ls file?.txt",
    )


def test_validate_target_commands_blocks_bracket_wildcard(
    argv_normalize: Any,
) -> None:
    """AC1: ``[abc]`` bracket wildcards in argv must block."""
    result = argv_normalize.validate_target_commands([["ls", "file[123].txt"]])
    _expect_blocked(result, context="ls file[123].txt")
    _expect_blocker(
        result,
        EXPECTED_BLOCKERS["target_command_wildcard"],
        context="ls file[123].txt",
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["ls", "a|b"],  # pipe
        ["ls", "a&b"],  # background / and
        ["ls", "a;b"],  # command separator
        ["ls", "a>b"],  # redirect stdout
        ["ls", "a<b"],  # redirect stdin
        ["ls", "a$b"],  # variable expansion
        ["ls", "a`b`"],  # command substitution backticks
        ["ls", "$(whoami)"],  # command substitution
        ["ls", "a~"],  # tilde expansion
        ["ls", "a!b"],  # history / negation
        ["ls", "a\\b"],  # backslash escape
        ["ls", "a\nb"],  # embedded newline
        ["ls", "a{b,c}"],  # brace expansion
        ["ls", "a(b)c"],  # subshell paren
    ],
)
def test_validate_target_commands_blocks_shell_metacharacters(
    argv_normalize: Any, argv: list[str]
) -> None:
    """AC1: any unsafe shell metacharacter in argv must block."""
    result = argv_normalize.validate_target_commands([argv])
    _expect_blocked(result, context=f"argv={argv!r}")
    _expect_blocker(
        result,
        EXPECTED_BLOCKERS["target_command_metachar"],
        context=f"argv={argv!r}",
    )


def test_validate_target_commands_blocks_pipe_in_argv_zero(
    argv_normalize: Any,
) -> None:
    """AC1: metacharacters anywhere in the argv (including argv[0]) must block."""
    result = argv_normalize.validate_target_commands([["ls|grep"]])
    _expect_blocked(result, context="ls|grep")
    _expect_blocker(
        result,
        EXPECTED_BLOCKERS["target_command_metachar"],
        context="ls|grep",
    )


def test_validate_target_commands_blocks_dotdot_path_traversal(
    argv_normalize: Any,
) -> None:
    """AC1: a ``..`` token in argv must block (path traversal)."""
    result = argv_normalize.validate_target_commands([["cat", "../etc/passwd"]])
    _expect_blocked(result, context="cat ../etc/passwd")
    _expect_blocker(
        result,
        EXPECTED_BLOCKERS["target_command_path_traversal"],
        context="cat ../etc/passwd",
    )


def test_validate_target_commands_blocks_embedded_dotdot(
    argv_normalize: Any,
) -> None:
    """AC1: ``..`` embedded in a longer path token must still block."""
    result = argv_normalize.validate_target_commands(
        [["cat", "foo/../bar/baz.txt"]]
    )
    _expect_blocked(result, context="cat foo/../bar/baz.txt")
    _expect_blocker(
        result,
        EXPECTED_BLOCKERS["target_command_path_traversal"],
        context="cat foo/../bar/baz.txt",
    )


def test_validate_target_commands_blocks_absolute_path(argv_normalize: Any) -> None:
    """AC1: a leading-``/`` absolute path in argv must block (path traversal)."""
    result = argv_normalize.validate_target_commands([["cat", "/etc/passwd"]])
    _expect_blocked(result, context="cat /etc/passwd")
    _expect_blocker(
        result,
        EXPECTED_BLOCKERS["target_command_path_traversal"],
        context="cat /etc/passwd",
    )


def test_validate_target_commands_blocks_home_expansion(argv_normalize: Any) -> None:
    """AC1: a leading-``~`` tilde path in argv must block (path traversal)."""
    result = argv_normalize.validate_target_commands([["cat", "~/secrets.txt"]])
    _expect_blocked(result, context="cat ~/secrets.txt")
    _expect_blocker(
        result,
        EXPECTED_BLOCKERS["target_command_path_traversal"],
        context="cat ~/secrets.txt",
    )


def test_validate_target_commands_blocks_empty_command(argv_normalize: Any) -> None:
    """AC1: a zero-length argv (empty command) must block."""
    result = argv_normalize.validate_target_commands([[]])
    _expect_blocked(result, context="empty argv")
    _expect_blocker(
        result,
        EXPECTED_BLOCKERS["target_command_empty"],
        context="empty argv",
    )


def test_validate_target_commands_blocks_empty_argv_in_list(
    argv_normalize: Any,
) -> None:
    """AC1: a single empty argv within a longer list must block the whole list."""
    result = argv_normalize.validate_target_commands(
        [["ls", "-la"], [], ["npm", "test"]]
    )
    _expect_blocked(result, context="empty argv in list")
    _expect_blocker(
        result,
        EXPECTED_BLOCKERS["target_command_empty"],
        context="empty argv in list",
    )


# --------------------------------------------------------------------------- #
# AC3: normalize produces the reviewed Bash(...) allow-string form            #
# --------------------------------------------------------------------------- #


def test_normalize_execution_boundary_returns_bash_allow_strings(
    argv_normalize: Any,
) -> None:
    """AC3: clean target_commands convert to ``Bash(<argv joined>)`` strings."""
    result = argv_normalize.normalize_execution_boundary(
        {
            "allowed_tools": ["Read", "Bash"],
            "target_commands": [["npm", "test"], ["ls", "-la"]],
        }
    )
    _expect_ok(result, context="normalize(bash + ls)")
    allowed_strings = result.get("allowed_strings", [])
    assert isinstance(allowed_strings, list), (
        f"normalize must expose a list 'allowed_strings'; got "
        f"{type(allowed_strings).__name__}"
    )
    assert "Bash(npm test)" in allowed_strings, (
        f"normalize must produce 'Bash(npm test)' for [['npm','test']]; "
        f"got {allowed_strings!r}"
    )
    assert "Bash(ls -la)" in allowed_strings, (
        f"normalize must produce 'Bash(ls -la)' for [['ls','-la']]; "
        f"got {allowed_strings!r}"
    )


def test_normalize_execution_boundary_allowed_strings_are_exact_bash_form(
    argv_normalize: Any,
) -> None:
    """AC3: every produced allowed string is the exact ``Bash(...)`` form.

    No other tool form is permitted — non-Bash tools only appear as
    plain tool names. The MVP invariant: ``Bash``-only ``target_commands``
    yield only ``Bash(...)`` allow strings.
    """
    result = argv_normalize.normalize_execution_boundary(
        {
            "allowed_tools": ["Bash"],
            "target_commands": [["echo", "hello"], ["ls", "-la"]],
        }
    )
    _expect_ok(result, context="normalize(bash only)")
    allowed_strings = result.get("allowed_strings", [])
    assert all(
        isinstance(item, str) and item.startswith("Bash(") and item.endswith(")")
        for item in allowed_strings
    ), (
        f"every allowed_string must be an exact Bash(...) form; "
        f"got {allowed_strings!r}"
    )


def test_normalize_execution_boundary_preserves_argv_order(argv_normalize: Any) -> None:
    """AC3: argv order is preserved in the produced Bash(...) allow string."""
    result = argv_normalize.normalize_execution_boundary(
        {
            "allowed_tools": ["Bash"],
            "target_commands": [["git", "log", "--oneline", "-n", "5"]],
        }
    )
    _expect_ok(result, context="normalize(git log)")
    allowed_strings = result.get("allowed_strings", [])
    assert allowed_strings == ["Bash(git log --oneline -n 5)"], (
        f"argv order must be preserved verbatim; got {allowed_strings!r}"
    )


def test_normalize_execution_boundary_preserves_dash_flags(argv_normalize: Any) -> None:
    """AC3: short and long flag tokens are kept verbatim in the allow string."""
    result = argv_normalize.normalize_execution_boundary(
        {
            "allowed_tools": ["Bash"],
            "target_commands": [["ls", "-la", "--color=auto"]],
        }
    )
    _expect_ok(result, context="normalize(ls flags)")
    allowed_strings = result.get("allowed_strings", [])
    assert "Bash(ls -la --color=auto)" in allowed_strings, (
        f"flag tokens must be preserved; got {allowed_strings!r}"
    )


def test_normalize_execution_boundary_preserves_eq_separated_values(
    argv_normalize: Any,
) -> None:
    """AC3: ``--key=value`` form is preserved as a single token in the allow string."""
    result = argv_normalize.normalize_execution_boundary(
        {
            "allowed_tools": ["Bash"],
            "target_commands": [["pytest", "--maxfail=1", "-q"]],
        }
    )
    _expect_ok(result, context="normalize(pytest)")
    allowed_strings = result.get("allowed_strings", [])
    assert "Bash(pytest --maxfail=1 -q)" in allowed_strings, (
        f"--key=value must be a single token in the allow string; "
        f"got {allowed_strings!r}"
    )


def test_normalize_execution_boundary_does_not_produce_glob_form(
    argv_normalize: Any,
) -> None:
    """AC3: the allow string must not contain wildcards or glob suffixes."""
    result = argv_normalize.normalize_execution_boundary(
        {
            "allowed_tools": ["Bash"],
            "target_commands": [["ls", "-la"]],
        }
    )
    _expect_ok(result, context="normalize(ls -la)")
    allowed_strings = result.get("allowed_strings", [])
    for item in allowed_strings:
        assert "*" not in item, (
            f"allow string must not contain '*' glob; got {item!r}"
        )


def test_normalize_execution_boundary_blocks_unsupported_tool(
    argv_normalize: Any,
) -> None:
    """AC2: an unsupported tool short-circuits the whole boundary normalize."""
    result = argv_normalize.normalize_execution_boundary(
        {
            "allowed_tools": ["Bash", "SendEmail"],
            "target_commands": [["ls"]],
        }
    )
    _expect_blocked(result, context="unsupported tool in normalize")
    _expect_blocker(
        result,
        EXPECTED_BLOCKERS["allowed_tool_unsupported"],
        context="unsupported tool in normalize",
    )


def test_normalize_execution_boundary_blocks_wildcard_command(
    argv_normalize: Any,
) -> None:
    """AC1: a wildcard command short-circuits the whole boundary normalize."""
    result = argv_normalize.normalize_execution_boundary(
        {
            "allowed_tools": ["Bash"],
            "target_commands": [["rm", "*"]],
        }
    )
    _expect_blocked(result, context="wildcard command in normalize")
    _expect_blocker(
        result,
        EXPECTED_BLOCKERS["target_command_wildcard"],
        context="wildcard command in normalize",
    )


def test_normalize_execution_boundary_blocks_path_traversal_command(
    argv_normalize: Any,
) -> None:
    """AC1: a path-traversal command short-circuits the whole boundary normalize."""
    result = argv_normalize.normalize_execution_boundary(
        {
            "allowed_tools": ["Bash"],
            "target_commands": [["cat", "../etc/passwd"]],
        }
    )
    _expect_blocked(result, context="path traversal in normalize")
    _expect_blocker(
        result,
        EXPECTED_BLOCKERS["target_command_path_traversal"],
        context="path traversal in normalize",
    )


def test_normalize_execution_boundary_no_allowed_strings_on_block(
    argv_normalize: Any,
) -> None:
    """AC1+AC2: a blocked normalize must not leak partial allowed strings."""
    result = argv_normalize.normalize_execution_boundary(
        {
            "allowed_tools": ["Bash", "SendEmail"],
            "target_commands": [["ls", "-la"]],
        }
    )
    _expect_blocked(result, context="block-no-allowed_strings")
    allowed_strings = result.get("allowed_strings", [])
    assert allowed_strings == [], (
        f"blocked normalize must not produce allowed_strings; got "
        f"{allowed_strings!r}"
    )


def test_normalize_execution_boundary_empty_target_commands_ok(
    argv_normalize: Any,
) -> None:
    """AC3: an empty target_commands list is valid; only Bash(...): no commands."""
    result = argv_normalize.normalize_execution_boundary(
        {
            "allowed_tools": ["Bash", "Read"],
            "target_commands": [],
        }
    )
    _expect_ok(result, context="normalize(empty target_commands)")
    assert result.get("allowed_strings") == [], (
        f"empty target_commands must yield no Bash(...) strings; got "
        f"{result.get('allowed_strings')!r}"
    )


def test_normalize_execution_boundary_ignores_unknown_boundary_keys(
    argv_normalize: Any,
) -> None:
    """AC1+AC2+AC3: unknown boundary keys must not affect validation.

    Future ADR revisions may add new fields (read_paths,
    network_policy, ...). The MVP normalizer is forward-compatible:
    extra keys pass through silently as long as the pinned
    ``allowed_tools`` / ``target_commands`` are valid.
    """
    result = argv_normalize.normalize_execution_boundary(
        {
            "allowed_tools": ["Bash"],
            "target_commands": [["ls", "-la"]],
            "read_paths": ["./workspace"],
            "future_field": {"x": 1},
        }
    )
    _expect_ok(result, context="normalize(extra keys)")
    assert "Bash(ls -la)" in result.get("allowed_strings", []), (
        f"valid boundary must still produce the Bash(...) allow string "
        f"when extra keys are present; got {result.get('allowed_strings')!r}"
    )
