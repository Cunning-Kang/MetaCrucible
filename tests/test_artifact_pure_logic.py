"""Pure-logic unit tests for the capability artifact parser (Issue #44).

These tests exercise the parser's named pure-logic functions directly
without going through the broader contract tests in
``test_capability_artifact_parser.py``. Each helper is pinned in
isolation so a future change to the parser cannot hide behind the
public end-to-end contract:

  - :func:`parse_skill` — Skill classification end-to-end.
  - :func:`parse_subagent` — Subagent classification end-to-end.
  - :func:`_split_frontmatter` — YAML delimiter split.
  - :func:`_parse_frontmatter` — minimal YAML subset parser.
  - :func:`_coerce_scalar` — bool / null / int / float / str coercion.
  - :func:`_content_hash_for` — SHA-256 hex digest helper.
  - :func:`_make_range` — parser-owned ``MutableRange`` constructor.

Fixtures are inline strings with obviously fake placeholders — no real
secrets, no LLM, network, sleep, or subprocess calls — so the suite
runs deterministically under ``pytest -q``.
"""
from __future__ import annotations

import hashlib

import pytest

from metacrucible.artifact import (
    MutableRange,
    SKILL_ROUTING_FIELDS,
    SUBAGENT_EXECUTION_PARAMS,
    SUBAGENT_ROUTING_FIELDS,
    SkillArtifact,
    SubagentArtifact,
    _coerce_scalar,
    _content_hash_for,
    _make_range,
    _parse_frontmatter,
    _split_frontmatter,
    parse_skill,
    parse_subagent,
)


# --------------------------------------------------------------------------- #
# Sample sources (obviously fake, no real secrets)                            #
# --------------------------------------------------------------------------- #

SKILL_SOURCE = (
    "---\n"
    "name: example-skill\n"
    "description: Demo skill used by pure-logic tests.\n"
    "---\n"
    "\n"
    "# example-skill\n"
    "\n"
    "Skill body. Edit me.\n"
)

SUBAGENT_SOURCE = (
    "---\n"
    "name: example-subagent\n"
    "description: Demo subagent for pure-logic tests.\n"
    "tools:\n"
    "  - search\n"
    "  - fetch\n"
    "spawns:\n"
    "  - helper\n"
    "output: json\n"
    "model: opus\n"
    "thinkingLevel: medium\n"
    "readSummarize: concise\n"
    "blocking: true\n"
    "autoloadSkills: false\n"
    "systemPrompt: |\n"
    "  You are a helpful subagent.\n"
    "  Edit me with the body.\n"
    "---\n"
    "\n"
    "Optional Markdown body after frontmatter.\n"
)

EMPTY_BODY_SKILL_SOURCE = (
    "---\n"
    "name: empty-body-skill\n"
    "description: Skill with no Markdown body.\n"
    "---\n"
)


# --------------------------------------------------------------------------- #
# _coerce_scalar                                                               #
# --------------------------------------------------------------------------- #


class TestCoerceScalar:
    """Pin the scalar coercion precedence: bool → null → int → float → str."""

    def test_true_lowercase_returns_true(self) -> None:
        assert _coerce_scalar("true") is True

    def test_true_capitalized_returns_true(self) -> None:
        # `lowered` is checked for bool tokens, so True/TRUE/TrUe all coerce.
        assert _coerce_scalar("True") is True
        assert _coerce_scalar("TRUE") is True

    def test_false_lowercase_returns_false(self) -> None:
        assert _coerce_scalar("false") is False

    def test_false_capitalized_returns_false(self) -> None:
        assert _coerce_scalar("False") is False
        assert _coerce_scalar("FALSE") is False

    def test_null_token_returns_none(self) -> None:
        assert _coerce_scalar("null") is None

    def test_tilde_token_returns_none(self) -> None:
        assert _coerce_scalar("~") is None

    def test_empty_string_returns_none(self) -> None:
        # Empty token is treated as null — including the bare empty-quoted
        # form, since the null check fires before the unwrap step.
        assert _coerce_scalar("") is None

    def test_positive_int_returns_int(self) -> None:
        result = _coerce_scalar("42")
        assert result == 42
        assert type(result) is int

    def test_negative_int_returns_int(self) -> None:
        result = _coerce_scalar("-7")
        assert result == -7
        assert type(result) is int

    def test_zero_returns_int(self) -> None:
        result = _coerce_scalar("0")
        assert result == 0
        assert type(result) is int

    def test_float_returns_float(self) -> None:
        result = _coerce_scalar("3.14")
        assert result == pytest.approx(3.14)
        assert type(result) is float

    def test_negative_float_returns_float(self) -> None:
        result = _coerce_scalar("-2.5")
        assert result == pytest.approx(-2.5)
        assert type(result) is float

    def test_int_token_does_not_fall_through_to_float(self) -> None:
        # Int is checked before float — "42" must stay an int (not 42.0).
        result = _coerce_scalar("42")
        assert type(result) is int

    def test_double_quoted_string_is_unwrapped(self) -> None:
        assert _coerce_scalar('"hello"') == "hello"

    def test_single_quoted_string_is_unwrapped(self) -> None:
        assert _coerce_scalar("'world'") == "world"

    def test_plain_string_passes_through_unchanged(self) -> None:
        assert _coerce_scalar("plain-text") == "plain-text"

    def test_token_with_inner_quote_is_plain_string(self) -> None:
        # Only unwrapped when the FIRST and LAST chars match a quote.
        assert _coerce_scalar('he"llo') == 'he"llo'

    def test_single_character_passes_through(self) -> None:
        # len(text) < 2 short-circuits the unwrap check.
        assert _coerce_scalar("x") == "x"


