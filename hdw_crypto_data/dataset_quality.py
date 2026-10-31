"""Small dataset-boundary checks shared by Coinbase build and load paths."""
from __future__ import annotations
import numpy as np
import pandas as pd


def epoch_microseconds(values):
    raw = pd.to_numeric(values, errors='raise')
    if raw.isna().any() or not np.isfinite(raw.to_numpy(dtype=float)).all() or (raw % 1 != 0).any():
        raise ValueError('Candle timestamps must be finite integer epochs')
    raw = raw.astype('int64')
    return raw.where(raw > 10**14, raw.where(raw > 10**11, raw*1000)*1000)


def validate_ohlcv(frame, *, interval, as_of=None):
    """Normalize epochs to us; retain actual prices and missing auxiliary fields."""
    from .total_dataset_loader import DEFAULT_COLUMNS
    from .coinbase_client import INTERVAL_SECONDS, utc_time
    if not frame.columns.is_unique or not set(DEFAULT_COLUMNS[:6]).issubset(frame):
        raise ValueError('Expected unique open_time and OHLCV columns')
    work = frame.copy().reindex(columns=DEFAULT_COLUMNS)
    step = INTERVAL_SECONDS[interval]*1_000_000
    work['open_time'] = epoch_microseconds(work.open_time)
    if (work.open_time < 0).any() or (work.open_time % step != 0).any():
        raise ValueError('Candle timestamp is not aligned with the selected interval')
    if as_of is not None:
        cutoff = utc_time(as_of).value//1000
        work = work.loc[work.open_time+step <= cutoff].copy()
    for field in DEFAULT_COLUMNS[1:6]:
        work[field] = pd.to_numeric(work[field], errors='raise')
    if not np.isfinite(work[DEFAULT_COLUMNS[1:6]].to_numpy(dtype=float)).all():
        raise ValueError('OHLCV values must be finite')
    if (work[['open','high','low','close']] <= 0).any().any() or (work.volume < 0).any():
        raise ValueError('Prices must be positive and volume nonnegative')
    if (work.high < work[['open','low','close']].max(axis=1)).any() or (work.low > work[['open','close']].min(axis=1)).any():
        raise ValueError('Invalid OHLC price relationships')
    if work.close_time.notna().any():
        present = work.close_time.notna()
        work.loc[present, 'close_time'] = epoch_microseconds(work.loc[present, 'close_time'])
        if ((work.loc[present, 'close_time'] < work.loc[present, 'open_time']) |
                (work.loc[present, 'close_time'] >= work.loc[present, 'open_time']+step)).any():
            raise ValueError('close_time does not match the candle interval')
    work['close_time'] = work.open_time+step-1
    for field in DEFAULT_COLUMNS[7:]:
        if work[field].notna().any():
            raise ValueError('Coinbase auxiliary measurements must remain unavailable')
    return work


def merge_observations(sources, *, strict=True):
    """Sources are ordered highest-priority first; OHLCV conflicts are explicit."""
    frames = []
    for label, frame in sources:
        if len(frame):
            frame = frame.copy()
            frame['__source'] = label
            frames.append(frame)
    if not frames:
        raise ValueError('No observed candles available')
    work = pd.concat(frames, ignore_index=True)
    diagnostics = []
    duplicate = work[work.open_time.duplicated(keep=False)]
    for stamp, group in duplicate.groupby('open_time', sort=True):
        # Binance historically retained the selected source even when its
        # prices were malformed; diagnostics must not turn that into failure.
        values = group[['open','high','low','close','volume']].apply(
            pd.to_numeric, errors='raise' if strict else 'coerce')
        if len(values.drop_duplicates()) > 1:
            timestamp = pd.Timestamp(int(stamp), unit='us', tz='UTC').isoformat()
            detail = f'Conflicting OHLCV at {timestamp}: ' + ', '.join(group.__source.astype(str))
            if strict:
                raise ValueError(detail)
            diagnostics.append(detail + '; retained source according to recent > daily > monthly priority')
    result = work.drop_duplicates('open_time', keep='first').drop(columns='__source')
    return result.sort_values('open_time', kind='stable').reset_index(drop=True), diagnostics


def coverage(frame, *, interval, start=None, end=None):
    from .coinbase_client import INTERVAL_SECONDS, utc_time
    from .coinbase_archive_provisioner import _ranges
    step = pd.Timedelta(seconds=INTERVAL_SECONDS[interval])
    observed = pd.DatetimeIndex(pd.to_datetime(frame.open_time, unit='us', utc=True))
    if observed.empty:
        return {'observations': 0, 'missing_buckets': 0, 'missing_ranges': []}
    start = observed.min() if start is None else utc_time(start)
    end = observed.max()+step if end is None else utc_time(end).floor(step)
    missing = pd.date_range(start, end, freq=step, inclusive='left').difference(observed)
    return dict(observations=len(frame), first_observed=observed.min().isoformat(),
                last_observed=observed.max().isoformat(), requested_start=start.isoformat(),
                requested_end=end.isoformat(), missing_buckets=len(missing), missing_ranges=_ranges(missing, step))
