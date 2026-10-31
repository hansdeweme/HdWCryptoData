"""Bounded live validation, exclusively inside a disposable test datastore.

Run explicitly: python -m hdw_crypto_data.coinbase_smoke
Checks product metadata and the previous completed UTC day at both intervals.
No production paths are accepted. Temporary archives are removed on exit.

Programmatic usage:
    with CoinbaseClient() as client:
        frame = client.get_candles('HNT-USD', interval='1h',
            start='2026-09-01T00:00:00Z', end='2026-09-02T00:00:00Z',
            as_of='2026-09-02T00:00:00Z')
        archives = CoinbaseArchiveProvisioner(chosen_datastore, client=client)
        results = archives.provision('HNT-USD', interval='1h',
            start='2026-09-01T00:00:00Z', end='2026-10-01T00:00:00Z',
            as_of='2026-10-02T00:00:00Z')
        # Repeat provision to request only unverified windows; repair=True
        # explicitly refetches full selected partitions.
        coverage = inspect_partition(results[0]['path'])
"""
from datetime import datetime, timezone
import argparse
import json
import tempfile

import pandas as pd

from .coinbase_client import CoinbaseClient
from .coinbase_archive_provisioner import CoinbaseArchiveProvisioner, inspect_partition


def main():
    parser = argparse.ArgumentParser(description='Disposable one-day Coinbase validation')
    parser.add_argument('--totals', action='store_true', help='Also build/load provider-aware totals from the temporary archives')
    args = parser.parse_args()
    as_of = pd.Timestamp(datetime.now(timezone.utc))
    end = as_of.normalize()
    start = end-pd.Timedelta(days=1)
    failures = 0
    with tempfile.TemporaryDirectory(prefix='hdw-coinbase-smoke-') as root, CoinbaseClient() as client:
        for product in ('FARTCOIN-USD', 'HNT-USD'):
            try:
                metadata = client.get_product(product)
                print(json.dumps({'product': product, 'status': metadata.get('status'),
                    'trading_disabled': metadata.get('trading_disabled')}))
                for interval in ('1h', '15m'):
                    results = CoinbaseArchiveProvisioner(root, client=client).provision(
                        product, interval=interval, start=start, end=end, as_of=as_of)
                    for result in results:
                        published = inspect_partition(result['path'])
                        if published['status'] not in ('COMPLETE', 'VERIFIED_SPARSE'):
                            failures += 1
                        print(json.dumps({key: published.get(key) for key in (
                            'provider', 'product', 'interval', 'status', 'observations',
                            'first_observed', 'last_observed', 'missing_buckets',
                            'missing_ranges', 'unverified_ranges', 'failed_windows')}))
                    if args.totals:
                        from .total_dataset_builder import TotalDatasetBuilder
                        from .total_dataset_loader import TotalDatasetLoader
                        from pathlib import Path
                        # A fixed completed-day cutoff makes this reproducible;
                        # source acquisition timestamps remain explicitly later.
                        total = TotalDatasetBuilder(product, {'datastore_root': root},
                            provider='coinbase', interval=interval, as_of=end,
                            recent_source=client, current_dir=str(Path(root)/'totals')).build()
                        frame = TotalDatasetLoader(product, {}, provider='coinbase', interval=interval).load_total_dataframe(total.filepath, preferred_tz='UTC')
                        print(json.dumps({'total': Path(total.filepath).name, 'provider': total.provider,
                            'product': product, 'interval': interval, 'rows': len(frame),
                            'timezone': str(frame.index.tz), 'first': frame.index.min().isoformat(),
                            'last': frame.index.max().isoformat(), 'missing_buckets': total.diagnostics['quality']['missing_buckets'],
                            'actual_observations_only': True}))
            except Exception as error:
                failures += 1
                print(json.dumps({'product': product, 'error': str(error), 'type': type(error).__name__}))
    return int(bool(failures))


if __name__ == '__main__':
    raise SystemExit(main())
