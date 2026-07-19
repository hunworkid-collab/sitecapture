from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QObject, QThread, QTimer, Slot
from PySide6.QtWidgets import QApplication, QMessageBox
from websocket import WebSocketTimeoutException

from site_capture.cdp import CdpConnection
from site_capture.errors import (
    BrowserDisconnectedError,
    CdpError,
    RunCancelled,
    Stage1Error,
    UserActionRequiredError,
)
from site_capture.google import GoogleSearchPage, _REGION_SCRIPT, _STATE_SCRIPT
from site_capture.execution import (
    BatchControl,
    BatchState,
    JobStatus,
    JobUpdate,
    RunnerCallbacks,
    Stage2Summary,
)
from site_capture.gui.failure import short_failure_reason
from site_capture.gui.main_window import MainWindow
from site_capture.gui.worker import BatchWorker
from site_capture.keyword_io import normalize_keywords, read_keyword_text_file
from site_capture.models import CaptureRect, CaptureResult, PageState, RunConfig
from site_capture.persistence import JobRepository, RunStatus
from site_capture.stage2_runner import Stage2Runner


class _WorkerReceiver(QObject):
    def __init__(self, loop: QEventLoop, gui_thread: QThread) -> None:
        super().__init__()
        self.loop = loop
        self.gui_thread = gui_thread
        self.summary: Stage2Summary | None = None
        self.received_on_gui_thread = False

    @Slot(object)
    def receive(self, summary: object) -> None:
        self.summary = summary if isinstance(summary, Stage2Summary) else None
        self.received_on_gui_thread = QThread.currentThread() == self.gui_thread
        self.loop.quit()


