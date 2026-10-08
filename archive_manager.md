# Crypto Archive Manager

The desktop manager inventories local Binance spot CSV archives and lets you move
an explicitly selected set of files into recoverable quarantine. It never downloads,
edits, merges, or fills gaps. The first cleanup level is recoverable quarantine;
the separate second level can permanently delete a selected quarantined asset
after explicit confirmation.

From an environment with the project's GUI dependencies installed, launch:

```powershell
python -m hdw_crypto_data.archive_manager_gui
```

PyQt6 is already part of the project's `gui` extra (`pip install -e ".[gui]"`).
The manager reuses the showcase's shared dark theme from
`hdw_crypto_data.stylesheet`, with clearer table headings and red permanent-deletion
actions. The top-level `stylesheet.py` keeps its existing `DARK_STYLE` import available
to the showcase.
Example screenshots using synthetic data: [main window](archive_manager_preview.png),
[quarantined assets](quarantine_assets_preview.png), and
[deletion confirmation](quarantine_delete_preview.png).
The manager itself only needs PyQt6 and the existing base package dependencies;
it does not import the analytical GUI or chart libraries. Python 3.11+ is supported
by the new modules. No existing entry points or public exports have changed.

Choose either the `spot` directory or its parent. The initial path comes from
`full_spot`, falling back to `spot`, in the current working directory's
`settings.json`, using the existing settings loader. A valid configured root loads
cached inventory and starts background verification on launch.
An empty directory named `spot` is a valid empty archive.

The supported raw hierarchy is:

```text
spot/daily/klines/BTCUSDT/1h/BTCUSDT-1h-2026-01-01.csv
spot/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2026-01.csv
```

Path and filename verification reuse `BinanceVisionDumper`'s existing path and
filename APIs. Other files are displayed with warnings; they are not silently
treated as supported market data. They can still be explicitly selected for
quarantine. Unrecognized files appear in an Unknown group, or their inferred
symbol/interval group when their directory layout is recognizable. The recognized
data-file count excludes unsupported/malformed files, while byte totals include
every encountered file. Linked directories are skipped and reported; linked files
cannot be quarantined. Unreadable directories produce scan-level warnings.

The shared SQLite index persists counts and coverage between sessions. Cached
inventory is displayed before background verification finishes and is labeled cached.
**Refresh / Verify** checks metadata and deeply reads only new, changed or previously
uninspected files. **Deep rescan selected** forces a selected asset/interval reread.
**Rebuild index** confirms a complete deep rebuild of derived metadata without
changing archive files. Scans run in a worker thread and support shutdown cancellation.
Cleanup independently revalidates filesystem state. See [shared index documentation](archive_index.md)
for ownership, schema, recovery, concurrency and the measured performance comparison.

Advertised coverage is the entire UTC day or month named by the filename.
Observed coverage is the minimum and maximum valid candle open timestamps read
from the contents. Seconds, milliseconds and microseconds use the existing package
timestamp normalization; displayed timestamps have second precision. Optional
`open_time` headers are accepted. Row counts include invalid data rows but exclude
the header. Empty, invalid, duplicate/out-of-order timestamps and range mismatches
are reported. Validation focuses on timestamps, not every OHLCV field or expected
candle continuity. Partial day/month coverage may be legitimate, such as a new
listing, so it is a warning rather than a cleanup recommendation.

Summary coverage columns show **observed** coverage only, and remain Unknown for
fast scans. Mixed or unreadable groups can have partial observed coverage; unknown
row counts stay Unknown. Individual advertised and observed ranges are always
separate in the detail table. Search filters symbols. Click column headers to sort,
select a group to see files, and tick individual file checkboxes. **Select all files**
checks every file in the selected asset/interval group; you can then untick any
individual exceptions. It preserves selections in other groups. Selections can
span groups and are cleared by a refresh. **Open containing folder** uses the
platform desktop service and reports when unavailable.

**Preview selected files** presents the exact source paths, count, bytes, affected
groups, dates, warnings and destination. It uses observed dates where available,
otherwise advertised dates, and explicitly warns about unknown observed coverage.
The preview button lights up in teal and displays the file count when files are selected.
Cancel leaves the filesystem untouched. Only **Confirm move to quarantine** moves
files. Byte totals represent data moved out of the active archive, not disk space
freed: quarantine retains those bytes on disk.

