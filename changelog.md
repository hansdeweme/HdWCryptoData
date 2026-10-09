## 0.4.1

### Changed

- Partial day/month coverage no longer produces an Archive Manager warning.
- Automatically remove retired partial-period warnings from the shared index while
  preserving other validation issues and updating changed record revisions.
- Keep warnings for timestamps outside the advertised range and invalid data.

## 0.4.0

### Added

- Added the Crypto Archive Manager companion application.
- Added cached archive inventory by asset and interval.
- Added advertised and observed coverage inspection.
- Added incremental verification, selected deep rescans and complete index rebuilds.
- Added recoverable file quarantine with operation manifests and safe restoration.
- Added optional second-level permanent cleanup of reviewed quarantined assets.
- Added a shared, versioned SQLite archive index beside the `spot` directory.

### Changed

- Integrated archive-index maintenance with Binance Vision downloads and extraction.
- Updated `binance_vision_client`, `binance_vision_dumper` and `total_dataset_builder`
  to update the index after successful file commits.
- Made live/total dataset writes atomic where applicable.
- Reused shared GUI styling across the showcase and Archive Manager.
- Improved archive-path resolution and compatibility with existing quarantine directories.

### Safety and compatibility

- The filesystem remains authoritative; the SQLite index is derived and rebuildable.
- Index failures do not invalidate successfully written market-data files.
- Permanent deletion is available only for files already moved into quarantine.
- Existing acquisition and analysis APIs remain compatible.
