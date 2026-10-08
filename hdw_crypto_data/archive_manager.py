"""Read-only Binance inventory and explicitly invoked recoverable cleanup.
No GUI imports, downloading, CSV editing.
"""
# Copyright (c) 2025, 2026 Hans De Weme
# Licensed under the MIT License (https://opensource.org/licenses/MIT).
# part of the HdW_crypto_data Project
#
from __future__ import annotations
import calendar
import csv
import json
import os
import re
import stat
import uuid
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
# local imports
from .binance_vision_dumper import BinanceVisionDumper
from .symbols import normalize_symbol
from .total_dataset_loader import format_total_dataset_datetime

UTC = timezone.utc


def _is_link(path: Path) -> bool:
    """Include Windows junctions/reparse points on Python 3.11 as well."""
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str


@dataclass(frozen=True)
class CoverageRange:
    start: datetime | None = None
    end: datetime | None = None


@dataclass(frozen=True)
class FileRecord:
    path: Path
    relative_path: Path
    symbol: str | None
    interval: str | None
    size_bytes: int
    modified_at: datetime | None
    identity: tuple[int, int, int, int] | None
    advertised: CoverageRange
    observed: CoverageRange = CoverageRange()
    row_count: int | None = None
    status: str = "Not inspected"
    issues: tuple[ValidationIssue, ...] = ()
    recognized: bool = False
    market: str = "spot"


@dataclass(frozen=True)
class AssetInventory:
    symbol: str
    interval: str
    files: tuple[FileRecord, ...]
    file_count: int
    size_bytes: int
    coverage: CoverageRange
    observed_rows: int | None
    status: str


@dataclass(frozen=True)
class Inventory:
    archive_root: Path
    files: tuple[FileRecord, ...]
    assets: tuple[AssetInventory, ...]
    issues: tuple[ValidationIssue, ...] = ()


def _identity(path: Path) -> tuple[int, int, int, int]:
    s = path.lstat()
    if not stat.S_ISREG(s.st_mode):
        raise ValueError(f"Not a regular file: {path}")
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns


def _safe_path(root: Path, path: Path, *, must_exist: bool = True) -> Path:
    """Reject traversal and all symlinks/junctions, including internal links."""
    path = Path(path)
    if ".." in path.parts:
        raise ValueError(f"Path traversal rejected: {path}")
    path = path if path.is_absolute() else root / path
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise ValueError(f"Path outside root: {path}") from None
    if not relative.parts:
        raise ValueError("Cannot operate on a root directory")
    current = root
    for part in relative.parts:
        current = current / part
        if _is_link(current):
            raise ValueError(f"Linked path rejected: {current}")
    resolved = path.resolve(strict=must_exist)
    if not resolved.is_relative_to(root):
        raise ValueError(f"Path escapes root: {path}")
    return resolved


def resolve_archive_root(root: str | Path) -> Path:
    candidate = Path(root).expanduser()
    # Reject links before resolving them away.
    for part in (candidate, *candidate.parents):
        if _is_link(part):
            raise ValueError(f"Linked archive root rejected: {part}")
    candidate = candidate.resolve(strict=True)
    if candidate.name.lower() != "spot":
        candidate = _safe_path(candidate, candidate / "spot")
    if not candidate.is_dir():
        raise ValueError("Choose a spot directory or its parent")
    # Opening the directory validates access even for an empty archive.
    with os.scandir(candidate):
        pass
    return candidate