# --------------------------------------------------------------------------- #
# _split_frontmatter                                                           #
# --------------------------------------------------------------------------- #


class TestSplitFrontmatter:
    """Pin the frontmatter delimiter contract for `_split_frontmatter`."""

    def test_returns_front_and_body_tuple(self) -> None:
        front, body = _split_frontmatter(SKILL_SOURCE)
        assert front == (
            "name: example-skill\n"
            "description: Demo skill used by pure-logic tests."
        )
        assert body.startswith("\n# example-skill")
        assert "Skill body. Edit me." in body

    def test_empty_body_is_allowed(self) -> None:
        front, body = _split_frontmatter(EMPTY_BODY_SKILL_SOURCE)
        assert "name: empty-body-skill" in front
        assert body == ""

    def test_missing_opening_delimiter_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="frontmatter"):
            _split_frontmatter("name: foo\n---\nbody")

    def test_missing_closing_delimiter_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="frontmatter"):
            _split_frontmatter("---\nname: foo\nbody without close")

    def test_bare_source_without_delimiters_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            _split_frontmatter("just a body, no frontmatter at all")


# --------------------------------------------------------------------------- #
# _parse_frontmatter                                                           #
# --------------------------------------------------------------------------- #


class TestParseFrontmatter:
    """Pin the minimal YAML subset accepted by `_parse_frontmatter`."""

    def test_parses_top_level_scalars(self) -> None:
        text = "name: example\ndescription: a description"
        result = _parse_frontmatter(text)
        assert result == {"name": "example", "description": "a description"}

    def test_coerces_bool_int_float_in_scalar_position(self) -> None:
        text = "flag: true\ncount: 7\nratio: 1.5\nname: plain"
        result = _parse_frontmatter(text)
        assert result["flag"] is True
        assert result["count"] == 7
        assert type(result["count"]) is int
        assert result["ratio"] == pytest.approx(1.5)
        assert result["name"] == "plain"

    def test_unwraps_quoted_strings(self) -> None:
        text = 'double: "hello"\nsingle: \'world\''
        result = _parse_frontmatter(text)
        assert result["double"] == "hello"
        assert result["single"] == "world"

    def test_parses_block_sequence_of_strings(self) -> None:
        text = "tools:\n  - search\n  - fetch\n  - run"
        result = _parse_frontmatter(text)
        assert result["tools"] == ["search", "fetch", "run"]

    def test_block_sequence_items_are_coerced(self) -> None:
        text = "values:\n  - 1\n  - 2\n  - 3"
        result = _parse_frontmatter(text)
        assert result["values"] == [1, 2, 3]
        assert all(type(v) is int for v in result["values"])

    def test_parses_block_scalar(self) -> None:
        text = (
            "systemPrompt: |\n"
            "  first line\n"
            "  second line\n"
        )
        result = _parse_frontmatter(text)
        assert result["systemPrompt"] == "first line\nsecond line"

    def test_block_scalar_strips_leading_and_trailing_blank_lines(self) -> None:
        text = (
            "body: |\n"
            "\n"
            "  only line\n"
            "\n"
        )
        result = _parse_frontmatter(text)
        assert result["body"] == "only line"

    def test_skips_blank_lines_and_full_line_comments(self) -> None:
        text = (
            "# leading comment\n"
            "\n"
            "name: foo\n"
            "\n"
            "# trailing comment\n"
            "count: 5\n"
        )
        result = _parse_frontmatter(text)
        assert result == {"name": "foo", "count": 5}

    def test_raises_on_indented_top_level_line(self) -> None:
        with pytest.raises(ValueError, match="unanchored"):
            _parse_frontmatter("  - oops\n")

    def test_raises_on_dash_at_top_level(self) -> None:
        with pytest.raises(ValueError, match="unanchored"):
            _parse_frontmatter("- item\n")

    def test_raises_on_line_without_colon(self) -> None:
        with pytest.raises(ValueError, match="key: value"):
            _parse_frontmatter("no-colon-here\n")

    def test_empty_text_returns_empty_dict(self) -> None:
        assert _parse_frontmatter("") == {}

    def test_full_subagent_subset_round_trip(self) -> None:
        text = (
            "name: sub\n"
            "description: demo\n"
            "tools:\n"
            "  - search\n"
            "spawns:\n"
            "  - helper\n"
            "output: json\n"
            "model: opus\n"
            "thinkingLevel: medium\n"
            "readSummarize: concise\n"
            "blocking: true\n"
            "autoloadSkills: false\n"
            "systemPrompt: |\n"
            "  body\n"
        )
        result = _parse_frontmatter(text)
        assert result["name"] == "sub"
        assert result["description"] == "demo"
        assert result["tools"] == ["search"]
        assert result["spawns"] == ["helper"]
        assert result["output"] == "json"
        assert result["model"] == "opus"
        assert result["thinkingLevel"] == "medium"
        assert result["readSummarize"] == "concise"
        assert result["blocking"] is True
        assert result["autoloadSkills"] is False
        assert result["systemPrompt"] == "body"


