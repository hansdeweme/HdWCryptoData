# Copyright (c) 2025, 2026 Hans De Weme
# Licensed under the MIT License.
"""Shared market showcase with optional acquisition and canonical DataFrame input."""
import json
import os
import re
from datetime import datetime
from html import escape
from pathlib import Path
import sys
os.environ.setdefault('NUMBA_DISABLE_JIT', '1')
# PyQt imports
from PyQt6.QtCore import QThread, pyqtSignal, Qt, QSize
from PyQt6.QtGui import QIcon, QColor
from PyQt6.QtWidgets import (QApplication, QMainWindow, QListWidgetItem, QTableWidgetItem, QFileDialog, QMessageBox)
# local imports
if __package__:
    from .showcase_ui import build_ui
    from .stylesheet import DARK_STYLE
    from .showcase_sources import (MarketDataset, StockRequest, CryptoRequest, AcquisitionController, load_csv, SOURCE_PACKAGES, source_availability)
else:
    from showcase_ui import build_ui
    from stylesheet import DARK_STYLE
    from showcase_sources import (MarketDataset, StockRequest, CryptoRequest, AcquisitionController, load_csv, SOURCE_PACKAGES, source_availability)


class PipelineWorker(QThread):
    dataset_ready = pyqtSignal(object)
    failed = pyqtSignal(str)
    status = pyqtSignal(str)
    asset_started = pyqtSignal(int)
    asset_finished = pyqtSignal(int, bool, int, str)
    progress = pyqtSignal(int, int)

    def __init__(self, requests, controller=None, parent=None):
        super().__init__(parent)
        self.requests = requests
        self.controller = controller or AcquisitionController()

    def run(self):
        for index, request in enumerate(self.requests):
            if self.isInterruptionRequested():
                break
            self.status.emit(f'Loading {request.symbol}...')
            self.asset_started.emit(index)
            try:
                dataset = self.controller.load(request)
                self.dataset_ready.emit(dataset)
                self.asset_finished.emit(index, True, len(dataset.dataframe),
                                         '; '.join(dataset.warnings) or 'Ready for charting')
            except Exception as exc:
                self.failed.emit(f'{request.symbol}: {exc}')
                self.asset_finished.emit(index, False, 0, str(exc))
            self.progress.emit(index + 1, len(self.requests))