def _record(root: Path, path: Path) -> FileRecord:
    relative = path.relative_to(root)
    issues = []
    symbol = interval = None
    advertised = CoverageRange()
    recognized = False
    identity = None
    modified = None
    size = 0
    try:
        _safe_path(root, path)
        identity = _identity(path)
        size = identity[2]
        modified = datetime.fromtimestamp(identity[3] / 1e9, UTC)
    except (OSError, ValueError) as exc:
        issues.append(ValidationIssue("unreadable", str(exc)))
    parts = relative.parts
    if len(parts) == 5 and parts[0] in ("daily", "monthly") and parts[1] == "klines":
        period, _, symbol, interval, filename = parts
        try:
            if normalize_symbol(symbol) != symbol:
                raise ValueError("Noncanonical symbol")
            match = re.fullmatch(r".+-(\d{4}-\d{2}(?:-\d{2})?)\.csv", filename)
            if not match:
                raise ValueError("Malformed or unsupported filename")
            date = datetime.strptime(match[1], "%Y-%m-%d" if period == "daily" else "%Y-%m").replace(tzinfo=UTC)
            dumper = BinanceVisionDumper(str(root), data_frequency=interval)
            expected = Path(dumper.get_local_dir_to_data(symbol, period)) / dumper.create_filename(symbol, date.date(), period)
            if expected != path:
                raise ValueError("Filename does not match archive hierarchy")
            days = 1 if period == "daily" else calendar.monthrange(date.year, date.month)[1]
            advertised = CoverageRange(date, date + timedelta(days=days) - timedelta(microseconds=1))
            recognized = True
        except ValueError as exc:
            issues.append(ValidationIssue("filename", str(exc)))
    else:
        issues.append(ValidationIssue("unsupported", "Unsupported archive path or file format"))
    if size == 0 and identity is not None:
        issues.append(ValidationIssue("empty", "Empty file"))
    status = "Unreadable" if identity is None else "Warning" if issues else "Not inspected"
    return FileRecord(path, relative, symbol, interval, size, modified, identity, advertised,
                      status=status, issues=tuple(issues), recognized=recognized)


def _inspect(record: FileRecord, root: Path, cancelled: Callable[[], bool]) -> FileRecord:
    if not record.recognized or record.identity is None:
        return record
    issues = list(record.issues)
    count = 0
    first = last = previous = None
    invalid = unordered = 0
    try:
        _safe_path(root, record.path)
        if _identity(record.path) != record.identity:
            raise ValueError("File changed since inventory")
        with record.path.open(encoding="utf-8-sig", newline="") as stream:
            for index, row in enumerate(csv.reader(stream)):
                if cancelled():
                    raise InterruptedError("Scan cancelled")
                if index == 0 and row and row[0].strip().lower() == "open_time":
                    continue
                count += 1
                try:
                    timestamp = datetime.strptime(format_total_dataset_datetime(row[0]), "%Y-%m-%dT%H-%M-%SZ").replace(tzinfo=UTC)
                    if previous is not None and timestamp <= previous:
                        unordered += 1
                    previous = timestamp
                    first = timestamp if first is None else min(first, timestamp)
                    last = timestamp if last is None else max(last, timestamp)
                except (ValueError, IndexError, OverflowError, TypeError):
                    invalid += 1
        if _identity(record.path) != record.identity:
            raise ValueError("File changed during inspection")
    except InterruptedError:
        raise
    except (OSError, ValueError, csv.Error, UnicodeError) as exc:
        issues.append(ValidationIssue("unreadable", str(exc)))
        return replace(record, status="Unreadable", issues=tuple(issues))
    if invalid:
        issues.append(ValidationIssue("timestamp", f"{invalid} rows with missing or invalid open timestamps"))
    if unordered:
        issues.append(ValidationIssue("order", f"{unordered} duplicate or out-of-order timestamps"))
    if first is None:
        issues.append(ValidationIssue("coverage", "No observed timestamps"))
    elif first < record.advertised.start or last > record.advertised.end:
        issues.append(ValidationIssue("range", "Observed timestamps extend outside advertised range"))
    elif first.date() != record.advertised.start.date() or last.date() != record.advertised.end.date():
        issues.append(ValidationIssue("range", "Observed dates do not span advertised dates (possibly partial data)"))
    return replace(record, observed=CoverageRange(first, last), row_count=count,
                   status="Warning" if issues else "OK", issues=tuple(issues))


