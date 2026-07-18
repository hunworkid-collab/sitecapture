from __future__ import annotations

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..execution import BatchState, JobStatus, JobUpdate
from ..keyword_io import normalize_keywords
from ..paths import normalize_keyword
from ..query import normalize_domains
from .widgets import create_card, tab_layout


_STATE_TEXT = {
    BatchState.IDLE.value: "대기",
    BatchState.PREPARING.value: "준비 중",
    BatchState.RUNNING.value: "실행 중",
    BatchState.PAUSED.value: "일시정지",
    BatchState.USER_ACTION_REQUIRED.value: "사용자 조치 필요",
    BatchState.STOPPING.value: "중단 처리 중",
    BatchState.STOPPED.value: "중단됨",
    BatchState.COMPLETED.value: "완료",
    BatchState.FAILED.value: "실패",
}
_FINAL_STATES = {
    BatchState.STOPPING.value,
    BatchState.STOPPED.value,
    BatchState.COMPLETED.value,
    BatchState.FAILED.value,
}


class ExecutionView(QWidget):
    keyword_file_requested = Signal()
    keywords_normalized = Signal(int)
    domain_validation_failed = Signal(str)
    test_requested = Signal()
    start_requested = Signal()
    retry_requested = Signal()
    pause_requested = Signal()
    resume_requested = Signal()
    stop_requested = Signal()
    user_action_confirmed = Signal()

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.execution_scroll_area = QScrollArea()
        self.execution_scroll_area.setWidgetResizable(True)
        self.execution_scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = tab_layout(content)
        input_row = QHBoxLayout()
        input_row.setSpacing(16)
        self._build_keyword_card(input_row)
        self._build_target_card(input_row)
        layout.addLayout(input_row)
        self.execution_columns_layout = QHBoxLayout()
        self.execution_columns_layout.setSpacing(16)
        self.execution_columns_layout.addWidget(self._build_control_card(), 1)
        self.execution_columns_layout.addWidget(self._build_recent_results_card(), 1)
        layout.addLayout(self.execution_columns_layout)
        layout.addStretch(1)
        self.execution_scroll_area.setWidget(content)
        root.addWidget(self.execution_scroll_area)
        self._recent_result_rows: list[tuple[int, str, str, JobStatus]] = []
        self._last_state = BatchState.IDLE.value
        self._connect_widgets()
        self._refresh_input_summary()

    def _connect_widgets(self) -> None:
        self.keyword_edit.textChanged.connect(self._refresh_input_summary)
        self.load_txt_button.clicked.connect(self.keyword_file_requested)
        self.dedupe_button.clicked.connect(self._normalize_keyword_text)
        self.clear_button.clicked.connect(self.keyword_edit.clear)
        self.domain_edit.returnPressed.connect(self._add_domain_from_input)
        self.add_domain_button.clicked.connect(self._add_domain_from_input)
        self.remove_domain_button.clicked.connect(self._remove_selected_domain)
        self.domain_list.currentRowChanged.connect(self._update_remove_button)
        self.test_button.clicked.connect(self.test_requested)
        self.start_button.clicked.connect(self.start_requested)
        self.retry_failed_button.clicked.connect(self.retry_requested)
        self.pause_button.clicked.connect(self.pause_requested)
        self.resume_button.clicked.connect(self.resume_requested)
        self.stop_button.clicked.connect(self.stop_requested)
        self.user_action_button.clicked.connect(self.user_action_confirmed)

    def keywords(self) -> tuple[str, ...]:
        return normalize_keywords(self.keyword_edit.toPlainText().splitlines())

    def set_keywords(self, keywords: tuple[str, ...]) -> None:
        self.keyword_edit.setPlainText("\n".join(keywords))

    def current_keyword(self) -> str:
        return normalize_keyword(self.keyword_edit.textCursor().block().text())

    def domains(self) -> tuple[str, ...]:
        return normalize_domains(
            tuple(
                self.domain_list.item(index).text()
                for index in range(self.domain_list.count())
            )
        )

    def set_domains(self, domains: tuple[str, ...]) -> None:
        self.domain_list.clear()
        for domain in domains:
            self.domain_list.addItem(QListWidgetItem(domain))
        self._refresh_input_summary()

    def add_domain(self, domain: str) -> None:
        self.set_domains(normalize_domains((*self.domains(), domain)))

    def set_input_enabled(self, enabled: bool) -> None:
        for widget in (
            self.keyword_edit,
            self.load_txt_button,
            self.dedupe_button,
            self.clear_button,
            self.domain_edit,
            self.add_domain_button,
            self.domain_list,
            self.remove_domain_button,
        ):
            widget.setEnabled(enabled)

    def set_start_actions_enabled(self, enabled: bool) -> None:
        self.test_button.setEnabled(enabled)
        self.start_button.setEnabled(enabled)

    def set_retry_available(self, available: bool) -> None:
        self.retry_failed_button.setEnabled(available)

    def disable_run_controls(self) -> None:
        for button in (
            self.pause_button,
            self.resume_button,
            self.stop_button,
            self.user_action_button,
        ):
            button.setEnabled(False)

    def set_user_action_confirmed(self) -> None:
        self.user_action_button.setEnabled(False)

    def set_run_state(self, state: str, message: str, *, active: bool) -> None:
        self._last_state = state
        self.state_label.setText(_STATE_TEXT.get(state, state))
        self.detail_label.setText(message)
        self.pause_button.setEnabled(active and state == BatchState.RUNNING.value)
        self.resume_button.setEnabled(active and state == BatchState.PAUSED.value)
        self.stop_button.setEnabled(active and state not in _FINAL_STATES)
        self.user_action_button.setEnabled(
            active and state == BatchState.USER_ACTION_REQUIRED.value
        )

    @property
    def last_state(self) -> str:
        return self._last_state

    def show_completion(self, message: str) -> None:
        self.completion_detail_label.setText(message)
        self.completion_card.setVisible(True)

    def hide_completion(self) -> None:
        self.completion_card.setVisible(False)

    @Slot()
    def _add_domain_from_input(self) -> None:
        try:
            self.add_domain(self.domain_edit.text())
        except ValueError as exc:
            self.domain_validation_failed.emit(str(exc))
            return
        self.domain_edit.clear()

    @Slot()
    def _remove_selected_domain(self) -> None:
        row = self.domain_list.currentRow()
        if row < 0:
            return
        self.domain_list.takeItem(row)
        self._refresh_input_summary()

    @Slot(int)
    def _update_remove_button(self, row: int) -> None:
        self.remove_domain_button.setEnabled(row >= 0)

    @Slot()
    def _refresh_input_summary(self) -> None:
        raw_count = sum(1 for line in self.keyword_edit.toPlainText().splitlines() if line.strip())
        keyword_count = len(self.keywords())
        domain_count = len(self.domains())
        self.keyword_count_label.setText(
            f"입력 {raw_count}개 · 유효 {keyword_count}개 · "
            f"제거 대상 {max(0, raw_count - keyword_count)}개"
        )
        self.capture_estimate_label.setText(
            f"키워드 {keyword_count}개 × 도메인 {domain_count}개 = "
            f"총 {keyword_count * domain_count}건 캡처 예정"
        )

    @Slot()
    def _normalize_keyword_text(self) -> None:
        keywords = self.keywords()
        self.set_keywords(keywords)
        self.keywords_normalized.emit(len(keywords))

    def _build_keyword_card(self, root: QHBoxLayout) -> None:
        card, layout = create_card(
            "1. 키워드",
            "한 줄에 하나씩 입력하세요. 같은 키워드는 실행 전에 정리할 수 있습니다.",
        )
        self.keyword_edit = QPlainTextEdit()
        self.keyword_edit.setPlaceholderText("예시\n키워드 1\n키워드 2")
        self.keyword_edit.setFixedHeight(150)
        layout.addWidget(self.keyword_edit)
        row = QHBoxLayout()
        self.load_txt_button = QPushButton("TXT 불러오기")
        self.dedupe_button = QPushButton("중복 정리")
        self.clear_button = QPushButton("지우기")
        self.keyword_count_label = QLabel()
        row.addWidget(self.load_txt_button)
        row.addWidget(self.dedupe_button)
        row.addWidget(self.clear_button)
        row.addStretch(1)
        row.addWidget(self.keyword_count_label)
        layout.addLayout(row)
        self.capture_estimate_label = QLabel()
        self.capture_estimate_label.setObjectName("helpText")
        layout.addWidget(self.capture_estimate_label)
        root.addWidget(card, 1)

    def _build_target_card(self, root: QHBoxLayout) -> None:
        card, layout = create_card(
            "2. 검색 대상",
            "도메인을 추가하면 키워드와 조합해 검색합니다.",
        )
        domain_row = QHBoxLayout()
        self.domain_edit = QLineEdit()
        self.domain_edit.setPlaceholderText("example.com")
        self.add_domain_button = QPushButton("도메인 추가")
        domain_row.addWidget(self.domain_edit)
        domain_row.addWidget(self.add_domain_button)
        layout.addLayout(domain_row)
        self.domain_list = QListWidget()
        self.domain_list.setFixedHeight(96)
        layout.addWidget(self.domain_list)
        self.remove_domain_button = QPushButton("선택 도메인 삭제")
        self.remove_domain_button.setEnabled(False)
        layout.addWidget(self.remove_domain_button)
        root.addWidget(card, 1)

    def _build_control_card(self) -> QFrame:
        group, layout = create_card(
            "3. 실행",
            "현재 입력값을 확인한 뒤 캡처를 시작합니다.",
        )
        self.control_buttons_layout = QGridLayout()
        self.control_buttons_layout.setHorizontalSpacing(8)
        self.control_buttons_layout.setVerticalSpacing(8)
        self.test_button = QPushButton("현재 키워드 1건 테스트")
        self.start_button = QPushButton("캡처 시작")
        self.start_button.setObjectName("primaryButton")
        self.retry_failed_button = QPushButton("실패 작업 다시 실행")
        self.pause_button = QPushButton("일시정지")
        self.resume_button = QPushButton("재개")
        self.stop_button = QPushButton("중단")
        self.stop_button.setObjectName("dangerButton")
        self.user_action_button = QPushButton("Chrome 확인 완료")
        for button in (
            self.pause_button,
            self.resume_button,
            self.stop_button,
            self.user_action_button,
            self.retry_failed_button,
        ):
            button.setEnabled(False)
        self.control_buttons_layout.addWidget(self.start_button, 0, 0, 1, 2)
        self.control_buttons_layout.addWidget(self.test_button, 1, 0)
        self.control_buttons_layout.addWidget(self.retry_failed_button, 1, 1)
        self.control_buttons_layout.addWidget(self.pause_button, 2, 0)
        self.control_buttons_layout.addWidget(self.resume_button, 2, 1)
        self.control_buttons_layout.addWidget(self.stop_button, 3, 0)
        self.control_buttons_layout.addWidget(self.user_action_button, 3, 1)
        self.control_buttons_layout.setColumnStretch(0, 1)
        self.control_buttons_layout.setColumnStretch(1, 1)
        buttons = QWidget()
        buttons.setLayout(self.control_buttons_layout)
        status_card = QFrame()
        status_card.setObjectName("statusCard")
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(16, 14, 16, 14)
        status = QHBoxLayout()
        self.state_label = QLabel()
        self.state_label.setObjectName("stateBadge")
        self.state_label.setMinimumHeight(28)
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_label = QLabel()
        self.detail_label.setObjectName("helpText")
        self.detail_label.setWordWrap(True)
        self.count_label = QLabel("완료 0 / 0 · 성공 0 · 실패 0")
        self.count_label.setWordWrap(False)
        self.count_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        status.addWidget(self.state_label)
        status.addStretch(1)
        status.addWidget(self.count_label)
        status_layout.addLayout(status)
        status_layout.addWidget(self.detail_label)
        status_layout.addWidget(self.progress_bar)
        status_layout.setSpacing(8)
        column = QVBoxLayout()
        column.setSpacing(12)
        column.addWidget(buttons)
        column.addWidget(status_card)
        layout.addLayout(column)
        self.completion_card = QFrame()
        self.completion_card.setObjectName("completionCard")
        completion_layout = QVBoxLayout(self.completion_card)
        completion_layout.setContentsMargins(16, 14, 16, 14)
        completion_top = QHBoxLayout()
        title = QLabel("작업 완료")
        title.setObjectName("summaryTitle")
        self.completion_detail_label = QLabel()
        self.completion_detail_label.setObjectName("helpText")
        self.completion_detail_label.setWordWrap(True)
        self.completion_open_output_button = QPushButton("결과 폴더 열기")
        completion_top.addWidget(title)
        completion_top.addStretch(1)
        completion_top.addWidget(self.completion_open_output_button)
        completion_layout.addLayout(completion_top)
        completion_layout.addWidget(self.completion_detail_label)
        self.completion_card.setVisible(False)
        layout.addWidget(self.completion_card)
        return group

    def _build_recent_results_card(self) -> QFrame:
        card, layout = create_card(
            "최근 결과",
            "가장 최근에 완료된 작업 5건을 표시합니다.",
        )
        self.recent_results_table = QTableWidget(0, 3)
        self.recent_results_table.setObjectName("recentResults")
        self.recent_results_table.setHorizontalHeaderLabels(["키워드", "도메인", "상태"])
        self.recent_results_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.recent_results_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.recent_results_table.setShowGrid(False)
        self.recent_results_table.verticalHeader().setVisible(False)
        header = self.recent_results_table.horizontalHeader()
        for column in range(self.recent_results_table.columnCount()):
            header.setSectionResizeMode(column, header.ResizeMode.Stretch)
        self.recent_results_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.recent_results_table, 1)
        return card

    def reset_run(self, total: int) -> None:
        self._recent_result_rows.clear()
        self.recent_results_table.setRowCount(0)
        self.completion_card.setVisible(False)
        self.progress_bar.setValue(0)
        self.count_label.setText(f"완료 0 / {total} · 성공 0 · 실패 0")

    def set_progress(
        self,
        completed: int,
        total: int,
        succeeded: int,
        failed: int,
    ) -> None:
        self.progress_bar.setValue(int(completed / total * 100) if total else 0)
        self.count_label.setText(
            f"완료 {completed} / {total} · 성공 {succeeded} · 실패 {failed}"
        )

    def record_completed_job(self, update: JobUpdate) -> None:
        self._recent_result_rows = [
            row for row in self._recent_result_rows if row[0] != update.index
        ]
        self._recent_result_rows.append(
            (update.index, update.keyword, update.domain, update.status)
        )
        rows = self._recent_result_rows[-5:]
        self.recent_results_table.setRowCount(len(rows))
        for row_index, (_, keyword, domain, status) in enumerate(reversed(rows)):
            for column, value in enumerate((keyword, domain, self._job_text(status))):
                item = QTableWidgetItem(value)
                if column == 2:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.recent_results_table.setItem(row_index, column, item)

    @staticmethod
    def _job_text(status: JobStatus) -> str:
        match status:
            case JobStatus.PENDING:
                return "대기"
            case JobStatus.RUNNING:
                return "실행 중"
            case JobStatus.SUCCESS:
                return "성공"
            case JobStatus.FAILED:
                return "실패"
            case JobStatus.CANCELLED:
                return "취소"
