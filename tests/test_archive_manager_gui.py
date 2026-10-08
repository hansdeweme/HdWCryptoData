"""Headless PyQt tests exercise selection, worker scanning and confirmation."""
import os
import time
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox, QTextEdit
from hdw_crypto_data.archive_manager import scan_archive, build_cleanup_plan
from hdw_crypto_data.archive_manager_gui import ArchiveManagerWindow, SortItem, QuarantineAssetsDialog
from hdw_crypto_data import archive_manager as am
from hdw_crypto_data.archive_index import ArchiveIndex
from test_archive_manager import ArchiveFixture


class GuiTests(ArchiveFixture):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def window(self):
        window = ArchiveManagerWindow(self.root)
        self.addCleanup(window.close)
        return window

    def wait_scan(self, window):
        deadline = time.monotonic() + 10
        while window.worker.isRunning() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertFalse(window.worker.isRunning())
        self.app.processEvents()

    def test_empty_launch_and_worker_population_filter_sort(self):
        window = self.window()
        window.show()
        window.start_scan()
        self.wait_scan(window)
        self.assertEqual(window.summary.rowCount(), 0)
        self.file()
        self.file("ETHUSDT")
        window.deep.setChecked(True)
        window.start_scan()
        self.wait_scan(window)
        self.assertEqual(window.summary.rowCount(), 2)
        window.summary.selectRow(0)
        self.assertEqual(window.details.rowCount(), 1)
        self.assertIn("2026", window.details.item(0, 4).text())
        window.search.setText("ETH")
        self.assertTrue(window.summary.isRowHidden(0))
        self.assertFalse(window.summary.isRowHidden(1))
        self.assertEqual(window.details.rowCount(), 0)
        self.assertTrue(SortItem(2) < SortItem(10))
        window.summary.sortItems(0, Qt.SortOrder.DescendingOrder)
        self.assertEqual(window.summary.item(0, 0).text(), "ETHUSDT")

    def test_cancel_does_not_mutate_and_confirm_moves_then_refreshes(self):
        first = self.file()
        window = self.window()
        window.populate(scan_archive(self.root))
        window.summary.selectRow(0)
        window.details.item(0, 0).setCheckState(Qt.CheckState.Checked)
        self.assertEqual(window.selected_paths, {first})
        with patch.object(window, "confirm_plan", return_value=False):
            window.preview_cleanup()
        self.assertTrue(first.exists())
        self.assertFalse(self.root.with_name("spot-quarantine").exists())
        with patch.object(window, "confirm_plan", return_value=True), patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Ok):
            window.preview_cleanup()
        self.wait_scan(window)
        self.assertFalse(first.exists())
        self.assertEqual(window.summary.rowCount(), 0)

    def test_errors_and_shutdown(self):
        window = self.window()
        with patch.object(QMessageBox, "warning") as warning:
            window.preview_cleanup()
            warning.assert_called_once()
            window.path.setText(str(self.base / "missing"))
            window.start_scan()
            self.wait_scan(window)
            self.assertEqual(warning.call_count, 2)
        self.assertTrue(window.scan.isEnabled())
        self.file()
        window.path.setText(str(self.root))
        window.start_scan()
        window.close()
        self.assertFalse(window.worker.isRunning())
        with patch.object(QMessageBox, "warning") as warning:
            self.app.processEvents()
            warning.assert_not_called()

    def test_folder_failure_and_preview_selection_across_groups(self):
        first, second = self.file(), self.file("ETHUSDT")
        window = self.window()
        window.populate(scan_archive(self.root))
        for row in range(2):
            window.summary.selectRow(row)
            window.details.item(0, 0).setCheckState(Qt.CheckState.Checked)
        self.assertEqual(window.selected_paths, {first, second})
        window.details.selectRow(0)
        with patch("hdw_crypto_data.archive_manager_gui.QDesktopServices.openUrl", return_value=False), patch.object(QMessageBox, "warning"):
            window.show_folder()
        self.assertFalse(window.open_folder.isEnabled())

    def test_actual_preview_dialog_lists_exact_files_and_cancel_is_read_only(self):
        first = self.file()
        window = self.window()
        plan = build_cleanup_plan(scan_archive(self.root), [first])
        captured = []
        def dismiss():
            dialog = self.app.activeModalWidget()
            captured.append(dialog.findChild(QTextEdit).toPlainText())
            dialog.reject()
        QTimer.singleShot(0, dismiss)
        self.assertFalse(window.confirm_plan(plan))
        self.assertIn(str(first), captured[0])
        self.assertIn(f"Bytes represented: {first.stat().st_size}", captured[0])
        self.assertIn("observed coverage unknown", captured[0])
        self.assertTrue(first.exists())
        self.assertFalse(plan.quarantine_root.exists())

    def test_partial_move_refreshes_and_reports_failure(self):
        first, second = self.file(), self.file("ETHUSDT")
        window = self.window()
        window.populate(scan_archive(self.root))
        window.selected_paths.update([first, second])
        def confirm(plan):
            second.write_text("changed after preview")
            return True
        dialogs = []
        def capture(message):
            dialogs.append(message)
            return QMessageBox.StandardButton.Ok
        with patch.object(window, "confirm_plan", confirm), patch.object(QMessageBox, "exec", capture), patch.object(QMessageBox, "warning") as warning:
            window.preview_cleanup()
            self.wait_scan(window)
            self.assertEqual(warning.call_count, 0, str(warning.call_args_list))
            result_dialog = dialogs[0]
            self.assertIn("Quarantined: 1 of 2 files", result_dialog.text())
            self.assertIn("Failed: 1", result_dialog.text())
            self.assertIn("failed:", result_dialog.detailedText())
            self.assertNotIn(str(first), result_dialog.text())
        self.assertFalse(first.exists())
        self.assertTrue(second.exists())
        self.assertEqual(window.summary.rowCount(), 1)

    def test_documented_module_launch_enters_and_exits_event_loop(self):
        import subprocess
        import sys
        script = (
            "import runpy; from PyQt6.QtWidgets import QApplication; from PyQt6.QtCore import QTimer; "
            "original = QApplication.exec; "
            "import hdw_crypto_data.total_dataset_loader as loader; loader.load_settings = lambda _: {}; "
            "QApplication.exec = lambda self: (QTimer.singleShot(50, self.quit), original())[1]; "
            "runpy.run_module('hdw_crypto_data.archive_manager_gui', run_name='__main__')"
        )
        launched = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=15)
        self.assertEqual(launched.returncode, 0, launched.stderr.decode(errors="replace"))

    def test_select_all_checks_current_group_and_preserves_other_selections(self):
        first, second, other = self.file(), self.file(date="2026-01-02"), self.file("ETHUSDT")
        window = self.window()
        self.assertFalse(window.select_all.isEnabled())
        self.assertFalse(window.preview.isEnabled())
        window.populate(scan_archive(self.root))
        self.assertFalse(window.select_all.isEnabled())
        window.summary.selectRow(0)
        self.assertTrue(window.select_all.isEnabled())
        window.details.sortItems(1, Qt.SortOrder.DescendingOrder)
        window.select_all.click()
        self.assertEqual(window.selected_paths, {first, second})
        self.assertTrue(window.preview.isEnabled())
        self.assertIn("Preview 2 selected files", window.preview.text())
        for row in range(window.details.rowCount()):
            self.assertEqual(window.details.item(row, 0).checkState(), Qt.CheckState.Checked)
        # Individual exclusions still work after selecting the whole group.
        window.details.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(window.selected_paths, {first})
        with patch.object(window, "confirm_plan", return_value=False) as confirmation:
            window.preview_cleanup()
        self.assertEqual([m.record.path for m in confirmation.call_args.args[0].files], [first])
        window.summary.selectRow(1)
        window.select_all.click()
        self.assertEqual(window.selected_paths, {first, other})
        window.search.setText("BTC")
        self.assertFalse(window.select_all.isEnabled())
        self.assertEqual(window.details.rowCount(), 0)
        self.assertTrue(all(path.exists() for path in (first, second, other)))
        self.assertFalse(self.root.with_name("spot-quarantine").exists())
        window.start_scan()
        self.assertFalse(window.select_all.isEnabled())
        self.wait_scan(window)
        self.assertEqual(window.selected_paths, set())
        self.assertFalse(window.preview.isEnabled())

    def test_post_quarantine_refresh_reuses_remaining_deep_results(self):
        first, remaining = self.file(), self.file("ETHUSDT")
        window = self.window()
        window.deep.setChecked(True)
        window.start_scan()
        self.wait_scan(window)
        window.summary.selectRow(0)
        window.select_all.click()
        dialogs = []
        def capture(message):
            dialogs.append(message)
            return QMessageBox.StandardButton.Ok
        with patch.object(window, "confirm_plan", return_value=True), \
                patch.object(QMessageBox, "exec", capture), \
                patch.object(am, "_inspect", side_effect=AssertionError("Unchanged CSV reread")):
            window.preview_cleanup()
            self.wait_scan(window)
            result_dialog = dialogs[0]
            self.assertIn("Quarantined: 1 of 1 files", result_dialog.text())
            self.assertEqual(result_dialog.detailedText(), "")
            self.assertNotIn(str(first), result_dialog.text())
        self.assertEqual([f.path for f in window.inventory.files], [remaining])
        self.assertEqual(window.inventory.files[0].row_count, 2)
        self.assertEqual(window.inventory.assets[0].status, "OK")

    def test_preview_attention_tracks_selection_without_moving_files(self):
        first = self.file()
        window = self.window()
        window.populate(scan_archive(self.root))
        window.summary.selectRow(0)
        checkbox = window.details.item(0, 0)
        self.assertFalse(window.preview.isEnabled())
        checkbox.setCheckState(Qt.CheckState.Checked)
        self.assertTrue(window.preview.isEnabled())
        self.assertIn("1 selected file", window.preview.text())
        self.assertEqual(window.preview.objectName(), "previewBtn")
        self.assertIn("QPushButton#previewBtn:enabled", window.styleSheet())
        checkbox.setCheckState(Qt.CheckState.Unchecked)
        self.assertFalse(window.preview.isEnabled())
        self.assertTrue(first.exists())

    def test_quarantine_asset_overview_cancel_and_confirm_whole_asset_deletion(self):
        first, other = self.file(), self.file("ETHUSDT")
        result = am.execute_quarantine(am.build_cleanup_plan(scan_archive(self.root), [first, other]))
        active = self.file(date="2026-01-03")
        window = self.window()
        self.assertIn(str(self.root.parent / ".hdw_archive" / "quarantine"), window.quarantine_path.text())
        dialog = QuarantineAssetsDialog(self.root, window)
        self.assertEqual(dialog.table.rowCount(), 2)
        self.assertFalse(dialog.delete.isEnabled())
        dialog.table.selectRow(0)
        self.assertTrue(dialog.delete.isEnabled())
        with patch.object(dialog, "confirm_deletion", return_value=False):
            dialog.delete_asset()
        self.assertTrue(result.files[0].destination.exists())
        dialog.table.selectRow(0)
        with patch.object(dialog, "confirm_deletion", return_value=True), \
                patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Ok):
            dialog.delete_asset()
        self.assertEqual(dialog.table.rowCount(), 1)
        self.assertEqual(dialog.table.item(0, 0).text(), "ETHUSDT")
        self.assertTrue(active.exists())
        self.assertTrue(result.files[1].destination.exists())
        self.assertTrue(result.manifest_path.exists())

    def test_permanent_deletion_confirmation_requires_matching_symbol_and_cancel_is_safe(self):
        first = self.file()
        operation = am.execute_quarantine(am.build_cleanup_plan(scan_archive(self.root), [first]))
        window = self.window()
        overview = QuarantineAssetsDialog(self.root, window)
        plan = am.build_asset_deletion_plan(overview.inventory, "BTCUSDT")
        from PyQt6.QtWidgets import QLineEdit, QPushButton
        assertions = []
        def cancel():
            dialog = self.app.activeModalWidget()
            button = next(b for b in dialog.findChildren(QPushButton) if b.text() == "Delete permanently")
            field = dialog.findChild(QLineEdit)
            assertions.append(not button.isEnabled())
            field.setText("ETHUSDT")
            assertions.append(not button.isEnabled())
            field.setText("BTCUSDT")
            assertions.append(button.isEnabled())
            dialog.reject()
        QTimer.singleShot(0, cancel)
        self.assertFalse(overview.confirm_deletion(plan))
        self.assertEqual(assertions, [True, True, True])
        self.assertTrue(operation.files[0].destination.exists())

    def test_quarantine_overview_corruption_disables_deletion_and_missing_root_is_handled(self):
        first = self.file()
        result = am.execute_quarantine(am.build_cleanup_plan(scan_archive(self.root), [first]))
        result.manifest_path.write_text("corrupt")
        overview = QuarantineAssetsDialog(self.root, self.window())
        self.assertFalse(overview.delete.isEnabled())
        self.assertIn("issues", overview.status.text())
        overview.archive_root = self.base / "missing"
        overview.reload()
        self.assertFalse(overview.delete.isEnabled())
        self.assertFalse(overview.open_folder.isEnabled())

    def test_cached_inventory_is_visible_before_verification_finishes(self):
        import threading
        first = self.file()
        index = ArchiveIndex(self.root)
        index.verify_incremental()
        first.unlink()  # Cache is deliberately stale until background verification.
        window = self.window()
        entered, release = threading.Event(), threading.Event()
        original = ArchiveIndex.verify_incremental
        def verify(index, **kwargs):
            entered.set()
            release.wait(5)
            return original(index, **kwargs)
        with patch.object(ArchiveIndex, "verify_incremental", verify):
            window.start_scan()
            try:
                deadline = time.monotonic() + 5
                while (not entered.is_set() or window.summary.rowCount() != 1) and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(.005)
                self.assertEqual(window.summary.rowCount(), 1)
                self.assertIn("Cached inventory loaded", window.status.text())
                self.assertFalse(window.preview.isEnabled())
            finally:
                release.set()
                self.wait_scan(window)
        self.assertEqual(window.summary.rowCount(), 0)
        self.assertIn("Verification complete", window.status.text())

    def test_index_failure_falls_back_and_explicit_rebuild_repairs(self):
        self.file()
        path = self.root.parent / ".hdw_archive" / "archive_index.sqlite"
        path.parent.mkdir()
        path.write_bytes(b"not sqlite")
        window = self.window()
        window.start_scan()
        self.wait_scan(window)
        self.assertEqual(window.summary.rowCount(), 1)
        self.assertIn("Index unavailable", window.status.text())
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            window.rebuild_index()
        self.assertEqual(path.read_bytes(), b"not sqlite")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            window.rebuild_index()
        self.wait_scan(window)
        self.assertIn("Verification complete", window.status.text())
        self.assertEqual(ArchiveIndex(self.root).load_inventory().files[0].row_count, 2)

    def test_selected_deep_rescan_reads_only_selected_group(self):
        first, other = self.file(), self.file("ETHUSDT")
        window = self.window()
        window.start_scan()
        self.wait_scan(window)
        window.summary.selectRow(0)
        self.assertTrue(window.deep_rescan_button.isEnabled())
        with patch.object(am, "_inspect", wraps=am._inspect) as inspect:
            window.deep_rescan_selected()
            self.wait_scan(window)
        self.assertEqual([c.args[0].path for c in inspect.call_args_list], [first])
        self.assertEqual(window.inventory.assets[1].observed_rows, 2)
        self.assertEqual(window.summary.selectedItems()[0].text(), "BTCUSDT")
        self.assertEqual(window.details.rowCount(), 1)
