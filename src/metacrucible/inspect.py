"""Read prior ``metacrucible`` optimization state for an artifact.

Issue #42 / PRD F5 reader. The module is **read-only**: it consumes
``.metacrucible/state.json``, ``.metacrucible/envelope.json``, and the
optimizer's append-only ``.metacrucible/history.jsonl`` of event
records, then projects the real schema into the seven-key contract
exposed by :data:`INSPECT_OUTPUT_KEYS`.

Real optimizer schema (ADR 0032 / pipeline contract):

  - ``state.json`` keys: ``current_best_revision`` (always ``None``
    after ``init``; the pipeline never updates it on accept),
    ``last_run_id`` (the most recent run id, or ``None``).
  - ``history.jsonl`` event records: ``optimize_started``,
    ``optimize_accepted``, ``optimize_rejected``,
    ``optimize_blocked``, ``optimize_finished``,
    ``optimize_preview``. The comparator decision dict is the
    real shape produced by
    :func:`metacrucible.optimizer.compare_eval_held_out` —
    ``{accepted, reason, baseline_eval_fail_blocked_count,
    candidate_eval_fail_blocked_count,
    new_held_out_fail_blocked_case_ids,
    held_out_pass_to_fail_case_ids,
    eval_fail_to_pass_case_ids,
    eval_pass_to_fail_case_ids}``. **No** ``revision_id``,
    ``eval_score``, ``accepted_at``, or ``held_out_delta`` keys are
    written by the pipeline — the reader must surface those as
    ``None`` or derive them from the record envelope.

Because the pipeline never updates ``state.current_best_revision``
after init, ``current_best_revision_id`` is always reported as
``None``. Consumers reconstruct the live "best" candidate from
``acceptance_decisions`` + ``evidence_bundles`` (the audit lineage
carries the machine-stable truth).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .storage import REPO_DIR_NAME

INSPECT_OUTPUT_KEYS = (
    "artifact_path",
    "workspace_path",
    "envelope_status",
    "current_best_revision_id",
    "revision_history",
    "acceptance_decisions",
    "evidence_bundles",
)

# Revision-history surfaces every optimizer event the audit lineage
# can emit. ``optimize_preview`` is the routing-confirmation short-
# circuit (Issue #39 / Task 2); it is intentionally excluded because
# preview never produces a candidate revision — the operator
# confirms before the pipeline runs the round.
REVISION_HISTORY_EVENTS = (
    "optimize_started",
    "optimize_accepted",
    "optimize_rejected",
    "optimize_blocked",
    "optimize_finished",
)

ACCEPTANCE_DECISION_EVENTS = {"optimize_accepted", "optimize_rejected"}

EVENT_TO_STATUS: dict[str, str] = {
    "optimize_started": "STARTED",
    "optimize_accepted": "ACCEPTED",
    "optimize_rejected": "REJECTED",
    "optimize_blocked": "BLOCKED",
    "optimize_finished": "FINISHED",
}

# Raw decision fields the comparator writes. Listed once so the
# projection stays aligned with the optimizer contract — adding a
# new field here is the only edit needed to surface it.
DECISION_PASSTHROUGH_KEYS = (
    "baseline_eval_fail_blocked_count",
    "candidate_eval_fail_blocked_count",
    "new_held_out_fail_blocked_case_ids",
    "held_out_pass_to_fail_case_ids",
    "eval_fail_to_pass_case_ids",
    "eval_pass_to_fail_case_ids",
)


def resolve_inspect_paths(artifact: Path) -> tuple[Path, Path]:
    artifact = artifact.resolve()
    if not artifact.is_file():
        raise FileNotFoundError(
            f"inspect path {artifact} does not exist or is not a regular file"
        )
    workspace = artifact.parent / REPO_DIR_NAME
    if not workspace.is_dir():
        raise FileNotFoundError(f"missing .metacrucible workspace at {workspace}")
    return artifact, workspace


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"missing {path.name} at {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return data


def _load_history(workspace: Path) -> list[dict[str, Any]]:
    """Read the append-only optimizer history, line by line.

    A single malformed line (invalid JSON, wrong shape) must not
    crash the entire inspect diagnostic. The reader is a
    non-destructive validator; per-line isolation mirrors the
    per-receipt isolation in :func:`_load_evidence_bundles` so a
    corrupt append never blanks the operator's view of the rest
    of the audit lineage.
    """
    history_path = workspace / "history.jsonl"
    if not history_path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line in history_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            # Skip the malformed line; continue indexing the
            # remaining records so a corrupt append doesn't take
            # the whole diagnostic down with it.
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _decision(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the nested comparator decision dict, or ``{}`` when absent."""
    decision = record.get("decision")
    return decision if isinstance(decision, Mapping) else {}


def _decision_passthrough(decision: Mapping[str, Any]) -> dict[str, Any]:
    """Map every real comparator field to its projected entry.

    Missing keys become ``None`` (not absent) so callers can rely
    on the full schema for every revision-history / acceptance-
    decisions row regardless of event type.
    """
    return {key: decision.get(key) for key in DECISION_PASSTHROUGH_KEYS}


