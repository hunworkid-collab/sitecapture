from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QStandardPaths, QThread, QTimer, QUrl, Slot
from PySide6.QtGui import QBrush, QCloseEvent, QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..keyword_io import normalize_keywords, read_keyword_text_file
from ..models import RunConfig
from ..paths import (
    application_data_directory,
    display_path,
    normalize_keyword,
    resolve_display_path,
)
from ..persistence import JobRepository
from ..query import build_query, normalize_domains
from .control import BatchControl
from .dialogs import show_validation_message
from .events import BatchState, JobStatus, JobUpdate, Stage2Summary
from .failure import short_failure_reason
from .theme import GUI_STYLE_SHEET
from .worker import BatchWorker


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
    "retry_wait": "재시도 대기",
    "success": "완료",
    "no_results_captured": "결과 없음",
    "failed": "실패",
    "cancelled": "중단",
    "skipped_existing": "기존 파일",
}


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self._thread: QThread | None = None
        self._worker: BatchWorker | None = None
        self._control: BatchControl | None = None
        self._closing = False
        self._fatal_error_text = ""
        self._current_run_id: str | None = None
        self._failed_job_count = 0
        self._recent_result_rows: list[tuple[int, str, str, JobStatus]] = []
        self._last_state = BatchState.IDLE.value
        self.setWindowTitle("Site Capture")
        self.setMinimumSize(960, 720)
        self.resize(1280, 900)
        self.setStyleSheet(GUI_STYLE_SHEET)
        root = QWidget(self)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._build_header(layout)
        self.workspace_tabs = QTabWidget()
        self._build_execution_tab()
        self._build_results_tab()
        self._build_log_tab()
        self._build_settings_tab()
        self._build_help_dialog()
        layout.addWidget(self.workspace_tabs, 1)
        self.footer_label = QLabel("Copyright © HUNHUN")
        self.footer_label.setObjectName("appFooter")
        self.footer_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.footer_label)
        self.setCentralWidget(root)
        self._input_widgets = [
            self.keyword_edit,
            self.load_txt_button,
            self.dedupe_button,
            self.clear_button,
            self.domain_edit,
            self.add_domain_button,
            self.domain_list,
            self.remove_domain_button,
            self.search_mode_combo,
            self.exact_phrase_check,
            self.delay_spin,
            self.timeout_spin,
            self.viewport_width_spin,
            self.viewport_height_spin,
            self.output_edit,
            self.browse_output_button,
            self.overwrite_check,
            self.metadata_check,
        ]
        self._connect_signals()
        self._update_keyword_count()
        self._apply_state(BatchState.IDLE.value, "키워드와 실행 조건을 입력하세요.")
        QTimer.singleShot(0, self._check_resume_job)

    @staticmethod
    def _create_card(title: str, help_text: str = "") -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(12)
        title_label = QLabel(title)
        title_label.setObjectName("cardTitle")
        layout.addWidget(title_label)
        if help_text:
            label = QLabel(help_text)
            label.setObjectName("helpText")
            label.setWordWrap(True)
            layout.addWidget(label)
        return card, layout

    def _build_header(self, root: QVBoxLayout) -> None:
        header = QWidget()
        header.setObjectName("pageHeader")
        layout = QVBoxLayout(header)
        layout.setContentsMargins(24, 18, 24, 14)
        layout.setSpacing(3)
        title_row = QHBoxLayout()
        title = QLabel("Site Capture")
        title.setObjectName("pageTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)
        self.help_button = QPushButton("도움말")
        title_row.addWidget(self.help_button)
        self.header_subtitle = QLabel(
            "도메인과 키워드로 Google 검색 결과를 증빙 이미지로 저장합니다."
        )
        self.header_subtitle.setObjectName("pageSubtitle")
        self.header_subtitle.setWordWrap(True)
        layout.addLayout(title_row)
        layout.addWidget(self.header_subtitle)
        root.addWidget(header)

    @staticmethod
    def _tab_layout(tab: QWidget) -> QVBoxLayout:
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(16)
        return layout

    def _build_execution_tab(self) -> None:
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        self.execution_scroll_area = QScrollArea()
        self.execution_scroll_area.setWidgetResizable(True)
        self.execution_scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = self._tab_layout(content)
        input_row = QHBoxLayout()
        input_row.setSpacing(16)
        self._build_keyword_ui(input_row)
        self._build_target_ui(input_row)
        layout.addLayout(input_row)
        self.execution_columns_layout = QHBoxLayout()
        self.execution_columns_layout.setSpacing(16)
        self.execution_columns_layout.addWidget(
            self._build_control_ui(),
            1,
            Qt.AlignmentFlag.AlignTop,
        )
        self.execution_columns_layout.addWidget(
            self._build_recent_results_ui(),
            1,
            Qt.AlignmentFlag.AlignTop,
        )
        layout.addLayout(self.execution_columns_layout)
        layout.addStretch(1)
        self.execution_scroll_area.setWidget(content)
        tab_layout.addWidget(self.execution_scroll_area)
        self.workspace_tabs.addTab(tab, "실행")

    def _build_keyword_ui(self, root: QHBoxLayout) -> None:
        card, layout = self._create_card(
            "1. 키워드",
            "한 줄에 하나씩 입력하세요. 같은 키워드는 실행 전에 정리할 수 있습니다.",
        )
        self.keyword_edit = QPlainTextEdit()
        self.keyword_edit.setPlaceholderText("예시\n키워드 1\n키워드 2")
        self.keyword_edit.setMinimumHeight(150)
        self.keyword_edit.setMaximumHeight(220)
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
        root.addWidget(card, 3)

    def _build_target_ui(self, root: QHBoxLayout) -> None:
        card, layout = self._create_card(
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
        self.domain_list.setMinimumHeight(96)
        self.domain_list.setMaximumHeight(132)
        layout.addWidget(self.domain_list)
        self.remove_domain_button = QPushButton("선택 도메인 삭제")
        self.remove_domain_button.setEnabled(False)
        layout.addWidget(self.remove_domain_button)
        root.addWidget(card, 2)

    def _build_settings_tab(self) -> None:
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        self.settings_scroll_area = QScrollArea()
        self.settings_scroll_area.setWidgetResizable(True)
        self.settings_scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        root = self._tab_layout(content)
        output_card, output_layout = self._create_card(
            "저장 위치",
            "캡처 이미지와 작업 기록을 저장할 기본 폴더입니다.",
        )
        output_form = QFormLayout()
        output_form.setSpacing(12)
        output_row = QWidget()
        output_layout_row = QHBoxLayout(output_row)
        output_layout_row.setContentsMargins(0, 0, 0, 0)
        self.output_edit = QLineEdit(self._default_download_path())
        self.browse_output_button = QPushButton("찾기")
        self.open_output_button = QPushButton("결과 폴더 열기")
        output_layout_row.addWidget(self.output_edit)
        output_layout_row.addWidget(self.browse_output_button)
        output_layout_row.addWidget(self.open_output_button)
        output_form.addRow("저장 폴더", output_row)
        output_layout.addLayout(output_form)
        root.addWidget(output_card)

        self.advanced_settings = QGroupBox("고급 설정")
        self.advanced_settings.setObjectName("advancedSettings")
        self.advanced_settings.setCheckable(True)
        self.advanced_settings.setChecked(True)
        advanced_layout = QVBoxLayout(self.advanced_settings)
        self.advanced_content = QWidget()
        advanced_form = QFormLayout(self.advanced_content)
        advanced_form.setSpacing(12)
        self.search_mode_combo = QComboBox()
        self.search_mode_combo.setMaximumWidth(420)
        self.search_mode_combo.addItem("검색창에 입력", "search-box")
        self.search_mode_combo.addItem("바로 검색하기", "direct-url")
        advanced_form.addRow("검색 방식", self.search_mode_combo)
        self.exact_phrase_check = QCheckBox("키워드 정확히 일치")
        advanced_form.addRow("", self.exact_phrase_check)
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 3600)
        self.delay_spin.setValue(5)
        self.delay_spin.setSuffix(" 초")
        self.delay_spin.setMaximumWidth(240)
        advanced_form.addRow("작업 간 대기", self.delay_spin)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(5, 300)
        self.timeout_spin.setValue(30)
        self.timeout_spin.setSuffix(" 초")
        self.timeout_spin.setMaximumWidth(240)
        advanced_form.addRow("최대 대기 시간", self.timeout_spin)
        self.overwrite_check = QCheckBox("동일 파일 덮어쓰기")
        self.metadata_check = QCheckBox("작업 정보 파일 저장")
        self.metadata_check.setChecked(True)
        advanced_form.addRow("", self.overwrite_check)
        advanced_form.addRow("", self.metadata_check)
        viewport = QWidget()
        viewport_row = QHBoxLayout(viewport)
        viewport_row.setContentsMargins(0, 0, 0, 0)
        self.viewport_width_spin = QSpinBox()
        self.viewport_width_spin.setRange(640, 4096)
        self.viewport_width_spin.setValue(1440)
        self.viewport_width_spin.setMaximumWidth(220)
        self.viewport_height_spin = QSpinBox()
        self.viewport_height_spin.setRange(480, 4096)
        self.viewport_height_spin.setValue(1000)
        self.viewport_height_spin.setMaximumWidth(220)
        viewport_row.addWidget(QLabel("가로"))
        viewport_row.addWidget(self.viewport_width_spin)
        viewport_row.addWidget(QLabel("세로"))
        viewport_row.addWidget(self.viewport_height_spin)
        viewport_row.addStretch(1)
        advanced_form.addRow("캡처 크기", viewport)
        advanced_layout.addWidget(self.advanced_content)
        self.advanced_content.setVisible(True)
        self.advanced_settings.toggled.connect(self.advanced_content.setVisible)
        root.addWidget(self.advanced_settings)
        root.addStretch(1)
        self.settings_scroll_area.setWidget(content)
        tab_layout.addWidget(self.settings_scroll_area)
        self.workspace_tabs.addTab(tab, "설정")

    def _build_control_ui(self) -> QFrame:
        group, layout = self._create_card(
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
        control_buttons = QWidget()
        control_buttons.setLayout(self.control_buttons_layout)
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
        control_status_layout = QVBoxLayout()
        control_status_layout.setSpacing(12)
        control_status_layout.addWidget(control_buttons)
        control_status_layout.addWidget(status_card)
        layout.addLayout(control_status_layout)
        self.completion_card = QFrame()
        self.completion_card.setObjectName("completionCard")
        completion_layout = QVBoxLayout(self.completion_card)
        completion_layout.setContentsMargins(16, 14, 16, 14)
        completion_top = QHBoxLayout()
        completion_title = QLabel("작업 완료")
        completion_title.setObjectName("summaryTitle")
        self.completion_detail_label = QLabel()
        self.completion_detail_label.setObjectName("helpText")
        self.completion_detail_label.setWordWrap(True)
        self.completion_open_output_button = QPushButton("결과 폴더 열기")
        completion_top.addWidget(completion_title)
        completion_top.addStretch(1)
        completion_top.addWidget(self.completion_open_output_button)
        completion_layout.addLayout(completion_top)
        completion_layout.addWidget(self.completion_detail_label)
        self.completion_card.setVisible(False)
        layout.addWidget(self.completion_card)
        return group

    def _build_recent_results_ui(self) -> QFrame:
        card, layout = self._create_card(
            "최근 결과",
            "가장 최근에 완료된 작업 5건을 표시합니다.",
        )
        self.recent_results_table = QTableWidget(0, 3)
        self.recent_results_table.setObjectName("recentResults")
        self.recent_results_table.setHorizontalHeaderLabels(
            ["키워드", "도메인", "상태"]
        )
        self.recent_results_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.recent_results_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.recent_results_table.setShowGrid(False)
        self.recent_results_table.verticalHeader().setVisible(False)
        recent_header = self.recent_results_table.horizontalHeader()
        for column in range(self.recent_results_table.columnCount()):
            recent_header.setSectionResizeMode(
                column,
                QHeaderView.ResizeMode.Stretch,
            )
        self._resize_recent_results_table()
        layout.addWidget(self.recent_results_table)
        return card

    def _build_results_tab(self) -> None:
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        self.results_scroll_area = QScrollArea()
        self.results_scroll_area.setWidgetResizable(True)
        self.results_scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        root = self._tab_layout(content)
        table_box, table_layout = self._create_card(
            "작업 결과",
            "작업별 저장 상태와 결과 파일을 확인합니다.",
        )
        self.job_table = QTableWidget(0, 7)
        self.job_table.setHorizontalHeaderLabels(["번호", "키워드", "도메인", "검색식", "상태", "저장 파일", "메시지"])
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
        self._resize_job_table()
        table_layout.addWidget(self.job_table)
        root.addWidget(table_box)
        root.addStretch(1)
        self.results_scroll_area.setWidget(content)
        tab_layout.addWidget(self.results_scroll_area)
        self.workspace_tabs.addTab(tab, "결과")

    def _build_log_tab(self) -> None:
        tab = QWidget()
        root = self._tab_layout(tab)
        log_box = QFrame()
        log_box.setObjectName("card")
        log_layout = QVBoxLayout(log_box)
        log_layout.setContentsMargins(20, 18, 20, 20)
        log_layout.setSpacing(12)
        title_label = QLabel("실행 로그")
        title_label.setObjectName("cardTitle")
        log_layout.addWidget(title_label)
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
        self.workspace_tabs.addTab(tab, "로그")

    def _build_help_dialog(self) -> None:
        self.help_dialog = QDialog(self)
        self.help_dialog.setWindowTitle("도움말")
        self.help_dialog.setModal(True)
        self.help_dialog.setMinimumWidth(760)
        layout = QVBoxLayout(self.help_dialog)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        steps_card, steps_layout = self._create_card(
            "사용 방법",
            "아래 순서로 입력한 뒤 캡처를 시작하세요.",
        )
        self.help_steps_label = QLabel(
            "1. 실행 탭에서 키워드를 한 줄에 하나씩 입력하고 도메인을 추가합니다.\n"
            "2. 설정 탭에서 저장 폴더와 고급 설정을 확인합니다.\n"
            "3. 캡처 시작 또는 현재 키워드 1건 테스트를 선택합니다.\n"
            "4. 결과 탭과 로그 탭에서 진행 상황과 저장 결과를 확인합니다."
        )
        self.help_steps_label.setObjectName("helpText")
        self.help_steps_label.setWordWrap(True)
        steps_layout.addWidget(self.help_steps_label)
        layout.addWidget(steps_card)

        actions_card, actions_layout = self._create_card(
            "실행 제어 버튼",
            "실행 중에는 필요한 제어 버튼만 사용할 수 있습니다.",
        )
        self.help_actions_label = QLabel(
            "<b>캡처 시작</b>: 입력한 모든 키워드 × 도메인 조합을 실행합니다.<br><br>"
            "<b>현재 키워드 1건 테스트</b>: 커서가 있는 키워드와 첫 번째 도메인만 1회 실행합니다.<br><br>"
            "<b>실패 작업 다시 실행</b>: 이전 실행에서 실패한 항목만 다시 실행합니다.<br><br>"
            "<b>일시정지</b>: 현재 작업이 끝난 뒤 다음 작업 시작을 멈춥니다.<br><br>"
            "<b>재개</b>: 일시정지된 작업을 다시 진행합니다.<br><br>"
            "<b>중단</b>: 현재 검색 작업이 끝나는 대로 전체 작업을 종료합니다.<br><br>"
            "<b>Chrome 확인 완료</b>: Chrome에서 직접 확인 또는 동의 처리가 필요할 때, 처리 후 이 버튼을 누릅니다."
        )
        self.help_actions_label.setObjectName("helpText")
        self.help_actions_label.setTextFormat(Qt.TextFormat.RichText)
        self.help_actions_label.setWordWrap(True)
        actions_layout.addWidget(self.help_actions_label)
        layout.addWidget(actions_card)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("닫기")
        buttons.rejected.connect(self.help_dialog.reject)
        layout.addWidget(buttons)

    @Slot()
    def _show_help_dialog(self) -> None:
        self.help_dialog.exec()

    def _connect_signals(self) -> None:
        self.help_button.clicked.connect(self._show_help_dialog)
        self.keyword_edit.textChanged.connect(self._update_keyword_count)
        self.domain_edit.returnPressed.connect(self._add_domain)
        self.add_domain_button.clicked.connect(self._add_domain)
        self.remove_domain_button.clicked.connect(self._remove_selected_domain)
        self.domain_list.currentRowChanged.connect(
            self._update_domain_remove_button
        )
        self.clear_log_button.clicked.connect(self._clear_log)
        self.load_txt_button.clicked.connect(self._load_keyword_file)
        self.dedupe_button.clicked.connect(self._normalize_keyword_text)
        self.clear_button.clicked.connect(self.keyword_edit.clear)
        self.browse_output_button.clicked.connect(self._browse_output_directory)
        self.open_output_button.clicked.connect(self._open_output_directory)
        self.completion_open_output_button.clicked.connect(
            self._open_output_directory
        )
        self.test_button.clicked.connect(lambda: self._start(test_mode=True))
        self.start_button.clicked.connect(lambda: self._start(test_mode=False))
        self.retry_failed_button.clicked.connect(self._retry_failed_jobs)
        self.pause_button.clicked.connect(self._request_pause)
        self.resume_button.clicked.connect(self._request_resume)
        self.stop_button.clicked.connect(self._request_stop)
        self.user_action_button.clicked.connect(self._confirm_user_action)

    @staticmethod
    def _default_download_path() -> str:
        value = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        return display_path(Path(value)) if value else display_path(Path.home() / "Downloads")

    @staticmethod
    def _profile_directory() -> Path:
        value = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
        return Path(value) / "chrome-profile" if value else application_data_directory() / "chrome-profile"

    def _current_keywords(self) -> tuple[str, ...]:
        return normalize_keywords(self.keyword_edit.toPlainText().splitlines())

    def _current_domains(self) -> tuple[str, ...]:
        return normalize_domains(
            tuple(
                self.domain_list.item(index).text()
                for index in range(self.domain_list.count())
            )
        )

    def _set_domains(self, domains: tuple[str, ...]) -> None:
        self.domain_list.clear()
        for domain in domains:
            self.domain_list.addItem(QListWidgetItem(domain))
        self._update_capture_estimate()

    @Slot()
    def _add_domain(self) -> None:
        try:
            domains = normalize_domains(
                (*self._current_domains(), self.domain_edit.text())
            )
        except ValueError as exc:
            QMessageBox.warning(self, "도메인 확인", str(exc))
            return
        self.domain_edit.clear()
        self._set_domains(domains)

    @Slot()
    def _remove_selected_domain(self) -> None:
        row = self.domain_list.currentRow()
        if row < 0:
            return
        self.domain_list.takeItem(row)
        self._update_capture_estimate()

    @Slot(int)
    def _update_domain_remove_button(self, row: int) -> None:
        self.remove_domain_button.setEnabled(row >= 0)

    @Slot()
    def _update_keyword_count(self) -> None:
        raw = [line for line in self.keyword_edit.toPlainText().splitlines() if line.strip()]
        valid = self._current_keywords()
        self.keyword_count_label.setText(f"입력 {len(raw)}개 · 유효 {len(valid)}개 · 제거 대상 {max(0, len(raw) - len(valid))}개")
        self._update_capture_estimate()

    @Slot()
    def _update_capture_estimate(self) -> None:
        keyword_count = len(self._current_keywords())
        domain_count = len(self._current_domains())
        self.capture_estimate_label.setText(
            f"키워드 {keyword_count}개 × 도메인 {domain_count}개 = "
            f"총 {keyword_count * domain_count}건 캡처 예정"
        )

    @Slot()
    def _load_keyword_file(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "키워드 TXT 선택", str(Path.home()), "Text files (*.txt);;All files (*.*)")
        if not filename:
            return
        try:
            keywords = normalize_keywords(read_keyword_text_file(Path(filename)))
            if not keywords:
                raise ValueError("유효한 키워드가 없습니다.")
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "키워드 불러오기 실패", str(exc))
            return
        self.keyword_edit.setPlainText("\n".join(keywords))
        self._append_log(f"키워드 파일 불러오기: {display_path(Path(filename))} ({len(keywords)}개)")

    @Slot()
    def _normalize_keyword_text(self) -> None:
        keywords = self._current_keywords()
        self.keyword_edit.setPlainText("\n".join(keywords))
        self._append_log(f"키워드 정리 완료: {len(keywords)}개")

    @Slot()
    def _browse_output_directory(self) -> None:
        current = self.output_edit.text().strip() or self._default_download_path()
        selected = QFileDialog.getExistingDirectory(self, "출력 루트 선택", str(resolve_display_path(current)))
        if selected:
            self.output_edit.setText(display_path(Path(selected)))

    @Slot()
    def _open_output_directory(self) -> None:
        path = resolve_display_path(self.output_edit.text())
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(self, "폴더 열기 실패", str(exc))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

    def _build_config(self) -> RunConfig:
        keywords = self._current_keywords()
        if not keywords:
            raise ValueError("키워드를 하나 이상 입력하세요.")
        domains = self._current_domains()
        if not domains:
            raise ValueError("검색할 도메인을 하나 이상 선택하세요.")
        output_root = resolve_display_path(self.output_edit.text()).resolve()
        if output_root.exists() and not output_root.is_dir():
            raise ValueError(f"출력 경로가 폴더가 아닙니다: {output_root}")
        output_root.mkdir(parents=True, exist_ok=True)
        profile_dir = self._profile_directory().expanduser().resolve()
        profile_dir.mkdir(parents=True, exist_ok=True)
        return RunConfig(keywords, domains, output_root, profile_dir, search_mode=str(self.search_mode_combo.currentData()), exact_phrase=self.exact_phrase_check.isChecked(), viewport_width=self.viewport_width_spin.value(), viewport_height=self.viewport_height_spin.value(), timeout_seconds=self.timeout_spin.value(), delay_between_jobs_seconds=self.delay_spin.value(), overwrite=self.overwrite_check.isChecked(), keep_chrome_open=False, write_metadata=self.metadata_check.isChecked(), headless=False, verbose=True)

    def _start(
        self,
        *,
        test_mode: bool,
        resume_run_id: str | None = None,
        resume_config: RunConfig | None = None,
    ) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        try:
            if resume_config is not None:
                config = resume_config
                config.output_root.mkdir(parents=True, exist_ok=True)
                config.profile_dir.mkdir(parents=True, exist_ok=True)
            else:
                config = self._build_config()
            if test_mode and resume_run_id is None:
                line = normalize_keyword(self.keyword_edit.textCursor().block().text())
                config = replace(
                    config,
                    keywords=(line if line in config.keywords else config.keywords[0],),
                    domains=(config.domains[0],),
                    delay_between_jobs_seconds=0.0,
                )
        except (ValueError, OSError) as exc:
            show_validation_message(self, str(exc))
            return
        self._prepare_job_table(config)
        self._fatal_error_text = ""
        self._failed_job_count = 0
        self.completion_card.setVisible(False)
        self.retry_failed_button.setEnabled(False)
        if resume_run_id is not None:
            self._current_run_id = resume_run_id
        self._control = BatchControl()
        self._thread = QThread(self)
        self._worker = BatchWorker(config, self._control, resume_run_id=resume_run_id)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.log_emitted.connect(self._append_log)
        self._worker.run_started.connect(self._on_run_started)
        self._worker.state_changed.connect(self._on_state_changed)
        self._worker.job_changed.connect(self._on_job_changed)
        self._worker.progress_changed.connect(self._on_progress_changed)
        self._worker.user_action_required.connect(self._on_user_action_required)
        self._worker.fatal_error.connect(self._on_fatal_error)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.finished.connect(self._thread.deleteLater)
        self._set_inputs_enabled(False)
        self.test_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self._apply_state(BatchState.PREPARING.value, "테스트 작업을 준비합니다." if test_mode else "전체 작업을 준비합니다.")
        if resume_run_id is not None:
            self._append_log(f"이전 작업 재개 요청: {resume_run_id}")
        else:
            self._append_log(
                f"새 작업 시작 요청: 키워드 {len(config.keywords)}개 × "
                f"도메인 {len(config.domains)}개"
            )
        self._thread.start()

    def _database(self) -> JobRepository:
        return JobRepository(application_data_directory() / "data" / "jobs.db")

    @Slot()
    def _check_resume_job(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return

        try:
            repository = self._database()
            repository.recover_interrupted_runs()
            run_id = repository.latest_resumable_run_id()
            if run_id is None:
                return
            config = repository.load_config(run_id)
            total, completed, succeeded, failed = repository.run_counts(run_id)
            remaining = max(0, total - completed)
            if remaining == 0:
                return
        except (KeyError, OSError, ValueError, sqlite3.Error) as exc:
            self._append_log(f"이전 작업 확인 실패: {exc}")
            return

        answer = QMessageBox.question(
            self,
            "이전 작업 재개",
            "완료되지 않은 이전 작업이 있습니다.\n\n"
            f"전체: {total}건\n"
            f"완료: {completed}건\n"
            f"남은 작업: {remaining}건\n\n"
            "이전 작업을 재개하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            repository.decline_resume(run_id)
            self._append_log("이전 작업의 남은 항목을 취소했습니다.")
            return

        self._apply_saved_config(config)
        self._start(test_mode=False, resume_run_id=run_id, resume_config=config)
        self._restore_saved_job_rows(repository, run_id)
        self._on_progress_changed(completed, total, succeeded, failed)

    def _apply_saved_config(self, config: RunConfig) -> None:
        self.keyword_edit.setPlainText("\n".join(config.keywords))
        self._set_domains(config.domains)
        self.exact_phrase_check.setChecked(config.exact_phrase)
        search_mode_index = self.search_mode_combo.findData(config.search_mode)
        if search_mode_index >= 0:
            self.search_mode_combo.setCurrentIndex(search_mode_index)
        self.delay_spin.setValue(round(config.delay_between_jobs_seconds))
        self.timeout_spin.setValue(round(config.timeout_seconds))
        self.viewport_width_spin.setValue(config.viewport_width)
        self.viewport_height_spin.setValue(config.viewport_height)
        self.output_edit.setText(display_path(config.output_root))
        self.overwrite_check.setChecked(config.overwrite)
        self.metadata_check.setChecked(config.write_metadata)

    def _restore_saved_job_rows(self, repository: JobRepository, run_id: str) -> None:
        try:
            rows = repository.job_display_rows(run_id)
        except (KeyError, OSError, ValueError, sqlite3.Error) as exc:
            self._append_log(f"작업표 복원 실패: {exc}")
            return

        for sequence, status, screenshot_path, error_message in rows:
            row = sequence - 1
            if row < 0 or row >= self.job_table.rowCount():
                continue
            self._set_table_text(row, 4, _DB_STATUS_TEXT.get(status, status))
            self._set_saved_file_cell(
                row,
                Path(screenshot_path) if screenshot_path else None,
            )
            self._set_table_text(
                row,
                6,
                short_failure_reason(error_message) if status == "failed" else error_message,
            )
            self._set_failure_row_style(row, status == "failed")

    def _prepare_job_table(self, config: RunConfig) -> None:
        total = len(config.keywords) * len(config.domains)
        self.job_table.clearContents()
        self.job_table.setRowCount(total)
        self._resize_job_table()
        self._recent_result_rows.clear()
        self.recent_results_table.setRowCount(0)
        self._resize_recent_results_table()
        self.completion_card.setVisible(False)
        self.progress_bar.setValue(0)
        self.count_label.setText(f"완료 0 / {total} · 성공 0 · 실패 0")
        row = 0
        for keyword in config.keywords:
            for domain in config.domains:
                query = build_query(domain, keyword, exact_phrase=config.exact_phrase)
                for column, value in enumerate((str(row + 1), keyword, domain, query, _JOB_TEXT[JobStatus.PENDING], "", "")):
                    self.job_table.setItem(row, column, QTableWidgetItem(value))
                row += 1

    @Slot(str, str)
    def _on_state_changed(self, state: str, message: str) -> None:
        self._apply_state(state, message)

    @Slot(str)
    def _on_run_started(self, run_id: str) -> None:
        self._current_run_id = run_id

    @Slot(object)
    def _on_job_changed(self, update: object) -> None:
        if not isinstance(update, JobUpdate):
            return
        row = update.index - 1
        if row < 0 or row >= self.job_table.rowCount():
            return
        self._set_table_text(row, 4, _JOB_TEXT[update.status])
        self._set_saved_file_cell(row, update.path)
        message = short_failure_reason(update.message) if update.status is JobStatus.FAILED else update.message
        self._set_table_text(row, 6, message)
        self.job_table.item(row, 6).setToolTip(update.message)
        self._set_failure_row_style(row, update.status is JobStatus.FAILED)
        self.job_table.scrollToItem(self.job_table.item(row, 0))
        match update.status:
            case JobStatus.PENDING | JobStatus.RUNNING:
                return
            case JobStatus.SUCCESS | JobStatus.FAILED | JobStatus.CANCELLED:
                self._record_recent_result(update)

    def _record_recent_result(self, update: JobUpdate) -> None:
        self._recent_result_rows = [
            row for row in self._recent_result_rows if row[0] != update.index
        ]
        self._recent_result_rows.append(
            (update.index, update.keyword, update.domain, update.status)
        )
        self._refresh_recent_results()

    def _refresh_recent_results(self) -> None:
        rows = self._recent_result_rows[-5:]
        self.recent_results_table.setRowCount(len(rows))
        for row_index, (_, keyword, domain, status) in enumerate(reversed(rows)):
            for column, value in enumerate(
                (keyword, domain, _JOB_TEXT[status])
            ):
                item = QTableWidgetItem(value)
                if column == 2:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.recent_results_table.setItem(row_index, column, item)
        self._resize_recent_results_table()

    @staticmethod
    def _resize_table_to_rows(table: QTableWidget, maximum_rows: int) -> None:
        visible_rows = min(max(1, table.rowCount()), maximum_rows)
        default_row_height = table.verticalHeader().defaultSectionSize()
        content_height = sum(
            table.rowHeight(row) if row < table.rowCount() else default_row_height
            for row in range(visible_rows)
        )
        table.setFixedHeight(
            table.horizontalHeader().height()
            + content_height
            + table.frameWidth() * 2
        )

    def _resize_recent_results_table(self) -> None:
        self._resize_table_to_rows(self.recent_results_table, maximum_rows=5)

    def _resize_job_table(self) -> None:
        self._resize_table_to_rows(self.job_table, maximum_rows=8)

    @Slot(int, int, int, int)
    def _on_progress_changed(self, completed: int, total: int, succeeded: int, failed: int) -> None:
        self.progress_bar.setValue(int(completed / total * 100) if total else 0)
        self.count_label.setText(
            f"완료 {completed} / {total} · 성공 {succeeded} · 실패 {failed}"
        )

    @Slot(str, str)
    def _on_user_action_required(self, _state: str, label: str) -> None:
        self.user_action_button.setEnabled(True)
        QMessageBox.information(self, "Chrome에서 직접 처리 필요", f"{label}이 표시되었습니다.\n\nChrome에서 확인한 뒤 'Chrome 확인 완료'를 누르세요.")

    @Slot(str)
    def _on_fatal_error(self, details: str) -> None:
        self._fatal_error_text = details
        self.completion_card.setVisible(False)
        self._append_log(details)
        self._apply_state(BatchState.FAILED.value, details.splitlines()[0] if details else "알 수 없는 오류")
        QMessageBox.critical(self, "작업 실행 실패", details.splitlines()[0] if details else "알 수 없는 오류")

    @Slot(object)
    def _on_worker_finished(self, summary: object) -> None:
        if isinstance(summary, Stage2Summary):
            self._failed_job_count = summary.failed
            self._on_progress_changed(summary.completed, summary.total, summary.succeeded, summary.failed)
            if summary.stopped:
                self.completion_card.setVisible(False)
                self._apply_state(BatchState.STOPPED.value, f"완료 {summary.completed}/{summary.total}, 미실행·취소 {summary.cancelled}")
            elif self._last_state != BatchState.FAILED.value:
                self._apply_state(BatchState.COMPLETED.value, f"성공 {summary.succeeded}, 실패 {summary.failed}")
                self.completion_detail_label.setText(
                    f"총 {summary.total}건 중 성공 {summary.succeeded}건, 실패 {summary.failed}건"
                )
                self.completion_card.setVisible(True)
        elif not self._fatal_error_text:
            self.completion_card.setVisible(False)
            self._apply_state(BatchState.FAILED.value, "작업 결과를 받지 못했습니다.")

    @Slot()
    def _on_thread_finished(self) -> None:
        self._thread = None
        self._worker = None
        self._control = None
        self._set_inputs_enabled(True)
        for button in (self.test_button, self.start_button):
            button.setEnabled(True)
        for button in (self.pause_button, self.resume_button, self.stop_button, self.user_action_button):
            button.setEnabled(False)
        self.retry_failed_button.setEnabled(
            self._current_run_id is not None and self._failed_job_count > 0
        )
        if self._closing:
            QTimer.singleShot(0, self.close)

    @Slot()
    def _request_pause(self) -> None:
        if self._control is not None:
            self._control.request_pause()
            self._append_log("일시정지 요청")

    @Slot()
    def _request_resume(self) -> None:
        if self._control is not None:
            self._control.request_resume()
            self._append_log("재개 요청")

    @Slot()
    def _request_stop(self) -> None:
        if self._control is not None:
            self._control.request_stop()
            self._apply_state(BatchState.STOPPING.value, "현재 검색 작업이 끝나는 즉시 작업을 중단합니다.")
            self._append_log("중단 요청")

    @Slot()
    def _retry_failed_jobs(self) -> None:
        if self._current_run_id is None:
            return
        try:
            repository = self._database()
            retried = repository.retry_failed_jobs(self._current_run_id)
            config = repository.load_config(self._current_run_id)
        except (KeyError, OSError, ValueError, sqlite3.Error) as exc:
            QMessageBox.critical(self, "실패 작업 재실행 실패", str(exc))
            return
        if retried == 0:
            self.retry_failed_button.setEnabled(False)
            return
        self._append_log(f"실패 작업 {retried}건 재실행 요청")
        self._start(
            test_mode=False,
            resume_run_id=self._current_run_id,
            resume_config=config,
        )
        self._restore_saved_job_rows(repository, self._current_run_id)

    @Slot()
    def _confirm_user_action(self) -> None:
        if self._control is not None:
            self.user_action_button.setEnabled(False)
            self._control.confirm_user_action()
            self._append_log("사용자 처리 완료 신호 전송")

    @Slot(str)
    def _append_log(self, message: str) -> None:
        self.log_edit.appendPlainText(f"[{datetime.now().strftime('%H:%M:%S')}] {message.rstrip()}")

    @Slot()
    def _clear_log(self) -> None:
        self.log_edit.clear()

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

    def _apply_state(self, state: str, message: str) -> None:
        self._last_state = state
        self.state_label.setText(_STATE_TEXT.get(state, state))
        self.detail_label.setText(message)
        active = self._thread is not None and self._thread.isRunning()
        self.pause_button.setEnabled(active and state == BatchState.RUNNING.value)
        self.resume_button.setEnabled(active and state == BatchState.PAUSED.value)
        self.stop_button.setEnabled(active and state not in {BatchState.STOPPING.value, BatchState.STOPPED.value, BatchState.COMPLETED.value, BatchState.FAILED.value})
        self.user_action_button.setEnabled(active and state == BatchState.USER_ACTION_REQUIRED.value)

    def _set_inputs_enabled(self, enabled: bool) -> None:
        for widget in self._input_widgets:
            widget.setEnabled(enabled)
        self.open_output_button.setEnabled(True)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._closing = True
            if self._control is not None:
                self._control.request_stop()
            self._apply_state(BatchState.STOPPING.value, "Chrome과 Worker를 정리한 뒤 창을 닫습니다.")
            event.ignore()
            return
        event.accept()
