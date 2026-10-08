# Shared SQLite archive index

**Writers keep the cache current; the manager verifies and repairs it; the filesystem
remains authoritative.** SQLite stores metadata, not candles, and can be rebuilt at
any time. A cached record never proves that a file still exists and never authorizes
a filesystem mutation.

## Location and legacy quarantine

`archive_paths.archive_locations()` is the single resolver. Select `spot` or its
parent; resolution does not depend on the working directory:

```text
data-root/
├── spot/                       active provider files
└── .hdw_archive/
    ├── archive_index.sqlite    rebuildable active-file index
    └── quarantine/            default for new installations
```

SQLite WAL/SHM files remain beside the database. Existing sibling `spot-quarantine`
directories are **reused**, including all existing manifests; they are not silently
abandoned, renamed or rewritten. If a legacy directory exists, the manager displays
and uses it for both old and new quarantine operations. Do not move or rename
quarantine folders manually: their manifests contain absolute locations. Custom
quarantine roots passed to the core API continue to work.

Active scanning excludes metadata/quarantine directories, the builder's `hdw-*`
working directories, and downloader staging files (`.part`, `.tmp`, `.zip`,
`.CHECKSUM`). Committed unsupported files remain visible as warnings. Generated
totals/live output outside `spot` are outside this index's scope.

## Startup, refresh and repair

The documented GUI launch command is unchanged:

```powershell
python -m hdw_crypto_data.archive_manager_gui
```

When the configured root is valid, startup automatically loads cached inventory in
a worker, displays **Cached inventory loaded — verifying archive**, and starts
incremental background verification. Cached startup queries SQLite summaries without
opening or statting individual archive files. A missing database initializes empty
and the first verification discovers the archive. Configuration still comes from
`full_spot` or `spot` in `settings.json`; choosing a different root and clicking
Refresh uses that root's adjacent database.

- **Refresh / Verify** checks directory entries and file metadata. New or changed
  files and previously uninspected files are read deeply. Unchanged fingerprints
  retain cached observed ranges, counts and validation issues. Missing files are
  removed. Copying, editing or deleting files manually is repaired on the next
  verification; there is no filesystem watcher or scheduled scan.
- **Deep rescan selected** rereads every file in the selected symbol/interval,
  ignoring unchanged fingerprints. Other groups keep their deep results.
- **Rebuild index** asks for confirmation, rereads all active contents, and replaces
  derived records only after inspection succeeds. It deletes no archive files or
  quarantine manifests. A valid old index is retained if inspection fails. SQL
  changes are committed atomically using the existing database, rather than swapping
  a live WAL database while other writers may be using it.

The final status reports new, changed, missing, unchanged and deeply inspected counts.
**Verification complete** means the reconciliation committed without detected
directory issues, races or concurrent index changes. **Verification pending** means
another refresh or repair is needed. Validation warnings/unreadable content remain
visible per file; verification does not certify OHLCV correctness or gap-free data.
Closing the window requests cancellation and waits for the worker. An interrupted
scan leaves `reconciliation_complete=false`; the next startup verifies again.

If SQLite is locked, read-only, corrupt or incompatible, the GUI stays usable and
falls back to filesystem inspection, explicitly displaying **Index unavailable**.
Cache fallback is not presented as a successfully repaired index. Explicit Rebuild
recovers corrupt/incompatible SQLite files under the bounded name
`archive_index.diagnostic.sqlite` (and corresponding sidecars), then constructs a
new index. Lock/permission errors do not trigger diagnostic rotation. Stop other
applications before rebuilding a corrupt/incompatible database; a locked database
may refuse recovery. Malformed cached record payloads can be repaired by an ordinary
transactional full rebuild without decoding their old values.

To discard the cache manually, first **stop related applications**, then remove only
`archive_index.sqlite` and its `-wal`/`-shm` files, if present. Keep the `.hdw_archive`
directory and its quarantine subdirectory. Removing `.hdw_archive` would also remove
quarantined data. The next launch reconstructs the index.

## Schema and persistence

Schema version **1** is recorded in SQLite `PRAGMA user_version` and metadata.
`archive_file` contains one row per archive-relative path, with symbol/interval,
market, bytes, nanosecond mtime, filesystem identity, advertised/observed ranges,
row count, validation state/issues, recognized flag, indexed time and write revision.
`index_metadata` records the canonical root path and device/inode identity, schema
and implementation versions, write generation, verification token/completion state,
and last successful incremental and full/deep timestamps.

