"""Tests for Issue #9: Claude Code Skill and subagent preflight sentinel.

Issue #9 pins the preflight sentinel contract from ADR 0028:

  - Skill preflight must use a fixed one-line sentinel:
    ``METACRUCIBLE_SKILL_DISCOVERABLE=<yes|no>; NAME=<resolved-name-or-empty>``
  - Missing or mismatched sentinel output blocks preflight.
  - The subagent path uses the same shape with its own prefix:
    ``METACRUCIBLE_SUBAGENT_DISCOVERABLE=<yes|no>; NAME=<resolved-name-or-empty>``

These tests are the red step: the preflight module does not exist
yet. Once it lands, the tests will turn green and pin the exact
format that downstream automation (the Claude Code adapter, ADR
0023) branches on verbatim.

The implementation under test lives in ``metacrucible.preflight`` and
exposes at least:

  - ``SKILL_SENTINEL_PREFIX`` / ``SUBAGENT_SENTINEL_PREFIX`` —
    the documented sentinel string fragments (ADR 0028).
  - ``skill_preflight_prompt()`` / ``subagent_preflight_prompt()`` —
    return the exact prompt the Claude Code adapter sends.
  - ``parse_skill_sentinel(output)`` / ``parse_subagent_sentinel(output)``
    — extract the sentinel components from a model response.
  - ``check_skill_preflight(output)`` / ``check_subagent_preflight(output)``
    — return ``{"ok": bool, "blockers": [...], "name": str, "discoverable": str}``
    matching the init/promote/load_benchmark shape.

References
----------
- ADR 0028 (Claude Code adapter contract): "Skill preflight prompts
  must ask for exactly ``METACRUCIBLE_SKILL_DISCOVERABLE=<yes|no>;
  NAME=<resolved-name-or-empty>``, and missing or mismatched
  sentinel output blocks preflight."
- Issue #9 acceptance criteria.
"""
from __future__ import annotations

from typing import Any

import pytest

PREFLIGHT_MODULE = "metacrucible.preflight"

# --------------------------------------------------------------------------- #
# Expected sentinel contract                                                  #
# --------------------------------------------------------------------------- #
#
# ADR 0028 pins the Skill sentinel exactly. The subagent path mirrors
# the same shape with a different prefix; both are fixed one-line
# strings the agent must emit (Issue #9 AC3).

SKILL_SENTINEL_PREFIX = "METACRUCIBLE_SKILL_DISCOVERABLE"
SUBAGENT_SENTINEL_PREFIX = "METACRUCIBLE_SUBAGENT_DISCOVERABLE"

# Stable blocker ids the preflight validator must emit. These are the
# machine contract: tests and downstream automation branch on them
# verbatim.
EXPECTED_SKILL_BLOCKERS: dict[str, str] = {
    "missing": "skill-preflight-sentinel-missing",
    "malformed": "skill-preflight-sentinel-malformed",
    "not_discoverable": "skill-preflight-not-discoverable",
}
EXPECTED_SUBAGENT_BLOCKERS: dict[str, str] = {
    "missing": "subagent-preflight-sentinel-missing",
    "malformed": "subagent-preflight-sentinel-malformed",
    "not_discoverable": "subagent-preflight-not-discoverable",
}

# Sample outputs used to exercise the parser/validator. Each sample
# exercises one branch of the parser.
SAMPLE_SKILL_OK: str = "METACRUCIBLE_SKILL_DISCOVERABLE=yes; NAME=metacrucible"
SAMPLE_SKILL_NOT_DISCOVERABLE: str = "METACRUCIBLE_SKILL_DISCOVERABLE=no; NAME="
SAMPLE_SKILL_NO_NAME: str = "METACRUCIBLE_SKILL_DISCOVERABLE=yes; NAME="
SAMPLE_SKILL_MALFORMED_BAD_VALUE: str = (
    "METACRUCIBLE_SKILL_DISCOVERABLE=maybe; NAME=metacrucible"
)
SAMPLE_SKILL_MALFORMED_MISSING_SEMI: str = (
    "METACRUCIBLE_SKILL_DISCOVERABLE=yes NAME=metacrucible"
)
SAMPLE_SKILL_MALFORMED_MISSING_NAME: str = "METACRUCIBLE_SKILL_DISCOVERABLE=yes; "
SAMPLE_SKILL_MALFORMED_NO_EQUALS: str = (
    "METACRUCIBLE_SKILL_DISCOVERABLE; NAME=metacrucible"
)

