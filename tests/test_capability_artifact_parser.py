"""Tests for Issue #4: Capability Artifact parser.

Issue #4 pins the parser contract for Skill and subagent capability
artifacts:

  - Parse Skill and subagent artifacts.
  - Classify routing-surface fields, execution parameters, and mutable
    body ranges.
  - Treat subagent ``systemPrompt`` as body (mutable range), even
    though it lives in the YAML frontmatter.

These tests are the red step: the parser module does not exist yet,
so importing it must fail. Once the parser lands, the tests will
turn green and pin the classification contract from the acceptance
criteria in Issue #4.

The implementation under test (not yet written) is expected to live
under ``metacrucible.artifact`` and to expose at least:

  - ``parse_skill(source: str)`` → classifies a Skill artifact.
  - ``parse_subagent(source: str)`` → classifies a subagent artifact.
  - The returned object exposes ``routing_surface`` (immutable field
    names), ``execution_params`` (mutable field names), and
    ``mutable_ranges`` (an iterable of body-range objects that carry
    their text — the systemPrompt text must appear among them).

References
----------
- ADR 0005 (runtime-native canonical sources).
- ADR 0006 (do not automatically mutate routing surfaces).
- ADR 0019 (treat subagent systemPrompt as body).
- Issue #4 acceptance criteria.
"""
from __future__ import annotations

from typing import Any, Iterable

import pytest

# Forward reference: the parser module is not implemented yet. The
# import is expected to fail (red step) until Issue #4 lands.
PARSER_MODULE = "metacrucible.artifact"

# --------------------------------------------------------------------------- #
# Sample sources used to exercise the parser                                 #
# --------------------------------------------------------------------------- #
#
# The fixtures are inline (not files on disk) so the parser contract is
# pinned entirely in this test module: a future change to the parser
# cannot break these tests by accidentally moving an example file.

SKILL_SOURCE = (
    "---\n"
    "name: my-skill\n"
    "description: Example skill for parser tests.\n"
    "---\n"
    "\n"
    "# my-skill\n"
    "\n"
    "The skill body. Optimizers are allowed to edit this freely.\n"
)

SUBAGENT_SOURCE = (
    "---\n"
    "name: my-subagent\n"
    "description: Example subagent for parser tests.\n"
    "tools:\n"
    "  - search\n"
    "  - fetch\n"
    "spawns:\n"
    "  - helper-agent\n"
    "output: json\n"
    "model: opus\n"
    "thinkingLevel: medium\n"
    "readSummarize: concise\n"
    "blocking: true\n"
    "autoloadSkills: false\n"
    "systemPrompt: |\n"
    "  You are a helpful subagent.\n"
    "  Optimizers may edit this body freely.\n"
    "---\n"
    "\n"
    "Optional Markdown body after the frontmatter.\n"
    "Edit me just like a Skill body.\n"
)

# Per ADR 0019: routing surface (immutable) and execution parameters
# (mutable) for subagent artifacts. The parser must respect this split.
SUBAGENT_ROUTING_FIELDS: tuple[str, ...] = (
    "name",
    "description",
    "tools",
    "spawns",
    "output",
)
SUBAGENT_EXECUTION_PARAMS: tuple[str, ...] = (
    "model",
    "thinkingLevel",
    "readSummarize",
    "blocking",
    "autoloadSkills",
)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _to_set(value: Any) -> set[str]:
    """Coerce an arbitrary iterable/None into a ``set[str]`` for membership checks."""
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    return {str(item) for item in value}


def _range_text(range_obj: Any) -> str:
    """Return the text carried by a mutable-range object.

    The parser is free to pick the exact shape, but each range must
    carry its text. We accept either an object with a ``.text``/
    ``.value`` attribute, or a raw string.
    """
    if isinstance(range_obj, str):
        return range_obj
    for attr in ("text", "value", "content"):
        text = getattr(range_obj, attr, None)
        if isinstance(text, str):
            return text
    return ""


def _mutable_range_texts(artifact: Any) -> list[str]:
    """Collect the texts of every mutable range on ``artifact``."""
    ranges: Iterable[Any] = getattr(artifact, "mutable_ranges", None) or ()
    return [_range_text(r) for r in ranges]


@pytest.fixture(scope="module")
def parser() -> Any:
    """Import the parser module; the test fails (red step) if it does not exist."""
    import importlib

    try:
        return importlib.import_module(PARSER_MODULE)
    except ImportError as exc:
        pytest.fail(
            f"parser module {PARSER_MODULE!r} is not implemented yet "
            f"(Issue #4 red step). Expected functions: parse_skill, "
            f"parse_subagent. ImportError: {exc}"
        )


