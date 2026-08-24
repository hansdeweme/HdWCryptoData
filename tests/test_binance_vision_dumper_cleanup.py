# test_binance_vision_dumper_cleanup.py
# Copyright (c) 2025, 2026 Hans De Weme
# Licensed under the MIT License (https://opensource.org/licenses/MIT).
# part of the HdW_crypto_data Project
import datetime
import shutil
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import requests

from hdw_crypto_data.binance_vision_client import BinanceChecksumMismatchError
from hdw_crypto_data.binance_vision_dumper import ArchiveDownloadStatus, _download_and_extract_task


class DownloadCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path.cwd() / "tmp_download_cleanup_test"
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        self.temp_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _run_task(self):
        return _download_and_extract_task(
            "https://data.binance.vision/data",
            self.temp_dir,
            "spot",
            "klines",
            "1h",
            "BONKUSDT",
            datetime.date(2026, 8, 21),
            "daily",
        )

    def test_checksum_failure_removes_downloaded_zip(self):
        expected_zip = (
            self.temp_dir
            / "spot"
            / "daily"
            / "klines"
            / "BONKUSDT"
            / "1h"
            / "BONKUSDT-1h-2026-08-21.zip"
        )

        def fake_download(session, url, destination):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"partial archive")

        def fake_verify(session, url, archive_path):
            raise BinanceChecksumMismatchError("checksum mismatch")

        with (
            patch("hdw_crypto_data.binance_vision_dumper.create_binance_session", return_value=object()),
            patch("hdw_crypto_data.binance_vision_dumper.download_file", side_effect=fake_download),
            patch("hdw_crypto_data.binance_vision_dumper.verify_file_checksum", side_effect=fake_verify),
        ):
            result = self._run_task()

        self.assertEqual(result.status, ArchiveDownloadStatus.CHECKSUM_MISMATCH, result.error)
        self.assertFalse(expected_zip.exists())

    def test_safe_extraction_uses_expected_member_only(self):
        expected_csv = (
            self.temp_dir
            / "spot"
            / "daily"
            / "klines"
            / "BONKUSDT"
            / "1h"
            / "BONKUSDT-1h-2026-08-21.csv"
        )
        traversal_path = self.temp_dir / "evil.csv"

        def fake_download(session, url, destination):
            destination.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(destination, "w") as archive:
                archive.writestr("BONKUSDT-1h-2026-08-21.csv", "expected")
                archive.writestr("../../evil.csv", "unexpected")

        with (
            patch("hdw_crypto_data.binance_vision_dumper.create_binance_session", return_value=object()),
            patch("hdw_crypto_data.binance_vision_dumper.download_file", side_effect=fake_download),
            patch("hdw_crypto_data.binance_vision_dumper.verify_file_checksum", return_value=None),
        ):
            result = self._run_task()

        self.assertEqual(result.status, ArchiveDownloadStatus.DOWNLOADED, result.error)
        self.assertEqual(expected_csv.read_text(encoding="utf-8"), "expected")
        self.assertFalse(traversal_path.exists())

    def test_expected_member_absence_is_invalid_archive(self):
        def fake_download(session, url, destination):
            destination.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(destination, "w") as archive:
                archive.writestr("OTHER-1h-2026-08-21.csv", "unexpected")

        with (
            patch("hdw_crypto_data.binance_vision_dumper.create_binance_session", return_value=object()),
            patch("hdw_crypto_data.binance_vision_dumper.download_file", side_effect=fake_download),
            patch("hdw_crypto_data.binance_vision_dumper.verify_file_checksum", return_value=None),
        ):
            result = self._run_task()

        self.assertEqual(result.status, ArchiveDownloadStatus.INVALID_ARCHIVE)
        self.assertIn("Expected", result.error)

    def test_http_404_and_429_map_to_distinct_download_statuses(self):
        for status_code, expected_status in (
            (404, ArchiveDownloadStatus.NOT_FOUND),
            (429, ArchiveDownloadStatus.RATE_LIMITED),
        ):
            response = requests.Response()
            response.status_code = status_code
            error = requests.HTTPError(f"{status_code} error")
            error.response = response

            with (
                patch("hdw_crypto_data.binance_vision_dumper.create_binance_session", return_value=object()),
                patch("hdw_crypto_data.binance_vision_dumper.download_file", side_effect=error),
            ):
                result = self._run_task()

            self.assertEqual(result.status, expected_status)


if __name__ == "__main__":
    unittest.main()
