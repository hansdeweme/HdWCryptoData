"""Shared persistent index tests, entirely offline and synthetic."""
import json
import os
import sqlite3
import threading
import time
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from hdw_crypto_data import archive_manager as am
from hdw_crypto_data.archive_paths import archive_locations
from hdw_crypto_data.archive_index import (
    ArchiveIndex, ArchiveIndexError, ArchiveIndexIncompatibleError, notify_committed_file,
    notify_dataframe_committed_file,
)
from test_archive_manager import ArchiveFixture


class ArchiveIndexTests(ArchiveFixture):
    def test_retired_partial_period_warning_is_removed_from_cache(self):
        self.file()
        self.file("ETHUSDT")
        index = ArchiveIndex(self.root)
        index.verify_incremental()
        retired = ["range", "Observed dates do not span advertised dates (possibly partial data)"]
        other = ["timestamp", "1 rows with missing or invalid open timestamps"]
        with index.transaction() as connection:
            connection.execute("DELETE FROM index_metadata WHERE key='partial_period_warning_retired'")
            connection.execute("UPDATE archive_file SET validation_status='Warning', issues_json=? WHERE symbol='BTCUSDT'", (json.dumps([retired]),))
            connection.execute("UPDATE archive_file SET validation_status='Warning', issues_json=? WHERE symbol='ETHUSDT'", (json.dumps([retired, other]),))
        fresh = ArchiveIndex(self.root)
        records = {record.symbol: record for record in fresh.load_inventory().files}
        self.assertEqual(records["BTCUSDT"].status, "OK")
        self.assertEqual(records["BTCUSDT"].issues, ())
        self.assertEqual(records["ETHUSDT"].status, "Warning")
        self.assertEqual([issue.code for issue in records["ETHUSDT"].issues], ["timestamp"])

    def test_layout_schema_summary_and_connection_settings(self):
        first, second = self.file(), self.file("ETHUSDT")
        index = ArchiveIndex.for_spot_root(self.root)
        self.assertEqual(index.path, self.base / ".hdw_archive" / "archive_index.sqlite")
        self.assertFalse(index.path.is_relative_to(self.root))
        with index.connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA busy_timeout").fetchone()[0], 1500)
        result = index.verify_incremental()
        self.assertTrue(result.complete)
        self.assertEqual(result.new, 2)
        summary = index.list_asset_summaries()[0]
        self.assertEqual((summary.symbol, summary.interval, summary.file_count, summary.row_count), ("BTCUSDT", "1h", 1, 2))
        self.assertEqual(summary.size_bytes, first.stat().st_size)
        self.assertEqual(summary.status, "OK")
        metadata = index.metadata()
        self.assertEqual(metadata["reconciliation_complete"], "true")
        self.assertTrue(metadata["last_incremental"] and metadata["last_deep"])
        self.assertEqual(metadata["archive_root"], str(self.root))
        # A fresh instance is a persistent cache, with no file opens or stat calls per row.
        fresh = ArchiveIndex(self.root)
        with patch.object(am, "_inspect", side_effect=AssertionError("reread")), \
                patch.object(am, "_identity", side_effect=AssertionError("cached files statted")):
            self.assertEqual(fresh.load_inventory(), result.inventory)

    def test_incremental_detects_new_size_mtime_deleted_and_manually_copied(self):
        first, second = self.file(), self.file("ETHUSDT")
        index = ArchiveIndex(self.root)
        index.verify_incremental()
        with patch.object(am, "_inspect", wraps=am._inspect) as inspect:
            unchanged = index.verify_incremental()
            inspect.assert_not_called()
        self.assertEqual((unchanged.unchanged, unchanged.inspected), (2, 0))
        first.write_text(first.read_text() + "invalid,1\n")
        with patch.object(am, "_inspect", wraps=am._inspect) as inspect:
            changed = index.verify_incremental()
            self.assertEqual(inspect.call_count, 1)
        self.assertEqual(changed.changed, 1)
        stamp = second.stat().st_mtime_ns
        os.utime(second, ns=(stamp, stamp + 1000000000))
        with patch.object(am, "_inspect", wraps=am._inspect) as inspect:
            changed = index.verify_incremental()
            self.assertEqual(inspect.call_count, 1)
        self.assertEqual(changed.changed, 1)
        new = self.file("SOLUSDT")
        first.unlink()
        result = index.verify_incremental()
        self.assertEqual((result.new, result.missing), (1, 1))
        self.assertEqual({f.path for f in result.inventory.files}, {new, second})

    def test_targeted_deep_rescan_ignores_unchanged_fingerprint(self):
        first, second = self.file(), self.file("ETHUSDT")
        index = ArchiveIndex(self.root)
        index.verify_incremental()
        with patch.object(am, "_inspect", wraps=am._inspect) as inspect:
            result = index.deep_rescan(("BTCUSDT", "1h"))
            self.assertEqual([c.args[0].path for c in inspect.call_args_list], [first])
        self.assertEqual(result.inspected, 1)

    def test_interruption_leaves_incomplete_state_and_can_recover(self):
        self.file()
        index = ArchiveIndex(self.root)
        index.verify_incremental()
        with self.assertRaises(InterruptedError):
            index.verify_incremental(cancelled=lambda: True)
        self.assertEqual(ArchiveIndex(self.root).metadata()["reconciliation_complete"], "false")
        self.assertTrue(index.verify_incremental().complete)

    def test_change_during_inspection_keeps_pending_and_eventually_repairs(self):
        path = self.file()
        index = ArchiveIndex(self.root)
        original = am._inspect
        def inspect(record, root, cancelled):
            result = original(record, root, cancelled)
            path.write_text("new content")
            return result
        with patch.object(am, "_inspect", inspect):
            result = index.verify_incremental()
        self.assertFalse(result.complete)
        self.assertTrue(result.warnings)
        self.assertEqual(index.load_inventory().files, ())
        self.assertTrue(index.verify_incremental().complete)

    def test_file_removed_between_inventory_and_inspection_is_safe(self):
        path = self.file()
        index = ArchiveIndex(self.root)
        original = am._inspect
        def inspect(record, root, cancelled):
            path.unlink()
            return original(record, root, cancelled)
        with patch.object(am, "_inspect", inspect):
            result = index.verify_incremental()
        self.assertFalse(result.complete)
        self.assertEqual(index.load_inventory().files, ())
        self.assertTrue(index.verify_incremental().complete)

    def test_version_zero_migration_and_transactional_failed_migration(self):
        self.file()
        index = ArchiveIndex(self.root)
        index.verify_incremental()
        with index.connection() as connection:
            connection.execute("ALTER TABLE archive_file DROP COLUMN revision")
            connection.execute("PRAGMA user_version=0")
        migrated = ArchiveIndex(self.root)
        self.assertEqual(len(migrated.load_inventory().files), 1)
        with migrated.connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
            connection.execute("ALTER TABLE archive_file RENAME COLUMN identity_json TO invalid_identity")
            connection.execute("PRAGMA user_version=0")
        with self.assertRaises(ArchiveIndexIncompatibleError):
            ArchiveIndex(self.root)
        with closing(sqlite3.connect(index.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM archive_file").fetchone()[0], 1)

    def test_unknown_future_schema_requires_explicit_rebuild_and_keeps_diagnostics(self):
        path = self.file()
        before = path.read_bytes()
        index = ArchiveIndex(self.root)
        with index.connection() as connection:
            connection.execute("PRAGMA user_version=99")
        with self.assertRaises(ArchiveIndexIncompatibleError):
            ArchiveIndex(self.root)
        result = ArchiveIndex.recover_and_rebuild(self.root)
        self.assertTrue(result.complete)
        self.assertTrue((index.path.parent / "archive_index.diagnostic.sqlite").exists())
        self.assertEqual(path.read_bytes(), before)

    def test_corruption_empty_or_missing_database_recovery(self):
        path = self.file()
        locations = archive_locations(self.root)
        locations.metadata.mkdir()
        locations.index.write_bytes(b"this is not SQLite")
        with self.assertRaises(ArchiveIndexError):
            ArchiveIndex(self.root)
        self.assertTrue(ArchiveIndex.recover_and_rebuild(self.root).complete)
        index = ArchiveIndex(self.root)
        index.path.unlink()
        self.assertEqual(ArchiveIndex(self.root).load_inventory().files, ())
        self.assertTrue(ArchiveIndex(self.root).verify_incremental().complete)
        self.assertEqual(path.read_text().count("\n"), 2)

    def test_rebuild_repairs_malformed_cached_payload_without_reading_it(self):
        self.file()
        index = ArchiveIndex(self.root)
        index.verify_incremental()
        with index.connection() as connection:
            connection.execute("UPDATE archive_file SET issues_json='invalid JSON'")
        with self.assertRaises(ValueError):
            index.load_inventory()
        self.assertTrue(index.rebuild().complete)
        self.assertEqual(index.load_inventory().files[0].row_count, 2)

    def test_failed_full_rebuild_preserves_previous_records_and_quarantine_history(self):
        first = self.file()
        index = ArchiveIndex(self.root)
        index.verify_incremental()
        second = self.file("ETHUSDT")
        operation = am.execute_quarantine(am.build_cleanup_plan(am.scan_archive(self.root), [second]))
        before = index.load_inventory()
        original = am._inspect
        def inspect(record, root, cancelled):
            if record.path == first:
                raise OSError("read failure")
            return original(record, root, cancelled)
        with patch.object(am, "_inspect", inspect), self.assertRaises(OSError):
            index.rebuild()
        self.assertEqual(index.load_inventory(), before)
        self.assertTrue(operation.manifest_path.exists())
        self.assertTrue(index.rebuild().complete)

    def test_readonly_or_unavailable_metadata_does_not_break_committed_archive(self):
        path = self.file()
        with patch("hdw_crypto_data.archive_index.ArchiveIndex", side_effect=PermissionError("read only")), \
                self.assertLogs("hdw_crypto_data.archive_index", level="WARNING") as log:
            notify_committed_file(path)
        self.assertTrue(path.exists())
        self.assertIn("verification required", log.output[0])

    def test_writer_known_dataframe_metadata_avoids_csv_reread(self):
        import pandas as pd
        path = self.file()
        frame = pd.DataFrame({"open_time": [1767225600000, 1767308400000]})
        with patch.object(am, "_inspect", side_effect=AssertionError("writer reread")):
            notify_dataframe_committed_file(path, frame)
        record = ArchiveIndex(self.root).load_inventory().files[0]
        self.assertEqual(record.row_count, 2)
        self.assertEqual(record.observed.end.hour, 23)

    def test_writer_replacement_updates_fingerprint_and_invalid_metadata_is_warning(self):
        path = self.file()
        notify_committed_file(path)
        before = ArchiveIndex(self.root).load_inventory().files[0]
        path.write_text(path.read_text() + "invalid,1\n")
        notify_committed_file(path)
        after = ArchiveIndex(self.root).load_inventory().files[0]
        self.assertNotEqual(before.identity, after.identity)
        self.assertEqual(after.row_count, 3)
        import pandas as pd
        with self.assertLogs("hdw_crypto_data.archive_index", level="WARNING"):
            notify_dataframe_committed_file(path, pd.DataFrame({"open_time": ["invalid"]}))
        self.assertTrue(path.exists())

    def test_downloader_indexes_only_successful_csv_commit_and_cache_failure_is_warning(self):
        import datetime
        import zipfile
        from hdw_crypto_data.binance_vision_dumper import _download_and_extract_task, ArchiveDownloadStatus
        def download(session, url, destination):
            destination.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(destination, "w") as archive:
                archive.writestr("BTCUSDT-1h-2026-01-01.csv", "1767225600000,1\n1767308400000,1\n")
        args = ("https://data.binance.vision/data", str(self.root), "spot", "klines", "1h", "BTCUSDT", datetime.date(2026, 1, 1), "daily")
        with patch("hdw_crypto_data.binance_vision_dumper.create_binance_session"), \
                patch("hdw_crypto_data.binance_vision_dumper.download_file", download), \
                patch("hdw_crypto_data.binance_vision_dumper.verify_file_checksum"):
            result = _download_and_extract_task(*args)
        self.assertEqual(result.status, ArchiveDownloadStatus.DOWNLOADED)
        self.assertEqual(ArchiveIndex(self.root).load_inventory().files[0].row_count, 2)
        path = self.root / "daily/klines/BTCUSDT/1h/BTCUSDT-1h-2026-01-01.csv"
        path.unlink()
        with patch("hdw_crypto_data.binance_vision_dumper.create_binance_session"), \
                patch("hdw_crypto_data.binance_vision_dumper.download_file", download), \
                patch("hdw_crypto_data.binance_vision_dumper.verify_file_checksum"), \
                patch("hdw_crypto_data.archive_index.ArchiveIndex", side_effect=ArchiveIndexError("locked")), \
                self.assertLogs("hdw_crypto_data.archive_index", level="WARNING"):
            result = _download_and_extract_task(*args)
        self.assertEqual(result.status, ArchiveDownloadStatus.DOWNLOADED)
        self.assertTrue(path.exists())
        path.unlink()
        with patch("hdw_crypto_data.binance_vision_dumper.create_binance_session"), \
                patch("hdw_crypto_data.binance_vision_dumper.download_file", download), \
                patch("hdw_crypto_data.binance_vision_dumper.verify_file_checksum"), \
                patch.object(Path, "replace", side_effect=OSError("commit failed")), \
                patch("hdw_crypto_data.archive_index.notify_committed_file") as notify:
            result = _download_and_extract_task(*args)
            notify.assert_not_called()
        self.assertEqual(result.status, ArchiveDownloadStatus.EXTRACTION_FAILURE)
        self.assertFalse(path.exists())

    def test_builder_commit_indexes_writer_known_rows_without_reopening(self):
        import pandas as pd
        from unittest.mock import Mock
        from hdw_crypto_data.total_dataset_builder import TotalDatasetBuilder
        source = Mock()
        source.fetch_recent_klines.return_value = pd.DataFrame({"open_time": [1767225600000, 1767308400000], "close_time": [0, 0]})
        builder = TotalDatasetBuilder("BTCUSDT", {"full_spot": str(self.root)}, recent_source=source)
        builder.current_dir = str(self.root)
        with patch.object(am, "_inspect", side_effect=AssertionError("writer reopened CSV")):
            self.assertTrue(builder.collect_data(open_candle="include"))
        self.assertEqual(ArchiveIndex(self.root).load_inventory().files[0].row_count, 2)
        with patch("pandas.DataFrame.to_csv", side_effect=OSError("partial write")), \
                patch("hdw_crypto_data.archive_index.notify_dataframe_committed_file") as notify, \
                self.assertRaises(OSError):
            builder.collect_data(open_candle="include")
        notify.assert_not_called()
        self.assertTrue(Path(builder.current_data).exists())
        self.assertFalse(Path(builder.current_data + ".part").exists())

    def test_concurrent_read_and_writer_upserts_are_thread_owned(self):
        self.file()
        index = ArchiveIndex(self.root)
        index.verify_incremental()
        def writer(number):
            path = self.file(f"SYM{number}USDT")
            ArchiveIndex(self.root).upsert_committed_file(path)
            return path
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(writer, i) for i in range(6)]
            index.list_asset_summaries()
            created = [f.result() for f in futures]
        self.assertEqual(len(index.load_inventory().files), 7)
        self.assertTrue(index.verify_incremental().complete)

    def test_concurrent_writer_revision_is_not_overwritten_by_reconciliation(self):
        path = self.file()
        index = ArchiveIndex(self.root)
        index.verify_incremental()
        original = am.scan_archive
        def scan(*args, **kwargs):
            inventory = original(*args, **kwargs)
            path.write_text(path.read_text() + "invalid,1\n")
            ArchiveIndex(self.root).upsert_committed_file(path)
            return inventory
        with patch.object(am, "scan_archive", scan):
            result = index.verify_incremental()
        self.assertFalse(result.complete)
        self.assertEqual(index.load_inventory().files[0].row_count, 3)
        self.assertTrue(index.verify_incremental().complete)

    def test_busy_database_preserves_files_and_does_not_trigger_corruption_recovery(self):
        path = self.file()
        index = ArchiveIndex(self.root, busy_timeout_ms=25)
        with index.connection() as locked:
            locked.execute("BEGIN IMMEDIATE")
            with self.assertRaises(ArchiveIndexError):
                index.upsert_committed_file(path)
            # WAL readers still work with a writer transaction open.
            self.assertEqual(index.list_asset_summaries(), ())
            locked.execute("ROLLBACK")
        self.assertTrue(path.exists())
        self.assertTrue(index.verify_incremental().complete)

    def test_quarantine_partial_restore_and_conflicts_keep_active_index_truthful(self):
        first, second = self.file(), self.file("ETHUSDT")
        index = ArchiveIndex(self.root)
        index.verify_incremental()
        plan = am.build_cleanup_plan(am.scan_archive(self.root), [first, second])
        second.write_text("changed after plan")
        result = am.execute_quarantine(plan)
        self.assertEqual([f.path for f in index.load_inventory().files], [second])
        first.write_text("restore conflict")
        restored = am.restore_quarantine_operation(result.operation_id, archive_root=self.root)
        self.assertFalse(restored.successful)
        self.assertEqual([f.path for f in index.load_inventory().files], [second])
        first.unlink()
        self.assertTrue(am.restore_quarantine_operation(result.operation_id, archive_root=self.root).successful)
        self.assertEqual({f.path for f in index.load_inventory().files}, {first, second})

    def test_legacy_quarantine_is_reused_and_metadata_staging_is_excluded(self):
        path = self.file()
        legacy = self.root.with_name("spot-quarantine")
        legacy.mkdir()
        self.assertEqual(archive_locations(self.root).quarantine, legacy)
        result = am.execute_quarantine(am.build_cleanup_plan(am.scan_archive(self.root), [path]))
        self.assertTrue(result.manifest_path.is_relative_to(legacy))
        self.assertTrue(am.scan_quarantine(self.root).assets)
        self.assertTrue(am.restore_quarantine_operation(result.operation_id, archive_root=self.root).successful)
        for directory in (".hdw_archive", "quarantine", "hdw-work"):
            nested = self.root / directory
            nested.mkdir()
            (nested / "noise.csv").write_text("not data")
        path.with_suffix(".csv.part").write_text("partial")
        self.assertEqual([f.path for f in am.scan_archive(self.root).files], [path])

    def test_cache_never_authorizes_cleanup_of_missing_file(self):
        path = self.file()
        index = ArchiveIndex(self.root)
        index.verify_incremental()
        cached = index.load_inventory()
        path.unlink()
        with self.assertRaises(OSError):
            am.build_cleanup_plan(cached, [path])

    def test_unchanged_archive_performance_and_zero_content_rereads(self):
        for number in range(40):
            content = "".join(f"{1767225600000 + row * 3600000},1,2,0,1,10\n" for row in range(240))
            self.file(f"BENCH{number}USDT", content=content)
        index = ArchiveIndex(self.root)
        start = time.perf_counter()
        cold = index.verify_incremental()
        cold_seconds = time.perf_counter() - start
        with patch.object(am, "_inspect", side_effect=AssertionError("unchanged contents reread")):
            start = time.perf_counter()
            index.load_inventory()
            cached_seconds = time.perf_counter() - start
            start = time.perf_counter()
            warm = index.verify_incremental()
            warm_seconds = time.perf_counter() - start
        self.assertEqual((cold.inspected, warm.inspected, warm.unchanged), (40, 0, 40))
        print(f"\nINDEX BENCHMARK 40 files / 9600 rows: cold={cold_seconds:.4f}s cached={cached_seconds:.4f}s warm={warm_seconds:.4f}s; warm CSV rereads=0")
