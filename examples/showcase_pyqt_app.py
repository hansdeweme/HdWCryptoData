# showcase_pyqt_app.py
# Copyright (c) 2025, 2026 Hans De Weme
# Licensed under the MIT License (https://opensource.org/licenses/MIT).
# part of the HdW_crypto_data Project
"""
Minimal standalone PyQt6 GUI to test the hdw_crypto_data package
"""
import os, sys, json
from pathlib  import Path
from datetime import datetime
# pandas_ta can trigger numba cache setup during import; disabling JIT avoids
# startup failures in packaged or sandboxed Python environments.
os.environ.setdefault("NUMBA_DISABLE_JIT", "1")
# PyQt imports
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                             QListWidget, QListWidgetItem, QCheckBox, QProgressBar, QTextEdit, QTableWidget, QTableWidgetItem,
                             QHeaderView, QGroupBox, QSplitter, QMessageBox, QAbstractItemView, QComboBox, QSpinBox,
                             QListView)
from PyQt6.QtCore    import Qt, QThread, pyqtSignal, QSize, QUrl
from PyQt6.QtGui     import QIcon, QColor, QDesktopServices
# local imports
from hdw_crypto_data.binance_vision_dumper import BinanceVisionDumper
from hdw_crypto_data.total_dataset_builder import TotalDatasetBuilder
from hdw_crypto_data.total_dataset_loader  import TotalDatasetLoader
from hdw_crypto_data.symbols               import normalize_symbol as normalize_binance_symbol
from ta_charts                             import TACharts
from stylesheet                            import DARK_STYLE

# ----------------------------------------------------------------------
# Qt Pipeline Worker Thread
# ----------------------------------------------------------------------
class PipelineWorker(QThread):
    log_signal = pyqtSignal(str, str)                      # (level, text)
    coin_started = pyqtSignal(str, int, int)               # (coin, current_idx, total)
    coin_dump_done = pyqtSignal(str, bool, str)            # (coin, success, message)
    coin_maketotal_done = pyqtSignal(str, bool, int, str, str)  # (coin, success, rows, filepath, dataframe_message)
    batch_finished = pyqtSignal(int, int)                  # (successful_count, failed_count)
    progress_signal = pyqtSignal(int, int)                 # (current, total)

    def __init__(self, coins: list[str], settings: dict, do_dump: bool, do_maketotal: bool, force_merge: bool):
        super().__init__()
        self.coins = coins
        self.settings = settings
        self.do_dump = do_dump
        self.do_maketotal = do_maketotal
        self.force_merge = force_merge
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        total_coins = len(self.coins)
        successful = 0
        failed = 0
        quote = self.settings.get("quote_currency", "USDT")
        pad = Path(self.settings.get("spot", "..\\spot"))
        spot_dir = str(pad.resolve())

        self.log_signal.emit("INFO", f"=== Starting Batch Run: {total_coins} asset(s) ===")
        self.log_signal.emit("INFO", f"Target Spot Directory: {spot_dir}")

        for idx, coin in enumerate(self.coins, start=1):
            if self._is_cancelled:
                self.log_signal.emit("WARN", "Batch run was cancelled by user.")
                break

            coin = coin.strip().upper()
            markt = f"{coin}{quote}"
            self.coin_started.emit(coin, idx, total_coins)
            self.progress_signal.emit(idx - 1, total_coins)
            self.log_signal.emit("INFO", f"\n=== [{idx}/{total_coins}] Processing {coin} ({markt}) ===")

            # --------------------------------------------------
            # Step 1: Historical Kline Dump via BinanceVisionDumper
            # --------------------------------------------------
            if self.do_dump:
                self.log_signal.emit("INFO", f"[{coin}] Starting historical 1h klines dump from Binance Vision...")
                try:
                    dumper = BinanceVisionDumper(
                        path_dir_where_to_dump=spot_dir,
                        asset_class="spot",
                        data_type="klines",
                        data_frequency="1h",
                    )
                    dumper.dump_data(
                        tickers=[markt],
                        date_start=None,
                        date_end=None,
                        is_to_update_existing=True,
                        tickers_to_exclude=["UST"],
                    )
                    dumper.delete_outdated_daily_results()
                    self.coin_dump_done.emit(coin, True, "Historical dump complete")
                    self.log_signal.emit("SUCCESS", f"[{coin}] Historical klines downloaded & cleaned up successfully.")
                except Exception as ex:
                    err_msg = f"Dump failed: {ex}"
                    self.coin_dump_done.emit(coin, False, err_msg)
                    self.log_signal.emit("ERROR", f"[{coin}] {err_msg}")
                    failed += 1
                    continue
            else:
                self.coin_dump_done.emit(coin, True, "Skipped (Dump unchecked)")

            # --------------------------------------------------
            # Step 2: Build Total Dataset
            # --------------------------------------------------
            if self.do_maketotal:
                if self._is_cancelled:
                    break
                self.log_signal.emit("INFO", f"[{coin}] Fetching live hourly data & merging into total dataset...")
                try:
                    total_builder = TotalDatasetBuilder(coin, self.settings, force_merge=self.force_merge)
                    result = total_builder.build()
                    df = total_builder.load_total_dataframe(
                        file_path=result.filepath,
                        mode="ta",
                        preferred_tz=self.settings.get("preferred_time_zone", "UTC"),
                    )
                    row_count = len(df)
                    df_msg = f"Loaded TA DataFrame ({row_count:,} rows, {len(df.columns)} columns)"
                    self.coin_maketotal_done.emit(coin, True, row_count, result.filepath, df_msg)
                    self.log_signal.emit("SUCCESS", f"[{coin}] Total dataset created and loaded: {os.path.basename(result.filepath)} ({row_count:,} hourly rows)")
                    successful += 1
                except Exception as ex:
                    err_msg = f"Total dataset build error: {ex}"
                    self.coin_maketotal_done.emit(coin, False, 0, "", err_msg)
                    self.log_signal.emit("ERROR", f"[{coin}] {err_msg}")
                    failed += 1
            else:
                self.coin_maketotal_done.emit(coin, True, 0, "Skipped", "Skipped")
                successful += 1
        self.progress_signal.emit(total_coins, total_coins)
        self.log_signal.emit("INFO", f"\n=== Batch Run Finished: {successful} Succeeded, {failed} Failed ===")
        self.batch_finished.emit(successful, failed)