# --------------------------------------------------------------------------- #
# _content_hash_for                                                            #
# --------------------------------------------------------------------------- #


class TestContentHashFor:
    """Pin the SHA-256 hex digest helper."""

    def test_empty_string_matches_well_known_sha256(self) -> None:
        # SHA-256("") is a fixed, well-known value.
        assert _content_hash_for(text="") == (
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )

    def test_known_input_matches_hashlib_sha256(self) -> None:
        # SHA-256("abc") is a standard test vector.
        expected = hashlib.sha256(b"abc").hexdigest()
        assert _content_hash_for(text="abc") == expected

    def test_returns_lowercase_hex_of_length_64(self) -> None:
        result = _content_hash_for(text="some text")
        assert result == result.lower()
        assert len(result) == 64
        assert all(ch in "0123456789abcdef" for ch in result)

    def test_encodes_input_as_utf8(self) -> None:
        # Non-ASCII text must encode as UTF-8 before hashing.
        utf8_text = "héllo-世界"
        expected = hashlib.sha256(utf8_text.encode("utf-8")).hexdigest()
        assert _content_hash_for(text=utf8_text) == expected

    def test_distinct_texts_produce_distinct_hashes(self) -> None:
        assert _content_hash_for(text="alpha") != _content_hash_for(text="beta")

    def test_same_text_produces_same_hash(self) -> None:
        assert _content_hash_for(text="stable") == _content_hash_for(text="stable")


# --------------------------------------------------------------------------- #
# _make_range                                                                  #
# --------------------------------------------------------------------------- #


class TestMakeRange:
    """Pin `MutableRange` construction via the parser-owned helper."""

    def test_text_is_carried_through(self) -> None:
        r = _make_range(text="hello", range_id=0)
        assert r.text == "hello"

    def test_range_id_is_carried_through(self) -> None:
        r = _make_range(text="hello", range_id=3)
        assert r.range_id == 3

    def test_content_hash_matches_helper(self) -> None:
        text = "the body text"
        r = _make_range(text=text, range_id=0)
        assert r.content_hash == _content_hash_for(text=text)

    def test_content_hash_is_lowercase_hex_64_chars(self) -> None:
        r = _make_range(text="x", range_id=0)
        assert len(r.content_hash) == 64
        assert all(ch in "0123456789abcdef" for ch in r.content_hash)

    def test_returns_mutable_range_instance(self) -> None:
        r = _make_range(text="x", range_id=0)
        assert isinstance(r, MutableRange)

    def test_is_hashable_via_frozen_dataclass(self) -> None:
        # MutableRange is a frozen dataclass — must be hashable so callers
        # can use it as a dict key or in a set.
        r = _make_range(text="x", range_id=0)
        # Should not raise.
        hash(r)

    def test_distinct_range_ids_yield_distinct_instances(self) -> None:
        a = _make_range(text="same", range_id=0)
        b = _make_range(text="same", range_id=1)
        assert a != b
        assert a.range_id != b.range_id

    def test_distinct_texts_yield_distinct_hashes(self) -> None:
        a = _make_range(text="alpha", range_id=0)
        b = _make_range(text="beta", range_id=0)
        assert a.content_hash != b.content_hash


