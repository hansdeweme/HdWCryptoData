"""Mixed Binance Vision/REST epoch units must merge as actual instants."""
import os
from pathlib import Path

import pandas as pd
import pytest

from hdw_crypto_data import TotalDatasetBuilder, TotalDatasetLoader
from hdw_crypto_data.total_dataset_loader import DEFAULT_COLUMNS


def candle(stamp, unit, close=2):
    micros = pd.Timestamp(stamp).value // 1000
    divisor = {'s': 1_000_000, 'ms': 1000, 'us': 1}[unit]
    return [micros // divisor, 2, 3, 1, close, 10,
            (micros + 3_600_000_000 - 1) // divisor, 20, 1, 5, 10, 0]


@pytest.mark.parametrize('interval', ['15m', '1h', '1d'])
@pytest.mark.parametrize('archive_unit', ['s', 'ms', 'us'])
def test_mixed_sources_deduplicate_and_sort_by_instant(tmp_path, monkeypatch, interval, archive_unit):
    builder = TotalDatasetBuilder('BONK', {'full_spot': str(tmp_path/'spot')}, interval=interval)
    builder.current_dir = str(tmp_path)
    monthly = Path(builder.MONTHS)
    daily = Path(builder.DAYS)
    monthly.mkdir(parents=True)
    daily.mkdir(parents=True)
    # Pin enumeration order to verify the existing first-source-wins policy.
    real_listdir = os.listdir
    monkeypatch.setattr(os, 'listdir', lambda path: sorted(real_listdir(path)))
    live = tmp_path/'000-live.csv'
    pd.DataFrame([candle('2026-10-20T00:00Z', 'ms', close=2.5)], columns=DEFAULT_COLUMNS).to_csv(live, index=False)
    builder.current_data = str(live)
    archive = monthly/f'BONKUSDT-{interval}-2026-10.csv'
    pd.DataFrame([
        candle('2026-10-20T00:00Z', archive_unit),
        candle('2025-01-01T00:00Z', archive_unit),
        candle('2026-10-21T00:00Z', 'us'),
    ], columns=DEFAULT_COLUMNS).to_csv(archive, index=False, header=False)
    # Same timestamp represented as strings in a header-bearing CSV.
    overlap = daily/f'BONKUSDT-{interval}-2026-10-20.csv'
    pd.DataFrame([candle('2026-10-20T00:00Z', 'us')], columns=DEFAULT_COLUMNS).to_csv(overlap, index=False)
    archive_bytes, overlap_bytes = archive.read_bytes(), overlap.read_bytes()

    path, rows = builder.merge_data()

    assert rows == 3
    assert Path(path).name == f'BONKUSDT-spot-{interval}-total-2025-01-01T00-00-00Z--2026-10-21T00-00-00Z.csv'
    result = TotalDatasetLoader('BONK', {}, current_dir=str(tmp_path)).load_research_dataframe(path, interval=interval)
    expected = pd.DatetimeIndex(pd.to_datetime(['2025-01-01T00:00Z', '2026-10-20T00:00Z', '2026-10-21T00:00Z']), name='dt')
    pd.testing.assert_index_equal(result.index, expected.as_unit('ns'))
    assert result.loc[pd.Timestamp('2026-10-20T00:00Z'), 'close'] == 2.5
    raw = pd.read_csv(path)
    assert raw.open_time.is_unique and raw.open_time.is_monotonic_increasing
    assert (raw.open_time > 10**14).all() and (raw.close_time > 10**14).all()
    assert archive.read_bytes() == archive_bytes and overlap.read_bytes() == overlap_bytes


def test_microsecond_precision_is_not_rounded_into_duplicates(tmp_path):
    builder = TotalDatasetBuilder('BONK', {'full_spot': str(tmp_path/'spot')})
    builder.current_dir = str(tmp_path)
    monthly = Path(builder.MONTHS)
    monthly.mkdir(parents=True)
    stamps = ['2026-10-01T00:00:00.123456Z', '2026-10-01T00:00:00.123457Z']
    rows = [candle(stamp, 'us') for stamp in stamps]
    pd.DataFrame(rows, columns=DEFAULT_COLUMNS).to_csv(monthly/'BONKUSDT-1h-2026-10.csv', header=False, index=False)
    path, count = builder.merge_data()
    raw = pd.read_csv(path)
    assert count == 2
    assert raw.open_time.tolist() == [row[0] for row in rows]
    assert raw.close_time.tolist() == [row[6] for row in rows]
