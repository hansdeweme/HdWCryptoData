# HdW Crypto Data

Utilities for downloading Binance Vision candlestick data, merging it with recent Binance API candles, and loading cleaned crypto time-series datasets for analysis.

The core package is intentionally kept lightweight. GUI tools and technical-analysis chart helpers live outside the `hdw_crypto_data` package so heavy optional dependencies do not get imported with the core data pipeline.

Repository: <https://github.com/hansdeweme/HdWCryptoData>

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

The core package supports Python 3.11 or newer. Use Python 3.13 for the optional analysis tools and showcase. From the repository root:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Optional extras are `parallel` (mpire), `analysis` (charting and spreadsheet helpers), and `gui` (analysis tools plus PyQt6):

```powershell
python -m pip install -e ".[gui,parallel]"
```

The root `requirements.txt` contains pinned acquisition dependencies; it does not install the project or the GUI extras.

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
  examples/
    binance_dump.py
    settings.json
    showcase_pyqt_app.py
    showcase_sources.py
    showcase_ui.py
    stylesheet.py
    ta_charts.py
  tests/
    smoke_test.py
    test_*.py
  pyproject.toml
  requirements.txt
  readme.md
```

Typical Binance Vision data layout under `full_spot`:

```text
spot/
  monthly/klines/BONKUSDT/1h/*.csv
  daily/klines/BONKUSDT/1h/*.csv
```

## Optional Tools

These files are outside the core package:

- `examples/binance_dump.py` is a command-line runner for `BinanceVisionDumper`.
- `examples/showcase_pyqt_app.py` is the shared PyQt6 market-data showcase.
- `examples/showcase_sources.py` defines the common `MarketDataset` contract and optional stock/crypto adapters.
- `examples/showcase_ui.py` and `examples/stylesheet.py` provide the interface and styling.
- `examples/ta_charts.py` contains technical-analysis and Plotly chart helpers.

These optional tools may require heavier dependencies such as PyQt6, Plotly, pandas-ta, ta, SciPy, and openpyxl.

### Shared Stock and Crypto Showcase

Install the GUI extra above, then install the optional stock package to enable both sources:

```powershell
python -m pip install hdw-stock-data
# Alternatively, install the neighboring source checkout:
python -m pip install -e ..\HdWStockData
```

Launch from the repository root:

```powershell
python -m examples.showcase_pyqt_app
```

Direct execution with `python examples/showcase_pyqt_app.py` is also supported. These example files belong to the source checkout and are not installed with the core package.

The app supports hourly Yahoo stock data through `hdw_stock_data` and hourly Binance crypto data through `hdw_crypto_data`. Missing source packages disable acquisition for that source; CSV import and supplied DataFrames remain available. Restart after installing a missing package.

CSV import expects a `dt` column containing timestamps with explicit timezone offsets, plus numeric `open`, `high`, `low`, `close`, and `volume` columns. Rows must be nonempty, sorted, and have unique timestamps. `number_of_trades` is optional. Raw Binance total CSVs must first be loaded with `TotalDatasetLoader` and exported using `df.to_csv("market.csv", index_label="dt")`.

For an existing DataFrame, use `MarketDataset.from_dataframe(df, symbol="AAPL", interval="1h")` from `examples.showcase_sources`, then pass the result to `MiniDumperApp(datasets=[dataset])` from `examples.showcase_pyqt_app` after creating a `QApplication`. The DataFrame must have the same numeric columns and a sorted, unique, timezone-aware `DatetimeIndex`.

## Settings

The example settings file is `examples/settings.json`. Edit its machine-specific paths before running acquisition:

```json
{
  "spot": "D:\\Coding\\forecast\\",
  "full_spot": "D:\\Coding\\forecast\\spot",
  "crypto_icons": "D:\\Coding\\forecast\\crypto_icons",
  "stock_icons": "D:\\Coding\\forecast\\stock_icons",
  "preferred_time_zone": "CET",
  "quote_currency": "USDT",
  "open_candle": "exclude"
}
```

`full_spot` should point to the directory containing the spot data tree. The dumper handles both `base/spot/...` and `base/...` layouts where possible. `open_candle` controls recent REST candles: `exclude` omits unfinished live candles, while `include` keeps them for dashboards or live inspection.

The showcase always reads settings beside its script. Its crypto adapter uses `spot` as the archive base and derives `full_spot` from it. Relative `spot`, `stock_icons`, and `crypto_icons` paths in the showcase resolve against `examples/`; icon directories are optional. The download CLI also defaults to the settings file beside its script, accepts `--settings PATH`, and prefers `full_spot` over `spot`. Use absolute data paths when sharing settings with the core package or CLI, where relative data paths resolve against the working directory.

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
    "examples/settings.json",
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

loader = TotalDatasetLoader("BONK", "examples/settings.json")
df = loader.load_total_dataframe(mode="ta", preferred_tz="CET")
print(df.tail())
```

Automatic discovery selects the most recently modified total CSV matching both the market and `data_frequency` setting (default `1h`) in the working directory. If none matches, it falls back to the legacy `<MARKET>-total.csv` filename. Pass `file_path=result.filepath` to load a specific build output.

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
python examples/binance_dump.py BONK
python examples/binance_dump.py BONK --start 2026-08-01 --end 2026-08-21
```

The CLI exits normally on success and prints `* * * KLAAR * * *`. Argument errors are handled by `argparse`. Download failures raise `BinanceVisionDumpError`, so the process exits non-zero and prints the exception traceback unless you catch it from your own wrapper.

## Testing

Install the analysis extra for the chart tests, then run the unit tests from the repository root:

```powershell
python -m pip install -e ".[analysis]"
$env:NUMBA_DISABLE_JIT='1'
python -m unittest discover -s tests -p "test*.py"
```

`NUMBA_DISABLE_JIT=1` avoids optional pandas-ta/numba cache issues when importing chart tests. `tests/smoke_test.py` is a manual end-to-end script and is not part of unit-test discovery. Run it with `python tests/smoke_test.py`; it reads `examples/settings.json` and requires network access and suitable historical data.

## Notes

The package downloads public market data from Binance endpoints. Network failures, missing Binance archives, checksum mismatches, rate limits, and invalid archives are reported explicitly by the dumper.

Original design notes: <https://code2trade.dev/c>
 