SAMPLE_SUBAGENT_OK: str = (
    "METACRUCIBLE_SUBAGENT_DISCOVERABLE=yes; NAME=researcher"
)
SAMPLE_SUBAGENT_NOT_DISCOVERABLE: str = (
    "METACRUCIBLE_SUBAGENT_DISCOVERABLE=no; NAME="
)
SAMPLE_SUBAGENT_MALFORMED: str = (
    "METACRUCIBLE_SUBAGENT_DISCOVERABLE=maybe; NAME=researcher"
)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _blocker_ids(payload: Any) -> list[str]:
    """Return the list of blocker ids in a check_* result, or empty if none."""
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


@pytest.fixture(scope="module")
def preflight() -> Any:
    """Import the preflight module; the test fails (red step) if it does not exist."""
    import importlib

    try:
        return importlib.import_module(PREFLIGHT_MODULE)
    except ImportError as exc:
        pytest.fail(
            f"preflight module {PREFLIGHT_MODULE!r} is not implemented yet "
            f"(Issue #9 red step). Expected module exposing: "
            f"SKILL_SENTINEL_PREFIX, SUBAGENT_SENTINEL_PREFIX, "
            f"check_skill_preflight, check_subagent_preflight, "
            f"parse_skill_sentinel, parse_subagent_sentinel, "
            f"skill_preflight_prompt, subagent_preflight_prompt. "
            f"ImportError: {exc}"
        )


# --------------------------------------------------------------------------- #
# AC1 — Sentinel exact format documented                                      #
# --------------------------------------------------------------------------- #


def test_preflight_module_exposes_skill_prefix_constant(preflight: Any) -> None:
    """AC1: the exact Skill sentinel prefix must be exposed as a module constant."""
    assert hasattr(preflight, "SKILL_SENTINEL_PREFIX"), (
        f"{PREFLIGHT_MODULE!r} must expose SKILL_SENTINEL_PREFIX "
        f"(ADR 0028, Issue #9 AC1); got attributes "
        f"{sorted(a for a in dir(preflight) if not a.startswith('_'))!r}"
    )
    assert preflight.SKILL_SENTINEL_PREFIX == SKILL_SENTINEL_PREFIX, (
        f"SKILL_SENTINEL_PREFIX must be exactly {SKILL_SENTINEL_PREFIX!r} "
        f"(ADR 0028); got {preflight.SKILL_SENTINEL_PREFIX!r}"
    )


def test_preflight_module_exposes_subagent_prefix_constant(preflight: Any) -> None:
    """AC1+AC3: the exact subagent sentinel prefix must be exposed as a constant."""
    assert hasattr(preflight, "SUBAGENT_SENTINEL_PREFIX"), (
        f"{PREFLIGHT_MODULE!r} must expose SUBAGENT_SENTINEL_PREFIX "
        f"(Issue #9 AC3); got attributes "
        f"{sorted(a for a in dir(preflight) if not a.startswith('_'))!r}"
    )
    assert preflight.SUBAGENT_SENTINEL_PREFIX == SUBAGENT_SENTINEL_PREFIX, (
        f"SUBAGENT_SENTINEL_PREFIX must be exactly {SUBAGENT_SENTINEL_PREFIX!r} "
        f"(Issue #9 AC3); got {preflight.SUBAGENT_SENTINEL_PREFIX!r}"
    )


