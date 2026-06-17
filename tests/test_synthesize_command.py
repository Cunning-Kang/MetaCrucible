"""Tests for Issue #41 (PRD F4 ``metacrucible synthesize``).

Task 1 ships ONLY the parser shell + a temporary
``synthesize-not-implemented`` placeholder; Task 2 wires the real
synthesis pipeline in (input resolution, draft canonical source,
generated cases, workspace writes). These tests pin the public
parser contract that subsequent tasks must not break AND the
Task 2 command-level contract:

  - ``synthesize`` is a registered subcommand of
    ``metacrucible`` (reachable from both ``python -m metacrucible``
    and the ``metacrucible`` console script).
  - A positional ``capability_need`` argument captures the freeform
    capability-need text.
  - ``--from <path>`` (stored on the namespace as ``from_spec``)
    captures the spec-file alternative input mode.
  - The two input modes are mutually exclusive: providing both, or
    neither, must raise ``SystemExit(2)`` at the parser level so
    automation sees a stable usage-error exit code (Issue #27).
  - Shared CLI flags (``--output``, ``--max-rounds``,
    ``--allow-routing-revision``, ``--allow-dirty-unrelated``,
    ``--confirm-resume``, ``--json``) are wired and expose their
    ``--no-...`` counterparts as the snake_case namespace
    attributes that the dispatcher and downstream command code
    read.

The real synthesis pipeline (workspace creation, baseline write,
benchmark generation, optimization loop wiring) lands in later
tasks; those tests live in subsequent Task-N files.

Task 2 contract pinned by the command-level tests below:

  - A draft canonical source is produced and a baseline is recorded.
  - Generated Evaluation Cases are produced for the draft and held
    pending review (the same
    :data:`metacrucible.benchmark.STATUS_GENERATED` sentinel +
    :data:`metacrucible.benchmark.BOOTSTRAP_PENDING_REVIEW_FIELD`
    envelope mechanism as F2 ``bootstrap``).
  - The blocker id ``synthesize-not-implemented`` from Task 1 is
    REMOVED; valid input creates the workspace and exits with
    :data:`metacrucible.exit_codes.EXIT_OK` and a
    ``draft_pending_review`` outcome.
  - Precondition blockers (missing spec path, empty spec content,
    existing output directory) return
    :data:`metacrucible.exit_codes.EXIT_BLOCKED` with stable ids
    and do NOT create the workspace.
"""
from __future__ import annotations

import argparse
import json
import pathlib
from pathlib import Path

import pytest

from metacrucible.benchmark import SPLIT_EVAL, SPLIT_HELD_OUT, STATUS_GENERATED
from metacrucible.exit_codes import EXIT_BLOCKED, EXIT_OK
from metacrucible.optimizer import ROUND_BUDGET_DEFAULT
from metacrucible.synthesize import (
    BENCHMARK_FILE_NAME,
    BOOTSTRAP_PENDING_REVIEW_FIELD,
    SYNTHESIZE_DRAFT_PENDING_REVIEW,
    SYNTHESIZE_INPUT_MISSING_BLOCKER,
    SYNTHESIZE_OUTPUT_EXISTS_BLOCKER,
    SYNTHESIZE_SPEC_EMPTY_BLOCKER,
    SYNTHESIZE_SPEC_MISSING_BLOCKER,
)


def test_synthesize_parser_accepts_positional_capability_need(
    tmp_path: Path,
) -> None:
    """AC0 (parser): ``synthesize <need> --output <path>`` parses cleanly.

    Asserts the contract surfaced by the dispatcher's happy path:
    a freeform ``capability_need`` positional plus an ``--output``
    path, with the shared flags present and ``--json`` defaulting
    off. The optimizer round budget default is loaded from
    :data:`metacrucible.optimizer.ROUND_BUDGET_DEFAULT` so the
    shared flag defaulting mirrors :mod:`metacrucible.__main__`
    exactly.
    """
    from metacrucible.__main__ import _build_parser

    parser = _build_parser()
    output_path = tmp_path / "skill"
    args = parser.parse_args(
        [
            "synthesize",
            "write a skill",
            "--output",
            str(output_path),
        ]
    )

    assert args.command == "synthesize", (
        f"args.command must be 'synthesize'; got {args.command!r}"
    )
    assert args.capability_need == "write a skill", (
        f"positional capability_need must surface verbatim; "
        f"got {args.capability_need!r}"
    )
    assert args.from_spec is None, (
        f"--from must default to None when omitted; got {args.from_spec!r}"
    )
    assert args.output == str(output_path), (
        f"--output must surface the supplied path verbatim; "
        f"got {args.output!r}"
    )
    assert args.max_rounds == ROUND_BUDGET_DEFAULT, (
        f"--max-rounds must default to ROUND_BUDGET_DEFAULT "
        f"({ROUND_BUDGET_DEFAULT}); got {args.max_rounds!r}"
    )
    assert args.json is False, (
        f"--json must default to False; got {args.json!r}"
    )
    assert args.allow_routing_revision is False, (
        f"--allow-routing-revision must default to False on the "
        f"renamed dest args.allow_routing_revision; "
        f"got {args.allow_routing_revision!r}"
    )
    assert args.allow_dirty_unrelated is False, (
        f"--allow-dirty-unrelated must default to False on the "
        f"renamed dest args.allow_dirty_unrelated; "
        f"got {args.allow_dirty_unrelated!r}"
    )
    assert args.confirm_resume is False, (
        f"--confirm-resume must default to False on the "
        f"renamed dest args.confirm_resume; "
        f"got {args.confirm_resume!r}"
    )


def test_synthesize_parser_accepts_from_spec_with_json(tmp_path: Path) -> None:
    """AC0 (parser): ``synthesize --from <spec> --output <path> --json`` parses cleanly.

    Mirrors the test above but on the ``--from`` arm of the
    mutually-exclusive input group: ``capability_need`` is
    ``None`` because no positional was supplied, ``from_spec``
    carries the spec path, and ``--json`` flips on. The other
    shared flags keep their defaults.
    """
    from metacrucible.__main__ import _build_parser

    parser = _build_parser()
    spec_path = tmp_path / "spec.md"
    spec_path.write_text("# spec\n", encoding="utf-8")
    output_path = tmp_path / "skill"

    args = parser.parse_args(
        [
            "synthesize",
            "--from",
            str(spec_path),
            "--output",
            str(output_path),
            "--json",
        ]
    )

    assert args.command == "synthesize", (
        f"args.command must be 'synthesize'; got {args.command!r}"
    )
    assert args.capability_need is None, (
        f"capability_need must be None when --from is used; "
        f"got {args.capability_need!r}"
    )
    assert args.from_spec == str(spec_path), (
        f"--from must surface as args.from_spec with the supplied "
        f"path verbatim; got {args.from_spec!r}"
    )
    assert args.output == str(output_path), (
        f"--output must surface the supplied path verbatim; "
        f"got {args.output!r}"
    )
    assert args.json is True, (
        f"--json must flip on; got {args.json!r}"
    )


def test_synthesize_parser_rejects_missing_input_with_systemexit_2(
    tmp_path: Path,
) -> None:
    """AC0 (parser): omitting both ``capability_need`` and ``--from``
    must raise ``SystemExit(2)`` (argparse usage-error code) so
    automation sees a stable, distinguishable failure mode and
    the command never reaches the dispatcher (Issue #27 task 27.1).
    """
    from metacrucible.__main__ import _build_parser

    parser = _build_parser()
    output_path = tmp_path / "skill"

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["synthesize", "--output", str(output_path)])

    assert exc_info.value.code == 2, (
        f"missing both inputs must produce argparse usage-error "
        f"SystemExit(2); got code={exc_info.value.code!r}"
    )


def test_synthesize_parser_rejects_conflicting_input_with_systemexit_2(
    tmp_path: Path,
) -> None:
    """AC0 (parser): providing both ``capability_need`` and ``--from``
    must raise ``SystemExit(2)`` at the parser level (mutually
    exclusive input modes). The command never reaches the
    dispatcher and the operator sees a clean argparse error
    message.
    """
    from metacrucible.__main__ import _build_parser

    parser = _build_parser()
    spec_path = tmp_path / "spec.md"
    spec_path.write_text("# spec\n", encoding="utf-8")
    output_path = tmp_path / "skill"

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(
            [
                "synthesize",
                "need",
                "--from",
                str(spec_path),
                "--output",
                str(output_path),
            ]
        )

    assert exc_info.value.code == 2, (
        f"conflicting inputs must produce argparse usage-error "
        f"SystemExit(2); got code={exc_info.value.code!r}"
    )

def test_synthesize_parser_renamed_confirm_flags_flip_renamed_dests(
    tmp_path: Path,
) -> None:
    """AC0 (parser): the three confirmation flags surface on the
    snake_case namespace dests the dispatcher and downstream code
    read: ``--allow-routing-revision`` -> ``args.allow_routing_revision``,
    ``--allow-dirty-unrelated`` -> ``args.allow_dirty_unrelated``,
    ``--confirm-resume`` -> ``args.confirm_resume``. All three are
    ``store_true`` confirmations aligned with the ``optimize`` command
    and a single parse that flips them on must flip the renamed
    dests on. This closes the parser-contract gap left by the
    default-false assertions in the positional-need test (Finding 3
    of the Task 1 code-quality review).
    """
    from metacrucible.__main__ import _build_parser

    parser = _build_parser()
    output_path = tmp_path / "skill"
    args = parser.parse_args(
        [
            "synthesize",
            "write a skill",
            "--output",
            str(output_path),
            "--allow-routing-revision",
            "--allow-dirty-unrelated",
            "--confirm-resume",
        ]
    )

    assert args.allow_routing_revision is True, (
        f"--allow-routing-revision must flip on args.allow_routing_revision; "
        f"got {args.allow_routing_revision!r}"
    )
    assert args.allow_dirty_unrelated is True, (
        f"--allow-dirty-unrelated must flip on args.allow_dirty_unrelated; "
        f"got {args.allow_dirty_unrelated!r}"
    )
    assert args.confirm_resume is True, (
        f"--confirm-resume must flip on args.confirm_resume; "
        f"got {args.confirm_resume!r}"
    )



