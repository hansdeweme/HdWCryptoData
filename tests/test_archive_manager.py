"""Offline archive manager integration and filesystem safety regression tests."""
import json
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from hdw_crypto_data import archive_manager as am


class ArchiveFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "spot"
        self.root.mkdir()

    def file(self, symbol="BTCUSDT", date="2026-01-01", period="daily", content=None):
        path = self.root / period / "klines" / symbol / "1h" / f"{symbol}-1h-{date}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        if content is None:
            timestamp = int(datetime.fromisoformat(date + ("-01" if len(date) == 7 else "")).replace(tzinfo=timezone.utc).timestamp() * 1000)
            content = f"{timestamp},1,2,0,1,10\n{timestamp + 23 * 3600000},1,2,0,1,10\n"
        path.write_text(content, encoding="utf-8")
        return path

    def plan(self, paths):
        return am.build_cleanup_plan(am.scan_archive(self.root), paths)


class ScannerTests(ArchiveFixture):
    def test_deep_refresh_reuses_unchanged_reads_changed_and_detects_removed_files(self):
        first, second = self.file(), self.file("ETHUSDT")
        cached = am.scan_archive(self.root, "deep")
        with patch.object(am, "_inspect", wraps=am._inspect) as inspect:
            refreshed = am.scan_archive(self.root, "deep", cached_inventory=cached)
            inspect.assert_not_called()
        self.assertEqual(refreshed, cached)
        first.unlink()
        second.write_text("invalid timestamp,1\n")
        new = self.file("SOLUSDT")
        with patch.object(am, "_inspect", wraps=am._inspect) as inspect:
            refreshed = am.scan_archive(self.root, "deep", cached_inventory=cached)
        self.assertEqual({call.args[0].path for call in inspect.call_args_list}, {second, new})
        self.assertNotIn(first, {f.path for f in refreshed.files})
        self.assertEqual(next(f for f in refreshed.files if f.path == second).status, "Warning")
        self.assertEqual(next(f for f in refreshed.files if f.path == new).row_count, 2)

    def test_cache_from_different_root_or_fast_scan_does_not_skip_deep_read(self):
        first = self.file()
        fast = am.scan_archive(self.root)
        with patch.object(am, "_inspect", wraps=am._inspect) as inspect:
            cached = am.scan_archive(self.root, "deep", cached_inventory=fast)
            self.assertEqual(inspect.call_count, 1)
        other_root = self.base / "other" / "spot"
        copied = other_root / first.relative_to(self.root)
        copied.parent.mkdir(parents=True)
        copied.write_bytes(first.read_bytes())
        with patch.object(am, "_inspect", wraps=am._inspect) as inspect:
            other = am.scan_archive(other_root, "deep", cached_inventory=cached)
            self.assertEqual(inspect.call_count, 1)
        self.assertEqual(other.files[0].row_count, 2)

    def test_grouping_size_and_read_only_advertised_observed(self):
        first = self.file()
        second = self.file(date="2026-01-02")
        self.file("ETHUSDT")
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.rglob("*.csv")}
        fast = am.scan_archive(self.base)
        group = fast.assets[0]
        self.assertEqual((group.symbol, group.interval, group.file_count), ("BTCUSDT", "1h", 2))
        self.assertEqual(group.size_bytes, first.stat().st_size + second.stat().st_size)
        self.assertIsNone(group.coverage.start)
        self.assertIsNone(fast.files[0].observed.start)
        self.assertIsNotNone(fast.files[0].advertised.start)
        deep = am.validate_inventory(fast)
        self.assertEqual(deep.assets[0].observed_rows, 4)
        self.assertEqual(deep.assets[0].coverage.end.day, 2)
        self.assertEqual(deep.assets[0].status, "OK")
        after = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.rglob("*.csv")}
        self.assertEqual(before, after)

    def test_bad_empty_unsupported_invalid_and_mismatch(self):
        self.file(content="")
        self.file("ETHUSDT", content="open_time,open\ninvalid,1\n,2\n")
        self.file("SOLUSDT", content="0,1\n")
        malformed = self.file("BNBUSDT").with_name("bad.csv")
        malformed.write_text("hello")
        (self.root / "notes.txt").write_text("note")
        inventory = am.scan_archive(self.root, "deep")
        codes = {i.code for f in inventory.files for i in f.issues}
        self.assertTrue({"empty", "timestamp", "range", "filename", "unsupported"}.issubset(codes))
        self.assertEqual(len(inventory.files), 6)
        self.assertEqual(sum(a.file_count for a in inventory.assets), 4)

    def test_unreadable_does_not_abort(self):
        bad, good = self.file(), self.file("ETHUSDT")
        original = Path.open
        def open_file(path, *args, **kwargs):
            if path == bad:
                raise PermissionError("denied")
            return original(path, *args, **kwargs)
        with patch.object(Path, "open", open_file):
            inventory = am.scan_archive(self.root, "deep")
        self.assertEqual({f.path: f.status for f in inventory.files}, {bad: "Unreadable", good: "OK"})
        refreshed = am.scan_archive(self.root, "deep", cached_inventory=inventory)
        self.assertEqual({f.path: f.status for f in refreshed.files}, {bad: "OK", good: "OK"})

    def test_timestamp_seconds_milliseconds_microseconds(self):
        seconds = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
        self.file(content=f"open_time,open\n{seconds},1\n{(seconds + 3600) * 1000},1\n{(seconds + 23 * 3600) * 1000000},1\n")
        record = am.scan_archive(self.root, "deep").files[0]
        self.assertEqual(record.row_count, 3)
        self.assertEqual(record.observed.end.hour, 23)
        self.assertEqual(record.status, "OK")

    def test_monthly_and_directory_errors(self):
        self.file(date="2026-02", period="monthly")
        record = am.scan_archive(self.root, "deep").files[0]
        self.assertEqual(record.advertised.end.day, 28)
        self.assertEqual(record.status, "OK")
        def failed_walk(root, **kwargs):
            kwargs["onerror"](PermissionError("directory denied"))
            return iter(())
        with patch.object(am.os, "walk", failed_walk):
            inventory = am.scan_archive(self.root)
        self.assertEqual(inventory.issues[0].code, "directory")

    def test_partial_month_is_ok(self):
        timestamp = int(datetime(2026, 2, 15, tzinfo=timezone.utc).timestamp()) * 1000
        self.file(date="2026-02", period="monthly", content=f"{timestamp},1\n")
        record = am.scan_archive(self.root, "deep").files[0]
        self.assertEqual(record.status, "OK")
        self.assertEqual(record.issues, ())

    def test_invalid_root_mode_and_cancellation(self):
        self.file()
        for root in (self.base / "missing", self.base / "invalid"):
            with self.assertRaises((ValueError, OSError)):
                am.scan_archive(root)
        with self.assertRaises(ValueError):
            am.scan_archive(self.root, "bad")
        with self.assertRaises(InterruptedError):
            am.scan_archive(self.root, "deep", cancelled=lambda: True)


