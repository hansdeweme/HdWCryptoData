"""Shared Binance symbol normalization and validation."""
# Copyright (c) 2024 Hans De Weme
# Licensed under the MIT License (https://opensource.org/licenses/MIT).
# part of the HdW_crypto_data Project
#
from __future__ import annotations
import re
from dataclasses import dataclass

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


@dataclass(frozen=True)
class MarketIdentity:
    provider: str
    product: str
    base: str
    quote: str
    interval: str


def resolve_market(asset, *, provider='binance', quote_currency='USDT', interval='1h'):
    """Explicit venue identity; existing Binance validators remain unchanged."""
    from .intervals import normalize_interval
    interval = normalize_interval(interval)
    if provider == 'binance':
        quote = normalize_symbol(quote_currency)
        product = normalize_market_symbol(asset, quote)
        return MarketIdentity(provider, product, product[:-len(quote)], quote, interval)
    if provider == 'coinbase':
        from .coinbase_client import product_id, INTERVAL_SECONDS
        product = product_id(asset)
        if interval not in INTERVAL_SECONDS:
            raise ValueError('Coinbase totals support 1h and 15m')
        base, quote = product.split('-')
        return MarketIdentity(provider, product, base, quote, interval)
    raise ValueError(f'Unsupported provider {provider!r}; use binance or coinbase')
