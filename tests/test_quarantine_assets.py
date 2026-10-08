"""Whole-asset permanent deletion tests use synthetic quarantine only."""
import json
from pathlib import Path
from unittest.mock import patch

from hdw_crypto_data import archive_manager as am
from test_archive_manager import ArchiveFixture


class QuarantineAssetTests(ArchiveFixture):
    def quarantine(self, paths):
        return am.execute_quarantine(am.build_cleanup_plan(am.scan_archive(self.root), paths))

    def test_overview_and_delete_complete_asset_across_operations_and_intervals(self):
        self.assertEqual(am.scan_quarantine(self.root).assets, ())
        first, other = self.file(), self.file("ETHUSDT")
        one = self.quarantine([first, other])
        second = self.file(date="2026-01-02")
        daily = second.with_name(second.name.replace("1h", "1d"))
        daily = daily.parent.parent / "1d" / daily.name
        daily.parent.mkdir()
        second.rename(daily)
        two = self.quarantine([daily])
        active = self.file(date="2026-01-03")
        before = active.read_bytes()
        inventory = am.scan_quarantine(self.root)
        self.assertFalse(inventory.issues)
        self.assertEqual([a.symbol for a in inventory.assets], ["BTCUSDT", "ETHUSDT"])
        asset = inventory.assets[0]
        self.assertEqual(asset.intervals, ("1d", "1h"))
        self.assertEqual(len(asset.files), 2)
        plan = am.build_asset_deletion_plan(inventory, "BTCUSDT")
        self.assertTrue(all(f.path.exists() for f in plan.asset.files))
        result = am.execute_asset_deletion(plan)
        self.assertEqual([f.outcome for f in result.files], ["deleted", "deleted"])
        self.assertEqual(result.bytes_deleted, asset.size_bytes)
        self.assertEqual(active.read_bytes(), before)
        self.assertTrue(one.files[1].destination.exists())
        self.assertEqual([a.symbol for a in am.scan_quarantine(self.root).assets], ["ETHUSDT"])
        for operation in (one, two):
            self.assertTrue(operation.manifest_path.exists())
            entry = json.loads(operation.manifest_path.read_text())["files"][0]
            self.assertEqual(entry["outcome"], "deleted")
            self.assertEqual(entry["quarantine_outcome"], "moved")
            self.assertEqual(entry["deletion_history"][-1]["outcome"], "deleted")
            self.assertTrue(entry["deletion_history"][-1]["timestamp"])
        # Restore skips permanently deleted entries and still restores the other asset.
        restored = am.restore_quarantine_operation(one.operation_id, archive_root=self.root)
        self.assertEqual(len(restored.files), 1)
        self.assertTrue(other.exists())

    def test_changed_asset_or_new_operation_after_preview_rejected(self):
        operation = self.quarantine([self.file()])
        plan = am.build_asset_deletion_plan(am.scan_quarantine(self.root), "BTCUSDT")
        self.quarantine([self.file(date="2026-01-02")])
        with self.assertRaisesRegex(ValueError, "changed since preview"):
            am.execute_asset_deletion(plan)
        self.assertTrue(operation.files[0].destination.exists())
        operation.files[0].destination.write_text("changed")
        inventory = am.scan_quarantine(self.root)
        self.assertTrue(inventory.issues)
        with self.assertRaises(ValueError):
            am.build_asset_deletion_plan(inventory, "BTCUSDT")

    def test_corrupt_traversal_untracked_and_linked_manifests_block_deletion(self):
        operation = self.quarantine([self.file()])
        original = operation.manifest_path.read_text()
        for text in ("not json", "[]", original.replace('daily', '..')):
            operation.manifest_path.write_text(text)
            inventory = am.scan_quarantine(self.root)
            self.assertTrue(inventory.issues)
            with self.assertRaises(ValueError):
                am.build_asset_deletion_plan(inventory, "BTCUSDT")
        operation.manifest_path.write_text(original)
        extra = operation.manifest_path.parent / "files" / "unknown.txt"
        extra.write_text("untracked")
        self.assertTrue(am.scan_quarantine(self.root).issues)
        extra.unlink()
        linked = operation.files[0].destination
        original_link = am._is_link
        with patch.object(am, "_is_link", lambda path: path == linked or original_link(path)):
            self.assertTrue(am.scan_quarantine(self.root).issues)
        self.assertTrue(linked.exists())

    def test_partial_deletion_reports_exact_failures_and_preserves_history(self):
        operation = self.quarantine([self.file(), self.file(date="2026-01-02")])
        plan = am.build_asset_deletion_plan(am.scan_quarantine(self.root), "BTCUSDT")
        blocked = operation.files[1].destination
        original = Path.unlink
        def unlink(path, *args, **kwargs):
            if path == blocked:
                raise PermissionError("file locked")
            return original(path, *args, **kwargs)
        with patch.object(Path, "unlink", unlink):
            result = am.execute_asset_deletion(plan)
        self.assertEqual([f.outcome for f in result.files], ["deleted", "failed"])
        self.assertIn("locked", result.files[1].error)
        self.assertTrue(blocked.exists())
        entries = json.loads(operation.manifest_path.read_text())["files"]
        self.assertEqual(entries[0]["outcome"], "deleted")
        self.assertEqual(entries[1]["outcome"], "moved")
        self.assertEqual(entries[1]["deletion_history"][-1]["outcome"], "failed")
        retry = am.build_asset_deletion_plan(am.scan_quarantine(self.root), "BTCUSDT")
        self.assertEqual(len(retry.asset.files), 1)
        self.assertEqual(am.execute_asset_deletion(retry).files[0].outcome, "deleted")

    def test_manifest_write_failure_before_deletion_keeps_data(self):
        operation = self.quarantine([self.file()])
        plan = am.build_asset_deletion_plan(am.scan_quarantine(self.root), "BTCUSDT")
        with patch.object(am, "_write_manifest", side_effect=OSError("disk full")):
            result = am.execute_asset_deletion(plan)
        self.assertEqual(result.files[0].outcome, "failed")
        self.assertTrue(result.errors)
        self.assertTrue(operation.files[0].destination.exists())

    def test_manifest_write_failure_after_deletion_stops_remaining_work(self):
        operation = self.quarantine([self.file(), self.file(date="2026-01-02")])
        plan = am.build_asset_deletion_plan(am.scan_quarantine(self.root), "BTCUSDT")
        original = am._write_manifest
        calls = 0
        def write(path, doc):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("disk full")
            return original(path, doc)
        with patch.object(am, "_write_manifest", write):
            result = am.execute_asset_deletion(plan)
        self.assertEqual([f.outcome for f in result.files], ["deleted", "not attempted"])
        self.assertTrue(result.errors)
        self.assertTrue(operation.files[1].destination.exists())
        self.assertTrue(am.scan_quarantine(self.root).issues)

    def test_link_or_identity_change_immediately_before_deletion_keeps_file(self):
        operation = self.quarantine([self.file()])
        plan = am.build_asset_deletion_plan(am.scan_quarantine(self.root), "BTCUSDT")
        source = operation.files[0].destination
        original_write = am._write_manifest
        changed = False
        def write(path, doc):
            nonlocal changed
            original_write(path, doc)
            changed = True
        original_link = am._is_link
        with patch.object(am, "_write_manifest", write), \
                patch.object(am, "_is_link", lambda path: (changed and path == source) or original_link(path)):
            result = am.execute_asset_deletion(plan)
        self.assertEqual(result.files[0].outcome, "failed")
        self.assertIn("Linked", result.files[0].error)
        self.assertTrue(source.exists())

    def test_forged_deletion_plan_never_operates_outside_quarantine(self):
        from dataclasses import replace
        operation = self.quarantine([self.file()])
        plan = am.build_asset_deletion_plan(am.scan_quarantine(self.root), "BTCUSDT")
        outside = self.base / "outside.csv"
        outside.write_text("protected")
        forged_file = replace(plan.asset.files[0], path=outside)
        forged = replace(plan, asset=replace(plan.asset, files=(forged_file,)))
        with self.assertRaises(ValueError):
            am.execute_asset_deletion(forged)
        self.assertEqual(outside.read_text(), "protected")
        self.assertTrue(operation.files[0].destination.exists())