# --------------------------------------------------------------------------- #
# Task 2 command-level tests (Issue #41 / PRD F4)                            #
# --------------------------------------------------------------------------- #

#: Pinned ``now`` value used to freeze timestamps so the synthesized
#: case_ids, baseline hashes, and history events are byte-stable across
#: runs. The string is an ISO-8601 UTC instant with a ``Z`` suffix to
#: mirror :func:`metacrucible.__main__._now_iso` exactly.
FROZEN_NOW = "2026-06-17T00:00:00Z"

#: Frozen ``case_id`` values the inline-need happy path test asserts on.
#: Derived from the SHA-256 of ``("write a skill to summarize documents"\x00eval\x00{FROZEN_NOW}")``
#: and the held-out split, sliced to the first 16 hex chars.
_FROZEN_NEED = "write a skill to summarize documents"


def _synthesize_namespace(
    *,
    tmp_path: Path,
    capability_need: str | None,
    from_spec: str | None,
    json_mode: bool = True,
) -> argparse.Namespace:
    """Build the ``argparse.Namespace`` ``cmd_synthesize`` expects.

    Mirrors the dispatcher's parser output: a fully-populated
    namespace with every shared CLI flag wired so the dispatcher
    can read ``args.json`` and the wrapper can build the
    ``_emit`` partial without raising ``AttributeError``. The
    snake_case dests match the parser rename contract pinned
    by the parser-level tests above.
    """
    output = tmp_path / "skill"
    return argparse.Namespace(
        command="synthesize",
        capability_need=capability_need,
        from_spec=from_spec,
        output=str(output),
        max_rounds=ROUND_BUDGET_DEFAULT,
        json=json_mode,
        allow_routing_revision=False,
        allow_dirty_unrelated=False,
        confirm_resume=False,
    )


