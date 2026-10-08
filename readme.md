# HdW Crypto Data

Utilities for downloading Binance Vision candlestick data, merging it with recent Binance API candles, and loading cleaned crypto time-series datasets for analysis.

The core package is intentionally kept lightweight. GUI tools and technical-analysis chart helpers are optional package modules; heavy dependencies are imported only when those tools are used.

Repository: <https://github.com/hansdeweme/HdWCryptoData>

Version 0.4.0 adds the local Crypto Archive Manager and shared SQLite archive index.
See [the changelog](changelog.md) for release details. The Showcase App opens
standalone HTML charts and logs chart paths and calculation errors to the terminal.

## What It Does

- downloads historical Binance Vision kline CSV files
- verifies downloaded archives with Binance checksum files
- stores monthly and daily spot kline data in the Binance Vision folder layout
- merges historical files with recent live Binance candle data
- writes a self-documenting total dataset name, for example `BONKUSDT-spot-1h-total-2026-12-22T00-00-00Z--2026-12-22T23-00-00Z.csv`
- loads total datasets into pandas DataFrames with timezone handling and optional gap filling

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

## Installation

The core package supports Python 3.11 or newer. Python 3.13 is recommended for the optional chart dependencies. From the repository root:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -e .
# Optional GUI and chart tools:
py -m pip install -e ".[gui]"
```

The optional GUI and chart files may need extra packages such as PyQt6, Plotly, pandas-ta, ta, SciPy, and openpyxl.

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
    showcase_pyqt_app.py
    showcase_sources.py
    showcase_ui.py
    stylesheet.py
    ta_charts.py
  examples/
    binance_dump.py
    settings.json
    ta_charts.py              # compatibility imports
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
- `python -m hdw_crypto_data.showcase_pyqt_app` launches the PyQt6 showcase.
- `hdw_crypto_data.ta_charts` contains the optional analysis and Plotly helpers.
- `python -m hdw_crypto_data.archive_manager_gui` launches the Archive Manager.

Install the `gui` extra for these GUI and chart tools.

## Settings

Most examples use a `settings.json` file. The download script defaults to
`examples/settings.json`. The showcase checks the working directory first, then
`examples/settings.json` in a source checkout. Relative paths are resolved beside
the selected settings file. The Archive Manager checks the working directory.
For an installed package, create `settings.json` in your working directory.

Example:

```json
{
  "spot": "D:\\Coding\\forecast\\",
  "full_spot": "D:\\Coding\\forecast\\spot",
  "crypto_icons": "D:\\Coding\\forecast\\crypto_icons",
  "preferred_time_zone": "CET",
  "quote_currency": "USDT",
  "open_candle": "exclude"
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