def _inventory(root: Path, records: tuple[FileRecord, ...], issues=()) -> Inventory:
    groups = defaultdict(list)
    for record in records:
        groups[(record.symbol or "Unknown", record.interval or "Unknown")].append(record)
    assets = []
    for (symbol, interval), files in sorted(groups.items()):
        starts = [f.observed.start for f in files if f.observed.start]
        ends = [f.observed.end for f in files if f.observed.end]
        statuses = {f.status for f in files}
        status = next((s for s in ("Unreadable", "Warning", "Not inspected", "OK") if s in statuses))
        rows = None if any(f.row_count is None for f in files) else sum(f.row_count for f in files)
        assets.append(AssetInventory(symbol, interval, tuple(files), sum(f.recognized for f in files),
                                     sum(f.size_bytes for f in files),
                                     CoverageRange(min(starts) if starts else None, max(ends) if ends else None), rows, status))
    return Inventory(root, records, tuple(assets), tuple(issues))


def scan_archive(root: str | Path, mode: str = "fast", *, progress=None, cancelled=None,
                 cached_inventory: Inventory | None = None) -> Inventory:
    if mode not in ("fast", "deep"):
        raise ValueError("Mode must be fast or deep")
    root = resolve_archive_root(root)
    cancelled = cancelled or (lambda: False)
    cached = {f.path: f for f in cached_inventory.files} if cached_inventory is not None and cached_inventory.archive_root == root else {}
    records, issues = [], []
    def walk_error(exc):
        issues.append(ValidationIssue("directory", str(exc)))
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        for name in list(dirs):
            if name in (".hdw_archive", "quarantine") or name.startswith("hdw-"):
                dirs.remove(name)
                continue
            path = Path(directory) / name
            if _is_link(path):
                dirs.remove(name)
                issues.append(ValidationIssue("link", f"Skipped linked directory: {path}"))
        for name in sorted(files):
            if name.endswith((".part", ".tmp", ".zip", ".CHECKSUM")):
                continue
            if cancelled():
                raise InterruptedError("Scan cancelled")
            record = _record(root, Path(directory) / name)
            previous = cached.get(record.path)
            if (mode == "deep" and previous is not None and record.recognized
                    and record.identity is not None and record.identity == previous.identity
                    and record.advertised == previous.advertised and previous.row_count is not None):
                record = previous
            elif mode == "deep":
                record = _inspect(record, root, cancelled)
            records.append(record)
            if progress:
                progress(len(records), str(record.relative_path))
    return _inventory(root, tuple(records), issues)


def validate_inventory(inventory: Inventory) -> Inventory:
    return _inventory(inventory.archive_root,
                      tuple(_inspect(f, inventory.archive_root, lambda: False) for f in inventory.files), inventory.issues)


@dataclass(frozen=True)
class PlannedFileMove:
    record: FileRecord
    destination: Path


@dataclass(frozen=True)
class CleanupPlan:
    archive_root: Path
    quarantine_root: Path
    operation_id: str
    files: tuple[PlannedFileMove, ...]
    total_size_bytes: int
    affected_groups: tuple[tuple[str, str], ...]
    coverage: CoverageRange
    warnings: tuple[str, ...]


def _quarantine_root(archive: Path, root: Path) -> Path:
    root = Path(root).absolute()
    if ".." in root.parts:
        raise ValueError("Quarantine traversal rejected")
    for part in (root, *root.parents):
        if _is_link(part):
            raise ValueError(f"Linked quarantine root rejected: {part}")
    root = root.resolve()
    if root.is_relative_to(archive) or archive.is_relative_to(root):
        raise ValueError("Quarantine must be outside and must not contain the archive")
    return root


