"""Basic PyQt6 desktop archive manager. Launch with python -m this module."""
# Copyright (c) 2025, 2026 Hans De Weme
# Licensed under the MIT License (https://opensource.org/licenses/MIT).
# part of the HdW_crypto_data Project
#
from __future__ import annotations
import sys
from pathlib import Path
# PyQt imports
from PyQt6.QtCore import Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTextEdit,
    QVBoxLayout, QWidget, QAbstractItemView,
)
# local imports
from .archive_manager import (
    build_cleanup_plan, execute_quarantine, scan_archive, quarantine_location,
    scan_quarantine, build_asset_deletion_plan, execute_asset_deletion,
)
from .total_dataset_loader import load_settings
from .stylesheet import ARCHIVE_MANAGER_STYLE
from .archive_index import ArchiveIndex, ReconciliationResult


def _date(value):
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else "Unknown"


class SortItem(QTableWidgetItem):
    def __init__(self, value):
        super().__init__(str(value) if value is not None else "Unknown")
        self.value = value

    def __lt__(self, other):
        if self.value is None:
            return other.value is not None
        if other.value is None:
            return False
        return self.value < other.value


class ScanWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(int, str)
    cached = pyqtSignal(object)
    verified = pyqtSignal(object)
    notice = pyqtSignal(str)

    def __init__(self, root, mode, parent=None, cached_inventory=None, selection=None):
        super().__init__(parent)
        self.root, self.mode = root, mode
        self.cached_inventory = cached_inventory
        self.selection = selection

    def run(self):
        try:
            try:
                if self.mode == "rebuild":
                    result = ArchiveIndex.recover_and_rebuild(self.root, progress=self.progress.emit,
                                                            cancelled=self.isInterruptionRequested)
                else:
                    index = ArchiveIndex(self.root)
                    self.cached.emit(index.load_inventory())
                    result = index.verify_incremental(progress=self.progress.emit,
                                                      cancelled=self.isInterruptionRequested,
                                                      selection=self.selection)
            except InterruptedError:
                raise
            except Exception as exc:
                # Cache problems never prevent filesystem inspection.
                message = f"Index unavailable: {exc}. Filesystem inventory shown; repair with Rebuild index."
                self.notice.emit(message)
                inventory = scan_archive(self.root, "deep", progress=self.progress.emit,
                                         cancelled=self.isInterruptionRequested, cached_inventory=self.cached_inventory)
                result = ReconciliationResult(inventory, 0, 0, 0, 0, 0, False, (message,))
            self.completed.emit(result.inventory)
            self.verified.emit(result)
        except InterruptedError:
            pass
        except Exception as exc:
            self.failed.emit(str(exc))


