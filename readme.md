# HdW Crypto Data

Utilities for downloading Binance Vision candlestick data, merging it with recent Binance API candles, and loading cleaned crypto time-series datasets for analysis.

The core package is intentionally kept lightweight. The Archive Manager is an optional package module; the showcase app and technical-analysis chart helpers live in `examples/`. Heavy dependencies are imported only when those tools are used.

Repository: <https://github.com/hansdeweme/HdWCryptoData>

Version 0.6.1 fixes mixed timestamp units when merging archive and REST candles.
Version 0.6.0 adds the lightweight, in-memory `BinanceKlineClient`.
Interval-aware total datasets and strict research loading are available since 0.5.0.
The local Crypto Archive Manager and shared SQLite archive index remain available.
See [the changelog](changelog.md) for release details. The Showcase App opens
standalone HTML charts and logs chart paths and calculation errors to the terminal.

## What It Does

- downloads historical Binance Vision kline CSV files
- verifies downloaded archives with Binance checksum files
- stores monthly and daily spot kline data in the Binance Vision folder layout
- merges historical files with recent live Binance candle data
- writes a self-documenting total dataset name, for example `BONKUSDT-spot-1h-total-2026-12-22T00-00-00Z--2026-12-22T23-00-00Z.csv`
- loads exact UTC observations for research without filling missing candles
- retains cleaned pandas DataFrame loading with timezone handling and optional gap filling for visualization

## Core Modules

- `binance_vision_dumper.py` provides `BinanceVisionDumper` for downloading historical Binance Vision data.
- `binance_vision_client.py` contains safe Binance Vision HTTP, retry, URL validation, and checksum helpers.
- `binance_rest_client.py` provides `BinanceRestClient` for recent Binance REST API candles.
- `total_dataset_builder.py` provides `TotalDatasetBuilder` for building total CSV datasets.
- `total_dataset_loader.py` provides `TotalDatasetLoader` for loading and normalizing total CSV files.
- `symbols.py` provides symbol normalization helpers.

Public imports are available from the package root:

```python
from hdw_crypto_data import BinanceVisionDumper, TotalDatasetBuilder, TotalDatasetLoader
```

## Strict Research Data (0.5.0)

Use an explicit interval (`15m`, `1h` or `1d`) when building or locating total
research datasets. The legacy builder default remains `1h`.

```python
from hdw_crypto_data import TotalDatasetBuilder, TotalDatasetLoader

# Explicit update: build a total dataset and omit the open live candle.
result = TotalDatasetBuilder("BONK", settings, interval="15m").build(
    open_candle="exclude")

# Exact observations: no sorting, deduplication, interpolation or side files.
loader = TotalDatasetLoader("BONK", settings, current_dir=total_directory)
frame = loader.load_research_dataframe(
    file_path=result.filepath, interval="15m", preferred_tz="UTC")

# Read-only: discover an existing hourly total without building/updating it.
hourly = loader.load_research_dataframe(interval="1h", preferred_tz="UTC")
```

The strict loader preserves internal gaps, duplicates, row order and malformed
values; callers must validate observations before analyzing them. It parses
second, millisecond and microsecond timestamps in their original units. Use
`load_total_dataframe()` for the existing optional cleaning/visualization path.

## Recent candles in memory

`BinanceKlineClient` provides recent Spot data without archive downloads,
temporary CSV files, logging, GUI imports or technical indicators:

```python
from hdw_crypto_data import BinanceKlineClient

with BinanceKlineClient() as client:
    df = client.get_klines("PEPEUSDT", interval="5m", limit=500)
```

The result has a sorted, unique UTC `DatetimeIndex` named `dt` and exactly six
numeric columns: `open`, `high`, `low`, `close`, `volume` (float64), and
`number_of_trades` (int64). Candle timestamps identify the candle's open time.
Identical duplicate timestamps collapse; conflicting duplicates, invalid numeric
values and inconsistent OHLC bounds raise `BinanceKlineResponseError`.
Missing candles are not filled or interpolated. Short/new listings can return
fewer rows (including an empty, correctly typed frame); callers enforce their
own minimum lookback requirements.

`get_klines(symbol, *, interval="1h", limit=500, closed_only=True)` accepts full
Binance pair symbols and limits from 1 through 1000. Symbols are stripped and
uppercased; intervals are case-sensitive (`1m` and `1M` are different). Spot
intervals include `1s`, `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`,
`8h`, `12h`, `1d`, `3d`, `1w` and `1M`. The archive/research APIs retain their
existing interval support and behavior.

Completed candles are the default: Binance's inclusive close time must be
strictly earlier than the UTC millisecond cutoff frozen before the call's first
request. The request's `endTime` also bounds the snapshot. The client requests
an extra candle and, when necessary at the 1000-row API ceiling, pages backward
to preserve the requested completed history. This conservative rule does not
admit a partial snapshot merely because the candle closes during retries or
response processing. It relies on an accurate system clock. Use
`closed_only=False` to explicitly include a possibly unfinished current candle.
Returned `df.attrs` records `symbol`, `interval`, `closed_only` and
`retrieval_cutoff_ms`.

