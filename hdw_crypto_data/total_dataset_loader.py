# Copyright (c) 2024 Hans De Weme
# Licensed under the MIT License (https://opensource.org/licenses/MIT).
# part of the HdW_crypto_data Project
# Purpose: Load, normalize, and clean generated total dataset CSV files
#
#
"""Load, normalize, and clean generated total dataset CSV files."""

from __future__ import annotations
import json
import os
import time
from pathlib import Path
import numpy as np
import pandas as pd
from .symbols import normalize_market_symbol

DEFAULT_COLUMNS = [
    'open_time', 'open', 'high', 'low', 'close', 'volume',
    'close_time', 'quote_asset_volume', 'number_of_trades',
    'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
]
TOTAL_DATASET_GLOB = "{market}-spot-{frequency}-total-*--*.csv"


def load_settings(settings) -> dict:
    if isinstance(settings, dict):
        return settings
    if isinstance(settings, (str, Path)) and os.path.isfile(settings):
        with open(settings, 'r') as f:
            return json.load(f)
    return {}

def format_total_dataset_datetime(value) -> str:
    """Format epoch-like candle timestamps as a filesystem-safe UTC datetime."""
    numeric_value = pd.to_numeric(value, errors='coerce')
    if pd.isna(numeric_value):
        raise ValueError(f"Invalid candle timestamp: {value}")
    if numeric_value > 1e14:
        seconds = numeric_value / 1e6
    elif numeric_value > 1e11:
        seconds = numeric_value / 1e3
    else:
        seconds = numeric_value
    timestamp = pd.to_datetime(seconds, unit='s', utc=True)
    return timestamp.strftime("%Y-%m-%dT%H-%M-%SZ")

def total_dataset_filename(market: str, frequency: str, start_time, end_time) -> str:
    start_text = format_total_dataset_datetime(start_time)
    end_text = format_total_dataset_datetime(end_time)
    return f"{market}-spot-{frequency}-total-{start_text}--{end_text}.csv"

