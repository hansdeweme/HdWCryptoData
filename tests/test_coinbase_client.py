"""Offline request, timing and validation tests; no public API calls."""
from datetime import datetime, timezone
from email.utils import format_datetime
import unittest

import pandas as pd
import requests

from hdw_crypto_data.coinbase_client import CoinbaseClient, CoinbaseError, CoinbaseResponseError

START = pd.Timestamp('2026-09-01T00:00:00Z')


def row(stamp, close=2.):
    return [int(stamp.timestamp()), 1., 3., 2., close, 10.]


class Response:
    def __init__(self, payload=None, status=200, headers=None):
        self.payload, self.status_code, self.headers = payload, status, headers or {}
        self.closed = False

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

    def close(self):
        self.closed = True


class Session:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class ClientTests(unittest.TestCase):
    def client(self, replies, **options):
        session = Session(replies)
        client = CoinbaseClient(session=session, min_request_interval=0, backoff_factor=0, **options)
        self.addCleanup(client.close)
        return client, session

    def test_pagination_601_hours_empty_middle_window_and_order(self):
        end = START+pd.Timedelta(hours=601)
        client, session = self.client([Response([row(START+pd.Timedelta(hours=1)), row(START)]),
                                       Response([]), Response([row(end-pd.Timedelta(hours=1))])])
        frame = client.get_candles('FARTCOIN-USD', interval='1h', start=START, end=end, as_of=end)
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(list(frame.open_time), sorted(frame.open_time))
        self.assertEqual(len(frame), 3)
        for url, kwargs in session.calls:
            self.assertTrue(url.endswith('/products/FARTCOIN-USD/candles'))
            params = kwargs['params']
            self.assertEqual(params['granularity'], 3600)
            self.assertLessEqual(pd.Timestamp(params['end'])-pd.Timestamp(params['start']), pd.Timedelta(hours=300))

    def test_half_open_range_and_completed_cutoff(self):
        client, _ = self.client([Response([row(START-pd.Timedelta(hours=1)), row(START),
                                           row(START+pd.Timedelta(hours=1)), row(START+pd.Timedelta(hours=2))])])
        frame = client.get_candles('HNT-USD', interval='1h', start=START,
            end=START+pd.Timedelta(hours=4), as_of=START+pd.Timedelta(hours=2, minutes=30))
        self.assertEqual(list(frame.open_time), [int(START.timestamp()*1e6), int((START+pd.Timedelta(hours=1)).timestamp()*1e6)])

    def test_15m_non_aligned_start_and_timezone_conversion(self):
        client, session = self.client([Response([row(START), row(START+pd.Timedelta(minutes=15))])])
        frame = client.get_candles('HNT-USD', interval='15m', start=(START+pd.Timedelta(minutes=1)).tz_convert('Europe/Amsterdam'),
            end=START+pd.Timedelta(minutes=30), as_of=START+pd.Timedelta(hours=1))
        self.assertEqual(len(frame), 1)
        self.assertEqual(session.calls[0][1]['params']['granularity'], 900)
        self.assertEqual(pd.Timestamp(session.calls[0][1]['params']['start']), START+pd.Timedelta(minutes=15))

    def test_identical_duplicates_collapsed_conflicts_rejected(self):
        client, _ = self.client([Response([row(START), row(START)])])
        self.assertEqual(len(client.get_candles('HNT-USD', interval='1h', start=START,
            end=START+pd.Timedelta(hours=1), as_of=START+pd.Timedelta(days=1))), 1)
        client, _ = self.client([Response([row(START), row(START, 2.5)])])
        with self.assertRaises(CoinbaseResponseError):
            client.get_candles('HNT-USD', interval='1h', start=START,
                end=START+pd.Timedelta(hours=1), as_of=START+pd.Timedelta(days=1))

    def test_bad_ohlcv_grid_json_and_response_shape_rejected(self):
        invalid = [Response([row(START, 4.)]), Response([row(START+pd.Timedelta(seconds=1))]),
                   Response([row(START)[:-1]]), Response({'message': 'bad'}),
                   Response(ValueError('json')), Response([row(START)]*301),
                   Response([[int(START.timestamp()), 1, 3, True, 2, 10]])]
        for response in invalid:
            with self.subTest(response=response):
                client, _ = self.client([response])
                with self.assertRaises(CoinbaseResponseError):
                    client.get_candles('HNT-USD', interval='1h', start=START,
                        end=START+pd.Timedelta(hours=2), as_of=START+pd.Timedelta(days=1))

    def test_invalid_arguments_make_no_requests(self):
        for product, interval, start in [('HNT/USD', '1h', START), ('hnt-usd', '1h', START),
                                         ('HNT-USD', '5m', START), ('HNT-USD', '1h', '2026-09-01')]:
            client, session = self.client([])
            with self.assertRaises(ValueError):
                client.get_candles(product, interval=interval, start=start,
                    end=START+pd.Timedelta(days=1), as_of=START+pd.Timedelta(days=1))
            self.assertEqual(session.calls, [])

    def test_timeout_server_and_rate_limit_retries_and_close(self):
        sleeps = []
        responses = [Response(status=503), Response(status=429, headers={'Retry-After': '2'}), Response([])]
        client, session = self.client(responses, max_attempts=4, sleep=sleeps.append, monotonic=lambda: 10.)
        frame = client.get_candles('HNT-USD', interval='1h', start=START,
            end=START+pd.Timedelta(hours=1), as_of=START+pd.Timedelta(days=1))
        self.assertTrue(frame.empty)
        self.assertIn(2., sleeps)
        self.assertTrue(all(r.closed for r in responses))
        client, session = self.client([requests.Timeout(), Response([])])
        client.get_candles('HNT-USD', interval='1h', start=START, end=START+pd.Timedelta(hours=1), as_of=START+pd.Timedelta(days=1))
        self.assertEqual(len(session.calls), 2)

    def test_retry_after_long_wait_is_not_ignored_and_exhaustion_bounded(self):
        client, session = self.client([Response(status=429, headers={'Retry-After': '1000'})])
        with self.assertRaises(CoinbaseError):
            client.get_product('HNT-USD')
        self.assertEqual(len(session.calls), 1)
        with self.assertRaises(CoinbaseError):
            client.get_product('HNT-USD')
        self.assertEqual(len(session.calls), 1)
        client, session = self.client([Response(status=500)]*3)
        with self.assertRaises(CoinbaseError):
            client.get_product('HNT-USD')
        self.assertEqual(len(session.calls), 3)

    def test_pacing_and_http_date_retry_after(self):
        now = [0.]
        waits = []
        def sleep(seconds):
            waits.append(seconds)
            now[0] += seconds
        session = Session([Response({'id': 'HNT-USD'}), Response({'id': 'HNT-USD'})])
        with CoinbaseClient(session=session, sleep=sleep, monotonic=lambda: now[0]) as client:
            client.get_product('HNT-USD')
            client.get_product('HNT-USD')
        self.assertGreaterEqual(sum(waits), .15)
        future = format_datetime(datetime.now(timezone.utc)+pd.Timedelta(seconds=5))
        client, _ = self.client([Response(status=429, headers={'Retry-After': future}), Response({'id': 'HNT-USD'})], sleep=sleep, monotonic=lambda: now[0])
        self.assertEqual(client.get_product('HNT-USD')['id'], 'HNT-USD')
        self.assertTrue(any(w > 3 for w in waits))

    def test_product_rejection_and_identity(self):
        for response in (Response(status=404), Response({'id': 'OTHER-USD'})):
            client, _ = self.client([response])
            with self.assertRaises((CoinbaseError, CoinbaseResponseError)):
                client.get_product('HNT-USD')


if __name__ == '__main__':
    unittest.main()
