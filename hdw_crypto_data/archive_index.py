"""Rebuildable SQLite metadata index. Filesystem commits always take priority."""
# Copyright (c) 2026 Hans De Weme
# Licensed under the MIT License (https://opensource.org/licenses/MIT).
# part of the HdW_crypto_data Project
#
from __future__ import annotations
import json
import logging
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
# local imports
from .archive_paths import archive_locations, spot_root_for_file

SCHEMA_VERSION = 1
FILE_COLUMNS = ("relative_path", "symbol", "interval", "market_type", "size_bytes", "modified_ns",
                "identity_json", "modified_at", "advertised_start", "advertised_end", "observed_start",
                "observed_end", "row_count", "validation_status", "issues_json", "recognized", "indexed_at", "revision")
LOG = logging.getLogger(__name__)


class ArchiveIndexError(RuntimeError):
    pass


class ArchiveIndexIncompatibleError(ArchiveIndexError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def _time(value):
    return value.astimezone(timezone.utc).isoformat() if value else None


def _parameters(record, revision):
    return (record.relative_path.as_posix(), record.symbol, record.interval, record.market,
            record.size_bytes, record.identity[3] if record.identity else 0,
            json.dumps(record.identity), _time(record.modified_at),
            _time(record.advertised.start), _time(record.advertised.end),
            _time(record.observed.start), _time(record.observed.end), record.row_count,
            record.status, json.dumps([(i.code, i.message) for i in record.issues]),
            int(record.recognized), _now(), revision)


UPSERT = """INSERT INTO archive_file VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(relative_path) DO UPDATE SET
symbol=excluded.symbol, interval=excluded.interval, market_type=excluded.market_type,
size_bytes=excluded.size_bytes, modified_ns=excluded.modified_ns, identity_json=excluded.identity_json,
modified_at=excluded.modified_at, advertised_start=excluded.advertised_start,
advertised_end=excluded.advertised_end, observed_start=excluded.observed_start,
observed_end=excluded.observed_end, row_count=excluded.row_count,
validation_status=excluded.validation_status, issues_json=excluded.issues_json,
recognized=excluded.recognized, indexed_at=excluded.indexed_at, revision=excluded.revision"""

SUMMARY_SQL = """SELECT COALESCE(symbol,'Unknown') symbol, COALESCE(interval,'Unknown') interval,
    SUM(recognized) files, SUM(size_bytes) bytes, MIN(observed_start) start, MAX(observed_end) end,
    CASE WHEN COUNT(row_count)=COUNT(*) THEN SUM(row_count) END rows,
    MAX(CASE validation_status WHEN 'Unreadable' THEN 3 WHEN 'Warning' THEN 2
        WHEN 'Not inspected' THEN 1 ELSE 0 END) severity
    FROM archive_file GROUP BY COALESCE(symbol,'Unknown'),COALESCE(interval,'Unknown') ORDER BY symbol,interval"""


@dataclass(frozen=True)
class IndexSummary:
    symbol: str
    interval: str
    file_count: int
    size_bytes: int
    observed_start: datetime | None
    observed_end: datetime | None
    row_count: int | None
    status: str


@dataclass(frozen=True)
class ReconciliationResult:
    inventory: object
    new: int
    changed: int
    missing: int
    unchanged: int
    inspected: int
    complete: bool
    warnings: tuple[str, ...] = ()


class ArchiveIndex:
    def __init__(self, root, *, busy_timeout_ms=1500):
        self.locations = archive_locations(root)
        self.root = self.locations.spot
        self.path = self.locations.index
        self.busy_timeout_ms = busy_timeout_ms
        self._initialize()

    @classmethod
    def for_spot_root(cls, root, **kwargs):
        return cls(root, **kwargs)

    @contextmanager
    def connection(self):
        from .archive_manager import _safe_path
        for suffix in ("", "-wal", "-shm", "-journal"):
            _safe_path(self.locations.metadata, self.path.with_name(self.path.name + suffix), must_exist=False)
        connection = None
        try:
            connection = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_ms)}")
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
        except sqlite3.Error as exc:
            raise ArchiveIndexError(str(exc)) from exc
        finally:
            if connection is not None:
                connection.close()

    @contextmanager
    def transaction(self):
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise

    @staticmethod
    def _set(connection, key, value):
        connection.execute("INSERT INTO index_metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

    @staticmethod
    def _metadata(connection):
        return dict(connection.execute("SELECT key,value FROM index_metadata"))

    def _initialize(self):
        self.locations.metadata.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if version not in (0, SCHEMA_VERSION):
                raise ArchiveIndexIncompatibleError(f"Unsupported index schema {version}; use a compatible application or explicitly rebuild")
            if version == 0 and tables and tables != {"archive_file", "index_metadata"}:
                raise ArchiveIndexIncompatibleError("Unrecognized version-zero index; explicit rebuild required")
            connection.execute("PRAGMA journal_mode=WAL")
        with self.transaction() as connection:
            # Another writer may have initialized the DB while we waited for the lock.
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if version not in (0, SCHEMA_VERSION):
                raise ArchiveIndexIncompatibleError("Index schema changed during initialization")
            if not tables:
                connection.execute("""CREATE TABLE archive_file (
                    relative_path TEXT PRIMARY KEY, symbol TEXT, interval TEXT, market_type TEXT,
                    size_bytes INTEGER NOT NULL, modified_ns INTEGER NOT NULL, identity_json TEXT NOT NULL,
                    modified_at TEXT, advertised_start TEXT, advertised_end TEXT,
                    observed_start TEXT, observed_end TEXT, row_count INTEGER,
                    validation_status TEXT NOT NULL, issues_json TEXT NOT NULL, recognized INTEGER NOT NULL,
                    indexed_at TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0)""")
                connection.execute("CREATE TABLE index_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            elif version == 0:
                # Supported prototype: current columns except the optimistic-write revision.
                columns = {r[1] for r in connection.execute("PRAGMA table_info(archive_file)")}
                required = {"relative_path", "identity_json", "issues_json", "recognized", "indexed_at"}
                if not required.issubset(columns):
                    raise ArchiveIndexIncompatibleError("Unsupported prototype schema; explicit rebuild required")
                if "revision" not in columns:
                    connection.execute("ALTER TABLE archive_file ADD COLUMN revision INTEGER NOT NULL DEFAULT 0")
            if tuple(r[1] for r in connection.execute("PRAGMA table_info(archive_file)")) != FILE_COLUMNS:
                raise ArchiveIndexIncompatibleError("Incompatible file table layout; explicit rebuild required")
            connection.execute("CREATE INDEX IF NOT EXISTS archive_group ON archive_file(symbol,interval)")
            connection.execute("CREATE INDEX IF NOT EXISTS archive_status ON archive_file(validation_status)")
            metadata = self._metadata(connection)
            identity = json.dumps([self.root.stat().st_dev, self.root.stat().st_ino])
            if metadata.get("archive_root", str(self.root)) != str(self.root) or metadata.get("root_identity", identity) != identity:
                raise ArchiveIndexIncompatibleError("Index belongs to a different archive root; explicit rebuild required")
            for key, value in (("schema_version", SCHEMA_VERSION), ("archive_root", self.root), ("root_identity", identity)):
                self._set(connection, key, value)
            for key, value in (("generation", "0"), ("reconciliation_complete", "false"),
                               ("last_incremental", ""), ("last_deep", ""), ("implementation", "1")):
                if key not in metadata:
                    self._set(connection, key, value)
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def metadata(self):
        with self.connection() as connection:
            return self._metadata(connection)

    def _decode(self, row):
        from .archive_manager import FileRecord, CoverageRange, ValidationIssue
        relative = Path(row["relative_path"])
        if relative.is_absolute() or relative.drive or relative.root or ".." in relative.parts or not relative.parts:
            raise ArchiveIndexIncompatibleError("Invalid cached relative path")
        # Cached startup must not stat every file. Mutation APIs validate paths afresh.
        path = self.root / relative
        def date(field):
            return datetime.fromisoformat(row[field]) if row[field] else None
        identity = json.loads(row["identity_json"])
        return FileRecord(path, relative, row["symbol"], row["interval"], row["size_bytes"], date("modified_at"),
                          tuple(identity) if identity else None,
                          CoverageRange(date("advertised_start"), date("advertised_end")),
                          CoverageRange(date("observed_start"), date("observed_end")), row["row_count"],
                          row["validation_status"], tuple(ValidationIssue(*i) for i in json.loads(row["issues_json"])),
                          bool(row["recognized"]), row["market_type"])

    def snapshot(self):
        with self.connection() as connection:
            connection.execute("BEGIN")
            rows = connection.execute("SELECT * FROM archive_file ORDER BY relative_path").fetchall()
            metadata = self._metadata(connection)
            connection.execute("COMMIT")
        return tuple(self._decode(r) for r in rows), {r["relative_path"]: r["revision"] for r in rows}, metadata

    def load_inventory(self):
        from .archive_manager import Inventory, AssetInventory, CoverageRange
        with self.connection() as connection:
            connection.execute("BEGIN")
            rows = connection.execute("SELECT * FROM archive_file ORDER BY relative_path").fetchall()
            summaries = self._summaries(connection.execute(SUMMARY_SQL).fetchall())
            connection.execute("COMMIT")
        records = tuple(self._decode(row) for row in rows)
        groups = {}
        for record in records:
            groups.setdefault((record.symbol or "Unknown", record.interval or "Unknown"), []).append(record)
        assets = tuple(AssetInventory(s.symbol, s.interval, tuple(groups.get((s.symbol, s.interval), ())),
                                     s.file_count, s.size_bytes, CoverageRange(s.observed_start, s.observed_end),
                                     s.row_count, s.status) for s in summaries)
        return Inventory(self.root, records, assets)

    def list_asset_summaries(self):
        # Totals are derived from the sole file table; no redundant summary cache.
        with self.connection() as connection:
            rows = connection.execute(SUMMARY_SQL).fetchall()
        return self._summaries(rows)

    @staticmethod
    def _summaries(rows):
        return tuple(IndexSummary(r["symbol"], r["interval"], r["files"], r["bytes"],
                                  datetime.fromisoformat(r["start"]) if r["start"] else None,
                                  datetime.fromisoformat(r["end"]) if r["end"] else None,
                                  r["rows"], ("OK", "Not inspected", "Warning", "Unreadable")[r["severity"]]) for r in rows)

    def upsert_record(self, record):
        from .archive_manager import _safe_path, _identity
        if _safe_path(self.root, record.path).relative_to(self.root) != record.relative_path or _identity(record.path) != record.identity:
            raise ArchiveIndexError("Committed file changed before index update")
        with self.transaction() as connection:
            if _identity(record.path) != record.identity:
                raise ArchiveIndexError("Committed file changed while waiting for index lock")
            revision = int(self._metadata(connection)["generation"]) + 1
            connection.execute(UPSERT, _parameters(record, revision))
            self._set(connection, "generation", revision)
            self._set(connection, "reconciliation_complete", "false")

    def upsert_committed_file(self, path, metadata=None):
        from .archive_manager import _record, _inspect, _safe_path
        path = _safe_path(self.root, Path(path))
        record = _record(self.root, path)
        if metadata is None:
            record = _inspect(record, self.root, lambda: False)
        else:
            # Writer-owned dataframe metadata comes from the final committed contents.
            record = replace(record, observed=metadata["observed"], row_count=metadata["row_count"],
                             status="Warning" if record.issues else metadata.get("status", "OK"),
                             issues=metadata.get("issues", record.issues))
        self.upsert_record(record)

    def remove(self, path):
        from .archive_manager import _safe_path
        path = _safe_path(self.root, Path(path), must_exist=False)
        # A concurrent replacement must not be hidden by an obsolete removal event.
        if path.exists():
            return self.upsert_committed_file(path)
        with self.transaction() as connection:
            connection.execute("DELETE FROM archive_file WHERE relative_path=?", (path.relative_to(self.root).as_posix(),))
            metadata = self._metadata(connection)
            self._set(connection, "generation", int(metadata["generation"]) + 1)
            self._set(connection, "reconciliation_complete", "false")

    def verify_incremental(self, *, progress=None, cancelled=None, selection=None, rebuild=False):
        from .archive_manager import _inventory, scan_archive, _identity, _inspect
        cancelled = cancelled or (lambda: False)
        if rebuild:
            # A full rebuild must not depend on decodable old cache records.
            with self.connection() as connection:
                connection.execute("BEGIN")
                revisions = dict(connection.execute("SELECT relative_path,revision FROM archive_file"))
                metadata = self._metadata(connection)
                connection.execute("COMMIT")
            records = ()
        else:
            records, revisions, metadata = self.snapshot()
        token = uuid.uuid4().hex
        with self.transaction() as connection:
            self._set(connection, "reconciliation_complete", "false")
            self._set(connection, "reconciliation_token", token)
        cached = None if rebuild else _inventory(self.root, records)
        if selection is not None and cached is not None:
            cached = _inventory(self.root, tuple(f for f in records if (f.symbol, f.interval) != selection))
        inventory = scan_archive(self.root, "deep", cached_inventory=cached, progress=progress, cancelled=cancelled)
        previous = {f.path: f for f in records}
        fresh = {f.path: f for f in inventory.files}
        new = sum(f.path not in previous for f in inventory.files)
        changed = sum(f.path in previous and f.identity != previous[f.path].identity for f in inventory.files)
        fresh_keys = {f.relative_path.as_posix() for f in inventory.files}
        missing = sum(key not in fresh_keys for key in revisions)
        inspected = sum(f.recognized and (rebuild or f.path not in previous or f.identity != previous[f.path].identity
                        or previous[f.path].row_count is None or (f.symbol, f.interval) == selection) for f in inventory.files)
        if cancelled():
            raise InterruptedError("Reconciliation cancelled")
        if rebuild and (inventory.issues or any(f.status == "Unreadable" for f in inventory.files)):
            raise ArchiveIndexError("Rebuild could not inspect the complete archive; previous records retained")
        # Validate before opening a short write transaction; never parse inside one.
        stable = []
        warnings = [issue.message for issue in inventory.issues]
        for record in inventory.files:
            try:
                if _identity(record.path) != record.identity:
                    raise ValueError("file changed")
                stable.append(record)
            except (OSError, ValueError):
                warnings.append(f"Changed or disappeared during verification: {record.path}")
        if rebuild and warnings:
            raise ArchiveIndexError("Archive changed during rebuild; previous records retained")
        with self.transaction() as connection:
            state = self._metadata(connection)
            complete = not inventory.issues and not warnings and state["generation"] == metadata["generation"]
            revision = int(state["generation"]) + 1
            for record in stable:
                try:
                    if _identity(record.path) != record.identity:
                        raise ValueError("file changed")
                except (OSError, ValueError):
                    if rebuild:
                        raise ArchiveIndexError("Archive changed during rebuild commit; previous records retained")
                    complete = False
                    warnings.append(f"Changed immediately before index commit: {record.path}")
                    continue
                key = record.relative_path.as_posix()
                current = connection.execute("SELECT revision FROM archive_file WHERE relative_path=?", (key,)).fetchone()
                if (current[0] if current else None) != revisions.get(key):
                    complete = False
                    continue
                if not rebuild and record == previous.get(record.path) and (record.symbol, record.interval) != selection:
                    continue
                connection.execute(UPSERT, _parameters(record, revision))
            if not inventory.issues:
                if rebuild:
                    for key, old_revision in revisions.items():
                        if key not in fresh_keys:
                            connection.execute("DELETE FROM archive_file WHERE relative_path=? AND revision=?", (key, old_revision))
                for record in records:
                    if record.path not in fresh and not record.path.exists():
                        connection.execute("DELETE FROM archive_file WHERE relative_path=? AND revision=?",
                                           (record.relative_path.as_posix(), revisions[record.relative_path.as_posix()]))
            if state.get("reconciliation_token") != token:
                complete = False
            self._set(connection, "generation", revision)
            if state.get("reconciliation_token") == token:
                self._set(connection, "reconciliation_complete", str(complete).lower())
                if complete:
                    self._set(connection, "last_incremental", _now())
                    if rebuild or (selection is None and inspected == sum(f.recognized for f in inventory.files)):
                        self._set(connection, "last_deep", _now())
        return ReconciliationResult(replace(self.load_inventory(), issues=inventory.issues), new, changed, missing, len(inventory.files) - new - changed,
                                    inspected, complete, tuple(warnings))

    def deep_rescan(self, selection, **kwargs):
        return self.verify_incremental(selection=selection, **kwargs)

    def rebuild(self, **kwargs):
        # Atomic transaction replaces records only after the full scan succeeds.
        return self.verify_incremental(rebuild=True, **kwargs)

    @classmethod
    def recover_and_rebuild(cls, root, **kwargs):
        locations = archive_locations(root)
        try:
            index = cls(root)
        except ArchiveIndexError as exc:
            code = getattr(exc.__cause__, "sqlite_errorcode", None)
            if not isinstance(exc, ArchiveIndexIncompatibleError) and code not in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
                raise
            from .archive_manager import _safe_path
            # Explicit rebuild only. Keep a bounded diagnostic DB and its WAL sidecars.
            for suffix in ("", "-wal", "-shm"):
                source = locations.index.with_name(locations.index.name + suffix)
                destination = locations.metadata / ("archive_index.diagnostic.sqlite" + suffix)
                _safe_path(locations.metadata, source, must_exist=False)
                _safe_path(locations.metadata, destination, must_exist=False)
                if source.exists():
                    source.replace(destination)
            index = cls(root)
        return index.rebuild(**kwargs)


def notify_committed_file(path, metadata=None):
    """Best effort: cache problems never undo a valid market-data commit."""
    root = spot_root_for_file(path)
    if root is None:
        return
    try:
        ArchiveIndex(root).upsert_committed_file(path, metadata)
    except Exception as exc:
        LOG.warning("Archive file committed but index update failed for %s; verification required: %s", path, exc)


def notify_removed_file(root, path):
    try:
        ArchiveIndex(root).remove(path)
    except Exception as exc:
        LOG.warning("Archive removal succeeded but index update failed for %s; verification required: %s", path, exc)


def dataframe_metadata(frame):
    from .archive_manager import CoverageRange
    from .total_dataset_loader import format_total_dataset_datetime
    timestamps = frame["open_time"].dropna()
    def timestamp(value):
        return datetime.strptime(format_total_dataset_datetime(value), "%Y-%m-%dT%H-%M-%SZ").replace(tzinfo=timezone.utc)
    return {"observed": CoverageRange(timestamp(timestamps.min()), timestamp(timestamps.max())) if len(timestamps) else CoverageRange(),
            "row_count": len(frame), "status": "OK" if len(timestamps) == len(frame) else "Warning"}


def notify_dataframe_committed_file(path, frame):
    if spot_root_for_file(path) is None:
        return
    try:
        notify_committed_file(path, dataframe_metadata(frame))
    except Exception as exc:
        LOG.warning("Dataframe committed but index metadata unavailable for %s; verification required: %s", path, exc)