class ArchiveManagerWindow(QMainWindow):
    def __init__(self, archive_root=None):
        super().__init__()
        self.setStyleSheet(ARCHIVE_MANAGER_STYLE)
        self.setWindowTitle("Crypto Archive Manager")
        self.resize(1400, 800)
        self.inventory = None
        self.worker = None
        self.mutating = False
        self.closing = False
        self.selected_paths = set()
        settings = load_settings(Path.cwd() / "settings.json")
        default = settings.get("full_spot") or settings.get("spot") or ""
        self.path = QLineEdit(str(archive_root) if archive_root is not None else default)
        self.browse = QPushButton("Browse…")
        self.scan = QPushButton("Refresh / Verify")
        self.deep = QCheckBox("Read CSV contents (deep validation)")
        self.deep.setChecked(True)  # Compatibility for programmatic callers; verification always inspects new data.
        self.deep_rescan_button = QPushButton("Deep rescan selected")
        self.deep_rescan_button.setEnabled(False)
        self.rebuild_button = QPushButton("Rebuild index…")
        self.status = QLabel("Choose an archive root and scan. All dates are UTC.")
        self.status.setWordWrap(True)
        self.quarantine_path = QLabel()
        self.quarantine_path.setObjectName("quarantineLocation")
        self.quarantine_path.setWordWrap(True)
        self.quarantine_path.setTextFormat(Qt.TextFormat.PlainText)
        self.quarantine_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.quarantined_assets = QPushButton("Quarantined assets…")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by symbol")
        self.summary = self._table(["Symbol", "Interval", "Data files", "Observed earliest UTC", "Observed latest UTC", "Observed rows", "Bytes", "Status"])
        self.details = self._table(["Select", "Relative path", "Advertised start UTC", "Advertised end UTC", "Observed first UTC", "Observed last UTC", "Rows", "Bytes", "Status / messages"])
        self.summary.sortItems(0, Qt.SortOrder.AscendingOrder)
        self.details.sortItems(1, Qt.SortOrder.AscendingOrder)
        self.preview = QPushButton("Preview selected files for quarantine…")
        self.preview.setEnabled(False)
        self.preview.setObjectName("previewBtn")
        self.select_all = QPushButton("Select all files")
        self.select_all.setToolTip("Check every file for the selected asset and interval")
        self.select_all.setEnabled(False)
        self.open_folder = QPushButton("Open containing folder")
        self.open_folder.setEnabled(False)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        top = QHBoxLayout()
        for widget in (QLabel("Archive root"), self.path, self.browse, self.scan,
                       self.deep_rescan_button, self.rebuild_button):
            top.addWidget(widget)
        layout.addLayout(top)
        layout.addWidget(self.status)
        quarantine_row = QHBoxLayout()
        quarantine_row.addWidget(self.quarantine_path, 1)
        quarantine_row.addWidget(self.quarantined_assets)
        layout.addLayout(quarantine_row)
        layout.addWidget(self.progress)
        layout.addWidget(self.search)
        splitter = QSplitter(Qt.Orientation.Vertical)
        for title, table in (("Active archive — assets and intervals", self.summary),
                             ("Files for the selected asset / interval", self.details)):
            panel = QWidget()
            panel_layout = QVBoxLayout(panel)
            panel_layout.setContentsMargins(0, 0, 0, 0)
            heading = QLabel(title)
            heading.setObjectName("sectionHeading")
            panel_layout.addWidget(heading)
            panel_layout.addWidget(table)
            splitter.addWidget(panel)
        splitter.setHandleWidth(6)
        splitter.setSizes([300, 350])
        layout.addWidget(splitter)
        actions = QHBoxLayout()
        actions.addWidget(self.select_all)
        actions.addWidget(self.open_folder)
        actions.addStretch()
        actions.addWidget(self.preview)
        layout.addLayout(actions)
        self.setCentralWidget(central)
        self.browse.clicked.connect(self.choose_root)
        self.scan.clicked.connect(self.start_scan)
        self.deep_rescan_button.clicked.connect(self.deep_rescan_selected)
        self.rebuild_button.clicked.connect(self.rebuild_index)
        self.search.textChanged.connect(self.filter_summary)
        self.summary.itemSelectionChanged.connect(self.show_details)
        self.details.itemChanged.connect(self.update_selection)
        self.details.itemSelectionChanged.connect(lambda: self.open_folder.setEnabled(bool(self.details.selectedItems())))
        self.preview.clicked.connect(self.preview_cleanup)
        self.select_all.clicked.connect(self.select_all_files)
        self.open_folder.clicked.connect(self.show_folder)
        self.path.textChanged.connect(self.update_quarantine_location)
        self.quarantined_assets.clicked.connect(self.show_quarantined_assets)
        self.update_quarantine_location()

    def update_quarantine_location(self):
        try:
            location = quarantine_location(self.path.text())
            self.quarantine_path.setText(f"Quarantine: {location}")
            self.quarantined_assets.setEnabled(self.scan.isEnabled() and not self.mutating)
        except (OSError, ValueError):
            self.quarantine_path.setText("Quarantine: choose a valid archive root")
            self.quarantined_assets.setEnabled(False)

    def show_quarantined_assets(self):
        try:
            dialog = QuarantineAssetsDialog(self.path.text(), self)
            self.mutating = True
            dialog.exec()
        except Exception as exc:
            self.show_error(str(exc))
        finally:
            self.mutating = False
            self.update_preview_action()

    @staticmethod
    def _table(headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSortingEnabled(True)
        table.setAlternatingRowColors(True)
        table.setWordWrap(False)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(32)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def choose_root(self):
        directory = QFileDialog.getExistingDirectory(self, "Choose spot or its parent", self.path.text())
        if directory:
            self.path.setText(directory)

    def deep_rescan_selected(self):
        items = self.summary.selectedItems()
        if items:
            asset = items[0].data(Qt.ItemDataRole.UserRole)
            self.start_scan("selected", (asset.symbol, asset.interval))

    def rebuild_index(self):
        answer = QMessageBox.question(self, "Rebuild archive index",
            "Reread the entire archive and rebuild its derived index? This may take time.\n"
            "No market data or quarantine manifests will be deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.start_scan("rebuild")

    def load_cached_inventory(self, inventory):
        self.populate(inventory)
        if not self.closing:
            self.status.setText("Cached inventory loaded — verifying archive… Coverage is cached until verification completes.")

    def verification_finished(self, result):
        if self.closing:
            return
        if result.warnings:
            self.status.setText("Verification pending: " + "; ".join(result.warnings))
        else:
            state = "Verification complete" if result.complete else "Verification pending; concurrent changes or directory issues require another refresh"
            self.status.setText(f"{state}: {result.new} new, {result.changed} changed, {result.missing} missing, "
                                f"{result.unchanged} unchanged; {result.inspected} CSVs inspected. All dates UTC.")

    def start_scan(self, mode="verify", selection=None):
        if self.closing:
            return
        if self.worker is not None and self.worker.isRunning():
            return
        self.scan.setEnabled(False)
        self.preview.setEnabled(False)
        self.select_all.setEnabled(False)
        self.browse.setEnabled(False)
        self.path.setEnabled(False)
        self.quarantined_assets.setEnabled(False)
        self.deep_rescan_button.setEnabled(False)
        self.rebuild_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.status.setText("Refreshing inventory; reusing unchanged CSV validation results…")
        self.worker = ScanWorker(self.path.text(), mode if isinstance(mode, str) else "verify", self,
                                 cached_inventory=self.inventory, selection=selection)
        self.worker.progress.connect(lambda count, path: self.status.setText(f"Verifying cached inventory — checked {count} files — {path}"))
        self.worker.cached.connect(self.load_cached_inventory)
        self.worker.verified.connect(self.verification_finished)
        self.worker.notice.connect(lambda message: self.status.setText(message))
        self.worker.completed.connect(self.populate)
        self.worker.failed.connect(self.show_error)
        self.worker.finished.connect(self.scan_finished)
        self.worker.start()

    def scan_finished(self):
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        for widget in (self.scan, self.browse, self.path):
            widget.setEnabled(True)
        self.rebuild_button.setEnabled(True)
        self.deep_rescan_button.setEnabled(bool(self.summary.selectedItems()))
        self.update_preview_action()
        self.update_quarantine_location()
        self.select_all.setEnabled(bool(self.summary.selectedItems()) and self.details.rowCount() > 0)

    def show_error(self, message):
        if self.closing:
            return
        self.status.setText(message)
        QMessageBox.warning(self, "Archive manager", message)

    def populate(self, inventory):
        if self.closing:
            return
        items = self.summary.selectedItems()
        selected = None
        if items and self.inventory is not None and self.inventory.archive_root == inventory.archive_root:
            asset = items[0].data(Qt.ItemDataRole.UserRole)
            selected = (asset.symbol, asset.interval)
        self.inventory = inventory
        self.selected_paths.clear()
        self.update_preview_action()
        self.select_all.setEnabled(False)
        self.details.setRowCount(0)
        self.summary.blockSignals(True)
        self.summary.clearSelection()
        self.summary.setSortingEnabled(False)
        self.summary.setRowCount(len(inventory.assets))
        for row, asset in enumerate(inventory.assets):
            values = [asset.symbol, asset.interval, asset.file_count, _date(asset.coverage.start),
                      _date(asset.coverage.end), asset.observed_rows, asset.size_bytes, asset.status]
            for col, value in enumerate(values):
                item = SortItem(value)
                item.setData(Qt.ItemDataRole.UserRole, asset)
                self.summary.setItem(row, col, item)
        self.summary.setSortingEnabled(True)
        self.summary.resizeColumnsToContents()
        self.filter_summary()
        self.summary.blockSignals(False)
        if selected is not None:
            for row in range(self.summary.rowCount()):
                asset = self.summary.item(row, 0).data(Qt.ItemDataRole.UserRole)
                if (asset.symbol, asset.interval) == selected and not self.summary.isRowHidden(row):
                    self.summary.selectRow(row)
                    break
        warnings = sum(bool(f.issues) for f in inventory.files)
        self.status.setText(f"{len(inventory.files)} encountered files, {len(inventory.assets)} groups, {warnings} files with warnings; {len(inventory.issues)} directory issues. All dates UTC.")
        if inventory.issues:
            self.show_error("\n".join(i.message for i in inventory.issues))

    def filter_summary(self):
        query = self.search.text().strip().upper()
        for row in range(self.summary.rowCount()):
            self.summary.setRowHidden(row, query not in self.summary.item(row, 0).text().upper())
        selected = self.summary.currentRow()
        if selected >= 0 and self.summary.isRowHidden(selected):
            self.summary.clearSelection()
            self.details.setRowCount(0)

    def show_details(self):
        items = self.summary.selectedItems()
        if not items:
            self.details.setRowCount(0)
            self.select_all.setEnabled(False)
            self.deep_rescan_button.setEnabled(False)
            return
        asset = items[0].data(Qt.ItemDataRole.UserRole)
        self.details.blockSignals(True)
        self.details.setSortingEnabled(False)
        self.details.setRowCount(len(asset.files))
        for row, record in enumerate(asset.files):
            values = ["", str(record.relative_path), _date(record.advertised.start), _date(record.advertised.end),
                      _date(record.observed.start), _date(record.observed.end), record.row_count, record.size_bytes,
                      record.status + ": " + "; ".join(i.message for i in record.issues)]
            for col, value in enumerate(values):
                item = SortItem(value)
                item.setData(Qt.ItemDataRole.UserRole, record)
                if col == 0:
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Checked if record.path in self.selected_paths else Qt.CheckState.Unchecked)
                self.details.setItem(row, col, item)
        self.details.setSortingEnabled(True)
        self.details.resizeColumnsToContents()
        self.details.blockSignals(False)
        self.select_all.setEnabled(self.details.rowCount() > 0 and self.scan.isEnabled())
        self.deep_rescan_button.setEnabled(self.scan.isEnabled())

    def select_all_files(self):
        if not self.select_all.isEnabled():
            return
        for row in range(self.details.rowCount()):
            self.details.item(row, 0).setCheckState(Qt.CheckState.Checked)

    def update_selection(self, item):
        if item.column() != 0:
            return
        record = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self.selected_paths.add(record.path)
        else:
            self.selected_paths.discard(record.path)
        self.update_preview_action()

    def update_preview_action(self):
        count = len(self.selected_paths)
        self.preview.setEnabled(count > 0 and self.scan.isEnabled() and not self.mutating)
        noun = "file" if count == 1 else "files"
        self.preview.setText(f"Preview {count} selected {noun} for quarantine…" if count else "Select files to preview quarantine…")

    def show_quarantine_result(self, plan, result):
        moved = [f for f in result.files if f.outcome == "moved"]
        failed = [f for f in result.files if f.outcome == "failed"]
        pending = [f for f in result.files if f.outcome == "not attempted"]
        sizes = {m.record.path: m.record.size_bytes for m in plan.files}
        moved_bytes = sum(sizes[f.source] for f in moved)
        message = QMessageBox(self)
        message.setWindowTitle("Quarantine result")
        message.setTextFormat(Qt.TextFormat.PlainText)
        message.setIcon(QMessageBox.Icon.Information if result.successful else QMessageBox.Icon.Warning)
        message.setText(
            f"Quarantined: {len(moved)} of {len(plan.files)} files\n"
            f"Failed: {len(failed)}\nNot attempted: {len(pending)}\n"
            f"Data moved: {moved_bytes:,} bytes\n"
            f"Assets / intervals: {', '.join(s + ' / ' + i for s, i in plan.affected_groups)}"
        )
        message.setInformativeText(f"Quarantine: {plan.quarantine_root / plan.operation_id}\nManifest: {result.manifest_path}")
        problems = [f"{f.outcome}: {f.source} — {f.error}" for f in failed + pending]
        if problems or result.errors:
            message.setDetailedText("\n".join(problems + list(result.errors)))
        message.exec()

    def confirm_plan(self, plan):
        dialog = QDialog(self)
        dialog.setWindowTitle("Review exact quarantine plan")
        dialog.resize(950, 650)
        layout = QVBoxLayout(dialog)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(
            f"Files: {len(plan.files)}\nBytes represented: {plan.total_size_bytes}\n"
            f"Groups: {', '.join(s + ' / ' + i for s, i in plan.affected_groups)}\n"
            f"Affected range UTC (observed when available, otherwise advertised):\n{_date(plan.coverage.start)} to {_date(plan.coverage.end)}\n"
            f"Destination: {plan.quarantine_root / plan.operation_id}\n\n"
            "Exact source files:\n" + "\n".join(str(m.record.path) for m in plan.files) +
            "\n\nWarnings:\n" + ("\n".join(plan.warnings) or "None") +
            "\n\nQuarantine moves files out of the active archive. Restore is available through the documented core API."
        )
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        confirm = buttons.addButton("Confirm move to quarantine", QDialogButtonBox.ButtonRole.AcceptRole)
        confirm.clicked.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        return dialog.exec() == QDialog.DialogCode.Accepted

    def preview_cleanup(self):
        if self.inventory is None:
            self.show_error("Scan an archive first")
            return
        try:
            plan = build_cleanup_plan(self.inventory, sorted(self.selected_paths))
            if not self.confirm_plan(plan):
                return
            # Mutation remains synchronous and visible; closing cannot hide work.
            self.mutating = True
            self.status.setText("Moving confirmed files to quarantine…")
            result = execute_quarantine(plan)
            self.show_quarantine_result(plan, result)
        except Exception as exc:
            self.show_error(str(exc))
        finally:
            self.mutating = False
        self.start_scan()

    def show_folder(self):
        items = self.details.selectedItems()
        if not items:
            return
        record = items[0].data(Qt.ItemDataRole.UserRole)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(record.path.parent))):
            self.open_folder.setEnabled(False)
            self.show_error("Opening folders is unavailable on this platform")

    def closeEvent(self, event):
        if self.mutating:
            event.ignore()
            return
        self.closing = True
        if self.worker is not None and self.worker.isRunning():
            self.worker.requestInterruption()
            self.worker.wait()
        event.accept()


