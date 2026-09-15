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
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
# local imports
from dateutil.relativedelta import relativedelta
from .binance_rest_client import BinanceRestClient
from .total_dataset_loader import TotalDatasetLoader, total_dataset_filename


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

        print("[Info] ----> Start with market-chosen: " + self.MARKET)
        self.WORK = dir_path
        self.MONTHS = self._resolve_kline_dir(dir_path, "monthly")
        self.DAYS = self._resolve_kline_dir(dir_path, "daily")

        print(f"[Info] ---> Base Directory: {dir_path}")
        print(f"[Info] ---> Months Directory: {self.MONTHS}")
        print(f"[Info] ---> Days Directory: {self.DAYS}")

    def build(self, open_candle: str | None = None) -> MakeTotalResult:
        """Build and return an explicit result for the newly generated total dataset."""
        is_recent, most_recent_date = self.check_recent_spotmarket_files(self.MONTHS, self.DAYS, self.MARKET)

        if not is_recent and not self.force_merge:
            msg = f"Historical data for {self.MARKET} is missing or older than 20 days (latest: {most_recent_date})."
            print(f"[Warning] {msg}")
            raise MakeTotalError(self.MARKET, msg)

        if not self.collect_data(open_candle=open_candle):
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
        path1 = os.path.join(base_dir, timeperiod, "klines", self.MARKET, self.data_frequency)
        if os.path.exists(path1):
            return path1
        path2 = os.path.join(base_dir, "spot", timeperiod, "klines", self.MARKET, self.data_frequency)
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

    def resolve_open_candle_policy(self, open_candle: str | None = None) -> str:
        policy = open_candle if open_candle is not None else self.settings.get("open_candle", "exclude")
        policy = str(policy).strip().lower()
        if policy not in ("exclude", "include"):
            raise MakeTotalError(
                self.MARKET,
                f"Invalid open_candle policy '{policy}'. Use 'exclude' or 'include'.",
            )
        return policy

    def apply_open_candle_policy(self, df: pd.DataFrame, open_candle: str) -> pd.DataFrame:
        if open_candle == "include" or df.empty or "close_time" not in df.columns:
            return df

        close_time = pd.to_numeric(df["close_time"], errors="coerce")
        close_time_ms = close_time.where(close_time <= 1e14, close_time / 1e3)
        now_ms = datetime.now(timezone.utc).timestamp() * 1000
        closed_df = df[close_time_ms < now_ms].copy()
        removed_count = len(df) - len(closed_df)
        if removed_count:
            print(f"[Info] Excluded {removed_count} unfinished live candle(s)")
        return closed_df

    def collect_data(self, open_candle: str | None = None) -> bool:
        """Fetch the latest 500 hourly candles from Binance REST API."""
        policy = self.resolve_open_candle_policy(open_candle)
        try:
            df = self.recent_source.fetch_recent_klines(
                self.MARKET,
                columns=self.COLUMNS,
                interval=self.data_frequency,
                limit=500,
            )
        except ConnectionError as ex:
            print(f"[Warning] {ex}")
            return False
        except ValueError as ex:
            raise MakeTotalError(self.MARKET, str(ex)) from ex

        df = self.apply_open_candle_policy(df, policy)
        if df.empty:
            raise MakeTotalError(self.MARKET, "No closed live candles returned by Binance")

        now_str = datetime.now().strftime("%Y-%m-%d")
        self.current_data = os.path.join(self.current_dir, f"{self.MARKET}-{now_str}.csv")
        df.to_csv(self.current_data, index=False)
        print(f"[Info] Latest {len(df)} live {self.data_frequency} datapoints collected from Binance")
        return True

    def merge_data(self) -> tuple[str, int]:
        """Merge historical monthly, daily, and live hourly candles into a total CSV."""
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
            if df_total.empty:
                msg = "No rows with valid candle timestamps available to merge"
                print(f"[Warning] {msg}")
                raise MakeTotalError(self.MARKET, msg)

            filename = total_dataset_filename(
                self.MARKET,
                self.data_frequency,
                df_total['open_time'].iloc[0],
                df_total['open_time'].iloc[-1],
            )
            out_file = os.path.join(self.current_dir, filename)
            temporary = out_file + ".tmp"
            df_total.to_csv(temporary, index=False)
            os.replace(temporary, out_file)
            rows = len(df_total)
            print(f"[Info] Total dataset created: {rows:,} hourly rows")
            print(f"[Info] Saved to: {out_file}")
            return out_file, rows
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
