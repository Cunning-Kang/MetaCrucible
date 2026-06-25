"""Pure-logic unit tests for ``metacrucible.benchmark`` (Issue #44).

The existing ``tests/test_benchmark_loader.py`` exercises the loader
through a deferred-import fixture and pins it against the ADR 0029
contract. This file complements that suite by importing the module
directly and pinning the *pure* logic of the four primitives the
public surface is built on:

  - :func:`_read_jsonl_records` — JSONL-tolerant record reader
  - :func:`_blocker`            — stable-id blocker builder
  - :func:`load_benchmark`      — partitioner + blocker surface
  - :func:`migrate_benchmark`   — dry-run-first schema migration

In addition to per-function coverage, this file pins:

  - **Schema paths** — how the loader treats ``schema_version``
    when it matches, mismatches, is missing, or is the wrong type.
  - **v1↔v2 migration** — the migration plan shape and the
    applied-vs-dry-run distinction across v0/v1/v2 starting points.
  - **Blocker id stability** — the exact strings that ADR 0029
    pins as the machine contract.

All tests use ``tmp_path`` for JSONL fixtures. No live model, network,
LLM, sleep, or subprocess calls. No real secrets — placeholder values
are obviously fake (e.g. ``"fake-token-NOT-REAL"``).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from metacrucible.benchmark import (
    BENCHMARK_SCHEMA_VERSION,
    DUPLICATE_CASE_ID_BLOCKER,
    MISSING_REVIEWED_EVAL_BLOCKER,
    MISSING_REVIEWED_HELD_OUT_BLOCKER,
    PENDING_GENERATED_BLOCKER,
    SCHEMA_VERSION_MISMATCH_BLOCKER,
    SPLIT_EVAL,
    SPLIT_HELD_OUT,
    STATUS_DISABLED,
    STATUS_GENERATED,
    STATUS_REVIEWED,
    BenchmarkResult,
    _blocker,
    _read_jsonl_records,
    load_benchmark,
    migrate_benchmark,
)


# --------------------------------------------------------------------------- #
# Shared JSONL fixture helpers                                                 #
# --------------------------------------------------------------------------- #


def _write_jsonl(path: Path, records: list[Any]) -> Path:
    """Write ``records`` as one JSON value per line at ``path``.

    ``None`` entries are serialized as JSON ``null`` so the file
    round-trips cleanly. The helper accepts non-dict records so
    tests can exercise the loader's isinstance(obj, dict) guard.
    """
    path.write_text(
        "\n".join(json.dumps(rec) for rec in records) + "\n",
        encoding="utf-8",
    )
    return path


def _metadata(*, schema_version: int = BENCHMARK_SCHEMA_VERSION) -> dict[str, Any]:
    """Build a minimal valid v1 metadata record (ADR 0029)."""
    return {"schema_version": schema_version}


def _case(
    case_id: str,
    *,
    status: str = STATUS_REVIEWED,
    split: str | None = None,
) -> dict[str, Any]:
    """Build a minimal valid case record (ADR 0029)."""
    record: dict[str, Any] = {"case_id": case_id, "status": status}
    if split is not None:
        record["split"] = split
    return record


def _blocker_ids(result: BenchmarkResult) -> list[str]:
    """Return the list of blocker ids from a load result, in order."""
    return [b["id"] for b in result.blockers]


# --------------------------------------------------------------------------- #
# _read_jsonl_records                                                          #
# --------------------------------------------------------------------------- #


class TestReadJsonlRecords:
    """Pin the JSONL-tolerant record reader used by the loader and migrator."""

    def test_returns_empty_list_when_path_is_missing(
        self, tmp_path: Path
    ) -> None:
        """A missing file must yield an empty list, not raise.

        The loader uses this to surface the "no metadata" branch
        cleanly; a missing benchmark must not crash the bootstrap path.
        """
        missing = tmp_path / "does-not-exist.jsonl"
        assert _read_jsonl_records(missing) == []

    def test_returns_empty_list_when_file_is_empty(self, tmp_path: Path) -> None:
        """An empty file must yield an empty list."""
        empty = tmp_path / "empty.jsonl"
        empty.write_text("", encoding="utf-8")
        assert _read_jsonl_records(empty) == []

    def test_reads_single_object_record(self, tmp_path: Path) -> None:
        """A single dict line must be returned as a one-element list."""
        path = _write_jsonl(tmp_path / "one.jsonl", [{"k": "v", "n": 1}])
        assert _read_jsonl_records(path) == [{"k": "v", "n": 1}]

    def test_reads_multiple_object_records_in_order(
        self, tmp_path: Path
    ) -> None:
        """Authoring order must be preserved across multiple records."""
        path = _write_jsonl(
            tmp_path / "many.jsonl",
            [{"n": 1}, {"n": 2}, {"n": 3}],
        )
        assert _read_jsonl_records(path) == [{"n": 1}, {"n": 2}, {"n": 3}]

    def test_skips_blank_lines(self, tmp_path: Path) -> None:
        """Blank lines must be silently dropped (ADR 0029 tolerant read)."""
        path = tmp_path / "blanks.jsonl"
        path.write_text(
            "\n".join(
                [
                    json.dumps({"a": 1}),
                    "",
                    "   ",
                    json.dumps({"a": 2}),
                    "",
                    json.dumps({"a": 3}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        assert _read_jsonl_records(path) == [{"a": 1}, {"a": 2}, {"a": 3}]

    def test_skips_malformed_json_lines(self, tmp_path: Path) -> None:
        """Lines that fail JSON parsing must be skipped, not raise.

        The loader's contract is "tolerate, do not crash": a malformed
        line in an audit-style JSONL must not abort the bootstrap.
        """
        path = tmp_path / "bad.jsonl"
        path.write_text(
            "\n".join(
                [
                    json.dumps({"good": 1}),
                    "{not valid json",
                    json.dumps({"also_good": 2}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        assert _read_jsonl_records(path) == [
            {"good": 1},
            {"also_good": 2},
        ]

    def test_skips_non_object_json_records(self, tmp_path: Path) -> None:
        """Lists, scalars, and ``null`` must be filtered out.

        The loader treats only JSON objects as records; a list or
        string on a line is a malformed case payload and must be
        silently dropped, matching the "tolerate" contract.
        """
        path = tmp_path / "mixed-kinds.jsonl"
        path.write_text(
            "\n".join(
                [
                    json.dumps({"case": "object"}),
                    json.dumps([1, 2, 3]),
                    json.dumps("a-string"),
                    json.dumps(42),
                    json.dumps(None),
                    json.dumps({"another": "object"}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        assert _read_jsonl_records(path) == [
            {"case": "object"},
            {"another": "object"},
        ]


# --------------------------------------------------------------------------- #
# _blocker                                                                     #
# --------------------------------------------------------------------------- #


class TestBlocker:
    """Pin the private blocker builder used by both load and migrate paths."""

    def test_blocker_has_stable_id_and_message_keys(self) -> None:
        """A blocker is a ``{"id": str, "message": str}`` mapping."""
        blocker = _blocker("test-id", "human message")
        assert blocker == {"id": "test-id", "message": "human message"}
        assert set(blocker.keys()) == {"id", "message"}

    def test_blocker_preserves_caller_strings_verbatim(self) -> None:
        """The id and message are stored verbatim — no normalization.

        Blocker ids are the machine contract, so even a strange
        value like ``"FAKE.not-real_id-12345"`` must round-trip
        unchanged. Messages follow the same rule.
        """
        blocker = _blocker("FAKE.not-real_id-12345", "msg-with-=-and-spaces")
        assert blocker["id"] == "FAKE.not-real_id-12345"
        assert blocker["message"] == "msg-with-=-and-spaces"

    def test_blocker_does_not_invent_extra_fields(self) -> None:
        """Only ``id`` and ``message`` are produced — no extra keys.

        Downstream consumers enumerate the dict; an unexpected key
        would be a contract break.
        """
        blocker = _blocker("x", "y")
        assert len(blocker) == 2


# --------------------------------------------------------------------------- #
# load_benchmark — schema paths                                                #
# --------------------------------------------------------------------------- #


class TestLoadBenchmarkSchemaPaths:
    """Pin the schema-version gate on the first JSONL record."""

    def test_metadata_none_when_file_is_missing(self, tmp_path: Path) -> None:
        """A missing benchmark must surface ``metadata=None``."""
        result = load_benchmark(tmp_path / "missing.jsonl")
        assert result.metadata is None

    def test_metadata_none_when_file_is_empty(self, tmp_path: Path) -> None:
        """An empty file must surface ``metadata=None``."""
        empty = tmp_path / "empty.jsonl"
        empty.write_text("", encoding="utf-8")
        result = load_benchmark(empty)
        assert result.metadata is None

    def test_v1_schema_version_is_accepted(self, tmp_path: Path) -> None:
        """A v1 metadata record must NOT surface a schema-version blocker.

        With a reviewed eval + reviewed held-out case the benchmark
        is optimize-runnable, so the only blockers possible are the
        two missing-reviewed ones (none, here).
        """
        path = _write_jsonl(
            tmp_path / "v1.jsonl",
            [
                _metadata(schema_version=1),
                _case("eval-1", split=SPLIT_EVAL),
                _case("held-1", split=SPLIT_HELD_OUT),
            ],
        )
        result = load_benchmark(path)
        assert SCHEMA_VERSION_MISMATCH_BLOCKER not in _blocker_ids(result)
        assert result.metadata is not None
        assert result.metadata["schema_version"] == 1

    def test_v2_schema_version_surfaces_blocker(self, tmp_path: Path) -> None:
        """A v2 metadata record must surface a ``schema-version-mismatch``.

        ADR 0029: "incompatible or newer versions block". The
        loader must surface a stable blocker so the bootstrap path
        can refuse to optimize and steer the user to migrate.
        """
        path = _write_jsonl(
            tmp_path / "v2.jsonl",
            [
                _metadata(schema_version=2),
                _case("eval-1", split=SPLIT_EVAL),
                _case("held-1", split=SPLIT_HELD_OUT),
            ],
        )
        result = load_benchmark(path)
        ids = _blocker_ids(result)
        assert SCHEMA_VERSION_MISMATCH_BLOCKER in ids
        # Other blockers may also be present (missing-reviewed etc.)
        # but the mismatch blocker must be among them.

    def test_missing_schema_version_surfaces_blocker(
        self, tmp_path: Path
    ) -> None:
        """A metadata record with no ``schema_version`` key must block.

        A metadata record without a version is malformed; the
        loader treats it as a version mismatch so the user gets a
        stable blocker id.
        """
        path = _write_jsonl(
            tmp_path / "no-version.jsonl",
            [
                {"name": "no-version"},
                _case("eval-1", split=SPLIT_EVAL),
            ],
        )
        result = load_benchmark(path)
        assert SCHEMA_VERSION_MISMATCH_BLOCKER in _blocker_ids(result)

    def test_non_integer_schema_version_surfaces_blocker(
        self, tmp_path: Path
    ) -> None:
        """A non-integer ``schema_version`` (e.g. ``"v1"``) must block.

        The pin is exact-equality with the int constant; a string
        is not equal to 1 and must surface the mismatch blocker.
        """
        path = _write_jsonl(
            tmp_path / "string-version.jsonl",
            [
                {"schema_version": "v1"},
                _case("eval-1", split=SPLIT_EVAL),
            ],
        )
        result = load_benchmark(path)
        assert SCHEMA_VERSION_MISMATCH_BLOCKER in _blocker_ids(result)

    def test_metadata_is_first_record_after_non_dict_lines_are_dropped(
        self, tmp_path: Path
    ) -> None:
        """The first *object* record after filtering is the metadata.

        Non-dict lines (lists/scalars) are filtered by
        ``_read_jsonl_records``; the loader's "first record is
        metadata" rule applies to the filtered stream.
        """
        path = tmp_path / "leading-non-object.jsonl"
        path.write_text(
            "\n".join(
                [
                    json.dumps([1, 2, 3]),  # filtered
                    json.dumps("string"),  # filtered
                    json.dumps(_metadata(schema_version=1)),
                    json.dumps(_case("eval-1", split=SPLIT_EVAL)),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        result = load_benchmark(path)
        assert result.metadata == _metadata(schema_version=1)
        assert result.cases == [_case("eval-1", split=SPLIT_EVAL)]


# --------------------------------------------------------------------------- #
# load_benchmark — partition logic                                             #
# --------------------------------------------------------------------------- #


class TestLoadBenchmarkPartition:
    """Pin the (status, split) partitioning into the four buckets."""

    def test_reviewed_eval_case_lands_in_eligible_eval(
        self, tmp_path: Path
    ) -> None:
        """A reviewed eval case must populate ``eligible_eval_cases``."""
        path = _write_jsonl(
            tmp_path / "one-eval.jsonl",
            [
                _metadata(),
                _case("eval-1", status=STATUS_REVIEWED, split=SPLIT_EVAL),
            ],
        )
        result = load_benchmark(path)
        assert len(result.eligible_eval_cases) == 1
        assert result.eligible_eval_cases[0]["case_id"] == "eval-1"

    def test_reviewed_held_out_case_lands_in_eligible_held_out(
        self, tmp_path: Path
    ) -> None:
        """A reviewed held-out case must populate ``eligible_held_out_cases``."""
        path = _write_jsonl(
            tmp_path / "one-held-out.jsonl",
            [
                _metadata(),
                _case(
                    "held-1",
                    status=STATUS_REVIEWED,
                    split=SPLIT_HELD_OUT,
                ),
            ],
        )
        result = load_benchmark(path)
        assert len(result.eligible_held_out_cases) == 1
        assert result.eligible_held_out_cases[0]["case_id"] == "held-1"

    def test_generated_case_lands_in_pending_not_eligible(
        self, tmp_path: Path
    ) -> None:
        """A generated case must appear in ``pending_generated_cases`` only."""
        path = _write_jsonl(
            tmp_path / "generated.jsonl",
            [
                _metadata(),
                _case("gen-1", status=STATUS_GENERATED, split=SPLIT_EVAL),
            ],
        )
        result = load_benchmark(path)
        assert result.eligible_eval_cases == []
        assert result.eligible_held_out_cases == []
        assert [c["case_id"] for c in result.pending_generated_cases] == [
            "gen-1"
        ]

    def test_disabled_case_is_ignored_for_optimize(
        self, tmp_path: Path
    ) -> None:
        """A disabled case must appear in ``disabled_cases`` and nowhere else."""
        path = _write_jsonl(
            tmp_path / "disabled.jsonl",
            [
                _metadata(),
                _case("dis-1", status=STATUS_DISABLED, split=SPLIT_EVAL),
            ],
        )
        result = load_benchmark(path)
        assert [c["case_id"] for c in result.disabled_cases] == ["dis-1"]
        assert result.eligible_eval_cases == []
        assert result.eligible_held_out_cases == []
        assert result.pending_generated_cases == []

    def test_cases_attribute_preserves_authoring_order(
        self, tmp_path: Path
    ) -> None:
        """``cases`` must mirror the JSONL line order, regardless of status."""
        records = [
            _metadata(),
            _case("a-eval", split=SPLIT_EVAL),
            _case("b-gen"),
            _case("c-held-out", split=SPLIT_HELD_OUT),
            _case("d-disabled"),
        ]
        path = _write_jsonl(tmp_path / "order.jsonl", records)
        result = load_benchmark(path)
        assert [c["case_id"] for c in result.cases] == [
            "a-eval",
            "b-gen",
            "c-held-out",
            "d-disabled",
        ]

    def test_eligible_buckets_preserve_authoring_order(
        self, tmp_path: Path
    ) -> None:
        """Eligible buckets must mirror the JSONL line order."""
        records = [
            _metadata(),
            _case("eval-2", split=SPLIT_EVAL),
            _case("eval-1", split=SPLIT_EVAL),
            _case("held-2", split=SPLIT_HELD_OUT),
            _case("held-1", split=SPLIT_HELD_OUT),
        ]
        path = _write_jsonl(tmp_path / "eligible-order.jsonl", records)
        result = load_benchmark(path)
        assert [c["case_id"] for c in result.eligible_eval_cases] == [
            "eval-2",
            "eval-1",
        ]
        assert [c["case_id"] for c in result.eligible_held_out_cases] == [
            "held-2",
            "held-1",
        ]

    def test_unknown_status_is_ignored_silently(self, tmp_path: Path) -> None:
        """An unknown status is dropped from every bucket.

        The loader does not raise on unknown statuses; the case is
        left in ``cases`` for audit but does not populate any of
        the four partition buckets.
        """
        path = _write_jsonl(
            tmp_path / "weird-status.jsonl",
            [
                _metadata(),
                _case("weird-1", status="archived", split=SPLIT_EVAL),
                _case("eval-1", status=STATUS_REVIEWED, split=SPLIT_EVAL),
            ],
        )
        result = load_benchmark(path)
        assert [c["case_id"] for c in result.eligible_eval_cases] == [
            "eval-1"
        ]
        assert result.pending_generated_cases == []
        assert result.disabled_cases == []

    def test_case_missing_case_id_still_partitions_by_status(
        self, tmp_path: Path
    ) -> None:
        """A case record with no ``case_id`` is still partitioned by status.

        The loader's ``case_id`` gate is *only* the duplicate-id
        blocker; the (status, split) partition below it runs
        unconditionally. A record that omits ``case_id`` is
        malformed, but the load does not raise and the record
        still lands in the matching bucket. This test pins the
        actual loader behavior.
        """
        path = _write_jsonl(
            tmp_path / "no-case-id.jsonl",
            [
                _metadata(),
                {"status": STATUS_REVIEWED, "split": SPLIT_EVAL},
                _case("eval-1", status=STATUS_REVIEWED, split=SPLIT_EVAL),
            ],
        )
        result = load_benchmark(path)
        # Both records end up in the eligible-eval bucket because
        # the partition branch only inspects status + split.
        assert len(result.eligible_eval_cases) == 2
        # And no duplicate-id blocker fires (the no-id record
        # cannot collide with itself, and the second record has
        # a fresh id).
        assert DUPLICATE_CASE_ID_BLOCKER not in _blocker_ids(result)


# --------------------------------------------------------------------------- #
# load_benchmark — blocker surfaces                                            #
# --------------------------------------------------------------------------- #


class TestLoadBenchmarkBlockers:
    """Pin the runnability blockers surfaced by the loader."""

    def test_missing_eval_and_held_out_both_surface(
        self, tmp_path: Path
    ) -> None:
        """A no-case benchmark must surface both missing-reviewed blockers.

        The empty case branch still gates runnability on the two
        ADR 0025 review-coverage requirements.
        """
        path = _write_jsonl(tmp_path / "no-cases.jsonl", [_metadata()])
        result = load_benchmark(path)
        ids = _blocker_ids(result)
        assert MISSING_REVIEWED_EVAL_BLOCKER in ids
        assert MISSING_REVIEWED_HELD_OUT_BLOCKER in ids

    def test_only_generated_cases_surfaces_all_three_blockers(
        self, tmp_path: Path
    ) -> None:
        """Generated-only benchmark surfaces pending + both missing-reviewed.

        ADR 0025 + ADR 0018: the user must see the full picture
        (no reviewed eval, no reviewed held-out, generated pending)
        so they know what review work is outstanding.
        """
        path = _write_jsonl(
            tmp_path / "generated-only.jsonl",
            [
                _metadata(),
                _case("g-1", status=STATUS_GENERATED, split=SPLIT_EVAL),
                _case("g-2", status=STATUS_GENERATED, split=SPLIT_HELD_OUT),
            ],
        )
        result = load_benchmark(path)
        ids = _blocker_ids(result)
        assert PENDING_GENERATED_BLOCKER in ids
        assert MISSING_REVIEWED_EVAL_BLOCKER in ids
        assert MISSING_REVIEWED_HELD_OUT_BLOCKER in ids

    def test_duplicate_case_id_surfaces_blocker(self, tmp_path: Path) -> None:
        """Two cases sharing ``case_id`` must surface the duplicate blocker.

        ADR 0029 pins ``duplicate-case-id`` as the machine contract.
        The loader does not deduplicate silently: the user must
        see the conflict.
        """
        path = _write_jsonl(
            tmp_path / "dup.jsonl",
            [
                _metadata(),
                _case("same", split=SPLIT_EVAL),
                _case("same", split=SPLIT_HELD_OUT),
            ],
        )
        result = load_benchmark(path)
        assert DUPLICATE_CASE_ID_BLOCKER in _blocker_ids(result)

    def test_is_optimize_runnable_is_inverse_of_blockers(
        self, tmp_path: Path
    ) -> None:
        """``is_optimize_runnable`` must be ``True`` iff ``blockers`` is empty.

        The property is a derived view; this test pins that view
        against the underlying ``blockers`` list.
        """
        # Runnable: reviewed eval + reviewed held-out.
        runnable_path = _write_jsonl(
            tmp_path / "runnable.jsonl",
            [
                _metadata(),
                _case("eval-1", split=SPLIT_EVAL),
                _case("held-1", split=SPLIT_HELD_OUT),
            ],
        )
        result = load_benchmark(runnable_path)
        assert result.is_optimize_runnable is True
        assert result.blockers == []

        # Not runnable: missing held-out.
        not_runnable_path = _write_jsonl(
            tmp_path / "not-runnable.jsonl",
            [
                _metadata(),
                _case("eval-1", split=SPLIT_EVAL),
            ],
        )
        result2 = load_benchmark(not_runnable_path)
        assert result2.is_optimize_runnable is False
        assert result2.blockers != []


# --------------------------------------------------------------------------- #
# migrate_benchmark — dry-run / apply contract                                 #
# --------------------------------------------------------------------------- #


class TestMigrateBenchmarkContract:
    """Pin the default safe path: dry-run-first, no implicit write."""

    def test_default_invocation_does_not_mutate_file(
        self, tmp_path: Path
    ) -> None:
        """The default path must never write the on-disk benchmark."""
        path = _write_jsonl(
            tmp_path / "v2.jsonl",
            [
                _metadata(schema_version=2),
                _case("eval-1", split=SPLIT_EVAL),
            ],
        )
        before = path.read_bytes()
        plan = migrate_benchmark(path)
        after = path.read_bytes()
        assert after == before
        assert plan["dry_run"] is True
        assert plan["applied"] is False

    def test_explicit_dry_run_true_does_not_mutate_file(
        self, tmp_path: Path
    ) -> None:
        """The explicit ``dry_run=True`` form must also never write."""
        path = _write_jsonl(
            tmp_path / "v2.jsonl",
            [
                _metadata(schema_version=2),
                _case("eval-1", split=SPLIT_EVAL),
            ],
        )
        before = path.read_bytes()
        migrate_benchmark(path, dry_run=True)
        assert path.read_bytes() == before

    def test_plan_keys_are_stable(self, tmp_path: Path) -> None:
        """The plan dict must carry the five documented keys.

        ADR 0029 + the docstring pin the plan shape. Downstream
        automation (CLI, JSON output) branches on these keys.
        """
        path = _write_jsonl(
            tmp_path / "v2.jsonl",
            [
                _metadata(schema_version=2),
                _case("eval-1", split=SPLIT_EVAL),
            ],
        )
        plan = migrate_benchmark(path)
        assert set(plan.keys()) >= {
            "path",
            "from_version",
            "to_version",
            "dry_run",
            "applied",
            "changes",
        }

    def test_plan_to_version_is_pinned_constant(
        self, tmp_path: Path
    ) -> None:
        """``to_version`` must equal the module's pinned current version."""
        path = _write_jsonl(
            tmp_path / "v2.jsonl",
            [_metadata(schema_version=2), _case("eval-1", split=SPLIT_EVAL)],
        )
        plan = migrate_benchmark(path)
        assert plan["to_version"] == BENCHMARK_SCHEMA_VERSION
        assert plan["to_version"] == 1

    def test_plan_path_is_stringified(self, tmp_path: Path) -> None:
        """The plan's ``path`` must be the string form of the input path."""
        path = _write_jsonl(
            tmp_path / "v2.jsonl",
            [_metadata(schema_version=2), _case("eval-1", split=SPLIT_EVAL)],
        )
        plan = migrate_benchmark(path)
        assert plan["path"] == str(path)

    def test_plan_changes_is_a_list(self, tmp_path: Path) -> None:
        """``changes`` must be a list of change records (possibly empty)."""
        path = _write_jsonl(
            tmp_path / "v2.jsonl",
            [_metadata(schema_version=2), _case("eval-1", split=SPLIT_EVAL)],
        )
        plan = migrate_benchmark(path)
        assert isinstance(plan["changes"], list)
        assert plan["changes"]  # non-empty for v2 -> v1 migration


