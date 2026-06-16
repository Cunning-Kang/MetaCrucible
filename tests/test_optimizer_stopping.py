"""Stopping Condition (Issue #36) regression tests.

Pins the machine-stable ``stop_reason`` contract on the
optimizer pipeline and the ``optimize`` CLI:

  - :class:`metacrucible.optimizer.OptimizerPipelineResult`
    always carries a populated ``stop_reason`` (one of
    :data:`metacrucible.optimizer.STOP_REASONS`).
  - The CLI ``--json`` payload surfaces the same
    ``stop_reason`` at the top level so downstream tools
    can branch on it without re-deriving the verdict from
    ``status`` / ``blockers`` / ``warnings``.
  - The ``optimize_finished`` history event carries the
    same ``stop_reason`` so a lineage reader can branch on
    the per-event reason without cross-referencing the
    evidence bundle. The ``optimize_blocked`` history
    events do NOT carry ``stop_reason`` (the canonical
    reason lives on the result and the
    ``optimize_finished`` event only; blocked events stay
    stop_reason-free to keep the three blocked-event
    payloads uniform with the pre-existing lineage
    contract).

Each test exercises exactly one Stopping Condition path:

  - ``no_candidate_edits`` — a non-BLOCKED completion with
    an empty-suggestion round (the MVP no-LLM path).
  - ``precondition_blocked`` — a BLOCKED completion via the
    loader-level missing-reviewed-cases precondition.
  - ``round_blocked`` — a BLOCKED completion via the
    in-loop ``_RoundBlocked`` signal (driven by the
    routing-HITL gate so the test is deterministic).
  - CLI ``--json`` round-trip — confirms the same
    ``stop_reason`` shows up in the top-level JSON
    payload when the pipeline is invoked through
    :func:`metacrucible.__main__.cmd_optimize`.

The tests follow the OPT-9 fixture pattern from
:mod:`tests.test_optimize_command`: a tiny Skill with a
single mutable body range, a benchmark with one eligible
eval + one eligible held-out case, and an envelope that
declares the artifact path.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from metacrucible.exit_codes import EXIT_BLOCKED, EXIT_OK
from metacrucible.optimizer import (
    STOP_REASONS,
    STOP_REASON_MAX_ROUNDS_REACHED,
    STOP_REASON_NO_CANDIDATE_EDITS,
    STOP_REASON_PRECONDITION_BLOCKED,
    STOP_REASON_ROUND_BLOCKED,
    run_optimizer_pipeline,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_FILE_NAME = "benchmark.jsonl"
ENVELOPE_REL_PATH = Path(".metacrucible") / "envelope.json"

#: Canonical fixture body text — kept here so the test
#: stays self-contained and does not depend on private
#: helpers from :mod:`tests.test_optimize_command`.
_BODY_TEXT = "# body\nThe body is the only mutable range.\n"
_ARTIFACT_TEXT = (
    "---\n"
    "name: stop-reason-skill\n"
    "description: Stopping Condition regression fixture\n"
    "---\n"
    f"{_BODY_TEXT}"
)


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> Path:
    """Write ``records`` as one JSON object per line at ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(dict(rec), sort_keys=True) for rec in records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _metadata_record() -> dict[str, Any]:
    """Minimal benchmark metadata record (ADR 0029)."""
    return {
        "record_type": "metadata",
        "name": "stop-reason-benchmark",
        "schema_version": 1,
    }


def _reviewed_case(case_id: str, *, split: str = "eval") -> dict[str, Any]:
    """Build a minimal eligible reviewed case (ADR 0029)."""
    return {
        "record_type": "case",
        "case_id": case_id,
        "status": "reviewed",
        "split": split,
        "input": {"prompt": "do the thing"},
        "execution_boundary": {"permissions": ["read"]},
        "checks": [{"name": "ok", "pattern": "ok"}],
    }


def _init_workspace(tmp_path: Path) -> Path:
    """Run ``init`` against a fresh workspace dir and return that dir.

    The fixture creates the empty benchmark container that the
    stopping-condition test then seeds with custom records.
    """
    workspace = tmp_path / "ws-stop-reason"
    workspace.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-m", "metacrucible", "init", str(workspace)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == EXIT_OK, (
        f"`init` must exit 0 before optimize; got "
        f"rc={result.returncode} stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    return workspace


def _seed_fixture(
    workspace: Path,
    *,
    include_reviewed_cases: bool = True,
) -> tuple[Path, Path]:
    """Seed a runnable OPT-9-style fixture and return ``(benchmark, artifact)``.

    Writes a benchmark with a metadata record and (optionally) one
    eligible eval + one eligible held-out case, an envelope that
    declares the artifact path, and the artifact body. Used by the
    ``no_candidate_edits``, ``round_blocked``, and CLI tests.

    When ``include_reviewed_cases`` is False, only the metadata
    record is written — that drives the
    ``precondition_blocked`` path.
    """
    benchmark = workspace / BENCHMARK_FILE_NAME
    artifact = workspace / "SKILL.md"
    artifact.write_text(_ARTIFACT_TEXT, encoding="utf-8")
    envelope = workspace / ENVELOPE_REL_PATH
    envelope.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "artifact_path": str(artifact),
                "artifact_workspace": str(workspace),
                "created_at": "2026-01-01T00:00:00Z",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    records: list[dict[str, Any]] = [_metadata_record()]
    if include_reviewed_cases:
        records.append(_reviewed_case("eval-1", split="eval"))
        records.append(_reviewed_case("held-1", split="held_out"))
    _write_jsonl(benchmark, records)
    return benchmark, artifact


def _read_history(workspace: Path) -> list[dict[str, Any]]:
    """Return every record persisted to ``history.jsonl``."""
    history = workspace / ".metacrucible" / "history.jsonl"
    if not history.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line in history.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if isinstance(rec, dict):
            records.append(rec)
    return records


# --------------------------------------------------------------------------- #
# non-BLOCKED completion: ``no_candidate_edits``                                #
# --------------------------------------------------------------------------- #


def test_stop_reason_no_candidate_edits_when_no_suggestions(
    tmp_path: Path,
) -> None:
    """``no_candidate_edits`` is the stop reason when a round
    produced zero usable suggestions and the loop broke.

    The MVP no-LLM path (``call_fn=None``) synthesizes an
    empty ``suggested_edits`` list per round, so the
    pipeline exits with the ``no_candidate_edits`` warning
    and the ``stop_reason="no_candidate_edits"`` value.
    The ``optimize_finished`` history event must record
    the same stop reason so a lineage reader does not have
    to cross-reference the result.
    """
    workspace = _init_workspace(tmp_path)
    benchmark, artifact = _seed_fixture(workspace)

    result = run_optimizer_pipeline(
        workspace=workspace,
        benchmark_path=benchmark,
        artifact_path=artifact,
        call_fn=None,  # MVP: no LLM wired → empty suggested_edits
        max_rounds=1,
        human_confirmed=False,
    )

    # Result carries the machine-stable reason.
    assert result.stop_reason == STOP_REASON_NO_CANDIDATE_EDITS, (
        f"pipeline must record stop_reason="
        f"{STOP_REASON_NO_CANDIDATE_EDITS!r} when a round "
        f"produced no usable suggestions; got "
        f"result.stop_reason={result.stop_reason!r}"
    )
    assert result.stop_reason in STOP_REASONS, (
        f"stop_reason must be one of the vocabulary "
        f"{sorted(STOP_REASONS)!r}; got {result.stop_reason!r}"
    )
    assert result.status == "REJECTED", (
        f"an empty-suggestion round must terminate with "
        f"REJECTED status (the no-improvement verdict); "
        f"got {result.status!r}"
    )
    # The corresponding warning is also on the result so
    # the operator sees the human English rationale.
    warning_ids = [
        w.get("id") for w in (result.warnings or [])
        if isinstance(w, dict)
    ]
    assert "no_candidate_edits" in warning_ids, (
        f"empty-suggestion run must surface the "
        f"no_candidate_edits warning on result.warnings; "
        f"got warning_ids={warning_ids!r}"
    )

    # The optimize_finished history event carries the
    # same stop reason so a downstream lineage reader can
    # branch on it without re-running the pipeline.
    finished = [
        r for r in _read_history(workspace)
        if isinstance(r, dict) and r.get("event") == "optimize_finished"
    ]
    assert finished, (
        f"pipeline must persist an optimize_finished "
        f"event for a non-precondition completion; got "
        f"history_events={[r.get('event') for r in _read_history(workspace)]!r}"
    )
    assert finished[-1].get("stop_reason") == STOP_REASON_NO_CANDIDATE_EDITS, (
        f"optimize_finished.stop_reason must mirror the "
        f"pipeline result; got "
        f"stop_reason={finished[-1].get('stop_reason')!r}"
    )


# --------------------------------------------------------------------------- #
# BLOCKED completion: ``precondition_blocked``                                  #
# --------------------------------------------------------------------------- #


