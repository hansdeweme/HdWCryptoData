# binacedump.py
# Copyright (c) 2025, 2026 Hans De Weme
# Licensed under the MIT License (https://opensource.org/licenses/MIT).
# part of the HdW_crypto_data Project
# Small runner for downloading Binance Vision kline data

from __future__ import annotations
import argparse
import datetime as dt
import json
from multiprocessing import freeze_support
from pathlib import Path
# local imports
from hdw_crypto_data.binance_vision_dumper import BinanceVisionDumper

DEFAULT_ASSET = "BONK"
DEFAULT_FREQUENCY = "1h"

def _parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    return dt.datetime.strptime(value, "%Y-%m-%d").date()


def _load_settings(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as settings_file:
        return json.load(settings_file)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download Binance Vision kline CSV files.")
    parser.add_argument("asset", nargs="?", default=DEFAULT_ASSET, help="Asset symbol, e.g. BONK or BONKUSDT.")
    parser.add_argument("--settings", default="settings.json", help="Path to settings.json.")
    parser.add_argument("--dump-dir", default=None, help="Directory where the Binance data tree is stored.")
    parser.add_argument("--quote", default=None, help="Quote currency to append when asset has no quote suffix.")
    parser.add_argument("--frequency", default=DEFAULT_FREQUENCY, help="Kline frequency, e.g. 1m, 5m, 1h, 1d.")
    parser.add_argument("--start", type=_parse_date, default=None, help="Start date as YYYY-MM-DD.")
    parser.add_argument("--end", type=_parse_date, default=None, help="End date as YYYY-MM-DD.")
    parser.add_argument("--fresh", action="store_true", help="Ignore existing files when deciding date ranges.")
    parser.add_argument("--keep-daily", action="store_true", help="Do not delete daily files covered by monthly files.")
    return parser


def _market_symbol(asset: str, quote: str) -> str:
    asset = asset.upper()
    quote = quote.upper()
    if asset.endswith(quote):
        return asset
    return f"{asset}{quote}"


def main() -> None:
    args = _build_parser().parse_args()
    settings = _load_settings(Path(args.settings))

    quote = args.quote or settings.get("quote_currency", "USDT")
    dump_dir = args.dump_dir or settings.get("full_spot") or settings.get("spot") or "."
    market = _market_symbol(args.asset, quote)

    dumper = BinanceVisionDumper(
        path_dir_where_to_dump=str(Path(dump_dir).resolve()),
        asset_class="spot",
        data_type="klines",
        data_frequency=args.frequency,
    )
    dumper.dump_data(
        tickers=[market],
        date_start=args.start,
        date_end=args.end,
        is_to_update_existing=not args.fresh,
        tickers_to_exclude=["UST"],
    )
    if not args.keep_daily:
        dumper.delete_outdated_daily_results()

    print("* * * KLAAR * * *")


if __name__ == "__main__":
    freeze_support()
    main()
