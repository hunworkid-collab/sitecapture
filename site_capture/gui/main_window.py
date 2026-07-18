from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QStandardPaths, QThread, QTimer, QUrl, Qt, Slot
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
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
from .events import BatchState, JobStatus, JobUpdate, Stage2Summary
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
        self._last_state = BatchState.IDLE.value
        self.setWindowTitle("Site Capture")
        self.resize(1280, 900)
        root = QWidget(self)
        layout = QVBoxLayout(root)
        self._build_keyword_ui(layout)
        self._build_settings_ui(layout)
        self._build_control_ui(layout)
        self._build_result_ui(layout)
        self.setCentralWidget(root)
        self._connect_signals()
        self._update_keyword_count()
        self._apply_state(BatchState.IDLE.value, "키워드와 실행 조건을 입력하세요.")
        QTimer.singleShot(0, self._check_resume_job)

    def _build_keyword_ui(self, root: QVBoxLayout) -> None:
        group = QGroupBox("1. 키워드")
        layout = QVBoxLayout(group)
        self.keyword_edit = QPlainTextEdit()
        self.keyword_edit.setPlaceholderText("키워드를 한 줄에 하나씩 입력하세요.")
        self.keyword_edit.setPlainText("")
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
        root.addWidget(group)

    def _build_settings_ui(self, root: QVBoxLayout) -> None:
        group = QGroupBox("2. 실행 설정")
        layout = QHBoxLayout(group)
        search = QGroupBox("검색")
        search_form = QFormLayout(search)
        self.domain_edit = QPlainTextEdit()
        self.domain_edit.setPlaceholderText("실제도메인\nsub.실제도메인")
        self.domain_edit.setMaximumHeight(70)
        search_form.addRow("도메인 (한 줄에 하나)", self.domain_edit)
        self.exclude_public_check = QCheckBox("루트 도메인 검색에서 public 제외")
        search_form.addRow("", self.exclude_public_check)
        self.search_mode_combo = QComboBox()
        self.search_mode_combo.addItem("Google 검색창", "search-box")
        self.search_mode_combo.addItem("검색 URL 직접 이동", "direct-url")
        search_form.addRow("검색 방식", self.search_mode_combo)
        self.exact_phrase_check = QCheckBox("키워드 정확히 일치")
        search_form.addRow("", self.exact_phrase_check)
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 3600)
        self.delay_spin.setValue(5)
        self.delay_spin.setSuffix(" 초")
        search_form.addRow("작업 간 대기", self.delay_spin)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(5, 300)
        self.timeout_spin.setValue(30)
        self.timeout_spin.setSuffix(" 초")
        search_form.addRow("타임아웃", self.timeout_spin)
        layout.addWidget(search)

        output = QGroupBox("출력")
        output_form = QFormLayout(output)
        output_row = QWidget()
        output_layout = QHBoxLayout(output_row)
        output_layout.setContentsMargins(0, 0, 0, 0)
        self.output_edit = QLineEdit(self._default_download_path())
        self.browse_output_button = QPushButton("찾기")
        self.open_output_button = QPushButton("열기")
        output_layout.addWidget(self.output_edit)
        output_layout.addWidget(self.browse_output_button)
        output_layout.addWidget(self.open_output_button)
        output_form.addRow("폴더", output_row)
        self.overwrite_check = QCheckBox("동일 파일 덮어쓰기")
        self.metadata_check = QCheckBox("JSON 메타데이터 저장")
        self.metadata_check.setChecked(True)
        output_form.addRow("", self.overwrite_check)
        output_form.addRow("", self.metadata_check)
        viewport = QWidget()
        viewport_row = QHBoxLayout(viewport)
        viewport_row.setContentsMargins(0, 0, 0, 0)
        self.viewport_width_spin = QSpinBox()
        self.viewport_width_spin.setRange(640, 4096)
        self.viewport_width_spin.setValue(1440)
        self.viewport_height_spin = QSpinBox()
        self.viewport_height_spin.setRange(480, 4096)
        self.viewport_height_spin.setValue(1000)
        viewport_row.addWidget(QLabel("가로"))
        viewport_row.addWidget(self.viewport_width_spin)
        viewport_row.addWidget(QLabel("세로"))
        viewport_row.addWidget(self.viewport_height_spin)
        output_form.addRow("뷰포트", viewport)
        layout.addWidget(output)
        root.addWidget(group)

    def _build_control_ui(self, root: QVBoxLayout) -> None:
        group = QGroupBox("3. 실행 제어")
        layout = QVBoxLayout(group)
        buttons = QHBoxLayout()
        self.test_button = QPushButton("현재 키워드 1건 테스트")
        self.start_button = QPushButton("전체 실행")
        self.pause_button = QPushButton("일시정지")
        self.resume_button = QPushButton("재개")
        self.stop_button = QPushButton("중단")
        self.user_action_button = QPushButton("Chrome 처리 완료")
        for button in (self.pause_button, self.resume_button, self.stop_button, self.user_action_button):
            button.setEnabled(False)
        for button in (self.test_button, self.start_button, self.pause_button, self.resume_button, self.stop_button, self.user_action_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        status = QHBoxLayout()
        self.state_label = QLabel()
        self.detail_label = QLabel()
        self.count_label = QLabel("완료 0 / 0 · 성공 0 · 실패 0")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        status.addWidget(QLabel("상태"))
        status.addWidget(self.state_label)
        status.addWidget(self.detail_label, 1)
        status.addWidget(self.count_label)
        status.addWidget(self.progress_bar)
        layout.addLayout(status)
        root.addWidget(group)

    def _build_result_ui(self, root: QVBoxLayout) -> None:
        splitter = QSplitter(Qt.Orientation.Vertical)
        table_box = QWidget()
        table_layout = QVBoxLayout(table_box)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.addWidget(QLabel("작업 목록"))
        self.job_table = QTableWidget(0, 7)
        self.job_table.setHorizontalHeaderLabels(["번호", "키워드", "도메인", "검색식", "상태", "저장 파일", "메시지"])
        self.job_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.job_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.job_table.setAlternatingRowColors(True)
        self.job_table.horizontalHeader().setStretchLastSection(True)
        table_layout.addWidget(self.job_table)
        splitter.addWidget(table_box)
        log_box = QWidget()
        log_layout = QVBoxLayout(log_box)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.addWidget(QLabel("로그"))
        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumBlockCount(5000)
        log_layout.addWidget(self.log_edit)
        splitter.addWidget(log_box)
        splitter.setSizes([480, 180])
        root.addWidget(splitter, 1)
        self._input_widgets = [self.keyword_edit, self.load_txt_button, self.dedupe_button, self.clear_button, self.domain_edit, self.exclude_public_check, self.search_mode_combo, self.exact_phrase_check, self.delay_spin, self.timeout_spin, self.viewport_width_spin, self.viewport_height_spin, self.output_edit, self.browse_output_button, self.overwrite_check, self.metadata_check]

    def _connect_signals(self) -> None:
        self.keyword_edit.textChanged.connect(self._update_keyword_count)
        self.load_txt_button.clicked.connect(self._load_keyword_file)
        self.dedupe_button.clicked.connect(self._normalize_keyword_text)
        self.clear_button.clicked.connect(self.keyword_edit.clear)
        self.browse_output_button.clicked.connect(self._browse_output_directory)
        self.open_output_button.clicked.connect(self._open_output_directory)
        self.test_button.clicked.connect(lambda: self._start(test_mode=True))
        self.start_button.clicked.connect(lambda: self._start(test_mode=False))
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

    @Slot()
    def _update_keyword_count(self) -> None:
        raw = [line for line in self.keyword_edit.toPlainText().splitlines() if line.strip()]
        valid = self._current_keywords()
        self.keyword_count_label.setText(f"입력 {len(raw)}개 · 유효 {len(valid)}개 · 제거 대상 {max(0, len(raw) - len(valid))}개")

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
        domains = normalize_domains(self.domain_edit.toPlainText().splitlines())
        if not domains:
            raise ValueError("검색할 도메인을 하나 이상 선택하세요.")
        output_root = resolve_display_path(self.output_edit.text()).resolve()
        if output_root.exists() and not output_root.is_dir():
            raise ValueError(f"출력 경로가 폴더가 아닙니다: {output_root}")
        output_root.mkdir(parents=True, exist_ok=True)
        profile_dir = self._profile_directory().expanduser().resolve()
        profile_dir.mkdir(parents=True, exist_ok=True)
        return RunConfig(keywords, domains, output_root, profile_dir, search_mode=str(self.search_mode_combo.currentData()), exact_phrase=self.exact_phrase_check.isChecked(), exclude_public_from_root=self.exclude_public_check.isChecked(), viewport_width=self.viewport_width_spin.value(), viewport_height=self.viewport_height_spin.value(), timeout_seconds=self.timeout_spin.value(), delay_between_jobs_seconds=self.delay_spin.value(), overwrite=self.overwrite_check.isChecked(), keep_chrome_open=False, write_metadata=self.metadata_check.isChecked(), headless=False, verbose=True)

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
            QMessageBox.warning(self, "실행 조건 확인", str(exc))
            return
        self._prepare_job_table(config)
        self._fatal_error_text = ""
        self._control = BatchControl()
        self._thread = QThread(self)
        self._worker = BatchWorker(config, self._control, resume_run_id=resume_run_id)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.log_emitted.connect(self._append_log)
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
        self.domain_edit.setPlainText("\n".join(config.domains))
        self.exclude_public_check.setChecked(config.exclude_public_from_root)
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
            shown_path = display_path(Path(screenshot_path)) if screenshot_path else ""
            self._set_table_text(row, 5, shown_path)
            self._set_table_text(row, 6, error_message)

    def _prepare_job_table(self, config: RunConfig) -> None:
        total = len(config.keywords) * len(config.domains)
        self.job_table.clearContents()
        self.job_table.setRowCount(total)
        self.progress_bar.setValue(0)
        self.count_label.setText(f"완료 0 / {total} · 성공 0 · 실패 0")
        row = 0
        for keyword in config.keywords:
            for domain in config.domains:
                query = build_query(domain, keyword, exact_phrase=config.exact_phrase, exclude_public_from_root=config.exclude_public_from_root)
                for column, value in enumerate((str(row + 1), keyword, domain, query, _JOB_TEXT[JobStatus.PENDING], "", "")):
                    self.job_table.setItem(row, column, QTableWidgetItem(value))
                row += 1

    @Slot(str, str)
    def _on_state_changed(self, state: str, message: str) -> None:
        self._apply_state(state, message)

    @Slot(object)
    def _on_job_changed(self, update: object) -> None:
        if not isinstance(update, JobUpdate):
            return
        row = update.index - 1
        if row < 0 or row >= self.job_table.rowCount():
            return
        self._set_table_text(row, 4, _JOB_TEXT[update.status])
        shown_path = display_path(update.path) if update.path is not None else ""
        self._set_table_text(row, 5, shown_path)
        self._set_table_text(row, 6, update.message)
        self.job_table.scrollToItem(self.job_table.item(row, 0))

    @Slot(int, int, int, int)
    def _on_progress_changed(self, completed: int, total: int, succeeded: int, failed: int) -> None:
        self.progress_bar.setValue(int(completed / total * 100) if total else 0)
        self.count_label.setText(f"완료 {completed} / {total} · 성공 {succeeded} · 실패 {failed}")

    @Slot(str, str)
    def _on_user_action_required(self, _state: str, label: str) -> None:
        self.user_action_button.setEnabled(True)
        QMessageBox.information(self, "Chrome에서 직접 처리 필요", f"{label}이 표시되었습니다.\n\nChrome에서 처리한 뒤 'Chrome 처리 완료'를 누르세요.")

    @Slot(str)
    def _on_fatal_error(self, details: str) -> None:
        self._fatal_error_text = details
        self._append_log(details)
        self._apply_state(BatchState.FAILED.value, details.splitlines()[0] if details else "알 수 없는 오류")
        QMessageBox.critical(self, "작업 실행 실패", details.splitlines()[0] if details else "알 수 없는 오류")

    @Slot(object)
    def _on_worker_finished(self, summary: object) -> None:
        if isinstance(summary, Stage2Summary):
            self._on_progress_changed(summary.completed, summary.total, summary.succeeded, summary.failed)
            if summary.stopped:
                self._apply_state(BatchState.STOPPED.value, f"완료 {summary.completed}/{summary.total}, 미실행·취소 {summary.cancelled}")
            elif self._last_state != BatchState.FAILED.value:
                self._apply_state(BatchState.COMPLETED.value, f"성공 {summary.succeeded}, 실패 {summary.failed}")
        elif not self._fatal_error_text:
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
            self._apply_state(BatchState.STOPPING.value, "현재 CDP 명령이 끝나는 즉시 작업을 중단합니다.")
            self._append_log("중단 요청")

    @Slot()
    def _confirm_user_action(self) -> None:
        if self._control is not None:
            self.user_action_button.setEnabled(False)
            self._control.confirm_user_action()
            self._append_log("사용자 처리 완료 신호 전송")

    @Slot(str)
    def _append_log(self, message: str) -> None:
        self.log_edit.appendPlainText(f"[{datetime.now().strftime('%H:%M:%S')}] {message.rstrip()}")

    def _set_table_text(self, row: int, column: int, text: str) -> None:
        item = self.job_table.item(row, column)
        if item is None:
            item = QTableWidgetItem()
            self.job_table.setItem(row, column, item)
        item.setText(text)

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