# --------------------------------------------------------------------------- #
# migrate_benchmark — v1↔v2 migration paths                                    #
# --------------------------------------------------------------------------- #


class TestMigrateBenchmarkV1V2Paths:
    """Pin migration behavior across the v0 / v1 / v2 starting points."""

    def test_v1_to_v1_is_no_op_in_dry_run(self, tmp_path: Path) -> None:
        """v1 -> v1 dry-run must report a single no-op change and no write."""
        path = _write_jsonl(
            tmp_path / "v1.jsonl",
            [
                _metadata(schema_version=1),
                _case("eval-1", split=SPLIT_EVAL),
                _case("held-1", split=SPLIT_HELD_OUT),
            ],
        )
        before = path.read_bytes()
        plan = migrate_benchmark(path, dry_run=True)
        after = path.read_bytes()
        assert plan["from_version"] == 1
        assert plan["to_version"] == 1
        assert plan["applied"] is False
        assert plan["dry_run"] is True
        assert after == before
        assert len(plan["changes"]) == 1
        assert plan["changes"][0]["kind"] == "no-op"

    def test_v1_to_v1_is_no_op_when_applied(self, tmp_path: Path) -> None:
        """v1 -> v1 apply must still be a no-op: no write, no version bump.

        The migrator must not rewrite a file that is already at the
        target version, even when the caller explicitly opts in
        with ``dry_run=False``.
        """
        path = _write_jsonl(
            tmp_path / "v1.jsonl",
            [_metadata(schema_version=1), _case("eval-1", split=SPLIT_EVAL)],
        )
        before = path.read_bytes()
        plan = migrate_benchmark(path, dry_run=False)
        after = path.read_bytes()
        assert after == before
        assert plan["applied"] is False
        assert plan["changes"][0]["kind"] == "no-op"

    def test_v2_to_v1_dry_run_describes_downgrade(
        self, tmp_path: Path
    ) -> None:
        """v2 -> v1 dry-run must describe a schema-version change, not write.

        The change record must carry the from/to versions and the
        ADR 0029 reference. ``applied`` must remain ``False``.
        """
        path = _write_jsonl(
            tmp_path / "v2.jsonl",
            [
                _metadata(schema_version=2),
                _case("eval-1", split=SPLIT_EVAL),
            ],
        )
        plan = migrate_benchmark(path, dry_run=True)
        assert plan["from_version"] == 2
        assert plan["to_version"] == 1
        assert plan["applied"] is False
        assert plan["changes"][0]["kind"] == "schema-version"
        change = plan["changes"][0]
        assert change["from"] == 2
        assert change["to"] == 1

    def test_v2_to_v1_apply_writes_file_and_bumps_version(
        self, tmp_path: Path
    ) -> None:
        """v2 -> v1 apply must rewrite the file with ``schema_version=1``.

        Field-level migration is not implemented yet (per the
        change message), so the only disk-visible effect is the
        metadata version bump. The case records must survive the
        rewrite unchanged.
        """
        path = _write_jsonl(
            tmp_path / "v2.jsonl",
            [
                _metadata(schema_version=2),
                _case("eval-1", split=SPLIT_EVAL),
                _case("held-1", split=SPLIT_HELD_OUT),
            ],
        )
        plan = migrate_benchmark(path, dry_run=False)
        assert plan["applied"] is True
        assert plan["changes"][0]["applied"] is True

        # Reload and inspect.
        reloaded = _read_jsonl_records(path)
        assert reloaded[0]["schema_version"] == 1
        # The case records must be preserved.
        assert reloaded[1:] == [
            _case("eval-1", split=SPLIT_EVAL),
            _case("held-1", split=SPLIT_HELD_OUT),
        ]

    def test_v2_to_v1_apply_preserves_case_records(
        self, tmp_path: Path
    ) -> None:
        """v2 -> v1 apply must not touch case records, only the metadata line.

        The migrator's message says "field-level v2->v1 migration
        is not implemented yet" — the only safe change is the
        version bump. Any case-level mutation would be a contract
        break.
        """
        case = _case("eval-1", split=SPLIT_EVAL)
        # Inject an extra field to make sure it survives.
        case_with_extra = dict(case, reviewer="alice")
        path = _write_jsonl(
            tmp_path / "v2-with-extra.jsonl",
            [_metadata(schema_version=2), case_with_extra],
        )
        migrate_benchmark(path, dry_run=False)
        reloaded = _read_jsonl_records(path)
        assert reloaded[1] == case_with_extra

    def test_v0_to_v1_dry_run_describes_upgrade(
        self, tmp_path: Path
    ) -> None:
        """A v0 metadata record (no ``schema_version``) must plan an upgrade.

        ``from_version=None`` is the "no version on disk" path;
        the plan must still describe the migration.
        """
        path = _write_jsonl(
            tmp_path / "v0.jsonl",
            [
                {"name": "no-version-yet"},
                _case("eval-1", split=SPLIT_EVAL),
            ],
        )
        plan = migrate_benchmark(path, dry_run=True)
        assert plan["from_version"] is None
        assert plan["to_version"] == 1
        assert plan["applied"] is False
        assert plan["changes"][0]["kind"] == "schema-version"

    def test_v0_to_v1_apply_does_not_invent_metadata(
        self, tmp_path: Path
    ) -> None:
        """A truly empty file must not be rewritten with synthetic data.

        The migrator refuses to fabricate a metadata record on an
        empty file: there is nothing to bump. The plan's
        ``applied`` flag must stay ``False`` even when the caller
        passes ``dry_run=False``. (A file with a metadata record
        that just lacks ``schema_version`` is a different path —
        the migrator DOES rewrite that, bumping the version.)
        """
        path = tmp_path / "truly-empty.jsonl"
        path.write_text("", encoding="utf-8")
        before = path.read_bytes()
        plan = migrate_benchmark(path, dry_run=False)
        after = path.read_bytes()
        assert after == before
        assert plan["applied"] is False
        assert plan["from_version"] is None

    def test_empty_file_dry_run_yields_from_version_none(
        self, tmp_path: Path
    ) -> None:
        """An empty benchmark must report ``from_version=None`` in the plan."""
        empty = tmp_path / "empty.jsonl"
        empty.write_text("", encoding="utf-8")
        plan = migrate_benchmark(empty, dry_run=True)
        assert plan["from_version"] is None
        assert plan["to_version"] == 1
        assert plan["applied"] is False

    def test_missing_file_dry_run_yields_from_version_none(
        self, tmp_path: Path
    ) -> None:
        """A missing file must be treated as ``from_version=None``."""
        missing = tmp_path / "missing.jsonl"
        plan = migrate_benchmark(missing, dry_run=True)
        assert plan["from_version"] is None
        assert plan["to_version"] == 1
        assert plan["applied"] is False


