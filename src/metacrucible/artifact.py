"""Capability Artifact parser (Issue #4).

Parse Skill and subagent canonical sources and classify their
frontmatter fields into routing surface, execution parameters, and
mutable body ranges. The classification pins the acceptance criteria
of Issue #4 and the binding ADRs:

  - ADR 0005 (runtime-native canonical sources) — the parser reads the
    runtime-native Markdown source as-is and exposes the parsed
    frontmatter / body for downstream consumers.
  - ADR 0006 (do not automatically mutate routing surfaces) — Skill
    frontmatter fields are classified as routing surface and therefore
    immutable by default.
  - ADR 0019 (treat subagent systemPrompt as body) — ``systemPrompt``
    is surfaced as a mutable body range even though it lives inside
    the frontmatter, and the parser does not classify it as routing.
  - Issue #33 / OPT-1 — :class:`MutableRange` carries
    ``range_id`` (positional) and ``content_hash`` (SHA-256 hex of
    ``.text``) so the optimizer can reference a specific range and
    detect a stale base without re-parsing the artifact. The parser
    is the single producer of these fields; no second convention.

YAML subset
-----------
``pyproject.toml`` does not declare a YAML parser, so this module ships
a small frontmatter parser that is sufficient for the canonical
sources the rest of MetaCrucible produces:

  - top-level ``key: value`` scalars
  - block sequences (``tools``, ``spawns``) with simple string items
  - block scalar (``|``) for ``systemPrompt``
  - boolean / integer / float / quoted-string scalar coercion

A full YAML 1.2 implementation is intentionally out of scope: the
fixtures in ``tests/test_capability_artifact_parser.py`` and the
canonical sources from ADR 0005 only exercise the shapes above.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping

__all__ = [
    "MutableRange",
    "SKILL_ROUTING_FIELDS",
    "SUBAGENT_EXECUTION_PARAMS",
    "SUBAGENT_ROUTING_FIELDS",
    "SkillArtifact",
    "SubagentArtifact",
    "parse_skill",
    "parse_subagent",
]


@dataclass(frozen=True)
class MutableRange:
    """An editable span of canonical source text.

    Each mutable range carries its own text so optimizers can read the
    body without re-parsing the artifact. ``.text`` is the canonical
    attribute; the parser is the single producer of these objects.

    The ``range_id`` and ``content_hash`` fields (Issue #33 / OPT-1)
    pin the range's stable identity so the optimizer can detect a
    stale base (the base hash shifted) and reference a specific range
    in ``edit_suggestion`` records without re-parsing the artifact.
    The parser is the single producer: it assigns ``range_id`` as the
    positional index of the range in the artifact's
    ``mutable_ranges`` tuple (0-based) and ``content_hash`` as the
    lowercase SHA-256 hex digest of ``.text`` UTF-8 bytes. Callers
    MUST NOT construct a ``MutableRange`` with arbitrary
    ``range_id`` / ``content_hash`` values; the two fields are
    parser-owned so a stale-base check can compare them
    field-for-field without trusting caller-supplied state.
    """

    text: str
    range_id: int = 0
    content_hash: str = ""


@dataclass(frozen=True)
class SkillArtifact:
    """Parsed Skill capability artifact.

    The Skill classification pins ADR 0006: frontmatter routing-surface
    fields (``name``, ``description``) are immutable by default, and
    the Markdown body is the only mutable range. Skills have no
    execution parameters in the MVP.
    """

    routing_surface: frozenset[str]
    execution_params: frozenset[str]
    mutable_ranges: tuple[MutableRange, ...]
    frontmatter: Mapping[str, Any]
    body: str


@dataclass(frozen=True)
class SubagentArtifact:
    """Parsed subagent capability artifact.

    The subagent classification pins ADR 0006 and ADR 0019:
    ``name``/``description``/``tools``/``spawns``/``output`` are
    routing surface (immutable by default); ``model``/``thinkingLevel``/
    ``readSummarize``/``blocking``/``autoloadSkills`` are execution
    parameters (may be revised); the ``systemPrompt`` block scalar and
    the Markdown body are mutable body ranges.
    """

    routing_surface: frozenset[str]
    execution_params: frozenset[str]
    mutable_ranges: tuple[MutableRange, ...]
    frontmatter: Mapping[str, Any]
    body: str


# --- Field classification (ADR 0006, ADR 0019) ---------------------------- #

SKILL_ROUTING_FIELDS: frozenset[str] = frozenset({"name", "description"})

SUBAGENT_ROUTING_FIELDS: frozenset[str] = frozenset(
    {"name", "description", "tools", "spawns", "output"}
)
SUBAGENT_EXECUTION_PARAMS: frozenset[str] = frozenset(
    {"model", "thinkingLevel", "readSummarize", "blocking", "autoloadSkills"}
)


# --- Frontmatter parser (minimal YAML subset) ----------------------------- #

_FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*\n(?P<front>.*?)\n---[ \t]*\n(?P<body>.*)\Z",
    re.DOTALL,
)


def _split_frontmatter(source: str) -> tuple[str, str]:
    """Split ``source`` into ``(frontmatter_text, body_text)``.

    Raises ``ValueError`` if the source does not start with a YAML
    frontmatter block delimited by ``---`` lines. The regex pins the
    MVP shape: opening delimiter, frontmatter, closing delimiter, and
    a body that may be empty.
    """
    match = _FRONTMATTER_RE.match(source)
    if match is None:
        raise ValueError(
            "source must start with a YAML frontmatter block delimited "
            "by '---' lines"
        )
    return match.group("front"), match.group("body")


def _coerce_scalar(text: str) -> Any:
    """Coerce a string scalar to bool / int / float / str.

    The order matters: ``true`` / ``false`` are checked before
    ``int``/``float`` so the bool tokens are not misparsed. Quoted
    scalars (``"foo"``, ``'foo'``) are unwrapped; everything else
    stays a plain ``str``.
    """
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in ("null", "~", ""):
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        return text[1:-1]
    return text


def _parse_frontmatter(text: str) -> dict[str, Any]:
    """Parse a minimal YAML frontmatter subset.

    Supports:
      - top-level ``key: value`` scalars
      - block sequences of string items (``- search``)
      - block scalars (``key: |``) that collect indented lines until
        the indent drops back to zero

    The parser is deliberately narrow: full YAML 1.2 is out of scope
    for the parser skeleton. Unknown constructs raise ``ValueError``
    with a line number so a malformed canonical source fails loud.
    """
    result: dict[str, Any] = {}
    lines = text.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if line[:1] in (" ", "\t") or line.startswith("- "):
            raise ValueError(
                f"unanchored list/scalar at line {i + 1}: {line!r}"
            )
        if ":" not in line:
            raise ValueError(
                f"expected 'key: value' on line {i + 1}; got {line!r}"
            )
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value == "|":
            i += 1
            collected: list[str] = []
            indent: int | None = None
            while i < n:
                cont = lines[i]
                if not cont.strip():
                    collected.append("")
                    i += 1
                    continue
                lead = len(cont) - len(cont.lstrip(" "))
                if lead == 0:
                    break
                if indent is None:
                    indent = lead
                collected.append(
                    cont[indent:] if len(cont) >= indent else cont.lstrip()
                )
                i += 1
            while collected and not collected[0].strip():
                collected.pop(0)
            while collected and not collected[-1].strip():
                collected.pop()
            result[key] = "\n".join(collected)
            continue
        if value == "":
            i += 1
            seq: list[Any] = []
            while i < n:
                cont = lines[i]
                if not cont.strip():
                    i += 1
                    continue
                lead = len(cont) - len(cont.lstrip(" "))
                if lead == 0:
                    break
                if cont.lstrip().startswith("- "):
                    seq.append(_coerce_scalar(cont.lstrip()[2:].strip()))
                    i += 1
                    continue
                break
            result[key] = seq
            continue
        result[key] = _coerce_scalar(value)
        i += 1
    return result


# --- Public parse API ----------------------------------------------------- #

def _content_hash_for(*, text: str) -> str:
    """Return the lowercase SHA-256 hex digest of ``text`` UTF-8 bytes.

    Stable-content hash used by :class:`MutableRange` to pin a
    range's identity for stale-base detection (Issue #33 / OPT-1).
    The function is a tiny, side-effect-free wrapper around
    :func:`hashlib.sha256` so callers do not have to import
    ``hashlib`` and so the parser is the single producer of
    ``content_hash`` values (no second convention).
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_range(*, text: str, range_id: int) -> MutableRange:
    """Build a :class:`MutableRange` with parser-owned identity fields.

    Centralizes the ``range_id`` / ``content_hash`` assignment so
    :func:`parse_skill` and :func:`parse_subagent` share a single
    producer. Tests and downstream code MUST go through this helper
    (or the parsers themselves) rather than constructing
    ``MutableRange`` instances directly.
    """
    return MutableRange(
        text=text,
        range_id=range_id,
        content_hash=_content_hash_for(text=text),
    )


def parse_skill(source: str) -> SkillArtifact:
    """Parse a Skill capability artifact from its canonical source.

    The Markdown body (everything after the closing ``---``) is
    exposed as the single mutable body range, with ``range_id=0``
    and ``content_hash`` set to the SHA-256 hex of the body
    (Issue #33 / OPT-1: the parser is the single producer of
    ``range_id`` and ``content_hash``). Skill frontmatter
    routing-surface fields (``name``, ``description``) are reported
    on ``routing_surface``; ``execution_params`` is always empty for
    Skills in the MVP.
    """
    front, body = _split_frontmatter(source)
    frontmatter = _parse_frontmatter(front)
    routing = frozenset(
        key for key in frontmatter if key in SKILL_ROUTING_FIELDS
    )
    body_range = _make_range(text=body, range_id=0)
    return SkillArtifact(
        routing_surface=routing,
        execution_params=frozenset(),
        mutable_ranges=(body_range,),
        frontmatter=frontmatter,
        body=body,
    )


def parse_subagent(source: str) -> SubagentArtifact:
    """Parse a subagent capability artifact from its canonical source.

    The ``systemPrompt`` block scalar is treated as a mutable body
    range per ADR 0019, alongside the Markdown body that follows the
    closing ``---``. Routing surface and execution parameter fields
    are reported disjointly so the optimizer can refuse to touch
    routing surface mutations. ``range_id`` is assigned in
    declaration order (system prompt first, then Markdown body) so
    edit suggestions carry a stable, parser-owned range id
    (Issue #33 / OPT-1).
    """
    front, body = _split_frontmatter(source)
    frontmatter = _parse_frontmatter(front)
    routing = frozenset(
        key for key in frontmatter if key in SUBAGENT_ROUTING_FIELDS
    )
    execution = frozenset(
        key for key in frontmatter if key in SUBAGENT_EXECUTION_PARAMS
    )
    ranges: list[MutableRange] = []
    system_prompt = frontmatter.get("systemPrompt", "")
    if isinstance(system_prompt, str) and system_prompt:
        ranges.append(_make_range(text=system_prompt, range_id=len(ranges)))
    if body:
        ranges.append(_make_range(text=body, range_id=len(ranges)))
    return SubagentArtifact(
        routing_surface=routing,
        execution_params=execution,
        mutable_ranges=tuple(ranges),
        frontmatter=frontmatter,
        body=body,
    )
