# stylesheet.py
# Copyright (c) 2025, 2026 Hans De Weme
# Licensed under the MIT License (https://opensource.org/licenses/MIT).
# part of the HdW_crypto_data Project
# ----------------------------------------------------------------------
# Modern Dark Theme Stylesheet
# ----------------------------------------------------------------------
DARK_STYLE = """
QMainWindow {
    background-color: #12161f;
}
QWidget {
    background-color: #12161f;
    color: #e2e8f0;
    font-family: "Segoe UI", Arial, sans-serif;
    font-size: 10pt;
}
QGroupBox {
    border: 1px solid #2d3748;
    border-radius: 8px;
    margin-top: 10px;
    padding-top: 14px;
    font-weight: bold;
    color: #90cdf4;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 5px;
}
QLineEdit {
    background-color: #1a202c;
    border: 1px solid #2d3748;
    border-radius: 6px;
    padding: 6px 10px;
    color: #edf2f7;
}
QLineEdit:focus {
    border: 1px solid #3182ce;
}
QListWidget {
    background-color: #1a202c;
    border: 1px solid #2d3748;
    border-radius: 6px;
    padding: 4px;
}
QListWidget::item {
    border-radius: 4px;
    padding: 4px 6px;
}
QListWidget::item:hover {
    background-color: #2d3748;
}
QListWidget::item:selected {
    background-color: #2b6cb0;
    color: #ffffff;
}
QPushButton {
    background-color: #2b6cb0;
    color: #ffffff;
    border: none;
    border-radius: 6px;
    padding: 7px 14px;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #3182ce;
}
QPushButton:pressed {
    background-color: #2c5282;
}
QPushButton:disabled {
    background-color: #2d3748;
    color: #718096;
}
QPushButton#startBtn {
    background-color: #319795;
    font-size: 11pt;
    padding: 10px 20px;
}
QPushButton#startBtn:hover {
    background-color: #38b2ac;
}
QPushButton#stopBtn {
    background-color: #e53e3e;
}
QPushButton#stopBtn:hover {
    background-color: #f56565;
}
QProgressBar {
    background-color: #1a202c;
    border: 1px solid #2d3748;
    border-radius: 6px;
    text-align: center;
    color: #edf2f7;
    height: 18px;
}
QProgressBar::chunk {
    background-color: #3182ce;
    border-radius: 5px;
}
QTableWidget {
    background-color: #1a202c;
    border: 1px solid #2d3748;
    border-radius: 6px;
    gridline-color: #2d3748;
    color: #e2e8f0;
}
QTableWidget::item {
    padding: 4px;
}
QHeaderView::section {
    background-color: #2d3748;
    color: #e2e8f0;
    font-weight: bold;
    border: none;
    padding: 6px;
}
QTextEdit#logConsole {
    background-color: #0d1117;
    border: 1px solid #2d3748;
    border-radius: 6px;
    font-family: "Consolas", "Courier New", monospace;
    font-size: 9pt;
    color: #c9d1d9;
}
QCheckBox {
    spacing: 8px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 3px;
    border: 1px solid #4a5568;
    background-color: #1a202c;
}
QCheckBox::indicator:checked {
    background-color: #3182ce;
    border-color: #3182ce;
}
"""

# Archive-specific refinements leave the showcase's existing theme unchanged.
ARCHIVE_MANAGER_STYLE = DARK_STYLE + """
QWidget { font-family: "Segoe UI"; }
QTableWidget {
    alternate-background-color: #171e2a;
    selection-background-color: #2b6cb0;
    selection-color: #ffffff;
}
QTableWidget::item:selected {
    background-color: #2b6cb0;
    color: #ffffff;
}
QTextEdit {
    background-color: #1a202c;
    border: 1px solid #2d3748;
    border-radius: 6px;
    padding: 8px;
}
QLabel#sectionHeading {
    color: #90cdf4;
    font-weight: bold;
    padding: 3px 0;
}
QLabel#quarantineLocation {
    color: #a0aec0;
}
QPushButton#previewBtn:enabled {
    background-color: #319795;
    border: 1px solid #4fd1c5;
    padding: 8px 16px;
}
QPushButton#previewBtn:enabled:hover { background-color: #38b2ac; }
QPushButton#previewBtn:enabled:pressed { background-color: #287e7c; }
QPushButton#deleteAssetBtn:enabled, QPushButton#confirmDeleteBtn:enabled {
    background-color: #c53030;
}
QPushButton#deleteAssetBtn:enabled:hover, QPushButton#confirmDeleteBtn:enabled:hover {
    background-color: #e53e3e;
}
QPushButton#deleteAssetBtn:enabled:pressed, QPushButton#confirmDeleteBtn:enabled:pressed {
    background-color: #9b2c2c;
}
QSplitter::handle { background-color: #2d3748; }
QScrollBar:vertical { background-color: #12161f; width: 12px; }
QScrollBar:horizontal { background-color: #12161f; height: 12px; }
QScrollBar::handle:vertical { background-color: #4a5568; min-height: 24px; border-radius: 4px; }
QScrollBar::handle:horizontal { background-color: #4a5568; min-width: 24px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
"""
