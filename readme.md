# HdW Crypto Data

Utilities for downloading Binance Vision candlestick data, merging it with recent Binance API candles, and loading cleaned crypto time-series datasets for analysis.

The core package is intentionally kept lightweight. GUI tools and technical-analysis chart helpers live outside the `hdw_crypto_data` package so heavy optional dependencies do not get imported with the core data pipeline.

Repository: <https://github.com/hansdeweme/HdWCryptoData>

Extended Documentation: <https://code2trade.dev/from-binance-scripts-to-a-reusable-python-data-package/>

## What It Does

- downloads historical Binance Vision kline CSV files
- verifies downloaded archives with Binance checksum files
- stores monthly and daily spot kline data in the Binance Vision folder layout
- merges historical files with recent live Binance candle data
- writes a `<MARKET>-total.csv` dataset, for example `BONKUSDT-total.csv`
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

Use Python 3.13 or newer. From the repository root:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r hdw_crypto_data\requirements.txt
```

The optional GUI and chart files may need extra packages such as PyQt6, Plotly, pandas-ta, ta, SciPy, and openpyxl.

## Folder Layout

Typical repository structure:

```text
HdWCryptoData/
  hdw_crypto_data/
    binance_vision_client.py
    binance_vision_dumper.py
    binance_rest_client.py
    total_dataset_builder.py
    total_dataset_loader.py
    symbols.py
    requirements.txt
  binancedump.py
  smoke_test.py
  showcase_pyqt_app.py
  ta_charts.py
  test_*.py
```

Typical Binance Vision data layout under `full_spot`:

```text
spot/
  monthly/klines/BONKUSDT/1h/*.csv
  daily/klines/BONKUSDT/1h/*.csv
```

## Optional Tools

These files are outside the core package:

- `../binancedump.py` is a small command-line runner for `BinanceVisionDumper`.
- `../showcase_pyqt_app.py` is a PyQt6 showcase app for testing the pipeline.
- `../ta_charts.py` contains optional technical-analysis and Plotly chart helpers.

These optional tools may require heavier dependencies such as PyQt6, Plotly, pandas-ta, ta, SciPy, and openpyxl.

## Settings

Most examples use a `settings.json` file:

```json
{
  "spot": "D:\\Coding\\forecast\\",
  "full_spot": "D:\\Coding\\forecast\\spot",
  "crypto_icons": "D:\\Coding\\forecast\\crypto_icons",
  "preferred_time_zone": "CET",
  "quote_currency": "USDT"
}
```

`full_spot` should point to the directory containing the spot data tree. The dumper handles both `base/spot/...` and `base/...` layouts where possible.

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

`force_merge=False` requires recent historical Binance Vision files before merging. Use `force_merge=True` to merge anyway when historical files are older or incomplete, for example during manual recovery or experiments.

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
py -3 binancedump.py BONK
py -3 binancedump.py BONK --start 2026-08-01 --end 2026-08-21
```

The CLI exits normally on success and prints `* * * KLAAR * * *`. Argument errors are handled by `argparse`. Download failures raise `BinanceVisionDumpError`, so the process exits non-zero and prints the exception traceback unless you catch it from your own wrapper.

## Testing

Run the unit tests from the repository root:

```powershell
$env:NUMBA_DISABLE_JIT='1'
py -m unittest discover -p "test*.py"
```

`NUMBA_DISABLE_JIT=1` avoids optional pandas-ta/numba cache issues when importing chart tests. `smoke_test.py` is a manual end-to-end script and is not part of unit-test discovery.

## Notes

The package downloads public market data from Binance endpoints. Network failures, missing Binance archives, checksum mismatches, rate limits, and invalid archives are reported explicitly by the dumper.

Original design notes: <https://code2trade.dev/c>
 