def build_cleanup_plan(inventory: Inventory, selected_paths, quarantine_root=None) -> CleanupPlan:
    archive = resolve_archive_root(inventory.archive_root)
    quarantine = quarantine_location(archive, quarantine_root)
    operation = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex
    known = {f.path: f for f in inventory.files}
    moves = []
    seen = set()
    for selected in selected_paths:
        source = _safe_path(archive, Path(selected))
        if source in seen:
            continue
        seen.add(source)
        if source not in known:
            raise ValueError(f"File is not in inventory: {source}")
        record = known[source]
        if _identity(source) != record.identity:
            raise ValueError(f"File changed since scan: {source}")
        destination = _safe_path(quarantine, quarantine / operation / "files" / record.relative_path, must_exist=False)
        moves.append(PlannedFileMove(record, destination))
    if not moves:
        raise ValueError("Select at least one file")
    starts, ends, warnings = [], [], []
    for move in moves:
        f = move.record
        coverage = f.observed if f.observed.start else f.advertised
        if coverage.start:
            starts.append(coverage.start)
            ends.append(coverage.end)
        if f.observed.start is None:
            warnings.append(f"{f.relative_path}: observed coverage unknown; using advertised dates if available")
        warnings.extend(f"{f.relative_path}: {i.message}" for i in f.issues)
    return CleanupPlan(archive, quarantine, operation, tuple(moves), sum(m.record.size_bytes for m in moves),
                       tuple(sorted({(m.record.symbol or "Unknown", m.record.interval or "Unknown") for m in moves})),
                       CoverageRange(min(starts) if starts else None, max(ends) if ends else None), tuple(warnings))


@dataclass(frozen=True)
class FileOutcome:
    source: Path
    destination: Path
    outcome: str
    error: str | None = None


@dataclass(frozen=True)
class OperationResult:
    operation_id: str
    manifest_path: Path
    files: tuple[FileOutcome, ...]
    errors: tuple[str, ...] = ()

    @property
    def successful(self):
        return bool(self.files) and not self.errors and all(f.outcome in ("moved", "restored") for f in self.files)


