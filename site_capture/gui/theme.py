from __future__ import annotations

from typing import Final


GUI_STYLE_SHEET: Final = """
QMainWindow {
    background: #F5F7FA;
    color: #172033;
    font-family: "A2Z", "Malgun Gothic", sans-serif;
    font-size: 14px;
}

QWidget#pageHeader {
    background: #FFFFFF;
    border-bottom: 1px solid #E2E8F0;
}

QLabel#pageTitle {
    font-size: 22px;
    font-weight: 700;
    color: #172033;
}

QLabel#pageSubtitle, QLabel#helpText {
    color: #64748B;
    font-size: 15px;
    line-height: 1.45;
}

QLabel#appFooter {
    color: #94A3B8;
    font-size: 12px;
    padding: 8px 24px 10px;
}

QFrame#card, QFrame#statusCard {
    background: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 12px;
}

QFrame#completionCard {
    background: #F0FDF4;
    border: 1px solid #86EFAC;
    border-radius: 10px;
}

QLabel#cardTitle {
    color: #172033;
    font-size: 16px;
    font-weight: 700;
}

QLabel#stateBadge {
    background: #E0E7FF;
    border-radius: 10px;
    color: #3730A3;
    font-weight: 700;
    padding: 4px 10px;
}

QLabel#summaryTitle {
    color: #172033;
    font-size: 16px;
    font-weight: 700;
}

QLineEdit, QPlainTextEdit, QComboBox, QSpinBox {
    background: #FFFFFF;
    border: 1px solid #CBD5E1;
    border-radius: 7px;
    padding: 8px 10px;
    selection-background-color: #BFDBFE;
    min-width: 0;
}

QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus {
    border: 2px solid #2563EB;
}

QPushButton {
    background: #FFFFFF;
    border: 1px solid #CBD5E1;
    border-radius: 7px;
    color: #334155;
    min-height: 22px;
    padding: 8px 14px;
    font-size: 14px;
}

QPushButton:hover {
    background: #F8FAFC;
    border-color: #94A3B8;
}

QPushButton:disabled {
    background: #F8FAFC;
    border-color: #E2E8F0;
    color: #94A3B8;
}

QPushButton#primaryButton {
    background: #2563EB;
    border-color: #2563EB;
    color: #FFFFFF;
    font-weight: 700;
}

QPushButton#primaryButton:hover {
    background: #1D4ED8;
    border-color: #1D4ED8;
}

QPushButton#dangerButton {
    border-color: #FCA5A5;
    color: #DC2626;
}

QDialog#validationDialog {
    background: #FFFFFF;
}

QLabel#validationDialogTitle {
    color: #172033;
    font-size: 18px;
    font-weight: 700;
}

QFrame#validationMessagePanel {
    background: #EFF6FF;
    border: 1px solid #BFDBFE;
    border-radius: 8px;
}

QLabel#validationDialogMessage {
    color: #1E3A5F;
    font-size: 14px;
}

QTabWidget::pane {
    border: 0;
    background: #F5F7FA;
}

QTabWidget::tab-bar {
    left: 32px;
}

QTabBar::tab {
    background: transparent;
    border: 0;
    color: #64748B;
    margin-right: 18px;
    padding: 10px 4px;
}

QTabBar::tab:selected {
    border-bottom: 2px solid #2563EB;
    color: #1D4ED8;
    font-weight: 700;
}

QGroupBox#advancedSettings {
    border: 1px solid #E2E8F0;
    border-radius: 8px;
    font-weight: 700;
    margin-top: 12px;
    padding: 16px 12px 10px 12px;
}

QGroupBox#advancedSettings::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 5px;
}

QProgressBar {
    background: #E2E8F0;
    border: 0;
    border-radius: 6px;
    color: #FFFFFF;
    height: 12px;
    text-align: center;
}

QProgressBar::chunk {
    background: #2563EB;
    border-radius: 6px;
}

QTableWidget {
    background: #FFFFFF;
    alternate-background-color: #F8FAFC;
    border: 1px solid #E2E8F0;
    border-radius: 8px;
    gridline-color: #E2E8F0;
    selection-background-color: #DBEAFE;
    selection-color: #172033;
}

QHeaderView::section {
    background: #F8FAFC;
    border: 0;
    border-bottom: 1px solid #E2E8F0;
    color: #475569;
    font-weight: 700;
    padding: 9px;
}
"""
