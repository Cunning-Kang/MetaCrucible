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
    return {
        "artifact_path": str(artifact),
        "workspace_path": str(workspace),
        "envelope_status": envelope.get("status"),
        "current_best_revision_id": state.get("current_best_revision"),
        "revision_history": revisions,
        "acceptance_decisions": decisions,
        "evidence_bundles": [],
    }