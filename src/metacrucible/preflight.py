"""Claude Code adapter preflight sentinel (Issue #9).

The Claude Code adapter runs an "Adapter Preflight" (CONTEXT.md)
before execution evaluation to verify that a materialized Skill or
injected subagent is discoverable by the agent runtime. Per
ADR 0028 the preflight must use a fixed one-line sentinel instead
of free-form model behavior:

    METACRUCIBLE_SKILL_DISCOVERABLE=<yes|no>; NAME=<resolved-name-or-empty>

The Skill prefix is pinned by ADR 0028; the subagent prefix mirrors
the same shape with its own prefix to cover the subagent path
required by Issue #9 AC3. Missing or mismatched sentinel output
blocks preflight, and the validator returns machine-stable blocker
ids that downstream automation (the optimizer pipeline) branches
on verbatim.

The module exposes:

  - :data:`SKILL_SENTINEL_PREFIX` / :data:`SUBAGENT_SENTINEL_PREFIX` —
    the documented sentinel string fragments (ADR 0028, Issue #9
    AC1).
  - :func:`skill_preflight_prompt` / :func:`subagent_preflight_prompt`
    — return the exact prompt the Claude Code adapter sends, with
    the sentinel format spelled out so the model has the format
    contract in front of it.
  - :func:`parse_skill_sentinel` / :func:`parse_subagent_sentinel` —
    extract the sentinel components from a raw model response.
  - :func:`check_skill_preflight` / :func:`check_subagent_preflight` —
    return ``{"ok": bool, "blockers": [...], "name": str, "discoverable": str}``
    matching the ``init --check`` / ``promote`` / ``load_benchmark``
    blocker shape.
"""
from __future__ import annotations

import re
from typing import Any

__all__ = [
    "SKILL_SENTINEL_PREFIX",
    "SUBAGENT_SENTINEL_PREFIX",
    "SKILL_SENTINEL_MISSING_BLOCKER",
    "SKILL_SENTINEL_MALFORMED_BLOCKER",
    "SKILL_NOT_DISCOVERABLE_BLOCKER",
    "SUBAGENT_SENTINEL_MISSING_BLOCKER",
    "SUBAGENT_SENTINEL_MALFORMED_BLOCKER",
    "SUBAGENT_NOT_DISCOVERABLE_BLOCKER",
    "skill_preflight_prompt",
    "subagent_preflight_prompt",
    "parse_skill_sentinel",
    "parse_subagent_sentinel",
    "check_skill_preflight",
    "check_subagent_preflight",
]

# --------------------------------------------------------------------------- #
# Sentinel contract (ADR 0028, Issue #9 AC1)                                  #
# --------------------------------------------------------------------------- #

#: Skill sentinel prefix pinned by ADR 0028. The downstream format
#: is ``METACRUCIBLE_SKILL_DISCOVERABLE=<yes|no>; NAME=<...>``.
SKILL_SENTINEL_PREFIX: str = "METACRUCIBLE_SKILL_DISCOVERABLE"

#: Subagent sentinel prefix (Issue #9 AC3). Mirrors the Skill
#: shape with a disjoint prefix so the two paths do not cross-match.
SUBAGENT_SENTINEL_PREFIX: str = "METACRUCIBLE_SUBAGENT_DISCOVERABLE"

#: Documented sentinel values for the ``<yes|no>`` field.
_SENTINEL_VALUE_YES: str = "yes"
_SENTINEL_VALUE_NO: str = "no"

# Regex that captures a well-formed sentinel line. Anchored to the
# prefix so the wrong-prefix case (Skill sentinel on the subagent
# path) is treated as "missing", not "malformed" (the malformed
# blocker is reserved for shape violations of the *correct* prefix).
_SENTINEL_LINE_RE: re.Pattern[str] = re.compile(
    r"^(?P<prefix>METACRUCIBLE_(?:SKILL|SUBAGENT)_DISCOVERABLE)"
    r"=(?P<value>yes|no)"
    r";\s*NAME=(?P<name>.*)$"
)

