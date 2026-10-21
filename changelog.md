## 0.6.1

### Fixed

- Normalize merged candle open/close timestamps to integer microseconds before
  deduplication and chronological sorting. Mixed Binance Vision and REST epoch
  units no longer leave duplicate candles, disorder the history, or produce an
  incorrect filename range. Microsecond precision and the existing first-source
  overlap policy are preserved; original archive inputs remain unchanged.
- Add mixed seconds/milliseconds/microseconds regression coverage for 15m, 1h
  and 1d total datasets, plus exact microsecond-precision checks.

## 0.6.0

### Added

- Lightweight `BinanceKlineClient` for recent Spot candles in memory, with a
  sorted, unique UTC index and validated OHLC, volume and trade-count columns.
- Closed-candle filtering, bounded pagination, rate-limit errors and public
  exception exports, plus mocked regression coverage and usage documentation.

## 0.5.0

### Added

- Strict `TotalDatasetLoader.load_research_dataframe()` for exact research
  observations, preserving gaps, duplicates, ordering and malformed values for
  downstream validation without interpolation, filling or side-file writes.
- Explicit fixed-interval validation for `15m`, `1h` and `1d` research datasets.
- Regression coverage for interval-aware acquisition, discovery, strict loading,
  original timestamp precision and compatibility with existing hourly defaults.

### Changed

- `TotalDatasetBuilder(..., interval=...)` consistently selects the requested
  archive, REST and merge interval; `MakeTotalResult` reports that interval.
- Recent-data filenames include their interval to avoid cross-interval collisions.
- Explicit interval-aware total-file discovery keeps intraday fallback files
  separate while preserving zero-argument legacy hourly behavior.
- Existing cleaned/visualization loading APIs remain available unchanged.

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
