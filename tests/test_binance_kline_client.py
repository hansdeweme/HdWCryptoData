"""Deterministic public-client contract tests; no network or data-file writes."""
import json
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

import pandas as pd
import requests

from hdw_crypto_data import (BinanceKlineClient, BinanceKlineError,
                             BinanceKlineResponseError, BinanceRateLimitError)

MODULE = 'hdw_crypto_data.binance_kline_client'
BASE = 1_792_368_000_000


def row(opened=BASE, price='100'):
    return [opened, price, '102', '99', '101', '10', opened + 299_999,
            '1000', 5, '5', '500', '0']


def response(payload, status=200, headers=None):
    item = requests.Response()
    item.status_code = status
    item._content = json.dumps(payload).encode()
    item.headers.update(headers or {})
    item.close = Mock()
    return item


class BinanceKlineClientTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.
        self.sleep_calls = []
        self.cutoff = BASE + 600_000
        self.patches = [
            patch(f'{MODULE}.time.monotonic', side_effect=lambda: self.now),
            patch(f'{MODULE}.time.sleep', side_effect=self.sleep),
            patch(f'{MODULE}.time.time_ns', side_effect=lambda: self.cutoff * 1_000_000),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def sleep(self, seconds):
        self.sleep_calls.append(seconds)
        self.now += seconds

    def client(self, *responses, **kwargs):
        session = Mock()
        session.get.side_effect = responses
        return BinanceKlineClient(session=session, **kwargs), session

    def test_public_five_minute_contract_without_file_writes(self):
        client, session = self.client(response([row(BASE + 300_000), row()]))
        with patch('builtins.open', side_effect=AssertionError('Unexpected file I/O')):
            frame = client.get_klines(' pepeusdt ', interval='5m', limit=2)
        self.assertEqual(list(frame.columns), ['open', 'high', 'low', 'close', 'volume', 'number_of_trades'])
        self.assertEqual(str(frame.index.tz), 'UTC')
        self.assertEqual(frame.index.name, 'dt')
        self.assertTrue(frame.index.is_monotonic_increasing)
        self.assertTrue(frame.index.is_unique)
        self.assertEqual([str(t) for t in frame.dtypes], ['float64'] * 5 + ['int64'])
        self.assertEqual(len(frame), 2)
        self.assertEqual(session.get.call_args.kwargs['params'], {
            'symbol': 'PEPEUSDT', 'interval': '5m', 'limit': 3, 'endTime': self.cutoff - 1})
        self.assertTrue(frame.attrs['closed_only'])

    def test_close_boundary_is_strict_and_snapshot_is_frozen(self):
        for delta, expected in ((-1, 0), (0, 0), (1, 1)):
            with self.subTest(delta=delta):
                self.cutoff = BASE + 299_999 + delta
                client, _ = self.client(response([row()]))
                self.assertEqual(len(client.get_klines('BTCUSDT', interval='5m', limit=1)), expected)

    def test_explicit_open_candle_mode(self):
        self.cutoff = BASE + 1000
        client, session = self.client(response([row()]))
        frame = client.get_klines('BTCUSDT', interval='5m', limit=1, closed_only=False)
        self.assertEqual(len(frame), 1)
        self.assertFalse(frame.attrs['closed_only'])
        self.assertNotIn('endTime', session.get.call_args.kwargs['params'])

    def test_response_crossing_close_does_not_admit_partial_snapshot(self):
        self.cutoff = BASE + 299_998
        client, session = self.client()
        def delayed_response(*args, **kwargs):
            self.cutoff = BASE + 300_001
            return response([row()])
        session.get.side_effect = delayed_response
        frame = client.get_klines('BTCUSDT', interval='5m', limit=1)
        self.assertTrue(frame.empty)
        self.assertEqual(frame.attrs['retrieval_cutoff_ms'], BASE + 299_998)

    def test_empty_and_short_responses_have_stable_schema(self):
        for payload in ([], [row()]):
            with self.subTest(payload=payload):
                client, _ = self.client(response(payload))
                frame = client.get_klines('BTCUSDT')
                self.assertEqual(len(frame), len(payload))
                self.assertEqual(str(frame.index.tz), 'UTC')
                self.assertEqual(str(frame['number_of_trades'].dtype), 'int64')

    def test_thousand_closed_candles_page_back_without_losing_history(self):
        self.cutoff = BASE + 1000 * 300_000 + 1000
        recent = [row(BASE + i * 300_000) for i in range(1, 1001)]
        client, session = self.client(response(recent), response([row()]))
        frame = client.get_klines('BTCUSDT', interval='5m', limit=1000)
        self.assertEqual(len(frame), 1000)
        self.assertEqual(frame.index[0].value // 1_000_000, BASE)
        self.assertEqual(frame.index[-1].value // 1_000_000, BASE + 999 * 300_000)
        self.assertEqual(session.get.call_args.kwargs['params']['endTime'], BASE + 300_000 - 1)

    def test_identical_values_at_distinct_times_are_preserved(self):
        client, _ = self.client(response([row(), row(BASE + 300_000)]))
        self.assertEqual(len(client.get_klines('BTCUSDT', limit=2)), 2)

    def test_duplicate_timestamps_deduplicate_or_reject_conflict(self):
        client, _ = self.client(response([row(), row()]))
        self.assertEqual(len(client.get_klines('BTCUSDT', limit=2)), 1)
        client, _ = self.client(response([row(), row(price='101')]))
        with self.assertRaises(BinanceKlineResponseError):
            client.get_klines('BTCUSDT', limit=2)

    def test_malformed_payloads_and_invalid_values_are_rejected_without_retry(self):
        payloads = [{'code': -1121, 'msg': 'Invalid symbol'}, ['bad'], [[1, 2]]]
        for column, value in [(0, -1), (0, True), (0, 1.5), (0, 10**30), (6, 0),
                              (1, 'NaN'), (2, '100'), (3, '103'), (4, 'inf'),
                              (5, -1), (8, -1), (8, 1.5), (8, None)]:
            invalid = row()
            invalid[column] = value
            payloads.append([invalid])
        for payload in payloads:
            with self.subTest(payload=payload):
                client, session = self.client(response(payload))
                with self.assertRaises(BinanceKlineResponseError):
                    client.get_klines('BTCUSDT')
                self.assertEqual(session.get.call_count, 1)

    def test_invalid_json_is_distinct_from_transport_failure(self):
        invalid = response([])
        invalid._content = b'not JSON'
        client, _ = self.client(invalid)
        with self.assertRaises(BinanceKlineResponseError):
            client.get_klines('BTCUSDT')
        invalid.close.assert_called_once()

    def test_transport_and_server_errors_retry_with_backoff(self):
        client, session = self.client(requests.Timeout(), response({}, 503), response([row()]))
        self.assertEqual(len(client.get_klines('BTCUSDT')), 1)
        self.assertEqual(session.get.call_count, 3)
        self.assertEqual(self.sleep_calls, [0.5, 1.0])

    def test_permanent_http_errors_and_exhaustion(self):
        client, session = self.client(response({}, 400))
        with self.assertRaises(BinanceKlineError):
            client.get_klines('BTCUSDT')
        self.assertEqual(session.get.call_count, 1)
        client, session = self.client(requests.Timeout(), requests.Timeout(), max_attempts=2)
        with self.assertRaises(BinanceKlineError):
            client.get_klines('BTCUSDT')
        self.assertEqual(session.get.call_count, 2)

    def test_rate_limit_wait_is_respected(self):
        client, session = self.client(response({}, 429, {'Retry-After': '2'}), response([row()]))
        self.assertEqual(len(client.get_klines('BTCUSDT')), 1)
        self.assertEqual(session.get.call_count, 2)
        self.assertEqual(self.sleep_calls, [2.])

    def test_long_throttle_and_ip_ban_fail_without_retrying_early(self):
        for status in (418, 429):
            with self.subTest(status=status):
                client, session = self.client(response({}, status, {'Retry-After': '120'}))
                for _ in range(2):
                    with self.assertRaises(BinanceRateLimitError) as error:
                        client.get_klines('BTCUSDT')
                    self.assertEqual(error.exception.retry_after, 120.)
                self.assertEqual(session.get.call_count, 1)

    def test_malformed_retry_after_fails_conservatively(self):
        client, session = self.client(response({}, 429, {'Retry-After': 'nan'}))
        with self.assertRaises(BinanceRateLimitError) as error:
            client.get_klines('BTCUSDT')
        self.assertEqual(error.exception.retry_after, 60.)
        self.assertEqual(session.get.call_count, 1)

    def test_pacing_between_successful_calls(self):
        client, _ = self.client(response([]), response([]))
        client.get_klines('BTCUSDT')
        client.get_klines('ETHUSDT')
        self.assertEqual(self.sleep_calls, [0.25])

    def test_bad_arguments_do_not_issue_requests(self):
        client, session = self.client()
        for kwargs in ({'symbol': None}, {'symbol': 'BTC/USDT'}, {'symbol': 'BTCUSDT', 'interval': '5M'},
                       {'symbol': 'BTCUSDT', 'interval': []},
                       {'symbol': 'BTCUSDT', 'limit': 0}, {'symbol': 'BTCUSDT', 'limit': 1001},
                       {'symbol': 'BTCUSDT', 'limit': True}, {'symbol': 'BTCUSDT', 'limit': 1.5},
                       {'symbol': 'BTCUSDT', 'closed_only': 'yes'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                client.get_klines(**kwargs)
        session.get.assert_not_called()
        for kwargs in ({'max_attempts': 0}, {'timeout': None}, {'timeout': (5,)}, {'min_request_interval': -1},
                       {'backoff_factor': float('nan')}, {'max_retry_wait': True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                BinanceKlineClient(**kwargs)

    def test_session_ownership_and_closed_client(self):
        client, session = self.client()
        with client:
            pass
        session.close.assert_not_called()
        with self.assertRaises(RuntimeError):
            client.get_klines('BTCUSDT')
        with patch(f'{MODULE}.requests.Session') as factory:
            with BinanceKlineClient():
                pass
            factory.return_value.close.assert_called_once()

    def test_out_of_range_response_is_rejected(self):
        client, _ = self.client(response([row(self.cutoff)]))
        with self.assertRaises(BinanceKlineResponseError):
            client.get_klines('BTCUSDT')

    def test_public_import_does_not_load_gui_or_indicators(self):
        script = ('import sys; from hdw_crypto_data import BinanceKlineClient; '
                  'assert not any(n.split(".")[0] in {"PyQt6", "pandas_ta", "scipy", "ta"} for n in sys.modules)')
        subprocess.run([sys.executable, '-c', script], check=True, capture_output=True)


if __name__ == '__main__':
    unittest.main()
