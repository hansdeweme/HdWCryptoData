"""Coinbase-specific archive verification and total publication, using Phase 1 I/O.

Companion JSON is authoritative; no archive repair, index migration or backfill
occurs here. A candle cutoff does not claim historical information availability.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import uuid

import pandas as pd

from .coinbase_client import CoinbaseClient, INTERVAL_SECONDS, utc_time
from .coinbase_archive_provisioner import (coinbase_spot_root, inspect_partition,
    _digest, _safe, _atomic_json, _partition_lock)
from .dataset_quality import validate_ohlcv, merge_observations, coverage


def read_partition(path, *, product, interval):
    metadata = inspect_partition(path)
    state = metadata['status']
    if state not in ('COMPLETE', 'VERIFIED_SPARSE', 'UNAVAILABLE'):
        raise ValueError(f'{path}: {state}; explicitly provision/repair and verify this partition first')
    if metadata.get('product') != product or metadata.get('interval') != interval:
        raise ValueError(f'{path}: provider/product/interval metadata mismatch')
    frame = validate_ohlcv(pd.read_csv(path), interval=interval)
    a, b = utc_time(metadata['partition_start']), utc_time(metadata['partition_end'])
    kind = path.parent.parent.parent.parent.name
    expected_date = a.strftime('%Y-%m' if kind == 'monthly' else '%Y-%m-%d')
    expected_end = a+pd.DateOffset(months=1) if kind == 'monthly' else a+pd.Timedelta(days=1)
    if (kind not in ('monthly', 'daily') or a != a.normalize() or b != expected_end
            or (kind == 'monthly' and a.day != 1)
            or path.name != f'{product}-{interval}-{expected_date}.csv'):
        raise ValueError(f'{path}: partition metadata does not match filename/hierarchy')
    observed = pd.DatetimeIndex(pd.to_datetime(frame.open_time, unit='us', utc=True))
    if observed.has_duplicates or not observed.is_monotonic_increasing or ((observed < a) | (observed >= b)).any():
        raise ValueError(f'{path}: observations violate partition bounds/order')
    step = pd.Timedelta(seconds=INTERVAL_SECONDS[interval])
    verified = pd.DatetimeIndex([], tz='UTC')
    for left, right in metadata['verified_ranges']:
        left, right = utc_time(left), utc_time(right)
        if not a <= left < right <= b or left != left.floor(step) or right != right.floor(step):
            raise ValueError(f'{path}: invalid verified range')
        verified = verified.union(pd.date_range(left, right, freq=step, inclusive='left'))
    expected = pd.date_range(a, b, freq=step, inclusive='left')
    quality = coverage(frame, interval=interval, start=a, end=b)
    if (not expected.difference(verified).empty or not observed.isin(verified).all()
            or metadata.get('unverified_ranges') or metadata.get('failed_windows')
            or metadata.get('observations') != len(frame)
            or metadata.get('expected_buckets') != len(expected)
            or metadata.get('missing_buckets') != len(expected.difference(observed))
            or (len(frame) and metadata.get('missing_ranges') != quality['missing_ranges'])):
        raise ValueError(f'{path}: inconsistent coverage metadata')
    if (metadata.get('first_observed') != (None if observed.empty else observed.min().isoformat())
            or metadata.get('last_observed') != (None if observed.empty else observed.max().isoformat())
            or metadata.get('quote_currency') != product.split('-')[1]
            or metadata.get('timestamp_unit') != 'us' or metadata.get('volume_unit') != 'base_asset'
            or _digest(path) != metadata['csv_sha256']):
        raise ValueError(f'{path}: inconsistent observation/provenance metadata')
    if ((state == 'COMPLETE' and len(frame) != len(expected))
            or (state == 'UNAVAILABLE' and len(frame))
            or (state == 'VERIFIED_SPARSE' and (not len(frame) or len(frame) == len(expected)))):
        raise ValueError(f'{path}: coverage state contradicts observations')
    return frame, metadata


def discover_partitions(spot, product, interval):
    """Explicit period priority/date/path ordering, independent of enumeration."""
    paths = []
    for priority, period in enumerate(('daily', 'monthly')):
        directory = spot/period/'klines'/product/interval
        for path in sorted(directory.glob('*.csv')):
            date = path.stem.removeprefix(f'{product}-{interval}-')
            try:
                stamp = pd.to_datetime(date, format='%Y-%m-%d' if period == 'daily' else '%Y-%m', utc=True)
            except ValueError as error:
                raise ValueError(f'Malformed archive filename: {path}') from error
            if path.name != f'{product}-{interval}-{date}.csv':
                raise ValueError(f'Unexpected archive product: {path}')
            paths.append((priority, stamp, str(path), path))
    return [item[-1] for item in sorted(paths)]


def publish_total(path, frame, metadata):
    """CSV+checksum companion commit with rollback on handled failures."""
    path = _safe(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    backup = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    replaced = False
    with _partition_lock(path):
        try:
            with temporary.open('x', encoding='utf-8', newline='') as stream:
                frame.to_csv(stream, index=False, na_rep='')
                stream.flush()
                os.fsync(stream.fileno())
            metadata['csv_sha256'] = _digest(temporary)
            if path.exists():
                shutil.copyfile(path, backup)
            os.replace(temporary, path)
            replaced = True
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


def build_coinbase_total(builder, as_of, *, recent_limit=500):
    from .total_dataset_builder import MakeTotalResult
    from .total_dataset_loader import total_dataset_filename
    as_of = utc_time(datetime.now(timezone.utc) if as_of is None else as_of)
    interval, product = builder.interval, builder.MARKET
    step = pd.Timedelta(seconds=INTERVAL_SECONDS[interval])
    cutoff = as_of.floor(step)
    sources, records, relevant = [], [], []
    for path in discover_partitions(Path(builder.WORK), product, interval):
        # Future partitions cannot contribute to a historical candle cutoff.
        date = path.stem.removeprefix(f'{product}-{interval}-')
        if pd.to_datetime(date, format='%Y-%m-%d' if len(date)==10 else '%Y-%m', utc=True) >= cutoff:
            continue
        frame, metadata = read_partition(path, product=product, interval=interval)
        if utc_time(metadata['partition_start']) >= cutoff:
            continue
        frame = validate_ohlcv(frame, interval=interval, as_of=as_of)
        sources.append((str(path), frame))
        relevant.append(metadata)
        records.append(dict(path=str(path), status=metadata['status'], csv_sha256=metadata['csv_sha256'],
            acquired_at_utc=metadata['acquired_at_utc'], partition_start=metadata['partition_start'],
            partition_end=metadata['partition_end'], missing_ranges=metadata['missing_ranges']))
    if not relevant:
        raise ValueError('No verified Coinbase archives; run archive provisioning separately first')
    if not any(len(frame) for _, frame in sources):
        raise ValueError('Verified archives are UNAVAILABLE: no observed candles; provision additional periods first')
    historical, _ = merge_observations(sources)
    latest = pd.Timestamp(int(historical.open_time.max()), unit='us', tz='UTC')
    request_start = latest  # Include the latest actual archived candle in the handoff.
    if cutoff-request_start > recent_limit*step:
        raise ValueError(f'Archive/recent handoff exceeds {recent_limit} {interval} candles; provision newer archives first')
    client = builder.recent_source
    owned = client is None
    client = CoinbaseClient() if owned else client
    try:
        recent = client.get_candles(product, interval=interval, start=request_start, end=cutoff, as_of=as_of)
    finally:
        if owned:
            client.close()
    recent = validate_ohlcv(recent, interval=interval, as_of=as_of)
    observed = pd.DatetimeIndex(pd.to_datetime(recent.open_time, unit='us', utc=True))
    if ((observed < request_start) | (observed >= cutoff)).any():
        raise ValueError('Recent source returned candles outside requested range')
    combined, conflicts = merge_observations([('coinbase recent request', recent), *sources])
    quality = coverage(combined, interval=interval,
        start=min(utc_time(r['partition_start']) for r in relevant), end=cutoff)
    # Internal gaps between partition files must be explained by verified ranges,
    # rather than being mistaken for no-trade buckets.
    verified = pd.DatetimeIndex([], tz='UTC')
    for metadata in relevant:
        for a, b in metadata['verified_ranges']:
            verified = verified.union(pd.date_range(utc_time(a), min(utc_time(b), cutoff), freq=step, inclusive='left'))
    verified = verified.union(pd.date_range(request_start, cutoff, freq=step, inclusive='left'))
    required = pd.date_range(utc_time(quality['requested_start']), cutoff, freq=step, inclusive='left')
    if not required.difference(verified).empty:
        raise ValueError('Unverified gap between archive partitions; provision the missing periods explicitly')
    warnings = tuple(([f"Verified sparse data: {quality['missing_buckets']} missing {interval} buckets; no filling applied"]
                      if quality['missing_buckets'] else []) +
                     [f"Unavailable archive period: {r['partition_start']}" for r in relevant if r['status']=='UNAVAILABLE'])
    metadata = dict(schema_version=1, record_type='total_dataset', provider='coinbase', product=product,
        base_asset=builder.identity.base, quote_asset=builder.identity.quote, interval=interval,
        timestamp_unit='us', volume_unit='base_asset', as_of=as_of.isoformat(),
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        availability_semantics='Candle cutoff only; acquisition timestamps do not prove historical availability',
        overlap_policy='recent > daily > monthly; conflicting OHLCV rejected',
        historical_sources=records, recent_request_start=request_start.isoformat(),
        recent_request_end=cutoff.isoformat(), quality=quality, warnings=list(warnings))
    filename = total_dataset_filename(product, interval, combined.open_time.iloc[0], combined.open_time.iloc[-1], provider='coinbase')
    path = Path(builder.current_dir)/filename
    publish_total(path, combined, metadata)
    return MakeTotalResult(product, str(path), len(combined), 'Coinbase total dataset created',
                           interval, provider='coinbase', warnings=warnings, diagnostics=metadata)


def load_total_metadata(path, *, product, interval):
    path = _safe(path)
    metadata = json.loads(path.with_name(path.name+'.metadata.json').read_text(encoding='utf-8'))
    if (metadata.get('schema_version') != 1 or metadata.get('record_type') != 'total_dataset'
            or metadata.get('provider') != 'coinbase' or metadata.get('product') != product
            or metadata.get('interval') != interval or metadata.get('csv_sha256') != _digest(path)):
        raise ValueError('Coinbase total identity or checksum mismatch')
    return metadata
