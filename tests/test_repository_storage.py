"""Tests for Issue #5: Repository/global storage + envelope/state/cache.

Issue #5 pins the storage-layer contract that subsequent issues
(evaluate, optimize, synthesize) build on:

  - Repository side (``<artifact>/.metacrucible/``) stores only
    lightweight history and state, never heavy evidence or raw
    transcripts.
  - User-global side (``~/.metacrucible/``) stores heavy evidence,
    raw transcripts, and result cache.
  - Cache identity is a full tuple of (artifact, executable case,
    harness, adapter/runtime version, model identities, execution
    boundary). Any single mismatch must be a cache miss.
  - Cleanup of raw evidence and cache records metadata (timestamp,
    retention policy, items removed) without deleting receipts,
    summaries, or trajectory digests.

These tests are the red step: ``metacrucible.storage`` is not
implemented yet, so importing it must fail. Once it lands, the tests
turn green and pin the contract from the acceptance criteria in
Issue #5.

The implementation under test (not yet written) is expected to
live under ``metacrucible.storage`` and expose at least:

  - ``RepositoryStorage`` - per-artifact ``.metacrucible/`` layout.
  - ``UserGlobalStorage`` - ``~/.metacrucible/`` layout.
  - ``CacheIdentity`` - full identity tuple for cache matching.
  - ``CleanupReport`` - recorded cleanup events.

References
----------
- ADR 0016 (store light history locally, heavy evidence globally).
- ADR 0020 (minimal write surface).
- ADR 0024 / 0030 (receipts and versioned evidence bundles).
- Issue #5 acceptance criteria.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

STORAGE_MODULE = "metacrucible.storage"

REPO_DIR_NAME = ".metacrucible"
GLOBAL_DIR_NAME = "metacrucible"

# Pushed past the 30-day retention default so prune exercises the
# retention path. 60 days is enough headroom for slow CI clocks.
_OLD_MTIME_SECONDS = 60 * 86400


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def isolated_artifact_dir(tmp_path: Path) -> Path:
    """Return a temp dir that pretends to be an artifact's working tree."""
    return tmp_path


@pytest.fixture()
def isolated_global_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pin ``HOME`` to a temp dir so the global storage layer does not pollute
    the developer's real ``~/.metacrucible/``."""
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    return fake_home


@pytest.fixture()
def storage() -> Any:
    """Import the storage module; the test fails (red step) if it does not exist."""
    import importlib

    try:
        return importlib.import_module(STORAGE_MODULE)
    except ImportError as exc:
        pytest.fail(
            f"storage module {STORAGE_MODULE!r} is not implemented yet "
            f"(Issue #5 red step). Expected symbols: RepositoryStorage, "
            f"UserGlobalStorage, CacheIdentity. ImportError: {exc}"
        )


def _age_file_to_past_retention(path: Path, days: int) -> None:
    """Set ``path``'s mtime to ``now - days * 86400`` so prune fires."""
    old = time.time() - (days * 86400)
    os.utime(path, (old, old))


# --------------------------------------------------------------------------- #
# AC1 — Repository side stores lightweight history/state only                #
# --------------------------------------------------------------------------- #


def test_repository_storage_class_exists(storage: Any) -> None:
    """The storage module must expose a ``RepositoryStorage`` class."""
    assert hasattr(storage, "RepositoryStorage"), (
        f"{STORAGE_MODULE!r} must expose a RepositoryStorage class; "
        f"got attributes {sorted(dir(storage))!r}"
    )


def test_repository_storage_root_is_under_artifact_dir(
    storage: Any, isolated_artifact_dir: Path
) -> None:
    """``RepositoryStorage`` lives at ``<artifact>/.metacrucible/``."""
    repo = storage.RepositoryStorage(isolated_artifact_dir)
    assert repo.root == isolated_artifact_dir / REPO_DIR_NAME, (
        f"repository storage root must be "
        f"{isolated_artifact_dir / REPO_DIR_NAME}; got {repo.root!r}"
    )


def test_repository_storage_creates_root_on_init(
    storage: Any, isolated_artifact_dir: Path
) -> None:
    """Constructing the storage object must create the ``.metacrucible/`` root."""
    storage.RepositoryStorage(isolated_artifact_dir)
    assert (isolated_artifact_dir / REPO_DIR_NAME).is_dir(), (
        f"{isolated_artifact_dir / REPO_DIR_NAME} must be created when "
        f"the storage object is constructed"
    )