def test_stop_reason_precondition_blocked_when_no_reviewed_cases(
    tmp_path: Path,
) -> None:
    """``precondition_blocked`` is the stop reason when the
    loader-level blocker (no eligible reviewed cases)
    blocks the run before the round loop.

    The pipeline's precondition check sees the empty
    benchmark (metadata only) and returns early with
    ``status="BLOCKED"`` and
    ``stop_reason="precondition_blocked"``. The path
    writes an ``optimize_blocked`` event but no
    ``optimize_finished`` event — the precondition
    short-circuits the run before the completion hook.
    """
    workspace = _init_workspace(tmp_path)
    benchmark, artifact = _seed_fixture(
        workspace, include_reviewed_cases=False
    )

    result = run_optimizer_pipeline(
        workspace=workspace,
        benchmark_path=benchmark,
        artifact_path=artifact,
        call_fn=None,
        max_rounds=1,
        human_confirmed=False,
    )

    # Result carries the precondition reason.
    assert result.stop_reason == STOP_REASON_PRECONDITION_BLOCKED, (
        f"pipeline must record stop_reason="
        f"{STOP_REASON_PRECONDITION_BLOCKED!r} when the "
        f"loader-level blocker short-circuits the run; "
        f"got result.stop_reason={result.stop_reason!r}"
    )
    assert result.status == "BLOCKED", (
        f"precondition-blocked run must terminate with "
        f"BLOCKED status; got {result.status!r}"
    )
    assert result.rounds == 0, (
        f"precondition-blocked run must not enter the "
        f"round loop; got rounds={result.rounds!r}"
    )
    # The loader's missing-required-cases blockers are
    # surfaced verbatim so the operator can branch on
    # them.
    blocker_ids = [
        b.get("id") for b in (result.blockers or [])
        if isinstance(b, dict)
    ]
    assert "missing-reviewed-eval-case" in blocker_ids, (
        f"precondition-blocked run must surface the "
        f"loader missing-reviewed-eval-case blocker; "
        f"got blocker_ids={blocker_ids!r}"
    )
    assert "missing-reviewed-held-out-case" in blocker_ids, (
        f"precondition-blocked run must surface the "
        f"loader missing-reviewed-held-out-case blocker; "
        f"got blocker_ids={blocker_ids!r}"
    )

    # The optimize_blocked lineage event is written but
    # the optimize_finished event is NOT — the
    # precondition path short-circuits the run before
    # the completion hook. The CLI and the evidence
    # bundle are the only completion records.
    history = _read_history(workspace)
    events = [r.get("event") for r in history if isinstance(r, dict)]
    assert "optimize_finished" not in events, (
        f"precondition-blocked run must NOT emit an "
        f"optimize_finished event; got events={events!r}"
    )
    assert "optimize_blocked" in events, (
        f"precondition-blocked run MUST emit an "
        f"optimize_blocked event for the lineage; got "
        f"events={events!r}"
    )


# --------------------------------------------------------------------------- #
# BLOCKED completion: ``round_blocked``                                         #
# --------------------------------------------------------------------------- #


def test_stop_reason_round_blocked_on_empty_replacement(
    tmp_path: Path,
) -> None:
    """``round_blocked`` is the stop reason when the
    in-loop :class:`metacrucible.optimizer._RoundBlocked`
    signal trips before the round can apply a candidate.

    The test injects a single non-routing suggestion whose
    ``replacement`` is the empty string. The merge plan
    stage (``_build_merge_plan``) flips
    ``merge_outside_mutable_range=True`` for the empty
    replacement, the runner appends
    :data:`metacrucible.optimizer.MUTABLE_RANGE_CONFLICT_BLOCKER`,
    and the round raises ``_RoundBlocked``. The pipeline
    terminates with ``status="BLOCKED"`` and
    ``stop_reason="round_blocked"``.

    The ``optimize_finished`` completion event carries
    the final ``stop_reason`` so a single lineage query
    can answer "why did this run stop" without joining
    the evidence bundle. The ``optimize_blocked``
    event itself stays stop_reason-free to keep the
    three blocked-event payloads uniform with the
    pre-existing lineage contract.
    """
    import hashlib

    workspace = _init_workspace(tmp_path)
    benchmark, artifact = _seed_fixture(workspace)
    body_hash = hashlib.sha256(_BODY_TEXT.encode("utf-8")).hexdigest()

    def _empty_replacement_call_fn(
        *, repair_context: Any = None
    ) -> dict[str, Any]:
        """Return one non-routing edit with an empty body.

        The base_hash is the current body hash so the
        suggestion survives the step 3c dedup, gets
        selected in step 3d, and trips the merge plan
        in step 3f because ``replacement`` is empty
        (``fits_in_range = bool(replacement)`` is False).
        """
        return {
            "rationale": "round_blocked contract regression",
            "suggested_edits": [
                {
                    "range_id": 0,
                    "base_hash": body_hash,
                    "intent": "round_blocked_empty_replacement",
                    "replacement": "",
                    "rationale": "empty replacement must block the round",
                    "routing": False,
                }
            ],
        }

    result = run_optimizer_pipeline(
        workspace=workspace,
        benchmark_path=benchmark,
        artifact_path=artifact,
        call_fn=_empty_replacement_call_fn,
        max_rounds=1,
        human_confirmed=False,
    )

    # Result carries the round-blocked reason.
    assert result.stop_reason == STOP_REASON_ROUND_BLOCKED, (
        f"pipeline must record stop_reason="
        f"{STOP_REASON_ROUND_BLOCKED!r} when a round "
        f"trips the _RoundBlocked signal; got "
        f"result.stop_reason={result.stop_reason!r}"
    )
    assert result.status == "BLOCKED", (
        f"round-blocked run must terminate with BLOCKED "
        f"status; got {result.status!r}"
    )
    # The artifact on disk must be unchanged because the
    # round never applied the candidate. The run-level
    # rollback restored the base bytes in the
    # ``_RoundBlocked`` handler.
    assert artifact.read_bytes() == _ARTIFACT_TEXT.encode("utf-8"), (
        f"round-blocked run must NOT mutate the "
        f"artifact; expected the seed text unchanged, "
        f"got {artifact.read_bytes()!r}"
    )

    # The optimize_blocked lineage event is emitted
    # uniformly across all blocked paths (precondition,
    # profile-gate, round-blocked) without a stop_reason
    # field; the canonical stop_reason lives on the
    # result and on the optimize_finished event.
    history = _read_history(workspace)
    blocked_events = [
        r for r in history
        if isinstance(r, dict) and r.get("event") == "optimize_blocked"
    ]
    assert blocked_events, (
        f"round-blocked run must persist an "
        f"optimize_blocked event; got events="
        f"{[r.get('event') for r in history]!r}"
    )
    # The optimize_finished event is still written by
    # the post-loop completion hook — it carries the
    # final stop_reason so the lineage has one canonical
    # record per run.
    finished = [
        r for r in history
        if isinstance(r, dict) and r.get("event") == "optimize_finished"
    ]
    assert finished, (
        f"round-blocked run must still emit an "
        f"optimize_finished event with the final "
        f"stop_reason; got events="
        f"{[r.get('event') for r in history]!r}"
    )
    assert finished[-1].get("stop_reason") == STOP_REASON_ROUND_BLOCKED, (
        f"optimize_finished.stop_reason must mirror the "
        f"result; got "
        f"stop_reason={finished[-1].get('stop_reason')!r}"
    )


# --------------------------------------------------------------------------- #
# Negative regression: ``optimize_blocked`` events do NOT carry ``stop_reason``#
# --------------------------------------------------------------------------- #