def test_preflight_skill_prompt_advertises_exact_sentinel_format(
    preflight: Any,
) -> None:
    """AC1: the Skill preflight prompt must advertise the exact sentinel shape.

    The prompt is the *only* thing the agent runtime sees; if the
    format string is not on the prompt, the model has no way to
    produce a parseable response (ADR 0028). We require the exact
    prefix, the ``=`` separator, the ``<yes|no>`` placeholder
    showing both branches, and the ``; NAME=`` field to all appear
    in the prompt — in that order.
    """
    assert hasattr(preflight, "skill_preflight_prompt"), (
        f"{PREFLIGHT_MODULE!r} must expose skill_preflight_prompt()"
    )
    prompt = preflight.skill_preflight_prompt()
    assert isinstance(prompt, str), "skill_preflight_prompt must return str"
    assert SKILL_SENTINEL_PREFIX in prompt, (
        f"Skill preflight prompt must contain the sentinel prefix "
        f"{SKILL_SENTINEL_PREFIX!r} (ADR 0028); got prompt={prompt!r}"
    )
    # Advertise both yes/no branches. We accept any whitespace
    # between the tokens (the prompt is human-facing), but the
    # order prefix / = / yes-or-no / ; / NAME= is required.
    import re as _re

    pattern = (
        _re.escape(SKILL_SENTINEL_PREFIX)
        + r"\s*=\s*(?:<\s*yes\s*\|\s*no\s*>|yes|no)"
    )
    assert _re.search(pattern, prompt), (
        f"Skill preflight prompt must advertise "
        f"``{SKILL_SENTINEL_PREFIX}=<yes|no>`` (ADR 0028); got prompt={prompt!r}"
    )
    assert "; NAME=" in prompt, (
        f"Skill preflight prompt must advertise the ``; NAME=`` field "
        f"(ADR 0028); got prompt={prompt!r}"
    )


def test_preflight_subagent_prompt_advertises_exact_sentinel_format(
    preflight: Any,
) -> None:
    """AC1+AC3: the subagent preflight prompt must mirror the Skill shape."""
    assert hasattr(preflight, "subagent_preflight_prompt"), (
        f"{PREFLIGHT_MODULE!r} must expose subagent_preflight_prompt()"
    )
    prompt = preflight.subagent_preflight_prompt()
    assert isinstance(prompt, str), "subagent_preflight_prompt must return str"
    assert SUBAGENT_SENTINEL_PREFIX in prompt, (
        f"subagent preflight prompt must contain the sentinel prefix "
        f"{SUBAGENT_SENTINEL_PREFIX!r} (Issue #9 AC3); got prompt={prompt!r}"
    )
    import re as _re

    pattern = (
        _re.escape(SUBAGENT_SENTINEL_PREFIX)
        + r"\s*=\s*(?:<\s*yes\s*\|\s*no\s*>|yes|no)"
    )
    assert _re.search(pattern, prompt), (
        f"subagent preflight prompt must advertise "
        f"``{SUBAGENT_SENTINEL_PREFIX}=<yes|no>`` (Issue #9 AC3); "
        f"got prompt={prompt!r}"
    )
    assert "; NAME=" in prompt, (
        f"subagent preflight prompt must advertise the ``; NAME=`` field "
        f"(Issue #9 AC3); got prompt={prompt!r}"
    )


# --------------------------------------------------------------------------- #
# AC2 — Missing/mismatched sentinel blocks preflight                          #
# --------------------------------------------------------------------------- #


def test_check_skill_preflight_passes_on_valid_sentinel(preflight: Any) -> None:
    """AC2 (positive): a well-formed ``discoverable=yes`` sentinel passes preflight."""
    result = preflight.check_skill_preflight(SAMPLE_SKILL_OK)
    assert isinstance(result, dict), (
        f"check_skill_preflight must return a dict; got {type(result).__name__}"
    )
    assert result.get("ok") is True, (
        f"well-formed Skill sentinel must produce ok=True; got result={result!r}"
    )
    assert _blocker_ids(result) == [], (
        f"well-formed Skill sentinel must not produce blockers; "
        f"got blocker_ids={_blocker_ids(result)!r}"
    )


def test_check_skill_preflight_blocks_on_missing_sentinel(preflight: Any) -> None:
    """AC2: output that does not contain the sentinel must block preflight."""
    result = preflight.check_skill_preflight("hello there, no sentinel here")
    assert result.get("ok") is False, (
        f"missing sentinel must block preflight; got result={result!r}"
    )
    assert EXPECTED_SKILL_BLOCKERS["missing"] in _blocker_ids(result), (
        f"missing sentinel must emit blocker id "
        f"{EXPECTED_SKILL_BLOCKERS['missing']!r}; "
        f"got blocker_ids={_blocker_ids(result)!r}"
    )


