from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from .widgets import create_card, tab_layout


class ExecutionView(QWidget):
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
