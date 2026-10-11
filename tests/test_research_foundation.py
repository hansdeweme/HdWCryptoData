"""Deterministic interval and research-loader regression tests."""
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

from hdw_crypto_data import TotalDatasetBuilder, TotalDatasetLoader
from hdw_crypto_data.total_dataset_builder import MakeTotalResult
from hdw_crypto_data.total_dataset_loader import DEFAULT_COLUMNS


@pytest.mark.parametrize("interval", ["15m", "1h", "1d"])
def test_interval_paths_rest_merge_and_discovery(tmp_path, interval):
    source = Mock()
    source.fetch_recent_klines.return_value = pd.DataFrame([
        [1791504000000, 2, 3, 1, 2, 10, 1791504899999, 20, 1, 5, 10, 0]
    ], columns=DEFAULT_COLUMNS)
    builder = TotalDatasetBuilder("BONK", {"full_spot": str(tmp_path)},
                                  force_merge=True, recent_source=source, interval=interval)
    builder.current_dir = str(tmp_path)
    assert builder.interval == interval
    assert Path(builder.MONTHS) == tmp_path / "monthly" / "klines" / "BONKUSDT" / interval
    assert Path(builder.DAYS) == tmp_path / "daily" / "klines" / "BONKUSDT" / interval
    assert builder.collect_data(open_candle="include")
    source.fetch_recent_klines.assert_called_once_with("BONKUSDT", columns=DEFAULT_COLUMNS,
                                                     interval=interval, limit=500)
    assert f"-{interval}-" in Path(builder.current_data).name
    path, rows = builder.merge_data()
    assert rows == 1
    assert Path(path).name.startswith(f"BONKUSDT-spot-{interval}-total-")
    loader = TotalDatasetLoader("BONK", {}, current_dir=str(tmp_path))
    assert len(loader.load_research_dataframe(interval=interval)) == 1
    assert loader.find_total_dataset_file(interval=interval) == path


def test_default_and_existing_settings_compatibility(tmp_path):
    builder = TotalDatasetBuilder("BONK", {"full_spot": str(tmp_path)})
    assert builder.interval == "1h"
    assert MakeTotalResult("BONKUSDT", "file.csv", 1, "built").interval == "1h"
    assert TotalDatasetBuilder("BONK", {"full_spot": str(tmp_path), "data_frequency": "15m"}).interval == "15m"
    assert TotalDatasetBuilder("BONK", {"full_spot": str(tmp_path), "data_frequency": "15m"},
                               interval="1d").interval == "1d"


@pytest.mark.parametrize("interval", ["15m", "1h", "1d"])
def test_strict_fallback_does_not_cross_intervals(tmp_path, interval):
    suffix = "" if interval == "1h" else f"-{interval}"
    frame = pd.DataFrame({"open_time": [1791504000000], "open": [2], "high": [3],
                          "low": [1], "close": [2], "volume": [10]})
    frame.to_csv(tmp_path / f"BONKUSDT{suffix}-total.csv", index=False)
    loader = TotalDatasetLoader("BONK", {}, current_dir=str(tmp_path))
    assert len(loader.load_research_dataframe(interval=interval)) == 1
    assert Path(loader.find_total_dataset_file(interval=interval)).name == f"BONKUSDT{suffix}-total.csv"
    other = "15m" if interval == "1h" else "1h"
    with pytest.raises(FileNotFoundError):
        loader.load_research_dataframe(interval=other)


@pytest.mark.parametrize("unit", ["ms", "us", "mixed"])
def test_research_preserves_gaps_duplicates_order_nan_and_has_no_side_files(tmp_path, unit):
    stamps = pd.to_datetime(["2026-10-01T03:00Z", "2026-10-01T00:00Z",
                             "2026-10-01T03:00Z", "2026-10-01T10:00Z"])
    millis = stamps.as_unit("ms").asi8
    times = millis if unit == "ms" else millis * 1000
    if unit == "mixed":
        times[1] = millis[1]
    frame = pd.DataFrame({"open_time": times, "open": [2] * 4, "high": [3] * 4,
                          "low": [1] * 4, "close": [2, np.nan, 2.5, 2], "volume": [10] * 4})
    path = tmp_path / "input.csv"
    frame.to_csv(path, index=False)
    loader = TotalDatasetLoader("BONK", {"preferred_time_zone": "Europe/Amsterdam"}, current_dir=str(tmp_path))
    result = loader.load_research_dataframe(path)
    pd.testing.assert_index_equal(result.index.as_unit("us"), stamps.rename("dt").as_unit("us"))
    assert result.index.has_duplicates
    assert not result.index.is_monotonic_increasing
    assert pd.isna(result.close.iloc[1])
    assert set(tmp_path.iterdir()) == {path}
    assert len(result) == 4


@pytest.mark.parametrize("interval", ["bad", "1M", "1m", "", None])
def test_invalid_intervals(tmp_path, interval):
    if interval is not None:
        with pytest.raises(ValueError, match="Unsupported interval"):
            TotalDatasetBuilder("BONK", {"full_spot": str(tmp_path)}, interval=interval)
    with pytest.raises(ValueError, match="Unsupported interval"):
        TotalDatasetLoader("BONK", {}).load_research_dataframe(interval=interval)


def test_build_result_provenance(tmp_path):
    builder = TotalDatasetBuilder("BONK", {"full_spot": str(tmp_path)}, interval="15m")
    builder.check_recent_spotmarket_files = Mock(return_value=(True, None))
    builder.collect_data = Mock(return_value=True)
    builder.merge_data = Mock(return_value=(str(tmp_path / "built.csv"), 4))
    assert builder.build().interval == "15m"


def test_microsecond_precision_and_invalid_timestamps_preserved(tmp_path):
    times = [1791504000123456, 1791504000654321, "bad"]
    path = tmp_path / "microseconds.csv"
    pd.DataFrame({"open_time": times, "close": [2, 3, 4]}).to_csv(path, index=False)
    result = TotalDatasetLoader("BONK", {}).load_research_dataframe(path)
    assert result.index[0] == pd.Timestamp(1791504000123456, unit="us", tz="UTC")
    assert result.index[1] == pd.Timestamp(1791504000654321, unit="us", tz="UTC")
    assert pd.isna(result.index[2])
    assert len(result) == 3
