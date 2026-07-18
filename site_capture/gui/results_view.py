from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QScrollArea,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from .widgets import create_card, tab_layout


class ResultsView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.results_scroll_area = QScrollArea()
        self.results_scroll_area.setWidgetResizable(True)
        self.results_scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        root = tab_layout(content)
        table_box, table_layout = create_card(
            "작업 결과",
            "작업별 저장 상태와 결과 파일을 확인합니다.",
        )
        self.job_table = QTableWidget(0, 7)
        self.job_table.setHorizontalHeaderLabels(
            ["번호", "키워드", "도메인", "검색식", "상태", "저장 파일", "메시지"]
        )
        self.job_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.job_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.job_table.setAlternatingRowColors(True)
        self.job_table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.job_table.setShowGrid(False)
        self.job_table.verticalHeader().setVisible(False)
        self.job_table.setColumnWidth(0, 60)
        self.job_table.setColumnWidth(1, 120)
        self.job_table.setColumnWidth(2, 190)
        self.job_table.setColumnWidth(3, 250)
        self.job_table.setColumnWidth(4, 90)
        self.job_table.setColumnWidth(5, 220)
        self.job_table.horizontalHeader().setStretchLastSection(True)
        self.resize_to_rows()
        table_layout.addWidget(self.job_table)
        root.addWidget(table_box)
        root.addStretch(1)
        self.results_scroll_area.setWidget(content)
        layout.addWidget(self.results_scroll_area)

    @staticmethod
    def resize_table_to_rows(table: QTableWidget, maximum_rows: int) -> None:
        visible_rows = min(max(1, table.rowCount()), maximum_rows)
        default_height = table.verticalHeader().defaultSectionSize()
        content_height = sum(
            table.rowHeight(row) if row < table.rowCount() else default_height
            for row in range(visible_rows)
        )
        table.setFixedHeight(
            table.horizontalHeader().height() + content_height + table.frameWidth() * 2
        )

    def resize_to_rows(self) -> None:
        self.resize_table_to_rows(self.job_table, maximum_rows=8)
