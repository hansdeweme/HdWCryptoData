# test_binance_vision_client.py
# Copyright (c) 2025, 2026 Hans De Weme
# Licensed under the MIT License (https://opensource.org/licenses/MIT).
# part of the HdW_crypto_data Project

import hashlib
import shutil
import unittest
from pathlib import Path
# local imports
from hdw_crypto_data.binance_vision_client import (
    BinanceChecksumMismatchError,
    _parse_checksum,
    validate_binance_url,
    verify_file_checksum,
)


class FakeChecksumResponse:
    def __init__(self, text: str):
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def raise_for_status(self):
        return None


class FakeChecksumSession:
    def __init__(self, checksum_text: str):
        self.checksum_text = checksum_text

    def get(self, url, timeout, verify):
        return FakeChecksumResponse(self.checksum_text)


class BinanceVisionClientTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path.cwd() / "tmp_binance_vision_client_test"
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        self.temp_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_checksum_parsing_accepts_binance_checksum_format(self):
        checksum = "a" * 64

        self.assertEqual(_parse_checksum(f"{checksum}  archive.zip\n"), checksum)

    def test_checksum_parsing_rejects_invalid_checksum(self):
        with self.assertRaises(ValueError):
            _parse_checksum("not-a-sha256 archive.zip")

    def test_checksum_mismatch_raises_specific_error(self):
        archive_path = self.temp_dir / "archive.zip"
        archive_path.write_bytes(b"archive contents")
        wrong_checksum = hashlib.sha256(b"different contents").hexdigest()
        session = FakeChecksumSession(f"{wrong_checksum}  archive.zip\n")

        with self.assertRaises(BinanceChecksumMismatchError):
            verify_file_checksum(
                session,
                "https://data.binance.vision/data/spot/daily/klines/BONKUSDT/1h/archive.zip",
                archive_path,
            )

    def test_url_rejection_blocks_non_https_or_unexpected_hosts(self):
        with self.assertRaisesRegex(ValueError, "must use HTTPS"):
            validate_binance_url("http://data.binance.vision/data/file.zip")
        with self.assertRaisesRegex(ValueError, "Unexpected download host"):
            validate_binance_url("https://example.com/data/file.zip")

        validate_binance_url("https://data.binance.vision/data/file.zip")


if __name__ == "__main__":
    unittest.main()
