# test_total_dataset_builder_collect_data.py
# Copyright (c) 2025, 2026 Hans De Weme
# Licensed under the MIT License (https://opensource.org/licenses/MIT).
# part of the HdW_crypto_data Project
#
import shutil
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
import pandas as pd
# local imports
from hdw_crypto_data.binance_rest_client import BinanceRestClient
from hdw_crypto_data.total_dataset_builder import MakeTotalError, TotalDatasetBuilder


class FakeKlineResponse:
    def __init__(self, payload):
        self.payload = payload
        self.raise_for_status_called = False

    def raise_for_status(self):
        self.raise_for_status_called = True

    def json(self):
        return self.payload


class TotalDatasetBuilderCollectDataTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path.cwd() / "tmp_collect_data_test"
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        self.temp_dir.mkdir()
        self.builder = TotalDatasetBuilder(
            "BONK",
            {
                "quote_currency": "USDT",
                "full_spot": str(self.temp_dir),
            },
            force_merge=True,
        )
        self.builder.current_dir = str(self.temp_dir)

        row = [
            1797897600000,
            "0.1",
            "0.2",
            "0.05",
            "0.15",
            "100",
            1797901199999,
            "15",
            10,
            "50",
            "7.5",
            "0",
        ]
        self.recent_rows = [row]

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_rest_client_uses_binance_params_and_checks_status(self):
        response = FakeKlineResponse(self.recent_rows)
        client = BinanceRestClient()

        with patch("hdw_crypto_data.binance_rest_client.requests.get", return_value=response) as mock_get:
            df = client.fetch_recent_klines("BONKUSDT", columns=self.builder.COLUMNS)

        self.assertTrue(response.raise_for_status_called)
        mock_get.assert_called_once_with(
            "https://api.binance.com/api/v3/klines",
            params={
                "symbol": "BONKUSDT",
                "interval": "1h",
                "limit": 500,
            },
            timeout=(10, 30),
        )
        self.assertEqual(len(df), 1)

    def test_collect_data_uses_recent_source(self):
        class FakeRecentSource:
            def __init__(self, rows):
                self.rows = rows
                self.calls = []

            def fetch_recent_klines(self, market, columns, interval="1h", limit=500):
                self.calls.append((market, columns, interval, limit))
                return pd.DataFrame(self.rows, columns=columns)

        recent_source = FakeRecentSource(self.recent_rows)
        self.builder.recent_source = recent_source

        self.assertTrue(self.builder.collect_data(open_candle="include"))

        self.assertEqual(recent_source.calls, [("BONKUSDT", self.builder.COLUMNS, "1h", 500)])
        self.assertTrue(Path(self.builder.current_data).exists())

    def test_collect_data_excludes_unfinished_candle_by_default(self):
        class FakeRecentSource:
            def __init__(self, rows):
                self.rows = rows

            def fetch_recent_klines(self, market, columns, interval="1h", limit=500):
                return pd.DataFrame(self.rows, columns=columns)

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        closed_row = list(self.recent_rows[0])
        closed_row[0] = now_ms - 7_200_000
        closed_row[6] = now_ms - 3_600_001
        open_row = list(self.recent_rows[0])
        open_row[0] = now_ms - 3_600_000
        open_row[6] = now_ms + 3_599_999
        self.builder.recent_source = FakeRecentSource([closed_row, open_row])

        self.assertTrue(self.builder.collect_data())

        df = pd.read_csv(self.builder.current_data)
        self.assertEqual(len(df), 1)
        self.assertEqual(int(df["open_time"].iloc[0]), closed_row[0])

    def test_collect_data_can_include_unfinished_candle(self):
        class FakeRecentSource:
            def __init__(self, rows):
                self.rows = rows

            def fetch_recent_klines(self, market, columns, interval="1h", limit=500):
                return pd.DataFrame(self.rows, columns=columns)

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        closed_row = list(self.recent_rows[0])
        closed_row[6] = now_ms - 3_600_001
        open_row = list(self.recent_rows[0])
        open_row[6] = now_ms + 3_599_999
        self.builder.recent_source = FakeRecentSource([closed_row, open_row])

        self.assertTrue(self.builder.collect_data(open_candle="include"))

        df = pd.read_csv(self.builder.current_data)
        self.assertEqual(len(df), 2)

    def test_collect_data_rejects_invalid_open_candle_policy(self):
        with self.assertRaisesRegex(MakeTotalError, "Invalid open_candle policy"):
            self.builder.collect_data(open_candle="maybe")

    def test_collect_data_rejects_empty_response(self):
        self.builder.recent_source = BinanceRestClient()
        response = FakeKlineResponse([])

        with patch("hdw_crypto_data.binance_rest_client.requests.get", return_value=response):
            with self.assertRaisesRegex(MakeTotalError, "Binance returned no live candles"):
                self.builder.collect_data()

    def test_collect_data_rejects_unexpected_response_structure(self):
        self.builder.recent_source = BinanceRestClient()
        response = FakeKlineResponse({"code": -1121, "msg": "Invalid symbol."})

        with patch("hdw_crypto_data.binance_rest_client.requests.get", return_value=response):
            with self.assertRaisesRegex(MakeTotalError, "Unexpected Binance kline response structure"):
                self.builder.collect_data()

    def test_merge_data_uses_isolated_temporary_directory(self):
        self.builder.MONTHS = str(self.temp_dir / "monthly" / "klines" / "BONKUSDT" / "1h")
        self.builder.DAYS = str(self.temp_dir / "daily" / "klines" / "BONKUSDT" / "1h")
        Path(self.builder.MONTHS).mkdir(parents=True)

        row = [
            "1797897600000",
            "0.1",
            "0.2",
            "0.05",
            "0.15",
            "100",
            "1797901199999",
            "15",
            "10",
            "50",
            "7.5",
            "0",
        ]
        historical_file = Path(self.builder.MONTHS) / "BONKUSDT-1h-2026-08.csv"
        historical_file.write_text(",".join(row), encoding="utf-8")

        out_file, rows = self.builder.merge_data()

        self.assertEqual(rows, 1)
        self.assertEqual(
            Path(out_file).name,
            "BONKUSDT-spot-1h-total-2026-12-22T00-00-00Z--2026-12-22T00-00-00Z.csv",
        )
        self.assertTrue(Path(out_file).exists())
        self.assertFalse((self.temp_dir / "_temp_merge").exists())
        self.assertEqual(list(self.temp_dir.glob("hdw-BONKUSDT-*")), [])

    def test_merge_data_preserves_existing_output_when_temporary_write_fails(self):
        self.builder.MONTHS = str(self.temp_dir / "monthly" / "klines" / "BONKUSDT" / "1h")
        self.builder.DAYS = str(self.temp_dir / "daily" / "klines" / "BONKUSDT" / "1h")
        Path(self.builder.MONTHS).mkdir(parents=True)
        historical_file = Path(self.builder.MONTHS) / "BONKUSDT-1h-2026-08.csv"
        historical_file.write_text(
            "1797897600000,0.1,0.2,0.05,0.15,100,1797901199999,15,10,50,7.5,0",
            encoding="utf-8",
        )
        output_file = self.temp_dir / "BONKUSDT-spot-1h-total-2026-12-22T00-00-00Z--2026-12-22T00-00-00Z.csv"
        output_file.write_text("existing output", encoding="utf-8")

        with (
            patch("pandas.DataFrame.to_csv", side_effect=RuntimeError("write failed")),
            patch("hdw_crypto_data.total_dataset_builder.os.replace") as mock_replace,
        ):
            with self.assertRaisesRegex(RuntimeError, "write failed"):
                self.builder.merge_data()

        mock_replace.assert_not_called()
        self.assertEqual(output_file.read_text(encoding="utf-8"), "existing output")


if __name__ == "__main__":
    unittest.main()