# Stable blocker ids. These are the machine contract: tests and
# downstream automation branch on the exact strings.
SKILL_SENTINEL_MISSING_BLOCKER: str = "skill-preflight-sentinel-missing"
SKILL_SENTINEL_MALFORMED_BLOCKER: str = "skill-preflight-sentinel-malformed"
SKILL_NOT_DISCOVERABLE_BLOCKER: str = "skill-preflight-not-discoverable"
SUBAGENT_SENTINEL_MISSING_BLOCKER: str = "subagent-preflight-sentinel-missing"
SUBAGENT_SENTINEL_MALFORMED_BLOCKER: str = "subagent-preflight-sentinel-malformed"
SUBAGENT_NOT_DISCOVERABLE_BLOCKER: str = "subagent-preflight-not-discoverable"

# Blockers by prefix. Kept in one place so the cross-prefix test
# (``test_skill_and_subagent_sentinels_are_disjoint``) can branch
# on the same dict that drives the validator.
_PREFIX_TO_BLOCKERS: dict[str, dict[str, str]] = {
    SKILL_SENTINEL_PREFIX: {
        "missing": SKILL_SENTINEL_MISSING_BLOCKER,
        "malformed": SKILL_SENTINEL_MALFORMED_BLOCKER,
        "not_discoverable": SKILL_NOT_DISCOVERABLE_BLOCKER,
    },
    SUBAGENT_SENTINEL_PREFIX: {
        "missing": SUBAGENT_SENTINEL_MISSING_BLOCKER,
        "malformed": SUBAGENT_SENTINEL_MALFORMED_BLOCKER,
        "not_discoverable": SUBAGENT_NOT_DISCOVERABLE_BLOCKER,
    },
}


# --------------------------------------------------------------------------- #
# Prompts (ADR 0028, Issue #9 AC1)                                            #
# --------------------------------------------------------------------------- #

_SKILL_PROMPT_TEMPLATE: str = (
    "You are running the MetaCrucible Claude Code adapter Preflight\n"
    "(ADR 0028). Reply with exactly one line, in this exact format:\n"
    "\n"
    "    {prefix}=<yes|no>; NAME=<resolved-name-or-empty>\n"
    "\n"
    "Replace ``<yes|no>`` with ``yes`` if the Skill named below is\n"
    "discoverable in this agent runtime, or ``no`` otherwise. Replace\n"
    "``NAME`` with the resolved Skill name (or leave it empty if you\n"
    "could not resolve it). Do not emit any other text, code fences,\n"
    "or commentary before or after the sentinel line.\n"
    "\n"
    "Skill name: {skill_name}\n"
)

_SUBAGENT_PROMPT_TEMPLATE: str = (
    "You are running the MetaCrucible Claude Code adapter Preflight\n"
    "(ADR 0028). Reply with exactly one line, in this exact format:\n"
    "\n"
    "    {prefix}=<yes|no>; NAME=<resolved-name-or-empty>\n"
    "\n"
    "Replace ``<yes|no>`` with ``yes`` if the subagent named below is\n"
    "discoverable in this agent runtime, or ``no`` otherwise. Replace\n"
    "``NAME`` with the resolved subagent name (or leave it empty if\n"
    "you could not resolve it). Do not emit any other text, code\n"
    "fences, or commentary before or after the sentinel line.\n"
    "\n"
    "Subagent name: {subagent_name}\n"
)


def skill_preflight_prompt(skill_name: str = "") -> str:
    """Return the exact preflight prompt for Skill discovery (ADR 0028).

    The returned string contains the literal sentinel format
    ``METACRUCIBLE_SKILL_DISCOVERABLE=<yes|no>; NAME=<...>`` so the
    model has the format contract in front of it (Issue #9 AC1).
    """
    return _SKILL_PROMPT_TEMPLATE.format(
        prefix=SKILL_SENTINEL_PREFIX,
        skill_name=skill_name or "<unknown>",
    )