class TotalDatasetLoader:
    """Load and clean previously generated total dataset CSV files."""

    def __init__(self, asset, settings, current_dir: str | None = None):
        self.settings = load_settings(settings)
        self.MARKET = normalize_market_symbol(
            asset,
            self.settings.get("quote_currency", "USDT"),
        )
        self.current_dir = current_dir or os.getcwd()
        self.COLUMNS = list(DEFAULT_COLUMNS)
        self.data_frequency = self.settings.get("data_frequency", "1h")

    def find_total_dataset_file(self) -> str:
        """Find the newest total CSV for this market and configured frequency."""
        current_path = Path(self.current_dir)
        pattern = TOTAL_DATASET_GLOB.format(market=self.MARKET, frequency=self.data_frequency)
        candidates = [path for path in current_path.glob(pattern) if path.is_file()]
        if candidates:
            return str(max(candidates, key=lambda path: path.stat().st_mtime))

        legacy_path = current_path / f"{self.MARKET}-total.csv"
        return str(legacy_path)

    def convert_dataframe_timezone(self, df: pd.DataFrame, preferred_tz: str = None) -> pd.DataFrame:
        if preferred_tz is None:
            preferred_tz = self.settings.get("preferred_time_zone", "UTC")
        df = df.copy()

        if not isinstance(df.index, pd.DatetimeIndex) and 'open_time' in df.columns:
            open_time_sec = pd.to_numeric(df['open_time'], errors='coerce') / 1000.0
            df.index = pd.to_datetime(open_time_sec, unit='s', utc=True)
            df.index.name = 'datetime'

        if isinstance(df.index, pd.DatetimeIndex):
            if df.index.tz is None:
                df.index = df.index.tz_localize('UTC')
            df.index = df.index.tz_convert(preferred_tz)
            return df
        raise ValueError("DataFrame index must be a DatetimeIndex or contain an 'open_time' column")

    def load_total_dataframe(
        self,
        file_path: str = None,
        mode: str = "ta",
        preferred_tz: str = None,
        set_datetime_index: bool = True,
        clean: bool = True,
        fill_gaps: bool = False,
        clean_and_fill: bool | None = None
    ) -> pd.DataFrame:
        """Load a total CSV and optionally clean or synthesize missing hourly candles."""
        if clean_and_fill is not None:
            clean = clean_and_fill
            fill_gaps = clean_and_fill

        if file_path is None:
            file_path = self.find_total_dataset_file()
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"Total dataset file not found: {file_path}")
        if preferred_tz is None:
            preferred_tz = self.settings.get("preferred_time_zone", "UTC")

        df = pd.read_csv(file_path)
        if list(df.columns) != self.COLUMNS and len(df.columns) == len(self.COLUMNS):
            df.columns = self.COLUMNS

        raw_time = pd.to_numeric(df['open_time'], errors='coerce')
        time_in_seconds = np.where(
            raw_time > 1e14, raw_time / 1e6,
            np.where(raw_time > 1e11, raw_time / 1e3, raw_time)
        )
        dt_series = pd.to_datetime(time_in_seconds, unit='s', utc=True)
        if preferred_tz.upper() != "UTC":
            dt_series = dt_series.tz_convert(preferred_tz)
        df['dt'] = dt_series

        mode_lower = str(mode).strip().lower()
        if mode_lower in ('strip', 'close'):
            drop_cols = [
                'open_time', 'open', 'high', 'low', 'volume', 'close_time',
                'quote_asset_volume', 'number_of_trades',
                'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
            ]
        elif mode_lower in ('none', 'full', 'raw'):
            drop_cols = ['ignore']
        else:
            drop_cols = [
                'open_time', 'close_time', 'quote_asset_volume',
                'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
            ]

        existing_drop = [col for col in drop_cols if col in df.columns]
        df.drop(columns=existing_drop, inplace=True)
        if fill_gaps:
            df = self.clean_and_fill_gaps(df, fill_method="spline", save_missing_csv=True)
        elif clean:
            df = self.clean_dataframe(df)
        elif set_datetime_index:
            df.set_index('dt', inplace=True)
        return df

    def clean_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """Clean rows and types without creating missing timestamps."""
        df = df.copy()

        if not isinstance(df.index, pd.DatetimeIndex) and 'dt' in df.columns:
            df.set_index('dt', inplace=True)

        df = df[~df.index.isna()]
        df = df[~df.index.duplicated(keep='first')]
        df = df.sort_index()
        if df.empty:
            return df

        for col in df.columns:
            if col == "is_imputed":
                continue
            try:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            except Exception:
                pass

        return df.dropna(how='all')

    def clean_and_fill_gaps(
        self,
        df: pd.DataFrame,
        fill_method: str = "spline",
        save_missing_csv: bool = True
    ) -> pd.DataFrame:
        df = self.clean_dataframe(df)
        if df.empty:
            return df

        observed_index = df.index
        full_range = pd.date_range(start=df.index.min(), end=df.index.max(), freq='h')
        df_full = df.reindex(full_range)
        df_full.index.name = 'dt'
        df_full["is_imputed"] = ~df_full.index.isin(observed_index)

        missing_times = df_full.index[df_full["is_imputed"]]
        missing_count = len(missing_times)

        if missing_count > 0:
            print(f"[Warning] Missing Data detected ({missing_count} missing data rows)")
            if missing_count <= 10:
                print(f"Missing timestamps: {list(missing_times.strftime('%Y-%m-%d %H:%M'))}")
            if save_missing_csv and missing_count > 3:
                missing_file = os.path.join(self.current_dir, f"{self.MARKET}_missingdata.csv")
                pd.Series(missing_times, name='missing_datetime').to_csv(missing_file, index=False)
                print(f"[Warning] Datetimes for {missing_count} missing values saved to: {missing_file}")
            try:
                if 'close' in df_full.columns:
                    if fill_method == 'spline':
                        df_full['close'] = df_full['close'].interpolate(method='spline', order=3)
                    else:
                        df_full['close'] = df_full['close'].interpolate(method=fill_method)
                    df_full['close'] = df_full['close'].bfill().ffill()

                for col in df_full.columns:
                    if col not in ('close', 'is_imputed') and pd.api.types.is_numeric_dtype(df_full[col]):
                        df_full[col] = df_full[col].interpolate(method='time').bfill().ffill()
                self._round_count_columns(df_full)

                print(f"[Info] Missing values filled in with {fill_method} interpolation")
            except Exception as ex:
                print(f"[Warning] {fill_method} interpolation failed ({ex}), falling back to time-linear interpolation.")
                fill_cols = [
                    col for col in df_full.columns
                    if col != "is_imputed" and pd.api.types.is_numeric_dtype(df_full[col])
                ]
                df_full[fill_cols] = df_full[fill_cols].interpolate(method='time').bfill().ffill()
                self._round_count_columns(df_full)
        else:
            print("[Info] No missing data detected (continuous index).")
        return df_full

    @staticmethod
    def _round_count_columns(df: pd.DataFrame) -> None:
        for col in ("number_of_trades",):
            if col not in df.columns:
                continue
            df[col] = df[col].round()
            try:
                df[col] = df[col].astype("Int64")
            except (TypeError, ValueError):
                pass

    def is_file_recent(self, file_path: str = None, max_age_hours: float = 2.0) -> bool:
        if file_path is None:
            file_path = self.find_total_dataset_file()
        if not os.path.isfile(file_path):
            return False
        try:
            file_mod_time = os.path.getmtime(file_path)
            current_time = time.time()
            time_diff_hours = (current_time - file_mod_time) / 3600.0
            return time_diff_hours <= max_age_hours
        except OSError:
            return False
