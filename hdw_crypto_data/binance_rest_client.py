"""Binance REST API client helpers."""
# Copyright (c) 2026 Hans De Weme
# Licensed under the MIT License (https://opensource.org/licenses/MIT).
# part of the HdW_crypto_data Project
#
from __future__ import annotations
from dataclasses import dataclass
import pandas as pd
import requests

@dataclass
class BinanceRestClient:
    """Retrieve recent market data from the Binance REST API."""

    base_url: str = "https://api.binance.com/api/v3"
    timeout: tuple[int, int] = (10, 30)

    def fetch_recent_klines(
        self,
        market: str,
        columns: list[str],
        interval: str = "1h",
        limit: int = 500,
    ) -> pd.DataFrame:
        """Fetch recent kline rows and return them as a DataFrame."""
        url = f"{self.base_url}/klines"
        try:
            response = requests.get(
                url,
                params={
                    "symbol": market,
                    "interval": interval,
                    "limit": limit,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as ex:
            raise ConnectionError(f"Failed to obtain latest data from Binance: {ex}") from ex

        if not data:
            raise ValueError("Binance returned no live candles")
        if not isinstance(data, list) or not all(isinstance(row, list) and len(row) >= len(columns) for row in data):
            raise ValueError("Unexpected Binance kline response structure")

        return pd.DataFrame([row[:len(columns)] for row in data], columns=columns)