def subagent_preflight_prompt(subagent_name: str = "") -> str:
    """Return the exact preflight prompt for subagent discovery (ADR 0028).

    The returned string contains the literal sentinel format
    ``METACRUCIBLE_SUBAGENT_DISCOVERABLE=<yes|no>; NAME=<...>``
    mirroring the Skill shape (Issue #9 AC3).
    """
    return _SUBAGENT_PROMPT_TEMPLATE.format(
        prefix=SUBAGENT_SENTINEL_PREFIX,
        subagent_name=subagent_name or "<unknown>",
    )


# --------------------------------------------------------------------------- #
# Parsers                                                                     #
# --------------------------------------------------------------------------- #


def parse_skill_sentinel(output: str) -> dict[str, Any]:
    """Parse a Skill preflight ``output`` and return the sentinel components.

    The parser is strict: the sentinel line must start with the
    Skill prefix; lines that start with a different prefix (e.g.
    the subagent prefix) are treated as "not matched". This
    prevents a Skill sentinel from accidentally satisfying the
    subagent check (Issue #9 AC3, negative).

    Returns
    -------
    dict
        ``{"matched": bool, "discoverable": "yes"|"no"|None,
        "name": str, "reason": str}``. ``name`` is always a string
        (empty when not resolved). ``reason`` is set when
        ``matched`` is ``False``.
    """
    return _parse_sentinel(output, SKILL_SENTINEL_PREFIX)


def parse_subagent_sentinel(output: str) -> dict[str, Any]:
    """Parse a subagent preflight ``output`` (mirror of :func:`parse_skill_sentinel`)."""
    return _parse_sentinel(output, SUBAGENT_SENTINEL_PREFIX)


def _parse_sentinel(output: str, expected_prefix: str) -> dict[str, Any]:
    """Shared sentinel parser used by both Skill and subagent paths.

    Strategy: look for a line in ``output`` that starts with
    ``expected_prefix`` and matches :data:`_SENTINEL_LINE_RE`. If
    no such line exists, report ``matched=False``. If a line
    starts with the expected prefix but does not match the regex
    (e.g. ``METACRUCIBLE_SKILL_DISCOVERABLE=maybe; NAME=foo``),
    return ``matched=False`` with ``reason="malformed"`` so the
    caller can emit the malformed-sentinel blocker.

    Lines that start with a *different* prefix (the wrong path)
    are treated as "missing" so the disjoint-sentinel guarantee
    from Issue #9 AC3 is preserved.
    """
    if not isinstance(output, str):
        return _unmatched(expected_prefix, reason="missing")
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if not line.startswith(expected_prefix):
            # Either a different sentinel prefix (wrong path) or
            # an unrelated line; either way the expected sentinel
            # is not present.
            if line.startswith("METACRUCIBLE_") and "DISCOVERABLE" in line:
                # Some other prefix was emitted: keep scanning in
                # case a second line carries the expected prefix.
                continue
            continue
        match = _SENTINEL_LINE_RE.match(line)
        if match is None:
            return _unmatched(expected_prefix, reason="malformed")
        return {
            "matched": True,
            "discoverable": match.group("value"),
            "name": match.group("name"),
            "reason": "",
        }
    return _unmatched(expected_prefix, reason="missing")


def _unmatched(expected_prefix: str, *, reason: str) -> dict[str, Any]:
    """Build a uniform "not matched" parse result."""
    return {
        "matched": False,
        "discoverable": None,
        "name": "",
        "reason": reason,
        "expected_prefix": expected_prefix,
    }


# --------------------------------------------------------------------------- #
# Validators                                                                  #
# --------------------------------------------------------------------------- #


