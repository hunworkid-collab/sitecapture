from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QStandardPaths, QThread, QTimer, QUrl, Slot
from PySide6.QtGui import QBrush, QCloseEvent, QColor, QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
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
from ..query import build_search_jobs, normalize_domains
from ..execution import BatchControl, BatchState, JobStatus, JobUpdate, Stage2Summary
from .dialogs import show_validation_message
from .execution_view import ExecutionView
from .failure import short_failure_reason
from .log_view import LogView
from .results_view import ResultsView
from .settings_view import SettingsView
from .theme import GUI_STYLE_SHEET
from .widgets import create_card
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
        self.setMinimumSize(1180, 820)
        self.resize(1500, 1000)
        self.setStyleSheet(GUI_STYLE_SHEET)
        root = QWidget(self)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._build_header(layout)
        self.workspace_tabs = QTabWidget()
        self.execution_view = ExecutionView()
        self.results_view = ResultsView()
        self.log_view = LogView()
        self.settings_view = SettingsView(self._default_download_path())
        self.workspace_tabs.addTab(self.execution_view, "실행")
        self.workspace_tabs.addTab(self.results_view, "결과")
        self.workspace_tabs.addTab(self.log_view, "로그")
        self.workspace_tabs.addTab(self.settings_view, "설정")
        self._bind_view_widgets()
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

    def _bind_view_widgets(self) -> None:
        for name in (
            "execution_scroll_area",
            "keyword_edit",
            "load_txt_button",
            "dedupe_button",
            "clear_button",
            "keyword_count_label",
            "capture_estimate_label",
            "domain_edit",
            "add_domain_button",
            "domain_list",
            "remove_domain_button",
            "control_buttons_layout",
            "test_button",
            "start_button",
            "retry_failed_button",
            "pause_button",
            "resume_button",
            "stop_button",
            "user_action_button",
            "state_label",
            "detail_label",
            "count_label",
            "progress_bar",
            "completion_card",
            "completion_detail_label",
            "completion_open_output_button",
            "recent_results_table",
            "execution_columns_layout",
        ):
            setattr(self, name, getattr(self.execution_view, name))
        for name in (
            "settings_scroll_area",
            "output_edit",
            "browse_output_button",
            "open_output_button",
            "advanced_settings",
            "advanced_content",
            "search_mode_combo",
            "exact_phrase_check",
            "delay_spin",
            "timeout_spin",
            "overwrite_check",
            "metadata_check",
            "viewport_width_spin",
            "viewport_height_spin",
        ):
            setattr(self, name, getattr(self.settings_view, name))
        for name in ("results_scroll_area", "job_table"):
            setattr(self, name, getattr(self.results_view, name))
        for name in ("log_header_layout", "log_help_label", "clear_log_button", "log_edit"):
            setattr(self, name, getattr(self.log_view, name))

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


    def _build_help_dialog(self) -> None:
        self.help_dialog = QDialog(self)
        self.help_dialog.setWindowTitle("도움말")
        self.help_dialog.setModal(True)
        self.help_dialog.setMinimumWidth(760)
        layout = QVBoxLayout(self.help_dialog)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        steps_card, steps_layout = create_card(
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

        actions_card, actions_layout = create_card(
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
        return RunConfig(keywords, domains, output_root, profile_dir, search_mode=str(self.search_mode_combo.currentData()), exact_phrase=self.exact_phrase_check.isChecked(), viewport_width=self.viewport_width_spin.value(), viewport_height=self.viewport_height_spin.value(), timeout_seconds=self.timeout_spin.value(), delay_between_jobs_seconds=self.delay_spin.value(), overwrite=self.overwrite_check.isChecked(), keep_chrome_open=False, write_metadata=self.metadata_check.isChecked(), headless=False)

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
        jobs = build_search_jobs(config)
        total = len(jobs)
        self.job_table.clearContents()
        self.job_table.setRowCount(total)
        self._resize_job_table()
        self._recent_result_rows.clear()
        self.recent_results_table.setRowCount(0)
        self.completion_card.setVisible(False)
        self.progress_bar.setValue(0)
        self.count_label.setText(f"완료 0 / {total} · 성공 0 · 실패 0")
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
