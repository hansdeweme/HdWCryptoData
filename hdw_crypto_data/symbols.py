"""Shared Binance symbol normalization and validation."""

from __future__ import annotations
import re

SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]{2,20}$")

def normalize_symbol(value: str) -> str:
    symbol = str(value).strip().upper()
    if not SYMBOL_PATTERN.fullmatch(symbol):
        raise ValueError(f"Invalid Binance symbol: {value!r}")
    return symbol

def normalize_market_symbol(asset: str, quote_currency: str = "USDT") -> str:
    """Return a Binance market symbol from either a base asset or full pair."""
    quote = normalize_symbol(quote_currency)
    symbol = normalize_symbol(asset)
    if symbol.endswith(quote):
        return symbol
    return normalize_symbol(f"{symbol}{quote}")
