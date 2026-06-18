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

DECISION_EVENTS = {"optimize_accepted", "optimize_rejected"}
REVISION_HISTORY_EVENTS = {"optimize_started", "optimize_accepted", "optimize_rejected"}


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
    history_path = workspace / "history.jsonl"
    if not history_path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line in history_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if isinstance(record, dict):
            records.append(record)
    return records


def _decision(record: Mapping[str, Any]) -> Mapping[str, Any]:
    decision = record.get("decision")
    return decision if isinstance(decision, Mapping) else {}


def _project_revision_event(record: Mapping[str, Any]) -> dict[str, Any] | None:
    event = record.get("event")
    if event not in REVISION_HISTORY_EVENTS:
        return None
    decision = _decision(record)
    return {
        "event": event,
        "run_id": record.get("run_id"),
        "round_id": record.get("round_id") or decision.get("round_id"),
        "revision_id": decision.get("revision_id"),
        "status": decision.get("status") or ("STARTED" if event == "optimize_started" else None),
        "accepted_at": decision.get("accepted_at"),
        "eval_score": decision.get("eval_score"),
        "held_out_delta": decision.get("held_out_delta"),
        "timestamp": record.get("timestamp"),
    }


def _project_acceptance_decision(record: Mapping[str, Any]) -> dict[str, Any] | None:
    event = record.get("event")
    if event not in DECISION_EVENTS:
        return None
    decision = _decision(record)
    if not decision:
        return None
    return {
        "event": event,
        "run_id": record.get("run_id"),
        "round_id": record.get("round_id") or decision.get("round_id"),
        "revision_id": decision.get("revision_id"),
        "status": decision.get("status"),
        "accepted": decision.get("accepted"),
        "accepted_at": decision.get("accepted_at"),
        "eval_score": decision.get("eval_score"),
        "held_out_delta": decision.get("held_out_delta"),
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


def _resolve_current_best_revision_id(
    state_current_best_revision: Any,
    decisions: list[dict[str, Any]],
) -> str | None:
    if isinstance(state_current_best_revision, str) and state_current_best_revision:
        return state_current_best_revision
    best_revision_id: str | None = None
    for decision in decisions:
        accepted = (
            decision.get("accepted") is True
            or decision.get("event") == "optimize_accepted"
        )
        revision_id = decision.get("revision_id")
        if accepted and isinstance(revision_id, str) and revision_id:
            best_revision_id = revision_id
    return best_revision_id


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
        "current_best_revision_id": _resolve_current_best_revision_id(
            state.get("current_best_revision"),
            decisions,
        ),
        "revision_history": revisions,
        "acceptance_decisions": decisions,
        "evidence_bundles": evidence_bundles,
    }
    return payload