def check_skill_preflight(output: str) -> dict[str, Any]:
    """Validate a Skill preflight ``output`` and return the result.

    The returned dict matches the shape used by ``init --check``,
    ``promote``, and :func:`metacrucible.benchmark.load_benchmark`:

      - ``ok`` (bool) — ``True`` iff the sentinel is well-formed
        and reports ``discoverable=yes``.
      - ``blockers`` (list[dict]) — empty when ``ok`` is ``True``;
        otherwise carries the stable blocker ids from
        :data:`SKILL_SENTINEL_MISSING_BLOCKER`,
        :data:`SKILL_SENTINEL_MALFORMED_BLOCKER`, or
        :data:`SKILL_NOT_DISCOVERABLE_BLOCKER`.
      - ``name`` (str) — the resolved Skill name, or an empty
        string when the sentinel is absent / malformed.
      - ``discoverable`` (str | None) — ``"yes"`` / ``"no"`` from
        the sentinel, or ``None`` when the sentinel is absent /
        malformed.
    """
    return _check_preflight(output, SKILL_SENTINEL_PREFIX)


def check_subagent_preflight(output: str) -> dict[str, Any]:
    """Validate a subagent preflight ``output`` (mirror of :func:`check_skill_preflight`)."""
    return _check_preflight(output, SUBAGENT_SENTINEL_PREFIX)


def _check_preflight(output: str, expected_prefix: str) -> dict[str, Any]:
    """Shared validator that maps a parse result to the blocker contract."""
    parsed = _parse_sentinel(output, expected_prefix)
    blockers_map = _PREFIX_TO_BLOCKERS[expected_prefix]

    if not parsed["matched"]:
        reason = parsed.get("reason", "missing")
        blocker_id = blockers_map.get(reason, blockers_map["missing"])
        return {
            "ok": False,
            "blockers": [
                {
                    "id": blocker_id,
                    "message": _blocker_message(blocker_id),
                }
            ],
            "name": parsed.get("name", ""),
            "discoverable": parsed.get("discoverable"),
        }

    discoverable = parsed["discoverable"]
    name = parsed["name"]
    if discoverable == _SENTINEL_VALUE_YES:
        return {
            "ok": True,
            "blockers": [],
            "name": name,
            "discoverable": discoverable,
        }
    # discoverable == "no" is a well-formed but semantically
    # blocking answer.
    blocker_id = blockers_map["not_discoverable"]
    return {
        "ok": False,
        "blockers": [
            {
                "id": blocker_id,
                "message": _blocker_message(blocker_id),
            }
        ],
        "name": name,
        "discoverable": discoverable,
    }


def _blocker_message(blocker_id: str) -> str:
    """Return the human message for a preflight blocker id."""
    messages: dict[str, str] = {
        SKILL_SENTINEL_MISSING_BLOCKER: (
            "Skill preflight output did not contain the required "
            f"{SKILL_SENTINEL_PREFIX}=<yes|no>; NAME=<...> sentinel "
            "(ADR 0028, Issue #9 AC2)"
        ),
        SKILL_SENTINEL_MALFORMED_BLOCKER: (
            "Skill preflight output contained the "
            f"{SKILL_SENTINEL_PREFIX} prefix but the line did not match "
            "the exact sentinel format (ADR 0028, Issue #9 AC2)"
        ),
        SKILL_NOT_DISCOVERABLE_BLOCKER: (
            "Skill preflight returned ``discoverable=no``; the Skill is "
            "not discoverable in this agent runtime (ADR 0028, Issue #9 AC2)"
        ),
        SUBAGENT_SENTINEL_MISSING_BLOCKER: (
            "subagent preflight output did not contain the required "
            f"{SUBAGENT_SENTINEL_PREFIX}=<yes|no>; NAME=<...> sentinel "
            "(ADR 0028, Issue #9 AC2+AC3)"
        ),
        SUBAGENT_SENTINEL_MALFORMED_BLOCKER: (
            "subagent preflight output contained the "
            f"{SUBAGENT_SENTINEL_PREFIX} prefix but the line did not "
            "match the exact sentinel format (ADR 0028, Issue #9 AC2+AC3)"
        ),
        SUBAGENT_NOT_DISCOVERABLE_BLOCKER: (
            "subagent preflight returned ``discoverable=no``; the "
            "subagent is not discoverable in this agent runtime "
            "(ADR 0028, Issue #9 AC2+AC3)"
        ),
    }
    return messages.get(
        blocker_id,
        f"preflight blocked (id={blocker_id})",
    )
