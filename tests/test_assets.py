"""Opt-in live smoke test: python -m examples.test_assets."""

import argparse
from datetime import datetime, timezone

from hdw_stock_data import HdWStockDataError, YahooStockLoader


DEFAULT_SYMBOLS = ("AAPL", "ASML.AS", "^GSPC", "^VIX")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--days", type=int, default=5)
    parser.add_argument("--timezone", default="Europe/Amsterdam")
    parser.add_argument("--save-to", help="Optional directory for CSV snapshots")
    args = parser.parse_args()

    loader = YahooStockLoader()
    failures = []
    print(f"Live Yahoo smoke test at {datetime.now(timezone.utc).isoformat()}")
    print(f"Interval: 1h; lookback: {args.days} days; timezone: {args.timezone}")
    for symbol in args.symbols:
        print(f"\n--- {symbol} ---", flush=True)
        try:
            result = loader.load(
                symbol, interval="1h", days=args.days,
                preferred_tz=args.timezone, save_to=args.save_to,
            )
        except (HdWStockDataError, ValueError) as exc:
            failures.append(symbol)
            print(f"FAIL: {type(exc).__name__}: {exc}")
            continue

        descriptor = result.descriptor
        print(f"PASS: {descriptor.rows} rows; exchange: {descriptor.exchange}; "
              f"currency: {descriptor.currency}")
        print(f"Range: {descriptor.actual_start} to {descriptor.actual_end}")
        print(result.dataframe.tail().to_string())
        for warning in result.warnings:
            print(f"Warning: {warning}")
        if result.filepath:
            print(f"CSV: {result.filepath}")

    print(f"\n{len(args.symbols) - len(failures)}/{len(args.symbols)} symbols passed.")
    if failures:
        print(f"Failed symbols: {', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