# --------------------------------------------------------------------------- #
# AC1 — Skill frontmatter routing fields are immutable by default             #
# --------------------------------------------------------------------------- #


def test_skill_parser_exists(parser: Any) -> None:
    """The parser module must expose a ``parse_skill`` function."""
    assert hasattr(parser, "parse_skill"), (
        f"{PARSER_MODULE!r} must expose a parse_skill(source) function; "
        f"got attributes {sorted(dir(parser))!r}"
    )
    assert callable(parser.parse_skill), (
        f"{PARSER_MODULE!r}.parse_skill must be callable"
    )


def test_skill_routing_surface_includes_name(parser: Any) -> None:
    """AC1: ``name`` is a Skill frontmatter routing-surface field."""
    artifact = parser.parse_skill(SKILL_SOURCE)
    routing = _to_set(getattr(artifact, "routing_surface", None))
    assert "name" in routing, (
        f"Skill frontmatter 'name' must be classified as routing surface; "
        f"got routing_surface={sorted(routing)!r}"
    )


def test_skill_routing_surface_includes_description(parser: Any) -> None:
    """AC1: ``description`` is a Skill frontmatter routing-surface field."""
    artifact = parser.parse_skill(SKILL_SOURCE)
    routing = _to_set(getattr(artifact, "routing_surface", None))
    assert "description" in routing, (
        f"Skill frontmatter 'description' must be classified as routing "
        f"surface; got routing_surface={sorted(routing)!r}"
    )


def test_skill_routing_surface_is_immutable_by_default(parser: Any) -> None:
    """AC1: routing surface fields are NOT also execution params or mutable.

    The "immutable by default" guarantee means routing surface and
    execution params must be disjoint sets. This is the strongest
    statement we can make without over-constraining the parser API:
    a field that is routing must never also be classified as
    execution-params or appear inside a mutable range.
    """
    artifact = parser.parse_skill(SKILL_SOURCE)
    routing = _to_set(getattr(artifact, "routing_surface", None))
    execution = _to_set(getattr(artifact, "execution_params", None))
    overlap = routing & execution
    assert not overlap, (
        f"routing surface and execution params must be disjoint for a "
        f"Skill artifact; overlap={sorted(overlap)!r} "
        f"routing={sorted(routing)!r} execution={sorted(execution)!r}"
    )


def test_skill_body_is_a_mutable_range(parser: Any) -> None:
    """AC1: the Skill Markdown body is exposed as a mutable body range.

    The body is the only place MetaCrucible is allowed to apply patch
    revisions to a Skill, so the parser must surface it as a mutable
    range whose text the optimizer can read.
    """
    artifact = parser.parse_skill(SKILL_SOURCE)
    texts = _mutable_range_texts(artifact)
    body_seen = any("skill body" in (t or "") for t in texts)
    assert body_seen, (
        f"Skill body must appear in mutable_ranges; "
        f"got texts={texts!r}"
    )


# --------------------------------------------------------------------------- #
# AC2 — Subagent name/description/tools/spawns/output are immutable          #
# --------------------------------------------------------------------------- #


def test_subagent_parser_exists(parser: Any) -> None:
    """The parser module must expose a ``parse_subagent`` function."""
    assert hasattr(parser, "parse_subagent"), (
        f"{PARSER_MODULE!r} must expose a parse_subagent(source) function; "
        f"got attributes {sorted(dir(parser))!r}"
    )
    assert callable(parser.parse_subagent), (
        f"{PARSER_MODULE!r}.parse_subagent must be callable"
    )


@pytest.mark.parametrize("field", SUBAGENT_ROUTING_FIELDS)
def test_subagent_routing_field_is_immutable(parser: Any, field: str) -> None:
    """AC2: each of name/description/tools/spawns/output is routing surface."""
    artifact = parser.parse_subagent(SUBAGENT_SOURCE)
    routing = _to_set(getattr(artifact, "routing_surface", None))
    assert field in routing, (
        f"subagent frontmatter {field!r} must be classified as routing "
        f"surface; got routing_surface={sorted(routing)!r}"
    )