class MiniDumperApp(QMainWindow):
    def __init__(self, datasets=()):
        super().__init__()
        self.settings = self.load_settings()
        self.worker = None
        self.datasets = {}
        self.chart_info_cache = {}
        self._active_source = None
        self._asset_selections = {}
        self._custom_assets = {0: set(), 1: set()}
        self._icon_maps = {}
        self._batch_active = False
        self.available_sources = source_availability()
        self.setWindowTitle('Market Data Showcase — Yahoo / Binance / DataFrame')
        self.resize(1240, 820)
        self.setStyleSheet(DARK_STYLE)
        self.init_ui()
        self.configure_source_availability()
        self.source_changed()
        for dataset in datasets:
            self.add_dataset(dataset)

    def load_settings(self):
        root = Path(__file__).resolve().parent
        settings = {'spot': str(root / 'spot'), 'quote_currency': 'USDT',
                    'preferred_time_zone': 'UTC'}
        path = root / 'settings.json'
        if path.exists():
            settings.update(json.loads(path.read_text(encoding='utf-8')))
        for key in ('spot', 'stock_icons', 'crypto_icons'):
            if settings.get(key):
                value = Path(settings[key]).expanduser()
                settings[key] = str(value if value.is_absolute() else root / value)
        return settings

    def init_ui(self):
        build_ui(self)

    def configure_source_availability(self):
        missing = [package for package, available in zip(SOURCE_PACKAGES, self.available_sources)
                   if not available]
        for index, available in enumerate(self.available_sources):
            if not available:
                self.source_combo.setItemText(index, self.source_combo.itemText(index) + ' (package missing)')
        self.dependency_notice.setVisible(bool(missing))
        if missing:
            mode = ('Only the installed source can acquire data.' if any(self.available_sources)
                    else 'Data acquisition is unavailable.')
            command = f'"{sys.executable}" -m pip install ' + ' '.join(missing)
            if os.name == 'nt':
                command = '& ' + command  # PowerShell invocation of a quoted executable.
            message = (f'Restricted mode: missing {", ".join(missing)}. {mode}\n'
                       'CSV import and supplied DataFrames remain available.\n'
                       f'Install the missing package(s) in this Python environment, then restart:\n{command}')
            self.dependency_notice.setText(message)
            self.append_log(message, 'WARN')
        if not self.available_sources[0] and self.available_sources[1]:
            self.source_combo.setCurrentIndex(1)

    def source_changed(self):
        if self._active_source is not None:
            self._asset_selections[self._active_source] = set(self.selected_symbols())
        self._active_source = self.source_combo.currentIndex()
        stock = self._active_source == 0
        self.days_spin.setEnabled(stock)
        self.days_spin.setVisible(stock)
        self.days_label.setVisible(stock)
        self.archive_check.setEnabled(not stock)
        self.force_check.setEnabled(not stock)
        self.archive_check.setVisible(not stock)
        self.force_check.setVisible(not stock)
        self.asset_group.setTitle('Select stock assets' if stock else 'Select crypto assets')
        self.pipeline_group.setTitle('Yahoo stock pipeline' if stock else 'Binance crypto pipeline')
        self.pipeline_description.setText(
            'Fetch hourly observations from Yahoo, normalize OHLCV, and prepare charts.' if stock else
            f'2. Build and load the total dataset. Archive directory: {self.settings["spot"]}')
        self.symbol_input.clear()
        self.symbol_input.setPlaceholderText('Add AAPL, BRK.B, ^GSPC...' if stock else 'Add BTC, ETH, SOL...')
        self.search_bar.clear()
        icons = {}
        directory = self.settings.get('stock_icons' if stock else 'crypto_icons')
        if directory and Path(directory).is_dir():
            for path in sorted(Path(directory).iterdir()):
                if path.is_file() and path.suffix.lower() == '.png':
                    try:
                        icons.setdefault(self.normalize_asset(path.stem), path)
                    except ValueError:
                        continue
        self._icon_maps[self._active_source] = icons
        self.icon_path_label.setText(f'{len(icons)} icons loaded' if icons else
                                     'No icons found. Add a symbol or choose a suggested asset.')
        self.icon_path_label.setToolTip(str(directory or 'No icon directory configured'))
        fallback = ['AAPL', 'MSFT', 'NVDA', '^GSPC'] if stock else ['BTC', 'ETH', 'SOL', 'BONK']
        symbols = sorted(set(icons or fallback) | self._custom_assets[self._active_source])
        selected = self._asset_selections.get(self._active_source, {'AAPL' if stock else 'BTC'})
        self.asset_list.blockSignals(True)
        self.asset_list.clear()
        for symbol in symbols:
            self.make_asset_item(symbol, symbol in selected)
        self.asset_list.blockSignals(False)
        self.update_selection_counter()

    def normalize_asset(self, symbol):
        value = symbol.strip().upper()
        if self.source_combo.currentIndex() == 1:
            quote = self.settings.get('quote_currency', 'USDT').upper()
            value = value.removesuffix(quote)
            pattern = r'[A-Z0-9]+'
        else:
            pattern = r'\^?[A-Z0-9][A-Z0-9.=_-]*'
        if not re.fullmatch(pattern, value):
            raise ValueError(f'Invalid asset symbol: {symbol}')
        return value

    def make_asset_item(self, symbol, checked=False):
        path = self._icon_maps.get(self._active_source, {}).get(symbol)
        item = QListWidgetItem(QIcon(str(path)) if path else QIcon(), symbol)
        item.setSizeHint(QSize(158, 42))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        item.setToolTip(symbol)
        self.asset_list.addItem(item)
        return item

    def selected_symbols(self):
        return [self.asset_list.item(i).text() for i in range(self.asset_list.count())
                if self.asset_list.item(i).checkState() == Qt.CheckState.Checked
                or self.asset_list.item(i).isSelected()]

    def update_selection_counter(self):
        count = len(self.selected_symbols())
        self.selection_label.setText(f'{count} asset(s) selected')
        self.load_button.setText(f'Start batch processing ({count})')
        available = self.available_sources[self.source_combo.currentIndex()]
        self.load_button.setEnabled(count > 0 and available and not self._batch_active)
        self.load_button.setToolTip('' if available else
                                   f'Install {SOURCE_PACKAGES[self.source_combo.currentIndex()]} and restart first.')

    def filter_assets(self, text):
        for i in range(self.asset_list.count()):
            item = self.asset_list.item(i)
            item.setHidden(text.strip().upper() not in item.text())

    def set_all_checked(self, checked):
        self.asset_list.blockSignals(True)
        if not checked:
            self.asset_list.clearSelection()
        for i in range(self.asset_list.count()):
            item = self.asset_list.item(i)
            if not checked or not item.isHidden():
                item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.asset_list.blockSignals(False)
        self.update_selection_counter()

    def add_custom_assets(self):
        try:
            symbols = [self.normalize_asset(value) for value in
                       self.symbol_input.text().replace(',', ' ').split()]
        except ValueError as exc:
            QMessageBox.warning(self, 'Invalid asset', str(exc))
            return
        self.search_bar.clear()
        for symbol in symbols:
            self._custom_assets[self._active_source].add(symbol)
            existing = self.asset_list.findItems(symbol, Qt.MatchFlag.MatchExactly)
            if existing:
                existing[0].setCheckState(Qt.CheckState.Checked)
            else:
                self.make_asset_item(symbol, True)
        self.symbol_input.clear()
        self.update_selection_counter()

    def append_log(self, message, level='INFO'):
        colors = {'INFO': '#90cdf4', 'SUCCESS': '#68d391', 'ERROR': '#fc8181', 'WARN': '#f6e05e'}
        self.log_console.append(f'<span style="color:{colors[level]}">'
                                f'[{datetime.now():%H:%M:%S}] [{level}] {escape(message)}</span>')

    def start_processing(self):
        if not self.available_sources[self.source_combo.currentIndex()]:
            QMessageBox.warning(self, 'Source package missing', self.dependency_notice.text())
            return
        from zoneinfo import ZoneInfo
        try:
            timezone = self.timezone_input.text().strip()
            ZoneInfo(timezone)
            symbols = self.selected_symbols()
            if not symbols:
                raise ValueError('Select at least one asset.')
            if self.source_combo.currentIndex() == 0:
                requests = [StockRequest(s, days=self.days_spin.value(), timezone=timezone) for s in symbols]
            else:
                requests = [CryptoRequest(s, dict(self.settings), timezone=timezone,
                                          download_archives=self.archive_check.isChecked(),
                                          force_merge=self.force_check.isChecked()) for s in symbols]
        except Exception as exc:
            QMessageBox.warning(self, 'Invalid request', str(exc))
            return
        self._batch_active = True
        self.asset_group.setEnabled(False)
        self.pipeline_group.setEnabled(False)
        self.update_selection_counter()
        self.stop_button.setEnabled(True)
        self.table.setRowCount(len(requests))
        for row, request in enumerate(requests):
            for column, text in enumerate((request.symbol, 'Yahoo' if isinstance(request, StockRequest) else 'Binance',
                                           'Pending', '-', 'Waiting')):
                self.table.setItem(row, column, QTableWidgetItem(text))
        self.progress_bar.setRange(0, len(requests))
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat('Processed %v / %m assets')
        self.worker = PipelineWorker(requests, parent=self)
        self.worker.dataset_ready.connect(self.add_dataset)
        self.worker.failed.connect(lambda text: self.append_log(text, 'ERROR'))
        self.worker.status.connect(self.append_log)
        self.worker.asset_started.connect(lambda row: self.table.setItem(row, 2, QTableWidgetItem('Running')))
        self.worker.asset_finished.connect(self.on_asset_finished)
        self.worker.progress.connect(lambda done, total: self.progress_bar.setValue(done))
        self.worker.finished.connect(self.batch_finished)
        self.worker.start()

    def stop_processing(self):
        if self.worker:
            self.worker.requestInterruption()
            self.stop_button.setEnabled(False)
            self.append_log('Stopping after the current asset finishes.', 'WARN')

    def on_asset_finished(self, row, success, rows, details):
        state = QTableWidgetItem('Ready' if success else 'Failed')
        state.setForeground(QColor('#68d391' if success else '#fc8181'))
        self.table.setItem(row, 2, state)
        self.table.setItem(row, 3, QTableWidgetItem(f'{rows:,}' if success else '-'))
        item = QTableWidgetItem(details)
        item.setToolTip(details)
        self.table.setItem(row, 4, item)
        if success:
            self.append_log(f'{self.table.item(row, 0).text()}: {rows:,} rows ready for charting.', 'SUCCESS')

    def batch_finished(self):
        self._batch_active = False
        self.asset_group.setEnabled(True)
        self.pipeline_group.setEnabled(True)
        self.update_selection_counter()
        self.stop_button.setEnabled(False)
        counts = {'Ready': 0, 'Failed': 0, 'Cancelled': 0}
        for row in range(self.table.rowCount()):
            state = self.table.item(row, 2).text()
            if state == 'Pending':
                state = 'Cancelled'
                self.table.setItem(row, 2, QTableWidgetItem(state))
                self.table.setItem(row, 4, QTableWidgetItem('Stopped before acquisition'))
            counts[state] = counts.get(state, 0) + 1
        summary = f'{counts["Ready"]} ready, {counts["Failed"]} failed, {counts["Cancelled"]} cancelled'
        self.progress_bar.setFormat(summary)
        self.append_log(f'Batch finished: {summary}')

    def add_dataset(self, dataset: MarketDataset):
        if not isinstance(dataset, MarketDataset):
            raise TypeError('The charting layer accepts MarketDataset objects.')
        key = (dataset.provider, dataset.symbol, dataset.interval, dataset.timezone)
        self.datasets[key] = dataset
        self.chart_info_cache.pop(key, None)
        index = next((i for i in range(self.chart_asset_combo.count())
                      if self.chart_asset_combo.itemData(i) == key), -1)
        if index < 0:
            icon = QIcon()
            directory = self.settings.get('stock_icons' if dataset.provider == 'Yahoo' else 'crypto_icons')
            if directory:
                symbol = dataset.symbol
                names = [symbol, symbol.lower(), symbol.lstrip('^').lower()]
                if dataset.provider == 'Binance':
                    quote = self.settings.get('quote_currency', 'USDT')
                    names.append(symbol.removesuffix(quote).lower())
                for name in names:
                    path = Path(directory) / f'{name}.png'
                    if path.is_file():
                        icon = QIcon(str(path))
                        break
            self.chart_asset_combo.addItem(icon, f'{dataset.symbol} · {dataset.provider} · {dataset.interval} · {dataset.timezone}', key)
            index = self.chart_asset_combo.count() - 1
        self.chart_asset_combo.setCurrentIndex(index)
        for widget in (self.chart_asset_combo, self.chart_type_combo, self.chart_period_spin,
                       self.show_chart_btn, self.save_chart_df_btn):
            widget.setEnabled(True)
        self.update_metadata()
        for warning in dataset.warnings:
            self.append_log(f'{dataset.symbol}: {warning}', 'WARN')

    def update_metadata(self):
        dataset = self.datasets.get(self.chart_asset_combo.currentData())
        if dataset is not None:
            trades = 'available' if dataset.dataframe.number_of_trades.notna().any() else 'unavailable'
            self.metadata_label.setText(f'{len(dataset.dataframe):,} rows | {dataset.actual_start} to '
                                        f'{dataset.actual_end} | Trade counts: {trades}')

    def build_ta_charts(self, key):
        if key not in self.chart_info_cache:
            if __package__:
                from .ta_charts import TACharts
            else:
                from ta_charts import TACharts
            dataset = self.datasets[key]
            self.chart_info_cache[key] = TACharts(dataset.symbol, dataset.dataframe.copy(deep=True))
        return self.chart_info_cache[key]

    def update_chart_period_control(self):
        _, period, unit = self.chart_type_combo.currentData()
        self.chart_period_spin.setValue(period)
        self.chart_period_spin.setSuffix(f' {unit}')

    def show_selected_chart(self):
        try:
            info = self.build_ta_charts(self.chart_asset_combo.currentData())
            key, _, _ = self.chart_type_combo.currentData()
            period = self.chart_period_spin.value()
            if key == 'keltner':
                info.do_keltner()
            if key in ('sma', 'sim'):
                info.calc_sma_ema()
            method = {'raw': 'plot_raw', 'pricevol': 'plot_pricevol', 'keltner': 'plot_keltner',
                      'sma': 'plot_sma', 'sim': 'do_sim', 'gauss': 'do_gauss'}[key]
            getattr(info, method)(period)
        except Exception as exc:
            QMessageBox.warning(self, 'Chart failed', str(exc))

    def open_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Open canonical OHLCV CSV', '', 'CSV (*.csv)')
        if path:
            try:
                self.add_dataset(load_csv(path, symbol=Path(path).stem,
                                          timezone=self.timezone_input.text().strip()))
            except Exception as exc:
                QMessageBox.warning(self, 'Import failed', str(exc))

    def save_selected_chart_dataframe(self):
        try:
            info = self.build_ta_charts(self.chart_asset_combo.currentData())
            path, _ = QFileDialog.getSaveFileName(self, 'Save enhanced data', 'enhanced.csv', 'CSV (*.csv)')
            if path:
                info.df.to_csv(path, index_label='dt')
                self.log_console.append(f'Saved {path}')
        except Exception as exc:
            QMessageBox.warning(self, 'Export failed', str(exc))

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.stop_processing()
            event.ignore()
            self.log_console.append('Wait for acquisition to finish before closing.')
        else:
            event.accept()


if __name__ == '__main__':
    from multiprocessing import freeze_support
    freeze_support()
    app = QApplication(sys.argv)
    window = MiniDumperApp()
    window.show()
    sys.exit(app.exec())
