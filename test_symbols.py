# test_symbols.py
# Copyright (c) 2025, 2026 Hans De Weme
# Licensed under the MIT License (https://opensource.org/licenses/MIT).
# part of the HdW_crypto_data Project

import unittest
# local imports
from hdw_crypto_data.symbols import normalize_market_symbol, normalize_symbol
from hdw_crypto_data.total_dataset_loader import TotalDatasetLoader


class MarketSymbolNormalizationTests(unittest.TestCase):
    def test_appends_quote_currency_to_base_asset(self):
        self.assertEqual(normalize_market_symbol("bonk", "usdt"), "BONKUSDT")

    def test_keeps_existing_quote_currency(self):
        self.assertEqual(normalize_market_symbol("bonkusdt", "usdt"), "BONKUSDT")

    def test_total_dataset_loader_accepts_base_asset_or_market_symbol(self):
        settings = {"quote_currency": "USDT"}

        self.assertEqual(TotalDatasetLoader("BONK", settings).MARKET, "BONKUSDT")
        self.assertEqual(TotalDatasetLoader("BONKUSDT", settings).MARKET, "BONKUSDT")

    def test_rejects_invalid_symbol_characters(self):
        with self.assertRaises(ValueError):
            normalize_symbol("BONK/USDT")


if __name__ == "__main__":
    unittest.main()
