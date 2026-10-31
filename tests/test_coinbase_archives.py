"""Synthetic partition integrity, incremental and compatibility tests."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from hdw_crypto_data import CoinbaseArchiveProvisioner, inspect_partition
from hdw_crypto_data.coinbase_client import CoinbaseError, OHLCV_COLUMNS
from hdw_crypto_data.total_dataset_loader import DEFAULT_COLUMNS, TotalDatasetLoader
from hdw_crypto_data.archive_manager import scan_archive


class SyntheticClient:
    def __init__(self, *, omit=(), fail=()):
        self.omit, self.fail, self.calls = set(omit), set(fail), []

    def get_candles(self, product, *, interval, start, end, as_of):
        self.calls.append((product, interval, start, end, as_of))
        if start in self.fail:
            raise CoinbaseError('synthetic acquisition failure')
        grid = pd.date_range(start, end, freq='1h' if interval == '1h' else '15min', inclusive='left')
        return pd.DataFrame([(int(t.timestamp()*1e6), 2., 3., 1., 2., 10.)
                             for t in grid if t not in self.omit], columns=OHLCV_COLUMNS)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.start = pd.Timestamp('2026-09-01T00:00:00Z')
        self.end = pd.Timestamp('2026-10-01T00:00:00Z')
        self.as_of = pd.Timestamp('2026-10-03T12:00:00Z')

    def run_archive(self, client=None, **options):
        defaults = dict(interval='1h', start=self.start, end=self.end, as_of=self.as_of)
        defaults.update(options)
        return CoinbaseArchiveProvisioner(self.base, client=client or SyntheticClient()).provision('FARTCOIN-USD', **defaults)

    def test_monthly_csv_provenance_and_exact_research_reader(self):
        result = self.run_archive()[0]
        path = Path(result['path'])
        self.assertEqual(path.relative_to(self.base).as_posix(), 'coinbase/spot/monthly/klines/FARTCOIN-USD/1h/FARTCOIN-USD-1h-2026-09.csv')
        self.assertEqual(result['status'], 'COMPLETE')
        self.assertEqual(result['observations'], 720)
        frame = pd.read_csv(path)
        self.assertEqual(list(frame.columns), DEFAULT_COLUMNS)
        self.assertTrue(frame[DEFAULT_COLUMNS[7:]].isna().all().all())
        self.assertEqual(frame.close_time.iloc[0]-frame.open_time.iloc[0], 3600_000_000-1)
        # Explicit file path parsing works; constructor product routing is Phase 2.
        loader = TotalDatasetLoader('BTCUSDT', {}, current_dir=str(self.base))
        loaded = loader.load_research_dataframe(file_path=str(path), interval='1h')
        self.assertEqual(str(loaded.index.tz), 'UTC')
        self.assertEqual(loaded.index[0], self.start)
        self.assertEqual(len(loaded), 720)
        self.assertEqual(inspect_partition(path)['csv_sha256'], result['csv_sha256'])
        self.assertEqual(TotalDatasetLoader('FARTCOIN-USD', {}, provider='coinbase').MARKET, 'FARTCOIN-USD')
        with self.assertRaises(ValueError):
            TotalDatasetLoader('FARTCOIN-USD', {})

    def test_daily_interval_root_isolation_and_no_current_day(self):
        for interval, count in [('1h', 24), ('15m', 96)]:
            results = self.run_archive(interval=interval, start=self.end, end=self.as_of)
            self.assertEqual(len(results), 2)
            self.assertTrue(all(r['partition_kind'] == 'daily' and r['observations'] == count for r in results))
        self.assertFalse((self.base/'spot').exists())
        root = self.base/'coinbase'/'spot'
        for supplied in (self.base, root.parent, root):
            self.assertEqual(CoinbaseArchiveProvisioner(supplied).root, root)
        self.assertEqual(CoinbaseArchiveProvisioner(self.base/'spot').root, root)

    def test_missing_bucket_fully_verified_is_sparse_and_incremental_skips(self):
        client = SyntheticClient(omit={self.start})
        result = self.run_archive(client)[0]
        self.assertEqual(result['status'], 'VERIFIED_SPARSE')
        self.assertEqual(result['missing_buckets'], 1)
        self.assertEqual(result['unverified_ranges'], [])
        self.assertIn('unknown', result['missing_bucket_meaning'])
        before = len(client.calls)
        content = Path(result['path']).read_bytes()
        result2 = self.run_archive(client)[0]
        self.assertEqual(result2['action'], 'SKIPPED_VERIFIED')
        self.assertEqual(len(client.calls), before)
        self.assertEqual(Path(result['path']).read_bytes(), content)

    def test_empty_early_window_does_not_end_scan(self):
        empty = pd.date_range(self.start, self.start+pd.Timedelta(hours=300), freq='1h', inclusive='left')
        result = self.run_archive(SyntheticClient(omit=empty))[0]
        self.assertEqual(result['status'], 'VERIFIED_SPARSE')
        self.assertEqual(result['observations'], 420)
        self.assertEqual(result['first_observed'], (self.start+pd.Timedelta(hours=300)).isoformat())

    def test_unavailable_recorded_and_inspect_never_requested(self):
        self.assertEqual(inspect_partition(self.base/'absent.csv')['status'], 'NEVER_REQUESTED')
        empty = pd.date_range(self.start, self.end, freq='1h', inclusive='left')
        result = self.run_archive(SyntheticClient(omit=empty))[0]
        self.assertEqual(result['status'], 'UNAVAILABLE')
        self.assertEqual(result['observations'], 0)
        self.assertEqual(result['unverified_ranges'], [])
        self.assertEqual(len(pd.read_csv(result['path'])), 0)

    def test_partial_failure_resumes_only_unverified_window(self):
        failed_start = self.start+pd.Timedelta(hours=300)
        client = SyntheticClient(fail={failed_start})
        result = self.run_archive(client)[0]
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertEqual(len(result['failed_windows']), 1)
        self.assertEqual(len(client.calls), 3)
        client.fail.clear()
        before = len(client.calls)
        resumed = self.run_archive(client)[0]
        self.assertEqual(len(client.calls)-before, 1)
        self.assertEqual(resumed['status'], 'COMPLETE')
        self.assertEqual(resumed['observations'], 720)

    def test_partial_request_is_not_claimed_as_complete_month(self):
        result = self.run_archive(end=self.start+pd.Timedelta(hours=3))[0]
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertEqual(result['observations'], 3)
        client = SyntheticClient()
        result = self.run_archive(client)[0]
        self.assertEqual(client.calls[0][2], self.start+pd.Timedelta(hours=3))
        self.assertEqual(result['status'], 'COMPLETE')

    def test_failed_or_sparser_repair_keeps_valid_csv_and_metadata(self):
        result = self.run_archive()[0]
        path = Path(result['path'])
        metadata = path.with_name(path.name+'.metadata.json')
        original = (path.read_bytes(), metadata.read_bytes())
        for client in (SyntheticClient(fail={self.start}), SyntheticClient(omit={self.start})):
            attempt = self.run_archive(client, repair=True)[0]
            self.assertEqual(attempt['action'], 'RETAINED_PREVIOUS')
            self.assertEqual((path.read_bytes(), metadata.read_bytes()), original)
        self.assertTrue(path.with_name(path.name+'.attempt.json').exists())

    def test_checksum_corruption_requires_explicit_repair(self):
        result = self.run_archive()[0]
        path = Path(result['path'])
        path.write_text('damaged', encoding='utf-8')
        client = SyntheticClient()
        self.assertEqual(self.run_archive(client)[0]['status'], 'REPAIR_REQUIRED')
        self.assertEqual(client.calls, [])
        self.assertEqual(self.run_archive(client, repair=True)[0]['status'], 'COMPLETE')

    def test_failed_repair_of_uncertain_csv_does_not_destroy_it(self):
        result = self.run_archive()[0]
        path = Path(result['path'])
        path.with_name(path.name+'.metadata.json').unlink()
        original = path.read_bytes()
        attempt = self.run_archive(SyntheticClient(fail={self.start}), repair=True)[0]
        self.assertEqual(attempt['action'], 'RETAINED_PREVIOUS')
        self.assertEqual(path.read_bytes(), original)

    def test_metadata_failure_on_first_publication_leaves_no_apparent_archive(self):
        with patch('hdw_crypto_data.coinbase_archive_provisioner._atomic_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.run_archive()
        self.assertFalse(list(self.base.rglob('*.csv')))
        self.assertFalse(list(self.base.rglob('*.tmp')))
        self.assertFalse(list(self.base.rglob('*.lock')))

    def test_metadata_publication_failure_rolls_back_csv(self):
        result = self.run_archive()[0]
        path = Path(result['path'])
        original = path.read_bytes()
        with patch('hdw_crypto_data.coinbase_archive_provisioner._atomic_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.run_archive(repair=True)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(inspect_partition(path)['status'], 'COMPLETE')
        self.assertFalse(list(self.base.rglob('*.tmp')))
        self.assertFalse(list(self.base.rglob('*.lock')))

    def test_busy_partition_and_inventory_phase2_boundary(self):
        result = self.run_archive()[0]
        path = Path(result['path'])
        path.with_name(path.name+'.lock').write_text('other writer')
        self.assertEqual(self.run_archive()[0]['status'], 'BUSY')
        path.with_name(path.name+'.lock').unlink()
        inventory = scan_archive(self.base/'coinbase'/'spot', 'deep')
        self.assertTrue(next(f for f in inventory.files if f.path == path).recognized)
        self.assertEqual(next(f for f in inventory.files if f.path == path).provider, 'coinbase')


if __name__ == '__main__':
    unittest.main()
