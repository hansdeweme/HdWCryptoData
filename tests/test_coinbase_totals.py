"""Provider-aware synthetic end-to-end tests; no live network or production I/O."""
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from hdw_crypto_data import CoinbaseArchiveProvisioner, TotalDatasetBuilder, TotalDatasetLoader
from hdw_crypto_data.coinbase_archive_provisioner import _digest
from hdw_crypto_data.coinbase_totals import read_partition
from hdw_crypto_data.symbols import resolve_market, normalize_symbol
from hdw_crypto_data.total_dataset_builder import MakeTotalError
from hdw_crypto_data.archive_manager import scan_archive
from hdw_crypto_data.archive_index import ArchiveIndex
from hdw_crypto_data.total_dataset_loader import DEFAULT_COLUMNS
from test_coinbase_archives import SyntheticClient

START = pd.Timestamp('2026-10-01T00:00:00Z')
END = START+pd.Timedelta(days=1)
CUTOFF = END+pd.Timedelta(hours=3)


def provision(root, product='FARTCOIN-USD', interval='1h', client=None, start=START, end=END, as_of=CUTOFF):
    return CoinbaseArchiveProvisioner(root, client=client or SyntheticClient()).provision(product,
        interval=interval, start=start, end=end, as_of=as_of)


def builder(root, *, product='FARTCOIN-USD', interval='1h', client=None, as_of=CUTOFF):
    return TotalDatasetBuilder(product, {'datastore_root': str(root)}, provider='coinbase',
        interval=interval, recent_source=client or SyntheticClient(), as_of=as_of, current_dir=str(root/'totals'))


@pytest.mark.parametrize('product', ['FARTCOIN-USD', 'HNT-USD'])
@pytest.mark.parametrize('interval', ['1h', '15m'])
def test_archive_builder_loader_end_to_end(tmp_path, product, interval):
    provision(tmp_path, product, interval)
    client = SyntheticClient()
    result = builder(tmp_path, product=product, interval=interval, client=client).build()
    expected = 27*(4 if interval == '15m' else 1)
    assert result.rows == expected and result.provider == 'coinbase'
    assert Path(result.filepath).name.startswith(f'coinbase__{product}-spot-{interval}-total-')
    raw = pd.read_csv(result.filepath)
    assert list(raw.columns) == DEFAULT_COLUMNS
    assert (raw.open_time > 10**14).all() and (raw.close_time > 10**14).all()
    assert raw.open_time.is_unique and raw.open_time.is_monotonic_increasing
    assert raw[DEFAULT_COLUMNS[7:]].isna().all().all()
    loader = TotalDatasetLoader(product, {}, provider='coinbase', interval=interval, current_dir=str(tmp_path/'totals'))
    assert loader.find_total_dataset_file() == result.filepath
    frame = loader.load_total_dataframe(preferred_tz='UTC')
    assert str(frame.index.tz) == 'UTC' and len(frame) == expected
    assert frame.index.min() == START
    assert frame.index.max() == CUTOFF-pd.Timedelta(minutes=15 if interval=='15m' else 60)
    assert frame.attrs['dataset_metadata']['provider'] == 'coinbase'
    assert frame.attrs['dataset_metadata']['quality']['missing_buckets'] == 0
    assert frame.close.dtype.kind == 'f' and frame.volume.dtype.kind == 'f'
    assert client.calls[0][2] < END  # Explicit observation overlap, not a filename date.
    assert result.diagnostics['historical_sources'][0]['acquired_at_utc']
    assert 'do not prove historical availability' in result.diagnostics['availability_semantics']
    with pytest.raises(ValueError, match='filled'):
        loader.load_total_dataframe(fill_gaps=True)
    with pytest.raises(FileNotFoundError):
        TotalDatasetLoader(product, {}, provider='coinbase', interval='15m' if interval=='1h' else '1h', current_dir=str(tmp_path/'totals')).find_total_dataset_file()