# ----------------------------------------------------------------------
# Main Application Window
# ----------------------------------------------------------------------
class MiniDumperApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = self.load_settings("settings.json")
        self.worker = None
        self.info = None
        self.quote = self.settings.get("quote_currency", "USDT")
        self.chart_data_files = {}
        self.chart_info_cache = {}
        self.crypto_icon_dir = None

        self.setWindowTitle("Binance TA Charts Pipeline Test GUI")
        self.resize(1180, 760)
        self.setStyleSheet(DARK_STYLE)

        self.init_ui()
        self.load_crypto_icons()

    def load_settings(self, filename: str) -> dict:
        default_settings = {
            "spot": "..\\spot",
            "crypto_icons": ".\\crypto_icons",
            "ticker_icons": ".\\ticker_icons",
            "home": os.getcwd()
        }
        if os.path.exists(filename):
            try:
                with open(filename, "r") as f:
                    data = json.load(f)
                    default_settings.update(data)
            except Exception as ex:
                print(f"Warning: Failed to parse {filename}: {ex}")
        return default_settings

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(12)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        # --------------------------------------------------
        # LEFT PANEL: Asset Selection
        # --------------------------------------------------
        left_widget = QWidget()
        left_widget.setMinimumWidth(340)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        asset_group = QGroupBox("Select Crypto Assets")
        asset_vbox = QVBoxLayout(asset_group)

        # Search Bar
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("🔍 Search asset (e.g. BTC, ETH, SOL, BONK)...")
        self.search_bar.textChanged.connect(self.filter_assets)
        asset_vbox.addWidget(self.search_bar)

        # Asset List
        self.asset_list = QListWidget()
        self.asset_list.setIconSize(QSize(28, 28))
        self.asset_list.setWrapping(True)
        self.asset_list.setFlow(QListView.Flow.LeftToRight)
        self.asset_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.asset_list.setMovement(QListView.Movement.Static)
        self.asset_list.setGridSize(QSize(132, 38))
        self.asset_list.setMinimumWidth(300)
        self.asset_list.setUniformItemSizes(True)
        self.asset_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.asset_list.itemChanged.connect(self.update_selection_counter)
        self.asset_list.itemSelectionChanged.connect(self.update_selection_counter)
        asset_vbox.addWidget(self.asset_list)

        # Custom Ticker Input
        custom_layout = QHBoxLayout()
        self.custom_input = QLineEdit()
        self.custom_input.setPlaceholderText("Or type symbol (e.g. ADA, AVAX)")
        self.add_custom_btn = QPushButton("+ Add")
        self.add_custom_btn.clicked.connect(self.add_custom_ticker)
        self.custom_input.returnPressed.connect(self.add_custom_ticker)
        custom_layout.addWidget(self.custom_input)
        custom_layout.addWidget(self.add_custom_btn)
        asset_vbox.addLayout(custom_layout)

        # Selection Helpers & Counter
        btn_row = QHBoxLayout()
        self.sel_all_btn = QPushButton("Select All")
        self.sel_all_btn.clicked.connect(lambda: self.set_all_checked(True))
        self.clear_sel_btn = QPushButton("Clear")
        self.clear_sel_btn.clicked.connect(lambda: self.set_all_checked(False))
        btn_row.addWidget(self.sel_all_btn)
        btn_row.addWidget(self.clear_sel_btn)
        asset_vbox.addLayout(btn_row)

        self.sel_counter_label = QLabel("0 coins selected")
        self.sel_counter_label.setStyleSheet("color: #a0aec0; font-size: 9pt;")
        asset_vbox.addWidget(self.sel_counter_label)

        left_layout.addWidget(asset_group)
        splitter.addWidget(left_widget)

        # --------------------------------------------------
        # RIGHT PANEL: Pipeline Options, Controls & Results
        # --------------------------------------------------
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

        # Options Group
        opt_group = QGroupBox("Processing Pipeline Options")
        opt_layout = QVBoxLayout(opt_group)

        check_row = QHBoxLayout()
        self.chk_dump = QCheckBox("1. Dump Historical Data (BinanceVisionDumper via multiprocessing)")
        self.chk_dump.setChecked(True)
        self.chk_maketotal = QCheckBox("2. Merge into Total Dataset")
        self.chk_maketotal.setChecked(True)
        self.chk_force_merge = QCheckBox("Force merge if gap exists")
        self.chk_force_merge.setChecked(True)
        check_row.addWidget(self.chk_dump)
        check_row.addWidget(self.chk_maketotal)
        check_row.addWidget(self.chk_force_merge)
        opt_layout.addLayout(check_row)

        pad = Path(self.settings.get("spot", "..\\spot"))
        dir_info = QLabel(f"📁 Spot Target: <span style='color: #63b3ed;'>{pad.resolve()}</span>")
        dir_info.setTextFormat(Qt.TextFormat.RichText)
        opt_layout.addWidget(dir_info)

        right_layout.addWidget(opt_group)

        # Action Buttons & Progress Bar
        action_layout = QHBoxLayout()
        self.start_btn = QPushButton("▶ Start Batch Processing")
        self.start_btn.setObjectName("startBtn")
        self.start_btn.clicked.connect(self.start_processing)

        self.stop_btn = QPushButton("⏹ Stop")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_processing)

        action_layout.addWidget(self.start_btn, stretch=3)
        action_layout.addWidget(self.stop_btn, stretch=1)
        right_layout.addLayout(action_layout)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Ready (%v/%m)")
        right_layout.addWidget(self.progress_bar)

        # Results Summary Table
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["Market", "Historical Dump", "Total CSV", "DataFrame", "Hourly Rows", "Actions"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.setFixedHeight(180)
        right_layout.addWidget(self.table)

        chart_group = QGroupBox("Charting")
        chart_layout = QHBoxLayout(chart_group)

        self.chart_asset_combo = QComboBox()
        self.chart_asset_combo.setMinimumWidth(120)
        self.chart_asset_combo.setPlaceholderText("Asset")
        self.chart_asset_combo.setEnabled(False)

        self.chart_type_combo = QComboBox()
        self.chart_type_combo.setMinimumWidth(260)
        self.chart_type_combo.addItem("Candlesticks + RSI / Stochastic / RoC", {"key": "raw", "unit": "days", "default": 4})
        self.chart_type_combo.addItem("Price + Volume + Trades", {"key": "prijsvol", "unit": "hours", "default": 96})
        self.chart_type_combo.addItem("Keltner Channel Signals", {"key": "keltner", "unit": "days", "default": 32})
        self.chart_type_combo.addItem("Moving Averages", {"key": "sma", "unit": "days", "default": 10})
        self.chart_type_combo.addItem("Bollinger / RSI / STC Signals", {"key": "sim", "unit": "days", "default": 32})
        self.chart_type_combo.addItem("Gaussian Bands + Pivots", {"key": "gauss", "unit": "days", "default": 8})
        self.chart_type_combo.currentIndexChanged.connect(self.update_chart_period_control)
        self.chart_type_combo.setEnabled(False)

        self.chart_period_spin = QSpinBox()
        self.chart_period_spin.setRange(1, 100000)
        self.chart_period_spin.setValue(4)
        self.chart_period_spin.setSuffix(" days")
        self.chart_period_spin.setEnabled(False)

        self.show_chart_btn = QPushButton("Show Chart")
        self.show_chart_btn.setFixedHeight(28)
        self.show_chart_btn.setEnabled(False)
        self.show_chart_btn.clicked.connect(self.show_selected_chart)

        self.save_chart_df_btn = QPushButton("Save Enhanced DataFrame")
        self.save_chart_df_btn.setFixedHeight(28)
        self.save_chart_df_btn.setEnabled(False)
        self.save_chart_df_btn.clicked.connect(self.save_selected_chart_dataframe)

        chart_layout.addWidget(QLabel("Asset"))
        chart_layout.addWidget(self.chart_asset_combo)
        chart_layout.addWidget(QLabel("Chart"))
        chart_layout.addWidget(self.chart_type_combo, stretch=1)
        chart_layout.addWidget(self.chart_period_spin)
        chart_layout.addWidget(self.show_chart_btn)
        chart_layout.addWidget(self.save_chart_df_btn)
        right_layout.addWidget(chart_group)

        # Log Console
        log_header_layout = QHBoxLayout()
        log_label = QLabel("Live Execution Console")
        log_label.setStyleSheet("font-weight: bold; color: #90cdf4;")
        clear_log_btn = QPushButton("Clear Console")
        clear_log_btn.setFixedHeight(24)
        clear_log_btn.clicked.connect(lambda: self.log_console.clear())
        log_header_layout.addWidget(log_label)
        log_header_layout.addStretch()
        log_header_layout.addWidget(clear_log_btn)
        right_layout.addLayout(log_header_layout)

        self.log_console = QTextEdit()
        self.log_console.setObjectName("logConsole")
        self.log_console.setReadOnly(True)
        right_layout.addWidget(self.log_console)

        splitter.addWidget(right_widget)
        splitter.setSizes([360, 820])
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 7)

    def find_crypto_icon_dir(self) -> Path | None:
        configured = Path(self.settings.get("crypto_icons", ".\\crypto_icons")).expanduser()
        candidates = [
            configured,
            Path.cwd() / "crypto_icons",
            Path.cwd() / "gui" / "crypto_icons",
            Path.cwd().parent / "forecast" / "gui" / "crypto_icons",
            configured.parent / "gui" / configured.name,
        ]

        seen = set()
        for candidate in candidates:
            icon_dir = candidate.resolve()
            if icon_dir in seen:
                continue
            seen.add(icon_dir)
            if icon_dir.exists():
                return icon_dir
        return None

    def crypto_icon_for_symbol(self, symbol: str) -> QIcon:
        if not self.crypto_icon_dir:
            return QIcon()

        try:
            normalized_symbol = self.normalize_symbol(symbol)
        except ValueError:
            return QIcon()

        icon_path = self.crypto_icon_dir / f"{normalized_symbol.lower()}.png"
        if icon_path.exists():
            return QIcon(str(icon_path))
        return QIcon()

    def load_crypto_icons(self):
        icon_dir = self.find_crypto_icon_dir()
        self.crypto_icon_dir = icon_dir
        if icon_dir and icon_dir.exists():
            icon_files = sorted([f for f in os.listdir(icon_dir) if f.lower().endswith(".png")])
            for f in icon_files:
                try:
                    coin_symbol = self.normalize_symbol(os.path.splitext(f)[0])
                except ValueError:
                    continue
                item = QListWidgetItem(self.crypto_icon_for_symbol(coin_symbol), coin_symbol)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                self.asset_list.addItem(item)

        if self.asset_list.count() == 0:
            for coin_symbol in ["BONK", "BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX"]:
                item = QListWidgetItem(self.crypto_icon_for_symbol(coin_symbol), coin_symbol)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                self.asset_list.addItem(item)

        # Pre-check BONK by default for testing
        for i in range(self.asset_list.count()):
            item = self.asset_list.item(i)
            if item.text() in ["BONK"]:
                item.setCheckState(Qt.CheckState.Checked)

        self.update_selection_counter()

    def filter_assets(self, text: str):
        query = text.strip().upper()
        for i in range(self.asset_list.count()):
            item = self.asset_list.item(i)
            item.setHidden(query not in item.text())

    def add_custom_ticker(self):
        raw_text = self.custom_input.text().strip().upper()
        if not raw_text:
            return

        symbols = []
        invalid_symbols = []
        for raw_symbol in raw_text.replace(",", " ").split():
            try:
                symbol = self.normalize_symbol(raw_symbol)
                symbols.append(symbol)
            except ValueError:
                invalid_symbols.append(raw_symbol)

        if invalid_symbols:
            QMessageBox.warning(
                self,
                "Invalid Symbol",
                "Ticker symbols may contain only letters and numbers."
            )
            self.custom_input.selectAll()
            return

        for sym in symbols:
            existing_item = None
            for i in range(self.asset_list.count()):
                if self.asset_list.item(i).text() == sym:
                    existing_item = self.asset_list.item(i)
                    break

            if existing_item:
                existing_item.setCheckState(Qt.CheckState.Checked)
                existing_item.setSelected(True)
            else:
                item = QListWidgetItem(self.crypto_icon_for_symbol(sym), sym)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked)
                item.setSelected(True)
                self.asset_list.insertItem(0, item)

        self.custom_input.clear()
        self.update_selection_counter()

    def set_all_checked(self, state: bool):
        target = Qt.CheckState.Checked if state else Qt.CheckState.Unchecked
        for i in range(self.asset_list.count()):
            item = self.asset_list.item(i)
            if not item.isHidden():
                item.setCheckState(target)
        self.update_selection_counter()

    def get_selected_coins(self) -> list[str]:
        selected = []
        for i in range(self.asset_list.count()):
            item = self.asset_list.item(i)
            if item.checkState() == Qt.CheckState.Checked or item.isSelected():
                try:
                    symbol = self.normalize_symbol(item.text())
                    selected.append(symbol)
                except ValueError:
                    continue
        return list(dict.fromkeys(selected))

    def normalize_symbol(self, symbol: str) -> str:
        symbol = str(symbol).strip().upper()
        if symbol.endswith(self.quote):
            symbol = symbol[:-len(self.quote)]
        return normalize_binance_symbol(symbol)

    def update_selection_counter(self):
        selected = self.get_selected_coins()
        count = len(selected)
        self.sel_counter_label.setText(f"{count} coin(s) selected")
        self.start_btn.setText(f"▶ Start Batch Processing ({count} coins)" if count > 0 else "▶ Select coins to start")
        self.start_btn.setEnabled(count > 0)

    # --------------------------------------------------
    # Batch Execution Control
    # --------------------------------------------------
    def start_processing(self):
        selected_coins = self.get_selected_coins()
        if not selected_coins:
            QMessageBox.warning(self, "No Coins Selected", "Please select at least one crypto coin to process.")
            return

        do_dump = self.chk_dump.isChecked()
        do_maketotal = self.chk_maketotal.isChecked()
        force_merge = self.chk_force_merge.isChecked()

        if not do_dump and not do_maketotal:
            QMessageBox.warning(self, "No Action Selected", "Please enable at least one pipeline step.")
            return

        # Prepare Table
        self.table.setRowCount(len(selected_coins))
        self.chart_data_files.clear()
        self.chart_info_cache.clear()
        self.chart_asset_combo.clear()
        self.chart_asset_combo.setEnabled(False)
        self.chart_type_combo.setEnabled(False)
        self.chart_period_spin.setEnabled(False)
        self.show_chart_btn.setEnabled(False)
        self.save_chart_df_btn.setEnabled(False)
        for row, coin in enumerate(selected_coins):
            self.table.setItem(row, 0, QTableWidgetItem(f"{coin}{self.quote}"))
            self.table.setItem(row, 1, QTableWidgetItem("⏳ Pending"))
            self.table.setItem(row, 2, QTableWidgetItem("⏳ Pending"))
            self.table.setItem(row, 3, QTableWidgetItem("⏳ Pending"))
            self.table.setItem(row, 4, QTableWidgetItem("-"))
            self.table.setItem(row, 5, QTableWidgetItem("-"))

        self.progress_bar.setMaximum(len(selected_coins))
        self.progress_bar.setValue(0)

        # UI state
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

        # Launch Worker Thread
        self.worker = PipelineWorker(
            coins=selected_coins,
            settings=self.settings,
            do_dump=do_dump,
            do_maketotal=do_maketotal,
            force_merge=force_merge,
        )
        self.worker.log_signal.connect(self.append_log)
        self.worker.coin_started.connect(self.on_coin_started)
        self.worker.coin_dump_done.connect(self.on_coin_dump_done)
        self.worker.coin_maketotal_done.connect(self.on_coin_maketotal_done)
        self.worker.progress_signal.connect(self.on_progress)
        self.worker.batch_finished.connect(self.on_batch_finished)
        self.worker.start()

    def stop_processing(self):
        if self.worker and self.worker.isRunning():
            self.append_log("WARN", "Stopping after current step completes...")
            self.worker.cancel()
            self.stop_btn.setEnabled(False)

    def find_coin_row(self, coin: str) -> int:
        markt = f"{coin}{self.quote}"
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item and item.text() == markt:
                return r
        return -1

    def on_coin_started(self, coin: str, current_idx: int, total: int):
        row = self.find_coin_row(coin)
        if row >= 0:
            self.table.setItem(row, 1, QTableWidgetItem("🔄 Dumping..."))
        self.progress_bar.setFormat(f"Processing {coin} ({current_idx}/{total})")

    def on_coin_dump_done(self, coin: str, success: bool, msg: str):
        row = self.find_coin_row(coin)
        if row >= 0:
            status_item = QTableWidgetItem("✅ Succeeded" if success else "❌ Failed")
            status_item.setForeground(QColor("#48bb78" if success else "#f56565"))
            self.table.setItem(row, 1, status_item)
            if success and self.chk_maketotal.isChecked():
                self.table.setItem(row, 2, QTableWidgetItem("🔄 Merging..."))
                self.table.setItem(row, 3, QTableWidgetItem("Loading..."))

    def on_coin_maketotal_done(self, coin: str, success: bool, rows: int, filepath: str, dataframe_message: str):
        row = self.find_coin_row(coin)
        if row >= 0:
            status_item = QTableWidgetItem("✅ Succeeded" if success else "❌ Failed")
            status_item.setForeground(QColor("#48bb78" if success else "#f56565"))
            self.table.setItem(row, 2, status_item)
            dataframe_item = QTableWidgetItem(dataframe_message)
            dataframe_item.setForeground(QColor("#48bb78" if success else "#f56565"))
            self.table.setItem(row, 3, dataframe_item)
            self.table.setItem(row, 4, QTableWidgetItem(f"{rows:,}" if rows > 0 else "-"))

            if success and filepath and os.path.exists(filepath):
                action_widget = QWidget()
                action_layout = QHBoxLayout(action_widget)
                action_layout.setContentsMargins(0, 0, 0, 0)
                action_layout.setSpacing(6)

                open_btn = QPushButton("Open CSV")
                open_btn.setFixedHeight(24)
                open_btn.clicked.connect(lambda checked, p=filepath: self.open_file(p))

                action_layout.addWidget(open_btn)
                action_layout.addStretch()
                self.table.setCellWidget(row, 5, action_widget)
                self.register_chart_asset(coin, filepath)

    def open_file(self, path: str):
        if os.path.exists(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def register_chart_asset(self, coin: str, filepath: str):
        coin = self.normalize_symbol(coin)
        self.chart_data_files[coin] = filepath
        if self.chart_asset_combo.findText(coin) < 0:
            self.chart_asset_combo.addItem(coin)
        self.chart_asset_combo.setEnabled(True)
        self.chart_type_combo.setEnabled(True)
        self.chart_period_spin.setEnabled(True)
        self.show_chart_btn.setEnabled(True)
        self.save_chart_df_btn.setEnabled(True)
        self.update_chart_period_control()

    def update_chart_period_control(self):
        chart_data = self.chart_type_combo.currentData() or {}
        unit = chart_data.get("unit", "days")
        default = chart_data.get("default", 4)
        is_sequence = chart_data.get("key") == "all"

        self.chart_period_spin.setEnabled(not is_sequence and self.chart_asset_combo.count() > 0)
        if not is_sequence:
            self.chart_period_spin.setValue(default)
        if unit == "hours":
            self.chart_period_spin.setSuffix(" hours")
        elif unit == "days":
            self.chart_period_spin.setSuffix(" days")
        else:
            self.chart_period_spin.setSuffix("")

    def load_chart_dataframe(self, coin: str):
        filepath = self.chart_data_files.get(coin)
        if not filepath or not os.path.exists(filepath):
            raise FileNotFoundError(f"No total CSV available for {coin}.")
        total_loader = self.make_total_loader(coin)
        return total_loader.load_total_dataframe(
            file_path=filepath,
            mode="ta",
            preferred_tz=self.settings.get("preferred_time_zone", "UTC"),
        )

    def build_ta_charts(self, coin: str):
        coin = self.normalize_symbol(coin)
        if coin in self.chart_info_cache:
            self.info = self.chart_info_cache[coin]
            return self.info

        df = self.load_chart_dataframe(coin)
        if df is None or df.empty:
            raise ValueError(f"{coin.upper()} DataFrame is empty.")
        self.info = TACharts(coin, df)
        if getattr(self.info, "df", None) is None or self.info.df.empty:
            raise ValueError(f"{coin.upper()} TACharts did not initialize a usable DataFrame.")
        self.chart_info_cache[coin] = self.info
        return self.info

    def show_selected_chart(self):
        coin = self.chart_asset_combo.currentText().strip()
        chart_data = self.chart_type_combo.currentData() or {}
        chart_key = chart_data.get("key")
        period = self.chart_period_spin.value()

        if not coin or not chart_key:
            QMessageBox.warning(self, "Chart Selection Missing", "Choose an asset and chart type first.")
            return

        try:
            info = self.build_ta_charts(coin)
            if chart_key == "raw":
                info.plot_raw(period)
            elif chart_key == "prijsvol":
                info.plot_prijsvol(period)
            elif chart_key == "keltner":
                info.do_keltner()
                info.plot_keltner(period)
            elif chart_key == "sma":
                info.calc_sma_ema()
                info.plot_sma(period)
            elif chart_key == "sim":
                info.calc_sma_ema()
                info.do_sim(period)
            elif chart_key == "gauss":
                info.do_gauss(period)
        except Exception as ex:
            QMessageBox.warning(self, "Chart Failed", f"Could not render the selected chart:\n\n{ex}")

    def save_selected_chart_dataframe(self):
        coin = self.chart_asset_combo.currentText().strip()
        if not coin:
            QMessageBox.warning(self, "No Asset", "Choose an asset first.")
            return
        try:
            info = self.info if self.info and self.normalize_symbol(self.info.MARKET) == coin else self.build_ta_charts(coin)
            info.save_data()
            QMessageBox.information(self, "Saved", f"Saved enhanced DataFrame for {coin}.")
        except Exception as ex:
            QMessageBox.warning(self, "Save Failed", f"Could not save enhanced DataFrame:\n\n{ex}")

    def make_total_loader(self, coin: str) -> TotalDatasetLoader:
        return TotalDatasetLoader(coin, self.settings)

    def on_progress(self, current: int, total: int):
        self.progress_bar.setValue(current)

    def on_batch_finished(self, successful: int, failed: int):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress_bar.setValue(self.progress_bar.maximum())
        self.progress_bar.setFormat(f"Completed: {successful} succeeded, {failed} failed")

        QMessageBox.information(
            self,
            "Batch Complete",
            f"Batch processing finished!\n\nSuccessful: {successful}\nFailed: {failed}",
        )

    def append_log(self, level: str, text: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        color_map = {
            "INFO": "#90cdf4",
            "SUCCESS": "#68d391",
            "WARN": "#f6e05e",
            "ERROR": "#fc8181",
        }
        color = color_map.get(level, "#e2e8f0")
        formatted = f"<span style='color:#718096;'>[{timestamp}]</span> <span style='color:{color}; font-weight: bold;'>[{level}]</span> <span style='color:#edf2f7;'>{text}</span>"
        self.log_console.append(formatted)


# ----------------------------------------------------------------------
# Application Entry Point
# ----------------------------------------------------------------------
if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()

    app = QApplication(sys.argv)
    window = MiniDumperApp()
    window.show()
    sys.exit(app.exec())