# --------------------------------------------------------------------------- #
# parse_skill                                                                  #
# --------------------------------------------------------------------------- #


class TestParseSkill:
    """Pin the public `parse_skill` end-to-end behavior."""

    def test_returns_skill_artifact(self) -> None:
        artifact = parse_skill(SKILL_SOURCE)
        assert isinstance(artifact, SkillArtifact)

    def test_routing_surface_is_name_and_description(self) -> None:
        artifact = parse_skill(SKILL_SOURCE)
        assert artifact.routing_surface == frozenset({"name", "description"})

    def test_routing_surface_matches_classification_constant(self) -> None:
        artifact = parse_skill(SKILL_SOURCE)
        assert artifact.routing_surface == SKILL_ROUTING_FIELDS

    def test_execution_params_are_empty(self) -> None:
        artifact = parse_skill(SKILL_SOURCE)
        assert artifact.execution_params == frozenset()

    def test_frontmatter_is_parsed(self) -> None:
        artifact = parse_skill(SKILL_SOURCE)
        assert artifact.frontmatter["name"] == "example-skill"
        assert (
            artifact.frontmatter["description"]
            == "Demo skill used by pure-logic tests."
        )

    def test_body_field_matches_source_body(self) -> None:
        artifact = parse_skill(SKILL_SOURCE)
        assert "Skill body. Edit me." in artifact.body
        assert artifact.body.startswith("\n# example-skill")

    def test_mutable_ranges_contains_body_with_range_id_zero(self) -> None:
        artifact = parse_skill(SKILL_SOURCE)
        assert len(artifact.mutable_ranges) == 1
        body_range = artifact.mutable_ranges[0]
        assert body_range.range_id == 0
        assert body_range.text == artifact.body

    def test_mutable_range_content_hash_matches_body(self) -> None:
        artifact = parse_skill(SKILL_SOURCE)
        body_range = artifact.mutable_ranges[0]
        assert body_range.content_hash == _content_hash_for(text=artifact.body)

    def test_mutable_range_is_mutable_range_instance(self) -> None:
        artifact = parse_skill(SKILL_SOURCE)
        assert isinstance(artifact.mutable_ranges[0], MutableRange)

    def test_extra_frontmatter_keys_appear_in_frontmatter_not_routing(self) -> None:
        # Even when extra keys are present, only `name`/`description` are
        # classified as routing surface.
        src = (
            "---\n"
            "name: x\n"
            "description: y\n"
            "something: else\n"
            "---\n"
            "body\n"
        )
        artifact = parse_skill(src)
        assert artifact.routing_surface == frozenset({"name", "description"})
        assert artifact.frontmatter["something"] == "else"

    def test_propagates_value_error_for_missing_frontmatter(self) -> None:
        with pytest.raises(ValueError):
            parse_skill("no frontmatter here\n")

    def test_handles_skill_with_no_markdown_body(self) -> None:
        artifact = parse_skill(EMPTY_BODY_SKILL_SOURCE)
        assert artifact.body == ""
        # Even with an empty body, the parser still emits one (empty) range.
        assert len(artifact.mutable_ranges) == 1
        assert artifact.mutable_ranges[0].text == ""
        assert artifact.mutable_ranges[0].range_id == 0


# --------------------------------------------------------------------------- #
# parse_subagent                                                               #
# --------------------------------------------------------------------------- #