Times use UTC ISO 8601 with `+00:00`. Paths are portable forward-slash relative keys;
Windows drive/root paths and traversal are rejected on cache decoding. Actual
mutations still resolve and validate paths and symlink/junction boundaries afresh.

The fingerprint is relative path, size and nanosecond mtime, with the existing
device/inode identity as an additional replacement check. This is an optimization,
not a content hash. An edit that preserves every fingerprint field needs a forced
deep rescan to be detected. No candle rows or redundant summary tables are stored.
Indexed SQL aggregation supplies counts, rows, coverage, bytes and status; indexes
cover symbol/interval grouping and validation status.

Supported version-zero prototype databases have the version-one file columns except
`revision`; they migrate in a transaction by adding that field. Unknown layouts,
future versions and root identity mismatches require explicit rebuild or a compatible
application. A failed migration rolls back, preserving the previous database.

## Writer and cleanup integration

- Binance Vision CSV extraction updates the index **after** final atomic replacement.
  The raw downloader has no dataframe metadata, so each newly committed CSV is read
  once to determine observed timestamps/counts; the manager then reuses that result.
- The generic Vision client hooks committed CSV downloads, excluding ZIP/partial
  downloads. Failed checksum, extraction or final commits create no new record.
- The dumper's existing old-daily-file cleanup removes successfully deleted entries.
- `TotalDatasetBuilder` live-data writes now use temporary output plus atomic commit.
  Live/total dataframe outputs below `spot` pass known ranges and row counts directly,
  without reopening the CSV. Outputs outside `spot` require no index work. Internal
  merge copies and temporary work-directory removal are deliberately not indexed.
- Quarantine removes active index entries only for successfully moved files. Failed
  moves retain their source's record. Restore upserts only successful restorations;
  conflicts do not create false active records. The manifest remains authoritative
  for operation history. Permanent second-level deletion affects quarantine only,
  so it does not remove additional active-index records.

An index-maintenance failure logs a warning requesting verification and never undoes
or reports failure of a successful primary archive write. If the database cannot be
updated, its persistent dirty flag may also be unavailable; every manager startup
verifies regardless of that flag. Existing downloader, builder and analysis APIs
remain compatible, apart from additive index APIs and safer atomic live-file writes.
Legacy standalone imports of the Vision modules also retain primary write behavior
when relative package imports are unavailable: index maintenance logs a warning and
is skipped. Use the installed package to enable the shared index.

## Concurrency and limitations

SQLite uses WAL where supported, foreign keys enabled, a 1.5-second busy timeout,
explicit short write transactions, and a fresh connection for each operation/thread.
Connections are always closed. No transaction spans downloads, CSV parsing, preview
confirmation or a batch of filesystem moves. Read snapshots keep detail rows and
SQL summaries consistent while writers update the database.

Reconciliation takes a versioned snapshot, inspects outside the transaction, and
conditionally commits only records whose revisions have not changed. Concurrent
writer updates are retained; verification remains pending and a later refresh
repairs remaining differences. File identities are checked again before commit.
Rebuild uses the same revision guards and atomic commit. This coordinates derived
index writes, not external filesystem tools, and does not add distributed locking.

The GUI currently repopulates its tables after verification, preserving the selected
group when available; it does not update individual rows in place. Metadata traversal
still occurs during verification to discover manual filesystem changes. Unsupported
layouts are metadata-only records, and the cache has no persistent summary table,
content hashes, watcher, or market-data storage.

## Validation and measured performance

Run the tests below to verify the current checkout. Test counts and skips depend
on the platform and optional dependencies.
Tests cover schema/settings, migration rollback, corruption and payload repair,
cached startup before verification, manual file changes/removal, interruption,
targeted rescan, failed rebuild preservation, writer commits/failures and known
metadata, concurrent readers/writers, busy locks, quarantine/restore/conflicts, legacy
quarantine, staging exclusion and cache-independent cleanup validation.

Synthetic benchmark on this Windows/Python 3.13 environment, 40 CSVs / 9,600 rows:

| Operation | Time | CSV content rereads |
| --- | ---: | ---: |
| Initial deep verification | 0.7755 s | 40 first inspections |
| Load cached inventory and SQL summaries | 0.0018 s | 0 |
| Verify unchanged archive | 0.0201 s | 0 |

These are local measurements, not latency guarantees. Warm verification was roughly
39 times faster than the initial deep inspection in this sample. The content-read
assertion is deterministic; timing is reported rather than asserted by tests.

Run the suite with `python -m pytest -q`. To print the benchmark:
`python -m pytest -q -s tests/test_archive_index.py`.