Quarantine defaults to `.hdw_archive/quarantine` beside `spot`, outside the scanned
tree. An existing sibling `spot-quarantine` is reused with its manifests intact.
Each operation has a UTC timestamp plus UUID directory and preserves the
path relative to `spot` under an operation's `files/` directory. This separates
archive files from manager metadata, even if a source is named `manifest.json`.
A version 1 JSON `manifest.json` records the operation ID,
roots, UTC operation time and, for each file, original/quarantine/relative paths,
size, filesystem identity (device, inode, size, nanosecond mtime), outcome, error
and move timestamp. It is written before any move and updated after each attempt.
The result dialog summarizes moved, failed and unattempted counts, bytes and affected
groups. Failure details can be expanded; the full file history remains in the manifest.
Results distinguish moved files from failed or unattempted files. The GUI refreshes
after a complete or partial operation. A manifest update failure stops further
moves and reports the exact completed and unattempted files; pending manifest
entries preserve paths for recovery after an interruption.

All selected source and destination paths are checked against their roots; traversal,
symlinks/junctions, directories, stale identities and destination overwrites are
rejected. Moves use atomic hard-link creation followed by removing the source name.
This requires a filesystem supporting hard links and quarantine on the same volume.
Cross-volume or unsupported filesystem attempts fail without removing the source.
If removing the source name fails, both paths remain and the operation reports a
failure. Quarantine and restore are synchronous in the GUI, so closing cannot leave
invisible mutation work running; large batches can temporarily occupy the window.
Avoid running acquisition or other tools that modify these same files during moves.
File identity checks detect ordinary concurrent changes, but are not content hashes.

## Restore through the core API

Restore is intentionally a tested core API in this first version. Use the operation
directory name shown in the result dialog or manifest:

```python
from hdw_crypto_data.archive_manager import restore_quarantine_operation

result = restore_quarantine_operation(
    "20261007T160000Z-REPLACE_WITH_ACTUAL_UUID",
    archive_root=r"D:\data\spot",
)
for file in result.files:
    print(file.outcome, file.destination, file.error)
```

The supplied archive root must match the manifest. If you selected a custom
quarantine root through the core API, also pass `quarantine_root`. Restore validates
manifest paths against explicitly supplied roots and original identities. It never
overwrites an existing file. A conflict leaves both files unchanged and records a
failed restore attempt; resolve the conflict manually and retry. The original move
metadata and UTC restore history remain in the manifest. Already restored files are
skipped. Pending entries from an interrupted operation are inspected by attempting
a safe restore; absent quarantine sources or conflicting original paths are reported.
Retain the operation directory and manifest for history.

The GUI and core are separate. Independent core APIs are `scan_archive`,
`validate_inventory`, `build_cleanup_plan`, `execute_quarantine`, and
`restore_quarantine_operation`; models are frozen dataclasses. Building a plan is
read-only. Calling `execute_quarantine` is the explicit mutation decision for API
users; the GUI always requires preview confirmation first.

## Second cleanup level: quarantined assets

The main window shows the quarantine path beside **Quarantined assets…**. This
opens a separate overview with one row per asset, combining all its intervals and
quarantine operations. Each row shows intervals, file count, bytes and operation
count. **Open quarantine folder** opens the storage location.

Select an asset and choose **Delete selected asset permanently…**. The preview
shows its symbol, intervals, file count, size and quarantine location. Type the
exact symbol to enable **Delete permanently**, or cancel without changing anything.
This deletes all currently inventoried quarantine files for that asset. It does
not delete files from the active spot archive, and does not offer individual-file
deletion. Once deleted, these quarantined files cannot be restored.

The overview reads manifests and filesystem metadata only; opening or refreshing
it does not reread active CSVs. Changed asset contents require a new preview. Paths
and file identities are revalidated immediately before each deletion. Files are
unlinked individually; no recursive directory removal is performed. Manifests and
empty operation directories remain for history. Deletion outcomes and UTC attempts
are recorded in `deletion_history`; deleted entries are excluded from overview and
restore. Results summarize deleted, failed and unattempted files, with expandable
errors. Partial failures leave remaining files available for retry. Reported bytes
represent files removed; other hard links may retain the underlying storage.

Malformed manifests, unexpected or untracked files, missing moved files, linked
paths or identity mismatches are displayed as issues and disable permanent deletion.
If manifest updates fail after a deletion, further work stops and the result reports
what was deleted. A pending deletion history entry and missing-file inventory issue
identify the operation needing manual review. No deletion of real quarantine data
is performed by automated tests; all deletion tests use synthetic temporary files.

Independent core APIs for this level are `scan_quarantine`,
`build_asset_deletion_plan`, and `execute_asset_deletion`. Calling the execution API
is the explicit permanent-deletion decision; the GUI always requires confirmation.

Current limitations: no restore GUI, coverage timeline, ZIP inspection,
or automatic overlap decisions. Generated total datasets
outside `spot` are outside this manager's scope. Existing downloader cleanup,
acquisition and analytical behavior are unchanged.

Offline tests use synthetic files and temporary directories, including headless
PyQt GUI flows:

```powershell
python -m pytest -q
```