class TestParseSubagent:
    """Pin the public `parse_subagent` end-to-end behavior."""

    def test_returns_subagent_artifact(self) -> None:
        artifact = parse_subagent(SUBAGENT_SOURCE)
        assert isinstance(artifact, SubagentArtifact)

    def test_routing_surface_matches_classification(self) -> None:
        artifact = parse_subagent(SUBAGENT_SOURCE)
        assert artifact.routing_surface == SUBAGENT_ROUTING_FIELDS

    def test_routing_surface_includes_each_pinned_field(self) -> None:
        artifact = parse_subagent(SUBAGENT_SOURCE)
        for field in ("name", "description", "tools", "spawns", "output"):
            assert field in artifact.routing_surface

    def test_execution_params_match_classification(self) -> None:
        artifact = parse_subagent(SUBAGENT_SOURCE)
        assert artifact.execution_params == SUBAGENT_EXECUTION_PARAMS

    def test_execution_params_includes_each_pinned_field(self) -> None:
        artifact = parse_subagent(SUBAGENT_SOURCE)
        for field in (
            "model",
            "thinkingLevel",
            "readSummarize",
            "blocking",
            "autoloadSkills",
        ):
            assert field in artifact.execution_params

    def test_routing_and_execution_are_disjoint(self) -> None:
        artifact = parse_subagent(SUBAGENT_SOURCE)
        assert artifact.routing_surface.isdisjoint(artifact.execution_params)

    def test_systemprompt_is_not_in_routing_surface(self) -> None:
        artifact = parse_subagent(SUBAGENT_SOURCE)
        assert "systemPrompt" not in artifact.routing_surface

    def test_systemprompt_is_not_in_execution_params(self) -> None:
        artifact = parse_subagent(SUBAGENT_SOURCE)
        assert "systemPrompt" not in artifact.execution_params

    def test_systemprompt_is_first_mutable_range_with_id_zero(self) -> None:
        artifact = parse_subagent(SUBAGENT_SOURCE)
        assert len(artifact.mutable_ranges) == 2
        first = artifact.mutable_ranges[0]
        assert first.range_id == 0
        assert first.text == artifact.frontmatter["systemPrompt"]

    def test_markdown_body_is_second_mutable_range_with_id_one(self) -> None:
        artifact = parse_subagent(SUBAGENT_SOURCE)
        second = artifact.mutable_ranges[1]
        assert second.range_id == 1
        assert second.text == artifact.body
        assert "Optional Markdown body" in second.text

    def test_content_hashes_match_helper_for_each_range(self) -> None:
        artifact = parse_subagent(SUBAGENT_SOURCE)
        first, second = artifact.mutable_ranges
        assert first.content_hash == _content_hash_for(text=first.text)
        assert second.content_hash == _content_hash_for(text=second.text)

    def test_frontmatter_parses_lists_and_bools(self) -> None:
        artifact = parse_subagent(SUBAGENT_SOURCE)
        assert artifact.frontmatter["tools"] == ["search", "fetch"]
        assert artifact.frontmatter["spawns"] == ["helper"]
        assert artifact.frontmatter["blocking"] is True
        assert artifact.frontmatter["autoloadSkills"] is False
        assert artifact.frontmatter["output"] == "json"
        assert artifact.frontmatter["model"] == "opus"

    def test_subagent_without_systemprompt_has_only_body_range(self) -> None:
        src = (
            "---\n"
            "name: sub\n"
            "description: demo\n"
            "model: opus\n"
            "blocking: true\n"
            "---\n"
            "\n"
            "Markdown body here.\n"
        )
        artifact = parse_subagent(src)
        assert artifact.frontmatter.get("systemPrompt", "") == ""
        assert len(artifact.mutable_ranges) == 1
        assert artifact.mutable_ranges[0].range_id == 0
        assert "Markdown body here." in artifact.mutable_ranges[0].text

    def test_subagent_with_empty_body_skips_body_range(self) -> None:
        src = (
            "---\n"
            "name: sub\n"
            "description: demo\n"
            "systemPrompt: |\n"
            "  only prompt, no body\n"
            "---\n"
        )
        artifact = parse_subagent(src)
        # systemPrompt present, body empty → exactly one mutable range.
        assert len(artifact.mutable_ranges) == 1
        assert artifact.mutable_ranges[0].range_id == 0
        assert artifact.mutable_ranges[0].text == "only prompt, no body"
        assert artifact.body == ""

    def test_subagent_missing_both_prompt_and_body_has_no_ranges(self) -> None:
        src = (
            "---\n"
            "name: sub\n"
            "description: demo\n"
            "---\n"
        )
        artifact = parse_subagent(src)
        assert artifact.mutable_ranges == ()
        assert artifact.body == ""

    def test_propagates_value_error_for_missing_frontmatter(self) -> None:
        with pytest.raises(ValueError):
            parse_subagent("no frontmatter at all")