def test_provider_identity_and_root_resolution(tmp_path):
    identity = resolve_market('FARTCOIN-USD', provider='coinbase', interval='15m')
    assert (identity.provider, identity.product, identity.base, identity.quote, identity.interval) == (
        'coinbase', 'FARTCOIN-USD', 'FARTCOIN', 'USD', '15m')
    for provider, product, interval in [('binance','HNT-USD','1h'), ('unknown','HNT-USD','1h'), ('coinbase','HNT','1h'), ('coinbase','HNT-USD','1d')]:
        with pytest.raises(ValueError):
            resolve_market(product, provider=provider, interval=interval)
    with pytest.raises(ValueError):
        normalize_symbol('HNT-USD')
    roots = [tmp_path, tmp_path/'coinbase', tmp_path/'coinbase'/'spot']
    for root in roots:
        instance = TotalDatasetBuilder('HNT-USD', {'coinbase_root': str(root)}, provider='coinbase')
        assert Path(instance.WORK) == tmp_path/'coinbase'/'spot'
    with pytest.raises(ValueError, match='Explicit as_of'):
        TotalDatasetBuilder('BONK', {}, as_of=CUTOFF)


def test_sparse_data_stays_missing_and_non_utc_loading(tmp_path):
    missing = START+pd.Timedelta(hours=4)
    provision(tmp_path, client=SyntheticClient(omit={missing}))
    result = builder(tmp_path).build()
    frame = TotalDatasetLoader('FARTCOIN-USD', {}, provider='coinbase').load_total_dataframe(result.filepath, preferred_tz='Europe/Amsterdam')
    assert missing.tz_convert('Europe/Amsterdam') not in frame.index
    assert len(frame) == 26
    assert result.diagnostics['quality']['missing_buckets'] == 1
    assert result.warnings


def test_partial_and_damaged_metadata_are_actionable_errors(tmp_path):
    archive = provision(tmp_path, end=START+pd.Timedelta(hours=3))[0]
    with pytest.raises(MakeTotalError, match='PARTIAL'):
        builder(tmp_path).build()
    provision(tmp_path)
    path = Path(archive['path'])
    sidecar = path.with_name(path.name+'.metadata.json')
    original = sidecar.read_bytes()
    for key, value in [('product','HNT-USD'), ('interval','15m'), ('missing_buckets', 7), ('first_observed', END.isoformat())]:
        metadata = json.loads(original)
        metadata[key] = value
        sidecar.write_text(json.dumps(metadata))
        with pytest.raises(MakeTotalError):
            builder(tmp_path).build()
    sidecar.write_bytes(original)
    path.write_text(path.read_text()+'broken')
    with pytest.raises(MakeTotalError, match='REPAIR_REQUIRED'):
        builder(tmp_path).build()


def test_handoff_bound_and_unverified_gap(tmp_path):
    provision(tmp_path)
    with pytest.raises(MakeTotalError, match='handoff exceeds'):
        builder(tmp_path, as_of=CUTOFF+pd.Timedelta(days=30)).build()
    with pytest.raises(MakeTotalError, match='No verified'):
        builder(tmp_path/'empty').build()
    provision(tmp_path, start=END+pd.Timedelta(days=1), end=END+pd.Timedelta(days=2), as_of=END+pd.Timedelta(days=3))
    with pytest.raises(MakeTotalError, match='Unverified gap'):
        builder(tmp_path, as_of=END+pd.Timedelta(days=2)).build()


def test_identical_overlap_collapses_conflict_blocks_publication(tmp_path):
    provision(tmp_path)
    class Conflict(SyntheticClient):
        def get_candles(self, *args, **kwargs):
            frame = super().get_candles(*args, **kwargs)
            frame.loc[0, 'close'] = 2.5
            return frame
    with pytest.raises(MakeTotalError, match='Conflicting OHLCV.*coinbase recent request'):
        builder(tmp_path, client=Conflict()).build()
    assert not list((tmp_path/'totals').glob('*.csv'))
    assert builder(tmp_path).build().rows == 27


