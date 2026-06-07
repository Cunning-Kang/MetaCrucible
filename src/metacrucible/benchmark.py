"""Benchmark JSONL v1 schema + loader (Issue #7).

The benchmark file is a strict-typed JSONL: one ``metadata`` record
first, then any number of case records (ADR 0029). This module is the
canonical read path that ``bootstrap``, ``evaluate``, ``optimize``,
and ``synthesize`` build on; it exposes:

  - :func:`load_benchmark` — parses the JSONL, partitions cases by
    status and split, and returns a structured result with blockers
    from ADR 0029's fixed small machine-stable set.
  - :func:`migrate_benchmark` — dry-run-first schema migration;
    the default path never mutates the on-disk file.

Status and split values are pinned by ADR 0018 / ADR 0025 / ADR 0029.
Blocker ids are the machine-stable contract: CI and downstream
automation branch on them verbatim.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Current on-disk schema version pinned by ADR 0029 ("Benchmark v1").
BENCHMARK_SCHEMA_VERSION = 1

#: Status values pinned by ADR 0018 / ADR 0029.
STATUS_REVIEWED = "reviewed"
STATUS_GENERATED = "generated"
STATUS_DISABLED = "disabled"

#: Split values pinned by ADR 0025 / ADR 0029.
SPLIT_EVAL = "eval"
SPLIT_HELD_OUT = "held_out"

#: Stable blocker ids from ADR 0029's "fixed small machine-stable set"
#: of invalid benchmark blocker codes. The exact strings are the
#: machine contract: tests and downstream automation branch on them
#: verbatim.
DUPLICATE_CASE_ID_BLOCKER = "duplicate-case-id"
SCHEMA_VERSION_MISMATCH_BLOCKER = "schema-version-mismatch"
MISSING_REVIEWED_EVAL_BLOCKER = "missing-reviewed-eval-case"
MISSING_REVIEWED_HELD_OUT_BLOCKER = "missing-reviewed-held-out-case"
PENDING_GENERATED_BLOCKER = "pending-generated-case"


@dataclass
class BenchmarkResult:
    """Structured result of :func:`load_benchmark`.

    Cases are partitioned by ``(status, split)`` into the four buckets
    the rest of the pipeline branches on. ``blockers`` carries stable
    snake_case ids from ADR 0029; any blocker means the benchmark is
    not optimize-runnable.

    Authoring order is preserved inside each bucket: the loader
    iterates the JSONL file in line order and appends to each bucket
    as it goes, so the same order is replayed during evaluation and
    surfaced in the evidence bundle.
    """

    metadata: dict[str, Any] | None
    cases: list[dict[str, Any]] = field(default_factory=list)
    eligible_eval_cases: list[dict[str, Any]] = field(default_factory=list)
    eligible_held_out_cases: list[dict[str, Any]] = field(default_factory=list)
    pending_generated_cases: list[dict[str, Any]] = field(default_factory=list)
    disabled_cases: list[dict[str, Any]] = field(default_factory=list)
    blockers: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_optimize_runnable(self) -> bool:
        """True iff no blockers are present (ADR 0025).

        A benchmark is optimize-runnable when it has at least one
        eligible reviewed eval case AND at least one eligible reviewed
        held-out case, with no duplicate ids and no pending generated
        cases blocking the run. All of those conditions surface as
        blockers, so the runnability gate is simply "no blockers".
        """
        return not self.blockers


def _read_jsonl_records(path: Path) -> list[dict[str, Any]]:
    """Return all parseable JSON-object records from a JSONL file.

    Blank lines and lines that fail to parse or that do not decode
    as a JSON object are skipped: the loader must not crash on a
    malformed line (the init --check validator follows the same rule).
    """
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            records.append(obj)
    return records


def _blocker(blocker_id: str, message: str) -> dict[str, Any]:
    """Build a blocker dict with a stable ``id`` and human ``message``."""
    return {"id": blocker_id, "message": message}


def load_benchmark(path: str | Path) -> BenchmarkResult:
    """Load a benchmark JSONL v1 file and partition its cases.

    The first JSONL record is the benchmark-level ``metadata`` record;
    subsequent records are case records. Cases are partitioned by
    ``(status, split)`` into the four buckets the pipeline branches
    on. Blocker ids are drawn from ADR 0029's fixed small
    machine-stable set:

      - ``schema-version-mismatch``        — on-disk version != v1
      - ``duplicate-case-id``              — two case records share ``case_id``
      - ``missing-reviewed-eval-case``     — no eligible reviewed eval case
      - ``missing-reviewed-held-out-case`` — no eligible reviewed held-out case
      - ``pending-generated-case``         — generated case(s) block optimize

    The returned result is always populated; an empty file yields a
    result with ``metadata=None``, empty case buckets, and the two
    missing-reviewed blockers (an empty container is valid but not
    runnable per ADR 0025).
    """
    benchmark_path = Path(path)
    records = _read_jsonl_records(benchmark_path)

    if records:
        metadata = records[0]
        case_records = records[1:]
    else:
        metadata = None
        case_records = []

    blockers: list[dict[str, Any]] = []

    # Schema version gate (ADR 0029: "incompatible or newer versions
    # block"). We only check this when a metadata record is present;
    # an empty file surfaces the missing-reviewed blockers below.
    if metadata is not None:
        schema_version = metadata.get("schema_version")
        if schema_version != BENCHMARK_SCHEMA_VERSION:
            blockers.append(
                _blocker(
                    SCHEMA_VERSION_MISMATCH_BLOCKER,
                    (
                        f"benchmark schema_version={schema_version!r} is not "
                        f"v{BENCHMARK_SCHEMA_VERSION}; run "
                        f"`migrate_benchmark` for a dry-run migration plan "
                        f"(ADR 0029)"
                    ),
                )
            )

    eligible_eval: list[dict[str, Any]] = []
    eligible_held_out: list[dict[str, Any]] = []
    pending_generated: list[dict[str, Any]] = []
    disabled: list[dict[str, Any]] = []
    seen_case_ids: set[str] = set()

    for case in case_records:
        case_id = case.get("case_id")
        status = case.get("status")
        split = case.get("split")

        # Duplicate-id gate (ADR 0029). Only string, non-empty ids
        # count: a record missing ``case_id`` is malformed in a way
        # the loader does not attempt to recover, and the partition
        # below will simply drop it into no bucket.
        if isinstance(case_id, str) and case_id:
            if case_id in seen_case_ids:
                blockers.append(
                    _blocker(
                        DUPLICATE_CASE_ID_BLOCKER,
                        (
                            f"duplicate case_id {case_id!r} in benchmark; "
                            f"case ids must be unique (ADR 0029)"
                        ),
                    )
                )
            else:
                seen_case_ids.add(case_id)

        if status == STATUS_REVIEWED:
            if split == SPLIT_EVAL:
                eligible_eval.append(case)
            elif split == SPLIT_HELD_OUT:
                eligible_held_out.append(case)
        elif status == STATUS_GENERATED:
            pending_generated.append(case)
        elif status == STATUS_DISABLED:
            disabled.append(case)

    # Runnability gates (ADR 0025). Surfaced independently so the
    # user sees the full picture: a benchmark with only generated
    # cases carries both the pending-generated blocker and both
    # missing-reviewed blockers.
    if not eligible_eval:
        blockers.append(
            _blocker(
                MISSING_REVIEWED_EVAL_BLOCKER,
                (
                    "no eligible reviewed eval cases; optimize requires at "
                    "least one human-reviewed eval case (ADR 0025)"
                ),
            )
        )
    if not eligible_held_out:
        blockers.append(
            _blocker(
                MISSING_REVIEWED_HELD_OUT_BLOCKER,
                (
                    "no eligible reviewed held-out cases; optimize requires "
                    "at least one human-reviewed held-out case (ADR 0025)"
                ),
            )
        )
    if pending_generated:
        blockers.append(
            _blocker(
                PENDING_GENERATED_BLOCKER,
                (
                    f"{len(pending_generated)} pending generated case(s) "
                    f"require human review before optimization (ADR 0018)"
                ),
            )
        )

    return BenchmarkResult(
        metadata=metadata,
        cases=case_records,
        eligible_eval_cases=eligible_eval,
        eligible_held_out_cases=eligible_held_out,
        pending_generated_cases=pending_generated,
        disabled_cases=disabled,
        blockers=blockers,
    )


def migrate_benchmark(
    path: str | Path,
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Plan (and optionally apply) a benchmark schema migration.

    ADR 0029: "Schema changes are never applied implicitly during
    evaluation or optimization; incompatible or newer versions block,
    while explicit migration may be provided as a dry-run-first
    command." The default ``dry_run=True`` is the safe path: the
    on-disk file is never read for writing and the returned plan's
    ``applied`` flag is always ``False``. Callers opt in to writing
    by passing ``dry_run=False``.

    The returned plan is a plain dict so the caller can render it as
    JSON, log it, or branch on its keys. The keys are:

      - ``path``         — the on-disk benchmark path
      - ``from_version`` — the on-disk ``schema_version`` (``None`` if
                           the file has no metadata record)
      - ``to_version``   — the target version (= :data:`BENCHMARK_SCHEMA_VERSION`)
      - ``dry_run``      — echoes the caller's flag
      - ``applied``      — ``True`` iff the file was rewritten
      - ``changes``      — list of change records describing what
                           would / did happen
    """
    benchmark_path = Path(path)
    records = _read_jsonl_records(benchmark_path)

    metadata = records[0] if records else None
    from_version = metadata.get("schema_version") if metadata else None
    to_version = BENCHMARK_SCHEMA_VERSION

    plan: dict[str, Any] = {
        "path": str(benchmark_path),
        "from_version": from_version,
        "to_version": to_version,
        "dry_run": dry_run,
        "applied": False,
        "changes": [],
    }

    if from_version == to_version:
        plan["changes"].append(
            {
                "kind": "no-op",
                "message": (
                    f"benchmark is already at schema_version={to_version}; "
                    f"no changes needed"
                ),
            }
        )
        return plan

    plan["changes"].append(
        {
            "kind": "schema-version",
            "from": from_version,
            "to": to_version,
            "message": (
                f"would update metadata.schema_version from "
                f"{from_version!r} to {to_version!r} (ADR 0029); explicit "
                f"field-level v{from_version}\u2192v{to_version} migration is "
                f"not implemented yet"
            ),
        }
    )

    if not dry_run:
        if metadata is None:
            # Empty file: there is nothing to migrate, and writing an
            # invented metadata record would silently fabricate data.
            # Leave the plan in the "not applied" state so the caller
            # can see the change was described but not executed.
            return plan
        metadata["schema_version"] = to_version
        records[0] = metadata
        new_lines = [json.dumps(rec, sort_keys=True) for rec in records]
        benchmark_path.write_text(
            "\n".join(new_lines) + "\n",
            encoding="utf-8",
        )
        plan["applied"] = True
        plan["changes"][-1]["applied"] = True

    return plan


__all__ = [
    "BENCHMARK_SCHEMA_VERSION",
    "BenchmarkResult",
    "STATUS_REVIEWED",
    "STATUS_GENERATED",
    "STATUS_DISABLED",
    "SPLIT_EVAL",
    "SPLIT_HELD_OUT",
    "DUPLICATE_CASE_ID_BLOCKER",
    "SCHEMA_VERSION_MISMATCH_BLOCKER",
    "MISSING_REVIEWED_EVAL_BLOCKER",
    "MISSING_REVIEWED_HELD_OUT_BLOCKER",
    "PENDING_GENERATED_BLOCKER",
    "load_benchmark",
    "migrate_benchmark",
]