def test_optimize_blocked_events_do_not_carry_stop_reason(
    tmp_path: Path,
) -> None:
    """Every ``optimize_blocked`` lineage event must be
    emitted without a ``stop_reason`` field.

    The blocked-lineage contract is uniform across all
    three blocked paths (precondition, profile-gate,
    round-blocked): each ``optimize_blocked`` history
    event records the per-event cause (``round_id``,
    ``blockers``, ``timestamp``) and leaves the
    canonical machine-stable ``stop_reason`` to the
    pipeline result and the ``optimize_finished`` event
    only. A regression that re-adds ``stop_reason`` to
    any blocked event (or that starts omitting it from
    the finished event) breaks the lineage contract and
    must surface here.

    The test exercises two of the three blocked paths
    deterministically — ``precondition_blocked`` (no
    reviewed cases) and ``round_blocked`` (empty
    replacement) — and asserts the field is absent on
    every ``optimize_blocked`` event in the history
    stream. The profile-gate path is harder to trigger
    deterministically and shares the same emission
    site as the precondition path, so the two-path
    guard is sufficient to pin the contract.
    """
    import hashlib

    # --- precondition_blocked path ------------------------------- #
    pre_ws = _init_workspace(tmp_path / "pre")
    pre_benchmark, pre_artifact = _seed_fixture(
        pre_ws, include_reviewed_cases=False
    )
    pre_result = run_optimizer_pipeline(
        workspace=pre_ws,
        benchmark_path=pre_benchmark,
        artifact_path=pre_artifact,
        call_fn=None,
        max_rounds=1,
        human_confirmed=False,
    )
    assert pre_result.status == "BLOCKED", (
        f"precondition fixture must terminate BLOCKED; "
        f"got status={pre_result.status!r}"
    )
    pre_history = _read_history(pre_ws)
    pre_blocked = [
        r for r in pre_history
        if isinstance(r, dict) and r.get("event") == "optimize_blocked"
    ]
    assert pre_blocked, (
        f"precondition-blocked run must persist an "
        f"optimize_blocked event; got events="
        f"{[r.get('event') for r in pre_history]!r}"
    )
    for i, ev in enumerate(pre_blocked):
        assert "stop_reason" not in ev, (
            f"optimize_blocked event #{i} on the "
            f"precondition path must NOT carry a "
            f"stop_reason field; got event={ev!r}"
        )

    # --- round_blocked path -------------------------------------- #
    round_ws = _init_workspace(tmp_path / "round")
    round_benchmark, round_artifact = _seed_fixture(round_ws)
    body_hash = hashlib.sha256(_BODY_TEXT.encode("utf-8")).hexdigest()

    def _empty_replacement_call_fn(
        *, repair_context: Any = None
    ) -> dict[str, Any]:
        """Return one non-routing edit with an empty
        body so the merge plan flips
        ``merge_outside_mutable_range=True`` and the
        round raises ``_RoundBlocked``.
        """
        return {
            "rationale": "negative-guard fixture",
            "suggested_edits": [
                {
                    "range_id": 0,
                    "base_hash": body_hash,
                    "intent": "negative_guard_empty_replacement",
                    "replacement": "",
                    "rationale": "empty replacement trips the round",
                    "routing": False,
                }
            ],
        }

    round_result = run_optimizer_pipeline(
        workspace=round_ws,
        benchmark_path=round_benchmark,
        artifact_path=round_artifact,
        call_fn=_empty_replacement_call_fn,
        max_rounds=1,
        human_confirmed=False,
    )
    assert round_result.status == "BLOCKED", (
        f"round_blocked fixture must terminate BLOCKED; "
        f"got status={round_result.status!r}"
    )
    round_history = _read_history(round_ws)
    round_blocked = [
        r for r in round_history
        if isinstance(r, dict) and r.get("event") == "optimize_blocked"
    ]
    assert round_blocked, (
        f"round-blocked run must persist an "
        f"optimize_blocked event; got events="
        f"{[r.get('event') for r in round_history]!r}"
    )
    for i, ev in enumerate(round_blocked):
        assert "stop_reason" not in ev, (
            f"optimize_blocked event #{i} on the "
            f"round_blocked path must NOT carry a "
            f"stop_reason field; got event={ev!r}"
        )

    # --- cross-path uniformity check ----------------------------- #
    # Both blocked paths must agree that ``stop_reason``
    # is absent. The field shape is not required to be
    # identical: the precondition path short-circuits
    # before the round loop and therefore has no
    # ``round_id``, while the round_blocked path carries
    # one. The contract is uniform absence of
    # ``stop_reason``, not uniform field shape.
    for i, ev in enumerate(pre_blocked + round_blocked):
        assert "stop_reason" not in ev, (
            f"optimize_blocked event #{i} must NOT "
            f"carry a stop_reason field across either "
            f"blocked path; got event={ev!r}"
        )


# --------------------------------------------------------------------------- #
# CLI ``--json`` payload surfaces ``stop_reason`` at the top level              #
# --------------------------------------------------------------------------- #