def test_repository_storage_writes_envelope(
    storage: Any, isolated_artifact_dir: Path
) -> None:
    """The repository side stores an ``envelope.json`` next to the artifact."""
    repo = storage.RepositoryStorage(isolated_artifact_dir)
    repo.write_envelope(
        {
            "schema_version": 1,
            "artifact_kind": "skill",
            "artifact_sha": "deadbeef" * 8,
            "envelope_status": "active",
        }
    )
    envelope_path = isolated_artifact_dir / REPO_DIR_NAME / "envelope.json"
    assert envelope_path.is_file(), (
        f"repository storage must write {envelope_path.relative_to(isolated_artifact_dir)}"
    )
    payload = json.loads(envelope_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["artifact_kind"] == "skill"


def test_repository_storage_writes_state(
    storage: Any, isolated_artifact_dir: Path
) -> None:
    """The repository side stores a ``state.json`` for current best revision / run index."""
    repo = storage.RepositoryStorage(isolated_artifact_dir)
    repo.write_state(
        {
            "schema_version": 1,
            "best_revision_id": "rev-001",
            "last_run_id": "run-abc",
        }
    )
    state_path = isolated_artifact_dir / REPO_DIR_NAME / "state.json"
    assert state_path.is_file(), (
        f"repository storage must write {state_path.relative_to(isolated_artifact_dir)}"
    )
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["best_revision_id"] == "rev-001"
    assert payload["last_run_id"] == "run-abc"


def test_repository_storage_appends_history_records(
    storage: Any, isolated_artifact_dir: Path
) -> None:
    """The repository side keeps a lightweight ``history.jsonl`` of revisions."""
    repo = storage.RepositoryStorage(isolated_artifact_dir)
    repo.append_history({"revision_id": "rev-001", "decision": "accepted"})
    repo.append_history({"revision_id": "rev-002", "decision": "rejected"})

    history_path = isolated_artifact_dir / REPO_DIR_NAME / "history.jsonl"
    assert history_path.is_file(), (
        f"repository storage must write {history_path.relative_to(isolated_artifact_dir)}"
    )
    lines = [
        line for line in history_path.read_text(encoding="utf-8").splitlines() if line
    ]
    assert len(lines) == 2, f"history must have 2 records; got {len(lines)}"
    first = json.loads(lines[0])
    second = json.loads(lines[1])
    assert first["revision_id"] == "rev-001"
    assert second["decision"] == "rejected"


def test_repository_storage_does_not_hold_heavy_evidence_paths(
    storage: Any, isolated_artifact_dir: Path
) -> None:
    """Repository side must NOT create heavy evidence or raw transcript dirs.

    ADR 0016 says heavy evidence and cache live under the user-global
    side. The repository side only stores envelope, state, and history
    JSONL. The test asserts that the well-known heavy-side directory
    names do NOT appear inside ``.metacrucible/`` after a normal write.
    """
    repo = storage.RepositoryStorage(isolated_artifact_dir)
    repo.write_envelope({"schema_version": 1, "artifact_kind": "skill"})
    repo.write_state({"schema_version": 1, "best_revision_id": None})
    repo.append_history({"revision_id": "rev-001"})

    heavy_dirs = {"evidence", "cache", "raw", "transcripts"}
    present = {
        name
        for name in heavy_dirs
        if (isolated_artifact_dir / REPO_DIR_NAME / name).exists()
    }
    assert not present, (
        f"repository-side {REPO_DIR_NAME}/ must not contain heavy "
        f"evidence/cache dirs; found {sorted(present)!r}"
    )


# --------------------------------------------------------------------------- #
# AC2 — User-global side stores heavy evidence/cache                         #
# --------------------------------------------------------------------------- #


def test_user_global_storage_class_exists(storage: Any) -> None:
    """The storage module must expose a ``UserGlobalStorage`` class."""
    assert hasattr(storage, "UserGlobalStorage"), (
        f"{STORAGE_MODULE!r} must expose a UserGlobalStorage class; "
        f"got attributes {sorted(dir(storage))!r}"
    )


def test_user_global_storage_roots_under_home(
    storage: Any, isolated_global_home: Path
) -> None:
    """``UserGlobalStorage`` lives at ``$HOME/.metacrucible/``."""
    global_store = storage.UserGlobalStorage()
    assert global_store.root == isolated_global_home / f".{GLOBAL_DIR_NAME}", (
        f"global storage root must be "
        f"{isolated_global_home / ('.' + GLOBAL_DIR_NAME)}; "
        f"got {global_store.root!r}"
    )


def test_user_global_storage_creates_root_on_init(
    storage: Any, isolated_global_home: Path
) -> None:
    """Constructing the global storage object must create ``~/.metacrucible/``."""
    storage.UserGlobalStorage()
    assert (isolated_global_home / f".{GLOBAL_DIR_NAME}").is_dir(), (
        f"{isolated_global_home / ('.' + GLOBAL_DIR_NAME)} must be created when "
        f"the global storage object is constructed"
    )


def test_user_global_storage_writes_evidence_receipt(
    storage: Any, isolated_global_home: Path
) -> None:
    """The global side stores per-run evidence bundles keyed by ``run_id``."""
    global_store = storage.UserGlobalStorage()
    receipt = {
        "schema_version": 1,
        "run_id": "run-abc",
        "status": "PASS",
        "artifact_sha": "deadbeef" * 8,
    }
    global_store.write_receipt("run-abc", receipt)
    receipt_path = (
        isolated_global_home
        / f".{GLOBAL_DIR_NAME}"
        / "evidence"
        / "run-abc"
        / "receipt.json"
    )
    assert receipt_path.is_file(), (
        f"global storage must write {receipt_path.relative_to(isolated_global_home)}"
    )
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run-abc"
    assert payload["status"] == "PASS"


def test_user_global_storage_writes_evidence_summary(
    storage: Any, isolated_global_home: Path
) -> None:
    """The global side stores per-run ``summary.json`` for aggregate views."""
    global_store = storage.UserGlobalStorage()
    summary = {
        "schema_version": 1,
        "run_id": "run-abc",
        "counts": {"pass": 3, "fail": 1, "blocked": 0},
    }
    global_store.write_summary("run-abc", summary)
    summary_path = (
        isolated_global_home
        / f".{GLOBAL_DIR_NAME}"
        / "evidence"
        / "run-abc"
        / "summary.json"
    )
    assert summary_path.is_file(), (
        f"global storage must write {summary_path.relative_to(isolated_global_home)}"
    )
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    assert payload["counts"] == {"pass": 3, "fail": 1, "blocked": 0}


def test_user_global_storage_writes_raw_evidence(
    storage: Any, isolated_global_home: Path
) -> None:
    """Heavy raw evidence lives under the global side and is prune-eligible."""
    global_store = storage.UserGlobalStorage()
    global_store.write_raw_evidence("run-abc", "transcript.jsonl", "raw bytes here")
    raw_path = (
        isolated_global_home
        / f".{GLOBAL_DIR_NAME}"
        / "evidence"
        / "run-abc"
        / "raw"
        / "transcript.jsonl"
    )
    assert raw_path.is_file(), (
        f"global storage must write {raw_path.relative_to(isolated_global_home)}"
    )
    assert raw_path.read_text(encoding="utf-8") == "raw bytes here"


def test_repository_and_global_layouts_are_disjoint(
    storage: Any,
    isolated_artifact_dir: Path,
    isolated_global_home: Path,
) -> None:
    """Repository storage and global storage must use disjoint directory roots.

    ADR 0016: the repository side is the artifact-side, the global
    side is the user-side. The two layout roots must not overlap, so
    a misconfigured module that writes heavy evidence into the repo
    fails loud.
    """
    repo = storage.RepositoryStorage(isolated_artifact_dir)
    global_store = storage.UserGlobalStorage()
    assert repo.root != global_store.root, (
        f"repository root {repo.root} and global root {global_store.root} "
        f"must be disjoint paths"
    )


# --------------------------------------------------------------------------- #
# AC3 — Cache match uses full identity tuple                                  #
# --------------------------------------------------------------------------- #


def test_cache_identity_class_exists(storage: Any) -> None:
    """The storage module must expose a ``CacheIdentity`` type."""
    assert hasattr(storage, "CacheIdentity"), (
        f"{STORAGE_MODULE!r} must expose a CacheIdentity type; "
        f"got attributes {sorted(dir(storage))!r}"
    )


def test_cache_identity_carries_full_tuple(storage: Any) -> None:
    """``CacheIdentity`` must hold the full identity tuple from ADR 0030.

    Full tuple = (artifact, executable case, harness, adapter/runtime
    version, execution boundary, model identities). Missing any field
    is a contract violation.
    """
    identity = storage.CacheIdentity(
        artifact_sha="a" * 64,
        executable_case_sha="b" * 64,
        harness_sha="c" * 64,
        adapter_version="claude-code/0.4.1",
        model_identities={"control": "anthropic/claude-opus-4"},
        execution_boundary_id="eb-001",
    )
    payload = identity.as_dict()
    assert payload["artifact_sha"] == "a" * 64
    assert payload["executable_case_sha"] == "b" * 64
    assert payload["harness_sha"] == "c" * 64
    assert payload["adapter_version"] == "claude-code/0.4.1"
    assert payload["model_identities"] == {"control": "anthropic/claude-opus-4"}
    assert payload["execution_boundary_id"] == "eb-001"


def test_cache_identity_key_is_stable_for_equal_inputs(storage: Any) -> None:
    """Two ``CacheIdentity`` objects with equal fields must produce equal cache keys.

    The cache key is a deterministic hash of the full tuple so that
    cache lookups are pure functions of the identity.
    """
    a = storage.CacheIdentity(
        artifact_sha="a" * 64,
        executable_case_sha="b" * 64,
        harness_sha="c" * 64,
        adapter_version="claude-code/0.4.1",
        model_identities={"control": "anthropic/claude-opus-4"},
        execution_boundary_id="eb-001",
    )
    b = storage.CacheIdentity(
        artifact_sha="a" * 64,
        executable_case_sha="b" * 64,
        harness_sha="c" * 64,
        adapter_version="claude-code/0.4.1",
        model_identities={"control": "anthropic/claude-opus-4"},
        execution_boundary_id="eb-001",
    )
    assert a.cache_key() == b.cache_key(), (
        f"equal identities must hash to the same cache key; got "
        f"{a.cache_key()!r} vs {b.cache_key()!r}"
    )


def test_cache_identity_key_changes_when_artifact_sha_differs(storage: Any) -> None:
    """A different artifact SHA must produce a different cache key."""
    a = storage.CacheIdentity(
        artifact_sha="a" * 64,
        executable_case_sha="b" * 64,
        harness_sha="c" * 64,
        adapter_version="claude-code/0.4.1",
        model_identities={"control": "anthropic/claude-opus-4"},
        execution_boundary_id="eb-001",
    )
    b = storage.CacheIdentity(
        artifact_sha="d" * 64,  # different artifact
        executable_case_sha="b" * 64,
        harness_sha="c" * 64,
        adapter_version="claude-code/0.4.1",
        model_identities={"control": "anthropic/claude-opus-4"},
        execution_boundary_id="eb-001",
    )
    assert a.cache_key() != b.cache_key(), (
        "cache key must change when artifact_sha differs"
    )


def test_cache_identity_key_changes_when_case_sha_differs(storage: Any) -> None:
    """A different executable case SHA must produce a different cache key."""
    a = storage.CacheIdentity(
        artifact_sha="a" * 64,
        executable_case_sha="b" * 64,
        harness_sha="c" * 64,
        adapter_version="claude-code/0.4.1",
        model_identities={"control": "anthropic/claude-opus-4"},
        execution_boundary_id="eb-001",
    )
    b = storage.CacheIdentity(
        artifact_sha="a" * 64,
        executable_case_sha="e" * 64,  # different case
        harness_sha="c" * 64,
        adapter_version="claude-code/0.4.1",
        model_identities={"control": "anthropic/claude-opus-4"},
        execution_boundary_id="eb-001",
    )
    assert a.cache_key() != b.cache_key(), (
        "cache key must change when executable_case_sha differs"
    )


def test_cache_identity_key_changes_when_harness_sha_differs(storage: Any) -> None:
    """A different harness SHA must produce a different cache key."""
    a = storage.CacheIdentity(
        artifact_sha="a" * 64,
        executable_case_sha="b" * 64,
        harness_sha="c" * 64,
        adapter_version="claude-code/0.4.1",
        model_identities={"control": "anthropic/claude-opus-4"},
        execution_boundary_id="eb-001",
    )
    b = storage.CacheIdentity(
        artifact_sha="a" * 64,
        executable_case_sha="b" * 64,
        harness_sha="f" * 64,  # different harness
        adapter_version="claude-code/0.4.1",
        model_identities={"control": "anthropic/claude-opus-4"},
        execution_boundary_id="eb-001",
    )
    assert a.cache_key() != b.cache_key(), (
        "cache key must change when harness_sha differs"
    )


def test_cache_identity_key_changes_when_adapter_version_differs(storage: Any) -> None:
    """A different adapter version must produce a different cache key."""
    a = storage.CacheIdentity(
        artifact_sha="a" * 64,
        executable_case_sha="b" * 64,
        harness_sha="c" * 64,
        adapter_version="claude-code/0.4.1",
        model_identities={"control": "anthropic/claude-opus-4"},
        execution_boundary_id="eb-001",
    )
    b = storage.CacheIdentity(
        artifact_sha="a" * 64,
        executable_case_sha="b" * 64,
        harness_sha="c" * 64,
        adapter_version="claude-code/0.5.0",  # different adapter
        model_identities={"control": "anthropic/claude-opus-4"},
        execution_boundary_id="eb-001",
    )
    assert a.cache_key() != b.cache_key(), (
        "cache key must change when adapter_version differs"
    )


def test_cache_identity_key_changes_when_model_identities_differ(storage: Any) -> None:
    """A different model identity must produce a different cache key."""
    a = storage.CacheIdentity(
        artifact_sha="a" * 64,
        executable_case_sha="b" * 64,
        harness_sha="c" * 64,
        adapter_version="claude-code/0.4.1",
        model_identities={"control": "anthropic/claude-opus-4"},
        execution_boundary_id="eb-001",
    )
    b = storage.CacheIdentity(
        artifact_sha="a" * 64,
        executable_case_sha="b" * 64,
        harness_sha="c" * 64,
        adapter_version="claude-code/0.4.1",
        model_identities={"control": "anthropic/claude-opus-4.5"},  # different model
        execution_boundary_id="eb-001",
    )
    assert a.cache_key() != b.cache_key(), (
        "cache key must change when model_identities differs"
    )


def test_cache_identity_key_changes_when_execution_boundary_differs(
    storage: Any,
) -> None:
    """A different execution boundary identity must produce a different cache key."""
    a = storage.CacheIdentity(
        artifact_sha="a" * 64,
        executable_case_sha="b" * 64,
        harness_sha="c" * 64,
        adapter_version="claude-code/0.4.1",
        model_identities={"control": "anthropic/claude-opus-4"},
        execution_boundary_id="eb-001",
    )
    b = storage.CacheIdentity(
        artifact_sha="a" * 64,
        executable_case_sha="b" * 64,
        harness_sha="c" * 64,
        adapter_version="claude-code/0.4.1",
        model_identities={"control": "anthropic/claude-opus-4"},
        execution_boundary_id="eb-002",  # different boundary
    )
    assert a.cache_key() != b.cache_key(), (
        "cache key must change when execution_boundary_id differs"
    )


def test_user_global_cache_put_and_get_roundtrip(
    storage: Any, isolated_global_home: Path
) -> None:
    """Writing then reading a cache entry must return the stored payload."""
    global_store = storage.UserGlobalStorage()
    identity = storage.CacheIdentity(
        artifact_sha="a" * 64,
        executable_case_sha="b" * 64,
        harness_sha="c" * 64,
        adapter_version="claude-code/0.4.1",
        model_identities={"control": "anthropic/claude-opus-4"},
        execution_boundary_id="eb-001",
    )
    payload = {"result": "PASS", "score": 0.91}
    global_store.cache_put(identity, payload)
    loaded = global_store.cache_get(identity)
    assert loaded == payload, (
        f"cache roundtrip must return the same payload; got {loaded!r}"
    )


def test_user_global_cache_miss_returns_none(
    storage: Any, isolated_global_home: Path
) -> None:
    """An unknown cache identity must return ``None`` (a clean miss)."""
    global_store = storage.UserGlobalStorage()
    identity = storage.CacheIdentity(
        artifact_sha="a" * 64,
        executable_case_sha="b" * 64,
        harness_sha="c" * 64,
        adapter_version="claude-code/0.4.1",
        model_identities={"control": "anthropic/claude-opus-4"},
        execution_boundary_id="eb-001",
    )
    assert global_store.cache_get(identity) is None, (
        "cache_get on a missing key must return None"
    )


def test_user_global_cache_miss_on_single_field_mismatch(
    storage: Any, isolated_global_home: Path
) -> None:
    """A single-field mismatch in the identity tuple must be a cache miss.

    ADR 0030: cache match uses the full identity tuple. Any single
    mismatch must cause a miss so a stale result never poisons a
    new run.
    """
    global_store = storage.UserGlobalStorage()
    base = storage.CacheIdentity(
        artifact_sha="a" * 64,
        executable_case_sha="b" * 64,
        harness_sha="c" * 64,
        adapter_version="claude-code/0.4.1",
        model_identities={"control": "anthropic/claude-opus-4"},
        execution_boundary_id="eb-001",
    )
    global_store.cache_put(base, {"result": "PASS"})

    mismatched = storage.CacheIdentity(
        artifact_sha="d" * 64,  # one field changed
        executable_case_sha="b" * 64,
        harness_sha="c" * 64,
        adapter_version="claude-code/0.4.1",
        model_identities={"control": "anthropic/claude-opus-4"},
        execution_boundary_id="eb-001",
    )
    assert global_store.cache_get(mismatched) is None, (
        "cache must miss when a single field of the identity tuple changes"
    )


def test_cache_key_is_content_addressed_hex(storage: Any) -> None:
    """The cache key must be a content-addressed hex string, not a local path.

    ADR 0030: evidence stores hashes/categories/relative references,
    not raw local paths. The cache key must be a deterministic hex
    digest so a cleanup pass cannot break cache identity.
    """
    identity = storage.CacheIdentity(
        artifact_sha="a" * 64,
        executable_case_sha="b" * 64,
        harness_sha="c" * 64,
        adapter_version="claude-code/0.4.1",
        model_identities={"control": "anthropic/claude-opus-4"},
        execution_boundary_id="eb-001",
    )
    key = identity.cache_key()
    # Hex-only, fixed length, no separators. SHA-256 = 64 hex chars.
    assert isinstance(key, str)
    assert all(c in "0123456789abcdef" for c in key), (
        f"cache key must be lowercase hex; got {key!r}"
    )
    assert len(key) == 64, (
        f"cache key must be a SHA-256 hex digest (64 chars); got {len(key)} chars"
    )


# --------------------------------------------------------------------------- #
# AC4 — Cleanup metadata is recorded                                         #
# --------------------------------------------------------------------------- #


def test_user_global_storage_prune_raw_evidence_keeps_receipts(
    storage: Any, isolated_global_home: Path
) -> None:
    """Pruning raw evidence must NOT delete receipt.json or summary.json.

    ADR 0030: ``cleanup commands prune raw evidence or cache without
    deleting receipts, summaries, or trajectory digests.''
    """
    global_store = storage.UserGlobalStorage()
    global_store.write_receipt("run-abc", {"run_id": "run-abc", "status": "PASS"})
    global_store.write_summary("run-abc", {"run_id": "run-abc", "counts": {"pass": 1}})
    raw_path = global_store.write_raw_evidence(
        "run-abc", "transcript.jsonl", "raw bytes"
    )
    # Push the raw file's mtime past the retention cutoff so the prune
    # actually fires. ADR 0030 retention is 30 days; 60 days is enough.
    _age_file_to_past_retention(raw_path, days=60)

    report = global_store.prune_raw_evidence(retention_days=30)

    bundle = isolated_global_home / f".{GLOBAL_DIR_NAME}" / "evidence" / "run-abc"
    assert (bundle / "receipt.json").is_file(), (
        "prune must NOT delete receipt.json"
    )
    assert (bundle / "summary.json").is_file(), (
        "prune must NOT delete summary.json"
    )
    assert not (bundle / "raw").exists(), (
        "prune must delete the raw/ directory when retention is exceeded"
    )
    assert report["retention_days"] == 30


def test_user_global_storage_prune_records_cleanup_metadata(
    storage: Any, isolated_global_home: Path
) -> None:
    """Pruning must record a cleanup metadata file inside the global state.

    ADR 0030 + Issue #5: ``Cleanup metadata is recorded.'' The cleanup
    record is itself a small durable artifact that lives alongside
    evidence so operators can see what was pruned and when.
    """
    global_store = storage.UserGlobalStorage()
    raw_path = global_store.write_raw_evidence(
        "run-abc", "transcript.jsonl", "raw bytes"
    )
    # Push the raw file's mtime past the retention cutoff so the prune
    # actually fires.
    _age_file_to_past_retention(raw_path, days=60)

    global_store.prune_raw_evidence(retention_days=30)

    cleanup_path = (
        isolated_global_home
        / f".{GLOBAL_DIR_NAME}"
        / "evidence"
        / "run-abc"
        / "cleanup.json"
    )
    assert cleanup_path.is_file(), (
        f"prune must write {cleanup_path.relative_to(isolated_global_home)}"
    )
    payload = json.loads(cleanup_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["retention_days"] == 30
    assert "pruned_at" in payload
    assert payload["pruned_at"], "pruned_at timestamp must be non-empty"
    assert payload["removed_paths"], "cleanup record must list removed paths"


def test_user_global_storage_prune_cache_records_cleanup_metadata(
    storage: Any, isolated_global_home: Path
) -> None:
    """Pruning the cache must also record cleanup metadata (separate log)."""
    global_store = storage.UserGlobalStorage()
    identity = storage.CacheIdentity(
        artifact_sha="a" * 64,
        executable_case_sha="b" * 64,
        harness_sha="c" * 64,
        adapter_version="claude-code/0.4.1",
        model_identities={"control": "anthropic/claude-opus-4"},
        execution_boundary_id="eb-001",
    )
    global_store.cache_put(identity, {"result": "PASS"})

    report = global_store.prune_cache()

    cleanup_log = (
        isolated_global_home / f".{GLOBAL_DIR_NAME}" / "cache" / "cleanup.jsonl"
    )
    assert cleanup_log.is_file(), (
        f"cache prune must write {cleanup_log.relative_to(isolated_global_home)}"
    )
    lines = [ln for ln in cleanup_log.read_text(encoding="utf-8").splitlines() if ln]
    assert lines, "cache cleanup log must contain at least one record"
    first = json.loads(lines[0])
    assert first["schema_version"] == 1
    assert "pruned_at" in first
    assert first["removed_count"] >= 1
    assert report["removed_count"] >= 1


def test_user_global_storage_prune_reports_zero_when_nothing_to_prune(
    storage: Any, isolated_global_home: Path
) -> None:
    """Pruning when no raw evidence is present must be a no-op that still records."""
    global_store = storage.UserGlobalStorage()
    report = global_store.prune_raw_evidence(retention_days=30)
    assert report["removed_paths"] == [], (
        f"prune with nothing to remove must report empty removed_paths; "
        f"got {report!r}"
    )
    # The cleanup record is only written when there was at least one
    # evidence bundle, but the API must never raise in the empty case.
    assert report["retention_days"] == 30


def test_cleanup_record_carries_retention_policy(
    storage: Any, isolated_global_home: Path
) -> None:
    """Cleanup metadata must record the retention policy used.

    The retention policy is what makes the prune reproducible: a future
    operator looking at the cleanup.json must be able to recover the
    threshold that triggered the prune.
    """
    global_store = storage.UserGlobalStorage()
    raw_path = global_store.write_raw_evidence(
        "run-abc", "transcript.jsonl", "raw bytes"
    )
    # Push the raw file's mtime past the retention cutoff.
    _age_file_to_past_retention(raw_path, days=60)

    global_store.prune_raw_evidence(retention_days=7)

    cleanup_path = global_store.evidence_bundle_dir("run-abc") / "cleanup.json"
    payload = json.loads(cleanup_path.read_text(encoding="utf-8"))
    assert payload["retention_days"] == 7, (
        f"cleanup record must carry the retention policy used; got {payload!r}"
    )

# --------------------------------------------------------------------------- #
# AC5 — Storage hardening (independent-review follow-ups)                    #
# --------------------------------------------------------------------------- #

def test_evidence_bundle_dir_rejects_path_traversal_run_id(
    storage: Any, isolated_global_home: Path
) -> None:
    """``evidence_bundle_dir`` must reject ``run_id`` that escapes the
    evidence root via traversal or absolute paths.

    Independent review concern: an attacker-controlled ``run_id`` (or a
    mistyped one) must never resolve outside ``$HOME/.metacrucible/evidence/``.
    """
    global_store = storage.UserGlobalStorage()
    with pytest.raises(ValueError):
        global_store.evidence_bundle_dir("../escape")

def test_evidence_bundle_dir_rejects_absolute_run_id(
    storage: Any, isolated_global_home: Path
) -> None:
    """``evidence_bundle_dir`` must reject absolute ``run_id``."""
    global_store = storage.UserGlobalStorage()
    with pytest.raises(ValueError):
        global_store.evidence_bundle_dir("/etc/evil")

def test_evidence_bundle_dir_rejects_backslash_run_id(
    storage: Any, isolated_global_home: Path
) -> None:
    """``evidence_bundle_dir`` must reject backslash separators."""
    global_store = storage.UserGlobalStorage()
    with pytest.raises(ValueError):
        global_store.evidence_bundle_dir("..\\evil")

def test_write_receipt_rejects_path_traversal_run_id(
    storage: Any, isolated_global_home: Path
) -> None:
    """``write_receipt`` must reject ``run_id`` that escapes the evidence root."""
    global_store = storage.UserGlobalStorage()
    with pytest.raises(ValueError):
        global_store.write_receipt("../escape", {"schema_version": 1})

def test_write_receipt_rejects_absolute_run_id(
    storage: Any, isolated_global_home: Path
) -> None:
    """``write_receipt`` must reject absolute ``run_id``."""
    global_store = storage.UserGlobalStorage()
    with pytest.raises(ValueError):
        global_store.write_receipt("/etc/evil", {"schema_version": 1})

def test_write_summary_rejects_path_traversal_run_id(
    storage: Any, isolated_global_home: Path
) -> None:
    """``write_summary`` must reject ``run_id`` that escapes the evidence root."""
    global_store = storage.UserGlobalStorage()
    with pytest.raises(ValueError):
        global_store.write_summary("../escape", {"schema_version": 1})

def test_write_trajectory_digest_rejects_path_traversal_run_id(
    storage: Any, isolated_global_home: Path
) -> None:
    """``write_trajectory_digest`` must reject ``run_id`` that escapes."""
    global_store = storage.UserGlobalStorage()
    with pytest.raises(ValueError):
        global_store.write_trajectory_digest("../escape", {"schema_version": 1})

def test_write_raw_evidence_rejects_path_traversal_name(
    storage: Any, isolated_global_home: Path
) -> None:
    """``write_raw_evidence`` must reject ``name`` with ``..`` traversal."""
    global_store = storage.UserGlobalStorage()
    with pytest.raises(ValueError):
        global_store.write_raw_evidence("run-abc", "../escape.txt", "x")

def test_write_raw_evidence_rejects_absolute_name(
    storage: Any, isolated_global_home: Path
) -> None:
    """``write_raw_evidence`` must reject absolute ``name`` paths."""
    global_store = storage.UserGlobalStorage()
    with pytest.raises(ValueError):
        global_store.write_raw_evidence("run-abc", "/etc/passwd", "x")

def test_write_raw_evidence_rejects_subpath_separator(
    storage: Any, isolated_global_home: Path
) -> None:
    """``write_raw_evidence`` ``name`` is a flat filename — slashes are rejected.

    Independent review: ``name`` must be a flat filename under ``raw/``.
    Sub-paths are not used by the current contract and would broaden
    the writable surface for free; reject them.
    """
    global_store = storage.UserGlobalStorage()
    with pytest.raises(ValueError):
        global_store.write_raw_evidence("run-abc", "subdir/file.jsonl", "x")

def test_write_raw_evidence_rejects_backslash_in_name(
    storage: Any, isolated_global_home: Path
) -> None:
    """``write_raw_evidence`` must reject Windows-style separators in ``name``."""
    global_store = storage.UserGlobalStorage()
    with pytest.raises(ValueError):
        global_store.write_raw_evidence("run-abc", "subdir\\file.jsonl", "x")

def test_write_raw_evidence_rejects_dotdot_alone(
    storage: Any, isolated_global_home: Path
) -> None:
    """``write_raw_evidence`` must reject the bare ``..`` filename."""
    global_store = storage.UserGlobalStorage()
    with pytest.raises(ValueError):
        global_store.write_raw_evidence("run-abc", "..", "x")

def test_path_validation_does_not_create_evidence_bundles(
    storage: Any, isolated_global_home: Path
) -> None:
    """A rejected ``run_id`` must not create any bundle directory on disk.

    Independent review: defense-in-depth — validation must fail
    *before* any ``mkdir`` so a malformed input cannot leave a
    half-created bundle on disk for the next call to find.
    """
    global_store = storage.UserGlobalStorage()
    evidence_root = (
        isolated_global_home / f".{GLOBAL_DIR_NAME}" / "evidence"
    )
    assert evidence_root.is_dir(), (
        "evidence root should exist from UserGlobalStorage init"
    )
    with pytest.raises(ValueError):
        global_store.write_receipt("../escape", {"schema_version": 1})
    # No bundle should have been created outside (or inside) the
    # evidence root from the rejected call.
    assert list(evidence_root.iterdir()) == [], (
        "rejected run_id must not create any evidence bundle directory"
    )

def test_user_global_storage_init_missing_home_raises_value_error(
    storage: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``UserGlobalStorage()`` with no ``home=`` and no ``$HOME`` must
    raise a deterministic ``ValueError`` (not ``KeyError``).

    Independent review concern: a bare ``os.environ['HOME']`` lookup
    raises ``KeyError`` when HOME is unset (common in containers, on
    Windows, or in test harnesses that strip the env). The contract
    surfaces a clean ``ValueError`` with a clear message so callers
    can either set ``HOME`` or pass ``home=`` explicitly.
    """
    monkeypatch.delenv("HOME", raising=False)
    with pytest.raises(ValueError):
        storage.UserGlobalStorage()

def test_user_global_storage_init_explicit_home_works_without_home_env(
    storage: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit ``home=`` argument must work even when ``$HOME`` is unset."""
    monkeypatch.delenv("HOME", raising=False)
    global_store = storage.UserGlobalStorage(home=tmp_path)
    expected = (tmp_path.resolve() / f".{GLOBAL_DIR_NAME}")
    assert global_store.root == expected, (
        f"explicit home= must place root at {expected}; got {global_store.root!r}"
    )

def test_cache_identity_immune_to_caller_mutation_of_model_identities(
    storage: Any,
) -> None:
    """``CacheIdentity`` must be immune to caller mutation of the
    ``model_identities`` mapping after construction.

    Independent review: the dataclass is ``frozen=True`` but a
    ``Mapping[str, str]`` field still holds a reference to the
    caller's dict. If the caller mutates the dict between two
    ``cache_key()`` calls, the keys diverge and the cache contract
    breaks. The fix: copy and freeze the mapping in ``__post_init__``
    so subsequent mutations are invisible to the identity.
    """
    identities: dict[str, str] = {"control": "anthropic/claude-opus-4"}
    identity = storage.CacheIdentity(
        artifact_sha="a" * 64,
        executable_case_sha="b" * 64,
        harness_sha="c" * 64,
        adapter_version="claude-code/0.4.1",
        model_identities=identities,
        execution_boundary_id="eb-001",
    )
    snapshot_key = identity.cache_key()
    snapshot_dict = identity.as_dict()
    snapshot_pairs = tuple(sorted(snapshot_dict["model_identities"].items()))

    # Caller mutates the dict they passed in.
    identities["control"] = "mutated"
    identities["new_key"] = "added"
    identities.pop("control", None)
    identities["another"] = "value"

    # The identity's view of model_identities must be unchanged.
    assert identity.cache_key() == snapshot_key, (
        "cache_key must be stable across caller mutations of the "
        "original model_identities dict"
    )
    assert tuple(sorted(identity.as_dict()["model_identities"].items())) == snapshot_pairs, (
        "as_dict() model_identities must reflect the construction-time "
        "values, not later mutations of the caller's dict"
    )

def test_cache_identity_model_identities_remain_sorted_after_construction(
    storage: Any,
) -> None:
    """``as_dict()`` must keep ``model_identities`` sorted by key regardless
    of the order the caller passed in, even if the caller later mutates.
    """
    identity = storage.CacheIdentity(
        artifact_sha="a" * 64,
        executable_case_sha="b" * 64,
        harness_sha="c" * 64,
        adapter_version="claude-code/0.4.1",
        model_identities={"z": "1", "a": "2", "m": "3"},
        execution_boundary_id="eb-001",
    )
    payload = identity.as_dict()
    keys = list(payload["model_identities"].keys())
    assert keys == sorted(keys), (
        f"model_identities must be sorted in as_dict(); got {keys!r}"
    )
    assert identity.cache_key() == storage.CacheIdentity(
        artifact_sha="a" * 64,
        executable_case_sha="b" * 64,
        harness_sha="c" * 64,
        adapter_version="claude-code/0.4.1",
        model_identities={"a": "2", "m": "3", "z": "1"},
        execution_boundary_id="eb-001",
    ).cache_key(), (
        "cache_key must be stable under permutation of the input mapping"
    )

def test_prune_raw_evidence_return_aggregates_removed_paths_across_bundles(
    storage: Any, isolated_global_home: Path
) -> None:
    """``prune_raw_evidence`` must aggregate ``removed_paths`` and
    ``removed_count`` across every pruned bundle in the return value.

    Independent review: the previous return shape described only the
    *last* bundle touched (and was effectively zero-valued), which
    made the return value useless for callers rendering prune results.
    The hardened contract: the returned ``CleanupReport`` aggregates
    paths from every bundle whose raw evidence exceeded retention.
    """
    global_store = storage.UserGlobalStorage()
    raw1 = global_store.write_raw_evidence("run-1", "transcript.jsonl", "r1")
    raw2 = global_store.write_raw_evidence("run-2", "transcript.jsonl", "r2")
    _age_file_to_past_retention(raw1, days=60)
    _age_file_to_past_retention(raw2, days=60)

    report = global_store.prune_raw_evidence(retention_days=30)

    assert report["removed_count"] == 2, (
        f"aggregate report must count raw files from every pruned bundle; "
        f"got removed_count={report['removed_count']!r}"
    )
    joined = "\n".join(report["removed_paths"])
    assert "run-1" in joined and "run-2" in joined, (
        f"aggregate removed_paths must include both bundles; "
        f"got {report['removed_paths']!r}"
    )
    assert report["retention_days"] == 30

def test_prune_raw_evidence_return_aggregates_partial_prune(
    storage: Any, isolated_global_home: Path
) -> None:
    """When some bundles are prune-eligible and others are not, the
    returned report must only aggregate the *pruned* bundles.

    Independent review: a "partial" pass (one bundle over retention,
    one under) must report only the actually-pruned paths — never
    include the fresh bundle's paths in the aggregate.
    """
    global_store = storage.UserGlobalStorage()
    old = global_store.write_raw_evidence("run-old", "transcript.jsonl", "old")
    fresh = global_store.write_raw_evidence("run-fresh", "transcript.jsonl", "new")
    _age_file_to_past_retention(old, days=60)
    # fresh is not aged; it must not be pruned.

    report = global_store.prune_raw_evidence(retention_days=30)

    joined = "\n".join(report["removed_paths"])
    assert "run-old" in joined, (
        f"pruned bundle must be in aggregate; got {report['removed_paths']!r}"
    )
    assert "run-fresh" not in joined, (
        f"fresh bundle must NOT be in aggregate; got {report['removed_paths']!r}"
    )
    assert report["removed_count"] == 1