def _write_manifest(path: Path, document: dict):
    for ancestor in (path, *path.parents):
        if _is_link(ancestor):
            raise ValueError(f"Linked manifest path rejected: {ancestor}")
    temporary = path.with_name(f".manifest-{uuid.uuid4().hex}.tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(document, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _move_no_overwrite(source: Path, destination: Path, identity):
    """Same-volume link/unlink move: atomic destination creation, never overwrite.

    An unlink failure leaves both copies available and is reported as a failure.
    Cross-volume destinations fail safely before the source is removed.
    """
    if identity is None or _identity(source) != tuple(identity):
        raise ValueError(f"Source identity changed: {source}")
    os.link(source, destination, follow_symlinks=False)
    if _identity(source) != tuple(identity) or _identity(destination) != tuple(identity):
        raise ValueError("Source changed during move; both paths retained")
    source.unlink()


def execute_quarantine(plan: CleanupPlan) -> OperationResult:
    """Explicit mutation API. GUI must confirm a preview before calling this."""
    archive = resolve_archive_root(plan.archive_root)
    quarantine = _quarantine_root(archive, plan.quarantine_root)
    if not plan.files:
        raise ValueError("Cannot execute an empty plan")
    if not re.fullmatch(r"[A-Za-z0-9-]+", plan.operation_id):
        raise ValueError("Invalid operation identifier")
    operation = _safe_path(quarantine, quarantine / plan.operation_id, must_exist=False)
    # Validate a manually constructed plan too, before creating anything.
    for move in plan.files:
        source = _safe_path(archive, move.record.path)
        if source.relative_to(archive) != move.record.relative_path:
            raise ValueError("Plan relative path mismatch")
        expected = operation / "files" / move.record.relative_path
        if move.destination != expected:
            raise ValueError("Plan destination mismatch")
        _safe_path(quarantine, expected, must_exist=False)
    quarantine.mkdir(parents=True, exist_ok=True)
    operation.mkdir(exist_ok=False)
    manifest = operation / "manifest.json"
    document = {"version": 1, "operation_id": plan.operation_id, "archive_root": str(archive),
                "quarantine_root": str(quarantine), "timestamp": datetime.now(UTC).isoformat(), "files": []}
    for move in plan.files:
        document["files"].append({"original_path": str(move.record.path), "relative_path": str(move.record.relative_path),
                                  "quarantine_path": str(move.destination), "size_bytes": move.record.size_bytes,
                                  "identity": move.record.identity, "outcome": "pending", "error": None})
    _write_manifest(manifest, document)
    outcomes, errors = [], []
    for move, entry in zip(plan.files, document["files"]):
        try:
            source = _safe_path(archive, move.record.path)
            destination = _safe_path(quarantine, move.destination, must_exist=False)
            destination.parent.mkdir(parents=True, exist_ok=True)
            _safe_path(quarantine, destination, must_exist=False)
            _move_no_overwrite(source, destination, move.record.identity)
            entry["outcome"] = "moved"
            from .archive_index import notify_removed_file
            notify_removed_file(archive, source)
        except (OSError, ValueError) as exc:
            entry.update(outcome="failed", error=str(exc))
        entry["timestamp"] = datetime.now(UTC).isoformat()
        outcomes.append(FileOutcome(move.record.path, move.destination, entry["outcome"], entry["error"]))
        try:
            _write_manifest(manifest, document)
        except (OSError, ValueError) as exc:
            errors.append(f"Manifest update failed: {exc}; stopped further moves. Inspect pending entries before restoring.")
            for remaining in plan.files[len(outcomes):]:
                outcomes.append(FileOutcome(remaining.record.path, remaining.destination, "not attempted", "Manifest update failed"))
            break
    return OperationResult(plan.operation_id, manifest, tuple(outcomes), tuple(errors))


def restore_quarantine_operation(operation_id: str, *, archive_root: str | Path, quarantine_root=None) -> OperationResult:
    """Restore using trusted explicit roots; never trust manifest paths alone."""
    archive = resolve_archive_root(archive_root)
    quarantine = quarantine_location(archive, quarantine_root)
    if not re.fullmatch(r"[A-Za-z0-9-]+", operation_id):
        raise ValueError("Invalid operation identifier")
    manifest = _safe_path(quarantine, quarantine / operation_id / "manifest.json")
    document = json.loads(manifest.read_text(encoding="utf-8"))
    if document["archive_root"] != str(archive) or document["quarantine_root"] != str(quarantine) or document["operation_id"] != operation_id:
        raise ValueError("Manifest roots or operation do not match")
    outcomes, errors = [], []
    for entry in document["files"]:
        if entry["outcome"] not in ("moved", "pending", "failed"):
            continue
        relative = Path(entry["relative_path"])
        source = quarantine / operation_id / "files" / relative
        destination = archive / relative
        if entry["outcome"] == "failed" and not os.path.lexists(source):
            continue
        try:
            if str(source) != entry["quarantine_path"] or str(destination) != entry["original_path"]:
                raise ValueError("Manifest path mismatch")
            _safe_path(quarantine, source)
            _safe_path(archive, destination, must_exist=False)
            destination.parent.mkdir(parents=True, exist_ok=True)
            _safe_path(archive, destination, must_exist=False)
            _move_no_overwrite(source, destination, entry["identity"])
            from .archive_index import notify_committed_file
            notify_committed_file(destination)
            outcome, error = "restored", None
            entry["outcome"] = "restored"
        except (OSError, ValueError) as exc:
            outcome, error = "failed", str(exc)
        entry.setdefault("restore_history", []).append({"timestamp": datetime.now(UTC).isoformat(), "outcome": outcome, "error": error})
        outcomes.append(FileOutcome(source, destination, outcome, error))
        try:
            _write_manifest(manifest, document)
        except (OSError, ValueError) as exc:
            errors.append(f"Manifest update failed: {exc}; stopped further restores.")
            break
    return OperationResult(operation_id, manifest, tuple(outcomes), tuple(errors))


@dataclass(frozen=True)
class QuarantinedFile:
    path: Path
    symbol: str
    interval: str
    identity: tuple[int, int, int, int]
    manifest_path: Path
    entry_index: int


@dataclass(frozen=True)
class QuarantinedAsset:
    symbol: str
    files: tuple[QuarantinedFile, ...]

    @property
    def size_bytes(self):
        return sum(f.identity[2] for f in self.files)

    @property
    def intervals(self):
        return tuple(sorted({f.interval for f in self.files}))


@dataclass(frozen=True)
class QuarantineInventory:
    archive_root: Path
    quarantine_root: Path
    assets: tuple[QuarantinedAsset, ...]
    issues: tuple[ValidationIssue, ...]


def quarantine_location(archive_root, quarantine_root=None) -> Path:
    archive = resolve_archive_root(archive_root)
    from .archive_paths import archive_locations
    return _quarantine_root(archive, quarantine_root or archive_locations(archive).quarantine)


def _load_quarantine_manifest(archive, quarantine, path):
    _safe_path(quarantine, path)
    document = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(document, dict) or document.get("version") != 1 or document.get("archive_root") != str(archive)
            or document.get("quarantine_root") != str(quarantine)
            or document.get("operation_id") != path.parent.name
            or not isinstance(document.get("files"), list)):
        raise ValueError(f"Manifest does not match configured roots: {path}")
    return document


