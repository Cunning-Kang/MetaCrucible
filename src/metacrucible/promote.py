"""Generated-case promotion workflow (Issue #8)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .benchmark import (
    SPLIT_EVAL,
    SPLIT_HELD_OUT,
    STATUS_GENERATED,
    STATUS_REVIEWED,
    _read_jsonl_records,
)
from .storage import RepositoryStorage


def _atomic_write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    """Rewrite JSONL records atomically while preserving record order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        lines = [json.dumps(record, sort_keys=True) for record in records]
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def promote_case(
    path: str | Path,
    *,
    case_id: str,
    split: str,
    reviewed_by: str,
    review_note: str = "",
    reviewed_at: str,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Plan promotion for one generated benchmark case."""
    benchmark_path = Path(path)
    records = _read_jsonl_records(benchmark_path)
    blockers: list[dict[str, Any]] = []
    if not reviewed_by.strip():
        blockers.append(
            {
                "id": "promote-empty-reviewed-by",
                "message": "reviewed_by must be non-empty",
            }
        )
    if split not in {SPLIT_EVAL, SPLIT_HELD_OUT}:
        blockers.append(
            {
                "id": "promote-invalid-split",
                "message": f"split must be {SPLIT_EVAL!r} or {SPLIT_HELD_OUT!r}",
            }
        )
    target = None
    for record in records[1:]:
        if record.get("case_id") == case_id:
            target = record
            break
    if target is None:
        blockers.append(
            {
                "id": "promote-case-not-found",
                "message": f"case_id {case_id!r} was not found",
            }
        )
    elif target.get("status") != STATUS_GENERATED:
        blockers.append(
            {
                "id": "promote-case-not-generated",
                "message": f"case_id {case_id!r} is not a generated case",
            }
        )

    changes = [] if blockers else [
        {
            "kind": "promote-case",
            "case_id": case_id,
            "from_status": STATUS_GENERATED,
            "to_status": STATUS_REVIEWED,
        }
    ]
    applied = False
    sentinel_cleared = False
    if not blockers and not dry_run and target is not None:
        target["status"] = STATUS_REVIEWED
        target["split"] = split
        target["reviewed"] = True
        target["reviewed_by"] = reviewed_by
        target["review_note"] = review_note
        target["reviewed_at"] = reviewed_at
        target.pop("BOOTSTRAP_PENDING_REVIEW", None)
        _atomic_write_jsonl(benchmark_path, records)
        RepositoryStorage(benchmark_path.parent).append_history(
            {
                "event": "case_promoted",
                "case_id": case_id,
                "split": split,
                "reviewed_by": reviewed_by,
                "review_note": review_note,
                "reviewed_at": reviewed_at,
            }
        )
        applied = True
        sentinel_cleared = True

    return {
        "path": str(benchmark_path),
        "case_id": case_id,
        "split": split,
        "reviewed_by": reviewed_by,
        "review_note": review_note,
        "reviewed_at": reviewed_at,
        "dry_run": dry_run,
        "applied": applied,
        "sentinel_cleared": sentinel_cleared,
        "blockers": blockers,
        "changes": changes,
    }