def _read_benchmark_records(path: Path) -> list[dict[str, object]]:
    """Read all parseable JSONL records from ``path``.

    Mirrors :func:`metacrucible.__main__._read_benchmark_records`
    in shape (skip blank lines, parse JSON) so the Task 2 tests
    can read the benchmark the pipeline wrote without depending
    on private ``__main__`` helpers.
    """
    records: list[dict[str, object]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        records.append(json.loads(raw))
    return records


def test_synthesize_inline_need_creates_draft_pending_review_workspace(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC2 (command, inline-need happy path):
    ``cmd_synthesize`` with a freeform ``capability_need`` and
    ``--json`` returns :data:`metacrucible.exit_codes.EXIT_OK`
    and emits a parseable JSON payload whose ``outcome`` is
    ``draft_pending_review``.

    The full Task 2 contract is asserted end-to-end:

      - return code is :data:`metacrucible.exit_codes.EXIT_OK`,
      - JSON ``status`` is ``"OK"`` and ``outcome`` is
        :data:`metacrucible.synthesize.SYNTHESIZE_DRAFT_PENDING_REVIEW`
        (``"draft_pending_review"``),
      - the draft artifact file exists under the workspace,
      - the envelope (``<workspace>/.metacrucible/envelope.json``)
        points at that artifact and declares
        ``source == "synthesize"``,
      - the benchmark (``<workspace>/benchmark.jsonl``) carries
        one ``case_eval`` (split=eval) and one ``case_held_out``
        (split=held_out) record, both with
        :data:`metacrucible.benchmark.STATUS_GENERATED` and
        :data:`metacrucible.synthesize.BOOTSTRAP_PENDING_REVIEW_FIELD`
        set to ``True``,
      - the state (``<workspace>/.metacrucible/state.json``)
        contains a ``baseline`` mapping with both artifact and
        benchmark hashes,
      - the history (``<workspace>/.metacrucible/history.jsonl``)
        contains the four synthesis events in the pinned order
        (``synthesis_started``, ``baseline_recorded``,
        ``generated_cases_created``, ``synthesis_pending_review``),
      - the ``sentinel`` field in the JSON payload is the literal
        :data:`metacrucible.synthesize.BOOTSTRAP_PENDING_REVIEW_FIELD`
        constant so downstream consumers see which sentinel is
        in effect.

    Time is frozen via ``monkeypatch.setattr`` on
    :mod:`metacrucible.__main__._now_iso` so the case_ids and
    history events are byte-stable across runs (the case_id is
    derived from a SHA-256 of ``need + split + now``).
    """
    from metacrucible import __main__ as cli_main

    monkeypatch.setattr(cli_main, "_now_iso", lambda: FROZEN_NOW)

    ns = _synthesize_namespace(
        tmp_path=tmp_path,
        capability_need=_FROZEN_NEED,
        from_spec=None,
    )
    rc = cli_main.cmd_synthesize(ns)
    captured = capsys.readouterr()

    assert rc == EXIT_OK, (
        f"inline-need synthesize must return EXIT_OK; got "
        f"rc={rc} stdout={captured.out!r} stderr={captured.err!r}"
    )
    payload = json.loads(captured.out)
    assert isinstance(payload, dict), (
        f"--json payload must be a JSON object; got "
        f"{type(payload).__name__} ({payload!r})"
    )
    assert payload.get("status") == "OK", (
        f"payload status must be 'OK'; got {payload.get('status')!r}"
    )
    assert (
        payload.get("outcome") == SYNTHESIZE_DRAFT_PENDING_REVIEW
    ), (
        f"payload outcome must be {SYNTHESIZE_DRAFT_PENDING_REVIEW!r}; "
        f"got {payload.get('outcome')!r}"
    )

    workspace = pathlib.Path(payload["workspace"])
    artifact_path = pathlib.Path(payload["artifact_path"])
    benchmark_path = pathlib.Path(payload["benchmark"])

    # Artifact file exists, is under the workspace, and carries
    # the verbatim capability need inside a ``# Capability Need``
    # section so a human reviewer can confirm the synthesis.
    assert artifact_path.is_file(), (
        f"artifact file must exist after a successful synthesize; "
        f"got artifact_path={artifact_path!r}"
    )
    assert artifact_path.parent == workspace, (
        f"artifact must live directly under the workspace; got "
        f"artifact={artifact_path!r} workspace={workspace!r}"
    )
    artifact_text = artifact_path.read_text(encoding="utf-8")
    assert _FROZEN_NEED in artifact_text, (
        f"artifact must contain the verbatim capability need; "
        f"need={_FROZEN_NEED!r} not in artifact"
    )
    assert "# Capability Need" in artifact_text, (
        f"artifact must carry a '# Capability Need' section; got "
        f"artifact text={artifact_text!r}"
    )
    assert artifact_text.endswith("\n"), (
        f"artifact must end with exactly one newline; got tail={artifact_text[-5:]!r}"
    )

    # Envelope: ``source == 'synthesize'`` + artifact path + need hash.
    envelope = json.loads(
        (workspace / ".metacrucible" / "envelope.json").read_text(
            encoding="utf-8"
        )
    )
    assert envelope.get("source") == "synthesize", (
        f"envelope must declare source='synthesize'; got "
        f"envelope={envelope!r}"
    )
    assert envelope.get("artifact_path") == str(artifact_path), (
        f"envelope.artifact_path must point at the draft artifact; "
        f"got envelope.artifact_path={envelope.get('artifact_path')!r} "
        f"artifact_path={str(artifact_path)!r}"
    )
    assert envelope.get("artifact_workspace") == str(workspace), (
        f"envelope.artifact_workspace must equal the workspace; got "
        f"{envelope.get('artifact_workspace')!r}"
    )
    assert isinstance(envelope.get("capability_need_hash"), str), (
        f"envelope must carry a capability_need_hash string; got "
        f"{envelope.get('capability_need_hash')!r}"
    )
    assert len(envelope["capability_need_hash"]) == 64, (
        f"capability_need_hash must be a SHA-256 hex digest (64 "
        f"chars); got {envelope['capability_need_hash']!r}"
    )

    # Benchmark: metadata + one eval case + one held-out case.
    records = _read_benchmark_records(benchmark_path)
    assert records[0]["record_type"] == "metadata", (
        f"benchmark[0] must be the metadata record; got "
        f"{records[0]!r}"
    )
    case_records = [r for r in records if r.get("record_type") != "metadata"]
    assert len(case_records) == 2, (
        f"synthesize must write exactly 2 generated cases (eval + "
        f"held-out); got {len(case_records)} case records"
    )
    eval_cases = [
        r for r in case_records if r.get("split") == SPLIT_EVAL
    ]
    held_out_cases = [
        r for r in case_records if r.get("split") == SPLIT_HELD_OUT
    ]
    assert len(eval_cases) == 1, (
        f"benchmark must have exactly 1 eval case; got {eval_cases!r}"
    )
    assert len(held_out_cases) == 1, (
        f"benchmark must have exactly 1 held-out case; got "
        f"{held_out_cases!r}"
    )
    for case in case_records:
        assert case["status"] == STATUS_GENERATED, (
            f"synthesized case must be status=generated; got "
            f"{case!r}"
        )
        assert case[BOOTSTRAP_PENDING_REVIEW_FIELD] is True, (
            f"synthesized case must carry the bootstrap-pending-review "
            f"sentinel; got case={case!r}"
        )
        assert case["reviewed"] is False, (
            f"synthesized case must be reviewed=False; got {case!r}"
        )
        assert case["checks"] == [], (
            f"synthesized case must have empty checks list; got "
            f"{case!r}"
        )
        assert case["judgment"] is None, (
            f"synthesized case must have judgment=None; got {case!r}"
        )
        cid = case["case_id"]
        assert isinstance(cid, str) and cid.startswith("synthesize-"), (
            f"synthesized case_id must be 'synthesize-<hex>'; got "
            f"{cid!r}"
        )
    assert eval_cases[0]["record_type"] == "case_eval", (
        f"eval case must carry record_type='case_eval'; got "
        f"{eval_cases[0]!r}"
    )
    assert held_out_cases[0]["record_type"] == "case_held_out", (
        f"held-out case must carry record_type='case_held_out'; got "
        f"{held_out_cases[0]!r}"
    )
    assert (
        eval_cases[0]["case_id"] != held_out_cases[0]["case_id"]
    ), (
        f"eval and held-out case_ids must be unique; got "
        f"eval={eval_cases[0]['case_id']!r} "
        f"held_out={held_out_cases[0]['case_id']!r}"
    )

    # State: default fields + a ``baseline`` mapping.
    state = json.loads(
        (workspace / ".metacrucible" / "state.json").read_text(
            encoding="utf-8"
        )
    )
    assert state.get("current_best_revision") is None
    assert state.get("last_run_id") is None
    baseline = state.get("baseline")
    assert isinstance(baseline, dict), (
        f"state.baseline must be a dict; got {baseline!r}"
    )
    assert isinstance(baseline.get("artifact_hash"), str) and len(
        baseline["artifact_hash"]
    ) == 64, (
        f"state.baseline.artifact_hash must be a 64-char SHA-256 hex; "
        f"got {baseline.get('artifact_hash')!r}"
    )
    assert isinstance(baseline.get("benchmark_hash"), str) and len(
        baseline["benchmark_hash"]
    ) == 64, (
        f"state.baseline.benchmark_hash must be a 64-char SHA-256 hex; "
        f"got {baseline.get('benchmark_hash')!r}"
    )

    # History: the four synthesis events in the pinned order.
    history_records = [
        json.loads(line)
        for line in (workspace / ".metacrucible" / "history.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    history_events = [r["event"] for r in history_records]
    assert history_events == [
        "synthesis_started",
        "baseline_recorded",
        "generated_cases_created",
        "synthesis_pending_review",
    ], (
        f"history must contain the four synthesis events in the "
        f"pinned order; got {history_events!r}"
    )

    # JSON payload cross-references.
    assert payload["sentinel"] == BOOTSTRAP_PENDING_REVIEW_FIELD, (
        f"payload sentinel must be the BOOTSTRAP_PENDING_REVIEW_FIELD "
        f"constant; got {payload.get('sentinel')!r}"
    )
    assert payload["blockers"] == [], (
        f"payload blockers must be empty on success; got "
        f"{payload.get('blockers')!r}"
    )
    assert payload["generated_case_ids"] == [
        eval_cases[0]["case_id"],
        held_out_cases[0]["case_id"],
    ], (
        f"payload generated_case_ids must list the eval case first "
        f"then the held-out case; got {payload.get('generated_case_ids')!r}"
    )
    assert (
        payload["baseline"]["artifact_hash"] == baseline["artifact_hash"]
    )
    assert (
        payload["baseline"]["benchmark_hash"] == baseline["benchmark_hash"]
    )


def test_synthesize_from_spec_creates_same_pending_review_shape(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC2 (command, --from spec happy path):
    ``cmd_synthesize`` with a ``--from <spec>`` path returns
    :data:`metacrucible.exit_codes.EXIT_OK` and produces a
    workspace whose draft artifact contains the spec text and
    whose benchmark carries the same generated-case / sentinel
    contract as the inline-need path.

    The spec file is written to ``tmp_path / "spec.md"`` and
    read by the pipeline via :func:`metacrucible.synthesize.resolve_synthesize_input`
    with UTF-8. The draft artifact must contain the spec text
    verbatim (after stripping) so a human reviewer can confirm
    the spec round-tripped through the pipeline.
    """
    from metacrucible import __main__ as cli_main

    monkeypatch.setattr(cli_main, "_now_iso", lambda: FROZEN_NOW)

    spec_text = "summarize legal documents into a 5-bullet brief"
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(spec_text + "\n", encoding="utf-8")

    ns = _synthesize_namespace(
        tmp_path=tmp_path,
        capability_need=None,
        from_spec=str(spec_path),
    )
    rc = cli_main.cmd_synthesize(ns)
    captured = capsys.readouterr()

    assert rc == EXIT_OK, (
        f"--from spec synthesize must return EXIT_OK; got "
        f"rc={rc} stdout={captured.out!r} stderr={captured.err!r}"
    )
    payload = json.loads(captured.out)
    assert (
        payload.get("outcome") == SYNTHESIZE_DRAFT_PENDING_REVIEW
    ), (
        f"payload outcome must be {SYNTHESIZE_DRAFT_PENDING_REVIEW!r}; "
        f"got {payload.get('outcome')!r}"
    )

    workspace = pathlib.Path(payload["workspace"])
    artifact_path = pathlib.Path(payload["artifact_path"])
    artifact_text = artifact_path.read_text(encoding="utf-8")
    assert spec_text in artifact_text, (
        f"artifact must contain the verbatim spec text; spec="
        f"{spec_text!r} not in artifact"
    )

    # Same generated-case / sentinel contract as inline-need path.
    records = _read_benchmark_records(workspace / BENCHMARK_FILE_NAME)
    case_records = [
        r for r in records if r.get("record_type") != "metadata"
    ]
    assert len(case_records) == 2
    eval_cases = [
        r for r in case_records if r.get("split") == SPLIT_EVAL
    ]
    held_out_cases = [
        r for r in case_records if r.get("split") == SPLIT_HELD_OUT
    ]
    assert len(eval_cases) == 1 and len(held_out_cases) == 1
    for case in case_records:
        assert case["status"] == STATUS_GENERATED
        assert case[BOOTSTRAP_PENDING_REVIEW_FIELD] is True
        assert case["reviewed"] is False
    assert payload["sentinel"] == BOOTSTRAP_PENDING_REVIEW_FIELD


def test_synthesize_blocks_when_spec_missing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC2 (command, precondition): ``--from`` pointing at a
    non-existent path returns
    :data:`metacrucible.exit_codes.EXIT_BLOCKED` with the
    stable :data:`metacrucible.synthesize.SYNTHESIZE_SPEC_MISSING_BLOCKER`
    id and does NOT create the workspace.

    The blocker path must short-circuit BEFORE any filesystem
    mutation so a missing spec cannot leave a half-created
    workspace behind.
    """
    from metacrucible import __main__ as cli_main

    monkeypatch.setattr(cli_main, "_now_iso", lambda: FROZEN_NOW)

    spec_path = tmp_path / "missing-spec.md"
    assert not spec_path.exists(), (
        f"precondition: spec path must not exist before the test; "
        f"got {spec_path!r}"
    )
    output = tmp_path / "skill"
    assert not output.exists(), (
        f"precondition: output must not exist before the test; "
        f"got {output!r}"
    )

    ns = _synthesize_namespace(
        tmp_path=tmp_path,
        capability_need=None,
        from_spec=str(spec_path),
    )
    rc = cli_main.cmd_synthesize(ns)
    captured = capsys.readouterr()

    assert rc == EXIT_BLOCKED, (
        f"missing --from spec must return EXIT_BLOCKED; got "
        f"rc={rc} stdout={captured.out!r} stderr={captured.err!r}"
    )
    payload = json.loads(captured.out)
    assert payload.get("status") == "BLOCKED", (
        f"missing spec payload must be status=BLOCKED; got "
        f"{payload.get('status')!r}"
    )
    blocker_ids = [
        b.get("id") for b in payload.get("blockers", [])
        if isinstance(b, dict)
    ]
    assert SYNTHESIZE_SPEC_MISSING_BLOCKER in blocker_ids, (
        f"missing spec payload must carry the "
        f"{SYNTHESIZE_SPEC_MISSING_BLOCKER!r} blocker id; got "
        f"blocker_ids={blocker_ids!r}"
    )
    # No workspace written for a blocker path.
    assert not output.exists(), (
        f"output path must NOT be created when the precondition "
        f"blocks; got {output!r} (exists={output.exists()!r})"
    )
    assert payload.get("generated_case_ids") == [], (
        f"BLOCKED payload must have empty generated_case_ids; got "
        f"{payload.get('generated_case_ids')!r}"
    )


def test_synthesize_blocks_when_spec_file_empty(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC2 (command, precondition): ``--from`` pointing at an
    empty (or whitespace-only) file returns
    :data:`metacrucible.exit_codes.EXIT_BLOCKED` with the
    stable :data:`metacrucible.synthesize.SYNTHESIZE_SPEC_EMPTY_BLOCKER`
    id and does NOT create the workspace.

    A whitespace-only file is also rejected (the pipeline
    strips the spec before checking emptiness so the operator
    cannot smuggle a no-op spec through with trailing
    newlines).
    """
    from metacrucible import __main__ as cli_main

    monkeypatch.setattr(cli_main, "_now_iso", lambda: FROZEN_NOW)

    spec_path = tmp_path / "empty-spec.md"
    spec_path.write_text("  \n\n  ", encoding="utf-8")
    output = tmp_path / "skill"
    assert not output.exists()

    ns = _synthesize_namespace(
        tmp_path=tmp_path,
        capability_need=None,
        from_spec=str(spec_path),
    )
    rc = cli_main.cmd_synthesize(ns)
    captured = capsys.readouterr()

    assert rc == EXIT_BLOCKED, (
        f"empty --from spec must return EXIT_BLOCKED; got "
        f"rc={rc} stdout={captured.out!r} stderr={captured.err!r}"
    )
    payload = json.loads(captured.out)
    assert payload.get("status") == "BLOCKED"
    blocker_ids = [
        b.get("id") for b in payload.get("blockers", [])
        if isinstance(b, dict)
    ]
    assert SYNTHESIZE_SPEC_EMPTY_BLOCKER in blocker_ids, (
        f"empty spec payload must carry the "
        f"{SYNTHESIZE_SPEC_EMPTY_BLOCKER!r} blocker id; got "
        f"blocker_ids={blocker_ids!r}"
    )
    assert not output.exists(), (
        f"output path must NOT be created when the precondition "
        f"blocks; got {output!r}"
    )


def test_synthesize_blocks_when_output_exists(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC2 (command, precondition): an existing ``--output`` path
    (directory or file) returns
    :data:`metacrucible.exit_codes.EXIT_BLOCKED` with the
    stable :data:`metacrucible.synthesize.SYNTHESIZE_OUTPUT_EXISTS_BLOCKER`
    id and does NOT mutate the existing path.

    The pipeline refuses to clobber an existing workspace or
    file (per the ``init``-style idempotency contract); the
    operator must remove or rename the target before
    re-running synthesize. The test pins the contract for
    both the directory-already-exists and the file-already-
    exists shapes.
    """
    from metacrucible import __main__ as cli_main

    monkeypatch.setattr(cli_main, "_now_iso", lambda: FROZEN_NOW)

    output = tmp_path / "skill"
    output.mkdir(parents=True)
    existing_file = output / "preexisting.txt"
    existing_file.write_text("do not clobber\n", encoding="utf-8")

    ns = _synthesize_namespace(
        tmp_path=tmp_path,
        capability_need=_FROZEN_NEED,
        from_spec=None,
    )
    rc = cli_main.cmd_synthesize(ns)
    captured = capsys.readouterr()

    assert rc == EXIT_BLOCKED, (
        f"existing --output must return EXIT_BLOCKED; got "
        f"rc={rc} stdout={captured.out!r} stderr={captured.err!r}"
    )
    payload = json.loads(captured.out)
    assert payload.get("status") == "BLOCKED"
    blocker_ids = [
        b.get("id") for b in payload.get("blockers", [])
        if isinstance(b, dict)
    ]
    assert SYNTHESIZE_OUTPUT_EXISTS_BLOCKER in blocker_ids, (
        f"existing output payload must carry the "
        f"{SYNTHESIZE_OUTPUT_EXISTS_BLOCKER!r} blocker id; got "
        f"blocker_ids={blocker_ids!r}"
    )
    # The pre-existing file inside the output dir is preserved.
    assert existing_file.is_file(), (
        f"pre-existing file must be preserved when the precondition "
        f"blocks; got {existing_file!r}"
    )
    # No new artifact was written under the output path.
    assert not (output / "synthesized-skill.md").exists(), (
        f"no draft artifact must be written when output already "
        f"exists; got {(output / 'synthesized-skill.md')!r}"
    )
    assert not (output / ".metacrucible").exists(), (
        f"no .metacrucible/ must be created when output already "
        f"exists; got {(output / '.metacrucible')!r}"
    )


# --------------------------------------------------------------------------- #
# Task 3 command-level tests (Issue #41 / PRD F4)                            #
# --------------------------------------------------------------------------- #

def _reviewed_synthesis_workspace(
    tmp_path: Path,
    *,
    capability_need: str = "write a skill to summarize documents",
    leave_generated: bool = False,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> Path:
    """Seed a synthesis workspace whose benchmark cases are reviewed.

    The helper invokes :func:`metacrucible.__main__.cmd_synthesize`
    once to create the workspace (Task 2 default shape: pending
    generated cases carrying the ``BOOTSTRAP_PENDING_REVIEW``
    sentinel), then rewrites ``benchmark.jsonl`` so the case
    records look human-reviewed:

      - ``status`` -> ``reviewed``,
      - ``reviewed`` -> ``True``,
      - :data:`metacrucible.synthesize.BOOTSTRAP_PENDING_REVIEW_FIELD`
        is removed.

    Split, ``case_id``, and the metadata record are preserved
    so the resulting workspace is indistinguishable from a
    promote-then-re-run shape: the same case_ids, the same
    artifact path, the same envelope, the same baseline
    mapping, the same split partition. When
    ``leave_generated`` is ``True`` the helper skips the
    rewrite and returns the freshly-created workspace (the
    pending-review state).

    The ``capsys`` fixture is read + cleared after the
    bootstrap call so the helper's emit does not bleed
    into the test's own ``captured.out`` assertion.

    Time is frozen via :func:`metacrucible.__main__._now_iso`
    so the case_ids and history events are byte-stable across
    runs.
    """
    from metacrucible import __main__ as cli_main

    monkeypatch.setattr(cli_main, "_now_iso", lambda: FROZEN_NOW)
    ns = _synthesize_namespace(
        tmp_path=tmp_path,
        capability_need=capability_need,
        from_spec=None,
    )
    rc = cli_main.cmd_synthesize(ns)
    assert rc == EXIT_OK, (
        f"workspace bootstrap synthesize must return EXIT_OK; "
        f"got rc={rc}"
    )
    # Drain the bootstrap call's emit so the test's own
    # ``captured.out`` carries only the resume call.
    capsys.readouterr()
    workspace = tmp_path / "skill"
    if leave_generated:
        return workspace
    benchmark_path = workspace / BENCHMARK_FILE_NAME
    records = _read_benchmark_records(benchmark_path)
    new_records: list[dict[str, object]] = []
    for record in records:
        if record.get("record_type") == "metadata":
            new_records.append(dict(record))
            continue
        rewritten = dict(record)
        rewritten["status"] = "reviewed"
        rewritten["reviewed"] = True
        rewritten.pop(BOOTSTRAP_PENDING_REVIEW_FIELD, None)
        new_records.append(rewritten)
    _write_jsonl(benchmark_path, new_records)
    return workspace


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> Path:
    """Write ``records`` as one JSON object per line at ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(dict(rec), sort_keys=True) for rec in records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def assert_payloads_equal_modulo_volatile(
    create: dict[str, object],
    resume: dict[str, object],
    *,
    volatile: frozenset[str] = frozenset({"created_at"}),
) -> None:
    """Assert the create-success and resume payloads are field-by-field equal.

    The ``_emit_pending_review_payload`` docstring claims the
    create-success and resume short-circuit payloads are
    "indistinguishable" so a downstream consumer cannot tell
    the difference between a fresh draft-pending-review and a
    re-invocation that short-circuited because the operator has
    not yet reviewed the generated cases. This helper pins that
    contract in tests by asserting field-by-field equality
    between the two payloads.

    ``volatile`` defaults to ``{"created_at"}`` (the helper's
    ``_now_iso`` is patched per-call, so timestamps may differ
    if the test ever relaxes the freeze). Tests can override the
    set to add or remove fields as the payload contract evolves.
    """
    create_filtered = {
        k: v for k, v in create.items() if k not in volatile
    }
    resume_filtered = {
        k: v for k, v in resume.items() if k not in volatile
    }
    assert create_filtered == resume_filtered, (
        f"create and resume payloads must be field-by-field equal "
        f"modulo {set(volatile)!r}; "
        f"create_keys={sorted(create_filtered.keys())!r} "
        f"resume_keys={sorted(resume_filtered.keys())!r} "
        f"create_only={sorted(set(create_filtered) - set(resume_filtered))!r} "
        f"resume_only={sorted(set(resume_filtered) - set(create_filtered))!r} "
        f"value_diffs={sorted(k for k in create_filtered if k in resume_filtered and create_filtered[k] != resume_filtered[k])!r}"
    )


def test_synthesize_reviewed_workspace_runs_optimizer_and_accepts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC3 (command, reviewed workspace, optimizer ACCEPTED):
    ``cmd_synthesize`` invoked against an existing synthesis
    workspace whose benchmark has reviewed eval + held-out
    cases runs the F3 optimizer and returns
    :data:`metacrucible.exit_codes.EXIT_OK` with
    ``outcome='accepted'`` when the pipeline reports
    ``status='ACCEPTED'`` and
    ``acceptance_decision.accepted is True``.

    The test pins the dispatch contract end-to-end:

      - The synthesize-side wrapper
        :func:`metacrucible.synthesize.run_synthesis_optimizer`
        is called exactly once with ``workspace``,
        ``benchmark_path``, ``artifact_path``, and
        ``max_rounds`` (from the dispatcher). The wrapper's
        internal defaults (``call_fn=None``,
        ``human_confirmed=False``,
        ``routing_confirmation_preview=True``) are NOT part
        of this test's contract -- they are
        :func:`run_synthesis_optimizer`'s contract and the
        wrapper is the single seam.
      - The JSON payload is parseable, has
        ``status='OK'`` and ``outcome='accepted'``, and
        carries the optimizer ``acceptance_decision``,
        ``evidence_refs``, ``record_counts``, and
        ``selected_candidate_ids`` pass-through fields.
      - History records ``synthesis_optimizer_started`` and
        ``synthesis_finished`` are appended to
        ``<workspace>/.metacrucible/history.jsonl`` so the
        audit lineage carries the synthesize-then-optimize
        sequence.
    """
    from dataclasses import dataclass
    from metacrucible import synthesize as synth_mod
    from metacrucible import __main__ as cli_main

    workspace = _reviewed_synthesis_workspace(
        tmp_path, monkeypatch=monkeypatch, capsys=capsys
    )
    benchmark_path = workspace / BENCHMARK_FILE_NAME
    # Pull the artifact path off the envelope (the optimizer
    # receives whatever the envelope declared).
    envelope = json.loads(
        (workspace / ".metacrucible" / "envelope.json").read_text(
            encoding="utf-8"
        )
    )
    artifact_path = envelope["artifact_path"]

    @dataclass
    class _StubResult:
        status: str = "ACCEPTED"
        run_id: str = "stub-run-accepted"
        rounds: int = 1
        record_counts: dict[str, int] = None  # type: ignore[assignment]
        evidence_refs: dict[str, str] = None  # type: ignore[assignment]
        blockers: list = None  # type: ignore[assignment]
        warnings: list = None  # type: ignore[assignment]
        best_revision: dict | None = None
        acceptance_decision: dict = None  # type: ignore[assignment]
        selected_candidate_ids: list = None  # type: ignore[assignment]
        stop_reason: str = "accepted"
        preview: dict | None = None

    evidence_refs = {
        "receipt": "/tmp/evidence/stub-run-accepted/receipt.json",
        "summary": "/tmp/evidence/stub-run-accepted/summary.json",
    }
    call_log: list[dict[str, object]] = []

    def _stub(**kwargs: object) -> _StubResult:
        call_log.append(dict(kwargs))
        return _StubResult(
            record_counts={"case_eval": 1, "case_held_out": 1},
            evidence_refs=evidence_refs,
            blockers=[],
            warnings=[],
            best_revision=None,
            acceptance_decision={
                "accepted": True,
                "reason": "accepted",
            },
            selected_candidate_ids=["cand-1"],
        )

    monkeypatch.setattr(synth_mod, "run_synthesis_optimizer", _stub)

    ns = _synthesize_namespace(
        tmp_path=tmp_path,
        capability_need=None,
        from_spec=None,
    )
    rc = cli_main.cmd_synthesize(ns)
    captured = capsys.readouterr()

    assert rc == EXIT_OK, (
        f"reviewed-workspace synthesize must return EXIT_OK when "
        f"optimizer accepts; got rc={rc} stdout={captured.out!r} "
        f"stderr={captured.err!r}"
    )
    payload = json.loads(captured.out)
    assert payload.get("status") == "OK", (
        f"payload status must be 'OK' on accepted resume; got "
        f"{payload.get('status')!r}"
    )
    assert payload.get("outcome") == "accepted", (
        f"payload outcome must be 'accepted' on accepted resume; "
        f"got {payload.get('outcome')!r}"
    )
    assert len(call_log) == 1, (
        f"optimizer entrypoint must be called exactly once on "
        f"reviewed resume; got {len(call_log)} calls"
    )
    call = call_log[0]
    assert call.get("workspace") == workspace, (
        f"optimizer must be called with the synthesis workspace; "
        f"got workspace={call.get('workspace')!r} expected={workspace!r}"
    )
    assert call.get("benchmark_path") == benchmark_path, (
        f"optimizer must be called with the workspace benchmark; "
        f"got benchmark_path={call.get('benchmark_path')!r} "
        f"expected={benchmark_path!r}"
    )
    assert call.get("artifact_path") == Path(artifact_path), (
        f"optimizer must be called with the envelope-declared "
        f"artifact path; got artifact_path={call.get('artifact_path')!r} "
        f"expected={artifact_path!r}"
    )
    assert call.get("max_rounds") == ROUND_BUDGET_DEFAULT, (
        f"optimizer must be called with max_rounds from the "
        f"dispatcher ({ROUND_BUDGET_DEFAULT}); got "
        f"max_rounds={call.get('max_rounds')!r}"
    )
    # Payload carries the optimizer pass-through fields.
    assert payload.get("acceptance_decision") == {
        "accepted": True,
        "reason": "accepted",
    }, (
        f"payload must carry the optimizer acceptance_decision "
        f"verbatim; got {payload.get('acceptance_decision')!r}"
    )
    assert payload.get("evidence_refs") == evidence_refs, (
        f"payload must carry the optimizer evidence_refs; got "
        f"{payload.get('evidence_refs')!r}"
    )
    assert payload.get("record_counts") == {
        "case_eval": 1,
        "case_held_out": 1,
    }, (
        f"payload must carry the optimizer record_counts; got "
        f"{payload.get('record_counts')!r}"
    )
    assert payload.get("selected_candidate_ids") == ["cand-1"], (
        f"payload must carry the optimizer selected_candidate_ids; "
        f"got {payload.get('selected_candidate_ids')!r}"
    )
    assert payload.get("blockers") == [], (
        f"accepted payload blockers must be empty; got "
        f"{payload.get('blockers')!r}"
    )

    # History: synthesis_optimizer_started + synthesis_finished
    # bracket the optimizer call. The pre-existing Task 2 events
    # (synthesis_started, baseline_recorded,
    # generated_cases_created, synthesis_pending_review) are
    # preserved.
    history_lines = [
        json.loads(line)
        for line in (workspace / ".metacrucible" / "history.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    history_events = [r["event"] for r in history_lines]
    assert "synthesis_optimizer_started" in history_events, (
        f"history must carry a synthesis_optimizer_started event; "
        f"got events={history_events!r}"
    )
    assert "synthesis_finished" in history_events, (
        f"history must carry a synthesis_finished event; got "
        f"events={history_events!r}"
    )
    started_idx = history_events.index("synthesis_optimizer_started")
    finished_idx = history_events.index("synthesis_finished")
    assert started_idx < finished_idx, (
        f"synthesis_optimizer_started must come BEFORE "
        f"synthesis_finished in history; got started_idx="
        f"{started_idx} finished_idx={finished_idx}"
    )
    finished_record = history_lines[finished_idx]
    assert finished_record.get("outcome") == "accepted", (
        f"synthesis_finished must carry outcome='accepted'; got "
        f"{finished_record!r}"
    )
    assert finished_record.get("stop_reason") == "accepted", (
        f"synthesis_finished must carry the optimizer stop_reason; "
        f"got {finished_record!r}"
    )


def test_synthesize_reviewed_workspace_aborts_when_optimizer_rejects(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC3 (command, reviewed workspace, optimizer REJECTED):
    ``cmd_synthesize`` invoked against an existing synthesis
    workspace whose benchmark has reviewed eval + held-out
    cases runs the F3 optimizer and returns
    :data:`metacrucible.exit_codes.EXIT_BLOCKED` with
    ``outcome='aborted'`` when the pipeline reports
    ``status='REJECTED'`` and
    ``acceptance_decision.accepted is False``.

    The test pins the dispatcher mapping for the rejected
    path:

      - exit code is :data:`metacrucible.exit_codes.EXIT_BLOCKED`,
      - ``status='BLOCKED'`` and ``outcome='aborted'``,
      - the payload carries the optimizer's ``blockers``,
        ``warnings``, ``evidence_refs``, ``record_counts``,
        ``rounds``, ``stop_reason``, and
        ``acceptance_decision`` fields verbatim so a
        downstream operator can reconstruct why the run
        stopped,
      - the payload does NOT include an ``accepted`` flag
        on the top-level outcome (the outcome string is the
        only machine-stable signal).
    """
    from dataclasses import dataclass
    from metacrucible import synthesize as synth_mod
    from metacrucible import __main__ as cli_main

    workspace = _reviewed_synthesis_workspace(
        tmp_path, monkeypatch=monkeypatch, capsys=capsys
    )

    @dataclass
    class _StubResult:
        status: str = "REJECTED"
        run_id: str = "stub-run-rejected"
        rounds: int = 1
        record_counts: dict[str, int] = None  # type: ignore[assignment]
        evidence_refs: dict[str, str] = None  # type: ignore[assignment]
        blockers: list = None  # type: ignore[assignment]
        warnings: list = None  # type: ignore[assignment]
        best_revision: dict | None = None
        acceptance_decision: dict = None  # type: ignore[assignment]
        selected_candidate_ids: list = None  # type: ignore[assignment]
        stop_reason: str = "max_rounds_reached"
        preview: dict | None = None

    blocker_record = {
        "id": "no-eval-improvement",
        "message": "candidate did not improve eval pass-rate",
    }
    evidence_refs = {
        "receipt": "/tmp/evidence/stub-run-rejected/receipt.json",
    }
    def _stub(**kwargs: object) -> _StubResult:
        return _StubResult(
            record_counts={"case_eval": 1, "case_held_out": 1},
            evidence_refs=evidence_refs,
            blockers=[blocker_record],
            warnings=[],
            best_revision=None,
            acceptance_decision={
                "accepted": False,
                "reason": "no_eval_improvement",
            },
            selected_candidate_ids=[],
        )

    monkeypatch.setattr(synth_mod, "run_synthesis_optimizer", _stub)

    ns = _synthesize_namespace(
        tmp_path=tmp_path,
        capability_need=None,
        from_spec=None,
    )
    rc = cli_main.cmd_synthesize(ns)
    captured = capsys.readouterr()

    assert rc == EXIT_BLOCKED, (
        f"reviewed-workspace synthesize must return EXIT_BLOCKED "
        f"when the optimizer rejects; got rc={rc} stdout="
        f"{captured.out!r} stderr={captured.err!r}"
    )
    payload = json.loads(captured.out)
    assert payload.get("status") == "BLOCKED", (
        f"aborted payload status must be 'BLOCKED'; got "
        f"{payload.get('status')!r}"
    )
    assert payload.get("outcome") == "aborted", (
        f"aborted payload outcome must be 'aborted'; got "
        f"{payload.get('outcome')!r}"
    )
    # The top-level payload must NOT include an ``accepted``
    # flag — the outcome string is the single machine-stable
    # signal (consistent with the other ``OK`` / ``BLOCKED``
    # outcome mappings in the CLI).
    assert "accepted" not in payload, (
        f"aborted payload must NOT carry a top-level 'accepted' "
        f"flag; got payload={payload!r}"
    )
    assert payload.get("blockers") == [blocker_record], (
        f"aborted payload must carry the optimizer blockers "
        f"verbatim; got {payload.get('blockers')!r}"
    )
    # Task 4: the aborted payload must still carry the optimizer's
    # evidence_refs verbatim (Task 3 contract) AND merge the BLOCKED
    # bundle refs (Task 4 contract). The optimizer's keys must be a
    # subset of the payload's evidence_refs keys with matching values.
    actual_evidence_refs = payload.get("evidence_refs") or {}
    for key, value in evidence_refs.items():
        assert key in actual_evidence_refs, (
            f"aborted payload must carry the optimizer "
            f"evidence_refs verbatim; missing key={key!r} "
            f"got {actual_evidence_refs!r}"
        )
        assert actual_evidence_refs[key] == value, (
            f"aborted payload evidence_refs[{key!r}] must equal "
            f"the optimizer value; got "
            f"{actual_evidence_refs[key]!r} expected {value!r}"
        )
    assert payload.get("record_counts") == {
        "case_eval": 1,
        "case_held_out": 1,
    }, (
        f"aborted payload must carry the optimizer record_counts "
        f"verbatim; got {payload.get('record_counts')!r}"
    )
    assert payload.get("rounds") == 1, (
        f"aborted payload must carry the optimizer rounds; got "
        f"{payload.get('rounds')!r}"
    )
    assert payload.get("stop_reason") == "max_rounds_reached", (
        f"aborted payload must carry the optimizer stop_reason; "
        f"got {payload.get('stop_reason')!r}"
    )
    assert payload.get("acceptance_decision") == {
        "accepted": False,
        "reason": "no_eval_improvement",
    }, (
        f"aborted payload must carry the optimizer "
        f"acceptance_decision; got "
        f"{payload.get('acceptance_decision')!r}"
    )
    # The synthesize_finished history event carries the
    # aborted outcome and the same stop_reason the payload
    # exposes.
    history_lines = [
        json.loads(line)
        for line in (workspace / ".metacrucible" / "history.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    history_events = [r["event"] for r in history_lines]
    assert "synthesis_finished" in history_events
    finished = next(
        r for r in history_lines if r["event"] == "synthesis_finished"
    )
    assert finished.get("outcome") == "aborted", (
        f"synthesis_finished outcome must be 'aborted' on the "
        f"rejected path; got {finished!r}"
    )
    assert finished.get("stop_reason") == "max_rounds_reached", (
        f"synthesis_finished stop_reason must mirror the "
        f"optimizer stop_reason; got {finished!r}"
    )


def test_synthesize_evaluation_stage_block_writes_blocked_bundle(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC4 (command, evaluation-stage BLOCKED bundle): when the
    optimizer returns a non-ACCEPTED status (here, BLOCKED with
    blockers), ``cmd_synthesize`` must emit the ADR 0035 minimal
    ``BLOCKED`` evidence bundle under
    ``run_type='synthesize_evaluation_stage'`` and attach the
    bundle refs to the payload's ``evidence_refs``.

    The test pins the bundle-emission contract end-to-end:

      - ``HOME`` is pinned to a temp dir so the
        :class:`UserGlobalStorage` writes into the test's own
        evidence directory (no pollution of the developer's
        ``~/.metacrucible/``),
      - a reviewed synthesis workspace is seeded via
        :func:`_reviewed_synthesis_workspace` (Task 3 helper),
      - the optimizer is monkeypatched to return a BLOCKED
        result with a stable stub blocker id,
      - ``cmd_synthesize`` invoked with ``json=True`` returns
        :data:`metacrucible.exit_codes.EXIT_BLOCKED` and a JSON
        payload with ``status='BLOCKED'``, ``outcome='aborted'``,
        and the stub blocker id in the blockers list,
      - ``$HOME/.metacrucible/evidence/<run_id>/receipt.json``,
        ``summary.json``, and ``trajectory-digest.json`` exist
        (the three durable bundle files; no ``raw/``,
        no ``cleanup.json``),
      - ``receipt.json`` carries ``status='BLOCKED'``,
        ``run_type='synthesize_evaluation_stage'``, and the
        normalised blocker list,
      - the payload's ``evidence_refs`` map carries
        ``blocked_receipt``, ``blocked_summary``, and
        ``blocked_trajectory_digest`` keys that name the bundle.
    """
    from dataclasses import dataclass
    from metacrucible import synthesize as synth_mod
    from metacrucible import __main__ as cli_main

    # Pin HOME so the BLOCKED bundle write does not pollute the
    # developer's ``~/.metacrucible/``. Mirrors the pattern in
    # tests/test_blocked_bundle_policy.py::isolated_global_home.
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))

    workspace = _reviewed_synthesis_workspace(
        tmp_path, monkeypatch=monkeypatch, capsys=capsys
    )

    @dataclass
    class _StubResult:
        status: str = "BLOCKED"
        run_id: str = "stub-run-eval-blocked"
        rounds: int = 0
        record_counts: dict[str, int] = None  # type: ignore[assignment]
        evidence_refs: dict[str, str] = None  # type: ignore[assignment]
        blockers: list = None  # type: ignore[assignment]
        warnings: list = None  # type: ignore[assignment]
        best_revision: dict | None = None
        acceptance_decision: dict = None  # type: ignore[assignment]
        selected_candidate_ids: list = None  # type: ignore[assignment]
        stop_reason: str = "evaluation_stage_blocked"
        preview: dict | None = None

    blocker_record = {
        "id": "no-eval-improvement",
        "message": "candidate did not improve eval pass-rate",
    }

    def _stub(**kwargs: object) -> _StubResult:
        return _StubResult(
            record_counts={"case_eval": 1, "case_held_out": 1},
            evidence_refs={},
            blockers=[blocker_record],
            warnings=[],
            best_revision=None,
            acceptance_decision={
                "accepted": False,
                "reason": "no_eval_improvement",
            },
            selected_candidate_ids=[],
        )

    monkeypatch.setattr(synth_mod, "run_synthesis_optimizer", _stub)
    # Freeze the synthesize module's _now_iso() so the run_id
    # is byte-stable; matches the Task 3 freeze pattern on
    # ``metacrucible.__main__._now_iso``.
    monkeypatch.setattr(synth_mod, "_now_iso", lambda: FROZEN_NOW)

    ns = _synthesize_namespace(
        tmp_path=tmp_path,
        capability_need=None,
        from_spec=None,
    )
    rc = cli_main.cmd_synthesize(ns)
    captured = capsys.readouterr()

    assert rc == EXIT_BLOCKED, (
        f"synthesize evaluation-stage BLOCKED must return "
        f"EXIT_BLOCKED; got rc={rc} stdout={captured.out!r} "
        f"stderr={captured.err!r}"
    )
    payload = json.loads(captured.out)
    assert payload.get("status") == "BLOCKED", (
        f"evaluation-stage BLOCKED payload status must be "
        f"'BLOCKED'; got {payload.get('status')!r}"
    )
    assert payload.get("outcome") == "aborted", (
        f"evaluation-stage BLOCKED payload outcome must be "
        f"'aborted'; got {payload.get('outcome')!r}"
    )
    blocker_ids = [
        b.get("id") for b in payload.get("blockers", [])
        if isinstance(b, dict)
    ]
    assert "no-eval-improvement" in blocker_ids, (
        f"evaluation-stage BLOCKED payload must carry the stub "
        f"blocker id; got blocker_ids={blocker_ids!r}"
    )

    # The three durable bundle files exist under
    # ``$HOME/.metacrucible/evidence/<run_id>/`` and carry
    # ``status='BLOCKED'`` + ``run_type='synthesize_evaluation_stage'``.
    evidence_root = fake_home / ".metacrucible" / "evidence"
    assert evidence_root.is_dir(), (
        f"BLOCKED bundle must create the evidence root; got "
        f"{evidence_root!r}"
    )
    bundle_dirs = [p for p in evidence_root.iterdir() if p.is_dir()]
    assert len(bundle_dirs) == 1, (
        f"BLOCKED bundle must create exactly one evidence "
        f"bundle directory; got {bundle_dirs!r}"
    )
    bundle_dir = bundle_dirs[0]
    assert bundle_dir.name == "synthesize-20260617T000000Z", (
        f"BLOCKED bundle dir name must equal the frozen "
        f"run_id 'synthesize-20260617T000000Z' "
        f"(FROZEN_NOW='{FROZEN_NOW}' with colons+hyphens "
        f"stripped); got {bundle_dir.name!r}"
    )
    receipt_path = bundle_dir / "receipt.json"
    summary_path = bundle_dir / "summary.json"
    trajectory_path = bundle_dir / "trajectory-digest.json"
    for path in (receipt_path, summary_path, trajectory_path):
        assert path.is_file(), (
            f"BLOCKED bundle must write {path.name!r}; got "
            f"missing={path!r}"
        )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt.get("status") == "BLOCKED", (
        f"BLOCKED receipt status must be 'BLOCKED'; got "
        f"{receipt.get('status')!r}"
    )
    assert (
        receipt.get("run_type") == "synthesize_evaluation_stage"
    ), (
        f"BLOCKED receipt run_type must be "
        f"'synthesize_evaluation_stage' (ADR 0035); got "
        f"{receipt.get('run_type')!r}"
    )
    receipt_blocker_ids = [
        b.get("id") for b in receipt.get("blockers", [])
        if isinstance(b, dict)
    ]
    assert "no-eval-improvement" in receipt_blocker_ids, (
        f"BLOCKED receipt must carry the stub blocker id; got "
        f"{receipt_blocker_ids!r}"
    )

    # The payload must carry the BLOCKED bundle refs alongside
    # any optimizer evidence_refs (Task 4 step 5).
    payload_evidence_refs = payload.get("evidence_refs") or {}
    assert (
        payload_evidence_refs.get("blocked_receipt")
        == f"{bundle_dir.name}/receipt.json"
    ), (
        f"payload evidence_refs.blocked_receipt must point at "
        f"the BLOCKED bundle receipt; got "
        f"{payload_evidence_refs.get('blocked_receipt')!r}"
    )
    assert (
        payload_evidence_refs.get("blocked_summary")
        == f"{bundle_dir.name}/summary.json"
    ), (
        f"payload evidence_refs.blocked_summary must point at "
        f"the BLOCKED bundle summary; got "
        f"{payload_evidence_refs.get('blocked_summary')!r}"
    )
    assert (
        payload_evidence_refs.get("blocked_trajectory_digest")
        == f"{bundle_dir.name}/trajectory-digest.json"
    ), (
        f"payload evidence_refs.blocked_trajectory_digest "
        f"must point at the BLOCKED bundle trajectory digest; "
        f"got "
        f"{payload_evidence_refs.get('blocked_trajectory_digest')!r}"
    )


def test_synthesize_keeps_pending_review_without_optimizer_call(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC3 (command, pending-review workspace): ``cmd_synthesize``
    invoked against an existing synthesis workspace whose
    benchmark still carries pending generated cases (the
    default Task 2 shape) short-circuits BEFORE the optimizer
    call and returns the existing ``draft_pending_review``
    payload shape + :data:`metacrucible.exit_codes.EXIT_OK`.

    The test pins the contract that the optimizer entrypoint
    is NEVER called when pending cases are present: the
    stub raises ``RuntimeError`` on call, and the test
    expects the synthesize command to return EXIT_OK with
    ``outcome='draft_pending_review'`` and the original
    ``BOOTSTRAP_PENDING_REVIEW`` sentinel in the payload.
    A downstream operator can then review + promote the
    cases and re-invoke synthesize to trigger the
    optimizer.

    The test also pins the contract that the create-success
    payload and the resume short-circuit payload are
    field-by-field equal (modulo volatile ``created_at``):
    ``_emit_pending_review_payload`` claims the two are
    "indistinguishable" so a downstream consumer cannot tell
    the difference between a fresh draft-pending-review and a
    re-invocation that short-circuited. The first
    ``cmd_synthesize`` call (on the same ``tmp_path``)
    captures the create-success payload; the second call
    captures the resume short-circuit payload; the helper
    ``assert_payloads_equal_modulo_volatile`` pins the
    equality end-to-end.
    """
    from metacrucible import synthesize as synth_mod
    from metacrucible import __main__ as cli_main

    # 1. Create the workspace and capture the create-success
    #    payload. The workspace lives under ``tmp_path/skill``
    #    and the resume re-invocation targets the same path, so
    #    the two payloads reference the same workspace /
    #    artifact / benchmark files.
    monkeypatch.setattr(cli_main, "_now_iso", lambda: FROZEN_NOW)
    create_ns = _synthesize_namespace(
        tmp_path=tmp_path,
        capability_need="write a skill to summarize documents",
        from_spec=None,
    )
    rc_create = cli_main.cmd_synthesize(create_ns)
    assert rc_create == EXIT_OK, (
        f"workspace bootstrap synthesize must return EXIT_OK; "
        f"got rc={rc_create}"
    )
    create_payload = json.loads(capsys.readouterr().out)

    def _must_not_call(**kwargs: object) -> object:
        raise RuntimeError(
            "run_optimizer_pipeline must not be called when "
            "the synthesis workspace still carries pending "
            "generated cases"
        )

    monkeypatch.setattr(
        synth_mod, "run_synthesis_optimizer", _must_not_call
    )

    ns = _synthesize_namespace(
        tmp_path=tmp_path,
        capability_need=None,
        from_spec=None,
    )
    rc = cli_main.cmd_synthesize(ns)
    captured = capsys.readouterr()

    assert rc == EXIT_OK, (
        f"pending-review re-invocation must return EXIT_OK "
        f"with outcome=draft_pending_review; got rc={rc} "
        f"stdout={captured.out!r} stderr={captured.err!r}"
    )
    payload = json.loads(captured.out)
    assert payload.get("status") == "OK", (
        f"pending-review payload status must be 'OK'; got "
        f"{payload.get('status')!r}"
    )
    assert (
        payload.get("outcome") == SYNTHESIZE_DRAFT_PENDING_REVIEW
    ), (
        f"pending-review re-invocation must keep outcome="
        f"{SYNTHESIZE_DRAFT_PENDING_REVIEW!r}; got "
        f"{payload.get('outcome')!r}"
    )
    assert payload.get("sentinel") == BOOTSTRAP_PENDING_REVIEW_FIELD, (
        f"pending-review payload must keep the "
        f"BOOTSTRAP_PENDING_REVIEW sentinel; got "
        f"{payload.get('sentinel')!r}"
    )
    # Pin the "indistinguishable" contract: the create-success
    # and resume short-circuit payloads are field-by-field equal
    # (modulo volatile ``created_at``).
    assert_payloads_equal_modulo_volatile(create_payload, payload)


# --------------------------------------------------------------------------- #
# Cross-task integration gap (F4 global review)                               #
# --------------------------------------------------------------------------- #
#
# The Task 1 parser accepts three F3 confirmation flags
# (``--allow-routing-revision``, ``--allow-dirty-unrelated``,
# ``--confirm-resume``) and advertises them as "aligned with
# optimize", but the Task 3 resume path silently dropped them
# before the optimizer pipeline call. The integration repair
# threads the three flags through
# :func:`metacrucible.synthesize.run_synthesis_optimizer` and
# forwards them into :func:`run_optimizer_pipeline`, mirroring
# :func:`metacrucible.__main__.cmd_optimize`. These two tests
# pin the end-to-end wiring by patching the underlying
# :func:`metacrucible.optimizer.run_optimizer_pipeline`
# reference (NOT the wrapper) and asserting the call sequence
# + kwargs from the parser down to the pipeline.


def test_synthesize_allow_routing_revision_escalates_preview_to_mutate(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--allow-routing-revision`` must escalate the synthesize
    resume path from a PREVIEW pipeline call to a mutating
    pipeline call (``human_confirmed=True``), mirroring
    :func:`metacrucible.__main__.cmd_optimize`'s preview /
    apply cutover.

    The test monkeypatches the underlying
    :func:`metacrucible.optimizer.run_optimizer_pipeline`
    reference (NOT :func:`run_synthesis_optimizer`) so the
    wrapper's preview / apply logic is exercised end-to-end
    against a deterministic fake. The fake returns a
    ``status="PREVIEW"`` result on the first call (simulating
    a routing-revision proposal) and a
    ``status="ACCEPTED"`` result on the second call (simulating
    the mutating pass after operator approval). The block in
    :func:`run_synthesis_optimizer` triggers the second call
    only when ``allow_routing_revision=True`` is forwarded.

    Pinned:
      - exactly 2 ``run_optimizer_pipeline`` calls,
      - first call: ``human_confirmed=False``,
        ``routing_confirmation_preview=True`` (preview pass),
      - second call: ``human_confirmed=True``,
        ``routing_confirmation_preview=False`` (mutating
        pass after explicit operator approval),
      - payload ``status='OK'`` and ``outcome='accepted'``
        (the mutating pass returned ACCEPTED).
    """
    from dataclasses import dataclass
    from metacrucible import synthesize as synth_mod
    from metacrucible import __main__ as cli_main

    @dataclass
    class _PreviewStubResult:
        status: str = "PREVIEW"
        run_id: str = "stub-preview"
        rounds: int = 0
        record_counts: dict[str, int] = None  # type: ignore[assignment]
        evidence_refs: dict[str, str] = None  # type: ignore[assignment]
        blockers: list = None  # type: ignore[assignment]
        warnings: list = None  # type: ignore[assignment]
        best_revision: dict | None = None
        acceptance_decision: dict = None  # type: ignore[assignment]
        selected_candidate_ids: list = None  # type: ignore[assignment]
        stop_reason: str = "routing_confirmation_preview"
        preview: dict | None = None

    @dataclass
    class _AcceptedStubResult:
        status: str = "ACCEPTED"
        run_id: str = "stub-accepted"
        rounds: int = 1
        record_counts: dict[str, int] = None  # type: ignore[assignment]
        evidence_refs: dict[str, str] = None  # type: ignore[assignment]
        blockers: list = None  # type: ignore[assignment]
        warnings: list = None  # type: ignore[assignment]
        best_revision: dict | None = None
        acceptance_decision: dict = None  # type: ignore[assignment]
        selected_candidate_ids: list = None  # type: ignore[assignment]
        stop_reason: str = "accepted"
        preview: dict | None = None

    workspace = _reviewed_synthesis_workspace(
        tmp_path, monkeypatch=monkeypatch, capsys=capsys
    )
    # Drain the bootstrap call's emit so the test's own
    # ``captured.out`` carries only the resume call.
    capsys.readouterr()

    call_log: list[dict[str, object]] = []

    def _stub_run_optimizer_pipeline(**kwargs: object) -> object:
        call_log.append(dict(kwargs))
        if len(call_log) == 1:
            # Preview pass: simulate a routing revision proposal
            # so the wrapper's preview / apply cutover fires.
            return _PreviewStubResult(
                record_counts={},
                evidence_refs={
                    "receipt": "/tmp/evidence/stub-preview/receipt.json"
                },
                blockers=[],
                warnings=[],
                best_revision=None,
                acceptance_decision={},
                selected_candidate_ids=[],
                preview={
                    "routing_confirmation": [
                        {
                            "suggestion_id": "sug-1",
                            "routing_field": "model",
                            "old": "haiku",
                            "new": "sonnet",
                        }
                    ],
                    "profile_verdict": {"blockers": []},
                },
            )
        # Mutating pass: simulate ACCEPTED so the resume path
        # exits with the accepted outcome + EXIT_OK.
        return _AcceptedStubResult(
            record_counts={"case_eval": 1, "case_held_out": 1},
            evidence_refs={
                "receipt": "/tmp/evidence/stub-accepted/receipt.json"
            },
            blockers=[],
            warnings=[],
            best_revision=None,
            acceptance_decision={
                "accepted": True,
                "reason": "accepted",
            },
            selected_candidate_ids=["cand-1"],
        )

    monkeypatch.setattr(
        synth_mod,
        "run_optimizer_pipeline",
        _stub_run_optimizer_pipeline,
    )
    # The Task 4 BLOCKED bundle write would touch the
    # user-global storage; the escalate path does not trigger
    # it (the mutating pass returns ACCEPTED), but pin the
    # guard so a future test cannot regress to a real write.
    monkeypatch.setattr(
        synth_mod, "_write_synthesize_blocked_bundle", lambda _: {}
    )

    ns = argparse.Namespace(
        command="synthesize",
        capability_need=None,
        from_spec=None,
        output=str(workspace),
        max_rounds=ROUND_BUDGET_DEFAULT,
        json=True,
        allow_routing_revision=True,
        allow_dirty_unrelated=False,
        confirm_resume=False,
    )
    rc = cli_main.cmd_synthesize(ns)
    captured = capsys.readouterr()

    assert rc == EXIT_OK, (
        f"synthesize with --allow-routing-revision must return "
        f"EXIT_OK when the mutating pass accepts; got rc={rc} "
        f"stdout={captured.out!r} stderr={captured.err!r}"
    )
    assert len(call_log) == 2, (
        f"--allow-routing-revision must escalate the resume "
        f"path from 1 preview call to 2 (preview + mutate); "
        f"got {len(call_log)} call(s); calls={call_log!r}"
    )
    # First call: preview pass.
    first = call_log[0]
    assert first.get("human_confirmed") is False, (
        f"preview pass must pass human_confirmed=False; "
        f"got {first.get('human_confirmed')!r}"
    )
    assert first.get("routing_confirmation_preview") is True, (
        f"preview pass must pass "
        f"routing_confirmation_preview=True; "
        f"got {first.get('routing_confirmation_preview')!r}"
    )
    assert first.get("max_rounds") == ROUND_BUDGET_DEFAULT, (
        f"preview pass must forward max_rounds from the "
        f"dispatcher ({ROUND_BUDGET_DEFAULT}); got "
        f"{first.get('max_rounds')!r}"
    )
    # Second call: mutating pass.
    second = call_log[1]
    assert second.get("human_confirmed") is True, (
        f"mutating pass must pass human_confirmed=True when "
        f"--allow-routing-revision is set; got "
        f"{second.get('human_confirmed')!r}"
    )
    assert second.get("routing_confirmation_preview") is False, (
        f"mutating pass must pass "
        f"routing_confirmation_preview=False; got "
        f"{second.get('routing_confirmation_preview')!r}"
    )
    assert second.get("max_rounds") == ROUND_BUDGET_DEFAULT, (
        f"mutating pass must forward max_rounds from the "
        f"dispatcher ({ROUND_BUDGET_DEFAULT}); got "
        f"{second.get('max_rounds')!r}"
    )
    payload = json.loads(captured.out)
    assert payload.get("status") == "OK", (
        f"payload status must be 'OK' when mutating pass "
        f"accepts; got {payload.get('status')!r}"
    )
    assert payload.get("outcome") == "accepted", (
        f"payload outcome must be 'accepted' when mutating "
        f"pass accepts; got {payload.get('outcome')!r}"
    )
    # The threaded flag MUST land on the BLOCKED payload shape
    # even on the accepted path (mirror cmd_optimize).
    assert payload.get("allow_dirty_unrelated") is False, (
        f"payload must carry allow_dirty_unrelated=False on "
        f"accepted resume; got "
        f"{payload.get('allow_dirty_unrelated')!r}"
    )
    assert payload.get("confirm_resume") is False, (
        f"payload must carry confirm_resume=False on accepted "
        f"resume; got {payload.get('confirm_resume')!r}"
    )


def test_synthesize_without_allow_routing_revision_blocks_on_preview(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without ``--allow-routing-revision`` the synthesize
    resume path runs ONE preview pass and BLOCKED on the
    PREVIEW result (the wrapper does NOT escalate to a
    mutating pass). Mirrors the
    :func:`metacrucible.__main__.cmd_optimize` preview /
    apply cutover.

    The test also pins that the threaded
    ``--allow-dirty-unrelated`` and ``--confirm-resume``
    flags reach the BLOCKED payload (mirror cmd_optimize
    BLOCKED payload shape) so downstream consumers can see
    the dispatcher-level flag values on the synthesize-side
    BLOCKED records.

    Pinned:
      - exactly 1 ``run_optimizer_pipeline`` call,
      - that call: ``human_confirmed=False``,
        ``routing_confirmation_preview=True``,
      - payload ``status='BLOCKED'`` and
        ``outcome='aborted'`` (PREVIEW is not ACCEPTED),
      - payload carries ``allow_dirty_unrelated=True`` and
        ``confirm_resume=True`` (the operator-supplied
        threaded flag values).
    """
    from dataclasses import dataclass
    from metacrucible import synthesize as synth_mod
    from metacrucible import __main__ as cli_main

    @dataclass
    class _PreviewStubResult:
        status: str = "PREVIEW"
        run_id: str = "stub-preview"
        rounds: int = 0
        record_counts: dict[str, int] = None  # type: ignore[assignment]
        evidence_refs: dict[str, str] = None  # type: ignore[assignment]
        blockers: list = None  # type: ignore[assignment]
        warnings: list = None  # type: ignore[assignment]
        best_revision: dict | None = None
        acceptance_decision: dict = None  # type: ignore[assignment]
        selected_candidate_ids: list = None  # type: ignore[assignment]
        stop_reason: str = "routing_confirmation_preview"
        preview: dict | None = None

    workspace = _reviewed_synthesis_workspace(
        tmp_path, monkeypatch=monkeypatch, capsys=capsys
    )
    capsys.readouterr()

    call_log: list[dict[str, object]] = []

    def _stub_run_optimizer_pipeline(**kwargs: object) -> object:
        call_log.append(dict(kwargs))
        return _PreviewStubResult(
            record_counts={},
            evidence_refs={
                "receipt": "/tmp/evidence/stub-preview/receipt.json"
            },
            blockers=[
                {
                    "id": "routing-revision-confirmation-required",
                    "message": "test routing revision requires confirmation",
                }
            ],
            warnings=[],
            best_revision=None,
            acceptance_decision={},
            selected_candidate_ids=[],
            preview={
                "routing_confirmation": [
                    {
                        "suggestion_id": "sug-1",
                        "routing_field": "model",
                        "old": "haiku",
                        "new": "sonnet",
                    }
                ],
                "profile_verdict": {"blockers": []},
            },
        )

    monkeypatch.setattr(
        synth_mod,
        "run_optimizer_pipeline",
        _stub_run_optimizer_pipeline,
    )
    # The Task 4 BLOCKED bundle write would touch the
    # user-global storage; the BLOCKED resume path triggers
    # it. Stub it out so the test does not need to wire a
    # fake HOME.
    monkeypatch.setattr(
        synth_mod, "_write_synthesize_blocked_bundle", lambda _: {}
    )

    ns = argparse.Namespace(
        command="synthesize",
        capability_need=None,
        from_spec=None,
        output=str(workspace),
        max_rounds=ROUND_BUDGET_DEFAULT,
        json=True,
        allow_routing_revision=False,
        allow_dirty_unrelated=True,
        confirm_resume=True,
    )
    rc = cli_main.cmd_synthesize(ns)
    captured = capsys.readouterr()

    assert rc == EXIT_BLOCKED, (
        f"synthesize without --allow-routing-revision must "
        f"return EXIT_BLOCKED on PREVIEW; got rc={rc} "
        f"stdout={captured.out!r} stderr={captured.err!r}"
    )
    assert len(call_log) == 1, (
        f"without --allow-routing-revision the resume path "
        f"must call run_optimizer_pipeline exactly once; "
        f"got {len(call_log)} call(s); calls={call_log!r}"
    )
    only = call_log[0]
    assert only.get("human_confirmed") is False, (
        f"preview-only call must pass human_confirmed=False; "
        f"got {only.get('human_confirmed')!r}"
    )
    assert only.get("routing_confirmation_preview") is True, (
        f"preview-only call must pass "
        f"routing_confirmation_preview=True; got "
        f"{only.get('routing_confirmation_preview')!r}"
    )
    payload = json.loads(captured.out)
    assert payload.get("status") == "BLOCKED", (
        f"payload status must be 'BLOCKED' on PREVIEW without "
        f"--allow-routing-revision; got {payload.get('status')!r}"
    )
    assert payload.get("outcome") == "aborted", (
        f"payload outcome must be 'aborted' on PREVIEW without "
        f"--allow-routing-revision; got {payload.get('outcome')!r}"
    )
    # Pin the BLOCKED payload shape: the threaded
    # ``--allow-dirty-unrelated`` and ``--confirm-resume``
    # flags MUST reach the BLOCKED payload (mirror cmd_optimize
    # BLOCKED payload shape).
    assert payload.get("allow_dirty_unrelated") is True, (
        f"BLOCKED payload must carry allow_dirty_unrelated=True "
        f"when --allow-dirty-unrelated is set; got "
        f"{payload.get('allow_dirty_unrelated')!r}"
    )
    assert payload.get("confirm_resume") is True, (
        f"BLOCKED payload must carry confirm_resume=True "
        f"when --confirm-resume is set; got "
        f"{payload.get('confirm_resume')!r}"
    )
