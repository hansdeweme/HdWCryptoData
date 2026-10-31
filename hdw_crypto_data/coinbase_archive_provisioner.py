"""Coinbase candles in Vision-shaped local partitions; no builder/index changes.

Each CSV has a required .csv.metadata.json companion binding provider, product,
coverage and SHA-256. Keep this companion with copied archives. Missing buckets
mean unobserved activity, not proven no-trade intervals. No candles are invented.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

import pandas as pd
import requests

from .coinbase_client import (CoinbaseClient, CoinbaseError, CoinbaseResponseError,
                              INTERVAL_SECONDS, candle_bounds, product_id, utc_time)
from .total_dataset_loader import DEFAULT_COLUMNS

FORMAT_VERSION = 1


def _ranges(timestamps, step):
    """Compact missing/verified bucket starts into half-open UTC intervals."""
    result = []
    for stamp in timestamps:
        if result and result[-1][1] == stamp:
            result[-1][1] = stamp + step
        else:
            result.append([stamp, stamp+step])
    return [[a.isoformat(), b.isoformat()] for a, b in result]


def _safe(path):
    path = Path(path).absolute()
    if '..' in path.parts:
        raise ValueError('Archive paths must not contain traversal')
    for parent in (path, *path.parents):
        if parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()):
            raise ValueError('Linked archive paths are unsupported')
    return path


def coinbase_spot_root(root):
    """Accept datastore, coinbase, or explicit coinbase/spot root.

    An existing Binance spot selection resolves to a sibling coinbase/spot tree;
    it never writes Coinbase partitions into the Binance directory.
    """
    path = _safe(root)
    if path.name.lower() == 'spot':
        if path.parent.name.lower() != 'coinbase':
            return path.parent/'coinbase'/'spot'
        return path
    return path/'spot' if path.name.lower() == 'coinbase' else path/'coinbase'/'spot'


def _digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path, document):
    temporary = _safe(path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp'))
    try:
        with temporary.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(document, stream, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _partition_lock(path):
    lock = _safe(path.with_name(path.name+'.lock'))
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.close(descriptor)
        yield
    finally:
        lock.unlink(missing_ok=True)


def inspect_partition(path):
    """Read provenance/coverage and verify its binding; never create an index."""
    path = _safe(path)
    metadata_path = _safe(path.with_name(path.name+'.metadata.json'))
    if not path.exists() and not metadata_path.exists():
        return {'status': 'NEVER_REQUESTED', 'path': str(path)}
    try:
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        if metadata.get('schema_version') != FORMAT_VERSION or metadata.get('provider') != 'coinbase':
            raise ValueError('Unsupported Coinbase metadata')
        product_id(metadata['product'])
        if metadata['interval'] not in INTERVAL_SECONDS or metadata['csv_sha256'] != _digest(path):
            raise ValueError('CSV identity/checksum mismatch')
        metadata['path'] = str(path)
        return metadata
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {'status': 'REPAIR_REQUIRED', 'path': str(path), 'reason': str(error)}


class CoinbaseArchiveProvisioner:
    def __init__(self, root, *, client=None):
        self.root = coinbase_spot_root(root)
        self.client = client

    @staticmethod
    def _partitions(start, end, as_of):
        month = start.normalize().replace(day=1)
        current_month = as_of.normalize().replace(day=1)
        while month < end:
            following = month + pd.DateOffset(months=1)
            if following <= current_month:
                yield 'monthly', month, following
            else:
                day = max(month, start.normalize())
                while day < min(following, end, as_of.normalize()):
                    yield 'daily', day, day+pd.Timedelta(days=1)
                    day += pd.Timedelta(days=1)
            month = following

    def provision(self, product, *, interval, start, end, as_of=None, repair=False):
        """Incrementally request missing windows; repair explicitly refetches periods.

        Current UTC day is excluded. Closed months become monthly files; the
        evaluation month's completed days become daily files. Results include
        failures; failed refreshes retain the previous published partition.
        """
        product = product_id(product)
        if not isinstance(repair, bool):
            raise ValueError('repair must be boolean')
        as_of = utc_time(datetime.now(timezone.utc) if as_of is None else as_of)
        start, end, step = candle_bounds(start, end, as_of, interval)
        if start >= end:
            return []
        owned = self.client is None
        client = self.client if self.client is not None else CoinbaseClient()
        try:
            return [self._provision_partition(client, product, interval, period, a, b,
                        max(start, a), min(end, b), as_of, step, repair)
                    for period, a, b in self._partitions(start, end, as_of)]
        finally:
            if owned:
                client.close()

    def _provision_partition(self, client, product, interval, period, a, b,
                             start, end, as_of, step, repair):
        date = a.strftime('%Y-%m' if period == 'monthly' else '%Y-%m-%d')
        path = _safe(self.root/period/'klines'/product/interval/f'{product}-{interval}-{date}.csv')
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with _partition_lock(path):
                return self._update(client, path, product, interval, period, a, b,
                                    start, end, as_of, step, repair)
        except FileExistsError:
            return {'status': 'BUSY', 'path': str(path), 'provider': 'coinbase', 'product': product}

    def _update(self, client, path, product, interval, period, a, b, start, end, as_of, step, repair):
        old = inspect_partition(path)
        if old['status'] == 'REPAIR_REQUIRED' and not repair:
            return old
        grid = pd.date_range(a, b, freq=step, inclusive='left')
        verified = pd.DatetimeIndex([], tz='UTC')
        frames = []
        if old['status'] not in ('NEVER_REQUESTED', 'REPAIR_REQUIRED'):
            if (old['product'], old['interval'], old['partition_start'], old['partition_end']) != (
                    product, interval, a.isoformat(), b.isoformat()):
                return {'status': 'REPAIR_REQUIRED', 'path': str(path), 'reason': 'Partition metadata identity mismatch'}
            if not repair:
                for left, right in old['verified_ranges']:
                    verified = verified.union(pd.date_range(utc_time(left), utc_time(right), freq=step, inclusive='left'))
                frames.append(pd.read_csv(path).reindex(columns=DEFAULT_COLUMNS[:6]))
        if repair:
            start, end = a, b
        requested = pd.date_range(start, end, freq=step, inclusive='left')
        pending = requested.difference(verified)
        if pending.empty and old['status'] != 'NEVER_REQUESTED':
            return {**old, 'action': 'SKIPPED_VERIFIED'}
        failures = []
        # Split only contiguous missing ranges; resume successful partial runs.
        for left, right in _ranges(pending, step):
            cursor, stop = utc_time(left), utc_time(right)
            while cursor < stop:
                window_end = min(cursor+300*step, stop)
                try:
                    acquired = client.get_candles(product, interval=interval, start=cursor,
                        end=window_end, as_of=as_of)
                    frames.append(acquired)
                    verified = verified.union(pd.date_range(cursor, window_end, freq=step, inclusive='left'))
                except (CoinbaseError, CoinbaseResponseError, requests.RequestException) as error:
                    failures.append({'start': cursor.isoformat(), 'end': window_end.isoformat(),
                                     'error': str(error), 'error_type': type(error).__name__})
                cursor = window_end
        frames = [frame for frame in frames if len(frame)]
        frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=DEFAULT_COLUMNS[:6])
        if len(frame):
            # Conflicting cached/new rows are never resolved by arbitrary order.
            unique = frame.drop_duplicates()
            if unique.open_time.duplicated().any():
                raise CoinbaseResponseError('Conflicting persisted Coinbase observations; explicit repair required')
            frame = unique.sort_values('open_time').reset_index(drop=True)
        observed = pd.DatetimeIndex(pd.to_datetime(frame.open_time.astype('int64'), unit='us', utc=True))
        if not observed.isin(grid).all() or observed.has_duplicates:
            raise CoinbaseResponseError('Observations outside partition grid')
        missing = grid.difference(observed)
        unverified = grid.difference(verified)
        status = ('PARTIAL' if len(unverified) else 'UNAVAILABLE' if not len(frame)
                  else 'VERIFIED_SPARSE' if len(missing) else 'COMPLETE')
        metadata = dict(schema_version=FORMAT_VERSION, provider='coinbase', product=product,
            quote_currency=product.split('-')[1], interval=interval, volume_unit='base_asset',
            timestamp_unit='us', close_time_semantics='inclusive final microsecond of bucket',
            auxiliary_fields='unavailable; CSV empty cells', partition_kind=period,
            partition_start=a.isoformat(), partition_end=b.isoformat(),
            requested_start=start.isoformat(), requested_end=end.isoformat(), as_of=as_of.isoformat(),
            acquired_at_utc=datetime.now(timezone.utc).isoformat(), status=status,
            observations=len(frame), expected_buckets=len(grid),
            first_observed=None if observed.empty else observed.min().isoformat(),
            last_observed=None if observed.empty else observed.max().isoformat(),
            missing_buckets=len(missing), missing_ranges=_ranges(missing, step),
            verified_ranges=_ranges(verified.sort_values(), step),
            unverified_ranges=_ranges(unverified, step), failed_windows=failures,
            missing_bucket_meaning='No observation; no-trade versus provider omission is unknown')
        protected = old['status'] in ('COMPLETE', 'VERIFIED_SPARSE', 'UNAVAILABLE')
        old_observed = (pd.DatetimeIndex(pd.to_datetime(pd.read_csv(path).open_time, unit='us', utc=True))
                        if protected else pd.DatetimeIndex([], tz='UTC'))
        if repair and path.exists() and (len(unverified) or (protected and not old_observed.isin(observed).all())):
            metadata.update(action='RETAINED_PREVIOUS', path=str(path))
            _atomic_json(path.with_name(path.name+'.attempt.json'), metadata)
            return metadata
        frame = frame.reindex(columns=DEFAULT_COLUMNS)
        frame['open_time'] = frame.open_time.astype('int64')
        frame['close_time'] = frame.open_time + INTERVAL_SECONDS[interval]*1_000_000 - 1
        temporary = _safe(path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp'))
        backup = _safe(path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp'))
        replaced = False
        try:
            with temporary.open('x', encoding='utf-8', newline='') as stream:
                frame.to_csv(stream, index=False, na_rep='')
                stream.flush()
                os.fsync(stream.fileno())
            # Check the exact staged contract before publishing.
            checked = pd.read_csv(temporary)
            if list(checked.columns) != DEFAULT_COLUMNS or len(checked) != len(frame):
                raise ValueError('Staged Coinbase CSV validation failed')
            metadata['csv_sha256'] = _digest(temporary)
            if path.exists():
                shutil.copyfile(path, backup)
            os.replace(temporary, path)
            replaced = True
            # A crash between commits is detected as REPAIR_REQUIRED by digest.
            _atomic_json(path.with_name(path.name+'.metadata.json'), metadata)
        except BaseException:
            if replaced:
                if backup.exists():
                    os.replace(backup, path)
                else:
                    path.unlink(missing_ok=True)
            raise
        finally:
            temporary.unlink(missing_ok=True)
            backup.unlink(missing_ok=True)
        return {**metadata, 'path': str(path), 'action': 'PUBLISHED'}
