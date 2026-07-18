from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..execution import JobStatus, JobUpdate
from ..models import RunConfig
from ..query import build_search_jobs
from .failure import short_failure_reason
from .widgets import create_card, tab_layout


_JOB_TEXT = {
    JobStatus.PENDING: "대기",
    JobStatus.RUNNING: "실행 중",
    JobStatus.SUCCESS: "성공",
    JobStatus.FAILED: "실패",
    JobStatus.CANCELLED: "취소",
}
_DB_STATUS_TEXT = {
    "pending": "대기",
    "running": "실행 중",
    "success": "완료",
    "no_results_captured": "결과 없음",
    "failed": "실패",
    "cancelled": "중단",
    "skipped_existing": "기존 파일",
}


class ResultsView(QWidget):
    job_completed = Signal(object)

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

    def prepare_jobs(self, config: RunConfig) -> int:
        jobs = build_search_jobs(config)
        self.job_table.clearContents()
        self.job_table.setRowCount(len(jobs))
        self.resize_to_rows()
        for row, job in enumerate(jobs):
            for column, value in enumerate(
                (
                    str(job.sequence),
                    job.keyword_normalized,
                    job.domain,
                    job.query,
                    _JOB_TEXT[JobStatus.PENDING],
                    "",
                    "",
                )
            ):
                self.job_table.setItem(row, column, QTableWidgetItem(value))
        return len(jobs)

    def restore_jobs(self, rows: list[tuple[int, str, str, str]]) -> None:
        for sequence, status, screenshot_path, error_message in rows:
            row = sequence - 1
            if row < 0 or row >= self.job_table.rowCount():
                continue
            self._set_table_text(row, 4, _DB_STATUS_TEXT.get(status, status))
            self._set_saved_file_cell(
                row,
                Path(screenshot_path) if screenshot_path else None,
            )
            message = (
                short_failure_reason(error_message)
                if status == "failed"
                else error_message
            )
            self._set_table_text(row, 6, message)
            self._set_failure_row_style(row, status == "failed")

    def apply_job_update(self, update: JobUpdate) -> bool:
        row = update.index - 1
        if row < 0 or row >= self.job_table.rowCount():
            return False
        self._set_table_text(row, 4, _JOB_TEXT[update.status])
        self._set_saved_file_cell(row, update.path)
        message = (
            short_failure_reason(update.message)
            if update.status is JobStatus.FAILED
            else update.message
        )
        self._set_table_text(row, 6, message)
        message_item = self.job_table.item(row, 6)
        if message_item is not None:
            message_item.setToolTip(update.message)
        self._set_failure_row_style(row, update.status is JobStatus.FAILED)
        sequence_item = self.job_table.item(row, 0)
        if sequence_item is not None:
            self.job_table.scrollToItem(sequence_item)
        if update.status in {
            JobStatus.SUCCESS,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }:
            self.job_completed.emit(update)
        return True

    def _set_table_text(self, row: int, column: int, text: str) -> None:
        item = self.job_table.item(row, column)
        if item is None:
            item = QTableWidgetItem()
            self.job_table.setItem(row, column, item)
        item.setText(text)

    def _set_saved_file_cell(self, row: int, path: Path | None) -> None:
        if path is None:
            self.job_table.removeCellWidget(row, 5)
            self._set_table_text(row, 5, "")
            return

        item = self.job_table.item(row, 5)
        if item is None:
            item = QTableWidgetItem()
            self.job_table.setItem(row, 5, item)
        item.setText(path.name)
        item.setToolTip(str(path))

        cell = QWidget()
        cell.setToolTip(str(path))
        layout = QHBoxLayout(cell)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(4)
        filename_label = QLabel(path.name)
        filename_label.setToolTip(str(path))
        filename_label.setMinimumWidth(0)
        filename_label.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        open_folder_button = QToolButton()
        open_folder_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)
        )
        open_folder_button.setToolTip(f"저장 폴더 열기\n{path.parent}")
        open_folder_button.clicked.connect(
            lambda _checked=False, folder=path.parent: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(folder))
            )
        )
        layout.addWidget(filename_label, 1)
        layout.addWidget(open_folder_button)
        self.job_table.setCellWidget(row, 5, cell)

    def _set_failure_row_style(self, row: int, failed: bool) -> None:
        background = QBrush(QColor("#ffe8e8")) if failed else QBrush()
        for column in range(self.job_table.columnCount()):
            item = self.job_table.item(row, column)
            if item is not None:
                item.setBackground(background)