class KeywordIoTests(unittest.TestCase):
    def test_normalize_keywords_preserves_order_and_removes_duplicates(self) -> None:
        self.assertEqual(normalize_keywords(["  A  B ", "A B", "", "C"]), ("A B", "C"))

    def test_read_keyword_text_file_accepts_cp949(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "keywords.txt"
            path.write_bytes("검색어A\n검색어B".encode("cp949"))
            self.assertEqual(read_keyword_text_file(path), ["검색어A", "검색어B"])


class BatchControlTests(unittest.TestCase):
    def test_pause_and_resume_checkpoint(self) -> None:
        control = BatchControl()
        entered = threading.Event()
        finished = threading.Event()

        def wait_for_resume() -> None:
            entered.set()
            control.checkpoint()
            finished.set()

        thread = threading.Thread(target=wait_for_resume)
        thread.start()
        self.assertTrue(entered.wait(timeout=1))
        control.request_pause()
        control.request_resume()
        thread.join(timeout=1)
        self.assertTrue(finished.is_set())

    def test_stop_checkpoint_raises(self) -> None:
        control = BatchControl()
        control.request_stop()
        with self.assertRaises(RunCancelled):
            control.checkpoint()


class GooglePageTests(unittest.TestCase):
    def test_google_scripts_support_result_heading_fallback(self) -> None:
        self.assertIn('document.querySelectorAll("a h3")', _REGION_SCRIPT)
        self.assertIn('selector: "search-results-with-search-box"', _REGION_SCRIPT)
        self.assertIn("const makeTopCapture", _REGION_SCRIPT)
        self.assertIn("const hasSearchResults", _STATE_SCRIPT)

    def test_wait_for_url_change_reraises_unrelated_cdp_error(self) -> None:
        class FailingCdp:
            def evaluate(self, _expression: str) -> str:
                raise CdpError("unrelated evaluation failure", method="Runtime.evaluate")

        page = GoogleSearchPage(
            FailingCdp(),
            viewport_width=1440,
            viewport_height=1000,
            timeout_seconds=1.0,
            stabilization_interval_seconds=0.01,
            stabilization_required_count=1,
        )
        with self.assertRaises(CdpError):
            page._wait_for_url_change("about:blank", timeout=0.1)


class CdpConnectionTests(unittest.TestCase):
    def test_waiting_for_cdp_response_checks_cancellation_checkpoint(self) -> None:
        class WaitingSocket:
            connected = True

            def __init__(self) -> None:
                self.timeouts: list[float] = []

            def send(self, _payload: str) -> None:
                return None

            def settimeout(self, timeout: float) -> None:
                self.timeouts.append(timeout)

            def recv(self) -> str:
                raise WebSocketTimeoutException("still waiting")

            def close(self) -> None:
                return None

        checkpoint_calls = 0

        def checkpoint() -> None:
            nonlocal checkpoint_calls
            checkpoint_calls += 1
            if checkpoint_calls == 3:
                raise RunCancelled("stop")

        socket = WaitingSocket()
        connection = CdpConnection(
            "ws://test",
            checkpoint=checkpoint,
        )
        connection._ws = socket

        with self.assertRaises(RunCancelled):
            connection.call("Page.captureScreenshot", timeout=60.0)

        self.assertEqual(len(socket.timeouts), 1)
        self.assertLessEqual(socket.timeouts[0], 0.2)

    def test_send_disconnect_closes_connection(self) -> None:
        class FailingSocket:
            connected = True

            def send(self, _payload: str) -> None:
                raise OSError("socket closed")

            def close(self) -> None:
                return None

        connection = CdpConnection("ws://test")
        connection._ws = FailingSocket()

        with self.assertRaises(BrowserDisconnectedError):
            connection.call("Page.enable")

        self.assertFalse(connection.connected)

    def test_receive_disconnect_closes_connection(self) -> None:
        class FailingSocket:
            connected = True

            def send(self, _payload: str) -> None:
                return None

            def settimeout(self, _timeout: float) -> None:
                return None

            def recv(self) -> str:
                raise OSError("socket closed")

            def close(self) -> None:
                return None

        connection = CdpConnection("ws://test")
        connection._ws = FailingSocket()

        with self.assertRaises(BrowserDisconnectedError):
            connection.call("Page.enable")

        self.assertFalse(connection.connected)


class Stage2RunnerTests(unittest.TestCase):
    def _runner(self, callbacks: RunnerCallbacks | None = None) -> Stage2Runner:
        config = RunConfig(
            keywords=("keyword",),
            domains=("example.com",),
            output_root=Path(tempfile.gettempdir()),
            profile_dir=Path(tempfile.gettempdir()) / "site-capture-profile",
            search_mode="direct-url",
            timeout_seconds=20.0,
        )
        return Stage2Runner(config, BatchControl(), callbacks)

    def _capture_result(self) -> CaptureResult:
        return CaptureResult(
            keyword="keyword",
            domain="example.com",
            query="site:example.com keyword",
            state=PageState.SEARCH_RESULTS,
            path=Path(tempfile.gettempdir()) / "capture.png",
            search_url="https://www.google.com/search?q=keyword",
            captured_at="2026-01-01T00:00:00+09:00",
            rect=CaptureRect(0, 0, 300, 100, "#search"),
            png_width=300,
            png_height=100,
            sha256="a" * 64,
        )

    def test_open_browser_gives_cdp_runner_checkpoint(self) -> None:
        runner = self._runner()
        session = MagicMock()
        session.get_page_target.return_value = {
            "webSocketDebuggerUrl": "ws://test"
        }
        cdp = MagicMock()
        page = MagicMock()

        with patch(
            "site_capture.stage2_runner.locate_chrome",
            return_value=Path("chrome.exe"),
        ), patch(
            "site_capture.stage2_runner.ChromeSession",
            return_value=session,
        ), patch(
            "site_capture.stage2_runner.CdpConnection",
            return_value=cdp,
        ) as cdp_factory, patch(
            "site_capture.stage2_runner.GoogleSearchPage",
            return_value=page,
        ):
            runner._open_browser()

        checkpoint = cdp_factory.call_args.kwargs["checkpoint"]
        self.assertIs(checkpoint.__self__, runner)
        self.assertIs(checkpoint.__func__, Stage2Runner._checkpoint)

    def test_execute_one_with_retry_succeeds_on_second_attempt(self) -> None:
        logs: list[str] = []
        runner = self._runner(RunnerCallbacks(log=logs.append))
        result = self._capture_result()
        page = object()
        cdp = object()

        with patch.object(
            runner,
            "_execute_one",
            side_effect=[Stage1Error("temporary"), result],
        ) as execute_one, patch.object(runner, "_reset_page_for_retry") as reset_page, patch.object(
            runner, "_interruptible_sleep"
        ) as sleep:
            actual = runner._execute_one_with_retry(
                page,
                cdp,
                "keyword",
                "example.com",
                "site:example.com keyword",
            )

        self.assertIs(actual, result)
        self.assertEqual(execute_one.call_count, 2)
        reset_page.assert_called_once_with(cdp)
        sleep.assert_called_once_with(2.0)
        self.assertIn("검색 실패: temporary", logs)

    def test_execute_one_with_retry_stops_after_two_attempts(self) -> None:
        runner = self._runner()
        page = object()
        cdp = object()

        with patch.object(
            runner,
            "_execute_one",
            side_effect=Stage1Error("persistent"),
        ) as execute_one, patch.object(runner, "_reset_page_for_retry") as reset_page, patch.object(
            runner, "_interruptible_sleep"
        ) as sleep:
            with self.assertRaisesRegex(Stage1Error, "persistent"):
                runner._execute_one_with_retry(
                    page,
                    cdp,
                    "keyword",
                    "example.com",
                    "site:example.com keyword",
                )

        self.assertEqual(execute_one.call_count, 2)
        reset_page.assert_called_once_with(cdp)
        sleep.assert_called_once_with(2.0)

    def test_execute_one_with_retry_does_not_retry_cancellation(self) -> None:
        runner = self._runner()
        page = object()
        cdp = object()

        with patch.object(runner, "_execute_one", side_effect=RunCancelled("stop")) as execute_one, patch.object(
            runner, "_reset_page_for_retry"
        ) as reset_page:
            with self.assertRaises(RunCancelled):
                runner._execute_one_with_retry(
                    page,
                    cdp,
                    "keyword",
                    "example.com",
                    "site:example.com keyword",
                )

        execute_one.assert_called_once()
        reset_page.assert_not_called()

    def test_run_reopens_browser_once_after_disconnect(self) -> None:
        result = self._capture_result()
        runner = self._runner()
        logs: list[str] = []
        runner.callbacks = RunnerCallbacks(log=logs.append)
        session_one = MagicMock()
        session_one.get_page_target.return_value = {"webSocketDebuggerUrl": "ws://one"}
        session_two = MagicMock()
        session_two.get_page_target.return_value = {"webSocketDebuggerUrl": "ws://two"}
        cdp_one = MagicMock()
        cdp_two = MagicMock()
        page_one = MagicMock()
        page_two = MagicMock()

        with tempfile.TemporaryDirectory() as directory, patch(
            "site_capture.stage2_runner.locate_chrome", return_value=Path("chrome.exe")
        ), patch(
            "site_capture.stage2_runner.ChromeSession",
            side_effect=[session_one, session_two],
        ) as chrome_factory, patch(
            "site_capture.stage2_runner.CdpConnection",
            side_effect=[cdp_one, cdp_two],
        ) as cdp_factory, patch(
            "site_capture.stage2_runner.GoogleSearchPage",
            side_effect=[page_one, page_two],
        ), patch.object(
            runner,
            "_execute_one_with_retry",
            side_effect=[BrowserDisconnectedError("lost"), result],
        ), patch.object(runner, "_interruptible_sleep"):
            runner.config = RunConfig(
                keywords=("keyword",),
                domains=("example.com",),
                output_root=Path(directory),
                profile_dir=Path(directory) / "profile",
                search_mode="direct-url",
                delay_between_jobs_seconds=0,
            )
            summary = runner.run()

        self.assertEqual(summary.succeeded, 1)
        self.assertEqual(chrome_factory.call_count, 2)
        self.assertEqual(cdp_factory.call_count, 2)
        session_one.stop.assert_called_once()
        cdp_one.close.assert_called_once()
        self.assertTrue(any("Chrome/CDP 연결이 종료되었습니다" in log for log in logs))

    def test_run_fails_after_second_disconnect_without_third_restart(self) -> None:
        runner = self._runner()
        session_one = MagicMock()
        session_one.get_page_target.return_value = {"webSocketDebuggerUrl": "ws://one"}
        session_two = MagicMock()
        session_two.get_page_target.return_value = {"webSocketDebuggerUrl": "ws://two"}

        with tempfile.TemporaryDirectory() as directory, patch(
            "site_capture.stage2_runner.locate_chrome", return_value=Path("chrome.exe")
        ), patch(
            "site_capture.stage2_runner.ChromeSession",
            side_effect=[session_one, session_two],
        ) as chrome_factory, patch(
            "site_capture.stage2_runner.CdpConnection",
            side_effect=[MagicMock(), MagicMock()],
        ), patch(
            "site_capture.stage2_runner.GoogleSearchPage",
            side_effect=[MagicMock(), MagicMock()],
        ), patch.object(
            runner,
            "_execute_one_with_retry",
            side_effect=[BrowserDisconnectedError("first"), BrowserDisconnectedError("second")],
        ), patch.object(runner, "_interruptible_sleep"):
            runner.config = RunConfig(
                keywords=("keyword",),
                domains=("example.com",),
                output_root=Path(directory),
                profile_dir=Path(directory) / "profile",
                search_mode="direct-url",
                delay_between_jobs_seconds=0,
            )
            summary = runner.run()

        self.assertEqual(summary.failed, 1)
        self.assertEqual(chrome_factory.call_count, 2)

    def test_run_logs_full_context_for_failed_job(self) -> None:
        logs: list[str] = []
        runner = self._runner(RunnerCallbacks(log=logs.append))
        browser = (MagicMock(), MagicMock(), MagicMock())

        with patch.object(runner, "_open_browser", return_value=browser), patch.object(
            runner,
            "_execute_one_with_retry",
            side_effect=Stage1Error("capture area unavailable"),
        ), patch.object(runner, "_close_browser"):
            summary = runner.run()

        detail = "\n".join(logs)
        self.assertEqual(summary.failed, 1)
        self.assertIn("키워드: keyword", detail)
        self.assertIn("도메인: example.com", detail)
        self.assertIn("예외: Stage1Error: capture area unavailable", detail)
        self.assertIn("Traceback", detail)

    def test_recovery_failure_marks_current_job_failed_and_preserves_remaining_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = RunConfig(
                keywords=("first", "second"),
                domains=("example.com",),
                output_root=root,
                profile_dir=root / "site-capture-profile",
                search_mode="direct-url",
                delay_between_jobs_seconds=0,
            )
            repository = JobRepository(root / "jobs.db")
            run_id = repository.create_run(config)
            runner = Stage2Runner(config, BatchControl(), repository=repository, run_id=run_id)
            initial_browser = (MagicMock(), MagicMock(), MagicMock())

            with patch.object(
                runner,
                "_open_browser",
                side_effect=[initial_browser, Stage1Error("restart failed")],
            ), patch.object(
                runner,
                "_execute_one_with_retry",
                side_effect=BrowserDisconnectedError("lost"),
            ) as execute_one, patch.object(runner, "_interruptible_sleep"):
                summary = runner.run()

            execute_one.assert_called_once()
            self.assertEqual(summary.failed, 1)
            self.assertEqual(summary.completed, 1)
            self.assertEqual(repository.run_counts(run_id), (2, 1, 0, 1))
            self.assertEqual(
                [row[1] for row in repository.job_display_rows(run_id)],
                ["failed", "pending"],
            )
            self.assertEqual(runner._current_state, BatchState.FAILED)

    def test_retry_selection_does_not_run_previously_pending_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = RunConfig(
                keywords=("first", "second"),
                domains=("example.com",),
                output_root=root,
                profile_dir=root / "site-capture-profile",
                search_mode="direct-url",
                delay_between_jobs_seconds=0,
            )
            repository = JobRepository(root / "jobs.db")
            run_id = repository.create_run(config)
            first_job, second_job = repository.pending_jobs(run_id)
            repository.mark_job_failed(first_job.id, Stage1Error("first failed"))
            repository.retry_failed_jobs(run_id)
            runner = Stage2Runner(
                config,
                BatchControl(),
                repository=repository,
                run_id=run_id,
                selected_job_ids=frozenset({first_job.id}),
            )
            browser = (MagicMock(), MagicMock(), MagicMock())

            with patch.object(runner, "_open_browser", return_value=browser), patch.object(
                runner,
                "_execute_one_with_retry",
                return_value=self._capture_result(),
            ) as execute_one, patch.object(runner, "_close_browser"), patch.object(
                repository,
                "set_run_status",
                wraps=repository.set_run_status,
            ) as set_run_status:
                summary = runner.run()

            execute_one.assert_called_once()
            self.assertEqual(summary.succeeded, 1)
            self.assertEqual(summary.remaining, 1)
            self.assertNotIn(
                RunStatus.COMPLETED,
                [call.args[1] for call in set_run_status.call_args_list],
            )
            self.assertEqual(repository.latest_resumable_run_id(), run_id)
            self.assertEqual(
                [row[1] for row in repository.job_display_rows(run_id)],
                ["success", "pending"],
            )

    def test_reset_page_for_retry_stops_loading_and_reloads(self) -> None:
        runner = self._runner()

        class FakeCdp:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, bool] | None, float | None]] = []

            def call(
                self,
                method: str,
                params: dict[str, bool] | None = None,
                *,
                timeout: float | None = None,
            ) -> dict[str, object]:
                self.calls.append((method, params, timeout))
                return {}

        cdp = FakeCdp()
        runner._reset_page_for_retry(cdp)

        self.assertEqual(
            cdp.calls,
            [
                ("Page.stopLoading", None, 3.0),
                ("Page.reload", {"ignoreCache": True}, 10.0),
            ],
        )

    def test_capture_time_captcha_returns_to_user_action_flow(self) -> None:
        class CaptureCaptchaPage:
            def __init__(self) -> None:
                self._stable_rect_calls = 0

            def search_direct_url(self, _query: str) -> None:
                return None

            def wait_for_terminal_state(self) -> PageState:
                return PageState.SEARCH_RESULTS

            def stable_main_rect(self) -> CaptureRect:
                self._stable_rect_calls += 1
                if self._stable_rect_calls == 1:
                    raise UserActionRequiredError(PageState.CAPTCHA_REQUIRED.value)
                return CaptureRect(0, 0, 300, 100, "#search")

            def page_state(self) -> PageState:
                return PageState.SEARCH_RESULTS

            def wait_document_ready(self) -> None:
                return None

            def current_url(self) -> str:
                return "https://www.google.com/search?q=keyword"

        config = RunConfig(
            keywords=("keyword",),
            domains=("example.com",),
            output_root=Path(tempfile.gettempdir()),
            profile_dir=Path(tempfile.gettempdir()) / "site-capture-profile",
            search_mode="direct-url",
            write_metadata=False,
        )
        control = BatchControl()
        user_action_states: list[str] = []
        callbacks = RunnerCallbacks(
            user_action_required=lambda state, _label: (
                user_action_states.append(state),
                control.confirm_user_action(),
            ),
        )
        runner = Stage2Runner(config, control, callbacks)

        with patch("site_capture.stage2_runner.capture_png", return_value=b"png"), patch(
            "site_capture.stage2_runner.validate_png", return_value=(300, 100)
        ), patch("site_capture.stage2_runner.sha256_hex", return_value="a" * 64), patch(
            "site_capture.stage2_runner.atomic_write_bytes"
        ):
            result = runner._execute_one(
                CaptureCaptchaPage(),
                object(),
                "keyword",
                "example.com",
                "site:example.com keyword",
            )

        self.assertEqual(user_action_states, [PageState.CAPTCHA_REQUIRED.value])
        self.assertEqual(result.state, PageState.SEARCH_RESULTS)

    def test_user_action_log_precedes_interactive_callback(self) -> None:
        class UserActionPage:
            def wait_document_ready(self) -> None:
                return None

            def page_state(self) -> PageState:
                return PageState.SEARCH_RESULTS

        events: list[str] = []
        control = BatchControl()
        callbacks = RunnerCallbacks(
            log=lambda _message: events.append("log"),
            user_action_required=lambda _state, _label: (
                events.append("prompt"),
                control.confirm_user_action(),
            ),
        )
        runner = Stage2Runner(self._runner().config, control, callbacks)

        state = runner._resolve_user_action(UserActionPage(), PageState.CAPTCHA_REQUIRED)

        self.assertEqual(state, PageState.SEARCH_RESULTS)
        self.assertEqual(events, ["log", "prompt"])

    def test_execute_one_logs_result_card_fallback(self) -> None:
        class FallbackPage:
            def search_direct_url(self, _query: str) -> None:
                return None

            def wait_for_terminal_state(self) -> PageState:
                return PageState.SEARCH_RESULTS

            def stable_main_rect(self) -> CaptureRect:
                return CaptureRect(0, 0, 640, 800, "result-card-fallback")

            def current_url(self) -> str:
                return "https://www.google.com/search?q=keyword"

        logs: list[str] = []
        runner = self._runner(RunnerCallbacks(log=logs.append))
        with patch("site_capture.stage2_runner.capture_png", return_value=b"png"), patch(
            "site_capture.stage2_runner.validate_png", return_value=(640, 800)
        ), patch("site_capture.stage2_runner.sha256_hex", return_value="a" * 64), patch(
            "site_capture.stage2_runner.atomic_write_bytes"
        ):
            result = runner._execute_one(
                FallbackPage(),
                object(),
                "keyword",
                "example.com",
                "site:example.com keyword",
            )

        self.assertEqual(result.rect.selector, "result-card-fallback")
        self.assertTrue(any("검색결과 카드 기준으로 캡처 영역을 계산했습니다." in log for log in logs))


class GuiModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_event_models_have_expected_values(self) -> None:
        update = JobUpdate(1, 1, "키워드", "도메인", "site:도메인 키워드", JobStatus.PENDING)
        summary = Stage2Summary(total=1)
        callbacks = RunnerCallbacks()
        self.assertEqual(BatchState.RUNNING.value, "running")
        self.assertEqual(update.status, JobStatus.PENDING)
        self.assertEqual(summary.total, 1)
        callbacks.log("ok")

    def test_main_window_offscreen_smoke(self) -> None:
        window = MainWindow()
        self.assertEqual(window.results_view.job_table.columnCount(), 7)
        self.assertEqual(window.execution_view.keyword_edit.toPlainText(), "")
        self.assertEqual(window.execution_view.domain_edit.text(), "")
        window.close()

    def test_failed_job_has_short_reason_and_retry_button(self) -> None:
        window = MainWindow()
        config = RunConfig(
            keywords=("test",),
            domains=("example.com",),
            output_root=Path(tempfile.gettempdir()) / "output",
            profile_dir=Path(tempfile.gettempdir()) / "profile",
        )
        window._prepare_job_table(config)
        window._current_run_id = "run-1"

        window._on_job_changed(
            JobUpdate(
                1,
                1,
                "test",
                "example.com",
                "site:example.com test",
                JobStatus.FAILED,
                "first cause\nfull detail",
            )
        )
        window._on_worker_finished(Stage2Summary(total=1, completed=1, failed=1))
        window._on_thread_finished()

        self.assertEqual(window.results_view.job_table.item(0, 6).text(), "first cause")
        self.assertEqual(window.results_view.job_table.item(0, 4).background().color().name(), "#ffe8e8")
        self.assertTrue(window.execution_view.retry_failed_button.isEnabled())
        self.assertEqual(short_failure_reason("first cause\nfull detail"), "first cause")
        window.close()

    def test_remaining_jobs_resume_without_restarting_the_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = RunConfig(
                keywords=("test",),
                domains=("example.com",),
                output_root=root,
                profile_dir=root / "profile",
            )
            repository = JobRepository(root / "jobs.db")
            run_id = repository.create_run(config)
            window = MainWindow()
            window._current_run_id = run_id
            window._remaining_job_count = 1

            with patch.object(window, "_database", return_value=repository), patch.object(
                window,
                "_start",
            ) as start:
                window._resume_remaining_jobs()

            start.assert_called_once_with(
                test_mode=False,
                resume_run_id=run_id,
                resume_config=config,
            )
            window.close()

    def test_failed_partial_run_shows_remaining_jobs_action(self) -> None:
        window = MainWindow()
        window._current_run_id = "run-1"
        window._on_state_changed(BatchState.FAILED.value, "Chrome 재실행 실패")

        window._on_worker_finished(
            Stage2Summary(total=2, completed=1, failed=1, remaining=1)
        )
        window._on_thread_finished()

        self.assertFalse(window.execution_view.completion_card.isHidden())
        self.assertEqual(window.execution_view.completion_title_label.text(), "남은 작업")
        self.assertFalse(
            window.execution_view.completion_resume_remaining_button.isHidden()
        )
        self.assertTrue(
            window.execution_view.completion_resume_remaining_button.isEnabled()
        )
        window.close()

    def test_main_window_builds_config_from_custom_domains(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            window = MainWindow()
            window.execution_view.set_keywords(("검색어",))
            for domain in (" Example.COM", "sub.example.com", "example.com"):
                window.execution_view.add_domain(domain)
            window.settings_view.set_output_directory(Path(directory))

            config = window._build_config()

            self.assertEqual(config.domains, ("example.com", "sub.example.com"))
            window.close()

    def test_fatal_error_marks_main_window_failed(self) -> None:
        window = MainWindow()
        with patch("site_capture.gui.main_window.QMessageBox.critical"):
            window._on_fatal_error("database initialization failed")

        self.assertEqual(window.execution_view.last_state, BatchState.FAILED.value)
        window.close()

    def test_main_window_offers_resume_and_restores_saved_rows(self) -> None:
        config = RunConfig(
            keywords=("keyword",),
            domains=("example.com", "public.example.com"),
            output_root=Path(tempfile.gettempdir()) / "saved-output",
            profile_dir=Path(tempfile.gettempdir()) / "saved-profile",
        )
        with tempfile.TemporaryDirectory() as directory:
            repository = JobRepository(Path(directory) / "jobs.db")
            run_id = repository.create_run(config)
            jobs = repository.pending_jobs(run_id)
            repository.mark_job_running(jobs[0].id)
            repository.mark_job_failed(jobs[0].id, ValueError("saved failure"))

            window = MainWindow()
            window._prepare_job_table(config)
            with patch.object(window, "_database", return_value=repository), patch.object(
                window, "_start"
            ) as start, patch(
                "site_capture.gui.main_window.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Yes,
            ):
                window._check_resume_job()

            start.assert_called_once_with(
                test_mode=False,
                resume_run_id=run_id,
                resume_config=config,
            )
            self.assertEqual(window.results_view.job_table.item(0, 4).text(), "실패")
            self.assertEqual(window.results_view.job_table.item(0, 6).text(), "saved failure")
            self.assertEqual(window.execution_view.progress_bar.value(), 50)
            window.close()

    def test_main_window_declining_resume_cancels_remaining_jobs(self) -> None:
        config = RunConfig(
            keywords=("keyword",),
            domains=("example.com",),
            output_root=Path(tempfile.gettempdir()) / "saved-output",
            profile_dir=Path(tempfile.gettempdir()) / "saved-profile",
        )
        with tempfile.TemporaryDirectory() as directory:
            repository = JobRepository(Path(directory) / "jobs.db")
            run_id = repository.create_run(config)
            window = MainWindow()
            with patch.object(window, "_database", return_value=repository), patch(
                "site_capture.gui.main_window.QMessageBox.question",
                return_value=QMessageBox.StandardButton.No,
            ):
                window._check_resume_job()

            self.assertIsNone(repository.latest_resumable_run_id())
            self.assertEqual(
                [row[1] for row in repository.job_display_rows(run_id)],
                ["cancelled"],
            )
            window.close()

    def test_worker_finished_signal_reaches_gui_thread(self) -> None:
        config = RunConfig(
            keywords=("keyword",),
            domains=("example.com",),
            output_root=Path(tempfile.gettempdir()),
            profile_dir=Path(tempfile.gettempdir()) / "site-capture-profile",
        )
        control = BatchControl()
        thread = QThread()
        worker = BatchWorker(config, control)
        worker.moveToThread(thread)
        loop = QEventLoop()
        receiver = _WorkerReceiver(loop, self.app.thread())
        worker.finished.connect(receiver.receive)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)

        class FakeRunner:
            def __init__(
                self,
                config: RunConfig,
                control: BatchControl,
                callbacks: RunnerCallbacks,
                **_kwargs: object,
            ) -> None:
                pass

            def run(self) -> Stage2Summary:
                return Stage2Summary(total=1, completed=1, succeeded=1)

        with tempfile.TemporaryDirectory() as db_directory, patch(
            "site_capture.gui.worker.application_data_directory",
            return_value=Path(db_directory),
        ), patch("site_capture.gui.worker.Stage2Runner", FakeRunner):
            thread.started.connect(worker.run)
            thread.start()
            QTimer.singleShot(2000, loop.quit)
            loop.exec()
        self.assertTrue(thread.wait(2000))
        self.assertTrue(receiver.received_on_gui_thread)
        self.assertEqual(receiver.summary, Stage2Summary(total=1, completed=1, succeeded=1))


if __name__ == "__main__":
    unittest.main()