def test_subagent_routing_surface_does_not_leak_to_execution(
    parser: Any,
) -> None:
    """AC2 (negative): routing surface fields must NOT be execution params."""
    artifact = parser.parse_subagent(SUBAGENT_SOURCE)
    routing = _to_set(getattr(artifact, "routing_surface", None))
    execution = _to_set(getattr(artifact, "execution_params", None))
    leak = routing & execution
    assert not leak, (
        f"routing surface fields leaked into execution_params; "
        f"overlap={sorted(leak)!r}"
    )


# --------------------------------------------------------------------------- #
# AC3 — Subagent systemPrompt is a body/mutable range                         #
# --------------------------------------------------------------------------- #


def test_subagent_systemprompt_text_is_a_mutable_range(parser: Any) -> None:
    """AC3: the systemPrompt text must appear in ``mutable_ranges``.

    Per ADR 0019, ``systemPrompt`` is treated as body even though it
    lives inside the YAML frontmatter. The parser must therefore
    surface its text as a mutable range — exactly like a Skill body.
    """
    artifact = parser.parse_subagent(SUBAGENT_SOURCE)
    texts = _mutable_range_texts(artifact)
    system_prompt_seen = any(
        "helpful subagent" in (t or "") for t in texts
    )
    assert system_prompt_seen, (
        f"subagent systemPrompt text must be exposed as a mutable body "
        f"range; got mutable range texts={texts!r}"
    )


def test_subagent_markdown_body_remains_a_mutable_range(parser: Any) -> None:
    """AC3 (companion): the Markdown body (after frontmatter) is also mutable.

    The Markdown body of a subagent is just as editable as the
    ``systemPrompt``. The parser must surface it as a mutable range
    too, so the optimizer can pick it as a target.
    """
    artifact = parser.parse_subagent(SUBAGENT_SOURCE)
    texts = _mutable_range_texts(artifact)
    body_seen = any("Optional Markdown body" in (t or "") for t in texts)
    assert body_seen, (
        f"subagent Markdown body must be exposed as a mutable range; "
        f"got mutable range texts={texts!r}"
    )


def test_subagent_systemprompt_is_not_classified_as_routing(
    parser: Any,
) -> None:
    """AC3 (negative): ``systemPrompt`` must NOT appear in routing surface.

    Treating ``systemPrompt`` as body means it must not be lumped in
    with the routing-surface fields that are immutable by default.
    """
    artifact = parser.parse_subagent(SUBAGENT_SOURCE)
    routing = _to_set(getattr(artifact, "routing_surface", None))
    assert "systemPrompt" not in routing, (
        f"subagent 'systemPrompt' must be treated as body, not routing; "
        f"routing_surface={sorted(routing)!r}"
    )


# --------------------------------------------------------------------------- #
# AC4 — Execution params are classified                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("field", SUBAGENT_EXECUTION_PARAMS)
def test_subagent_execution_param_is_classified(
    parser: Any, field: str
) -> None:
    """AC4: each of model/thinkingLevel/readSummarize/blocking/autoloadSkills
    is classified as an execution parameter, distinct from routing."""
    artifact = parser.parse_subagent(SUBAGENT_SOURCE)
    execution = _to_set(getattr(artifact, "execution_params", None))
    assert field in execution, (
        f"subagent frontmatter {field!r} must be classified as an "
        f"execution parameter; got execution_params={sorted(execution)!r}"
    )


def test_subagent_routing_and_execution_are_disjoint(parser: Any) -> None:
    """AC2+AC4: routing surface and execution params are disjoint sets.

    The parser must keep these two classifications mutually exclusive.
    A field that is "routing surface" cannot also be "execution
    param", and vice versa.
    """
    artifact = parser.parse_subagent(SUBAGENT_SOURCE)
    routing = _to_set(getattr(artifact, "routing_surface", None))
    execution = _to_set(getattr(artifact, "execution_params", None))
    overlap = routing & execution
    assert not overlap, (
        f"routing surface and execution params must be disjoint; "
        f"overlap={sorted(overlap)!r} "
        f"routing={sorted(routing)!r} execution={sorted(execution)!r}"
    )


def test_subagent_execution_params_does_not_leak_to_routing(
    parser: Any,
) -> None:
    """AC4 (negative): execution params must NOT appear in routing surface."""
    artifact = parser.parse_subagent(SUBAGENT_SOURCE)
    routing = _to_set(getattr(artifact, "routing_surface", None))
    execution = _to_set(getattr(artifact, "execution_params", None))
    leak = execution & routing
    assert not leak, (
        f"execution params leaked into routing surface; "
        f"overlap={sorted(leak)!r}"
    )
