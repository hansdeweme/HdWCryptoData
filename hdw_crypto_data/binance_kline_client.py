"""Recent Binance Spot candles as validated, in-memory UTC data."""
# Copyright (c) 2026 Hans De Weme. Licensed under the MIT License.
from __future__ import annotations

import math
from numbers import Integral
import threading
import time

import numpy as np
import pandas as pd
import requests

from .symbols import normalize_symbol

KLINE_INTERVALS = frozenset({
    '1s', '1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h',
    '6h', '8h', '12h', '1d', '3d', '1w', '1M',
})
_COLUMNS = ['open', 'high', 'low', 'close', 'volume', 'number_of_trades']


class BinanceKlineError(ConnectionError):
    """A market-data request failed after the applicable retry policy."""


class BinanceKlineResponseError(ValueError):
    """Binance returned malformed or internally inconsistent candle data."""


class BinanceRateLimitError(BinanceKlineError):
    """Binance throttled/banned requests; retry_after is the remaining cooldown."""

    def __init__(self, message, *, retry_after=None, status_code=None):
        super().__init__(message)
        self.retry_after = retry_after
        self.status_code = status_code


class BinanceKlineClient:
    """Fetch Spot klines without archive builders, indicators or file I/O.

    Calls on one instance are serialized and paced. Pacing is per instance,
    not a shared-IP rate-limit manager. Reuse an instance or use it as a
    context manager. An injected requests-compatible session remains caller-owned.
    """

    def __init__(self, *, session=None, timeout=(5, 15), max_attempts=3,
                 min_request_interval=0.25, backoff_factor=0.5,
                 max_retry_wait=30.0):
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, Integral) or max_attempts < 1:
            raise ValueError('max_attempts must be a positive integer')
        for name, value in [('min_request_interval', min_request_interval),
                            ('backoff_factor', backoff_factor), ('max_retry_wait', max_retry_wait)]:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f'{name} must be a finite nonnegative number')
        timeouts = timeout if isinstance(timeout, tuple) else (timeout,)
        if (isinstance(timeout, tuple) and len(timeout) != 2) or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0
            for v in timeouts
        ):
            raise ValueError('timeout must be positive seconds or a (connect, read) tuple')
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.min_request_interval = min_request_interval
        self.backoff_factor = backoff_factor
        self.max_retry_wait = max_retry_wait
        self._session = session if session is not None else requests.Session()
        self._owns_session = session is None
        self._last_start = None
        self._blocked_until = 0.0
        self._closed = False
        self._lock = threading.Lock()

    def close(self):
        with self._lock:
            if not self._closed and self._owns_session:
                self._session.close()
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _pace(self):
        now = time.monotonic()
        cooldown = max(0., self._blocked_until - now)
        if cooldown > self.max_retry_wait:
            raise BinanceRateLimitError('Binance cooldown is still active', retry_after=cooldown)
        pacing = 0. if self._last_start is None else self.min_request_interval - (now - self._last_start)
        delay = max(cooldown, pacing, 0.)
        if delay:
            time.sleep(delay)
        self._last_start = time.monotonic()

    def _request(self, params):
        for attempt in range(self.max_attempts):
            self._pace()
            try:
                response = self._session.get('https://api.binance.com/api/v3/klines',
                                             params=params, timeout=self.timeout)
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt + 1 == self.max_attempts:
                    raise BinanceKlineError('Binance kline transport failed') from exc
                self._backoff(attempt)
                continue
            except requests.RequestException as exc:
                raise BinanceKlineError('Binance kline request failed') from exc
            try:
                status = response.status_code
                if status in (418, 429):
                    try:
                        wait = float(response.headers.get('Retry-After', 60))
                        if not math.isfinite(wait) or wait < 0:
                            raise ValueError()
                    except (TypeError, ValueError):
                        wait = 60.0
                    self._blocked_until = max(self._blocked_until, time.monotonic() + wait)
                    error = BinanceRateLimitError(f'Binance returned HTTP {status}',
                                                  retry_after=wait, status_code=status)
                    if status == 418 or wait > self.max_retry_wait or attempt + 1 == self.max_attempts:
                        raise error
                    continue  # _pace respects the complete Retry-After value.
                if 500 <= status < 600:
                    if attempt + 1 == self.max_attempts:
                        raise BinanceKlineError(f'Binance returned HTTP {status}')
                else:
                    try:
                        response.raise_for_status()
                    except requests.HTTPError as exc:
                        raise BinanceKlineError(f'Binance returned HTTP {status}') from exc
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise BinanceKlineResponseError('Binance returned invalid JSON') from exc
            finally:
                response.close()
            self._backoff(attempt)
        raise BinanceKlineError('Binance request attempts exhausted')

    def _backoff(self, attempt):
        delay = min(self.max_retry_wait, self.backoff_factor * 2 ** attempt)
        if delay:
            time.sleep(delay)

    @staticmethod
    def _parse(payload):
        if not isinstance(payload, list) or any(not isinstance(row, list) or len(row) != 12 for row in payload):
            raise BinanceKlineResponseError('Expected a list of 12-field Binance klines')
        if not payload:
            frame = pd.DataFrame({c: pd.Series(dtype='int64' if c == 'number_of_trades' else 'float64')
                                  for c in _COLUMNS})
            frame.index = pd.DatetimeIndex([], tz='UTC', name='dt')
            frame['_close_time'] = pd.Series(index=frame.index, dtype='int64')
            return frame
        raw = pd.DataFrame(payload)
        numeric_columns = [0, 1, 2, 3, 4, 5, 6, 8]
        try:
            for column in numeric_columns:
                if raw[column].map(lambda v: isinstance(v, (bool, np.bool_))).any():
                    raise ValueError('Boolean candle value')
                raw[column] = pd.to_numeric(raw[column], errors='raise')
            if not np.isfinite(raw[numeric_columns].to_numpy(dtype=float)).all():
                raise ValueError('Nonfinite candle value')
            for column in (0, 6, 8):
                if (raw[column] < 0).any() or (raw[column] > np.iinfo(np.int64).max).any() or (raw[column] % 1 != 0).any():
                    raise ValueError('Invalid integer candle value')
                raw[column] = raw[column].astype('int64')
                if (raw[column] < 0).any():
                    raise ValueError('Integer candle value overflow')
            index = pd.DatetimeIndex(pd.to_datetime(raw[0], unit='ms', utc=True), name='dt')
            pd.to_datetime(raw[6], unit='ms', utc=True)  # Validate close-time bounds too.
            if (raw[6] < raw[0]).any() or (raw[[1, 2, 3, 4]] <= 0).any().any() or (raw[5] < 0).any():
                raise ValueError('Invalid price, volume or candle bounds')
            if (raw[2] < raw[[1, 3, 4]].max(axis=1)).any() or (raw[3] > raw[[1, 2, 4]].min(axis=1)).any():
                raise ValueError('Inconsistent OHLC bounds')
        except (ValueError, TypeError, OverflowError) as exc:
            raise BinanceKlineResponseError(f'Invalid Binance candle values: {exc}') from exc
        frame = raw[[1, 2, 3, 4, 5, 8, 6]].copy()
        frame.columns = _COLUMNS + ['_close_time']
        frame.index = index
        frame[_COLUMNS[:-1]] = frame[_COLUMNS[:-1]].astype('float64')
        # Identical duplicate candles are harmless; conflicting snapshots are not.
        if frame.index.has_duplicates:
            unique = frame.reset_index().drop_duplicates()
            if unique['dt'].duplicated().any():
                raise BinanceKlineResponseError('Conflicting duplicate candle timestamps')
            frame = unique.set_index('dt')
        return frame.sort_index()

    def get_klines(self, symbol: str, *, interval: str = '1h', limit: int = 500,
                   closed_only: bool = True) -> pd.DataFrame:
        """Return up to limit candles, sorted by UTC open time, with six numeric columns.

        By default only close times strictly before the pre-request UTC cutoff
        are eligible. closed_only=False explicitly includes the current candle.
        A short/empty listing returns fewer rows; malformed responses raise.
        """
        if not isinstance(symbol, str):
            raise ValueError('symbol must be a full Binance Spot pair string')
        symbol = normalize_symbol(symbol)
        if not isinstance(interval, str) or interval not in KLINE_INTERVALS:
            raise ValueError(f'Unsupported Binance kline interval: {interval!r}')
        if isinstance(limit, bool) or not isinstance(limit, Integral) or not 1 <= limit <= 1000:
            raise ValueError('limit must be an integer from 1 to 1000')
        if not isinstance(closed_only, bool):
            raise ValueError('closed_only must be boolean')
        with self._lock:
            if self._closed:
                raise RuntimeError('BinanceKlineClient is closed')
            cutoff = time.time_ns() // 1_000_000
            end_time = cutoff - 1 if closed_only else None
            pages = []
            count = 0
            while count < limit:
                requested = min(1000, limit - count + int(closed_only))
                params = {'symbol': symbol, 'interval': interval, 'limit': requested}
                if end_time is not None:
                    params['endTime'] = end_time
                payload = self._request(params)
                page = self._parse(payload)
                if len(payload) > requested:
                    raise BinanceKlineResponseError('Binance returned more candles than requested')
                if page.empty:
                    break
                # Timestamp.value is always nanoseconds; DatetimeIndex.asi8
                # follows its resolution (which differs across pandas versions).
                opens = np.array([stamp.value // 1_000_000 for stamp in page.index], dtype='int64')
                if end_time is not None and (opens > end_time).any():
                    raise BinanceKlineResponseError('Binance returned candles after requested endTime')
                eligible = page.loc[page['_close_time'] < cutoff] if closed_only else page
                pages.append(eligible)
                count += len(eligible)
                if count >= limit or len(payload) < requested:
                    break
                end_time = int(opens.min()) - 1
            result = (pd.concat(pages).sort_index().tail(limit) if pages else self._parse([]))
            result = result.drop(columns=['_close_time'])
            result.attrs.update(symbol=symbol, interval=interval, closed_only=closed_only,
                                retrieval_cutoff_ms=cutoff)
            return result