def scan_quarantine(archive_root, quarantine_root=None) -> QuarantineInventory:
    """Read manifest-backed quarantine assets across all operations/intervals."""
    archive = resolve_archive_root(archive_root)
    quarantine = quarantine_location(archive, quarantine_root)
    groups, issues, seen = defaultdict(list), [], set()
    if not quarantine.exists():
        return QuarantineInventory(archive, quarantine, (), ())
    for operation in sorted(quarantine.iterdir()):
        try:
            _safe_path(quarantine, operation)
            if not operation.is_dir():
                raise ValueError(f"Unexpected quarantine entry: {operation}")
            manifest = operation / "manifest.json"
            document = _load_quarantine_manifest(archive, quarantine, manifest)
            tracked = set()
            for index, entry in enumerate(document["files"]):
                if entry["outcome"] not in ("moved", "pending", "failed", "restored", "deleted"):
                    raise ValueError("Unknown manifest outcome")
                relative = Path(entry["relative_path"])
                original = _safe_path(archive, archive / relative, must_exist=False)
                if str(original) != entry["original_path"]:
                    raise ValueError("Manifest original path mismatch")
                source = Path(entry["quarantine_path"])
                if source not in (operation / "files" / relative, operation / relative):
                    raise ValueError("Manifest quarantine path mismatch")
                _safe_path(quarantine, source, must_exist=False)
                tracked.add(source)
                if entry["outcome"] in ("restored", "deleted"):
                    if os.path.lexists(source):
                        raise ValueError(f"Unexpected file for completed entry: {source}")
                    continue
                if not os.path.lexists(source):
                    if entry["outcome"] == "moved":
                        raise ValueError(f"Missing quarantined file: {source}")
                    continue
                parts = relative.parts
                if len(parts) != 5 or parts[0] not in ("daily", "monthly") or parts[1] != "klines":
                    raise ValueError(f"File cannot be assigned to an asset: {source}")
                symbol = normalize_symbol(parts[2])
                if symbol != parts[2] or source in seen:
                    raise ValueError(f"Noncanonical symbol or duplicate manifest entry: {source}")
                identity = _identity(source)
                if identity != tuple(entry["identity"]):
                    raise ValueError(f"Quarantined file identity changed: {source}")
                seen.add(source)
                groups[symbol].append(QuarantinedFile(source, symbol, parts[3], identity, manifest, index))
            def walk_error(exc):
                raise exc
            for directory, dirs, files in os.walk(operation, followlinks=False, onerror=walk_error):
                for name in dirs:
                    _safe_path(quarantine, Path(directory) / name)
                for name in files:
                    path = Path(directory) / name
                    if path != manifest and path not in tracked:
                        raise ValueError(f"Untracked quarantine file: {path}")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            issues.append(ValidationIssue("quarantine", str(exc)))
    assets = tuple(QuarantinedAsset(symbol, tuple(files)) for symbol, files in sorted(groups.items()))
    return QuarantineInventory(archive, quarantine, assets, tuple(issues))