# --------------------------------------------------------------------------- #
# migrate_benchmark — v1↔v2 apply round-trip with the loader                    #
# --------------------------------------------------------------------------- #


class TestMigrateThenLoadRoundTrip:
    """Pin that ``migrate`` + ``load`` together clear the schema-version gate."""

    def test_v2_then_migrate_apply_removes_schema_mismatch_blocker(
        self, tmp_path: Path
    ) -> None:
        """After ``migrate(apply=True)`` the loader must accept the file.

        Round-trip contract: a v2 file blocks on load with
        ``schema-version-mismatch``; after a successful
        ``dry_run=False`` migration the file is at v1 and loads
        with no mismatch blocker (and, given a reviewed eval +
        reviewed held-out, with no blockers at all).
        """
        path = _write_jsonl(
            tmp_path / "v2.jsonl",
            [
                _metadata(schema_version=2),
                _case("eval-1", split=SPLIT_EVAL),
                _case("held-1", split=SPLIT_HELD_OUT),
            ],
        )
        # Pre-migration: must surface the mismatch blocker.
        pre = load_benchmark(path)
        assert SCHEMA_VERSION_MISMATCH_BLOCKER in _blocker_ids(pre)

        # Migrate, applied.
        plan = migrate_benchmark(path, dry_run=False)
        assert plan["applied"] is True

        # Post-migration: mismatch blocker gone, benchmark runnable.
        post = load_benchmark(path)
        assert SCHEMA_VERSION_MISMATCH_BLOCKER not in _blocker_ids(post)
        assert post.is_optimize_runnable is True

    def test_v2_then_migrate_dry_run_keeps_mismatch_blocker(
        self, tmp_path: Path
    ) -> None:
        """A dry-run migration must NOT clear the schema-version blocker.

        The dry-run-first contract means a caller who forgets to
        pass ``dry_run=False`` must still see the file as blocked
        on the next load.
        """
        path = _write_jsonl(
            tmp_path / "v2.jsonl",
            [
                _metadata(schema_version=2),
                _case("eval-1", split=SPLIT_EVAL),
                _case("held-1", split=SPLIT_HELD_OUT),
            ],
        )
        migrate_benchmark(path, dry_run=True)
        result = load_benchmark(path)
        assert SCHEMA_VERSION_MISMATCH_BLOCKER in _blocker_ids(result)