def test_check_skill_preflight_blocks_on_empty_output(preflight: Any) -> None:
    """AC2: empty output is treated as a missing sentinel and blocks preflight."""
    result = preflight.check_skill_preflight("")
    assert result.get("ok") is False
    assert EXPECTED_SKILL_BLOCKERS["missing"] in _blocker_ids(result), (
        f"empty output must emit missing-sentinel blocker; "
        f"got blocker_ids={_blocker_ids(result)!r}"
    )


@pytest.mark.parametrize(
    "malformed",
    [
        SAMPLE_SKILL_MALFORMED_BAD_VALUE,
        SAMPLE_SKILL_MALFORMED_MISSING_SEMI,
        SAMPLE_SKILL_MALFORMED_MISSING_NAME,
        SAMPLE_SKILL_MALFORMED_NO_EQUALS,
    ],
    ids=[
        "bad-value",
        "missing-semi",
        "missing-name",
        "no-equals",
    ],
)
def test_check_skill_preflight_blocks_on_malformed_sentinel(
    preflight: Any, malformed: str
) -> None:
    """AC2: a sentinel whose shape does not match the exact format blocks preflight.

    The four parametrized cases are the canonical malformed shapes:
      - a token that is not ``yes``/``no``
      - a missing ``;`` separator
      - a missing ``NAME=`` field
      - a missing ``=`` after the prefix
    """
    result = preflight.check_skill_preflight(malformed)
    assert result.get("ok") is False, (
        f"malformed sentinel must block preflight; got result={result!r} "
        f"for input={malformed!r}"
    )
    ids = _blocker_ids(result)
    assert EXPECTED_SKILL_BLOCKERS["malformed"] in ids, (
        f"malformed sentinel must emit blocker id "
        f"{EXPECTED_SKILL_BLOCKERS['malformed']!r}; "
        f"got blocker_ids={ids!r} for input={malformed!r}"
    )


def test_check_skill_preflight_blocks_on_not_discoverable(preflight: Any) -> None:
    """AC2 (semantic): a well-formed ``discoverable=no`` sentinel still blocks.

    The sentinel format is valid but the answer is "not discoverable",
    which is a semantic block (not a malformed one). The validator
    must distinguish "the model produced the right shape" from
    "the model produced the right shape and the answer is yes".
    """
    result = preflight.check_skill_preflight(SAMPLE_SKILL_NOT_DISCOVERABLE)
    assert result.get("ok") is False, (
        f"``discoverable=no`` must block preflight; got result={result!r}"
    )
    ids = _blocker_ids(result)
    assert EXPECTED_SKILL_BLOCKERS["not_discoverable"] in ids, (
        f"``discoverable=no`` must emit blocker id "
        f"{EXPECTED_SKILL_BLOCKERS['not_discoverable']!r}; "
        f"got blocker_ids={ids!r}"
    )


def test_check_skill_preflight_surfaces_resolved_name(preflight: Any) -> None:
    """AC1+AC2: the validator must surface the resolved Skill name for callers.

    Downstream preflight consumers (the optimizer pipeline) need the
    resolved name to map the preflight result back to the artifact
    that was asked about. The payload must carry ``name`` with the
    resolved value (or an empty string when not resolved).
    """
    result = preflight.check_skill_preflight(SAMPLE_SKILL_OK)
    assert isinstance(result, dict)
    assert result.get("name") == "metacrucible", (
        f"valid Skill sentinel must surface the resolved name "
        f"{'metacrucible'!r}; got name={result.get('name')!r}"
    )


# --------------------------------------------------------------------------- #
# AC3 — Skill and subagent paths are both covered                             #
# --------------------------------------------------------------------------- #


def test_check_subagent_preflight_passes_on_valid_sentinel(preflight: Any) -> None:
    """AC3: a well-formed subagent sentinel passes preflight."""
    result = preflight.check_subagent_preflight(SAMPLE_SUBAGENT_OK)
    assert isinstance(result, dict)
    assert result.get("ok") is True, (
        f"well-formed subagent sentinel must produce ok=True; got {result!r}"
    )
    assert _blocker_ids(result) == []


