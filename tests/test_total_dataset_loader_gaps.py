# test_total_dataset_loader_gasp.py
# Copyright (c) 2025, 2026 Hans De Weme
# Licensed under the MIT License (https://opensource.org/licenses/MIT).
# part of the HdW_crypto_data Project
#
import os
import shutil
import unittest
from pathlib import Path
import pandas as pd
# local imports
from hdw_crypto_data.total_dataset_loader import DEFAULT_COLUMNS, TotalDatasetLoader


class TotalDatasetLoaderGapTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path.cwd() / "tmp_loader_gap_test"
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        self.temp_dir.mkdir()
        self.csv_path = self.temp_dir / "BONKUSDT-total.csv"

        rows = [
            [
                "1797897600000",
                "0.1",
                "0.2",
                "0.05",
                "0.15",
                "100",
                "1797901199999",
                "15",
                "1",
                "50",
                "7.5",
                "0",
            ],
            [
                "1797904800000",
                "0.3",
                "0.4",
                "0.25",
                "0.35",
                "200",
                "1797908399999",
                "70",
                "4",
                "80",
                "28",
                "0",
            ],
        ]
        self.csv_path.write_text(
            ",".join(DEFAULT_COLUMNS) + "\n" + "\n".join(",".join(row) for row in rows),
            encoding="utf-8",
        )
        self.loader = TotalDatasetLoader(
            "BONKUSDT",
            {"quote_currency": "USDT", "preferred_time_zone": "UTC"},
            current_dir=str(self.temp_dir),
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_default_cleaning_does_not_synthesize_missing_candles(self):
        df = self.loader.load_total_dataframe(file_path=str(self.csv_path), mode="ta")

        self.assertEqual(len(df), 2)
        self.assertNotIn("is_imputed", df.columns)

    def test_default_loader_finds_self_documenting_total_csv(self):
        new_csv_path = self.temp_dir / (
            "BONKUSDT-spot-1h-total-2026-12-22T00-00-00Z--2026-12-22T02-00-00Z.csv"
        )
        self.csv_path.replace(new_csv_path)

        df = self.loader.load_total_dataframe(mode="ta")

        self.assertEqual(len(df), 2)

    def test_discovery_selects_newest_file_only_within_configured_frequency(self):
        paths = {}
        for frequency, day, modified in (("1h", "21", 100), ("1h", "22", 200), ("1d", "23", 300)):
            path = self.temp_dir / (
                f"BONKUSDT-spot-{frequency}-total-2026-12-{day}T00-00-00Z--2026-12-{day}T02-00-00Z.csv"
            )
            path.write_bytes(self.csv_path.read_bytes())
            os.utime(path, (modified, modified))
            paths[frequency] = path

        self.assertEqual(self.loader.find_total_dataset_file(), str(paths["1h"]))
        daily_loader = TotalDatasetLoader("BONK", {"data_frequency": "1d"}, current_dir=str(self.temp_dir))
        self.assertEqual(daily_loader.find_total_dataset_file(), str(paths["1d"]))

    def test_discovery_falls_back_to_legacy_when_only_other_frequency_exists(self):
        daily = self.temp_dir / "BONKUSDT-spot-1d-total-2026-12-22T00-00-00Z--2026-12-23T00-00-00Z.csv"
        daily.write_bytes(self.csv_path.read_bytes())
        self.assertEqual(self.loader.find_total_dataset_file(), str(self.csv_path))

    def test_fill_gaps_marks_imputed_rows_and_keeps_trade_counts_integral(self):
        df = self.loader.load_total_dataframe(
            file_path=str(self.csv_path),
            mode="ta",
            fill_gaps=True,
        )

        self.assertEqual(len(df), 3)
        self.assertIn("is_imputed", df.columns)
        self.assertEqual(df["is_imputed"].tolist(), [False, True, False])
        self.assertEqual(str(df["number_of_trades"].dtype), "Int64")
        self.assertEqual(df["number_of_trades"].tolist(), [1, 2, 4])

    def test_mixed_millisecond_and_microsecond_timestamps_are_normalized(self):
        microsecond_csv = self.temp_dir / "BONKUSDT-mixed-time-total.csv"
        rows = [
            [
                "1797897600000",
                "0.1",
                "0.2",
                "0.05",
                "0.15",
                "100",
                "1797901199999",
                "15",
                "1",
                "50",
                "7.5",
                "0",
            ],
            [
                "1797901200000000",
                "0.2",
                "0.3",
                "0.15",
                "0.25",
                "150",
                "1797904799999999",
                "37.5",
                "2",
                "60",
                "15",
                "0",
            ],
        ]
        microsecond_csv.write_text(
            ",".join(DEFAULT_COLUMNS) + "\n" + "\n".join(",".join(row) for row in rows),
            encoding="utf-8",
        )

        df = self.loader.load_total_dataframe(file_path=str(microsecond_csv), mode="ta")

        self.assertEqual(df.index[0], df.index[1] - pd.Timedelta(hours=1))
        self.assertEqual(df.index[0], pd.Timestamp("2026-12-22 00:00:00", tz="UTC"))


if __name__ == "__main__":
    unittest.main()
