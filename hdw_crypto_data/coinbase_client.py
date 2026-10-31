"""Public Coinbase Exchange OHLCV, bounded UTC windows and no filesystem I/O.

Times are timezone-aware. Only whole candles contained in [start, end) and
completed by as_of are returned. open_time is Unix microseconds; volume is base
asset volume. iter_windows streams at most 300 candles at a time.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import re
import threading
import time

import pandas as pd
import requests

INTERVAL_SECONDS = {'1h': 3600, '15m': 900}
OHLCV_COLUMNS = ['open_time', 'open', 'high', 'low', 'close', 'volume']


class CoinbaseError(ConnectionError):
    """Transport/provider failure after bounded retries."""


class CoinbaseResponseError(ValueError):
    """Malformed or conflicting provider observations."""


def product_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Z0-9]{1,30}-[A-Z0-9]{1,15}', value):
        raise ValueError('Use an explicit uppercase Coinbase product such as FARTCOIN-USD')
    return value


def utc_time(value):
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError('Time must be an explicit timezone-aware timestamp')
    return stamp.tz_convert('UTC')


def candle_bounds(start, end, as_of, interval):
    if interval not in INTERVAL_SECONDS:
        raise ValueError('Coinbase history supports 1h and 15m')
    start, end, as_of = utc_time(start), utc_time(end), utc_time(as_of)
    if start >= end:
        raise ValueError('start must precede end')
    step = pd.Timedelta(seconds=INTERVAL_SECONDS[interval])
    return start.ceil(step), min(end, as_of).floor(step), step


def empty_candles():
    return pd.DataFrame({name: pd.Series(dtype='int64' if name == 'open_time' else 'float64')
                         for name in OHLCV_COLUMNS})


@dataclass(frozen=True)
class CandleWindow:
    start: pd.Timestamp
    end: pd.Timestamp
    frame: pd.DataFrame


class CoinbaseClient:
    def __init__(self, *, session=None, timeout=(5, 20), max_attempts=3,
                 min_request_interval=.15, backoff_factor=.6, max_retry_wait=30.,
                 sleep=time.sleep, monotonic=time.monotonic):
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
            raise ValueError('max_attempts must be a positive integer')
        for name, value in (('min_request_interval', min_request_interval),
                            ('backoff_factor', backoff_factor), ('max_retry_wait', max_retry_wait)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(name + ' must be finite and nonnegative')
        limits = timeout if isinstance(timeout, tuple) else (timeout,)
        if (isinstance(timeout, tuple) and len(timeout) != 2) or any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in limits):
            raise ValueError('timeout must contain positive seconds')
        self.session = session if session is not None else requests.Session()
        self._owns_session = session is None
        self.timeout, self.max_attempts = timeout, max_attempts
        self.pacing, self.backoff, self.max_retry_wait = min_request_interval, backoff_factor, max_retry_wait
        self.sleep, self.monotonic = sleep, monotonic
        self._last_start = None
        self._blocked_until = 0.
        self._lock = threading.Lock()
        self._closed = False

    def close(self):
        with self._lock:
            if not self._closed and self._owns_session:
                self.session.close()
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _request(self, path, params=None):
        # Serialize and pace all calls on this instance, including retries.
        with self._lock:
            if self._closed:
                raise RuntimeError('Coinbase client is closed')
            for attempt in range(self.max_attempts):
                now = self.monotonic()
                cooldown = self._blocked_until - now
                if cooldown > self.max_retry_wait:
                    raise CoinbaseError('Coinbase Retry-After cooldown still active; retry later')
                wait = max(0., cooldown, 0. if self._last_start is None else self.pacing - (now-self._last_start))
                if wait:
                    self.sleep(wait)
                self._last_start = self.monotonic()
                retry_wait = min(self.max_retry_wait, self.backoff * 2**attempt)
                try:
                    response = self.session.get('https://api.exchange.coinbase.com' + path,
                        params=params or {}, timeout=self.timeout,
                        headers={'Accept': 'application/json', 'User-Agent': 'hdw-crypto-data/coinbase'})
                except (requests.Timeout, requests.ConnectionError) as error:
                    if attempt+1 == self.max_attempts:
                        raise CoinbaseError('Coinbase transport retries exhausted') from error
                except requests.RequestException as error:
                    raise CoinbaseError('Coinbase request failed') from error
                else:
                    try:
                        status = response.status_code
                        if status == 200:
                            try:
                                return response.json()
                            except ValueError as error:
                                raise CoinbaseResponseError('Coinbase returned invalid JSON') from error
                        if status == 429:
                            header = response.headers.get('Retry-After')
                            if header is not None:
                                try:
                                    retry_wait = float(header)
                                except ValueError:
                                    try:
                                        retry_wait = (parsedate_to_datetime(header)-datetime.now(timezone.utc)).total_seconds()
                                    except (ValueError, TypeError, OverflowError):
                                        retry_wait = self.backoff * 2**attempt
                            if not math.isfinite(retry_wait) or retry_wait < 0:
                                retry_wait = self.backoff * 2**attempt
                            self._blocked_until = max(self._blocked_until, self.monotonic()+retry_wait)
                            if retry_wait > self.max_retry_wait:
                                raise CoinbaseError('Coinbase Retry-After exceeds bounded wait; retry later')
                        elif not 500 <= status < 600:
                            raise CoinbaseError(f'Coinbase HTTP {status}: product unavailable or request rejected')
                        if attempt+1 == self.max_attempts:
                            raise CoinbaseError(f'Coinbase HTTP {status}: retries exhausted')
                    finally:
                        response.close()
                if retry_wait:
                    self.sleep(retry_wait)
            raise CoinbaseError('Coinbase retries exhausted')

    def get_product(self, product):
        product = product_id(product)
        result = self._request('/products/' + product)
        if not isinstance(result, dict) or result.get('id') != product:
            raise CoinbaseResponseError('Coinbase product identity mismatch')
        return result

    @staticmethod
    def _parse(rows, start, end):
        if not isinstance(rows, list) or len(rows) > 300:
            raise CoinbaseResponseError('Expected at most 300 Coinbase candle rows')
        observations = {}
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) != 6 or any(isinstance(x, bool) for x in row):
                raise CoinbaseResponseError('Expected six numeric Coinbase candle fields')
            try:
                timestamp, low, high, opening, close, volume = map(float, row)
                if not all(math.isfinite(x) for x in (timestamp, low, high, opening, close, volume)):
                    raise ValueError('nonfinite value')
                if timestamp != int(timestamp) or timestamp < 0:
                    raise ValueError('invalid timestamp')
                stamp = pd.Timestamp(int(timestamp), unit='s', tz='UTC')
                if min(low, high, opening, close) <= 0 or volume < 0 or high < max(low, opening, close) or low > min(opening, close):
                    raise ValueError('invalid OHLCV')
            except (ValueError, TypeError, OverflowError) as error:
                raise CoinbaseResponseError('Invalid Coinbase candle values') from error
            if not start <= stamp < end:
                continue
            seconds = int(timestamp)
            values = (opening, high, low, close, volume)
            if seconds in observations and observations[seconds] != values:
                raise CoinbaseResponseError('Conflicting duplicate Coinbase candles')
            observations[seconds] = values
        if not observations:
            return empty_candles()
        return pd.DataFrame([(seconds*1_000_000, *observations[seconds]) for seconds in sorted(observations)], columns=OHLCV_COLUMNS)

    def iter_windows(self, product, *, interval, start, end, as_of=None):
        product = product_id(product)
        as_of = datetime.now(timezone.utc) if as_of is None else as_of
        cursor, stop, step = candle_bounds(start, end, as_of, interval)
        while cursor < stop:
            window_end = min(cursor + 300*step, stop)
            # An exclusive end avoids a 301st boundary bucket on inclusive APIs.
            rows = self._request('/products/' + product + '/candles',
                {'granularity': INTERVAL_SECONDS[interval], 'start': cursor.isoformat(),
                 'end': (window_end-pd.Timedelta(seconds=1)).isoformat()})
            frame = self._parse(rows, cursor, window_end)
            if len(frame) and (frame.open_time % (INTERVAL_SECONDS[interval]*1_000_000) != 0).any():
                raise CoinbaseResponseError('Off-grid Coinbase candle timestamp')
            yield CandleWindow(cursor, window_end, frame)
            cursor = window_end  # Also advances after a verified empty response.

    def get_candles(self, product_id, *, interval, start, end, as_of=None):
        frames = [window.frame for window in self.iter_windows(product_id, interval=interval,
                  start=start, end=end, as_of=as_of)]
        return pd.concat(frames, ignore_index=True) if frames else empty_candles()