def test_check_subagent_preflight_blocks_on_missing_sentinel(preflight: Any) -> None:
    """AC3: missing subagent sentinel blocks preflight with the subagent blocker id."""
    result = preflight.check_subagent_preflight("no sentinel here")
    assert result.get("ok") is False
    assert EXPECTED_SUBAGENT_BLOCKERS["missing"] in _blocker_ids(result), (
        f"missing subagent sentinel must emit blocker id "
        f"{EXPECTED_SUBAGENT_BLOCKERS['missing']!r}; "
        f"got blocker_ids={_blocker_ids(result)!r}"
    )


def test_check_subagent_preflight_blocks_on_malformed_sentinel(
    preflight: Any,
) -> None:
    """AC3: malformed subagent sentinel emits the subagent malformed blocker id."""
    result = preflight.check_subagent_preflight(SAMPLE_SUBAGENT_MALFORMED)
    assert result.get("ok") is False
    assert EXPECTED_SUBAGENT_BLOCKERS["malformed"] in _blocker_ids(result), (
        f"malformed subagent sentinel must emit blocker id "
        f"{EXPECTED_SUBAGENT_BLOCKERS['malformed']!r}; "
        f"got blocker_ids={_blocker_ids(result)!r}"
    )


def test_check_subagent_preflight_blocks_on_not_discoverable(
    preflight: Any,
) -> None:
    """AC3: well-formed ``discoverable=no`` subagent sentinel blocks preflight."""
    result = preflight.check_subagent_preflight(SAMPLE_SUBAGENT_NOT_DISCOVERABLE)
    assert result.get("ok") is False
    assert EXPECTED_SUBAGENT_BLOCKERS["not_discoverable"] in _blocker_ids(result), (
        f"``discoverable=no`` subagent sentinel must emit blocker id "
        f"{EXPECTED_SUBAGENT_BLOCKERS['not_discoverable']!r}; "
        f"got blocker_ids={_blocker_ids(result)!r}"
    )


def test_skill_and_subagent_sentinels_are_disjoint(preflight: Any) -> None:
    """AC3 (negative): the Skill and subagent sentinels must not cross-satisfy.

    A Skill-only sentinel must not accidentally satisfy the
    subagent check (or vice versa). The two prefixes are disjoint
    by construction; the validators must reject the wrong prefix
    on the wrong path with the missing-sentinel blocker (not the
    malformed-sentinel blocker, which is reserved for shape
    violations of the *correct* prefix).
    """
    skill_on_subagent = preflight.check_subagent_preflight(SAMPLE_SKILL_OK)
    assert skill_on_subagent.get("ok") is False, (
        f"a Skill sentinel must not satisfy the subagent preflight; "
        f"got result={skill_on_subagent!r}"
    )
    subagent_on_skill = preflight.check_skill_preflight(SAMPLE_SUBAGENT_OK)
    assert subagent_on_skill.get("ok") is False, (
        f"a subagent sentinel must not satisfy the Skill preflight; "
        f"got result={subagent_on_skill!r}"
    )


# --------------------------------------------------------------------------- #
# Parser-level tests                                                          #
# --------------------------------------------------------------------------- #


def test_parse_skill_sentinel_returns_components_for_valid_output(
    preflight: Any,
) -> None:
    """The parser must extract ``discoverable`` and ``name`` from a valid line."""
    parsed = preflight.parse_skill_sentinel(SAMPLE_SKILL_OK)
    assert isinstance(parsed, dict)
    assert parsed.get("matched") is True, (
        f"parser must report matched=True on the canonical sample; "
        f"got parsed={parsed!r}"
    )
    assert parsed.get("discoverable") == "yes", (
        f"parser must extract discoverable='yes'; got {parsed.get('discoverable')!r}"
    )
    assert parsed.get("name") == "metacrucible", (
        f"parser must extract name='metacrucible'; got {parsed.get('name')!r}"
    )


def test_parse_skill_sentinel_returns_matched_false_for_missing(
    preflight: Any,
) -> None:
    """The parser must clearly signal a non-match (no silent empty success)."""
    parsed = preflight.parse_skill_sentinel("nothing here")
    assert isinstance(parsed, dict)
    assert parsed.get("matched") is False, (
        f"parser must report matched=False when no sentinel is found; "
        f"got parsed={parsed!r}"
    )