class QuarantineAssetsDialog(QDialog):
    """Second cleanup level: asset overview, explicit permanent deletion."""
    def __init__(self, archive_root, parent=None):
        super().__init__(parent)
        self.setStyleSheet(ARCHIVE_MANAGER_STYLE)
        self.archive_root = archive_root
        self.inventory = None
        self.setWindowTitle("Quarantined assets")
        self.resize(900, 550)
        layout = QVBoxLayout(self)
        self.location = QLabel()
        self.location.setObjectName("quarantineLocation")
        self.location.setWordWrap(True)
        self.location.setTextFormat(Qt.TextFormat.PlainText)
        self.location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.location)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.table = ArchiveManagerWindow._table(["Asset", "Intervals", "Files", "Bytes", "Operations"])
        self.table.sortItems(0, Qt.SortOrder.AscendingOrder)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        self.refresh = QPushButton("Refresh quarantine")
        self.open_folder = QPushButton("Open quarantine folder")
        self.delete = QPushButton("Delete selected asset permanently…")
        self.delete.setObjectName("deleteAssetBtn")
        self.delete.setEnabled(False)
        row.addWidget(self.refresh)
        row.addWidget(self.open_folder)
        row.addStretch()
        row.addWidget(self.delete)
        layout.addLayout(row)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.refresh.clicked.connect(self.reload)
        self.open_folder.clicked.connect(self.show_folder)
        self.table.itemSelectionChanged.connect(self.update_delete_action)
        self.delete.clicked.connect(self.delete_asset)
        self.reload()

    def reload(self):
        try:
            self.inventory = scan_quarantine(self.archive_root)
        except (OSError, ValueError) as exc:
            self.inventory = None
            self.table.clearSelection()
            self.delete.setEnabled(False)
            self.open_folder.setEnabled(False)
            self.status.setText(str(exc))
            return
        self.location.setText(f"Quarantine: {self.inventory.quarantine_root}")
        self.open_folder.setEnabled(self.inventory.quarantine_root.is_dir())
        self.table.clearSelection()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.inventory.assets))
        for row, asset in enumerate(self.inventory.assets):
            values = [asset.symbol, ", ".join(asset.intervals), len(asset.files), asset.size_bytes,
                      len({f.manifest_path for f in asset.files})]
            for column, value in enumerate(values):
                item = SortItem(value)
                item.setData(Qt.ItemDataRole.UserRole, asset)
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()
        self.status.setText(
            "Deletion is disabled until these quarantine issues are resolved:\n" +
            "\n".join(i.message for i in self.inventory.issues) if self.inventory.issues else
            f"{len(self.inventory.assets)} quarantined {'asset' if len(self.inventory.assets) == 1 else 'assets'}. Deleting an asset removes all its quarantined files across every interval and operation."
        )
        self.update_delete_action()

    def update_delete_action(self):
        self.delete.setEnabled(self.inventory is not None and bool(self.table.selectedItems()) and not self.inventory.issues)

    def confirm_deletion(self, plan):
        dialog = QDialog(self)
        dialog.setWindowTitle("Confirm permanent asset deletion")
        layout = QVBoxLayout(dialog)
        description = QLabel(
            f"Permanently delete quarantined asset {plan.asset.symbol}?\n\n"
            f"Files: {len(plan.asset.files)}\nBytes: {plan.asset.size_bytes:,}\n"
            f"Intervals: {', '.join(plan.asset.intervals)}\nQuarantine: {plan.quarantine_root}\n\n"
            "These quarantined files cannot be restored after deletion. Active spot files remain untouched.\n"
            "Type the asset symbol below to confirm:"
        )
        description.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(description)
        symbol = QLineEdit()
        layout.addWidget(symbol)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        confirm = buttons.addButton("Delete permanently", QDialogButtonBox.ButtonRole.AcceptRole)
        confirm.setObjectName("confirmDeleteBtn")
        confirm.setEnabled(False)
        symbol.textChanged.connect(lambda value: confirm.setEnabled(value == plan.asset.symbol))
        confirm.clicked.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        return dialog.exec() == QDialog.DialogCode.Accepted

    def delete_asset(self):
        items = self.table.selectedItems()
        if not items or self.inventory is None or self.inventory.issues:
            return
        try:
            plan = build_asset_deletion_plan(self.inventory, items[0].data(Qt.ItemDataRole.UserRole).symbol)
            if not self.confirm_deletion(plan):
                return
            result = execute_asset_deletion(plan)
            deleted = sum(f.outcome == "deleted" for f in result.files)
            message = QMessageBox(self)
            message.setWindowTitle("Asset deletion result")
            message.setTextFormat(Qt.TextFormat.PlainText)
            message.setIcon(QMessageBox.Icon.Information if deleted == len(plan.asset.files) and not result.errors else QMessageBox.Icon.Warning)
            message.setText(f"Asset: {result.symbol}\nDeleted: {deleted} of {len(plan.asset.files)} files\n"
                            f"Failed: {sum(f.outcome == 'failed' for f in result.files)}\n"
                            f"Not attempted: {sum(f.outcome == 'not attempted' for f in result.files)}\n"
                            f"Data removed: {result.bytes_deleted:,} bytes\nOperation manifests retained for history.")
            problems = [f"{f.outcome}: {f.source} — {f.error}" for f in result.files if f.outcome != "deleted"]
            if problems or result.errors:
                message.setDetailedText("\n".join(problems + list(result.errors)))
            message.exec()
        except Exception as exc:
            QMessageBox.warning(self, "Quarantined assets", str(exc))
        finally:
            self.reload()

    def show_folder(self):
        if self.inventory is None:
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.inventory.quarantine_root))):
            self.open_folder.setEnabled(False)
            QMessageBox.warning(self, "Quarantined assets", "Opening folders is unavailable on this platform")


def main():
    app = QApplication(sys.argv)
    window = ArchiveManagerWindow()
    window.show()
    if window.quarantined_assets.isEnabled():
        window.start_scan()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