Constructor options are `timeout=(5, 15)` (connect/read seconds),
`max_attempts=3`, `min_request_interval=0.25`, `backoff_factor=0.5`,
`max_retry_wait=30.0` and an optional caller-owned requests-compatible `session`.
One instance serializes calls, reuses its HTTP session, and spaces request starts.
This is per-instance pacing; applications sharing an IP still coordinate their
overall API usage. Transport failures and HTTP 5xx responses use bounded
exponential backoff. HTTP 429 respects the full `Retry-After` cooldown; a long
cooldown or HTTP 418 raises `BinanceRateLimitError` immediately, carrying
`retry_after` (seconds) and `status_code` when supplied by the response. The
instance remembers the cooldown across calls and never retries before it expires.
Other HTTP failures raise `BinanceKlineError` without retrying; malformed data or
JSON raises `BinanceKlineResponseError`. All three errors are public root exports.
The client closes its own session via `close()` or the context manager and does
not close an injected session. Closed clients cannot issue further requests.

Offline checks: `py -3.13 -m unittest discover -s tests -p test_binance_kline_client.py -v`.
API references: [Spot klines](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market)
and [rate limits](https://developers.binance.com/en/docs/products/spot/rest-api).

## Installation

The core package supports Python 3.11 or newer. Python 3.13 is recommended for the optional chart dependencies. From the repository root:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install hdw-crypto-data
# Required when running the showcase and chart examples:
py -m pip install -r examples/requirements.txt
```

For development, replace `py -m pip install hdw-crypto-data` with `py -m pip install -e .`; also install `examples/requirements.txt` to run the examples. The examples require a source checkout because they are not included in the installed wheel.

The examples requirements install `hdw-ta` (imported as `hdw_ta`) for the Keltner calculations, plus PyQt6, Plotly, pandas-ta, ta, SciPy, progressbar2, and openpyxl for the GUI, charts, and Excel export. NumPy and pandas are installed by the core package.

## Folder Layout

Typical repository structure:

```text
HdWCryptoData/
  hdw_crypto_data/
    __init__.py
    version.py
    binance_vision_client.py
    binance_vision_dumper.py
    binance_rest_client.py
    total_dataset_builder.py
    total_dataset_loader.py
    symbols.py
    archive_paths.py
    archive_index.py
    archive_manager.py
    archive_manager_gui.py
    stylesheet.py
  examples/
    requirements.txt
    binance_dump.py
    settings.json
    showcase_pyqt_app.py
    showcase_sources.py
    showcase_ui.py
    stylesheet.py            # imports the shared package theme
    ta_charts.py
  tests/
    smoke_test.py
    test_*.py
  pyproject.toml
  requirements.txt
  readme.md
  changelog.md
  archive_manager.md
  archive_index.md
```

Typical Binance Vision data layout under `full_spot`:

```text
spot/
  monthly/klines/BONKUSDT/1h/*.csv
  daily/klines/BONKUSDT/1h/*.csv
```

## Optional Tools

- `examples/binance_dump.py` is a command-line runner for `BinanceVisionDumper`.
- `python -m examples.showcase_pyqt_app` launches the PyQt6 showcase from the repository root.
- `examples.ta_charts` contains the optional analysis and Plotly helpers.
- `python -m hdw_crypto_data.archive_manager_gui` launches the Archive Manager.

Install `examples/requirements.txt` for the showcase and chart helpers. For the Archive Manager alone, install the `gui` extra with `py -m pip install "hdw-crypto-data[gui]"`.
The showcase and chart helpers require a source checkout; they are not included in the installed wheel.

## Settings

The download script and showcase use `examples/settings.json` by default.
The showcase resolves relative paths against the repository root, independently
of the working directory. Local icon folders default to `examples/stock_icons`
and `examples/crypto_icons`; either folder may be absent. The Archive Manager
checks the working directory for its own settings file. The showcase requires
a source checkout.

Portable showcase settings:

```json
{
  "spot": ".",
  "full_spot": "./spot",
  "stock_icons": "examples/stock_icons",
  "crypto_icons": "examples/crypto_icons",
  "quote_currency": "USDT",
  "preferred_time_zone": "Europe/Amsterdam"
}
```

`full_spot` should point to the directory containing the spot data tree. The dumper handles both `base/spot/...` and `base/...` layouts where possible. `open_candle` controls recent REST candles: `exclude` omits unfinished live candles, while `include` keeps them for dashboards or live inspection.

## Basic Usage

Download Binance Vision data:

```python
from hdw_crypto_data import BinanceVisionDumper

dumper = BinanceVisionDumper(
    path_dir_where_to_dump=r"D:\Coding\forecast\spot",
    asset_class="spot",
    data_type="klines",
    data_frequency="1h",
)
dumper.dump_data(tickers=["BONKUSDT"])
dumper.delete_outdated_daily_results()
```

Missing Binance archives are not always failures. New listings, inactive symbols, and dates before a market existed can legitimately return 404. The dumper reports `not_found` separately from network errors, rate limits, checksum failures, and invalid archives. If operational failures occur, `BinanceVisionDumpError.failures` contains the per-file `ArchiveDownloadResult` values with `date`, `status`, and optional `error`.

Build a total dataset:

```python
from hdw_crypto_data import BinanceRestClient, BinanceVisionDumper, TotalDatasetBuilder

vision_dumper = BinanceVisionDumper(path_dir_where_to_dump=r"D:\Coding\forecast\spot")
rest_client = BinanceRestClient()

builder = TotalDatasetBuilder(
    "BONK",
    "settings.json",
    force_merge=False,
    historical_source=vision_dumper,
    recent_source=rest_client,
)
result = builder.build()
print(result.filepath, result.rows)
```

The builder defaults to `open_candle="exclude"` from settings. You can override it per run:

```python
result = builder.build(open_candle="include")
```

`force_merge=False` requires recent historical Binance Vision files before merging. Use `force_merge=True` to merge anyway when historical files are older or incomplete, for example during manual recovery or experiments.

Total CSV filenames include the market pair, literal `spot`, candle frequency, and UTC start/end candle datetimes:

```text
<MARKET>-spot-<FREQUENCY>-total-<START_DATETIME>--<END_DATETIME>.csv
```

Load a total dataset:

```python
from hdw_crypto_data import TotalDatasetLoader

loader = TotalDatasetLoader("BONK", "settings.json")
df = loader.load_total_dataframe(mode="ta", preferred_tz="CET")
print(df.tail())
```

By default the loader cleans the data without synthesizing missing candles:

```python
df = loader.load_total_dataframe(clean=True, fill_gaps=False)
```

Set `fill_gaps=True` only when you want a continuous hourly index. In that mode, missing hourly candles are created and numeric fields are filled by interpolation/backfill/forward-fill. The returned DataFrame includes an `is_imputed` column so synthetic rows can be filtered or audited:

```python
df = loader.load_total_dataframe(fill_gaps=True)
synthetic_rows = df[df["is_imputed"]]
```

`number_of_trades` is rounded back to integer values after filling so interpolated trade counts are not fractional.

Run the small download script from the project root:

```powershell
py -3 examples/binance_dump.py BONK
py -3 examples/binance_dump.py BONK --start 2026-08-01 --end 2026-08-21
```

The CLI exits normally on success and prints `* * * KLAAR * * *`. Argument errors are handled by `argparse`. Download failures raise `BinanceVisionDumpError`, so the process exits non-zero and prints the exception traceback unless you catch it from your own wrapper.

## Local archive manager

Launch the PyQt6 inventory and recoverable cleanup utility from the project environment:

```powershell
python -m hdw_crypto_data.archive_manager_gui
```

Choose `spot` or its parent, scan, inspect symbol/interval groups, and select
individual files. A shared SQLite index under `.hdw_archive` beside `spot` supplies
cached inventory immediately; background verification inspects only new or changed
CSV contents. Writers update the index after successful commits. **Refresh / Verify**,
**Deep rescan selected**, and **Rebuild index** offer incremental and forced repair.
Cleanup requires a preview and explicit confirmation, then moves files to
`.hdw_archive/quarantine` with a JSON manifest (existing `spot-quarantine` folders
are reused). Restore is available through a tested core API and refuses overwrites.
A separate **Quarantined assets…** overview offers a second cleanup level:
permanently delete one whole quarantined asset across all intervals and operations,
after preview and explicit confirmation. Active spot files remain untouched.
See [archive manager documentation](archive_manager.md) for usage, restore
examples, safety behavior and limitations. The existing `gui` extra includes PyQt6.
See [shared index documentation](archive_index.md) for schema, ownership,
concurrency, cache repair and benchmarks. The filesystem remains authoritative.

## Testing

Run the unit tests from the repository root:

```powershell
$env:NUMBA_DISABLE_JIT='1'
py -m unittest discover -s tests -p "test*.py"
```

`NUMBA_DISABLE_JIT=1` avoids optional pandas-ta/numba cache issues when importing chart tests. `tests/smoke_test.py` is a manual end-to-end script and is not part of unit-test discovery.

## Notes

The package downloads public market data from Binance endpoints. Network failures, missing Binance archives, checksum mismatches, rate limits, and invalid archives are reported explicitly by the dumper.

Original design notes: <https://code2trade.dev/c>