def _derived_revision_id(record: Mapping[str, Any]) -> str | None:
    """Synthesize a stable handle from ``run_id`` / ``round_id``.

    The real optimizer never writes ``revision_id`` into the
    decision dict (ADR 0032). The nearest machine-stable
    identifier is the (run_id, round_id) pair on the enclosing
    event record. Return ``f"{run_id}/{round_id}"`` when both
    fields are present, ``run_id`` alone when only run_id is
    present (e.g. run-level ``optimize_blocked`` /
    ``optimize_finished``), and ``None`` when neither is
    available (e.g. ``optimize_started``).
    """
    run_id = record.get("run_id")
    round_id = record.get("round_id")
    if (
        isinstance(run_id, str)
        and run_id
        and isinstance(round_id, str)
        and round_id
    ):
        return f"{run_id}/{round_id}"
    if isinstance(run_id, str) and run_id:
        return run_id
    return None


def _project_revision_event(record: Mapping[str, Any]) -> dict[str, Any] | None:
    event = record.get("event")
    if event not in REVISION_HISTORY_EVENTS:
        return None
    decision = _decision(record)
    # accepted_at is the event timestamp for accept/reject events
    # (the optimizer never writes an explicit accept timestamp);
    # every other event type has no accepted_at.
    accepted_at = (
        record.get("timestamp")
        if event in ACCEPTANCE_DECISION_EVENTS
        else None
    )
    return {
        "event": event,
        "run_id": record.get("run_id"),
        "round_id": record.get("round_id"),
        "revision_id": _derived_revision_id(record),
        "status": EVENT_TO_STATUS.get(event),
        "accepted_at": accepted_at,
        # The real optimizer does not write ``eval_score`` or
        # ``held_out_delta`` into either the event record or the
        # nested decision dict. Surface them as ``None`` so the
        # contract keys stay stable across every event type.
        "eval_score": None,
        "held_out_delta": None,
        "reason": decision.get("reason"),
        **_decision_passthrough(decision),
        "timestamp": record.get("timestamp"),
    }


def _project_acceptance_decision(record: Mapping[str, Any]) -> dict[str, Any] | None:
    event = record.get("event")
    if event not in ACCEPTANCE_DECISION_EVENTS:
        return None
    decision = _decision(record)
    return {
        "event": event,
        "run_id": record.get("run_id"),
        "round_id": record.get("round_id"),
        "revision_id": _derived_revision_id(record),
        "status": EVENT_TO_STATUS.get(event),
        "accepted": decision.get("accepted"),
        "accepted_at": record.get("timestamp"),
        "reason": decision.get("reason"),
        **_decision_passthrough(decision),
        "timestamp": record.get("timestamp"),
    }


def _load_evidence_bundles() -> list[dict[str, Any]]:
    evidence_root = Path.home() / ".metacrucible" / "evidence"
    if not evidence_root.is_dir():
        return []
    bundles: list[dict[str, Any]] = []
    for receipt_path in sorted(evidence_root.glob("*/receipt.json")):
        try:
            receipt = _load_json(receipt_path)
            summary_ref = receipt.get("summary_ref", "summary.json")
            run_id = str(receipt.get("run_id") or receipt_path.parent.name)
        except (json.JSONDecodeError, OSError, ValueError):
            # A single malformed or unreadable receipt must not
            # crash the entire inspect diagnostic. Skip it and
            # continue indexing the remaining receipts so the
            # operator still sees the evidence_bundles that are
            # actually readable under $HOME.
            continue
        bundles.append(
            {
                "run_id": run_id,
                "receipt_path": str(receipt_path),
                "summary_path": str(
                    receipt_path.parent / str(summary_ref)
                ),
                "run_type": receipt.get("run_type"),
                "status": receipt.get("status"),
                "summary_ref": summary_ref,
                "trajectory_digest_ref": receipt.get(
                    "trajectory_digest_ref", "trajectory-digest.json"
                ),
            }
        )
    return bundles


def build_inspect_payload(target: Path) -> dict[str, Any]:
    artifact, workspace = resolve_inspect_paths(target)
    state = _load_json(workspace / "state.json")
    envelope_path = workspace / "envelope.json"
    envelope = _load_json(envelope_path) if envelope_path.is_file() else {}
    history = _load_history(workspace)
    revisions = [
        projected
        for record in history
        if (projected := _project_revision_event(record)) is not None
    ]
    decisions = [
        projected
        for record in history
        if (projected := _project_acceptance_decision(record)) is not None
    ]
    evidence_bundles = _load_evidence_bundles()
    payload = {
        "artifact_path": str(artifact),
        "workspace_path": str(workspace),
        "envelope_status": envelope.get("status"),
        # The optimizer pipeline writes ``state.current_best_revision``
        # once at ``init`` and never updates it after a successful
        # round (ADR 0032). Treating it as a live pointer to the
        # current best candidate would surface a stale or
        # fabricated identifier; the audit lineage
        # (``acceptance_decisions`` + ``evidence_bundles``) is the
        # source of truth for "what was the latest accepted round".
        # ``state`` is still loaded so a missing / malformed
        # ``state.json`` raises the same precondition failure as
        # before — see ``test_inspect_missing_state_*``.
        "current_best_revision_id": None,
        "revision_history": revisions,
        "acceptance_decisions": decisions,
        "evidence_bundles": evidence_bundles,
    }
    return payload