def test_stop_reason_in_cli_json_payload(tmp_path: Path) -> None:
    """The ``optimize --json`` payload exposes the same
    ``stop_reason`` the pipeline recorded on the result.

    The test runs the full ``metacrucible optimize``
    command via subprocess (same pattern as the other
    CLI tests in :mod:`tests.test_optimize_command`) with
    a clean benchmark, a seeded envelope, and a seeded
    artifact. The pipeline's ``call_fn=None`` MVP path
    produces the ``no_candidate_edits`` stop reason; the
    CLI must surface that value at the top level of the
    ``--json`` payload, alongside ``status``, ``run_id``,
    and ``rounds``.
    """
    workspace = _init_workspace(tmp_path)
    _seed_fixture(workspace)

    result = subprocess.run(
        [
            sys.executable, "-m", "metacrucible",
            "optimize", str(workspace), "--json",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode in (EXIT_OK, EXIT_BLOCKED), (
        f"`optimize --json` must exit 0 or {EXIT_BLOCKED}; "
        f"got rc={result.returncode} stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(
            f"`optimize --json` must emit valid JSON on "
            f"stdout; got stdout={result.stdout!r} error={exc}"
        )
    assert isinstance(payload, dict), (
        f"optimize --json must return a JSON object; got "
        f"{type(payload).__name__} ({payload!r})"
    )
    # The stop_reason key is a top-level payload field
    # alongside status / run_id / rounds.
    assert "stop_reason" in payload, (
        f"optimize --json must surface stop_reason at "
        f"the top level; got keys={sorted(payload.keys())!r}"
    )
    assert payload["stop_reason"] == STOP_REASON_NO_CANDIDATE_EDITS, (
        f"CLI payload must report stop_reason="
        f"{STOP_REASON_NO_CANDIDATE_EDITS!r} for a "
        f"no-LLM run; got "
        f"payload['stop_reason']={payload['stop_reason']!r}"
    )
    # The reason must come from the canonical vocabulary;
    # the CLI must not invent a prose value.
    assert payload["stop_reason"] in STOP_REASONS, (
        f"CLI stop_reason must be a vocabulary string "
        f"from {sorted(STOP_REASONS)!r}; got "
        f"{payload['stop_reason']!r}"
    )


# --------------------------------------------------------------------------- #
# Vocabulary exhaustion: ``STOP_REASONS`` is the complete set                    #
# --------------------------------------------------------------------------- #


def test_stop_reason_default_for_clean_exhaustion() -> None:
    """When the round loop exits with no explicit break,
    the local stop reason is the initialized default
    ``max_rounds_reached``.

    This is the "configured round limit" path: the
    pipeline ran the loop, every round produced a
    non-empty ranked set, and the loop exhausted the
    budget. The init-time default is the only way this
    reason is set, so the test simply asserts the
    vocabulary constant is stable and the local default
    in :func:`run_optimizer_pipeline` matches it.
    """
    # The init-time default for the local ``stop_reason``
    # variable is the only place this constant is used
    # (the explicit break paths overwrite it). This test
    # pins the constant so a future refactor that
    # accidentally changes the default value fails loud.
    assert STOP_REASON_MAX_ROUNDS_REACHED == "max_rounds_reached", (
        f"the max_rounds_reached constant must remain "
        f"machine-stable; got {STOP_REASON_MAX_ROUNDS_REACHED!r}"
    )
    # The vocabulary is a frozenset that contains exactly
    # the contract stop reasons (six original plus the
    # ``routing_confirmation_preview`` id introduced by
    # Issue #39 / Task 2 for the non-mutating preview
    # short-circuit). The new id is the only addition; a
    # regression that drops an existing id or introduces
    # an undeclared one must still fail loud.
    assert STOP_REASONS == frozenset({
        "max_rounds_reached",
        "accepted",
        "no_candidate_edits",
        "no_candidate_selected",
        "round_blocked",
        "precondition_blocked",
        "routing_confirmation_preview",
    }), (
        f"STOP_REASONS must enumerate exactly the seven "
        f"contract stop reasons; got {sorted(STOP_REASONS)!r}"
    )

# --------------------------------------------------------------------------- #
# Loop-bound: ``max_rounds`` is honored on continuous rejection                #
# --------------------------------------------------------------------------- #

def test_max_rounds_not_exceeded_under_continuous_rejection(
    tmp_path: Path,
) -> None:
    """``run_optimizer_pipeline`` runs EXACTLY ``max_rounds``
    iterations under continuous suggestion + rejection and
    stops with ``stop_reason == max_rounds_reached``.

    Issue #36 Stopping Condition: the round loop is bounded
    by ``max_rounds``; a regression that breaks the loop
    early, runs more than ``max_rounds``, or fails to set
    the default ``stop_reason`` after the loop exhausts
    must be visible at the result level.

    The test injects:

      - a ``call_fn`` stub that returns one valid
        :data:`ROUND_REFLECTION_SCHEMA`-shaped suggestion
        every invocation. The suggestion targets the only
        mutable range with the parser-owned
        ``content_hash`` and a non-empty
        ``replacement`` so the merge step marks the plan
        ``fits_in_range = True``. ``routing = False`` so
        the per-round routing-cap / HITL gates do not
        trip.
      - an ``eval_call_fn`` stub that returns
        ``{"status": "FAIL"}`` for every case. The
        comparator sees zero ``FAIL -> PASS`` transitions
        on the candidate side and rejects every round.

    With ``max_rounds=2`` and a never-improving candidate,
    the loop runs both iterations, rolls back the
    candidate after each rejection, exits the ``for``
    loop naturally (no early ``break``), and reports
    ``stop_reason="max_rounds_reached"`` (the
    init-time default that survives because no explicit
    break path overwrote it).
    """
    import hashlib

    workspace = _init_workspace(tmp_path)
    benchmark, artifact = _seed_fixture(workspace)
    body_hash = hashlib.sha256(_BODY_TEXT.encode("utf-8")).hexdigest()

    def _suggestion_call_fn(
        *, repair_context: Any = None
    ) -> dict[str, Any]:
        """Return one valid suggestion targeting range 0.

        The replacement is a non-empty string that fits
        inside the body range so the merge plan marks
        ``fits_in_range = True``; the ``base_hash``
        matches the parser-owned ``content_hash`` so the
        suggestion survives the stale-hash dedup in step
        3c. ``routing = False`` so the per-round
        routing-cap / HITL gates do not block.
        """
        return {
            "rationale": (
                "continuous-rejection regression: one valid "
                "non-routing suggestion per round"
            ),
            "suggested_edits": [
                {
                    "range_id": 0,
                    "base_hash": body_hash,
                    "intent": "no_improvement_yet",
                    "replacement": (
                        "# body\n"
                        "The body is the only mutable range.\n"
                    ),
                    "rationale": (
                        "candidate that always fails the eval "
                        "comparator under the stub eval_call_fn"
                    ),
                    "routing": False,
                }
            ],
        }

    def _failing_eval_call_fn(
        case: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Return ``FAIL`` for every case.

        The shape mirrors :func:`_evaluate_single_case`
        enough for :func:`compare_eval_held_out` to read
        ``status``. Both baseline and candidate eval /
        held-out results report ``FAIL`` so the
        comparator sees zero ``FAIL -> PASS`` transitions
        and rejects every round.
        """
        case_id = (
            case.get("case_id") if isinstance(case, dict) else None
        )
        case_id_str = case_id if isinstance(case_id, str) else "?"
        return {
            "case_id": case_id_str,
            "evaluator": "rule_check",
            "status": "FAIL",
            "blockers": [],
            "evidence": {"stub": "continuous-rejection"},
        }

    result = run_optimizer_pipeline(
        workspace=workspace,
        benchmark_path=benchmark,
        artifact_path=artifact,
        call_fn=_suggestion_call_fn,
        max_rounds=2,
        human_confirmed=False,
        eval_call_fn=_failing_eval_call_fn,
    )

    # The loop ran exactly ``max_rounds`` iterations: not
    # 1 (would be a regression that breaks the loop on
    # the first rejection) and not 3+ (would be a
    # regression that ignores the configured budget).
    assert result.rounds == 2, (
        f"continuous-rejection run with max_rounds=2 must "
        f"run EXACTLY 2 iterations before stopping; got "
        f"result.rounds={result.rounds!r}"
    )
    # The init-time default survives because no explicit
    # break path overwrote it: every round produced a
    # non-empty ranked set (the stub returns one
    # valid suggestion), the comparator rejected
    # (stub eval_call_fn returns FAIL), and the loop
    # exhausted the budget without an early break.
    assert result.stop_reason == STOP_REASON_MAX_ROUNDS_REACHED, (
        f"continuous-rejection run that exhausts the "
        f"budget must report stop_reason="
        f"{STOP_REASON_MAX_ROUNDS_REACHED!r}; got "
        f"result.stop_reason={result.stop_reason!r}"
    )
    assert result.stop_reason in STOP_REASONS, (
        f"stop_reason must be a vocabulary string from "
        f"{sorted(STOP_REASONS)!r}; got "
        f"result.stop_reason={result.stop_reason!r}"
    )
    # The comparator rejected every round; the run
    # terminates with REJECTED status (no blockers, no
    # accepted round).
    assert result.status == "REJECTED", (
        f"continuous-rejection run must terminate with "
        f"REJECTED status (the comparator never accepted "
        f"and there are no blockers); got "
        f"result.status={result.status!r}"
    )
    # The on-disk artifact must be unchanged: every
    # rejected round rolled back the candidate write so
    # the run-level artifact equals the seed bytes.
    assert artifact.read_bytes() == _ARTIFACT_TEXT.encode("utf-8"), (
        f"continuous-rejection run must NOT mutate the "
        f"artifact on disk (per-round rollback restores "
        f"the seed bytes after each rejection); got "
        f"artifact bytes={artifact.read_bytes()!r}"
    )


# --------------------------------------------------------------------------- #
# Issue #38 — interrupted-run detection + resume gate (pure)                   #
# --------------------------------------------------------------------------- #


def test_detect_interrupted_optimizer_runs_empty_history_returns_empty() -> None:
    """An empty history stream has no interrupted runs.

    Issue #38 / ADR 0017: detection operates on an already-loaded
    iterable of history events. The trivial empty case must yield
    an empty list without raising.
    """
    from metacrucible.optimizer import detect_interrupted_optimizer_runs

    assert detect_interrupted_optimizer_runs([]) == []


def test_detect_interrupted_optimizer_runs_matching_finish_clears_start() -> None:
    """A matching ``optimize_finished`` clears its started run.

    The detection contract: an interrupted run is an
    ``optimize_started`` event with NO matching
    ``optimize_finished`` event for the same ``run_id``. The
    matched pair must drop out of the result.
    """
    from metacrucible.optimizer import detect_interrupted_optimizer_runs

    history = [
        {"event": "optimize_started", "run_id": "opt-20260616-abcdef12"},
        {"event": "optimize_finished", "run_id": "opt-20260616-abcdef12"},
    ]
    assert detect_interrupted_optimizer_runs(history) == []


def test_detect_interrupted_optimizer_runs_unfinished_run_is_returned() -> None:
    """An ``optimize_started`` with no matching finish is interrupted.

    The single interrupted ``run_id`` must appear in the result.
    """
    from metacrucible.optimizer import detect_interrupted_optimizer_runs

    history = [
        {"event": "optimize_started", "run_id": "opt-20260616-abcdef12"},
    ]
    assert detect_interrupted_optimizer_runs(history) == [
        "opt-20260616-abcdef12"
    ]


def test_detect_interrupted_optimizer_runs_multiple_unfinished_first_seen_order() -> None:
    """Multiple unfinished runs are returned in first-seen start order.

    The detection contract preserves the first-seen start order
    so downstream callers (CLI surfacing) see a deterministic
    list. Duplicate starts for the same unfinished run must
    collapse to a single entry.
    """
    from metacrucible.optimizer import detect_interrupted_optimizer_runs

    history = [
        {"event": "optimize_started", "run_id": "opt-20260616-aaaaaaaa"},
        {"event": "optimize_started", "run_id": "opt-20260616-bbbbbbbb"},
        {"event": "optimize_started", "run_id": "opt-20260616-cccccccc"},
        # Duplicate start for the first run; the matching
        # finish never arrives so it stays interrupted.
        {"event": "optimize_started", "run_id": "opt-20260616-aaaaaaaa"},
    ]
    assert detect_interrupted_optimizer_runs(history) == [
        "opt-20260616-aaaaaaaa",
        "opt-20260616-bbbbbbbb",
        "opt-20260616-cccccccc",
    ]


def test_detect_interrupted_optimizer_runs_finish_for_unrelated_run_id_does_not_clear() -> None:
    """A finish event for an unrelated ``run_id`` does NOT clear a started run.

    The detection contract is keyed on ``run_id``: a finish for
    run X must not retroactively clear a started run Y. Both
    unfinished runs appear in first-seen order.
    """
    from metacrucible.optimizer import detect_interrupted_optimizer_runs

    history = [
        {"event": "optimize_started", "run_id": "opt-20260616-aaaaaaaa"},
        # Finish for a completely different run id; must NOT
        # clear the started run above.
        {"event": "optimize_finished", "run_id": "opt-20260616-bbbbbbbb"},
        {"event": "optimize_started", "run_id": "opt-20260616-bbbbbbbb"},
    ]
    assert detect_interrupted_optimizer_runs(history) == [
        "opt-20260616-aaaaaaaa",
        "opt-20260616-bbbbbbbb",
    ]


# --------------------------------------------------------------------------- #
# validate_resume_interrupted_runs — pure gate                                #
# --------------------------------------------------------------------------- #


def test_validate_resume_interrupted_runs_empty_passes() -> None:
    """No interrupted runs → ok=True with empty blockers.

    The gate short-circuits on an empty interrupt list: there is
    nothing to confirm and no resume decision to make.
    """
    from metacrucible.optimizer import validate_resume_interrupted_runs

    result = validate_resume_interrupted_runs(
        [],
        interactive=False,
        confirmed=False,
    )
    assert result == {"ok": True, "blockers": []}, (
        f"empty interrupted_runs must pass the gate; got {result!r}"
    )


def test_validate_resume_interrupted_runs_non_interactive_without_confirm_blocks() -> None:
    """Non-interactive optimize without --confirm-resume BLOCKS.

    ADR 0017 + AC: a non-interactive caller cannot silently resume
    an interrupted run; the gate requires an explicit
    ``--confirm-resume`` flag or the run aborts. The blocker
    message must mention ``--confirm-resume`` so automation can
    branch on it without parsing free-form text.
    """
    from metacrucible.optimizer import validate_resume_interrupted_runs

    result = validate_resume_interrupted_runs(
        ["opt-20260616-abcdef12"],
        interactive=False,
        confirmed=False,
    )
    assert result.get("ok") is False, (
        f"non-interactive + no confirm + interrupted run must "
        f"BLOCK; got result={result!r}"
    )
    blockers = result.get("blockers") or []
    assert blockers, (
        f"BLOCKED result must carry at least one blocker; "
        f"got blockers={blockers!r}"
    )
    joined = " ".join(str(b.get("message", "")) for b in blockers)
    assert "--confirm-resume" in joined, (
        f"non-interactive blocker must mention --confirm-resume "
        f"so automation knows how to opt in; got blockers={blockers!r}"
    )
    # The interrupted run id must be present in the payload so
    # the BLOCKED result is actionable.
    assert any(
        "opt-20260616-abcdef12" in (str(b.get("message", "")) + str(b))
        for b in blockers
    ), (
        f"blocker payload must include the interrupted run_id "
        f"for traceability; got blockers={blockers!r}"
    )


def test_validate_resume_interrupted_runs_interactive_without_confirm_blocks() -> None:
    """Interactive optimize without confirmation BLOCKS.

    ADR 0017 + AC: an interactive caller with an interrupted
    run still must explicitly confirm resume — the gate does
    not auto-prompt; it BLOCKS so the CLI can surface the
    blocker and ask. The blocker message must reference the
    confirmation requirement.
    """
    from metacrucible.optimizer import validate_resume_interrupted_runs

    result = validate_resume_interrupted_runs(
        ["opt-20260616-abcdef12"],
        interactive=True,
        confirmed=False,
    )
    assert result.get("ok") is False, (
        f"interactive + no confirm + interrupted run must "
        f"BLOCK; got result={result!r}"
    )
    blockers = result.get("blockers") or []
    assert blockers, (
        f"BLOCKED result must carry at least one blocker; "
        f"got blockers={blockers!r}"
    )
    joined = " ".join(str(b.get("message", "")) for b in blockers).lower()
    assert "confirm" in joined, (
        f"interactive blocker must reference confirmation; "
        f"got blockers={blockers!r}"
    )


def test_validate_resume_interrupted_runs_interactive_with_confirm_passes() -> None:
    """Interactive optimize WITH confirmation PASSES.

    The positive path: the caller confirmed the resume and the
    gate returns ok=True with no blockers.
    """
    from metacrucible.optimizer import validate_resume_interrupted_runs

    result = validate_resume_interrupted_runs(
        ["opt-20260616-abcdef12"],
        interactive=True,
        confirmed=True,
    )
    assert result == {"ok": True, "blockers": []}, (
        f"interactive + confirmed must pass the gate; "
        f"got result={result!r}"
    )


def test_validate_resume_interrupted_runs_non_interactive_with_confirm_passes() -> None:
    """Non-interactive optimize WITH --confirm-resume PASSES.

    The automation path: the caller passed the explicit
    ``--confirm-resume`` flag and the gate honors it without
    requiring an interactive prompt.
    """
    from metacrucible.optimizer import validate_resume_interrupted_runs

    result = validate_resume_interrupted_runs(
        ["opt-20260616-abcdef12"],
        interactive=False,
        confirmed=True,
    )
    assert result == {"ok": True, "blockers": []}, (
        f"non-interactive + --confirm-resume must pass the gate; "
        f"got result={result!r}"
    )

def test_detect_interrupted_runs_after_synthetic_finish_is_clean() -> None:
    """A started run followed by a synthetic optimize_finished is clean.

    The CLI retirement write in cmd_optimize appends a synthetic
    ``optimize_finished`` event for every stale ``run_id`` after the
    user confirms the resume. The detector must treat the pair
    ``optimize_started`` + synthetic ``optimize_finished`` as a clean
    (non-interrupted) lineage so the next optimize call does not
    re-trigger the gate.

    Issue #38 (post-integration-fix).
    """
    from metacrucible.optimizer import detect_interrupted_optimizer_runs

    history = [
        {"event": "optimize_started", "run_id": "opt-20260616-retire12"},
        {
            "event": "optimize_finished",
            "run_id": "opt-20260616-retire12",
            "status": "SUPERSEDED",
            "stop_reason": "interrupted-run-resumed",
            "superseded_by": "confirmed-resume",
        },
    ]
    assert detect_interrupted_optimizer_runs(history) == [], (
        f"synthetic finish after the start must clear the "
        f"interrupted-run detection; got "
        f"{detect_interrupted_optimizer_runs(history)!r}"
    )

# --------------------------------------------------------------------------- #
# Issue #39 — routing revision detection + confirmation gate (pure)             #
# --------------------------------------------------------------------------- #


def _make_routing_test_suggestion(
    *,
    suggestion_id: str = "opt-routing-001",
    routing_field: str = "description",
    replacement: str = "new routing text",
    intent: str = "clarify_routing",
    rationale: str = "evidence rationale",
    range_id: int = 0,
) -> Any:
    """Build one bounded EditSuggestion with routing=True for tests."""
    from metacrucible.optimizer import EditSuggestion

    return EditSuggestion(
        record_type="edit_suggestion",
        suggestion_id=suggestion_id,
        run_id="opt-20260616-routing01",
        round_id="round-routing-1",
        timestamp="2026-01-01T00:00:00Z",
        range_id=range_id,
        base_hash="",
        intent=intent,
        replacement=replacement,
        rationale=rationale,
        routing=True,
        human_confirmed=False,
        routing_field=routing_field,
    )


def _make_routing_test_context(
    *,
    old_text: str = "old routing text",
    routing_fields: frozenset[str] = frozenset({"description"}),
) -> Any:
    """Build one minimal OptimizerContext carrying a single mutable range."""
    from metacrucible.artifact import MutableRange
    from metacrucible.optimizer import OptimizerContext

    mutable_range = MutableRange(text=old_text, range_id=0, content_hash="")
    return OptimizerContext(
        run_id="opt-20260616-routing01",
        workspace="/tmp/metacrucible-routing-test",
        benchmark_path="/tmp/metacrucible-routing-test/benchmark.jsonl",
        artifact_path="/tmp/metacrucible-routing-test/SKILL.md",
        artifact_kind="skill",
        base_content_hash="",
        mutable_ranges=(mutable_range,),
        routing_surface_fields=routing_fields,
        eligible_eval_case_ids=(),
        eligible_held_out_case_ids=(),
        benchmark_metadata={},
        max_rounds=1,
        human_confirmed=False,
    )


def test_detect_routing_revision_confirmation_no_routing_returns_empty() -> None:
    """No suggestions at all → detector returns an empty record list.

    Issue #39 AC: the detector is the pure entry point the CLI
    layer feeds the record list into the gate with; an empty
    candidate set means there is nothing to confirm.
    """
    from metacrucible.optimizer import detect_routing_revision_confirmation

    assert detect_routing_revision_confirmation([]) == [], (
        "empty suggestion list must produce zero routing revision "
        "records; the gate then short-circuits on no-routing input"
    )


def test_detect_routing_revision_confirmation_extracts_diff() -> None:
    """Detector builds one record per routing edit with old/new/field.

    The record is the minimal diff/evidence the CLI needs to
    surface the proposed routing revision. The ``old`` text comes
    from the context's mutable range whose ``range_id`` matches
    the suggestion; the ``new`` text is the suggestion's
    ``replacement``; the intent and rationale round-trip so a
    human reviewing the gate can see why the change was proposed.
    """
    from metacrucible.optimizer import detect_routing_revision_confirmation

    suggestion = _make_routing_test_suggestion()
    context = _make_routing_test_context()
    records = detect_routing_revision_confirmation([suggestion], context=context)
    assert len(records) == 1, (
        f"one routing suggestion must produce one record; got "
        f"{records!r}"
    )
    record = records[0]
    assert record["suggestion_id"] == "opt-routing-001", (
        f"record must carry the suggestion_id for traceability; "
        f"got record={record!r}"
    )
    assert record["routing_field"] == "description", (
        f"record must carry the routing_field name; got record={record!r}"
    )
    assert record["old"] == "old routing text", (
        f"record must carry the mutable range text as the 'old' "
        f"diff side; got record={record!r}"
    )
    assert record["new"] == "new routing text", (
        f"record must carry the suggestion's replacement as the "
        f"'new' diff side; got record={record!r}"
    )
    assert record["intent"] == "clarify_routing", (
        f"record must carry the suggestion's intent so a human "
        f"reviewer can see the proposed change shape; got "
        f"record={record!r}"
    )
    assert record["rationale"] == "evidence rationale", (
        f"record must carry the suggestion's rationale so a "
        f"human reviewer can see why the change was proposed; "
        f"got record={record!r}"
    )


def test_detect_routing_revision_confirmation_attaches_profile_evidence() -> None:
    """Detector carries the profile verdict (and its parts) on the record.

    The CLI surfaces the profile verdict inside the BLOCKED /
    confirmation payload so the human reviewer can see exactly
    which profile flagged the routing change as needing HITL.
    The detector must embed the verdict unchanged so downstream
    rendering is byte-stable.
    """
    from metacrucible.optimizer import detect_routing_revision_confirmation

    suggestion = _make_routing_test_suggestion()
    context = _make_routing_test_context()
    profile_verdict: dict[str, Any] = {
        "accepted": False,
        "blockers": [
            {
                "id": "routing-surface-safety.hitl-required",
                "message": "confirmation required",
            }
        ],
        "supplemental_findings": [
            {
                "id": "routing-surface-safety.routing-change",
                "message": "description changed",
            }
        ],
    }
    records = detect_routing_revision_confirmation(
        [suggestion],
        context=context,
        profile_verdict=profile_verdict,
    )
    assert len(records) == 1, (
        f"profile verdict must not change the record count; got "
        f"records={records!r}"
    )
    record = records[0]
    assert record["profile_verdict"] == profile_verdict, (
        f"profile_verdict must round-trip unchanged so the CLI "
        f"can render the evidence byte-for-byte; got "
        f"record={record!r}"
    )
    assert record["accepted"] is False, (
        f"record must surface the verdict's accepted flag so "
        f"the CLI can branch without re-parsing the verdict; "
        f"got record={record!r}"
    )
    assert record["blockers"] == profile_verdict["blockers"], (
        f"record must surface the verdict's blockers list "
        f"unchanged; got record={record!r}"
    )
    assert (
        record["supplemental_findings"]
        == profile_verdict["supplemental_findings"]
    ), (
        f"record must surface the verdict's supplemental_findings "
        f"list unchanged; got record={record!r}"
    )


def test_validate_routing_revision_confirmation_empty_passes() -> None:
    """No records → ok=True with empty blockers.

    The gate short-circuits when the detector found no routing
    revisions: there is nothing to confirm and no HITL to request.
    """
    from metacrucible.optimizer import validate_routing_revision_confirmation

    result = validate_routing_revision_confirmation(
        [], interactive=False, confirmed=False
    )
    assert result == {"ok": True, "blockers": []}, (
        f"empty record list must pass the gate; got {result!r}"
    )


def test_validate_routing_revision_confirmation_non_interactive_blocks() -> None:
    """Non-interactive optimize without --allow-routing-revision BLOCKS.

    Issue #39 AC: a non-interactive caller cannot silently apply
    a routing revision; the gate requires the explicit
    ``--allow-routing-revision`` flag or the run aborts. The
    blocker id is the stable
    :data:`ROUTING_REVISION_NON_INTERACTIVE_BLOCKER` and the
    message names the opt-in flag plus the routing field /
    suggestion id so the payload is actionable.
    """
    from metacrucible.optimizer import (
        ROUTING_REVISION_NON_INTERACTIVE_BLOCKER,
        validate_routing_revision_confirmation,
    )

    records = [
        {
            "suggestion_id": "opt-routing-001",
            "routing_field": "description",
            "old": "old routing text",
            "new": "new routing text",
            "intent": "clarify_routing",
            "rationale": "evidence rationale",
            "accepted": False,
            "blockers": [],
            "supplemental_findings": [],
        }
    ]
    result = validate_routing_revision_confirmation(
        records, interactive=False, confirmed=False
    )
    assert result.get("ok") is False, (
        f"non-interactive + no confirm + routing revision must "
        f"BLOCK; got result={result!r}"
    )
    blockers = result.get("blockers") or []
    assert blockers, (
        f"BLOCKED result must carry at least one blocker; got "
        f"blockers={blockers!r}"
    )
    blocker_ids = [b.get("id") for b in blockers]
    assert ROUTING_REVISION_NON_INTERACTIVE_BLOCKER in blocker_ids, (
        f"non-interactive blocker id must be "
        f"{ROUTING_REVISION_NON_INTERACTIVE_BLOCKER!r}; got "
        f"blockers={blockers!r}"
    )
    joined = " ".join(str(b.get("message", "")) for b in blockers)
    assert "--allow-routing-revision" in joined, (
        f"non-interactive blocker must name the "
        f"--allow-routing-revision flag so automation knows how "
        f"to opt in; got blockers={blockers!r}"
    )
    assert "description" in joined, (
        f"non-interactive blocker must name the routing_field so "
        f"the operator knows which field is gated; got "
        f"blockers={blockers!r}"
    )
    assert "opt-routing-001" in joined, (
        f"non-interactive blocker must name the suggestion_id "
        f"for traceability; got blockers={blockers!r}"
    )


def test_validate_routing_revision_confirmation_interactive_without_confirm_blocks() -> None:
    """Interactive optimize without confirmation BLOCKS.

    Issue #39 AC: an interactive caller still must explicitly
    confirm the routing revision — the gate does not auto-prompt;
    it BLOCKS so the CLI can surface the blocker and ask. The
    blocker id is the stable
    :data:`ROUTING_REVISION_CONFIRMATION_REQUIRED_BLOCKER` and
    the message names the routing field and suggestion id.
    """
    from metacrucible.optimizer import (
        ROUTING_REVISION_CONFIRMATION_REQUIRED_BLOCKER,
        validate_routing_revision_confirmation,
    )

    records = [
        {
            "suggestion_id": "opt-routing-001",
            "routing_field": "description",
            "old": "old routing text",
            "new": "new routing text",
            "intent": "clarify_routing",
            "rationale": "evidence rationale",
            "accepted": False,
            "blockers": [],
            "supplemental_findings": [],
        }
    ]
    result = validate_routing_revision_confirmation(
        records, interactive=True, confirmed=False
    )
    assert result.get("ok") is False, (
        f"interactive + no confirm + routing revision must "
        f"BLOCK; got result={result!r}"
    )
    blockers = result.get("blockers") or []
    assert blockers, (
        f"BLOCKED result must carry at least one blocker; got "
        f"blockers={blockers!r}"
    )
    blocker_ids = [b.get("id") for b in blockers]
    assert (
        ROUTING_REVISION_CONFIRMATION_REQUIRED_BLOCKER in blocker_ids
    ), (
        f"interactive blocker id must be "
        f"{ROUTING_REVISION_CONFIRMATION_REQUIRED_BLOCKER!r}; got "
        f"blockers={blockers!r}"
    )
    joined = " ".join(str(b.get("message", "")) for b in blockers)
    assert "description" in joined, (
        f"interactive blocker must name the routing_field; got "
        f"blockers={blockers!r}"
    )
    assert "opt-routing-001" in joined, (
        f"interactive blocker must name the suggestion_id; got "
        f"blockers={blockers!r}"
    )


def test_validate_routing_revision_confirmation_confirmed_passes() -> None:
    """--allow-routing-revision / interactive confirm PASSES.

    The positive path: the caller confirmed the routing revision
    via the CLI flag and the gate honors it. The returned payload
    is ``{"ok": True, "blockers": []}`` so the CLI can proceed.
    """
    from metacrucible.optimizer import validate_routing_revision_confirmation

    records = [
        {
            "suggestion_id": "opt-routing-001",
            "routing_field": "description",
            "old": "old routing text",
            "new": "new routing text",
            "intent": "clarify_routing",
            "rationale": "evidence rationale",
            "accepted": False,
            "blockers": [],
            "supplemental_findings": [],
        }
    ]
    result = validate_routing_revision_confirmation(
        records, interactive=False, confirmed=True
    )
    assert result == {"ok": True, "blockers": []}, (
        f"confirmed record must pass the gate; got result={result!r}"
    )

# --------------------------------------------------------------------------- #
# Issue #39 / Task 2 — non-mutating routing proposal preview (pipeline)        #
# --------------------------------------------------------------------------- #
#
# These tests pin the ``routing_confirmation_preview=True`` contract on
# :func:`metacrucible.optimizer.run_optimizer_pipeline`:
#
#   * When preview mode is on AND the round produced at least one
#     unconfirmed routing suggestion, the pipeline returns a
#     ``PREVIEW`` result with a structured ``preview`` payload and
#     does NOT mutate the artifact on disk. The legacy
#     ``routing-hitl-unconfirmed`` rejection id is intentionally
#     absent from the run-level ``blockers`` list: the profile
#     verdict surfaces the HITL gate inside the preview payload,
#     not as a run-level abort.
#   * When preview mode is on AND every routing suggestion is
#     confirmed (or there are no routing suggestions at all), the
#     pipeline runs to completion: a confirmed-only set proceeds
#     through conflict checks / merge / apply / evaluate exactly
#     as it would without the preview knob.
#   * When preview mode is off (the default), the existing
#     rejection semantics are preserved byte-for-byte: an
#     unconfirmed routing edit is rejected with the
#     ``routing-hitl-unconfirmed`` reason id and the pipeline
#     exits with ``REJECTED`` (no candidate) status.
#
# The tests follow the OPT-9 fixture pattern from
# :mod:`tests.test_optimize_command`: a tiny Skill with a single
# mutable body range, a benchmark with one eligible eval + one
# eligible held-out case, and an envelope that declares the
# artifact path.


def _unconfirmed_routing_call_fn(
    *, body_hash: str, body_text: str
) -> Any:
    """Build a ``call_fn`` that returns one unconfirmed routing edit.

    The suggestion targets ``range_id=0`` (the body) and carries
    ``routing=True`` with ``routing_field="description"`` (a
    Skill routing surface). ``human_confirmed=False`` so the
    HITL gate in step 3d would normally reject the edit; the
    preview tests rely on this default rejection to assert the
    preview short-circuit fires.
    """
    def _call_fn(*, repair_context: Any = None) -> dict[str, Any]:
        return {
            "rationale": (
                "preview-mode regression: one unconfirmed "
                "routing edit per round"
            ),
            "suggested_edits": [
                {
                    "range_id": 0,
                    "base_hash": body_hash,
                    "intent": "preview_only_routing",
                    "replacement": body_text,
                    "rationale": (
                        "preview-only routing edit on description; "
                        "human_confirmed=False so the HITL gate would "
                        "normally reject; preview mode must collect "
                        "this into pending_routing instead"
                    ),
                    "routing": True,
                    "routing_field": "description",
                }
            ],
        }
    return _call_fn


def _confirmed_routing_call_fn(
    *, body_hash: str, body_text: str
) -> Any:
    """Build a ``call_fn`` that returns one *confirmed* routing edit.

    The suggestion is byte-identical to the unconfirmed variant
    except ``human_confirmed=True`` on the suggestion, so the
    preview short-circuit must NOT fire (there is no pending
    routing edit to confirm) and the pipeline proceeds normally.
    """
    def _call_fn(*, repair_context: Any = None) -> dict[str, Any]:
        return {
            "rationale": (
                "preview-mode regression: one confirmed "
                "routing edit per round"
            ),
            "suggested_edits": [
                {
                    "range_id": 0,
                    "base_hash": body_hash,
                    "intent": "preview_only_routing_confirmed",
                    "replacement": body_text,
                    "rationale": (
                        "preview-only routing edit on description; "
                        "human_confirmed=True so the preview "
                        "short-circuit must NOT fire"
                    ),
                    "routing": True,
                    "routing_field": "description",
                    "human_confirmed": True,
                }
            ],
        }
    return _call_fn


def test_routing_preview_does_not_mutate_artifact(
    tmp_path: Path,
) -> None:
    """Preview mode must not mutate the artifact on disk.

    RED (Task 2 AC1): drive an unconfirmed routing
    suggestion through ``run_optimizer_pipeline`` with
    ``routing_confirmation_preview=True`` and assert the
    artifact file content is byte-for-byte unchanged after
    the run. The legacy ``routing-hitl-unconfirmed``
    rejection path would have preserved the bytes too,
    so this test pins the preview short-circuit
    specifically: the pipeline must EXIT before
    ``apply_patch_revision`` would have written the
    candidate text. A regression that lets the apply
    step run in preview mode would corrupt the artifact.
    """
    import hashlib

    from metacrucible.optimizer import (
        STOP_REASON_ROUTING_CONFIRMATION_PREVIEW,
        run_optimizer_pipeline,
    )

    workspace = _init_workspace(tmp_path)
    benchmark, artifact = _seed_fixture(workspace)
    body_hash = hashlib.sha256(
        _BODY_TEXT.encode("utf-8")
    ).hexdigest()
    before_bytes = artifact.read_bytes()
    call_fn = _unconfirmed_routing_call_fn(
        body_hash=body_hash, body_text=_BODY_TEXT
    )

    result = run_optimizer_pipeline(
        workspace=workspace,
        benchmark_path=benchmark,
        artifact_path=artifact,
        call_fn=call_fn,
        max_rounds=1,
        human_confirmed=False,
        routing_confirmation_preview=True,
    )

    # The on-disk artifact must be byte-for-byte unchanged.
    # A regression that runs ``apply_patch_revision`` in
    # preview mode would write the candidate body (which is
    # the same as the seed body in this fixture) and the
    # assert below would still pass — so we also assert the
    # candidate write was NEVER scheduled, via the result's
    # status / stop_reason / preview payload below.
    after_bytes = artifact.read_bytes()
    assert after_bytes == before_bytes, (
        f"preview mode must NOT mutate the artifact on "
        f"disk; before={before_bytes!r} after={after_bytes!r}"
    )
    # The result is a structured PREVIEW, not the legacy
    # REJECTED / BLOCKED path. The machine-stable stop
    # reason names the preview short-circuit so a
    # downstream tool can branch on it.
    assert result.status == "PREVIEW", (
        f"preview mode must return status=PREVIEW when the "
        f"round produced an unconfirmed routing edit; got "
        f"result.status={result.status!r}"
    )
    assert (
        result.stop_reason
        == STOP_REASON_ROUTING_CONFIRMATION_PREVIEW
    ), (
        f"preview mode must set "
        f"stop_reason={STOP_REASON_ROUTING_CONFIRMATION_PREVIEW!r}; "
        f"got result.stop_reason={result.stop_reason!r}"
    )
    # The preview payload is non-None so the CLI can
    # render it; the CLI cutover (Task 3) reads the
    # ``routing_confirmation`` list and the profile
    # verdict from this mapping.
    assert result.preview is not None, (
        f"preview mode must populate result.preview; got "
        f"result.preview={result.preview!r}"
    )


def test_routing_preview_exposes_machine_readable_confirmation_records(
    tmp_path: Path,
) -> None:
    """Preview mode surfaces a routing confirmation list with the
    field set the CLI renders.

    RED (Task 2 AC2): the preview payload's
    ``routing_confirmation`` list is the per-suggestion diff
    the CLI shows to the operator. Every record must carry
    ``suggestion_id``, ``routing_field``, ``old``, ``new``,
    ``intent``, ``rationale``, and the routing-surface-safety
    profile verdict evidence (``accepted``, ``blockers``,
    ``supplemental_findings``). A regression that drops a
    field, mis-keys it, or omits the profile verdict breaks
    the CLI cutover; the test pins the contract.
    """
    import hashlib

    from metacrucible.optimizer import run_optimizer_pipeline

    workspace = _init_workspace(tmp_path)
    benchmark, artifact = _seed_fixture(workspace)
    body_hash = hashlib.sha256(
        _BODY_TEXT.encode("utf-8")
    ).hexdigest()
    call_fn = _unconfirmed_routing_call_fn(
        body_hash=body_hash, body_text=_BODY_TEXT
    )

    result = run_optimizer_pipeline(
        workspace=workspace,
        benchmark_path=benchmark,
        artifact_path=artifact,
        call_fn=call_fn,
        max_rounds=1,
        human_confirmed=False,
        routing_confirmation_preview=True,
    )

    # The preview payload is a mapping with the contract
    # shape the CLI reads. The exact keys are:
    #   routing_confirmation, profile_verdict,
    #   routing_changes, selected_routing_suggestions,
    #   round_id.
    assert isinstance(result.preview, dict), (
        f"result.preview must be a mapping; got "
        f"type={type(result.preview).__name__}"
    )
    records = result.preview.get("routing_confirmation")
    assert isinstance(records, list), (
        f"preview['routing_confirmation'] must be a list; "
        f"got type={type(records).__name__}"
    )
    assert len(records) == 1, (
        f"the unconfirmed-routing fixture must produce "
        f"exactly one routing confirmation record; got "
        f"{len(records)} records in {records!r}"
    )
    record = records[0]
    # The CLI renders each of these fields; a missing or
    # mis-named field is a CLI cutover regression.
    expected_fields = (
        "suggestion_id",
        "routing_field",
        "old",
        "new",
        "intent",
        "rationale",
        "accepted",
        "blockers",
        "supplemental_findings",
    )
    for field_name in expected_fields:
        assert field_name in record, (
            f"routing confirmation record must carry "
            f"field={field_name!r} so the CLI can render "
            f"the diff + evidence; got record={record!r}"
        )
    assert record["suggestion_id"] == "round-01-sug-00", (
        f"record must carry the suggestion_id for "
        f"traceability; got record={record!r}"
    )
    assert record["routing_field"] == "description", (
        f"record must carry the routing_field name; got "
        f"record={record!r}"
    )
    assert record["old"] == _BODY_TEXT, (
        f"record['old'] must carry the mutable range text "
        f"so the CLI can render the diff; got "
        f"record={record!r}"
    )
    assert record["new"] == _BODY_TEXT, (
        f"record['new'] must carry the suggestion's "
        f"replacement text; got record={record!r}"
    )
    assert record["intent"] == "preview_only_routing", (
        f"record['intent'] must carry the suggestion's "
        f"intent; got record={record!r}"
    )
    assert record["rationale"] and isinstance(
        record["rationale"], str
    ), (
        f"record['rationale'] must carry the suggestion's "
        f"rationale text; got record={record!r}"
    )
    # The profile verdict fields are present and reflect
    # the routing-surface-safety verdict with the HITL
    # gate fired (human_confirmed=False in the preview).
    assert record["accepted"] is False, (
        f"record['accepted'] must surface the profile "
        f"verdict's accepted flag; a False means the "
        f"routing-surface-safety profile fired its HITL "
        f"gate; got record={record!r}"
    )
    assert isinstance(record["blockers"], list) and record["blockers"], (
        f"record['blockers'] must carry at least one "
        f"routing-surface-safety blocker; got "
        f"record={record!r}"
    )
    hitl_blocker_ids = [
        b.get("id") for b in record["blockers"]
        if isinstance(b, dict) and isinstance(b.get("id"), str)
    ]
    assert any("hitl" in bid for bid in hitl_blocker_ids), (
        f"record['blockers'] must include the "
        f"routing-surface-safety HITL blocker; got "
        f"blocker ids={hitl_blocker_ids!r}"
    )
    # The top-level profile_verdict is also present so
    # the CLI can render the aggregate verdict without
    # re-walking each per-suggestion record.
    profile_verdict = result.preview.get("profile_verdict")
    assert isinstance(profile_verdict, dict), (
        f"preview['profile_verdict'] must be a mapping; "
        f"got type={type(profile_verdict).__name__}"
    )
    assert profile_verdict.get("accepted") is False, (
        f"preview['profile_verdict']['accepted'] must be "
        f"False for an unconfirmed preview; got "
        f"profile_verdict={profile_verdict!r}"
    )
    assert profile_verdict.get("blockers"), (
        f"preview['profile_verdict']['blockers'] must be "
        f"non-empty for an unconfirmed preview; got "
        f"profile_verdict={profile_verdict!r}"
    )
    # The raw routing_changes carry the per-field
    # {field, old, new} triple the CLI may show in a
    # diff view.
    routing_changes = result.preview.get("routing_changes")
    assert isinstance(routing_changes, list) and len(routing_changes) == 1, (
        f"preview['routing_changes'] must carry exactly "
        f"one change for the single unconfirmed routing "
        f"suggestion; got routing_changes={routing_changes!r}"
    )
    change = routing_changes[0]
    assert change.get("field") == "description", (
        f"routing_changes entry must carry the "
        f"routing_field name; got change={change!r}"
    )
    assert "old" in change and "new" in change, (
        f"routing_changes entry must carry 'old' and "
        f"'new' text; got change={change!r}"
    )


def test_routing_preview_does_not_return_routing_hitl_unconfirmed_blocker(
    tmp_path: Path,
) -> None:
    """Preview mode does not surface the legacy
    ``routing-hitl-unconfirmed`` blocker before the CLI
    has had a chance to confirm.

    RED (Task 2 AC3): the legacy rejection path
    (``routing-hitl-unconfirmed``) is the run-level
    abort signal a downstream BLOCKED payload would
    carry. Preview mode replaces that abort with a
    structured PREVIEW payload: the HITL gate is
    still observed, but the *signal* the CLI
    receives is ``status="PREVIEW"`` with the
    confirmation records on ``result.preview``, not
    a ``blockers`` list. A regression that
    double-counts the HITL gate (preview status
    AND the legacy blocker id) would let a CLI
    consumer branch on the wrong signal and might
    trip a BLOCKED abort before the operator has
    seen the diff.
    """
    import hashlib

    from metacrucible.optimizer import (
        ROUTING_HITL_UNCONFIRMED_BLOCKER,
        run_optimizer_pipeline,
    )

    workspace = _init_workspace(tmp_path)
    benchmark, artifact = _seed_fixture(workspace)
    body_hash = hashlib.sha256(
        _BODY_TEXT.encode("utf-8")
    ).hexdigest()
    call_fn = _unconfirmed_routing_call_fn(
        body_hash=body_hash, body_text=_BODY_TEXT
    )

    result = run_optimizer_pipeline(
        workspace=workspace,
        benchmark_path=benchmark,
        artifact_path=artifact,
        call_fn=call_fn,
        max_rounds=1,
        human_confirmed=False,
        routing_confirmation_preview=True,
    )

    # The run-level blockers list is empty in preview
    # mode: the HITL gate is observed but the abort
    # signal is the PREVIEW status, not a blocker id.
    run_blocker_ids = [
        b.get("id") for b in (result.blockers or [])
        if isinstance(b, dict)
    ]
    assert (
        ROUTING_HITL_UNCONFIRMED_BLOCKER not in run_blocker_ids
    ), (
        f"preview mode must NOT surface the legacy "
        f"{ROUTING_HITL_UNCONFIRMED_BLOCKER!r} blocker "
        f"as a run-level blocker; got run_blocker_ids="
        f"{run_blocker_ids!r}"
    )
    # The status is the new PREVIEW value, not BLOCKED.
    # A regression that flips the status to BLOCKED
    # would re-introduce the silent-abort behavior the
    # preview mode is meant to replace.
    assert result.status != "BLOCKED", (
        f"preview mode must not return status=BLOCKED; "
        f"the CLI cutover relies on a structured PREVIEW "
        f"payload, not a run-level abort; got "
        f"result.status={result.status!r}"
    )
    # The history lineage carries an optimize_preview
    # event (the per-round preview request) and an
    # optimize_finished event with status=PREVIEW. A
    # regression that omits the optimize_preview event
    # would break the audit trace.
    finished_events = [
        r for r in _read_history(workspace)
        if isinstance(r, dict) and r.get("event") == "optimize_finished"
    ]
    assert finished_events, (
        f"preview mode must persist an optimize_finished "
        f"event to close the audit lineage; got "
        f"events={[r.get('event') for r in _read_history(workspace)]!r}"
    )
    last_finished = finished_events[-1]
    assert last_finished.get("status") == "PREVIEW", (
        f"optimize_finished event must carry status=PREVIEW "
        f"in preview mode; got "
        f"status={last_finished.get('status')!r}"
    )
    preview_events = [
        r for r in _read_history(workspace)
        if isinstance(r, dict) and r.get("event") == "optimize_preview"
    ]
    assert preview_events, (
        f"preview mode must persist an optimize_preview "
        f"event so the audit lineage carries the "
        f"machine-readable trace of the preview request; "
        f"got events="
        f"{[r.get('event') for r in _read_history(workspace)]!r}"
    )


def test_routing_preview_preserves_normal_pipeline_when_no_pending_routing(
    tmp_path: Path,
) -> None:
    """Preview mode is a no-op when every routing edit is
    confirmed (or there are no routing edits at all).

    The preview knob is a *guard*, not a transform: if
    the round has nothing to confirm, the pipeline must
    run to completion exactly as it would without the
    knob. The test pins that contract by injecting a
    confirmed-only routing edit and asserting the result
    falls through the preview short-circuit.
    """
    import hashlib

    from metacrucible.optimizer import (
        STOP_REASON_ROUTING_CONFIRMATION_PREVIEW,
        run_optimizer_pipeline,
    )

    workspace = _init_workspace(tmp_path)
    benchmark, artifact = _seed_fixture(workspace)
    body_hash = hashlib.sha256(
        _BODY_TEXT.encode("utf-8")
    ).hexdigest()
    call_fn = _confirmed_routing_call_fn(
        body_hash=body_hash, body_text=_BODY_TEXT
    )

    result = run_optimizer_pipeline(
        workspace=workspace,
        benchmark_path=benchmark,
        artifact_path=artifact,
        call_fn=call_fn,
        max_rounds=1,
        human_confirmed=True,
        routing_confirmation_preview=True,
    )

    # A confirmed-only round must NOT short-circuit; the
    # pipeline runs through conflict / merge / apply /
    # evaluate and exits with a normal verdict. The
    # specific outcome (ACCEPTED / REJECTED) depends on
    # the comparator, but the preview-only status and
    # stop reason must NOT appear.
    assert result.status != "PREVIEW", (
        f"preview mode with no pending routing edits "
        f"must NOT return status=PREVIEW; got "
        f"result.status={result.status!r}"
    )
    assert (
        result.stop_reason
        != STOP_REASON_ROUTING_CONFIRMATION_PREVIEW
    ), (
        f"preview mode with no pending routing edits "
        f"must NOT set "
        f"stop_reason={STOP_REASON_ROUTING_CONFIRMATION_PREVIEW!r}; "
        f"got result.stop_reason={result.stop_reason!r}"
    )
    assert result.preview is None, (
        f"preview mode with no pending routing edits "
        f"must leave result.preview=None; got "
        f"result.preview={result.preview!r}"
    )


def test_routing_preview_preserves_normal_rejection_when_off(
    tmp_path: Path,
) -> None:
    """Preview mode off (the default) preserves the
    existing rejection semantics for an unconfirmed
    routing edit.

    A regression that flips the default of
    ``routing_confirmation_preview`` to ``True`` would
    silently change the CLI behavior; the test pins the
    default by omitting the parameter and asserting the
    legacy rejection path runs. The ranked_edit_set
    record carries a ``rejected`` entry with
    ``reason_id=routing-hitl-unconfirmed`` (the same
    contract the existing
    ``test_optimize_routing_hitl_unconfirmed_blocks_routing_edit``
    test pins, restated here so the preview knob's
    default is regression-tested in this module).
    """
    import hashlib

    from metacrucible.optimizer import (
        ROUTING_HITL_UNCONFIRMED_BLOCKER,
        run_optimizer_pipeline,
    )

    workspace = _init_workspace(tmp_path)
    benchmark, artifact = _seed_fixture(workspace)
    body_hash = hashlib.sha256(
        _BODY_TEXT.encode("utf-8")
    ).hexdigest()
    call_fn = _unconfirmed_routing_call_fn(
        body_hash=body_hash, body_text=_BODY_TEXT
    )

    result = run_optimizer_pipeline(
        workspace=workspace,
        benchmark_path=benchmark,
        artifact_path=artifact,
        call_fn=call_fn,
        max_rounds=1,
        human_confirmed=False,
        # routing_confirmation_preview omitted on purpose:
        # the default must preserve the legacy rejection
        # path so callers that do not know about the new
        # knob see no behavior change.
    )

    # The legacy rejection path runs. The status is
    # either REJECTED (no_candidate_selected; the only
    # suggestion was rejected by the HITL gate) or
    # BLOCKED (a downstream conflict), but it must NOT
    # be PREVIEW.
    assert result.status != "PREVIEW", (
        f"preview mode OFF (the default) must NOT return "
        f"status=PREVIEW; got result.status={result.status!r}"
    )
    assert result.preview is None, (
        f"preview mode OFF (the default) must leave "
        f"result.preview=None; got result.preview={result.preview!r}"
    )
    # The ranked_edit_set record carries the canonical
    # HITL rejection reason id. The CLI's existing
    # BLOCKED-payload path branches on this id; the
    # preview knob must not change the lineage.
    ranked_records = [
        r for r in _read_history(workspace)
        if isinstance(r, dict)
        and r.get("record_type") == "ranked_edit_set"
    ]
    assert ranked_records, (
        f"pipeline must persist a ranked_edit_set "
        f"record even when the routing edit is rejected; "
        f"got {len(ranked_records)} records in history"
    )
    last_ranked = ranked_records[-1]
    rejected = last_ranked.get("rejected") or []
    hitl_rejections = [
        r for r in rejected
        if isinstance(r, dict)
        and r.get("reason_id") == ROUTING_HITL_UNCONFIRMED_BLOCKER
    ]
    assert hitl_rejections, (
        f"ranked_edit_set.rejected must contain an entry "
        f"with reason_id={ROUTING_HITL_UNCONFIRMED_BLOCKER!r} "
        f"when preview mode is OFF; got rejected={rejected!r}"
    )