def test_monthly_daily_deterministic_overlap_and_unavailable(tmp_path):
    month_start = pd.Timestamp('2026-09-01T00:00:00Z')
    day = pd.Timestamp('2026-09-29T00:00:00Z')
    # Provision September daily before October, then monthly after month closes.
    provision(tmp_path, start=day, end=day+pd.Timedelta(days=1), as_of=day+pd.Timedelta(days=1))
    provision(tmp_path, start=month_start, end=START)
    result = builder(tmp_path, as_of=START+pd.Timedelta(hours=2)).build()
    assert result.rows == 722
    assert result.diagnostics['overlap_policy'].startswith('recent > daily > monthly')


def test_unavailable_period_stays_distinct_from_failure(tmp_path):
    empty_day = pd.date_range(START, END, freq='1h', inclusive='left')
    provision(tmp_path, client=SyntheticClient(omit=empty_day))
    with pytest.raises(MakeTotalError, match='UNAVAILABLE'):
        builder(tmp_path).build()
    provision(tmp_path, start=END, end=END+pd.Timedelta(days=1), as_of=END+pd.Timedelta(days=2))
    result = builder(tmp_path, as_of=END+pd.Timedelta(days=1)).build()
    assert result.rows == 24
    assert result.diagnostics['quality']['missing_buckets'] == 24
    assert any('Unavailable archive' in warning for warning in result.warnings)


def test_invalid_ohlcv_and_unfinished_recent_observation(tmp_path):
    provision(tmp_path)
    class Extra(SyntheticClient):
        def get_candles(self, product, **kwargs):
            options = dict(kwargs, end=kwargs['end']+pd.Timedelta(hours=1))
            return super().get_candles(product, **options)
    result = builder(tmp_path, client=Extra()).build()
    assert result.rows == 27  # The unfinished extra candle was excluded by explicit cutoff.
    class Negative(SyntheticClient):
        def get_candles(self, *args, **kwargs):
            frame = super().get_candles(*args, **kwargs)
            frame.loc[0,'volume'] = -1
            return frame
    with pytest.raises(MakeTotalError, match='nonnegative'):
        builder(tmp_path, client=Negative()).build()


def test_atomic_failure_preserves_existing_total(tmp_path):
    provision(tmp_path)
    result = builder(tmp_path).build()
    path = Path(result.filepath)
    original = path.read_bytes()
    with patch('hdw_crypto_data.coinbase_totals._atomic_json', side_effect=OSError('disk full')):
        with pytest.raises(MakeTotalError, match='disk full'):
            builder(tmp_path).build()
    assert path.read_bytes() == original
    assert not list((tmp_path/'totals').glob('*.tmp'))
    assert not list((tmp_path/'totals').glob('*.lock'))


def test_archive_inventory_index_uses_companion_without_migration(tmp_path):
    record = provision(tmp_path, client=SyntheticClient(omit={START}))[0]
    root = tmp_path/'coinbase'/'spot'
    inventory = scan_archive(root, 'deep')
    assert len(inventory.files) == 1
    item = inventory.files[0]
    assert item.recognized and item.provider == 'coinbase' and item.status == 'Warning'
    assert item.coverage_metadata['status'] == 'VERIFIED_SPARSE'
    index = ArchiveIndex(root)
    index.verify_incremental()
    assert index.load_inventory().files[0].provider == 'coinbase'
    sidecar = Path(record['path']+'.metadata.json')
    metadata = json.loads(sidecar.read_text())
    metadata['status'] = 'PARTIAL'
    sidecar.write_text(json.dumps(metadata))
    # Metadata-only edits must not hide behind the unchanged CSV identity cache.
    assert any('PARTIAL' in issue.message for issue in index.load_inventory().files[0].issues)
    assert index.list_asset_summaries()[0].status == 'Warning'


def test_reject_tampered_total_and_wrong_provider_file(tmp_path):
    provision(tmp_path)
    result = builder(tmp_path).build()
    loader = TotalDatasetLoader('HNT-USD', {}, provider='coinbase')
    with pytest.raises(ValueError, match='identity'):
        loader.load_research_dataframe(result.filepath)
    with Path(result.filepath).open('a') as stream:
        stream.write('broken')
    with pytest.raises(ValueError):
        TotalDatasetLoader('FARTCOIN-USD', {}, provider='coinbase').load_research_dataframe(result.filepath)
