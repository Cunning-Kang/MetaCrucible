"""Tests for Issue #7: Benchmark JSONL v1 schema + loader.

Issue #7 pins the public behavior of the benchmark loader that
``bootstrap``, ``evaluate``, ``optimize``, and ``synthesize`` build on:

  - The benchmark file is a strict-typed JSONL: one ``metadata`` record
    first, then any number of case records (ADR 0029, ADR 0025).
  - Every case record carries ``case_id`` (unique), ``status`` from
    {``reviewed``, ``generated``, ``disabled``}, and ``split`` from
    {``eval``, ``held_out``}. Reviewed cases are eligible; generated
    cases are pending review; disabled cases are ignored.
  - Duplicate ``case_id`` is a hard blocker (ADR 0029's
    "duplicate id" entry in the fixed small machine-stable set).
  - Schema version mismatch is a hard blocker; an explicit
    ``dry_run`` migration command is the supported way forward
    (ADR 0029: "Schema changes are never applied implicitly during
    evaluation or optimization; incompatible or newer versions block,
    while explicit migration may be provided as a dry-run-first
    command.").
  - ``optimize`` is only runnable when at least one reviewed eval
    case AND at least one reviewed held-out case are present
    (ADR 0025: baseline creation requires at least one human-reviewed
    eval case and one human-reviewed held-out case, because
    optimization needs a selection signal and acceptance needs a
    regression guard).

These tests are the red step: ``metacrucible.benchmark`` is not
implemented yet, so importing it must fail. Once the loader lands,
the tests turn green and pin the contract from the acceptance
criteria in Issue #7.

The implementation under test (not yet written) is expected to
live in ``src/metacrucible/benchmark.py`` and expose at least:

  - ``load_benchmark(path)`` returning a structured result with
    metadata, cases, eligible eval / held-out cases, pending
    generated cases, disabled cases, and a ``blockers`` list.
  - ``migrate_benchmark(path, *, dry_run=True)`` returning a
    migration plan that the caller can review before applying.

The blockers list carries stable snake_case ids drawn from the
fixed small machine-stable set pinned by ADR 0029:

  - ``duplicate-case-id``              - two case records share a ``case_id``
  - ``schema-version-mismatch``        - the on-disk schema_version is not v1
  - ``missing-reviewed-eval-case``     - no eligible reviewed eval case
  - ``missing-reviewed-held-out-case`` - no eligible reviewed held-out case
  - ``pending-generated-case``         - a generated case blocks optimize

References
----------
- ADR 0018 (build benchmarks from reviewed / generated cases).
- ADR 0025 (use reviewed JSONL benchmarks with eval / held-out splits).
- ADR 0029 (pin benchmark JSONL v1 schema).
- ADR 0035 (init is noninteractive; bootstrap / optimize gates on
  reviewed eval + held-out coverage).
- Issue #7 acceptance criteria.
"""
from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCHMARK_MODULE = "metacrucible.benchmark"
BENCHMARK_FILE_NAME = "benchmark.jsonl"

#: Current on-disk schema version pinned by ADR 0029 ("Benchmark v1").
BENCHMARK_SCHEMA_VERSION = 1

#: Stable blocker ids from ADR 0029's "fixed small machine-stable set"
#: of invalid benchmark blocker codes. The exact strings are the
#: machine contract: tests assert on them verbatim so CI and
#: downstream automation can branch on them.
DUPLICATE_CASE_ID_BLOCKER = "duplicate-case-id"
SCHEMA_VERSION_MISMATCH_BLOCKER = "schema-version-mismatch"
MISSING_REVIEWED_EVAL_BLOCKER = "missing-reviewed-eval-case"
MISSING_REVIEWED_HELD_OUT_BLOCKER = "missing-reviewed-held-out-case"
PENDING_GENERATED_BLOCKER = "pending-generated-case"

#: Status values pinned by ADR 0018 / ADR 0029.
STATUS_REVIEWED = "reviewed"
STATUS_GENERATED = "generated"
STATUS_DISABLED = "disabled"

#: Split values pinned by ADR 0025 / ADR 0029.
SPLIT_EVAL = "eval"
SPLIT_HELD_OUT = "held_out"


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def benchmark_mod() -> Any:
    """Import the benchmark module; the test fails (red step) if it does not exist.

    Mirrors the storage-test red-step pattern: the issue under test
    has not yet landed, so the import raises ``ModuleNotFoundError``;
    the test surfaces that as a clean failure pinned to the missing
    module name (not as an opaque traceback).
    """
    try:
        return importlib.import_module(BENCHMARK_MODULE)
    except ModuleNotFoundError as exc:
        pytest.fail(
            f"benchmark module {BENCHMARK_MODULE!r} is not implemented yet "
            f"(Issue #7 red step). Expected at least: load_benchmark, "
            f"migrate_benchmark. ImportError: {exc}"
        )


