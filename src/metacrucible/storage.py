"""Storage layer for Issue #5: repository + user-global + cache.

MetaCrucible splits its persistent state across two disjoint roots so
that the working tree stays small and shareable, while heavy evidence
and cache live where they cannot leak into version control:

  - **Repository side** (``<artifact>/.metacrucible/``) stores only
    lightweight envelope, state, and history. ADR 0016 pins this split
    so a developer can share their repository without leaking raw
    transcripts or cached model outputs.

  - **User-global side** (``$HOME/.metacrucible/``) stores heavy
    evidence bundles (receipt, summary, raw, trajectory digest),
    per-case result cache, and cleanup metadata. ADR 0030 pins the
    evidence-bundle shape and the retention policy; cleanup commands
    prune raw evidence and cache without ever deleting receipts,
    summaries, or trajectory digests.

Cache identity is a full tuple of (artifact, executable case,
harness, adapter/runtime version, model identities, execution
boundary) so a single field mismatch is a guaranteed cache miss
(ADR 0030). The cache key is a deterministic SHA-256 hex digest of
the canonical JSON encoding of the tuple.

Cleanup metadata is recorded for every prune pass: each pruned
evidence bundle carries a ``cleanup.json`` describing what was
removed, when, and under which retention policy; cache cleanup
appends to ``cache/cleanup.jsonl`` so the log survives multiple
passes.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

__all__ = [
    "CacheIdentity",
    "CleanupReport",
    "RepositoryStorage",
    "UserGlobalStorage",
]


# --------------------------------------------------------------------------- #
# Constants — pinned by ADRs                                                  #
# --------------------------------------------------------------------------- #

#: Name of the per-artifact directory inside an artifact's working tree.
#: ADR 0016 / ADR 0020 pin this as the minimal repo-side write surface.
REPO_DIR_NAME = ".metacrucible"

#: Name of the user-global directory under ``$HOME``. ADR 0016 pins
#: ``~/.metacrucible/`` for the global side; the dotfile prefix is
#: kept consistent with the per-artifact side so both layers are
#: hidden by default.
GLOBAL_DIR_NAME = ".metacrucible"

#: Current schema version stamped onto every JSON we emit. Bumping it
#: is a breaking change and must be paired with a migration plan.
SCHEMA_VERSION = 1

#: Default raw-evidence retention, per ADR 0030 (30 days).
DEFAULT_RAW_RETENTION_DAYS = 30

#: Subdirectory inside an evidence bundle that holds raw, prune-eligible
#: evidence (transcripts, normalized event streams, model outputs).
RAW_SUBDIR = "raw"



# --------------------------------------------------------------------------- #
# Path validation                                                             #
# --------------------------------------------------------------------------- #

def _safe_bundle_key(value: str, *, kind: str) -> str:
    """Validate a flat path-safe identifier used as a ``run_id`` or raw
    evidence ``name``.

    ``run_id`` and ``name`` are joined to fixed root directories
    (``evidence/<run_id>`` and ``<bundle>/raw/<name>``), so they must
    be flat, non-empty, untrimmed strings. The check rejects:

      - empty strings and whitespace-only strings
      - path separators (``/`` and ``\\``)
      - ``..`` parent-directory references and the bare ``.`` name
      - null bytes (which can truncate paths on POSIX)

    Independent-review hardening: blocking traversal and absolute
    paths here keeps every evidence write rooted under the global
    storage root even when ``run_id`` or ``name`` come from
    attacker-controlled or mistyped inputs.
    """
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(
            f"{kind} must be a non-empty, untrimmed string; got {value!r}"
        )
    if "/" in value or "\\" in value:
        raise ValueError(
            f"{kind} must be a flat name without path separators; got {value!r}"
        )
    if value in {".", ".."}:
        raise ValueError(
            f"{kind} must not be a parent-directory reference; got {value!r}"
        )
    if "\x00" in value:
        raise ValueError(
            f"{kind} must not contain null bytes; got {value!r}"
        )
    return value

# --------------------------------------------------------------------------- #
# Cache identity                                                              #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CacheIdentity:
    """Full identity tuple for a per-case result cache entry.

    A cache hit requires every field to match (ADR 0030):

      - ``artifact_sha``            — canonical-source hash of the artifact
      - ``executable_case_sha``     — hash of the eligible reviewed case
      - ``harness_sha``             — hash of the evaluation/optimizer harness
      - ``adapter_version``         — runtime adapter identifier+version
      - ``execution_boundary_id``   — execution boundary identity
      - ``model_identities``        — frozen provider/model idents used

    Two ``CacheIdentity`` objects are equal iff every field is equal,
    and :meth:`cache_key` returns a SHA-256 hex digest of the
    canonical JSON encoding of the full tuple.
    """

    artifact_sha: str
    executable_case_sha: str
    harness_sha: str
    adapter_version: str
    execution_boundary_id: str
    model_identities: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Independent-review hardening: ``frozen=True`` only freezes
        # the dataclass fields, not the values they reference. The
        # caller's mutable dict (or any other Mapping) must not be
        # able to drift the cache key after construction. Copy and
        # wrap in a read-only proxy so subsequent mutations are
        # invisible to ``as_dict()`` / ``cache_key()``.
        if self.model_identities is None:
            object.__setattr__(self, "model_identities", MappingProxyType({}))
            return
        object.__setattr__(
            self,
            "model_identities",
            MappingProxyType(dict(self.model_identities)),
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a plain dict form suitable for ``receipt.json``.

        ``model_identities`` is sorted by key for stable JSON output
        so the cache key is independent of dict ordering.
        """
        return {
            "artifact_sha": self.artifact_sha,
            "executable_case_sha": self.executable_case_sha,
            "harness_sha": self.harness_sha,
            "adapter_version": self.adapter_version,
            "execution_boundary_id": self.execution_boundary_id,
            "model_identities": dict(sorted(self.model_identities.items())),
        }

    def cache_key(self) -> str:
        """Return a deterministic SHA-256 hex digest of the identity tuple.

        The cache key is computed over the canonical JSON encoding of
        :meth:`as_dict` so that a future change to the identity
        schema produces a key namespace shift without breaking the
        contract of "any single mismatch is a cache miss".
        """
        encoded = json.dumps(
            self.as_dict(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


# --------------------------------------------------------------------------- #
# Repository-side storage (lightweight only)                                  #
# --------------------------------------------------------------------------- #


class RepositoryStorage:
    """Lightweight per-artifact storage rooted at ``<artifact>/.metacrucible/``.

    The repository side stores exactly three durable files plus an
    append-only history stream. Heavy evidence, raw transcripts, and
    cache must never be written here; the global side owns those.

    Layout::

        <artifact>/.metacrucible/
        ├── envelope.json     # lightweight artifact metadata
        ├── state.json        # current best revision, last run id
        └── history.jsonl     # append-only revision history
    """

    def __init__(self, artifact_dir: str | os.PathLike[str]) -> None:
        self.artifact_dir = Path(artifact_dir).resolve()
        self.root = self.artifact_dir / REPO_DIR_NAME
        self.root.mkdir(parents=True, exist_ok=True)

    # -- Envelope -------------------------------------------------------------

    def write_envelope(self, payload: Mapping[str, Any]) -> Path:
        """Write ``envelope.json`` atomically.

        ``payload`` is augmented with ``schema_version`` if not present
        so downstream readers can branch on the version.
        """
        merged = {"schema_version": SCHEMA_VERSION, **dict(payload)}
        return _atomic_write_json(self.envelope_path, merged)

    def read_envelope(self) -> dict[str, Any]:
        """Read the envelope, returning an empty dict if the file is missing."""
        if not self.envelope_path.is_file():
            return {}
        return json.loads(self.envelope_path.read_text(encoding="utf-8"))

    @property
    def envelope_path(self) -> Path:
        return self.root / "envelope.json"

    # -- State ----------------------------------------------------------------

    def write_state(self, payload: Mapping[str, Any]) -> Path:
        """Write ``state.json`` atomically."""
        merged = {"schema_version": SCHEMA_VERSION, **dict(payload)}
        return _atomic_write_json(self.state_path, merged)

    def read_state(self) -> dict[str, Any]:
        """Read the state, returning an empty dict if the file is missing."""
        if not self.state_path.is_file():
            return {}
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    # -- History (append-only JSONL) -----------------------------------------

    def append_history(self, record: Mapping[str, Any]) -> Path:
        """Append a single JSON-encoded record to ``history.jsonl``.

        History is append-only: a record is one line of JSON. The file
        is created on first write. Records must be JSON-serializable;
        non-serializable values raise ``TypeError`` from the standard
        library rather than being silently stringified.
        """
        line = json.dumps(dict(record), sort_keys=True, separators=(",", ":"))
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return self.history_path

    def read_history(self) -> list[dict[str, Any]]:
        """Read all history records, skipping blank lines."""
        if not self.history_path.is_file():
            return []
        records: list[dict[str, Any]] = []
        for raw in self.history_path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            records.append(json.loads(raw))
        return records

    @property
    def history_path(self) -> Path:
        return self.root / "history.jsonl"


# --------------------------------------------------------------------------- #
# User-global storage (heavy evidence + cache + cleanup)                      #
# --------------------------------------------------------------------------- #


@dataclass
class CleanupReport:
    """Summary of a single cleanup pass.

    Returned by :meth:`UserGlobalStorage.prune_raw_evidence` and
    :meth:`UserGlobalStorage.prune_cache` so callers can render the
    result to humans or to ``--json`` output.
    """

    removed_paths: list[str]
    retention_days: int
    pruned_at: str
    removed_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "removed_paths": list(self.removed_paths),
            "retention_days": self.retention_days,
            "pruned_at": self.pruned_at,
            "removed_count": self.removed_count,
        }


class UserGlobalStorage:
    """Heavy user-global storage rooted at ``$HOME/.metacrucible/``.

    Layout::

        ~/.metacrucible/
        ├── evidence/
        │   └── <run_id>/
        │       ├── receipt.json           (durable)
        │       ├── summary.json           (durable)
        │       ├── trajectory-digest.json (durable)
        │       ├── cleanup.json           (durable, written by prune)
        │       └── raw/                   (prune-eligible)
        └── cache/
            ├── <key>.json
            └── cleanup.jsonl              (append-only cleanup log)

    The ``$HOME`` lookup is performed at construction time. Tests
    typically pin ``HOME`` via a fixture before instantiating this
    class.
    """

    def __init__(self, home: str | os.PathLike[str] | None = None) -> None:
        if home is None:
            try:
                home_path = Path(os.environ["HOME"])
            except KeyError as exc:
                # Independent-review hardening: a bare
                # ``os.environ['HOME']`` lookup raises ``KeyError``
                # when HOME is unset (common in containers, on
                # Windows, or in test harnesses that strip the env).
                # Surface a deterministic ``ValueError`` so callers
                # can either set ``HOME`` or pass ``home=`` explicitly.
                raise ValueError(
                    "UserGlobalStorage requires HOME to be set or an "
                    "explicit home= argument; got neither"
                ) from exc
        else:
            home_path = Path(home)
        self.root = (home_path / GLOBAL_DIR_NAME).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.evidence_dir = self.root / "evidence"
        self.cache_dir = self.root / "cache"
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # -- Bundle paths ---------------------------------------------------------

    def evidence_bundle_dir(self, run_id: str) -> Path:
        """Return the per-run evidence bundle directory, creating it lazily.

        ``run_id`` is validated via :func:`_safe_bundle_key` *before*
        any ``mkdir`` call so a rejected input cannot leave a
        half-created bundle on disk for the next call to find.
        """
        _safe_bundle_key(run_id, kind="run_id")
        bundle = self.evidence_dir / run_id
        bundle.mkdir(parents=True, exist_ok=True)
        return bundle

    # -- Receipt / summary / trajectory digest -------------------------------

    def write_receipt(self, run_id: str, payload: Mapping[str, Any]) -> Path:
        """Write ``<bundle>/receipt.json`` for ``run_id``."""
        merged = {"schema_version": SCHEMA_VERSION, **dict(payload)}
        return _atomic_write_json(
            self.evidence_bundle_dir(run_id) / "receipt.json", merged
        )

    def write_summary(self, run_id: str, payload: Mapping[str, Any]) -> Path:
        """Write ``<bundle>/summary.json`` for ``run_id``."""
        merged = {"schema_version": SCHEMA_VERSION, **dict(payload)}
        return _atomic_write_json(
            self.evidence_bundle_dir(run_id) / "summary.json", merged
        )

    def write_trajectory_digest(
        self, run_id: str, payload: Mapping[str, Any]
    ) -> Path:
        """Write ``<bundle>/trajectory-digest.json`` for ``run_id``."""
        merged = {"schema_version": SCHEMA_VERSION, **dict(payload)}
        return _atomic_write_json(
            self.evidence_bundle_dir(run_id) / "trajectory-digest.json", merged
        )

    def write_raw_evidence(
        self, run_id: str, name: str, content: str | bytes
    ) -> Path:
        """Write a single file into ``<bundle>/raw/`` (prune-eligible).

        Both ``run_id`` (via :meth:`evidence_bundle_dir`) and ``name``
        are validated as flat path-safe identifiers: a rejected
        ``name`` raises ``ValueError`` *before* any filesystem write
        so a malformed input cannot escape ``<bundle>/raw/``.
        """
        # ``run_id`` is validated inside ``evidence_bundle_dir``;
        # validate ``name`` here so a rejected name fails before any
        # mkdir, write, or partial path is created.
        _safe_bundle_key(name, kind="raw evidence name")
        raw_dir = self.evidence_bundle_dir(run_id) / RAW_SUBDIR
        raw_dir.mkdir(parents=True, exist_ok=True)
        target = raw_dir / name
        if isinstance(content, str):
            target.write_text(content, encoding="utf-8")
        else:
            target.write_bytes(content)
        return target

    # -- Cache ----------------------------------------------------------------

    def cache_put(
        self, identity: CacheIdentity, payload: Mapping[str, Any]
    ) -> Path:
        """Store a per-case cache entry keyed by ``identity.cache_key()``."""
        target = self.cache_dir / f"{identity.cache_key()}.json"
        body = {
            "schema_version": SCHEMA_VERSION,
            "identity": identity.as_dict(),
            "cache_key": identity.cache_key(),
            "payload": dict(payload),
        }
        return _atomic_write_json(target, body)

    def cache_get(self, identity: CacheIdentity) -> dict[str, Any] | None:
        """Return the cached ``payload`` for ``identity`` or ``None`` on miss.

        The on-disk record's ``identity`` field is checked against the
        lookup ``identity`` to defend against future cache key collisions
        or namespace shifts: a hit only fires when every identity
        field matches. This is the strict "full identity tuple"
        guarantee from ADR 0030.
        """
        target = self.cache_dir / f"{identity.cache_key()}.json"
        if not target.is_file():
            return None
        body = json.loads(target.read_text(encoding="utf-8"))
        stored = body.get("identity", {})
        if stored != identity.as_dict():
            return None
        payload = body.get("payload", {})
        return dict(payload)

    # -- Cleanup --------------------------------------------------------------

    def prune_raw_evidence(
        self, retention_days: int = DEFAULT_RAW_RETENTION_DAYS
    ) -> dict[str, Any]:
        """Remove ``<bundle>/raw/`` files older than ``retention_days``.

        ``receipt.json``, ``summary.json``, ``trajectory-digest.json``,
        and any prior ``cleanup.json`` are never removed. When at least
        one raw file is removed, a ``cleanup.json`` describing the
        prune is written into the same bundle.

        The returned :class:`CleanupReport` aggregates the removed
        paths and count across every bundle pruned in this pass,
        sorted by path for determinism. The per-bundle audit trail
        still lives in each ``cleanup.json``. An empty pass returns
        ``removed_paths=[]`` and ``removed_count=0``.
        """
        pruned_at = _now_iso()
        cutoff = time.time() - (retention_days * 86400)
        all_removed: list[str] = []
        if not self.evidence_dir.is_dir():
            return CleanupReport(
                removed_paths=all_removed,
                retention_days=retention_days,
                pruned_at=pruned_at,
                removed_count=0,
            ).as_dict()
        for bundle in sorted(self.evidence_dir.iterdir()):
            if not bundle.is_dir():
                continue
            raw_dir = bundle / RAW_SUBDIR
            if not raw_dir.is_dir():
                continue
            raw_paths = sorted(p for p in raw_dir.rglob("*") if p.is_file())
            if not raw_paths:
                # No raw evidence: nothing to prune. We leave the
                # empty raw/ directory in place; it is empty and
                # inexpensive, and removing it would force every
                # cleanup pass to write metadata even when idle.
                continue
            oldest = min(p.stat().st_mtime for p in raw_paths)
            if oldest > cutoff:
                # All raw evidence is younger than the cutoff; skip.
                continue
            for path in raw_paths:
                path.unlink()
            # Prune now-empty subdirectories inside raw/, then the
            # raw/ directory itself when it becomes empty. Future
            # writes recreate it via ``write_raw_evidence``.
            for sub in sorted(raw_dir.rglob("*"), reverse=True):
                if sub.is_dir():
                    sub.rmdir()
            try:
                raw_dir.rmdir()
            except OSError:
                # Non-empty (a writer raced us): keep the directory.
                pass
            bundle_removed = [
                str(p.relative_to(self.root)) for p in raw_paths
            ]
            # Per-bundle audit trail (durable, lives next to receipt.json).
            cleanup_payload = {
                "schema_version": SCHEMA_VERSION,
                "retention_days": retention_days,
                "pruned_at": pruned_at,
                "removed_paths": bundle_removed,
                "removed_count": len(bundle_removed),
            }
            _atomic_write_json(bundle / "cleanup.json", cleanup_payload)
            # Aggregate across every pruned bundle for the return value.
            all_removed.extend(bundle_removed)
        return CleanupReport(
            removed_paths=sorted(all_removed),
            retention_days=retention_days,
            pruned_at=pruned_at,
            removed_count=len(all_removed),
        ).as_dict()

    def prune_cache(self) -> dict[str, Any]:
        """Remove every cache entry and append a record to ``cache/cleanup.jsonl``.

        ADR 0030: cache is prune-eligible. The cleanup record is
        appended (not overwritten) so multiple passes are auditable
        as a log.
        """
        pruned_at = _now_iso()
        removed: list[str] = []
        if self.cache_dir.is_dir():
            for path in sorted(self.cache_dir.glob("*.json")):
                removed.append(str(path.relative_to(self.root)))
                path.unlink()
        record = {
            "schema_version": SCHEMA_VERSION,
            "pruned_at": pruned_at,
            "removed_paths": removed,
            "removed_count": len(removed),
        }
        log_path = self.cache_dir / "cleanup.jsonl"
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
            )
        return CleanupReport(
            removed_paths=removed,
            retention_days=0,
            pruned_at=pruned_at,
            removed_count=len(removed),
        ).as_dict()


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    """Write ``payload`` as pretty JSON to ``path`` atomically.

    The temp file is written in the same directory as ``path`` so the
    final ``os.replace`` is a single-filesystem rename and therefore
    atomic on POSIX. ``encoding="utf-8"`` is pinned because the rest
    of MetaCrucible is text-only.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(dict(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return path


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with ``Z`` suffix."""
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
