"""CLI tests for the ``optimize`` subcommand (Issue #30, PRD F3).

Pins the MVP sentinel-gate behavior of
``metacrucible optimize <workspace>``:

  - The subcommand is recognized by argparse (no "unrecognized
    arguments" error from ``optimize --help``).
  - With a benchmark that has generated (pending-review) cases
    or the literal ``BOOTSTRAP_PENDING_REVIEW`` sentinel, the
    optimize command refuses to start with a stable
    ``bootstrap-pending-review`` blocker id (Issue #30 AC3:
    "Does not allow optimize until promote clears sentinel").
    The loader's own ``pending-generated-case`` blocker is
    preserved alongside so the operator sees the full
    picture.
  - A benchmark with no reviewed cases is BLOCKED via the
    loader's ``missing-reviewed-eval-case`` /
    ``missing-reviewed-held-out-case`` ids; the optimize
    command relays those verbatim rather than inventing its
    own.
  - The JSON output is parseable, exposes a ``blockers`` list
    with stable ids, and surfaces
    ``pending_review_case_ids`` so a downstream reader can
    branch on the machine-stable keys.
  - A benchmark that is otherwise optimize-runnable (eligible
    reviewed eval + held-out cases, no pending generated
    cases, no bootstrap sentinel) still returns
    ``EXIT_BLOCKED`` with the ``optimize-not-implemented``
    blocker: full optimization is W3 per the PRD, and the
    MVP contract is "we will refuse with a stable reason
    code" rather than "we silently do nothing".
  - The optimize command never mutates the benchmark file;
    the sentinel check is a read-only pass over the loader's
    partitioned cases.

These tests follow the subprocess invocation pattern from
:mod:`tests.test_promote_command` and
:mod:`tests.test_bootstrap_command`: ``python -m metacrucible``
is invoked in a temp dir, both stdout and stderr are captured,
and the JSON payload is parsed for the machine-stable fields.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

import pytest

from metacrucible.exit_codes import EXIT_BLOCKED, EXIT_OK, EXIT_USER_ERROR

REPO_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_FILE_NAME = "benchmark.jsonl"

#: Stable blocker id emitted by ``optimize`` when at least one
#: case still carries the literal bootstrap pending-review
#: sentinel. The id is the machine contract; the message is
#: human English prose.
OPTIMIZE_BOOTSTRAP_PENDING_REVIEW_BLOCKER = "bootstrap-pending-review"

#: Stable blocker id emitted by ``optimize`` when a fully
#: runnable benchmark (no blockers, no bootstrap sentinel) is
#: presented. Full optimization is W3 per the PRD; the MVP
#: command surfaces a dedicated blocker id rather than
#: silently doing nothing.
OPTIMIZE_NOT_IMPLEMENTED_BLOCKER = "optimize-not-implemented"

#: Literal case-level field that flags bootstrap-generated
#: cases as "pending human review". The string is the
#: machine-stable contract the optimize gate keys off.
BOOTSTRAP_PENDING_REVIEW_FIELD = "BOOTSTRAP_PENDING_REVIEW"

#: Stable blocker id for a benchmark with at least one
#: generated (pending review) case. Re-exported here so the
#: tests can branch on the id without re-deriving it from the
#: ``benchmark`` module.
PENDING_GENERATED_BLOCKER = "pending-generated-case"

#: Stable blocker ids emitted by the ADR 0029 loader when the
#: benchmark has no eligible reviewed cases. Re-exported
#: here so the optimize test asserts the loader's
#: missing-required-cases path without re-deriving the ids.
MISSING_REVIEWED_EVAL_BLOCKER = "missing-reviewed-eval-case"
MISSING_REVIEWED_HELD_OUT_BLOCKER = "missing-reviewed-held-out-case"


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _run_metacrucible(
    argv: list[str], *, cwd: Path
) -> subprocess.CompletedProcess[str]:
    """Invoke ``python -m metacrucible`` with captured text output.

    Mirrors the helper in :mod:`tests.test_promote_command` so
    the optimize tests use the same subprocess pattern the
    rest of the CLI test suite uses.
    """
    return subprocess.run(
        [sys.executable, "-m", "metacrucible", *argv],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


def _write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> Path:
    """Write ``records`` as one JSON object per line at ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(dict(rec), sort_keys=True) for rec in records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _init_workspace(tmp_path: Path) -> Path:
    """Run ``init`` against a fresh workspace dir and return that dir.

    The fixture creates the empty benchmark container that the
    optimize test then seeds with custom records. Each test
    starts from a known-good state with the benchmark file
    present at the workspace root.
    """
    workspace = tmp_path / "ws-optimize"
    workspace.mkdir(parents=True, exist_ok=True)
    result = _run_metacrucible(["init", str(workspace)], cwd=REPO_ROOT)
    assert result.returncode == EXIT_OK, (
        f"`init` must exit 0 before optimize; got "
        f"rc={result.returncode} stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    return workspace


def _metadata_record() -> dict[str, Any]:
    """Minimal benchmark metadata record (ADR 0029)."""
    return {
        "record_type": "metadata",
        "name": "default-benchmark",
        "schema_version": 1,
    }


def _generated_case(
    case_id: str, **extras: Any
) -> dict[str, Any]:
    """Build a generated (pending-review) case record.

    ``extras`` is forwarded to the case dict so tests can
    layer on the ``BOOTSTRAP_PENDING_REVIEW`` sentinel (the
    literal field the optimize gate keys off) without
    touching the helper.
    """
    record: dict[str, Any] = {
        "record_type": "case",
        "case_id": case_id,
        "status": "generated",
        "split": "eval",
        "input": {"prompt": "do the thing"},
        "execution_boundary": {"permissions": ["read"]},
        "checks": [{"name": "ok", "pattern": "ok"}],
    }
    record.update(extras)
    return record


def _reviewed_case(
    case_id: str, *, split: str = "eval"
) -> dict[str, Any]:
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


# --------------------------------------------------------------------------- #
# AC1 — ``optimize`` is a recognized subcommand                                #
# --------------------------------------------------------------------------- #

def test_optimize_subcommand_is_recognized() -> None:
    """``metacrucible optimize`` is a registered subcommand.

    Argparse raises ``unrecognized arguments: optimize`` if
    the subcommand is not wired in. The acceptance criterion
    is that ``optimize`` appears in the help output and the
    subcommand-level ``--help`` exits 0.
    """
    result = _run_metacrucible(["optimize", "--help"], cwd=REPO_ROOT)
    assert result.returncode == EXIT_OK, (
        f"`metacrucible optimize --help` must exit {EXIT_OK}; "
        f"got rc={result.returncode} stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    assert "optimize" in result.stdout, (
        f"optimize --help must mention the subcommand name; "
        f"got {result.stdout!r}"
    )
    assert "workspace" in result.stdout, (
        f"optimize --help must advertise the workspace "
        f"positional; got {result.stdout!r}"
    )
    assert "--json" in result.stdout, (
        f"optimize --help must advertise the --json flag; "
        f"got {result.stdout!r}"
    )


# --------------------------------------------------------------------------- #
# AC2 — optimize blocks when generated cases are present                       #
# --------------------------------------------------------------------------- #

def test_optimize_blocks_when_generated_cases_present(
    tmp_path: Path,
) -> None:
    """A benchmark with at least one generated case is
    BLOCKED with both the loader's ``pending-generated-case``
    blocker and the optimize command's
    ``bootstrap-pending-review`` blocker.

    The optimize command must surface both blockers so the
    operator sees the full picture: the loader partitions
    the cases and surfaces the partition-level blocker, and
    the optimize command surfaces the literal-sentinel
    blocker the gate keys off of.
    """
    workspace = _init_workspace(tmp_path)
    benchmark = workspace / BENCHMARK_FILE_NAME
    _write_jsonl(
        benchmark,
        [
            _metadata_record(),
            _reviewed_case("eval-1", split="eval"),
            _reviewed_case("held-1", split="held_out"),
            _generated_case("gen-1"),
        ],
    )

    result = _run_metacrucible(
        ["optimize", str(workspace), "--json"],
        cwd=REPO_ROOT,
    )
    assert result.returncode == EXIT_BLOCKED, (
        f"`optimize` with a generated case must exit "
        f"{EXIT_BLOCKED}; got rc={result.returncode} "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict), (
        f"optimize --json must return a JSON object; got "
        f"{type(payload).__name__} ({payload!r})"
    )
    blocker_ids = [
        b.get("id") for b in payload.get("blockers", [])
        if isinstance(b, dict)
    ]
    assert PENDING_GENERATED_BLOCKER in blocker_ids, (
        f"optimize with a generated case must surface the "
        f"loader pending-generated-case blocker; got "
        f"blocker_ids={blocker_ids!r}"
    )
    # The optimize command is blocked by the literal
    # sentinel only when the case carries
    # ``BOOTSTRAP_PENDING_REVIEW=True``. A generated case
    # without the sentinel still blocks via the loader's
    # pending-generated-case id; the optimize command does
    # NOT add the bootstrap-pending-review blocker because
    # the case is not bootstrap-tagged. Pin both shapes.
    assert OPTIMIZE_BOOTSTRAP_PENDING_REVIEW_BLOCKER not in blocker_ids, (
        f"a generated case WITHOUT the BOOTSTRAP_PENDING_REVIEW "
        f"sentinel must NOT trigger the bootstrap-pending-review "
        f"blocker; got blocker_ids={blocker_ids!r}"
    )


def test_optimize_blocks_when_bootstrap_sentinel_present(
    tmp_path: Path,
) -> None:
    """A case carrying the literal ``BOOTSTRAP_PENDING_REVIEW``
    sentinel triggers the dedicated optimize blocker on top
    of the loader's pending-generated-case id.

    The optimize command reads the case-level sentinel
    directly (per Issue #30 AC3) and surfaces the dedicated
    ``bootstrap-pending-review`` blocker so the operator sees
    exactly which cases are blocking the gate. The case
    also contributes to the loader's
    ``pending-generated-case`` blocker because
    ``status=generated``.
    """
    workspace = _init_workspace(tmp_path)
    benchmark = workspace / BENCHMARK_FILE_NAME
    _write_jsonl(
        benchmark,
        [
            _metadata_record(),
            _reviewed_case("eval-1", split="eval"),
            _reviewed_case("held-1", split="held_out"),
            _generated_case(
                "gen-1",
                **{BOOTSTRAP_PENDING_REVIEW_FIELD: True},
            ),
        ],
    )

    result = _run_metacrucible(
        ["optimize", str(workspace), "--json"],
        cwd=REPO_ROOT,
    )
    assert result.returncode == EXIT_BLOCKED, (
        f"`optimize` with a bootstrap-sentinel case must "
        f"exit {EXIT_BLOCKED}; got rc={result.returncode} "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    payload = json.loads(result.stdout)
    blocker_ids = [
        b.get("id") for b in payload.get("blockers", [])
        if isinstance(b, dict)
    ]
    assert OPTIMIZE_BOOTSTRAP_PENDING_REVIEW_BLOCKER in blocker_ids, (
        f"optimize with a BOOTSTRAP_PENDING_REVIEW sentinel "
        f"case must surface the bootstrap-pending-review "
        f"blocker; got blocker_ids={blocker_ids!r}"
    )
    assert PENDING_GENERATED_BLOCKER in blocker_ids, (
        f"optimize must ALSO surface the loader's "
        f"pending-generated-case blocker alongside; got "
        f"blocker_ids={blocker_ids!r}"
    )
    # The blocker message lists the case ids that carry
    # the sentinel so the operator can act on the precise
    # list of cases blocking the gate.
    sentinel_blocker = next(
        b for b in payload["blockers"]
        if isinstance(b, dict)
        and b.get("id") == OPTIMIZE_BOOTSTRAP_PENDING_REVIEW_BLOCKER
    )
    assert "gen-1" in sentinel_blocker.get("message", ""), (
        f"bootstrap-pending-review message must list the "
        f"case ids that carry the sentinel; got "
        f"{sentinel_blocker!r}"
    )
    pending_ids = payload.get("pending_review_case_ids") or []
    assert pending_ids == ["gen-1"], (
        f"optimize payload must surface the case ids that "
        f"carry the sentinel under pending_review_case_ids; "
        f"got {pending_ids!r}"
    )


# --------------------------------------------------------------------------- #
# AC3 — optimize blocks when no reviewed cases                                 #
# --------------------------------------------------------------------------- #

def test_optimize_blocks_when_no_reviewed_cases(tmp_path: Path) -> None:
    """A benchmark with no eligible reviewed cases is
    BLOCKED via the loader's missing-required-cases ids.

    The optimize command relays the loader blockers
    verbatim rather than inventing its own. A freshly
    ``init``-ed workspace carries only the metadata record,
    so both ``missing-reviewed-eval-case`` and
    ``missing-reviewed-held-out-case`` surface alongside.
    """
    workspace = _init_workspace(tmp_path)
    # The fixture's ``init`` left the benchmark with only
    # the metadata record; no cases at all.
    benchmark = workspace / BENCHMARK_FILE_NAME
    records = [
        json.loads(line)
        for line in benchmark.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(records) == 1, (
        f"init must leave exactly the metadata record; got "
        f"{len(records)} records"
    )

    result = _run_metacrucible(
        ["optimize", str(workspace), "--json"],
        cwd=REPO_ROOT,
    )
    assert result.returncode == EXIT_BLOCKED, (
        f"`optimize` on an empty benchmark must exit "
        f"{EXIT_BLOCKED}; got rc={result.returncode} "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    payload = json.loads(result.stdout)
    blocker_ids = [
        b.get("id") for b in payload.get("blockers", [])
        if isinstance(b, dict)
    ]
    assert MISSING_REVIEWED_EVAL_BLOCKER in blocker_ids, (
        f"optimize on an empty benchmark must surface the "
        f"loader missing-reviewed-eval-case blocker; got "
        f"blocker_ids={blocker_ids!r}"
    )
    assert MISSING_REVIEWED_HELD_OUT_BLOCKER in blocker_ids, (
        f"optimize on an empty benchmark must surface the "
        f"loader missing-reviewed-held-out-case blocker; got "
        f"blocker_ids={blocker_ids!r}"
    )


# --------------------------------------------------------------------------- #
# AC4 — JSON output shape (machine-stable contract)                           #
# --------------------------------------------------------------------------- #

def test_optimize_reports_blockers_in_json_output(
    tmp_path: Path,
) -> None:
    """``optimize --json`` emits a parseable JSON object with
    the canonical machine-stable keys and a non-empty
    blockers list when blocked.

    The shape is the contract downstream automation
    branches on: ``workspace``, ``benchmark``,
    ``benchmark_present``, ``is_optimize_runnable``,
    ``pending_review_case_ids``, ``blockers``. The
    ``blockers`` list carries the canonical ``{id, message}``
    shape so the operator can branch on the id and read
    the human English message.
    """
    workspace = _init_workspace(tmp_path)
    benchmark = workspace / BENCHMARK_FILE_NAME
    _write_jsonl(
        benchmark,
        [
            _metadata_record(),
            _reviewed_case("eval-1", split="eval"),
            _reviewed_case("held-1", split="held_out"),
            _generated_case(
                "gen-1",
                **{BOOTSTRAP_PENDING_REVIEW_FIELD: True},
            ),
        ],
    )

    result = _run_metacrucible(
        ["optimize", str(workspace), "--json"],
        cwd=REPO_ROOT,
    )
    assert result.returncode == EXIT_BLOCKED, (
        f"`optimize --json` on a blocked benchmark must exit "
        f"{EXIT_BLOCKED}; got rc={result.returncode} "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
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
    for key in (
        "status",
        "workspace",
        "benchmark",
        "is_optimize_runnable",
        "pending_review_case_ids",
        "blockers",
        "rounds",
    ):
        assert key in payload, (
            f"optimize --json must surface {key!r}; got keys "
            f"{sorted(payload.keys())!r}"
        )
    assert payload["is_optimize_runnable"] is False, (
        f"is_optimize_runnable must be False when the "
        f"benchmark is blocked; got "
        f"{payload['is_optimize_runnable']!r}"
    )
    assert isinstance(payload["blockers"], list) and payload["blockers"], (
        f"optimize --json must report a non-empty blockers "
        f"list when blocked; got {payload['blockers']!r}"
    )
    for blocker in payload["blockers"]:
        assert isinstance(blocker, dict), (
            f"each blocker must be a dict with id+message; "
            f"got {blocker!r}"
        )
        assert isinstance(blocker.get("id"), str) and blocker["id"], (
            f"each blocker must carry a non-empty string id; "
            f"got {blocker!r}"
        )
        # The human message is required by ADR 0029.
        assert isinstance(blocker.get("message"), str), (
            f"each blocker must carry a string message; got "
            f"{blocker!r}"
        )


# --------------------------------------------------------------------------- #
# AC5 — clean benchmark surfaces "not yet implemented"                        #
# --------------------------------------------------------------------------- #

def test_optimize_clean_benchmark_enters_pipeline(
    tmp_path: Path,
) -> None:
    """A clean (loader-runnable) benchmark no longer emits
    the ``optimize-not-implemented`` W3 placeholder
    blocker (OPT-0). The MVP sentinel gate is replaced by
    the full SkillOpt-shaped pipeline; the BLOCKED path
    that remains is the artifact-path precondition (the
    pipeline cannot run without an envelope-declared
    artifact).

    The test seeds a clean benchmark and asserts the
    optimize command blocks on the artifact precondition
    rather than the W3 placeholder, so the
    ``optimize-not-implemented`` blocker id is GONE from
    the payload. The blocker that surfaces is the new
    ``optimize-artifact-unresolved`` id (OD1-equivalent
    for the optimizer).
    """
    workspace = _init_workspace(tmp_path)
    benchmark = workspace / BENCHMARK_FILE_NAME
    _write_jsonl(
        benchmark,
        [
            _metadata_record(),
            _reviewed_case("eval-1", split="eval"),
            _reviewed_case("held-1", split="held_out"),
        ],
    )

    result = _run_metacrucible(
        ["optimize", str(workspace), "--json"],
        cwd=REPO_ROOT,
    )
    assert result.returncode == EXIT_BLOCKED, (
        f"`optimize` on a clean benchmark without an "
        f"envelope-declared artifact must exit {EXIT_BLOCKED}; "
        f"got rc={result.returncode} stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    payload = json.loads(result.stdout)
    blocker_ids = [
        b.get("id") for b in payload.get("blockers", [])
        if isinstance(b, dict)
    ]
    # OPT-0: the W3 placeholder is gone.
    assert OPTIMIZE_NOT_IMPLEMENTED_BLOCKER not in blocker_ids, (
        f"optimize must no longer surface the W3 placeholder "
        f"blocker; got blocker_ids={blocker_ids!r}"
    )
    # The pipeline has not started: there is no envelope
    # artifact_path, so the precondition blocks. The
    # operator sees a stable, machine-branched blocker id
    # rather than a silent pass.
    assert "optimize-artifact-unresolved" in blocker_ids, (
        f"optimize must surface the optimize-artifact-"
        f"unresolved blocker on the new precondition path; "
        f"got blocker_ids={blocker_ids!r}"
    )
    # ``is_optimize_runnable`` is True (the benchmark is
    # fine; the missing piece is the artifact, which is a
    # separate precondition). The command emits the BLOCKED
    # status from the payload.
    assert payload.get("status") == "BLOCKED", (
        f"clean-benchmark-without-artifact must report "
        f"status=BLOCKED; got {payload.get('status')!r}"
    )


# AC6 — optimize is read-only (no benchmark mutation)                          #
# --------------------------------------------------------------------------- #

def test_optimize_does_not_mutate_benchmark_file(tmp_path: Path) -> None:
    """``optimize`` is a read-only sentinel gate.

    The MVP contract is "we will refuse to start"; the
    command never rewrites ``benchmark.jsonl`` or
    ``history.jsonl``. The test pins the file bytes around
    the BLOCKED call so any accidental write fails loud.
    """
    workspace = _init_workspace(tmp_path)
    benchmark = workspace / BENCHMARK_FILE_NAME
    _write_jsonl(
        benchmark,
        [
            _metadata_record(),
            _reviewed_case("eval-1", split="eval"),
            _reviewed_case("held-1", split="held_out"),
            _generated_case(
                "gen-1",
                **{BOOTSTRAP_PENDING_REVIEW_FIELD: True},
            ),
        ],
    )
    before_bytes = benchmark.read_bytes()

    result = _run_metacrucible(
        ["optimize", str(workspace), "--json"],
        cwd=REPO_ROOT,
    )
    assert result.returncode == EXIT_BLOCKED, (
        f"`optimize` BLOCKED call must exit {EXIT_BLOCKED}; "
        f"got rc={result.returncode} stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    after_bytes = benchmark.read_bytes()
    assert after_bytes == before_bytes, (
        f"optimize must NOT mutate the benchmark file; "
        f"before={before_bytes!r} after={after_bytes!r}"
    )
    # And no history event was written.
    history = workspace / ".metacrucible" / "history.jsonl"
    if history.exists():
        records = [
            json.loads(line)
            for line in history.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        optimize_events = [
            r for r in records
            if isinstance(r, dict)
            and r.get("event") in {"optimize_started", "optimize_blocked"}
        ]
        assert not optimize_events, (
            f"optimize BLOCKED must not write history events; "
            f"found {optimize_events!r}"
        )


# --------------------------------------------------------------------------- #
# AC7 — argparse usage error for missing workspace positional                   #
# --------------------------------------------------------------------------- #

def test_optimize_missing_workspace_argparse_error() -> None:
    """``optimize`` with no workspace positional is an
    argparse usage error (Issue #27 task 27.1).

    The CLI dispatcher maps argparse errors to
    :data:`EXIT_USER_ERROR` (1) so the contract is distinct
    from BLOCKED (2) and INTERNAL (3). A missing positional
    is exactly that: argparse usage, not a semantic blocker.
    """
    result = _run_metacrucible(["optimize"], cwd=REPO_ROOT)
    assert result.returncode == EXIT_USER_ERROR, (
        f"`optimize` with no workspace must exit "
        f"{EXIT_USER_ERROR} (argparse usage); got "
        f"rc={result.returncode} stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )


# --------------------------------------------------------------------------- #
# AC8 — optimize blocks when benchmark file is missing                         #
# --------------------------------------------------------------------------- #

def test_optimize_blocks_when_benchmark_file_missing(
    tmp_path: Path,
) -> None:
    """A workspace without a benchmark file is BLOCKED with
    the loader's missing-required-cases ids.

    The optimize command is read-only: it does not create
    the benchmark container. A missing file surfaces the
    same two missing-required-cases blockers an empty
    benchmark would (per the loader's contract on an
    absent file).
    """
    workspace = tmp_path / "ws-optimize-missing-bench"
    workspace.mkdir(parents=True, exist_ok=True)
    assert not (workspace / BENCHMARK_FILE_NAME).exists()

    result = _run_metacrucible(
        ["optimize", str(workspace), "--json"],
        cwd=REPO_ROOT,
    )
    assert result.returncode == EXIT_BLOCKED, (
        f"`optimize` on a missing-benchmark workspace must "
        f"exit {EXIT_BLOCKED}; got rc={result.returncode} "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    payload = json.loads(result.stdout)
    blocker_ids = [
        b.get("id") for b in payload.get("blockers", [])
        if isinstance(b, dict)
    ]
    assert MISSING_REVIEWED_EVAL_BLOCKER in blocker_ids, (
        f"missing-benchmark optimize must surface the "
        f"loader missing-reviewed-eval-case blocker; got "
        f"blocker_ids={blocker_ids!r}"
    )
    assert MISSING_REVIEWED_HELD_OUT_BLOCKER in blocker_ids, (
        f"missing-benchmark optimize must surface the "
        f"loader missing-reviewed-held-out-case blocker; "
        f"got blocker_ids={blocker_ids!r}"
    )
    # The optimize command must not silently create the
    # benchmark file.
    assert not (workspace / BENCHMARK_FILE_NAME).exists(), (
        f"optimize BLOCKED must NOT create the benchmark "
        f"file; found {workspace / BENCHMARK_FILE_NAME}"
    )


# --------------------------------------------------------------------------- #
# AC9 — human output is English-only                                            #
# --------------------------------------------------------------------------- #

def test_optimize_human_output_is_english_only(
    tmp_path: Path,
) -> None:
    """Human output of the optimize path is English-only.

    Issue #27 task 27.4: the CLI's own prose is the
    English-only contract. The optimize human output has no
    user-controlled freeform text, so the surface stays
    ASCII throughout.
    """
    workspace = _init_workspace(tmp_path)
    benchmark = workspace / BENCHMARK_FILE_NAME
    _write_jsonl(
        benchmark,
        [
            _metadata_record(),
            _reviewed_case("eval-1", split="eval"),
            _reviewed_case("held-1", split="held_out"),
            _generated_case(
                "gen-1",
                **{BOOTSTRAP_PENDING_REVIEW_FIELD: True},
            ),
        ],
    )

    result = _run_metacrucible(
        ["optimize", str(workspace)],
        cwd=REPO_ROOT,
    )
    assert result.returncode == EXIT_BLOCKED, (
        f"`optimize` no --json must exit {EXIT_BLOCKED}; "
        f"got rc={result.returncode} stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    offenders = sorted(
        {ch for ch in result.stdout + result.stderr
         if ord(ch) > 0x7F and not ch.isspace()}
    )
    assert not offenders, (
        f"human output must be English-only; got offenders "
        f"{offenders!r} in stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )

# --------------------------------------------------------------------------- #
# OPT-0 — no SkillOpt runtime dependency                                      #
# --------------------------------------------------------------------------- #

def test_optimize_path_does_not_require_skillopt_import() -> None:
    """Importing the optimize path must not require ``skillopt``.

    Issue #33 AC5 / ADR 0022: MetaCrucible re-implements the
    SkillOpt-shaped loop without a runtime dependency on
    Microsoft SkillOpt. The test pins the contract by
    installing a sys.modules stub for ``skillopt`` that
    raises on attribute access, then imports the entire
    metacrucible package. If any module under the optimize
    path tries to import ``skillopt`` (top-level or
    transitive), the import fails loud.

    The test is intentionally a fresh ``importlib.import_module``
    round so it does not rely on the prior test's
    import state.
    """
    import importlib
    import sys

    blocked = {"skillopt": None}
    for mod_name in list(sys.modules):
        if mod_name == "skillopt" or mod_name.startswith("skillopt."):
            blocked[mod_name] = sys.modules.pop(mod_name)

    class _SkilloptImportBlocker:
        """A module object whose attribute access raises."""

        def __getattr__(self, name: str) -> None:
            raise ImportError(
                f"metacrucible.optimize must not depend on "
                f"skillopt at runtime (ADR 0022); blocked "
                f"attribute {name!r}"
            )

    sys.modules["skillopt"] = _SkilloptImportBlocker()  # type: ignore[assignment]
    try:
        # Force a re-import of metacrucible + the optimize path.
        for mod_name in [
            "metacrucible",
            "metacrucible.optimizer",
            "metacrucible.__main__",
        ]:
            sys.modules.pop(mod_name, None)
        importlib.import_module("metacrucible")
        importlib.import_module("metacrucible.optimizer")
        importlib.import_module("metacrucible.__main__")
    finally:
        # Restore the previous sys.modules state so the
        # rest of the test suite is unaffected.
        for mod_name, mod in blocked.items():
            if mod is None:
                sys.modules.pop(mod_name, None)
            else:
                sys.modules[mod_name] = mod


# --------------------------------------------------------------------------- #
# OPT-9 — record-counts contract                                              #
# --------------------------------------------------------------------------- #

def test_optimize_pipeline_produces_required_record_types() -> None:
    """The pipeline persists every required record type
    (OPT-2 / OPT-9 AC1).

    The test drives the pipeline directly (no subprocess)
    with a deterministic no-LLM ``call_fn`` and asserts
    that every required record type was appended to the
    workspace's ``history.jsonl`` at least once. A
    pre-acceptance candidate is rejected (eval-split FAIL
    counts are not strictly improved), so the run's
    record count for ``range_merge_plan`` is 1 (the
    merge plan that was rejected).
    """
    import json as _json
    from metacrucible.optimizer import run_optimizer_pipeline

    workspace = _init_workspace(tmp_path=None) if False else _tmp_workspace()  # type: ignore[arg-type]
    benchmark = workspace / BENCHMARK_FILE_NAME
    artifact = workspace / "SKILL.md"
    artifact.write_text(
        "---\n"
        "name: opt-skill\n"
        "description: a tiny skill for the OPT-9 contract test\n"
        "---\n"
        "# body\nThe body is the only mutable range.\n",
        encoding="utf-8",
    )
    # Envelope must declare artifact_path (OD1).
    envelope = workspace / ".metacrucible" / "envelope.json"
    envelope.write_text(
        _json.dumps(
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
    _write_jsonl(
        benchmark,
        [
            _metadata_record(),
            _reviewed_case("eval-1", split="eval"),
            _reviewed_case("held-1", split="held_out"),
        ],
    )

    # Inject a deterministic call_fn that returns a valid
    # round_reflection with one edit_suggestion targeting
    # the body's range_id (=0). The fake matches the
    # call_structured contract: ``call_fn(repair_context=...)``
    # returns a JSON-compatible object that validates against
    # the schema.
    def _fake_round_reflection(*, repair_context=None):
        return {
            "rationale": "improve the body clarity",
            "suggested_edits": [
                {
                    "range_id": 0,
                    "base_hash": (
                        __import__("hashlib").sha256(
                            b"# body\nThe body is the only mutable range.\n"
                        ).hexdigest()
                    ),
                    "intent": "clarify_triggers",
                    "replacement": (
                        "# body\nThe body is the only mutable range.\n"
                        "Skill name: opt-skill\n"
                    ),
                    "rationale": "improve clarity",
                    "routing": False,
                }
            ],
        }

    result = run_optimizer_pipeline(
        workspace=workspace,
        benchmark_path=benchmark,
        artifact_path=artifact,
        call_fn=_fake_round_reflection,
        max_rounds=1,
        human_confirmed=False,
    )

    history = workspace / ".metacrucible" / "history.jsonl"
    assert history.is_file(), (
        f"optimize pipeline must append to history.jsonl; "
        f"file missing at {history}"
    )
    record_types: set[str] = set()
    for line in history.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = _json.loads(line)
        if isinstance(rec, dict):
            rt = rec.get("record_type")
            if isinstance(rt, str):
                record_types.add(rt)
    for required in (
        "case_reflection",
        "round_reflection",
        "edit_suggestion",
        "ranked_edit_set",
        "range_merge_plan",
    ):
        assert required in record_types, (
            f"pipeline must persist a {required!r} record "
            f"during a run; got record_types={sorted(record_types)!r}"
        )
    assert result.run_id, "pipeline must return a non-empty run_id"
    # The run produced an evidence bundle.
    assert result.evidence_refs, (
        f"pipeline must persist an evidence bundle; got "
        f"evidence_refs={result.evidence_refs!r}"
    )


def _tmp_workspace(tmp_path: Path | None = None) -> Path:
    """Helper: create an isolated ``init``-ed workspace for the OPT-9 test."""
    import tempfile
    if tmp_path is None:
        tmp_path = Path(tempfile.mkdtemp(prefix="metacrucible-opt9-"))
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    result = _run_metacrucible(["init", str(workspace)], cwd=REPO_ROOT)
    assert result.returncode == EXIT_OK
    return workspace


# --------------------------------------------------------------------------- #
# OPT-9 contract regression tests for AC2 / AC3 / AC4                          #
# --------------------------------------------------------------------------- #


def _opt9_skill_artifact_path(workspace: Path) -> Path:
    """Return the path the OPT-9 tests use for the artifact under optimization.

    The artifact is a tiny Skill so the parser produces exactly
    one mutable range (the body, ``range_id=0``). Sharing the
    path keeps the OPT-9 tests consistent with
    :func:`test_optimize_pipeline_produces_required_record_types`.
    """
    return workspace / "SKILL.md"


def _opt9_seed_artifact(workspace: Path) -> Path:
    """Write the OPT-9 fixture artifact and return its path."""
    artifact = _opt9_skill_artifact_path(workspace)
    artifact.write_text(
        "---\n"
        "name: opt9-skill\n"
        "description: OPT-9 contract regression fixture\n"
        "---\n"
        "# body\nThe body is the only mutable range.\n",
        encoding="utf-8",
    )
    return artifact


def _opt9_seed_envelope(
    workspace: Path, artifact: Path
) -> Path:
    """Write the envelope the OPT-9 tests rely on (OD1)."""
    import json as _json

    envelope = workspace / ".metacrucible" / "envelope.json"
    envelope.write_text(
        _json.dumps(
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
    return envelope


def _opt9_body_text() -> str:
    """Return the canonical OPT-9 fixture body text."""
    return "# body\nThe body is the only mutable range.\n"


def _opt9_body_hash() -> str:
    """Return the parser-owned content hash for the OPT-9 body."""
    import hashlib

    return hashlib.sha256(_opt9_body_text().encode("utf-8")).hexdigest()


def _opt9_read_history(workspace: Path) -> list[dict[str, Any]]:
    """Read and JSON-decode every record in ``history.jsonl``."""
    import json as _json

    history = workspace / ".metacrucible" / "history.jsonl"
    records: list[dict[str, Any]] = []
    if not history.is_file():
        return records
    for line in history.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = _json.loads(line)
        if isinstance(rec, dict):
            records.append(rec)
    return records


def _opt9_find_records(
    records: list[dict[str, Any]], record_type: str
) -> list[dict[str, Any]]:
    """Filter history records to those whose ``record_type`` matches."""
    return [
        r for r in records
        if isinstance(r.get("record_type"), str)
        and r["record_type"] == record_type
    ]


def test_optimize_held_out_excluded_from_context_and_history() -> None:
    """AC2: held-out case content must never reach the optimizer
    context or the persisted history (OPT-9 / ADR 0032).

    The test pins the contract from two angles:

    1. :func:`metacrucible.optimizer.build_optimizer_context`
       must store held-out case *ids* only; the prompts /
       expected behavior of held-out cases must not leak into
       the context payload.
    2. Driving the full pipeline with a call_fn spy must
       neither thread held-out content into the spy payloads
       nor persist held-out case references in
       ``history.jsonl`` before the candidate is evaluated.
    """
    import json as _json

    from metacrucible.optimizer import (
        build_optimizer_context,
        run_optimizer_pipeline,
    )

    workspace = _tmp_workspace()
    benchmark = workspace / BENCHMARK_FILE_NAME
    artifact = _opt9_seed_artifact(workspace)
    _opt9_seed_envelope(workspace, artifact)

    # Distinctive held-out sentinel string the test will search
    # for in every payload that touches the optimizer. If the
    # sentinel ever surfaces, the held-out exclusion contract
    # has regressed.
    held_out_sentinel = "HELD_OUT_SENTINEL_DO_NOT_LEAK_42"
    eval_sentinel = "EVAL_SENTINEL_OK_99"

    _write_jsonl(
        benchmark,
        [
            _metadata_record(),
            # Reviewed eval case with a distinctive prompt.
            _reviewed_case(
                "eval-1",
                split="eval",
            ) | {"input": {"prompt": eval_sentinel}},
            # Reviewed held-out case with a distinctive prompt
            # that must never appear in any optimizer context
            # or history record.
            _reviewed_case(
                "held-1",
                split="held_out",
            ) | {"input": {"prompt": held_out_sentinel}},
        ],
    )

    # 1. The optimizer context itself must hold held-out as
    #    *ids only* (no prompts / no expected behavior).
    context = build_optimizer_context(
        workspace=workspace,
        benchmark_path=benchmark,
        artifact_path=artifact,
        max_rounds=1,
        human_confirmed=False,
    )
    assert "eval-1" in context.eligible_eval_case_ids, (
        f"context must surface the eval case id; got "
        f"{list(context.eligible_eval_case_ids)!r}"
    )
    assert "held-1" in context.eligible_held_out_case_ids, (
        f"context must surface the held-out case id; got "
        f"{list(context.eligible_held_out_case_ids)!r}"
    )
    ctx_blob = _json.dumps(context.as_dict(), sort_keys=True)
    assert held_out_sentinel not in ctx_blob, (
        f"optimizer context must NOT carry held-out prompt "
        f"content; sentinel leaked into context.as_dict()"
    )
    # Sanity: the eval sentinel is not in the context either
    # (the context only stores ids, not case bodies) — this
    # confirms the no-content rule applies to both splits.
    assert eval_sentinel not in ctx_blob, (
        f"optimizer context must NOT carry eval prompt "
        f"content either; eval sentinel leaked"
    )

    # 2. Drive the full pipeline with a call_fn spy that
    #    records every ``repair_context`` it receives. The
    #    spy also returns a deterministic edit suggestion
    #    so the pipeline can run end-to-end.
    captured_contexts: list[Any] = []

    def _spy_call_fn(*args: Any, **kwargs: Any) -> dict[str, Any]:
        repair_context: Any = kwargs.get("repair_context")
        if repair_context is None and args:
            repair_context = args[0]
        captured_contexts.append(repair_context)
        # Deterministic round_reflection with one edit
        # suggestion whose replacement keeps the artifact
        # identical so the apply / evaluate stages don't
        # change the on-disk bytes for this contract test.
        return {
            "rationale": "AC2 contract regression: spy call_fn",
            "suggested_edits": [
                {
                    "range_id": 0,
                    "base_hash": _opt9_body_hash(),
                    "intent": "no_op_for_held_out_test",
                    "replacement": _opt9_body_text(),
                    "rationale": "replace with same body text",
                    "routing": False,
                }
            ],
        }

    result = run_optimizer_pipeline(
        workspace=workspace,
        benchmark_path=benchmark,
        artifact_path=artifact,
        call_fn=_spy_call_fn,
        max_rounds=1,
        human_confirmed=False,
    )
    assert captured_contexts, (
        f"pipeline must have invoked the call_fn spy at "
        f"least once; got {len(captured_contexts)} calls"
    )
    # No call_fn payload (args / kwargs / repair_context) may
    # carry the held-out sentinel.
    for idx, ctx in enumerate(captured_contexts):
        ctx_repr = repr(ctx)
        assert held_out_sentinel not in ctx_repr, (
            f"call_fn invocation #{idx + 1} must NOT carry "
            f"held-out content; repair_context={ctx_repr!r}"
        )

    # History records before candidate evaluation must not
    # reference the held-out case id or its prompt. The
    # case_reflection records carry only eval case ids; the
    # run-level start event carries only run metadata.
    records = _opt9_read_history(workspace)
    history_blob = _json.dumps(records, sort_keys=True)
    assert held_out_sentinel not in history_blob, (
        f"history must NOT carry held-out prompt content; "
        f"found held_out_sentinel in {history_blob!r}"
    )
    # The case_reflection record is per eval case; held-out
    # case ids must never appear as a case_id reference.
    case_reflections = _opt9_find_records(records, "case_reflection")
    for rec in case_reflections:
        assert rec.get("case_id") != "held-1", (
            f"case_reflection must NOT reference a held-out "
            f"case_id; got {rec!r}"
        )
    # The contract holds even if the run is rejected /
    # blocked: the run must not have evaluated the held-out
    # split before the candidate materialized.
    assert result.run_id, (
        f"pipeline must produce a non-empty run_id; got "
        f"{result.run_id!r}"
    )


def test_optimize_routing_cap_exceeded_blocks_second_routing_edit() -> None:
    """AC3 (routing cap=1): when the round_reflection returns
    two selected routing edits, the second one is rejected
    with the canonical :data:`ROUTING_CAP_EXCEEDED_BLOCKER`
    rejection id (OPT-4 / ADR 0032).

    The test injects a deterministic ``call_fn`` that returns
    two routing edits on the same range. Both carry
    ``human_confirmed=True`` so the per-suggestion HITL gate
    does not trip first; the cap is the limiting factor. The
    test reads ``history.jsonl``, finds the
    ``ranked_edit_set`` record, and asserts the ``rejected``
    list contains an entry whose ``reason_id`` is the cap
    blocker id.
    """
    from metacrucible.optimizer import (
        ROUTING_CAP_EXCEEDED_BLOCKER,
        run_optimizer_pipeline,
    )

    workspace = _tmp_workspace()
    benchmark = workspace / BENCHMARK_FILE_NAME
    artifact = _opt9_seed_artifact(workspace)
    _opt9_seed_envelope(workspace, artifact)
    _write_jsonl(
        benchmark,
        [
            _metadata_record(),
            _reviewed_case("eval-1", split="eval"),
            _reviewed_case("held-1", split="held_out"),
        ],
    )

    body_hash = _opt9_body_hash()

    def _two_routing_edits(*, repair_context: Any = None) -> dict[str, Any]:
        # Two routing edits on the body's range_id=0. Both
        # name the "name" routing field (which is on the
        # Skill routing surface so the contradictory-intent
        # rule does not trip). Both carry
        # ``human_confirmed=True`` so the per-suggestion
        # HITL gate does not trip first — only the cap
        # should reject.
        return {
            "rationale": "AC3 cap-exceeded contract regression",
            "suggested_edits": [
                {
                    "range_id": 0,
                    "base_hash": body_hash,
                    "intent": "rename_skill_first",
                    "replacement": _opt9_body_text(),
                    "rationale": "first routing edit",
                    "routing": True,
                    "routing_field": "name",
                },
                {
                    "range_id": 0,
                    "base_hash": body_hash,
                    "intent": "rename_skill_second",
                    "replacement": _opt9_body_text(),
                    "rationale": "second routing edit",
                    "routing": True,
                    "routing_field": "name",
                },
            ],
        }

    run_optimizer_pipeline(
        workspace=workspace,
        benchmark_path=benchmark,
        artifact_path=artifact,
        call_fn=_two_routing_edits,
        max_rounds=1,
        human_confirmed=True,
    )

    records = _opt9_read_history(workspace)
    ranked_records = _opt9_find_records(records, "ranked_edit_set")
    assert ranked_records, (
        f"pipeline must persist at least one ranked_edit_set "
        f"record on a two-routing-edit run; got "
        f"{len(ranked_records)} records in history"
    )
    last_ranked = ranked_records[-1]
    rejected = last_ranked.get("rejected") or []
    cap_rejections = [
        r for r in rejected
        if isinstance(r, dict)
        and r.get("reason_id") == ROUTING_CAP_EXCEEDED_BLOCKER
    ]
    assert cap_rejections, (
        f"ranked_edit_set.rejected must contain an entry "
        f"with reason_id={ROUTING_CAP_EXCEEDED_BLOCKER!r} "
        f"when the round submits two routing edits; got "
        f"rejected={rejected!r}"
    )
    # The first routing edit must have been selected (cap
    # only fires for the second+ edit).
    selected = last_ranked.get("selected") or []
    assert len(selected) == 1, (
        f"exactly one routing edit must survive the cap "
        f"clip; got selected={selected!r}"
    )


def test_optimize_routing_hitl_unconfirmed_blocks_routing_edit() -> None:
    """AC3 (routing HITL): a routing edit without explicit
    human confirmation is rejected with the canonical
    :data:`ROUTING_HITL_UNCONFIRMED_BLOCKER` rejection id
    (OPT-4 / ADR 0032).

    The test injects a deterministic ``call_fn`` returning a
    single routing edit with ``human_confirmed=False`` on the
    suggestion and ``human_confirmed=False`` on the optimizer
    context. The cap check is not the limiting factor here
    (only one routing edit is submitted); the HITL gate
    must reject the edit. The test reads ``history.jsonl``,
    finds the ``ranked_edit_set`` record, and asserts the
    ``rejected`` list contains an entry whose ``reason_id``
    is the HITL blocker id.
    """
    from metacrucible.optimizer import (
        ROUTING_HITL_UNCONFIRMED_BLOCKER,
        run_optimizer_pipeline,
    )

    workspace = _tmp_workspace()
    benchmark = workspace / BENCHMARK_FILE_NAME
    artifact = _opt9_seed_artifact(workspace)
    _opt9_seed_envelope(workspace, artifact)
    _write_jsonl(
        benchmark,
        [
            _metadata_record(),
            _reviewed_case("eval-1", split="eval"),
            _reviewed_case("held-1", split="held_out"),
        ],
    )

    body_hash = _opt9_body_hash()

    def _unconfirmed_routing_edit(
        *, repair_context: Any = None
    ) -> dict[str, Any]:
        # One routing edit; the suggestion-level
        # ``human_confirmed`` is False and the context-level
        # ``human_confirmed`` will be False at the call site
        # so the HITL gate trips on this edit alone.
        return {
            "rationale": "AC3 HITL contract regression",
            "suggested_edits": [
                {
                    "range_id": 0,
                    "base_hash": body_hash,
                    "intent": "rename_skill_without_confirm",
                    "replacement": _opt9_body_text(),
                    "rationale": "routing edit without HITL",
                    "routing": True,
                    "routing_field": "name",
                }
            ],
        }

    result = run_optimizer_pipeline(
        workspace=workspace,
        benchmark_path=benchmark,
        artifact_path=artifact,
        call_fn=_unconfirmed_routing_edit,
        max_rounds=1,
        human_confirmed=False,
    )
    # Sanity: the pipeline did not mutate the artifact
    # because the only selected candidate was rejected.
    # The HITL gate tripped in step 3d so the apply /
    # evaluate stages never ran; the bytes on disk must
    # match what the seed helper wrote.
    expected_artifact_bytes = (
        b"---\nname: opt9-skill\n"
        b"description: OPT-9 contract regression fixture\n"
        b"---\n# body\nThe body is the only mutable range.\n"
    )
    assert artifact.read_bytes() == expected_artifact_bytes, (
        f"HITL-blocked routing edit must NOT mutate the "
        f"artifact; expected={expected_artifact_bytes!r} "
        f"actual={artifact.read_bytes()!r}"
    )
    records = _opt9_read_history(workspace)
    ranked_records = _opt9_find_records(records, "ranked_edit_set")
    assert ranked_records, (
        f"pipeline must persist a ranked_edit_set record "
        f"even when the routing edit is rejected; got "
        f"{len(ranked_records)} records"
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
        f"when a routing edit lacks confirmation; got "
        f"rejected={rejected!r}"
    )
    # Selected must be empty (the only routing edit was
    # rejected) so the pipeline exits with no candidate.
    assert last_ranked.get("selected") in (None, [], ()), (
        f"no suggestion must be selected when the only "
        f"routing edit is HITL-blocked; got "
        f"selected={last_ranked.get('selected')!r}"
    )
    assert result.status in {"REJECTED", "BLOCKED"}, (
        f"HITL-blocked run must terminate with REJECTED or "
        f"BLOCKED status; got {result.status!r}"
    )


def test_optimize_stale_base_hash_blocks_before_disk_write() -> None:
    """AC4 (stale base detection): an ``edit_suggestion``
    whose ``base_hash`` does not match the parser-owned
    :data:`MutableRange.content_hash` of the target range
    must be rejected before the candidate artifact is
    written to disk (OPT-1 / OPT-5 / ADR 0032).

    The test pins two contracts:

    1. The pipeline drops the stale suggestion at the
       round-processing stage (step 3c). The drop is
       observable in the persisted ``round_reflection``
       record's ``bounded_rejected_themes`` list as a
       ``{"kind": "stale_base_hash", ...}`` entry. The
       artifact on disk must be byte-for-byte unchanged
       after the run.
    2. The deterministic
       :func:`metacrucible.optimizer._check_stale_base_hash`
       check emits the canonical
       :data:`STALE_BASE_HASH_BLOCKER` blocker id when a
       stale ``base_hash`` is given to it directly. This
       pins the blocker id that downstream reports branch
       on without driving the full pipeline.
    """
    import hashlib

    from metacrucible.optimizer import (
        STALE_BASE_HASH_BLOCKER,
        _check_stale_base_hash,
        build_optimizer_context,
        run_optimizer_pipeline,
    )

    workspace = _tmp_workspace()
    benchmark = workspace / BENCHMARK_FILE_NAME
    artifact = _opt9_seed_artifact(workspace)
    _opt9_seed_envelope(workspace, artifact)
    _write_jsonl(
        benchmark,
        [
            _metadata_record(),
            _reviewed_case("eval-1", split="eval"),
            _reviewed_case("held-1", split="held_out"),
        ],
    )

    # ``stale_base_hash`` is a deliberately wrong 64-char
    # hex digest so the pipeline must reject the
    # suggestion in the round-processing stage.
    stale_base_hash = "0" * 64
    assert stale_base_hash != _opt9_body_hash(), (
        "test fixture invariant: the stale base hash "
        "must differ from the canonical body hash"
    )

    def _stale_suggestion(*, repair_context: Any = None) -> dict[str, Any]:
        return {
            "rationale": "AC4 stale base regression",
            "suggested_edits": [
                {
                    "range_id": 0,
                    "base_hash": stale_base_hash,
                    "intent": "should_be_dropped",
                    "replacement": (
                        "# body\nThis replacement must never "
                        "be written.\n"
                    ),
                    "rationale": "stale base hash contract",
                    "routing": False,
                }
            ],
        }

    before_bytes = artifact.read_bytes()

    result = run_optimizer_pipeline(
        workspace=workspace,
        benchmark_path=benchmark,
        artifact_path=artifact,
        call_fn=_stale_suggestion,
        max_rounds=1,
        human_confirmed=False,
    )

    # Contract 1a: the artifact on disk must be byte-for-byte
    # unchanged — the stale suggestion is dropped before
    # apply.
    after_bytes = artifact.read_bytes()
    assert after_bytes == before_bytes, (
        f"stale-base suggestion must NOT mutate the "
        f"artifact; before={before_bytes!r} "
        f"after={after_bytes!r}"
    )

    # Contract 1b: history must NOT carry a stale
    # edit_suggestion record. The drop happens before the
    # suggestion is appended to the record stream.
    records = _opt9_read_history(workspace)
    edit_records = _opt9_find_records(records, "edit_suggestion")
    stale_edit_records = [
        r for r in edit_records
        if isinstance(r.get("base_hash"), str)
        and r["base_hash"] == stale_base_hash
    ]
    assert not stale_edit_records, (
        f"a stale-base edit_suggestion must NEVER be "
        f"persisted; got {stale_edit_records!r}"
    )

    # Contract 1c: the pipeline must surface a
    # ``no_candidate_edits`` warning so downstream tools
    # can detect the no-mutation outcome. The warning is
    # the observable signal that the round processed the
    # suggestion but found it unusable.
    no_candidate_warnings = [
        w for w in (result.warnings or [])
        if isinstance(w, dict)
        and w.get("id") == "no_candidate_edits"
    ]
    assert no_candidate_warnings, (
        f"a stale-base round must surface a "
        f"no_candidate_edits warning on result.warnings; "
        f"got result.warnings={result.warnings!r}"
    )

    # Contract 2: the deterministic
    # :func:`_check_stale_base_hash` emits the canonical
    # STALE_BASE_HASH_BLOCKER id when given a stale
    # suggestion directly. This pins the blocker id
    # downstream reports branch on.
    context = build_optimizer_context(
        workspace=workspace,
        benchmark_path=benchmark,
        artifact_path=artifact,
        max_rounds=1,
        human_confirmed=False,
    )
    # Build a fresh EditSuggestion whose base_hash is
    # wrong; ``_check_stale_base_hash`` compares against
    # the parser-owned ``context.mutable_ranges[*].content_hash``.
    from metacrucible.optimizer import EditSuggestion

    stale_suggestion = EditSuggestion(
        record_type="edit_suggestion",
        suggestion_id="opt9-stale-direct",
        run_id=context.run_id,
        round_id="round-direct",
        timestamp="2026-01-01T00:00:00Z",
        range_id=0,
        base_hash=hashlib.sha256(b"definitely-not-the-body").hexdigest(),
        intent="stale_direct_check",
        replacement="",
        rationale="",
        routing=False,
    )
    direct_blockers = _check_stale_base_hash(
        [stale_suggestion], context
    )
    stale_direct_blockers = [
        b for b in direct_blockers
        if isinstance(b, dict) and b.get("id") == STALE_BASE_HASH_BLOCKER
    ]
    assert stale_direct_blockers, (
        f"_check_stale_base_hash must emit the "
        f"{STALE_BASE_HASH_BLOCKER!r} blocker id for a "
        f"stale base_hash; got {direct_blockers!r}"
    )


# --------------------------------------------------------------------------- #
# BLK-2 — OPT-6 ACCEPTED-path regression test                                 #
# --------------------------------------------------------------------------- #

def test_optimize_pipeline_accepted_path() -> None:
    """BLK-2: a candidate with strict eval-split improvement
    AND zero new held-out regressions must reach ACCEPTED
    status; the candidate's text is written to disk and
    ``acceptance_decision.accepted`` is True.

    This pins the OPT-6 acceptance comparator end-to-end.
    Without it, no test in the suite drives the pipeline to
    the ACCEPTED branch (BLK-1 made the path unreachable;
    the inverted ``fits_in_range`` check blocked every real
    edit at step 3f, so the runner exited BLOCKED before
    the acceptance comparator ever ran). After BLK-1 the
    path is reachable; this test confirms it works.

    Test mechanics:

      - The eval_call_fn returns FAIL for ``eval-1`` when
        the on-disk artifact body does NOT contain the
        ``OPT9_ACCEPT_MARKER`` marker (baseline), and PASS
        when the marker is present (candidate).
      - The held-out case ``held-1`` always returns PASS,
        so the candidate cannot introduce a new held-out
        regression.
      - The LLM ``call_fn`` returns a valid
        ``round_reflection`` whose ``suggested_edits`` has
        one entry targeting the body's ``range_id=0`` with
        a ``replacement`` that contains the accept marker.
      - The candidate's body differs from the base, so the
        inverted BLK-1 fits_in_range check would have
        blocked the round; the test fails if BLK-1 is
        reintroduced.
    """
    from metacrucible.optimizer import run_optimizer_pipeline

    workspace = _tmp_workspace()
    benchmark = workspace / BENCHMARK_FILE_NAME
    artifact = _opt9_seed_artifact(workspace)
    _opt9_seed_envelope(workspace, artifact)
    _write_jsonl(
        benchmark,
        [
            _metadata_record(),
            _reviewed_case("eval-1", split="eval"),
            _reviewed_case("held-1", split="held_out"),
        ],
    )

    body_hash = _opt9_body_hash()
    accept_marker = "OPT9_ACCEPT_MARKER"
    candidate_body = _opt9_body_text() + "\n" + accept_marker + "\n"

    def _accept_call_fn(*, repair_context: Any = None) -> dict[str, Any]:
        return {
            "rationale": "ACCEPTED-path regression: add marker",
            "suggested_edits": [
                {
                    "range_id": 0,
                    "base_hash": body_hash,
                    "intent": "add_accept_marker",
                    "replacement": candidate_body,
                    "rationale": "candidate adds accept marker",
                    "routing": False,
                }
            ],
        }

    def _accept_eval_call_fn(case: Mapping[str, Any]) -> Mapping[str, Any]:
        case_id = case.get("case_id", "")
        if case_id == "eval-1":
            artifact_text = artifact.read_text(encoding="utf-8")
            if accept_marker in artifact_text:
                return {"status": "PASS", "case_id": case_id}
            return {"status": "FAIL", "case_id": case_id}
        # held-out case: always PASS so no new regression.
        return {"status": "PASS", "case_id": case_id}

    result = run_optimizer_pipeline(
        workspace=workspace,
        benchmark_path=benchmark,
        artifact_path=artifact,
        call_fn=_accept_call_fn,
        max_rounds=1,
        human_confirmed=False,
        eval_call_fn=_accept_eval_call_fn,
    )

    # The pipeline must reach ACCEPTED after BLK-1 fix.
    assert result.status == "ACCEPTED", (
        f"strict eval improvement + zero new held-out "
        f"regressions must reach ACCEPTED status; got "
        f"status={result.status!r} "
        f"acceptance_decision={result.acceptance_decision!r}"
    )
    assert result.best_revision is not None, (
        f"accepted run must populate best_revision; got "
        f"{result.best_revision!r}"
    )
    assert result.acceptance_decision.get("accepted") is True, (
        f"acceptance_decision.accepted must be True on an "
        f"accepted run; got "
        f"acceptance_decision={result.acceptance_decision!r}"
    )
    # The comparator's machine-readable verdict must be
    # "accepted" (the strict-improvement-and-clean-held-out
    # reason), not "eval_no_improvement" or
    # "held_out_regression".
    assert result.acceptance_decision.get("reason") == "accepted", (
        f"acceptance_decision.reason must be 'accepted' "
        f"on a strict-improvement-and-clean-held-out run; "
        f"got {result.acceptance_decision.get('reason')!r}"
    )
    # The artifact on disk must be the candidate text
    # (the accepted candidate is committed, not rolled
    # back). This is the load-bearing end-to-end check:
    # it proves the runner took the ACCEPTED branch and
    # skipped the rollback path.
    import hashlib as _hashlib
    artifact_bytes_after = artifact.read_bytes()
    artifact_sha_after = _hashlib.sha256(
        artifact_bytes_after
    ).hexdigest()
    artifact_sha_before = _hashlib.sha256(
        _opt9_body_text().encode("utf-8")
        # The base artifact is a Skill-shaped fixture
        # with frontmatter + the canonical OPT-9 body.
        # We compare against the seeded on-disk bytes
        # (the test seeds it via _opt9_seed_artifact).
    ).hexdigest()
    # The accepted candidate wrote new bytes; the file
    # SHA must differ from the seed hash that the runner
    # saw at baseline-eval time. The runner read
    # ``base_artifact_text = Path(artifact).read_bytes()``
    # before apply; if rollback ran, the on-disk bytes
    # would equal that hash. We assert the OPPOSITE: the
    # accepted candidate committed a different artifact.
    base_artifact_hash = best_revision_pre_sha = None
    # Use best_revision.artifact_text_sha256 when
    # available; otherwise compare to the seeded bytes
    # which we know the baseline saw.
    if result.best_revision is not None:
        best_revision_pre_sha = (
            result.best_revision.get("artifact_text_sha256")
        )
    # The on-disk SHA must equal the best_revision's
    # candidate SHA (the runner wrote the candidate to
    # disk and did not roll back).
    assert best_revision_pre_sha is not None
    assert artifact_sha_after == best_revision_pre_sha, (
        f"accepted candidate's on-disk SHA must match "
        f"best_revision.artifact_text_sha256 (no "
        f"rollback); on-disk={artifact_sha_after!r} "
        f"best_revision="
        f"{best_revision_pre_sha!r}"
    )
    # The history must record the optimize_accepted event.
    records = _opt9_read_history(workspace)
    accepted_events = [
        r for r in records
        if isinstance(r, dict)
        and r.get("event") == "optimize_accepted"
    ]
    assert accepted_events, (
        f"accepted run must append an optimize_accepted "
        f"history event; got events="
        f"{[r.get('event') for r in records]!r}"
    )


# --------------------------------------------------------------------------- #
# NB-4 parity test — optimizer._split_artifact_text vs artifact._split_frontmatter
# --------------------------------------------------------------------------- #

def test_split_artifact_text_matches_parser_frontmatter_split() -> None:
    """NB-4 parity: ``optimizer._split_artifact_text`` and
    ``artifact._split_frontmatter`` must produce equivalent
    ``(frontmatter, body)`` splits for every well-formed
    artifact the parser accepts. This pins the single
    convention: any future frontmatter-shape change in
    :mod:`metacrucible.artifact` must keep the optimizer's
    helper in lockstep (or the test will fail).
    """
    from metacrucible.artifact import _split_frontmatter
    from metacrucible.optimizer import _split_artifact_text

    # A representative Skill artifact source.
    skill_source = (
        "---\n"
        "name: parity-skill\n"
        "description: NB-4 parity fixture\n"
        "---\n"
        "# body\nThe body is the only mutable range.\n"
    )
    # A representative subagent artifact source with a
    # systemPrompt block.
    subagent_source = (
        "---\n"
        "name: parity-subagent\n"
        "description: NB-4 parity subagent fixture\n"
        "systemPrompt: |\n"
        "  You are a parity-test agent.\n"
        "---\n"
        "Agent body text for the parity test.\n"
    )

    for label, source in (
        ("skill", skill_source),
        ("subagent", subagent_source),
    ):
        parser_front, parser_body = _split_frontmatter(source)
        opt_front, opt_body = _split_artifact_text(source)
        assert parser_front == opt_front, (
            f"NB-4 parity ({label}): optimizer frontmatter "
            f"differs from parser; parser={parser_front!r} "
            f"optimizer={opt_front!r}"
        )
        assert parser_body == opt_body, (
            f"NB-4 parity ({label}): optimizer body differs "
            f"from parser; parser={parser_body!r} "
            f"optimizer={opt_body!r}"
        )
