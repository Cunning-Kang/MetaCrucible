"""Tests for Issue #41 (PRD F4 ``metacrucible synthesize``) Task 1.

Task 1 ships ONLY the parser shell + a temporary
``synthesize-not-implemented`` placeholder. The placeholder surfaces
in the BLOCKED bundle on every invocation until Task 2 wires the
real synthesis pipeline in. These tests pin the public parser
contract that subsequent tasks must not break:

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
tasks; those tests live in subsequent Task-N files. The blocker
id ``synthesize-not-implemented`` is the explicit Task 1 contract
and MUST be removed by Task 2.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from metacrucible.optimizer import ROUND_BUDGET_DEFAULT


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


def test_synthesize_command_emits_blocked_payload_human(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC1 (dispatcher, human): ``synthesize <need> --output <path>``
    returns :data:`metacrucible.exit_codes.EXIT_BLOCKED` and emits a
    BLOCKED bundle carrying the pinned
    :data:`metacrucible.__main__.SYNTHESIZE_NOT_IMPLEMENTED_BLOCKER`
    id.

    Task 1 ships only a parser shell + a transient placeholder; the
    placeholder surfaces on every invocation until Task 2 wires the
    real synthesis pipeline in. This test pins the user-visible
    surface through the PUBLIC ``main()`` entrypoint (no
    private-method coupling, no mocks) so future refactors cannot
    silently drop the BLOCKED bundle or change the pinned id
    without breaking automation. The blocker id is referenced via
    the constant so a future id rename propagates to the test
    instead of stranding a stale literal — the contract under test
    is "the placeholder carries THIS id, whatever it is", not "the
    literal string ``synthesize-not-implemented``".
    """
    from metacrucible import __main__ as cli_main
    from metacrucible.__main__ import SYNTHESIZE_NOT_IMPLEMENTED_BLOCKER
    from metacrucible.exit_codes import EXIT_BLOCKED

    output_path = tmp_path / "skill"
    rc = cli_main.main(
        ["synthesize", "write a skill", "--output", str(output_path)]
    )
    captured = capsys.readouterr()

    assert rc == EXIT_BLOCKED, (
        f"synthesize Task 1 placeholder must return EXIT_BLOCKED; "
        f"got rc={rc} stdout={captured.out!r} stderr={captured.err!r}"
    )
    assert "BLOCKED" in captured.out, (
        f"human surface must surface status: BLOCKED; "
        f"got stdout={captured.out!r}"
    )
    assert SYNTHESIZE_NOT_IMPLEMENTED_BLOCKER in captured.out, (
        f"human surface must surface the pinned blocker id "
        f"({SYNTHESIZE_NOT_IMPLEMENTED_BLOCKER!r}); "
        f"got stdout={captured.out!r}"
    )


def test_synthesize_command_emits_blocked_payload_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC1 (dispatcher, JSON): ``synthesize <need> --output <path> --json``
    returns :data:`metacrucible.exit_codes.EXIT_BLOCKED` and emits a
    parseable JSON object whose ``status`` is ``"BLOCKED"`` and whose
    ``blockers[0].id`` is
    :data:`metacrucible.__main__.SYNTHESIZE_NOT_IMPLEMENTED_BLOCKER`.

    The id is asserted via the constant reference (not a string
    literal) so a future id rename forces a coordinated test update
    instead of silently passing. Closes the dispatcher-contract gap
    left by the parser-only coverage that Task 1 originally
    shipped (Finding 1 of the Task 1 code-quality review).
    """
    import json

    from metacrucible import __main__ as cli_main
    from metacrucible.__main__ import SYNTHESIZE_NOT_IMPLEMENTED_BLOCKER
    from metacrucible.exit_codes import EXIT_BLOCKED

    output_path = tmp_path / "skill"
    rc = cli_main.main(
        [
            "synthesize",
            "write a skill",
            "--output",
            str(output_path),
            "--json",
        ]
    )
    captured = capsys.readouterr()

    assert rc == EXIT_BLOCKED, (
        f"synthesize --json Task 1 placeholder must return EXIT_BLOCKED; "
        f"got rc={rc} stdout={captured.out!r} stderr={captured.err!r}"
    )
    payload = json.loads(captured.out)
    assert payload["status"] == "BLOCKED", (
        f"--json payload must surface status=BLOCKED; "
        f"got status={payload.get('status')!r}"
    )
    assert payload["blockers"], (
        f"--json payload must carry at least one blocker; "
        f"got blockers={payload.get('blockers')!r}"
    )
    assert payload["blockers"][0]["id"] == SYNTHESIZE_NOT_IMPLEMENTED_BLOCKER, (
        f"first blocker must carry the pinned id "
        f"({SYNTHESIZE_NOT_IMPLEMENTED_BLOCKER!r}); "
        f"got blockers[0]={payload['blockers'][0]!r}"
    )
