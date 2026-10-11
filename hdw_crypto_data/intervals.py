"""Fixed Binance intervals supported by total dataset research APIs."""
SUPPORTED_INTERVALS = frozenset({"15m", "1h", "1d"})


def normalize_interval(interval):
    # Preserve case: Binance's 1M denotes a calendar month, not a minute.
    value = str(interval).strip()
    if value not in SUPPORTED_INTERVALS:
        raise ValueError(f"Unsupported interval {interval!r}; use 15m, 1h or 1d")
    return value