def _write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> Path:
    """Write ``records`` as one JSON object per line at ``path``.

    Convenience helper for fixture builders. Lines are written in
    caller-supplied order; case ordering is part of the loader's
    contract (some callers rely on JSONL line order matching the
    reviewer's authoring order).
    """
    lines = [json.dumps(dict(rec), sort_keys=True) for rec in records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _metadata_record(
    *,
    name: str = "default-benchmark",
    schema_version: int = BENCHMARK_SCHEMA_VERSION,
    **extras: Any,
) -> dict[str, Any]:
    """Build a minimal valid metadata record (ADR 0029).

    The ``record_type`` discriminator is the loader's branch point;
    the ``schema_version`` is the migration gate. Extra fields are
    forwarded verbatim so individual tests can add their own
    benchmark-level keys.
    """
    record: dict[str, Any] = {
        "record_type": "metadata",
        "name": name,
        "schema_version": schema_version,
    }
    record.update(extras)
    return record


def _case_record(
    case_id: str,
    *,
    status: str = STATUS_REVIEWED,
    split: str = SPLIT_EVAL,
    **extras: Any,
) -> dict[str, Any]:
    """Build a minimal valid case record (ADR 0029).

    ``case_id`` must be unique within the benchmark; ``status`` and
    ``split`` default to the reviewed-eval shape, which is the
    simplest eligible case. Extra fields are forwarded verbatim so
    individual tests can add input, execution_boundary, checks, or
    judgments.
    """
    record: dict[str, Any] = {
        "record_type": "case",
        "case_id": case_id,
        "status": status,
        "split": split,
    }
    record.update(extras)
    return record


def _minimal_eligible_case(
    case_id: str,
    *,
    split: str = SPLIT_EVAL,
) -> dict[str, Any]:
    """Build the smallest valid reviewed case record (ADR 0029).

    A reviewed case must provide input, execution boundary, and at
    least one deterministic check or non-deterministic judgment
    (ADR 0029: "Each eligible case must provide input, execution
    boundary, and at least one deterministic check or non-deterministic
    judgment; judgments must name rubric and pass condition rather
    than relying on bare scores."). The minimal helper stamps the
    three required keys with non-empty values.
    """
    return _case_record(
        case_id,
        status=STATUS_REVIEWED,
        split=split,
        input={"prompt": "do the thing"},
        execution_boundary={"permissions": ["read"]},
        checks=[{"name": "output_contains_thing", "pattern": "thing"}],
    )


def _blocker_ids(result: Any) -> list[str]:
    """Extract a list of blocker ids from a load result.

    Accepts both ``[{"id": "..."}]`` and bare string lists so the
    test does not over-constrain the blocker shape (the ADR pins
    the ids, not the field name).
    """
    blockers = getattr(result, "blockers", None)
    if blockers is None and isinstance(result, dict):
        blockers = result.get("blockers", [])
    if not blockers:
        return []
    ids: list[str] = []
    for entry in blockers:
        if isinstance(entry, str):
            ids.append(entry)
        elif isinstance(entry, dict):
            bid = entry.get("id") or entry.get("code")
            if isinstance(bid, str):
                ids.append(bid)
    return ids


# --------------------------------------------------------------------------- #
# AC1 — Module + public API surface                                            #
# --------------------------------------------------------------------------- #


def test_benchmark_module_is_importable(benchmark_mod: Any) -> None:
    """``metacrucible.benchmark`` must be importable.

    The red step: today this raises ``ModuleNotFoundError`` and the
    test fails for the right reason. Once Issue #7 lands, the
    module exists and the import returns the module object.
    """
    assert benchmark_mod is not None, (
        f"importing {BENCHMARK_MODULE!r} returned None"
    )


def test_benchmark_module_exposes_load_function(benchmark_mod: Any) -> None:
    """The module must expose a ``load_benchmark`` function.

    ADR 0029 pins the loader as the canonical read path for
    benchmark files; the rest of the pipeline (bootstrap / evaluate
    / optimize / synthesize) branches on its return value.
    """
    assert hasattr(benchmark_mod, "load_benchmark"), (
        f"{BENCHMARK_MODULE!r} must expose load_benchmark; "
        f"got attributes {sorted(dir(benchmark_mod))!r}"
    )
    assert callable(benchmark_mod.load_benchmark), (
        f"{BENCHMARK_MODULE!r}.load_benchmark must be callable; "
        f"got type {type(benchmark_mod.load_benchmark).__name__}"
    )


def test_benchmark_module_exposes_migrate_function(benchmark_mod: Any) -> None:
    """The module must expose a ``migrate_benchmark`` function.

    ADR 0029: schema changes are never applied implicitly; explicit
    migration is provided as a dry-run-first command. The
    ``migrate_benchmark`` entry point is the contract surface.
    """
    assert hasattr(benchmark_mod, "migrate_benchmark"), (
        f"{BENCHMARK_MODULE!r} must expose migrate_benchmark; "
        f"got attributes {sorted(dir(benchmark_mod))!r}"
    )
    assert callable(benchmark_mod.migrate_benchmark), (
        f"{BENCHMARK_MODULE!r}.migrate_benchmark must be callable; "
        f"got type {type(benchmark_mod.migrate_benchmark).__name__}"
    )


# --------------------------------------------------------------------------- #
# AC2 — Metadata record is the first line (ADR 0029)                          #
# --------------------------------------------------------------------------- #


def test_load_benchmark_returns_metadata_record(
    benchmark_mod: Any, tmp_path: Path
) -> None:
    """``load_benchmark`` must surface the metadata record.

    ADR 0029: the first JSONL record is the benchmark-level
    ``metadata`` record. The loader exposes it as a structured
    field so downstream commands can branch on benchmark-level
    settings (name, schema_version, etc.) without re-reading the
    file.
    """
    benchmark = tmp_path / BENCHMARK_FILE_NAME
    _write_jsonl(
        benchmark,
        [
            _metadata_record(name="my-benchmark"),
            _minimal_eligible_case("case-1", split=SPLIT_EVAL),
            _minimal_eligible_case("case-2", split=SPLIT_HELD_OUT),
        ],
    )
    result = benchmark_mod.load_benchmark(benchmark)
    metadata = getattr(result, "metadata", None)
    if metadata is None and isinstance(result, dict):
        metadata = result.get("metadata")
    assert metadata is not None, (
        f"load_benchmark result must expose the metadata record; "
        f"got result type {type(result).__name__}"
    )
    assert isinstance(metadata, dict), (
        f"metadata must be a dict; got {type(metadata).__name__}"
    )
    assert metadata.get("name") == "my-benchmark", (
        f"metadata.name must round-trip; got {metadata!r}"
    )


def test_load_benchmark_records_schema_version(
    benchmark_mod: Any, tmp_path: Path
) -> None:
    """The metadata record's ``schema_version`` must be readable by the loader.

    ADR 0029: the schema_version on the metadata record is the
    migration gate. A loader that swallows the version field
    cannot decide whether to block on a future / incompatible
    benchmark.
    """
    benchmark = tmp_path / BENCHMARK_FILE_NAME
    _write_jsonl(
        benchmark,
        [
            _metadata_record(schema_version=BENCHMARK_SCHEMA_VERSION),
            _minimal_eligible_case("case-1"),
        ],
    )
    result = benchmark_mod.load_benchmark(benchmark)
    metadata = getattr(result, "metadata", None)
    if metadata is None and isinstance(result, dict):
        metadata = result.get("metadata")
    assert metadata is not None, "metadata must be present on the load result"
    assert metadata.get("schema_version") == BENCHMARK_SCHEMA_VERSION, (
        f"metadata.schema_version must equal v{BENCHMARK_SCHEMA_VERSION}; "
        f"got {metadata.get('schema_version')!r} (full metadata={metadata!r})"
    )


# --------------------------------------------------------------------------- #
# AC3 — Status + split filtering for reviewed / generated / disabled cases     #
# --------------------------------------------------------------------------- #


def test_reviewed_eval_case_is_eligible(benchmark_mod: Any, tmp_path: Path) -> None:
    """A reviewed eval case must appear in the eligible-eval set.

    ADR 0018 / ADR 0025 / ADR 0029: reviewed cases are the only
    cases eligible for baseline, evaluation, and optimization.
    A loader that drops reviewed cases from the eligible set
    breaks the entire pipeline.
    """
    benchmark = tmp_path / BENCHMARK_FILE_NAME
    _write_jsonl(
        benchmark,
        [
            _metadata_record(),
            _minimal_eligible_case("eval-1", split=SPLIT_EVAL),
        ],
    )
    result = benchmark_mod.load_benchmark(benchmark)
    eligible = getattr(result, "eligible_eval_cases", None)
    if eligible is None and isinstance(result, dict):
        eligible = result.get("eligible_eval_cases")
    assert eligible is not None, (
        f"load_benchmark must expose an eligible_eval_cases field; "
        f"got result type {type(result).__name__}"
    )
    ids = [c.get("case_id") for c in eligible if isinstance(c, dict)]
    assert "eval-1" in ids, (
        f"reviewed eval case must be in eligible_eval_cases; "
        f"got eligible={eligible!r}"
    )


def test_reviewed_held_out_case_is_eligible(
    benchmark_mod: Any, tmp_path: Path
) -> None:
    """A reviewed held-out case must appear in the eligible-held-out set.

    ADR 0025: the held-out split is the regression guard for
    optimization. A loader that puts held-out cases in the eval
    set (or drops them) breaks acceptance.
    """
    benchmark = tmp_path / BENCHMARK_FILE_NAME
    _write_jsonl(
        benchmark,
        [
            _metadata_record(),
            _minimal_eligible_case("ho-1", split=SPLIT_HELD_OUT),
        ],
    )
    result = benchmark_mod.load_benchmark(benchmark)
    eligible = getattr(result, "eligible_held_out_cases", None)
    if eligible is None and isinstance(result, dict):
        eligible = result.get("eligible_held_out_cases")
    assert eligible is not None, (
        f"load_benchmark must expose an eligible_held_out_cases field; "
        f"got result type {type(result).__name__}"
    )
    ids = [c.get("case_id") for c in eligible if isinstance(c, dict)]
    assert "ho-1" in ids, (
        f"reviewed held-out case must be in eligible_held_out_cases; "
        f"got eligible={eligible!r}"
    )


def test_generated_case_is_pending_not_eligible(
    benchmark_mod: Any, tmp_path: Path
) -> None:
    """Generated cases must NOT be eligible; they remain pending review.

    ADR 0018: "Generated cases can be stored for audit and future
    use, but they cannot drive optimization until a human reviewer
    promotes them to ``status:'reviewed'``." A loader that treats
    generated cases as eligible short-circuits the review gate.
    """
    benchmark = tmp_path / BENCHMARK_FILE_NAME
    _write_jsonl(
        benchmark,
        [
            _metadata_record(),
            _case_record(
                "gen-1",
                status=STATUS_GENERATED,
                split=SPLIT_EVAL,
                input={"prompt": "x"},
                execution_boundary={"permissions": ["read"]},
                checks=[{"name": "ok", "pattern": "ok"}],
            ),
        ],
    )
    result = benchmark_mod.load_benchmark(benchmark)
    eligible = getattr(result, "eligible_eval_cases", None)
    if eligible is None and isinstance(result, dict):
        eligible = result.get("eligible_eval_cases", [])
    ids = [c.get("case_id") for c in (eligible or []) if isinstance(c, dict)]
    assert "gen-1" not in ids, (
        f"generated case must NOT be in eligible_eval_cases; "
        f"got eligible={eligible!r}"
    )


def test_disabled_case_is_ignored(benchmark_mod: Any, tmp_path: Path) -> None:
    """Disabled cases must NOT be eligible; they remain in the file for audit.

    ADR 0029: "disabled cases are ignored by eligibility checks."
    A loader that drops or promotes disabled cases breaks the
    audit trail and may silently re-enable dead work.
    """
    benchmark = tmp_path / BENCHMARK_FILE_NAME
    _write_jsonl(
        benchmark,
        [
            _metadata_record(),
            _case_record(
                "dis-1",
                status=STATUS_DISABLED,
                split=SPLIT_EVAL,
                input={"prompt": "x"},
                execution_boundary={"permissions": ["read"]},
                checks=[{"name": "ok", "pattern": "ok"}],
            ),
        ],
    )
    result = benchmark_mod.load_benchmark(benchmark)
    eligible_eval = getattr(result, "eligible_eval_cases", None)
    if eligible_eval is None and isinstance(result, dict):
        eligible_eval = result.get("eligible_eval_cases", [])
    eligible_held_out = getattr(result, "eligible_held_out_cases", None)
    if eligible_held_out is None and isinstance(result, dict):
        eligible_held_out = result.get("eligible_held_out_cases", [])
    all_eligible_ids = [
        c.get("case_id")
        for c in (eligible_eval or []) + (eligible_held_out or [])
        if isinstance(c, dict)
    ]
    assert "dis-1" not in all_eligible_ids, (
        f"disabled case must NOT be eligible; "
        f"got eligible_eval={eligible_eval!r} eligible_held_out={eligible_held_out!r}"
    )


def test_pending_generated_case_is_surfaced(
    benchmark_mod: Any, tmp_path: Path
) -> None:
    """Generated cases must be surfaced in a pending (non-eligible) view.

    The loader must not silently swallow generated cases: the
    bootstrap / inspect commands need a stable way to count
    pending review work.
    """
    benchmark = tmp_path / BENCHMARK_FILE_NAME
    _write_jsonl(
        benchmark,
        [
            _metadata_record(),
            _minimal_eligible_case("eval-1", split=SPLIT_EVAL),
            _minimal_eligible_case("ho-1", split=SPLIT_HELD_OUT),
            _case_record(
                "gen-1",
                status=STATUS_GENERATED,
                split=SPLIT_EVAL,
                input={"prompt": "x"},
                execution_boundary={"permissions": ["read"]},
                checks=[{"name": "ok", "pattern": "ok"}],
            ),
        ],
    )
    result = benchmark_mod.load_benchmark(benchmark)
    pending = getattr(result, "pending_generated_cases", None)
    if pending is None and isinstance(result, dict):
        pending = result.get("pending_generated_cases")
    assert pending is not None, (
        f"load_benchmark must expose a pending_generated_cases field; "
        f"got result type {type(result).__name__}"
    )
    ids = [c.get("case_id") for c in pending if isinstance(c, dict)]
    assert "gen-1" in ids, (
        f"generated case must be in pending_generated_cases; "
        f"got pending={pending!r}"
    )


def test_mixed_status_benchmark_partitions_correctly(
    benchmark_mod: Any, tmp_path: Path
) -> None:
    """A benchmark with all three statuses must partition cleanly.

    One reviewed eval, one reviewed held-out, one generated, one
    disabled. Each case must land in exactly one of the three
    buckets the loader exposes; no case is dropped or duplicated.
    """
    benchmark = tmp_path / BENCHMARK_FILE_NAME
    _write_jsonl(
        benchmark,
        [
            _metadata_record(),
            _minimal_eligible_case("eval-1", split=SPLIT_EVAL),
            _minimal_eligible_case("ho-1", split=SPLIT_HELD_OUT),
            _case_record(
                "gen-1",
                status=STATUS_GENERATED,
                split=SPLIT_EVAL,
                input={"prompt": "x"},
                execution_boundary={"permissions": ["read"]},
                checks=[{"name": "ok", "pattern": "ok"}],
            ),
            _case_record(
                "dis-1",
                status=STATUS_DISABLED,
                split=SPLIT_EVAL,
                input={"prompt": "x"},
                execution_boundary={"permissions": ["read"]},
                checks=[{"name": "ok", "pattern": "ok"}],
            ),
        ],
    )
    result = benchmark_mod.load_benchmark(benchmark)
    eval_ids = {
        c.get("case_id")
        for c in (
            getattr(result, "eligible_eval_cases", None)
            or (result.get("eligible_eval_cases") if isinstance(result, dict) else [])
        )
        if isinstance(c, dict)
    }
    held_out_ids = {
        c.get("case_id")
        for c in (
            getattr(result, "eligible_held_out_cases", None)
            or (
                result.get("eligible_held_out_cases")
                if isinstance(result, dict)
                else []
            )
        )
        if isinstance(c, dict)
    }
    pending_ids = {
        c.get("case_id")
        for c in (
            getattr(result, "pending_generated_cases", None)
            or (
                result.get("pending_generated_cases")
                if isinstance(result, dict)
                else []
            )
        )
        if isinstance(c, dict)
    }
    # No case is in two buckets; the reviewed ones are eligible; the
    # generated one is pending; the disabled one is in none of the
    # three.
    assert "eval-1" in eval_ids, (
        f"reviewed eval case must be in eligible_eval_cases; got {eval_ids!r}"
    )
    assert "ho-1" in held_out_ids, (
        f"reviewed held-out case must be in eligible_held_out_cases; "
        f"got {held_out_ids!r}"
    )
    assert "gen-1" in pending_ids, (
        f"generated case must be in pending_generated_cases; "
        f"got {pending_ids!r}"
    )
    # Disabled must not appear in any eligible / pending bucket.
    for bucket, label in (
        (eval_ids, "eligible_eval_cases"),
        (held_out_ids, "eligible_held_out_cases"),
        (pending_ids, "pending_generated_cases"),
    ):
        assert "dis-1" not in bucket, (
            f"disabled case must not be in {label}; got {bucket!r}"
        )


# --------------------------------------------------------------------------- #
# AC4 — Duplicate case_id is a hard blocker (ADR 0029)                         #
# --------------------------------------------------------------------------- #


def test_duplicate_case_id_surfaces_blocker(
    benchmark_mod: Any, tmp_path: Path
) -> None:
    """Two case records sharing a ``case_id`` must produce a duplicate-id blocker.

    ADR 0029: duplicate id is one of the fixed small machine-stable
    set of invalid benchmark blocker codes. The loader must
    surface it (rather than silently keeping one of the two
    records) so a benchmark with conflicting cases fails loud.
    """
    benchmark = tmp_path / BENCHMARK_FILE_NAME
    _write_jsonl(
        benchmark,
        [
            _metadata_record(),
            _minimal_eligible_case("dup-1", split=SPLIT_EVAL),
            _minimal_eligible_case("dup-1", split=SPLIT_HELD_OUT),
        ],
    )
    result = benchmark_mod.load_benchmark(benchmark)
    assert DUPLICATE_CASE_ID_BLOCKER in _blocker_ids(result), (
        f"duplicate case_id must surface {DUPLICATE_CASE_ID_BLOCKER!r} "
        f"blocker (ADR 0029); got blockers="
        f"{getattr(result, 'blockers', None) or (result.get('blockers') if isinstance(result, dict) else None)!r}"
    )


def test_duplicate_case_id_does_not_silently_pass_through(
    benchmark_mod: Any, tmp_path: Path
) -> None:
    """A benchmark with a duplicate id must not be optimizable.

    The optimize runnability gate (AC6) must refuse a benchmark
    that has the duplicate-id blocker. Pinning this on the loader
    result keeps the contract local — downstream commands can ask
    the result "are you runnable?" instead of re-deriving it.
    """
    benchmark = tmp_path / BENCHMARK_FILE_NAME
    _write_jsonl(
        benchmark,
        [
            _metadata_record(),
            _minimal_eligible_case("dup-1", split=SPLIT_EVAL),
            _minimal_eligible_case("dup-1", split=SPLIT_HELD_OUT),
        ],
    )
    result = benchmark_mod.load_benchmark(benchmark)
    runnable = getattr(result, "is_optimize_runnable", None)
    if runnable is None and isinstance(result, dict):
        runnable = result.get("is_optimize_runnable")
    assert runnable is False, (
        f"benchmark with duplicate case_id must NOT be optimize-runnable; "
        f"got is_optimize_runnable={runnable!r} "
        f"blockers={getattr(result, 'blockers', None) or (result.get('blockers') if isinstance(result, dict) else None)!r}"
    )


# --------------------------------------------------------------------------- #
# AC5 — Schema version mismatch is a blocker; dry-run migration is offered     #
# --------------------------------------------------------------------------- #


def test_newer_schema_version_surfaces_blocker(
    benchmark_mod: Any, tmp_path: Path
) -> None:
    """An on-disk ``schema_version`` newer than v1 must surface a blocker.

    ADR 0029: "incompatible or newer versions block." The loader
    must refuse to silently coerce a v2 (or any unknown future)
    benchmark into the v1 shape; that would lose or misinterpret
    fields that the v1 loader does not know about.
    """
    benchmark = tmp_path / BENCHMARK_FILE_NAME
    _write_jsonl(
        benchmark,
        [
            _metadata_record(schema_version=2),
            _minimal_eligible_case("eval-1", split=SPLIT_EVAL),
            _minimal_eligible_case("ho-1", split=SPLIT_HELD_OUT),
        ],
    )
    result = benchmark_mod.load_benchmark(benchmark)
    assert SCHEMA_VERSION_MISMATCH_BLOCKER in _blocker_ids(result), (
        f"newer schema_version must surface "
        f"{SCHEMA_VERSION_MISMATCH_BLOCKER!r} blocker (ADR 0029); "
        f"got blockers="
        f"{getattr(result, 'blockers', None) or (result.get('blockers') if isinstance(result, dict) else None)!r}"
    )


def test_migrate_benchmark_default_is_dry_run(
    benchmark_mod: Any, tmp_path: Path
) -> None:
    """``migrate_benchmark`` must default to ``dry_run=True`` and never write.

    ADR 0029: "explicit migration may be provided as a dry-run-first
    command." The default path must be safe: callers explicitly
    opt in to writing the migrated file. A loader that migrates
    on first load violates the "never applied implicitly" contract.
    """
    benchmark = tmp_path / BENCHMARK_FILE_NAME
    _write_jsonl(
        benchmark,
        [
            _metadata_record(schema_version=2),
            _minimal_eligible_case("eval-1", split=SPLIT_EVAL),
        ],
    )
    before_mtime = benchmark.stat().st_mtime
    before_bytes = benchmark.read_bytes()
    # Default invocation: dry-run, no on-disk change.
    plan = benchmark_mod.migrate_benchmark(benchmark)
    assert plan is not None, (
        f"migrate_benchmark must return a plan object; got None"
    )
    after_bytes = benchmark.read_bytes()
    after_mtime = benchmark.stat().st_mtime
    assert after_bytes == before_bytes, (
        f"default migrate_benchmark must not mutate the on-disk benchmark "
        f"(dry-run is the default per ADR 0029); "
        f"file changed: {before_bytes!r} -> {after_bytes!r}"
    )
    assert after_mtime == before_mtime, (
        f"default migrate_benchmark must not change mtime; "
        f"got {before_mtime} -> {after_mtime}"
    )


def test_migrate_benchmark_explicit_dry_run_true_is_safe(
    benchmark_mod: Any, tmp_path: Path
) -> None:
    """``migrate_benchmark(path, dry_run=True)`` must never write the file.

    Pinning the explicit form: the keyword argument is the public
    contract, the default is just a convenience over the same
    safe behavior.
    """
    benchmark = tmp_path / BENCHMARK_FILE_NAME
    _write_jsonl(
        benchmark,
        [
            _metadata_record(schema_version=2),
            _minimal_eligible_case("eval-1", split=SPLIT_EVAL),
        ],
    )
    before_bytes = benchmark.read_bytes()
    benchmark_mod.migrate_benchmark(benchmark, dry_run=True)
    after_bytes = benchmark.read_bytes()
    assert after_bytes == before_bytes, (
        f"migrate_benchmark(dry_run=True) must never write the file; "
        f"file changed"
    )


def test_migrate_benchmark_dry_run_describes_plan(
    benchmark_mod: Any, tmp_path: Path
) -> None:
    """A dry-run migration plan must describe what would change.

    The plan is the only signal a caller has to decide whether to
    apply the migration. At minimum it must carry enough
    information for a human reviewer to spot a destructive
    change (e.g. an ``applied`` flag, a list of changes, or an
    explicit "no change needed" outcome).
    """
    benchmark = tmp_path / BENCHMARK_FILE_NAME
    _write_jsonl(
        benchmark,
        [
            _metadata_record(schema_version=2),
            _minimal_eligible_case("eval-1", split=SPLIT_EVAL),
        ],
    )
    plan = benchmark_mod.migrate_benchmark(benchmark, dry_run=True)
    # Plan must be a structured object (dict or dataclass), not a bare bool.
    if isinstance(plan, bool):
        pytest.fail(
            f"migrate_benchmark dry-run must return a structured plan "
            f"(dict / dataclass with changes + applied flag); got bare bool"
        )
    plan_dict: dict[str, Any]
    if hasattr(plan, "__dict__") and not isinstance(plan, dict):
        plan_dict = vars(plan)
    else:
        plan_dict = plan if isinstance(plan, dict) else {}
    # The plan must distinguish "would change" from "did change" so
    # callers cannot accidentally treat a dry-run as an apply.
    assert "applied" in plan_dict, (
        f"migration plan must carry an 'applied' flag; got keys "
        f"{sorted(plan_dict.keys())!r}"
    )
    assert plan_dict["applied"] is False, (
        f"dry-run plan must have applied=False; got {plan_dict['applied']!r}"
    )


# --------------------------------------------------------------------------- #
# AC6 — Optimize runnability requires reviewed eval AND held-out coverage      #
# --------------------------------------------------------------------------- #


def test_optimize_runnable_with_reviewed_eval_and_held_out(
    benchmark_mod: Any, tmp_path: Path
) -> None:
    """A benchmark with one reviewed eval + one reviewed held-out is runnable.

    ADR 0025: baseline creation requires at least one human-reviewed
    eval case and one human-reviewed held-out case. The loader
    surfaces this as ``is_optimize_runnable == True`` so the
    ``optimize`` command can gate on it without re-implementing
    the rule.
    """
    benchmark = tmp_path / BENCHMARK_FILE_NAME
    _write_jsonl(
        benchmark,
        [
            _metadata_record(),
            _minimal_eligible_case("eval-1", split=SPLIT_EVAL),
            _minimal_eligible_case("ho-1", split=SPLIT_HELD_OUT),
        ],
    )
    result = benchmark_mod.load_benchmark(benchmark)
    runnable = getattr(result, "is_optimize_runnable", None)
    if runnable is None and isinstance(result, dict):
        runnable = result.get("is_optimize_runnable")
    assert runnable is True, (
        f"benchmark with reviewed eval + held-out must be optimize-runnable "
        f"(ADR 0025); got is_optimize_runnable={runnable!r} "
        f"blockers={getattr(result, 'blockers', None) or (result.get('blockers') if isinstance(result, dict) else None)!r}"
    )


def test_optimize_not_runnable_with_no_reviewed_eval(
    benchmark_mod: Any, tmp_path: Path
) -> None:
    """A benchmark missing a reviewed eval case must not be optimize-runnable.

    ADR 0025: optimization needs a selection signal. The loader
    blocks the run with the ``missing-reviewed-eval-case`` blocker
    and flips ``is_optimize_runnable`` to False.
    """
    benchmark = tmp_path / BENCHMARK_FILE_NAME
    _write_jsonl(
        benchmark,
        [
            _metadata_record(),
            _minimal_eligible_case("ho-1", split=SPLIT_HELD_OUT),
        ],
    )
    result = benchmark_mod.load_benchmark(benchmark)
    runnable = getattr(result, "is_optimize_runnable", None)
    if runnable is None and isinstance(result, dict):
        runnable = result.get("is_optimize_runnable")
    assert runnable is False, (
        f"benchmark with no reviewed eval case must NOT be optimize-runnable; "
        f"got is_optimize_runnable={runnable!r} "
        f"blockers={getattr(result, 'blockers', None) or (result.get('blockers') if isinstance(result, dict) else None)!r}"
    )
    assert MISSING_REVIEWED_EVAL_BLOCKER in _blocker_ids(result), (
        f"missing reviewed eval case must surface "
        f"{MISSING_REVIEWED_EVAL_BLOCKER!r} blocker (ADR 0029); "
        f"got blockers="
        f"{getattr(result, 'blockers', None) or (result.get('blockers') if isinstance(result, dict) else None)!r}"
    )


def test_optimize_not_runnable_with_no_reviewed_held_out(
    benchmark_mod: Any, tmp_path: Path
) -> None:
    """A benchmark missing a reviewed held-out case must not be optimize-runnable.

    ADR 0025: acceptance needs a regression guard. The loader
    blocks the run with the ``missing-reviewed-held-out-case``
    blocker.
    """
    benchmark = tmp_path / BENCHMARK_FILE_NAME
    _write_jsonl(
        benchmark,
        [
            _metadata_record(),
            _minimal_eligible_case("eval-1", split=SPLIT_EVAL),
        ],
    )
    result = benchmark_mod.load_benchmark(benchmark)
    runnable = getattr(result, "is_optimize_runnable", None)
    if runnable is None and isinstance(result, dict):
        runnable = result.get("is_optimize_runnable")
    assert runnable is False, (
        f"benchmark with no reviewed held-out case must NOT be optimize-runnable; "
        f"got is_optimize_runnable={runnable!r} "
        f"blockers={getattr(result, 'blockers', None) or (result.get('blockers') if isinstance(result, dict) else None)!r}"
    )
    assert MISSING_REVIEWED_HELD_OUT_BLOCKER in _blocker_ids(result), (
        f"missing reviewed held-out case must surface "
        f"{MISSING_REVIEWED_HELD_OUT_BLOCKER!r} blocker (ADR 0029); "
        f"got blockers="
        f"{getattr(result, 'blockers', None) or (result.get('blockers') if isinstance(result, dict) else None)!r}"
    )


def test_optimize_not_runnable_with_only_generated_cases(
    benchmark_mod: Any, tmp_path: Path
) -> None:
    """A benchmark with only generated cases must not be optimize-runnable.

    The pending-generated-cases gate is what stops bootstrap from
    accidentally entering optimization (F2 acceptance). A loader
    that treats generated cases as eligible bypasses the human
    review that ADR 0018 mandates.
    """
    benchmark = tmp_path / BENCHMARK_FILE_NAME
    _write_jsonl(
        benchmark,
        [
            _metadata_record(),
            _case_record(
                "gen-1",
                status=STATUS_GENERATED,
                split=SPLIT_EVAL,
                input={"prompt": "x"},
                execution_boundary={"permissions": ["read"]},
                checks=[{"name": "ok", "pattern": "ok"}],
            ),
            _case_record(
                "gen-2",
                status=STATUS_GENERATED,
                split=SPLIT_HELD_OUT,
                input={"prompt": "x"},
                execution_boundary={"permissions": ["read"]},
                checks=[{"name": "ok", "pattern": "ok"}],
            ),
        ],
    )
    result = benchmark_mod.load_benchmark(benchmark)
    runnable = getattr(result, "is_optimize_runnable", None)
    if runnable is None and isinstance(result, dict):
        runnable = result.get("is_optimize_runnable")
    assert runnable is False, (
        f"benchmark with only generated cases must NOT be optimize-runnable; "
        f"got is_optimize_runnable={runnable!r} "
        f"blockers={getattr(result, 'blockers', None) or (result.get('blockers') if isinstance(result, dict) else None)!r}"
    )
    # The pending-generated-case blocker is the explicit signal a
    # bootstrap-style command uses to refuse and point the user at
    # the review path. Both the split blockers and the pending
    # blocker must be present so the user sees the full picture.
    blockers = _blocker_ids(result)
    assert PENDING_GENERATED_BLOCKER in blockers or any(
        b in blockers
        for b in (MISSING_REVIEWED_EVAL_BLOCKER, MISSING_REVIEWED_HELD_OUT_BLOCKER)
    ), (
        f"all-generated benchmark must surface either the pending-generated-case "
        f"blocker or the missing-reviewed-{['eval-case', 'held-out-case']} blockers; "
        f"got blockers={blockers!r}"
    )


# --------------------------------------------------------------------------- #
# AC7 — Loader integrates with the ``init`` workspace envelope                  #
# --------------------------------------------------------------------------- #


def _run_metacrucible(
    argv: list[str], *, cwd: Path
) -> subprocess.CompletedProcess[str]:
    """Invoke ``python -m metacrucible`` with ``argv`` inside ``cwd``.

    Both stdout and stderr are captured as text so the test can
    distinguish argparse errors (stderr) from the command's own
    human output (stdout).
    """
    return subprocess.run(
        [sys.executable, "-m", "metacrucible", *argv],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


def test_init_then_load_benchmark_round_trips(
    benchmark_mod: Any, tmp_path: Path
) -> None:
    """A benchmark file written by ``init`` must load cleanly.

    ``init`` stamps an empty-but-valid benchmark container (ADR
    0025: empty benchmarks are valid containers, not runnable
    benchmarks). The loader must accept that shape — at minimum
    by returning a result with the metadata record — so the rest
    of the pipeline can build on it.
    """
    workspace = tmp_path / "ws-load-roundtrip"
    workspace.mkdir(parents=True, exist_ok=True)
    init = _run_metacrucible(["init", str(workspace)], cwd=REPO_ROOT)
    assert init.returncode == 0, (
        f"`metacrucible init` must exit 0 before the loader test; "
        f"got rc={init.returncode} stderr={init.stderr!r}"
    )
    benchmark = workspace / BENCHMARK_FILE_NAME
    assert benchmark.is_file(), (
        f"init must create the benchmark container; got workspace contents: "
        f"{sorted(p.name for p in workspace.iterdir())!r}"
    )
    # The init-produced empty benchmark has only a metadata record;
    # the loader must accept that as a valid (if not optimize-runnable)
    # benchmark.
    result = benchmark_mod.load_benchmark(benchmark)
    metadata = getattr(result, "metadata", None)
    if metadata is None and isinstance(result, dict):
        metadata = result.get("metadata")
    assert metadata is not None, (
        f"loader must surface the metadata record from an init-produced "
        f"benchmark; got result type {type(result).__name__}"
    )
    # An empty benchmark is not optimize-runnable (ADR 0025):
    # the runnability gate must fire because both split blockers
    # apply.
    runnable = getattr(result, "is_optimize_runnable", None)
    if runnable is None and isinstance(result, dict):
        runnable = result.get("is_optimize_runnable")
    assert runnable is False, (
        f"init-produced empty benchmark must NOT be optimize-runnable "
        f"(ADR 0025); got is_optimize_runnable={runnable!r}"
    )


# --------------------------------------------------------------------------- #
# AC8 — Eligible cases preserve JSONL authoring order                          #
# --------------------------------------------------------------------------- #


def test_eligible_cases_preserve_authoring_order(
    benchmark_mod: Any, tmp_path: Path
) -> None:
    """The loader must preserve JSONL line order in the eligible buckets.

    The benchmark's authoring order is part of the audit trail:
    the same order is replayed during evaluation and surfaced in
    the evidence bundle. A loader that resorts (e.g. by case_id)
    breaks the link between the benchmark file and the receipt.
    """
    benchmark = tmp_path / BENCHMARK_FILE_NAME
    _write_jsonl(
        benchmark,
        [
            _metadata_record(),
            _minimal_eligible_case("eval-c", split=SPLIT_EVAL),
            _minimal_eligible_case("eval-a", split=SPLIT_EVAL),
            _minimal_eligible_case("eval-b", split=SPLIT_EVAL),
            _minimal_eligible_case("ho-z", split=SPLIT_HELD_OUT),
            _minimal_eligible_case("ho-a", split=SPLIT_HELD_OUT),
        ],
    )
    result = benchmark_mod.load_benchmark(benchmark)
    eval_ids = [
        c.get("case_id")
        for c in (
            getattr(result, "eligible_eval_cases", None)
            or (result.get("eligible_eval_cases") if isinstance(result, dict) else [])
        )
        if isinstance(c, dict)
    ]
    held_out_ids = [
        c.get("case_id")
        for c in (
            getattr(result, "eligible_held_out_cases", None)
            or (
                result.get("eligible_held_out_cases")
                if isinstance(result, dict)
                else []
            )
        )
        if isinstance(c, dict)
    ]
    assert eval_ids == ["eval-c", "eval-a", "eval-b"], (
        f"eligible_eval_cases must preserve JSONL authoring order; "
        f"got {eval_ids!r}"
    )
    assert held_out_ids == ["ho-z", "ho-a"], (
        f"eligible_held_out_cases must preserve JSONL authoring order; "
        f"got {held_out_ids!r}"
    )
