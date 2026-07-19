from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt, QStandardPaths, QThread, QTimer, QUrl, Slot
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..keyword_io import read_keyword_text_file
from ..models import RunConfig
from ..paths import (
    application_data_directory,
    display_path,
)
from ..persistence import JobRepository
from ..execution import BatchControl, BatchState, JobUpdate, Stage2Summary
from .dialogs import show_validation_message
from .execution_view import ExecutionView
from .log_view import LogView
from .results_view import ResultsView
from .settings_view import SettingsView
from .theme import GUI_STYLE_SHEET
from .widgets import create_card
from .worker import BatchWorker


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
        self._remaining_job_count = 0
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
        self._build_help_dialog()
        layout.addWidget(self.workspace_tabs, 1)
        self.footer_label = QLabel("Copyright © HUNHUN")
        self.footer_label.setObjectName("appFooter")
        self.footer_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.footer_label)
        self.setCentralWidget(root)
        self._connect_signals()
        self._apply_state(BatchState.IDLE.value, "키워드와 실행 조건을 입력하세요.")
        QTimer.singleShot(0, self._check_resume_job)

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
        self.log_view.clear_log_button.clicked.connect(self._clear_log)
        self.execution_view.keyword_file_requested.connect(self._load_keyword_file)
        self.execution_view.keywords_normalized.connect(self._log_keyword_normalized)
        self.execution_view.domain_validation_failed.connect(
            self._show_domain_validation_error
        )
        self.settings_view.browse_output_requested.connect(self._browse_output_directory)
        self.settings_view.open_output_requested.connect(self._open_output_directory)
        self.execution_view.completion_open_output_button.clicked.connect(
            self._open_output_directory
        )
        self.execution_view.test_requested.connect(lambda: self._start(test_mode=True))
        self.execution_view.start_requested.connect(lambda: self._start(test_mode=False))
        self.execution_view.retry_requested.connect(self._retry_failed_jobs)
        self.execution_view.resume_remaining_requested.connect(
            self._resume_remaining_jobs
        )
        self.execution_view.pause_requested.connect(self._request_pause)
        self.execution_view.resume_requested.connect(self._request_resume)
        self.execution_view.stop_requested.connect(self._request_stop)
        self.execution_view.user_action_confirmed.connect(self._confirm_user_action)
        self.results_view.job_completed.connect(self.execution_view.record_completed_job)

    @staticmethod
    def _default_download_path() -> str:
        value = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        return display_path(Path(value)) if value else display_path(Path.home() / "Downloads")

    @staticmethod
    def _profile_directory() -> Path:
        value = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
        return Path(value) / "chrome-profile" if value else application_data_directory() / "chrome-profile"

    @Slot()
    def _load_keyword_file(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "키워드 TXT 선택", str(Path.home()), "Text files (*.txt);;All files (*.*)")
        if not filename:
            return
        try:
            keywords = tuple(read_keyword_text_file(Path(filename)))
            if not keywords:
                raise ValueError("유효한 키워드가 없습니다.")
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "키워드 불러오기 실패", str(exc))
            return
        self.execution_view.set_keywords(keywords)
        self._append_log(f"키워드 파일 불러오기: {display_path(Path(filename))} ({len(keywords)}개)")

    @Slot(int)
    def _log_keyword_normalized(self, count: int) -> None:
        self._append_log(f"키워드 정리 완료: {count}개")

    @Slot(str)
    def _show_domain_validation_error(self, message: str) -> None:
        QMessageBox.warning(self, "도메인 확인", message)

    @Slot()
    def _browse_output_directory(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "출력 루트 선택",
            str(self.settings_view.output_directory()),
        )
        if selected:
            self.settings_view.set_output_directory(Path(selected))

    @Slot()
    def _open_output_directory(self) -> None:
        path = self.settings_view.output_directory()
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(self, "폴더 열기 실패", str(exc))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

    def _build_config(self) -> RunConfig:
        keywords = self.execution_view.keywords()
        if not keywords:
            raise ValueError("키워드를 하나 이상 입력하세요.")
        domains = self.execution_view.domains()
        if not domains:
            raise ValueError("검색할 도메인을 하나 이상 선택하세요.")
        profile_dir = self._profile_directory().expanduser().resolve()
        return self.settings_view.build_run_config(keywords, domains, profile_dir)

    def _start(
        self,
        *,
        test_mode: bool,
        resume_run_id: str | None = None,
        resume_config: RunConfig | None = None,
        selected_job_ids: frozenset[str] | None = None,
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
                line = self.execution_view.current_keyword()
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
        self._remaining_job_count = 0
        self.execution_view.hide_completion()
        self.execution_view.set_retry_available(False)
        self.execution_view.set_remaining_resume_available(0, enabled=False)
        if resume_run_id is not None:
            self._current_run_id = resume_run_id
        self._control = BatchControl()
        self._thread = QThread(self)
        self._worker = BatchWorker(
            config,
            self._control,
            resume_run_id=resume_run_id,
            selected_job_ids=selected_job_ids,
        )
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
        self.execution_view.set_start_actions_enabled(False)
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
        self.execution_view.set_keywords(config.keywords)
        self.execution_view.set_domains(config.domains)
        self.settings_view.apply_config(config)

    def _restore_saved_job_rows(self, repository: JobRepository, run_id: str) -> None:
        try:
            rows = repository.job_display_rows(run_id)
        except (KeyError, OSError, ValueError, sqlite3.Error) as exc:
            self._append_log(f"작업표 복원 실패: {exc}")
            return

        self.results_view.restore_jobs(rows)

    def _prepare_job_table(self, config: RunConfig) -> None:
        total = self.results_view.prepare_jobs(config)
        self.execution_view.reset_run(total)

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
        self.results_view.apply_job_update(update)

    @Slot(int, int, int, int)
    def _on_progress_changed(self, completed: int, total: int, succeeded: int, failed: int) -> None:
        self.execution_view.set_progress(completed, total, succeeded, failed)

    @Slot(str, str)
    def _on_user_action_required(self, _state: str, label: str) -> None:
        QMessageBox.information(self, "Chrome에서 직접 처리 필요", f"{label}이 표시되었습니다.\n\nChrome에서 확인한 뒤 'Chrome 확인 완료'를 누르세요.")

    @Slot(str)
    def _on_fatal_error(self, details: str) -> None:
        self._fatal_error_text = details
        self.execution_view.hide_completion()
        self._append_log(details)
        self._apply_state(BatchState.FAILED.value, details.splitlines()[0] if details else "알 수 없는 오류")
        QMessageBox.critical(self, "작업 실행 실패", details.splitlines()[0] if details else "알 수 없는 오류")

    @Slot(object)
    def _on_worker_finished(self, summary: object) -> None:
        if isinstance(summary, Stage2Summary):
            self._failed_job_count = summary.failed
            self._remaining_job_count = summary.remaining
            self._on_progress_changed(summary.completed, summary.total, summary.succeeded, summary.failed)
            if summary.stopped:
                self.execution_view.hide_completion()
                self._apply_state(BatchState.STOPPED.value, f"완료 {summary.completed}/{summary.total}, 미실행·취소 {summary.cancelled}")
            elif self.execution_view.last_state != BatchState.FAILED.value:
                self._apply_state(BatchState.COMPLETED.value, f"성공 {summary.succeeded}, 실패 {summary.failed}")
                completion_text = (
                    f"총 {summary.total}건 중 성공 {summary.succeeded}건, 실패 {summary.failed}건"
                )
                if summary.remaining:
                    completion_text += f" · 미실행 {summary.remaining}건 보존"
                self.execution_view.show_completion(
                    completion_text,
                    remaining=summary.remaining,
                )
            elif summary.remaining:
                self.execution_view.show_completion(
                    f"작업 중 오류로 중단됐습니다. 남은 {summary.remaining}건을 이어서 실행할 수 있습니다.",
                    title="남은 작업",
                    remaining=summary.remaining,
                )
        elif not self._fatal_error_text:
            self.execution_view.hide_completion()
            self._apply_state(BatchState.FAILED.value, "작업 결과를 받지 못했습니다.")

    @Slot()
    def _on_thread_finished(self) -> None:
        self._thread = None
        self._worker = None
        self._control = None
        self._set_inputs_enabled(True)
        self.execution_view.set_start_actions_enabled(True)
        self.execution_view.disable_run_controls()
        self.execution_view.set_retry_available(
            self._current_run_id is not None and self._failed_job_count > 0
        )
        self.execution_view.set_remaining_resume_available(
            self._remaining_job_count,
            enabled=True,
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
            failed_job_ids = repository.failed_job_ids(self._current_run_id)
            retried = repository.retry_failed_jobs(self._current_run_id)
            config = repository.load_config(self._current_run_id)
        except (KeyError, OSError, ValueError, sqlite3.Error) as exc:
            QMessageBox.critical(self, "실패 작업 재실행 실패", str(exc))
            return
        if retried == 0:
            self.execution_view.set_retry_available(False)
            return
        self._append_log(f"실패 작업 {retried}건 재실행 요청")
        self._start(
            test_mode=False,
            resume_run_id=self._current_run_id,
            resume_config=config,
            selected_job_ids=failed_job_ids,
        )
        self._restore_saved_job_rows(repository, self._current_run_id)

    @Slot()
    def _resume_remaining_jobs(self) -> None:
        if self._current_run_id is None or self._remaining_job_count == 0:
            return
        try:
            repository = self._database()
            remaining = repository.pending_job_count(self._current_run_id)
            if remaining == 0:
                self._remaining_job_count = 0
                self.execution_view.set_remaining_resume_available(0, enabled=False)
                return
            config = repository.load_config(self._current_run_id)
        except (KeyError, OSError, ValueError, sqlite3.Error) as exc:
            QMessageBox.critical(self, "남은 작업 재개 실패", str(exc))
            return
        self._append_log(f"남은 작업 {remaining}건 이어서 실행 요청")
        self._start(
            test_mode=False,
            resume_run_id=self._current_run_id,
            resume_config=config,
        )
        self._restore_saved_job_rows(repository, self._current_run_id)

    @Slot()
    def _confirm_user_action(self) -> None:
        if self._control is not None:
            self.execution_view.set_user_action_confirmed()
            self._control.confirm_user_action()
            self._append_log("사용자 처리 완료 신호 전송")

    @Slot(str)
    def _append_log(self, message: str) -> None:
        self.log_view.append_message(message)

    @Slot()
    def _clear_log(self) -> None:
        self.log_view.clear_messages()

    def _apply_state(self, state: str, message: str) -> None:
        active = self._thread is not None and self._thread.isRunning()
        self.execution_view.set_run_state(state, message, active=active)

    def _set_inputs_enabled(self, enabled: bool) -> None:
        self.execution_view.set_input_enabled(enabled)
        self.settings_view.set_input_enabled(enabled)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._closing = True
            if self._control is not None:
                self._control.request_stop()
            self._apply_state(BatchState.STOPPING.value, "Chrome과 Worker를 정리한 뒤 창을 닫습니다.")
            event.ignore()
            return
        event.accept()
