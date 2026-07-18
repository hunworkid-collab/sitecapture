from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from .widgets import tab_layout


class LogView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        root = tab_layout(self)
        log_box = QFrame()
        log_box.setObjectName("card")
        log_layout = QVBoxLayout(log_box)
        log_layout.setContentsMargins(20, 18, 20, 20)
        log_layout.setSpacing(12)
        title = QLabel("실행 로그")
        title.setObjectName("cardTitle")
        log_layout.addWidget(title)
        self.log_header_layout = QHBoxLayout()
        self.log_help_label = QLabel("최근 실행 메시지를 표시합니다.")
        self.log_help_label.setObjectName("helpText")
        self.log_help_label.setWordWrap(True)
        self.log_header_layout.addWidget(self.log_help_label, 1)
        self.log_header_layout.addStretch(1)
        self.clear_log_button = QPushButton("로그 비우기")
        self.log_header_layout.addWidget(self.clear_log_button)
        log_layout.addLayout(self.log_header_layout)
        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumBlockCount(5000)
        log_layout.addWidget(self.log_edit)
        root.addWidget(log_box, 1)