class CleanupTests(ArchiveFixture):
    def test_exact_immutable_plan_without_mutation(self):
        first, second = self.file(), self.file("ETHUSDT")
        inventory = am.scan_archive(self.root)
        plan = am.build_cleanup_plan(inventory, [first, first])
        self.assertEqual(len(plan.files), 1)
        self.assertEqual(plan.total_size_bytes, first.stat().st_size)
        self.assertEqual(plan.affected_groups, (("BTCUSDT", "1h"),))
        self.assertTrue(plan.warnings)
        self.assertFalse(plan.quarantine_root.exists())
        self.assertTrue(first.exists() and second.exists())

    def test_reject_outside_directory_missing_traversal_and_stale(self):
        path = self.file()
        outside = self.base / "outside.csv"
        outside.write_text("x")
        inventory = am.scan_archive(self.root)
        for selected in (outside, path.parent, self.root / "missing.csv", self.root / ".." / "outside.csv", self.root):
            with self.subTest(selected=selected), self.assertRaises((ValueError, OSError)):
                am.build_cleanup_plan(inventory, [selected])
        path.write_text("changed")
        with self.assertRaisesRegex(ValueError, "changed"):
            am.build_cleanup_plan(inventory, [path])
        with self.assertRaises(ValueError):
            am.build_cleanup_plan(inventory, [])

    def test_quarantine_restore_manifest_and_inventory_exclusion(self):
        first, second = self.file(), self.file("ETHUSDT")
        contents = first.read_bytes()
        plan = self.plan([first])
        result = am.execute_quarantine(plan)
        self.assertTrue(result.successful)
        self.assertFalse(first.exists())
        self.assertTrue(second.exists())
        self.assertEqual(am.scan_archive(self.root).assets[0].symbol, "ETHUSDT")
        manifest = json.loads(result.manifest_path.read_text())
        entry = manifest["files"][0]
        self.assertEqual(entry["original_path"], str(first))
        self.assertEqual(entry["quarantine_path"], str(plan.files[0].destination))
        self.assertEqual(entry["size_bytes"], len(contents))
        self.assertEqual(entry["outcome"], "moved")
        self.assertTrue(entry["timestamp"])
        restored = am.restore_quarantine_operation(result.operation_id, archive_root=self.root)
        self.assertTrue(restored.successful)
        self.assertEqual(first.read_bytes(), contents)
        updated = json.loads(result.manifest_path.read_text())
        self.assertEqual(updated["files"][0]["restore_history"][0]["outcome"], "restored")
        self.assertIn("timestamp", updated)

    def test_restore_collision_is_retryable_and_keeps_both(self):
        first = self.file()
        result = am.execute_quarantine(self.plan([first]))
        first.write_text("new data")
        restored = am.restore_quarantine_operation(result.operation_id, archive_root=self.root)
        self.assertFalse(restored.successful)
        self.assertEqual(first.read_text(), "new data")
        self.assertTrue(result.files[0].destination.exists())
        first.unlink()
        self.assertTrue(am.restore_quarantine_operation(result.operation_id, archive_root=self.root).successful)
        history = json.loads(result.manifest_path.read_text())["files"][0]["restore_history"]
        self.assertEqual([h["outcome"] for h in history], ["failed", "restored"])

    def test_partial_failure_and_stale_execution(self):
        first, second = self.file(), self.file("ETHUSDT")
        plan = self.plan([first, second])
        second.write_text("changed after confirmation")
        result = am.execute_quarantine(plan)
        self.assertFalse(result.successful)
        self.assertEqual([f.outcome for f in result.files], ["moved", "failed"])
        self.assertFalse(first.exists())
        self.assertTrue(second.exists())
        entries = json.loads(result.manifest_path.read_text())["files"]
        self.assertEqual(entries[1]["outcome"], "failed")
        self.assertIn("changed", entries[1]["error"])
        self.assertTrue(am.restore_quarantine_operation(result.operation_id, archive_root=self.root).successful)

    def test_collision_during_move_never_overwrites(self):
        first = self.file()
        plan = self.plan([first])
        original = am._move_no_overwrite
        def collide(source, destination, identity):
            destination.write_text("existing")
            original(source, destination, identity)
        with patch.object(am, "_move_no_overwrite", collide):
            result = am.execute_quarantine(plan)
        self.assertFalse(result.successful)
        self.assertTrue(first.exists())
        self.assertEqual(plan.files[0].destination.read_text(), "existing")
        with self.assertRaises(FileExistsError):
            am.execute_quarantine(plan)

    def test_manifest_failure_stops_further_moves_and_reports_outcomes(self):
        first, second = self.file(), self.file("ETHUSDT")
        plan = self.plan([first, second])
        original = am._write_manifest
        calls = 0
        def write(path, document):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("disk full")
            original(path, document)
        with patch.object(am, "_write_manifest", write):
            result = am.execute_quarantine(plan)
        self.assertEqual([f.outcome for f in result.files], ["moved", "not attempted"])
        self.assertTrue(result.errors)
        self.assertFalse(result.successful)
        self.assertTrue(second.exists())
        # Write-ahead pending entry allows recovery after a manifest failure.
        restored = am.restore_quarantine_operation(result.operation_id, archive_root=self.root)
        self.assertTrue(first.exists())
        self.assertEqual(restored.files[0].outcome, "restored")

    def test_bad_quarantine_forged_plan_and_manifest(self):
        first = self.file()
        inventory = am.scan_archive(self.root)
        for root in (self.root, self.root / "quarantine", self.base):
            with self.assertRaises(ValueError):
                am.build_cleanup_plan(inventory, [first], root)
        plan = self.plan([first])
        forged = replace(plan, files=(replace(plan.files[0], destination=self.base / "escape"),))
        with self.assertRaises(ValueError):
            am.execute_quarantine(forged)
        result = am.execute_quarantine(plan)
        document = json.loads(result.manifest_path.read_text())
        document["files"][0]["relative_path"] = "../outside.csv"
        result.manifest_path.write_text(json.dumps(document))
        restored = am.restore_quarantine_operation(result.operation_id, archive_root=self.root)
        self.assertFalse(restored.successful)
        self.assertTrue(plan.files[0].destination.exists())
        with self.assertRaises(ValueError):
            am.restore_quarantine_operation("../escape", archive_root=self.root)

    def test_symlink_escape_scan_plan_execution_restore(self):
        first = self.file()
        outside = self.base / "outside"
        outside.mkdir()
        link = self.root / "linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"Symlink creation unavailable: {exc}")
        outside_file = outside / "data.csv"
        outside_file.write_text("x")
        inventory = am.scan_archive(self.root)
        self.assertTrue(inventory.issues)
        with self.assertRaises(ValueError):
            am.build_cleanup_plan(inventory, [link / "data.csv"])
        plan = self.plan([first])
        first.unlink()
        first.symlink_to(outside_file)
        with self.assertRaises(ValueError):
            am.execute_quarantine(plan)

    def test_link_policy_without_platform_symlink_privilege(self):
        first = self.file()
        inventory = am.scan_archive(self.root)
        plan = self.plan([first])
        original = am._is_link
        def linked(path):
            return path == first or original(path)
        with patch.object(am, "_is_link", linked):
            scanned = am.scan_archive(self.root)
            self.assertEqual(scanned.files[0].status, "Unreadable")
            with self.assertRaises(ValueError):
                am.build_cleanup_plan(inventory, [first])
            with self.assertRaises(ValueError):
                am.execute_quarantine(plan)
        result = am.execute_quarantine(plan)
        quarantined = result.files[0].destination
        with patch.object(am, "_is_link", lambda path: path == quarantined or original(path)):
            restored = am.restore_quarantine_operation(result.operation_id, archive_root=self.root)
        self.assertFalse(restored.successful)
        self.assertFalse(first.exists())
        self.assertTrue(quarantined.exists())

    def test_windows_reparse_points_detected_without_new_pathlib_api(self):
        from types import SimpleNamespace
        with patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=0o40755, st_file_attributes=0x400)):
            self.assertTrue(am._is_link(self.root))

    def test_archive_manifest_filename_does_not_collide_with_operation_metadata(self):
        path = self.root / "manifest.json"
        path.write_text("user data")
        result = am.execute_quarantine(self.plan([path]))
        self.assertTrue(result.successful)
        self.assertEqual(result.files[0].destination.read_text(), "user data")
        self.assertEqual(json.loads(result.manifest_path.read_text())["version"], 1)
        self.assertTrue(am.restore_quarantine_operation(result.operation_id, archive_root=self.root).successful)

    @unittest.skipUnless(os.name == "nt", "Windows junction test")
    def test_real_windows_junction_is_excluded_and_rejected(self):
        import subprocess
        self.file()
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "data.csv").write_text("outside")
        link = self.root / "linked"
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                       check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        self.addCleanup(lambda: link.rmdir() if link.exists() else None)
        inventory = am.scan_archive(self.root)
        self.assertEqual(len(inventory.files), 1)
        self.assertTrue(inventory.issues)
        with self.assertRaises(ValueError):
            am.build_cleanup_plan(inventory, [link / "data.csv"])


if __name__ == "__main__":
    unittest.main()