# --------------------------------------------------------------------------- #
# Blocker id stability — the machine contract                                  #
# --------------------------------------------------------------------------- #


class TestBlockerIdStability:
    """Pin the exact strings the rest of the pipeline branches on."""

    @pytest.mark.parametrize(
        "blocker_id",
        [
            DUPLICATE_CASE_ID_BLOCKER,
            SCHEMA_VERSION_MISMATCH_BLOCKER,
            MISSING_REVIEWED_EVAL_BLOCKER,
            MISSING_REVIEWED_HELD_OUT_BLOCKER,
            PENDING_GENERATED_BLOCKER,
        ],
    )
    def test_blocker_id_is_nonempty_string(self, blocker_id: str) -> None:
        """Each blocker id must be a non-empty string."""
        assert isinstance(blocker_id, str)
        assert blocker_id != ""

    def test_duplicate_case_id_blocker_is_pinned(self) -> None:
        """The duplicate-id blocker must be exactly ``"duplicate-case-id"``."""
        assert DUPLICATE_CASE_ID_BLOCKER == "duplicate-case-id"

    def test_schema_version_mismatch_blocker_is_pinned(self) -> None:
        """The schema-version blocker must be exactly ``"schema-version-mismatch"``."""
        assert SCHEMA_VERSION_MISMATCH_BLOCKER == "schema-version-mismatch"

    def test_missing_reviewed_eval_blocker_is_pinned(self) -> None:
        """The missing-reviewed-eval blocker must be exactly
        ``"missing-reviewed-eval-case"``."""
        assert MISSING_REVIEWED_EVAL_BLOCKER == "missing-reviewed-eval-case"

    def test_missing_reviewed_held_out_blocker_is_pinned(self) -> None:
        """The missing-reviewed-held-out blocker must be exactly
        ``"missing-reviewed-held-out-case"``."""
        assert (
            MISSING_REVIEWED_HELD_OUT_BLOCKER
            == "missing-reviewed-held-out-case"
        )

    def test_pending_generated_blocker_is_pinned(self) -> None:
        """The pending-generated blocker must be exactly
        ``"pending-generated-case"``."""
        assert PENDING_GENERATED_BLOCKER == "pending-generated-case"

    def test_blocker_ids_are_unique(self) -> None:
        """The five blocker ids must be pairwise distinct.

        Two ids colliding would silently merge distinct failure
        modes for downstream consumers that branch on the id.
        """
        ids = {
            DUPLICATE_CASE_ID_BLOCKER,
            SCHEMA_VERSION_MISMATCH_BLOCKER,
            MISSING_REVIEWED_EVAL_BLOCKER,
            MISSING_REVIEWED_HELD_OUT_BLOCKER,
            PENDING_GENERATED_BLOCKER,
        }
        assert len(ids) == 5

    def test_blocker_ids_match_loader_emitted_ids(
        self, tmp_path: Path
    ) -> None:
        """The loader must emit the exact pinned ids, verbatim.
        The end-to-end check: build a benchmark that hits every
        blocker, then assert each pinned id appears in the result.
        """
        path = _write_jsonl(
            tmp_path / "all-blockers.jsonl",
            [
                _metadata(schema_version=2),  # mismatch
                _case(
                    "x", status=STATUS_GENERATED
                ),  # pending + first of dup
                _case(
                    "x", status=STATUS_GENERATED
                ),  # pending + duplicate
            ],
        )
        result = load_benchmark(path)
        ids = set(_blocker_ids(result))
        # mismatch + duplicate + pending + both missing-reviewed.
        assert SCHEMA_VERSION_MISMATCH_BLOCKER in ids
        assert DUPLICATE_CASE_ID_BLOCKER in ids
        assert PENDING_GENERATED_BLOCKER in ids
        assert MISSING_REVIEWED_EVAL_BLOCKER in ids
        assert MISSING_REVIEWED_HELD_OUT_BLOCKER in ids


# --------------------------------------------------------------------------- #
# Schema-version constant stability                                            #
# --------------------------------------------------------------------------- #


class TestSchemaVersionConstant:
    """Pin the on-disk schema version pinned by ADR 0029."""

    def test_benchmark_schema_version_is_one(self) -> None:
        """The current on-disk schema version is v1 (ADR 0029)."""
        assert BENCHMARK_SCHEMA_VERSION == 1

    def test_benchmark_schema_version_is_int(self) -> None:
        """The constant must be an int (used in exact-equality checks)."""
        assert isinstance(BENCHMARK_SCHEMA_VERSION, int)
