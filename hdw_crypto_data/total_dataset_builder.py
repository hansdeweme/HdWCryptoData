# Copyright (c) 2024 Hans De Weme
# Licensed under the MIT License (https://opensource.org/licenses/MIT).
# part of the HdW_crypto_data Project
# Purpose: Build total dataset CSVs from historical Binance Vision data and live candles
#
"""Build total dataset CSVs from historical Binance Vision data and live candles."""

from __future__ import annotations
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import pandas as pd
# local imports
from dateutil.relativedelta import relativedelta
from .binance_rest_client import BinanceRestClient
from .total_dataset_loader import TotalDatasetLoader


@dataclass(frozen=True)
class MakeTotalResult:
    market: str
    filepath: str
    rows: int
    dataframe_message: str

class MakeTotalError(RuntimeError):
    """Raised when total dataset creation cannot complete."""

    def __init__(self, market: str, message: str) -> None:
        self.market = market
        super().__init__(message)

class TotalDatasetBuilder(TotalDatasetLoader):
    """Fetch live candles and merge them with historical Binance Vision CSVs."""

    def __init__(
        self,
        asset,
        settings,
        force_merge=False,
        historical_source=None,
        recent_source: BinanceRestClient | None = None,
    ):
        super().__init__(asset, settings)
        self.force_merge = force_merge
        self.historical_source = historical_source
        self.recent_source = recent_source or BinanceRestClient()

        spot_path = Path(self.settings.get("full_spot", "..\\spot"))
        dir_path = str(spot_path.resolve())

        print("[Info] Start with market-chosen: " + self.MARKET)
        self.WORK = dir_path
        self.MONTHS = self._resolve_kline_dir(dir_path, "monthly")
        self.DAYS = self._resolve_kline_dir(dir_path, "daily")

        print(f"[Info] ---> Base Directory: {dir_path}")
        print(f"[Info] ---> Months Directory: {self.MONTHS}")
        print(f"[Info] ---> Days Directory: {self.DAYS}")

    def build(self) -> MakeTotalResult:
        """Build and return an explicit result for the newly generated total dataset."""
        is_recent, most_recent_date = self.check_recent_spotmarket_files(self.MONTHS, self.DAYS, self.MARKET)

        if not is_recent and not self.force_merge:
            msg = f"Historical data for {self.MARKET} is missing or older than 20 days (latest: {most_recent_date})."
            print(f"[Warning] {msg}")
            raise MakeTotalError(self.MARKET, msg)

        if not self.collect_data():
            msg = f"Failed to collect live Binance data for {self.MARKET}."
            raise MakeTotalError(self.MARKET, msg)

        filepath, rows = self.merge_data()
        return MakeTotalResult(
            market=self.MARKET,
            filepath=filepath,
            rows=rows,
            dataframe_message=f"Total dataset created ({rows:,} rows)",
        )

    def _resolve_kline_dir(self, base_dir: str, timeperiod: str) -> str:
        """Find the correct kline directory whether stored in base/ or base/spot/."""
        path1 = os.path.join(base_dir, timeperiod, "klines", self.MARKET, "1h")
        if os.path.exists(path1):
            return path1
        path2 = os.path.join(base_dir, "spot", timeperiod, "klines", self.MARKET, "1h")
        if os.path.exists(path2):
            return path2
        return path1

    def check_recent_spotmarket_files(self, months_dir: str, days_dir: str, spotmarket: str):
        """Check if historical data exists and is reasonably up to date."""
        current_date = datetime.now()
        month_pattern = re.compile(rf'{spotmarket}-\d+[a-zA-Z]+-(\d{{4}}-\d{{2}})\.csv')
        day_pattern = re.compile(rf'{spotmarket}-\d+[a-zA-Z]+-(\d{{4}}-\d{{2}}-\d{{2}})\.csv')
        valid_dates = []

        if os.path.exists(days_dir):
            for file in Path(days_dir).iterdir():
                if file.is_file():
                    match = day_pattern.search(file.name)
                    if match:
                        try:
                            valid_dates.append(datetime.strptime(match.group(1), '%Y-%m-%d'))
                        except ValueError:
                            pass

        if os.path.exists(months_dir):
            for file in Path(months_dir).iterdir():
                if file.is_file():
                    match = month_pattern.search(file.name)
                    if match:
                        try:
                            month_start = datetime.strptime(match.group(1), '%Y-%m')
                            valid_dates.append(month_start + relativedelta(months=1, days=-1))
                        except ValueError:
                            pass

        if not valid_dates:
            print(f"\nNo historical data files found for: {spotmarket}")
            return False, None

        most_recent_date = max(valid_dates)
        diff_days = (current_date - most_recent_date).days
        print(f"[Info] Most recent historical data for {spotmarket} is from: {most_recent_date.date()} ({diff_days} days ago)")

        if diff_days <= 20:
            print(f"[Info] Historical data is up-to-date (within {diff_days} days of today).")
            return True, most_recent_date.date()

        print(f"[Info] Historical data is {diff_days} days old (> 20 days).")
        return False, most_recent_date.date()

    def collect_data(self) -> bool:
        """Fetch the latest 500 hourly candles from Binance REST API."""
        try:
            df = self.recent_source.fetch_recent_klines(
                self.MARKET,
                columns=self.COLUMNS,
                interval="1h",
                limit=500,
            )
        except ConnectionError as ex:
            print(f"[Warning] {ex}")
            return False
        except ValueError as ex:
            raise MakeTotalError(self.MARKET, str(ex)) from ex

        now_str = datetime.now().strftime("%Y-%m-%d")
        self.current_data = os.path.join(self.current_dir, f"{self.MARKET}-{now_str}.csv")
        df.to_csv(self.current_data, index=False)
        print(f"[Info] Latest {len(df)} live hourly datapoints collected from Binance")
        return True

    def merge_data(self) -> tuple[str, int]:
        """Merge historical monthly, daily, and live hourly candles into {MARKET}-total.csv."""
        os.makedirs(self.WORK, exist_ok=True)
        work_dir = os.path.join(self.WORK, f"hdw-{self.MARKET}-{uuid.uuid4().hex[:8]}")
        os.makedirs(work_dir, exist_ok=False)
        try:
            if hasattr(self, 'current_data') and os.path.exists(self.current_data):
                shutil.move(self.current_data, os.path.join(work_dir, os.path.basename(self.current_data)))

            monthly_count = 0
            if os.path.exists(self.MONTHS):
                for file_name in os.listdir(self.MONTHS):
                    if file_name.startswith(self.MARKET) and file_name.endswith('.csv'):
                        monthly_count += 1
                        shutil.copy(os.path.join(self.MONTHS, file_name), os.path.join(work_dir, file_name))
            print(f"[Info] Copied {monthly_count} monthly files")

            daily_count = 0
            if os.path.exists(self.DAYS):
                for file_name in os.listdir(self.DAYS):
                    if file_name.startswith(self.MARKET) and file_name.endswith('.csv'):
                        daily_count += 1
                        shutil.copy(os.path.join(self.DAYS, file_name), os.path.join(work_dir, file_name))
            print(f"[Info] Copied {daily_count} daily files")

            file_list = [os.path.join(work_dir, f) for f in os.listdir(work_dir) if f.endswith('.csv')]
            print(f"[Info] Total files to merge: {len(file_list)}")

            if not file_list:
                msg = "No CSV files available to merge!"
                print(f"[Warning] {msg}")
                raise MakeTotalError(self.MARKET, msg)

            df_list = []
            for file in file_list:
                try:
                    df_temp = pd.read_csv(file, header=None)
                    if df_temp.empty:
                        continue
                    if str(df_temp.iloc[0, 0]).strip().lower() == 'open_time':
                        df_temp = df_temp.iloc[1:]

                    if len(df_temp.columns) >= len(self.COLUMNS):
                        df_temp = df_temp.iloc[:, :len(self.COLUMNS)]
                        df_temp.columns = self.COLUMNS
                        df_list.append(df_temp)
                    else:
                        df_temp.columns = self.COLUMNS[:len(df_temp.columns)]
                        df_list.append(df_temp)
                except Exception as ex:
                    print(f"[Warning] Failed reading {file}: {ex}")

            if not df_list:
                msg = "No readable CSV data available to merge"
                print(f"[Warning] {msg}")
                raise MakeTotalError(self.MARKET, msg)

            df_total = pd.concat(df_list, ignore_index=True)
            df_total = df_total.drop_duplicates(subset=['open_time'])
            df_total['open_time'] = pd.to_numeric(df_total['open_time'], errors='coerce')
            df_total = df_total.dropna(subset=['open_time']).sort_values('open_time').reset_index(drop=True)

            out_file = os.path.join(self.current_dir, f"{self.MARKET}-total.csv")
            temporary = out_file + ".tmp"
            df_total.to_csv(temporary, index=False)
            os.replace(temporary, out_file)
            rows = len(df_total)
            print(f"[Info] Total dataset created: {rows:,} hourly rows")
            print(f"[Info] Saved to: {out_file}")
            return out_file, rows
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