@dataclass(frozen=True)
class AssetDeletionPlan:
    archive_root: Path
    quarantine_root: Path
    asset: QuarantinedAsset


def build_asset_deletion_plan(inventory: QuarantineInventory, symbol: str) -> AssetDeletionPlan:
    """Read-only preview: entire symbol across every interval and operation."""
    current = scan_quarantine(inventory.archive_root, inventory.quarantine_root)
    if current.issues:
        raise ValueError("Resolve quarantine inventory issues before deleting: " + "; ".join(i.message for i in current.issues))
    for asset in current.assets:
        if asset.symbol == symbol:
            return AssetDeletionPlan(current.archive_root, current.quarantine_root, asset)
    raise ValueError(f"No quarantined files for asset: {symbol}")


@dataclass(frozen=True)
class AssetDeletionResult:
    symbol: str
    files: tuple[FileOutcome, ...]
    bytes_deleted: int
    errors: tuple[str, ...] = ()


def execute_asset_deletion(plan: AssetDeletionPlan) -> AssetDeletionResult:
    """Explicit permanent deletion API; never recursively delete directories."""
    current = build_asset_deletion_plan(
        QuarantineInventory(plan.archive_root, plan.quarantine_root, (), ()), plan.asset.symbol)
    if current != plan:
        raise ValueError("Quarantined asset changed since preview; review a new plan")
    outcomes, errors, deleted_bytes = [], [], 0
    for file in plan.asset.files:
        document = None
        entry = None
        deleted = False
        try:
            document = _load_quarantine_manifest(plan.archive_root, plan.quarantine_root, file.manifest_path)
            entry = document["files"][file.entry_index]
            if entry["quarantine_path"] != str(file.path) or tuple(entry["identity"]) != file.identity:
                raise ValueError("Manifest changed since preview")
            attempt = {"timestamp": datetime.now(UTC).isoformat(), "outcome": "pending", "error": None}
            entry.setdefault("deletion_history", []).append(attempt)
            _write_manifest(file.manifest_path, document)
            _safe_path(plan.quarantine_root, file.path)
            if _identity(file.path) != file.identity:
                raise ValueError("Quarantined file changed immediately before deletion")
            file.path.unlink()
            deleted = True
            deleted_bytes += file.identity[2]
            entry.setdefault("quarantine_outcome", entry["outcome"])
            entry["outcome"] = "deleted"
            attempt["outcome"] = "deleted"
            outcomes.append(FileOutcome(file.path, file.path, "deleted"))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            outcomes.append(FileOutcome(file.path, file.path, "failed", str(exc)))
            if entry is not None and entry.get("deletion_history"):
                entry["deletion_history"][-1].update(outcome="failed", error=str(exc))
        if document is not None:
            try:
                _write_manifest(file.manifest_path, document)
            except (OSError, ValueError) as exc:
                errors.append(f"Manifest update failed after {'deletion' if deleted else 'failure'}: {exc}; further deletion stopped")
                for remaining in plan.asset.files[len(outcomes):]:
                    outcomes.append(FileOutcome(remaining.path, remaining.path, "not attempted", "Manifest update failed"))
                break
    return AssetDeletionResult(plan.asset.symbol, tuple(outcomes), deleted_bytes, tuple(errors))
