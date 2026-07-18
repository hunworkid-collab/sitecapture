from __future__ import annotations

import traceback
from datetime import datetime
from pathlib import Path

from .capture import atomic_write_bytes, atomic_write_json, capture_png, sha256_hex, validate_png
from .cdp import CdpConnection
from .chrome import ChromeSession, locate_chrome
from .errors import BrowserDisconnectedError, BrowserRecoveryError, RunCancelled, Stage1Error, UserActionRequiredError
from .google import GoogleSearchPage
from .gui.control import BatchControl
from .gui.events import BatchState, JobStatus, JobUpdate, RunnerCallbacks, Stage2Summary
from .manifest import ResultManifest
from .models import CaptureRect, CaptureResult, PageState, RunConfig
from .paths import build_output_path
from .persistence import JobRepository, RunStatus, StoredJob
from .query import build_query


class Stage2Runner:
    def __init__(
        self,
        config: RunConfig,
        control: BatchControl,
        callbacks: RunnerCallbacks | None = None,
        repository: JobRepository | None = None,
        run_id: str | None = None,
    ) -> None:
        self.config = config
        self.control = control
        self.callbacks = callbacks or RunnerCallbacks()
        self.repository = repository
        self.run_id = run_id
        self._current_state = BatchState.IDLE
        self._state_before_pause = BatchState.RUNNING
        self._pause_announced = False

    def run(self) -> Stage2Summary:
        session: ChromeSession | None = None
        cdp: CdpConnection | None = None
        work_items: list[tuple[int, str, str, str, StoredJob | None]] = []

        if self.repository is not None and self.run_id is not None:
            total, completed, succeeded, failed = self.repository.run_counts(self.run_id)
            summary = Stage2Summary(
                total=total,
                completed=completed,
                succeeded=succeeded,
                failed=failed,
            )
            for job in self.repository.pending_jobs(self.run_id):
                work_items.append((job.sequence, job.keyword_original, job.domain, job.query, job))
        else:
            total = len(self.config.keywords) * len(self.config.domains)
            summary = Stage2Summary(total=total)
            sequence = 0
            for keyword in self.config.keywords:
                for domain in self.config.domains:
                    sequence += 1
                    query = build_query(
                        domain,
                        keyword,
                        exact_phrase=self.config.exact_phrase,
                    )
                    work_items.append((sequence, keyword, domain, query, None))

        manifest = ResultManifest(self.config.output_root)

        try:
            self._set_state(BatchState.PREPARING, "Chrome 실행을 준비합니다.")
            session, cdp, page = self._open_browser()
            self._set_state(BatchState.RUNNING, "검색 작업을 시작합니다.")
            self._emit_progress(summary)

            for position, item in enumerate(work_items):
                sequence, keyword, domain, query, stored_job = item
                self._checkpoint()
                if position > 0 and self.config.delay_between_jobs_seconds > 0:
                    self._interruptible_sleep(self.config.delay_between_jobs_seconds)

                try:
                    result, session, cdp, page = self._run_one_job(
                        session,
                        cdp,
                        page,
                        sequence,
                        summary.total,
                        keyword,
                        domain,
                        query,
                        stored_job,
                    )
                except RunCancelled:
                    if stored_job is not None and self.repository is not None:
                        self.repository.reset_job_pending(stored_job.id)
                    self.callbacks.job_changed(
                        JobUpdate(sequence, summary.total, keyword, domain, query, JobStatus.CANCELLED, "사용자 중단")
                    )
                    raise
                except BrowserRecoveryError as exc:
                    self._record_failed_job(
                        manifest,
                        summary,
                        sequence,
                        keyword,
                        domain,
                        query,
                        stored_job,
                        exc,
                    )
                    self._set_state(BatchState.FAILED, str(exc))
                    return summary
                except Exception as exc:
                    self._record_failed_job(
                        manifest,
                        summary,
                        sequence,
                        keyword,
                        domain,
                        query,
                        stored_job,
                        exc,
                    )
                    continue

                self._record_successful_job(
                    manifest,
                    summary,
                    sequence,
                    keyword,
                    domain,
                    query,
                    stored_job,
                    result,
                )

            if self.repository is not None and self.run_id is not None:
                self.repository.finish_run(self.run_id)
            self._set_state(
                BatchState.COMPLETED,
                f"완료: 성공 {summary.succeeded}, 실패 {summary.failed}",
            )
            return summary
        except RunCancelled:
            summary.stopped = True
            summary.cancelled = max(0, summary.total - summary.completed)
            if self.repository is not None and self.run_id is not None:
                self.repository.finish_run(self.run_id, stopped=True)
            self._set_state(BatchState.STOPPED, f"중단됨: {summary.completed}/{summary.total}")
            return summary
        except Exception as exc:
            if self.repository is not None and self.run_id is not None:
                self.repository.set_run_status(self.run_id, RunStatus.FAILED, str(exc))
            self._set_state(BatchState.FAILED, str(exc))
            raise
        finally:
            self._close_browser(session, cdp)

    def _run_one_job(
        self,
        session: ChromeSession,
        cdp: CdpConnection,
        page: GoogleSearchPage,
        sequence: int,
        total: int,
        keyword: str,
        domain: str,
        query: str,
        stored_job: StoredJob | None,
    ) -> tuple[CaptureResult, ChromeSession, CdpConnection, GoogleSearchPage]:
        self.callbacks.job_changed(
            JobUpdate(sequence, total, keyword, domain, query, JobStatus.RUNNING, "검색 중")
        )
        self.callbacks.log(f"[{sequence}/{total}] 검색 시작: {query}")
        if stored_job is not None and self.repository is not None:
            self.repository.mark_job_running(stored_job.id)

        browser_restart_used = False
        while True:
            try:
                result = self._execute_one_with_retry(
                    page,
                    cdp,
                    keyword,
                    domain,
                    query,
                    max_attempts=stored_job.max_attempts if stored_job is not None else self.config.max_attempts,
                )
                return result, session, cdp, page
            except BrowserDisconnectedError as exc:
                if browser_restart_used:
                    raise
                browser_restart_used = True
                self.callbacks.log(f"Chrome/CDP 연결이 종료되었습니다: {exc}")
                self.callbacks.log("Chrome을 다시 실행하고 현재 작업을 한 번 재시도합니다.")
                self._close_browser(session, cdp, force=True)
                self._interruptible_sleep(1.0)
                try:
                    session, cdp, page = self._open_browser()
                except (KeyError, OSError, Stage1Error) as exc:
                    raise BrowserRecoveryError(
                        "Chrome/CDP 재실행에 실패했습니다. 남은 작업을 중단합니다.",
                        method="browser-restart",
                    ) from exc

    def _record_successful_job(
        self,
        manifest: ResultManifest,
        summary: Stage2Summary,
        sequence: int,
        keyword: str,
        domain: str,
        query: str,
        stored_job: StoredJob | None,
        result: CaptureResult,
    ) -> None:
        if stored_job is not None and self.repository is not None:
            self.repository.mark_job_success(stored_job.id, result)
        try:
            csv_path = manifest.append_success(
                sequence=sequence,
                keyword=keyword,
                domain=domain,
                query=query,
                result=result,
            )
            self.callbacks.log(f"결과 CSV 기록: {csv_path}")
        except Exception as csv_error:
            self.callbacks.log(f"results.csv 기록 실패: {csv_error}")
        summary.completed += 1
        summary.succeeded += 1
        summary.results.append(result)
        self.callbacks.job_changed(
            JobUpdate(
                sequence,
                summary.total,
                keyword,
                domain,
                query,
                JobStatus.SUCCESS,
                f"{result.png_width}×{result.png_height}",
                result.path,
                result.state.value,
            )
        )
        self.callbacks.log(f"[{sequence}/{summary.total}] 저장 완료: {result.path}")
        self._emit_progress(summary)

    def _record_failed_job(
        self,
        manifest: ResultManifest,
        summary: Stage2Summary,
        sequence: int,
        keyword: str,
        domain: str,
        query: str,
        stored_job: StoredJob | None,
        error: Exception,
    ) -> None:
        if stored_job is not None and self.repository is not None:
            self.repository.mark_job_failed(stored_job.id, error)
        try:
            csv_path = manifest.append_failure(
                sequence=sequence,
                keyword=keyword,
                domain=domain,
                query=query,
                error=error,
            )
            self.callbacks.log(f"실패 결과 CSV 기록: {csv_path}")
        except Exception as csv_error:
            self.callbacks.log(f"results.csv 기록 실패: {csv_error}")
        summary.completed += 1
        summary.failed += 1
        message = str(error)
        summary.errors.append(f"{query}: {message}")
        self.callbacks.job_changed(
            JobUpdate(sequence, summary.total, keyword, domain, query, JobStatus.FAILED, message)
        )
        self.callbacks.log(
            "\n".join(
                (
                    f"[{sequence}/{summary.total}] 작업 실패",
                    f"  키워드: {keyword}",
                    f"  도메인: {domain}",
                    f"  검색식: {query}",
                    f"  예외: {type(error).__name__}: {message}",
                    "  전체 예외 추적:",
                    traceback.format_exc().rstrip(),
                )
            )
        )
        self._emit_progress(summary)

    def _open_browser(
        self,
    ) -> tuple[ChromeSession, CdpConnection, GoogleSearchPage]:
        self._checkpoint()
        executable = locate_chrome(self.config.chrome_path)
        session = ChromeSession(
            executable=executable,
            profile_dir=self.config.profile_dir,
            width=self.config.viewport_width,
            height=self.config.viewport_height,
            headless=self.config.headless,
        )
        cdp: CdpConnection | None = None

        try:
            self.callbacks.log(f"Chrome: {executable}")
            session.start(timeout=min(max(self.config.timeout_seconds, 10.0), 60.0))
            self._checkpoint()
            target = session.get_page_target()
            websocket_url = target.get("webSocketDebuggerUrl")
            if not websocket_url:
                raise Stage1Error("Chrome 페이지 Target의 WebSocket 주소가 없습니다.")

            cdp = CdpConnection(
                str(websocket_url),
                default_timeout=self.config.timeout_seconds,
                checkpoint=self._checkpoint,
            )
            cdp.connect()
            page = GoogleSearchPage(
                cdp,
                viewport_width=self.config.viewport_width,
                viewport_height=self.config.viewport_height,
                timeout_seconds=self.config.timeout_seconds,
                stabilization_interval_seconds=self.config.stabilization_interval_seconds,
                stabilization_required_count=self.config.stabilization_required_count,
                checkpoint=self._checkpoint,
            )
            page.initialize()
            self.callbacks.log("Chrome/CDP 연결 완료")
            return session, cdp, page
        except Exception:
            if cdp is not None:
                cdp.close()
            session.stop()
            raise

    def _execute_one_with_retry(
        self,
        page: GoogleSearchPage,
        cdp: CdpConnection,
        keyword: str,
        domain: str,
        query: str,
        *,
        max_attempts: int | None = None,
    ) -> CaptureResult:
        retry_limit = self.config.max_attempts if max_attempts is None else max_attempts

        for attempt in range(1, retry_limit + 1):
            self._checkpoint()

            try:
                return self._execute_one(page, cdp, keyword, domain, query)
            except RunCancelled:
                raise
            except BrowserDisconnectedError:
                raise
            except Stage1Error as exc:
                if attempt >= retry_limit:
                    raise

                self.callbacks.log(f"검색 실패: {exc}")
                self.callbacks.log("페이지를 정리한 뒤 같은 검색을 한 번 다시 실행합니다.")
                self._reset_page_for_retry(cdp)
                self._interruptible_sleep(2.0)

        raise Stage1Error(f"검색 재시도 실패: {query}")

    def _reset_page_for_retry(self, cdp: CdpConnection) -> None:
        try:
            cdp.call("Page.stopLoading", timeout=3.0)
        except Exception:
            pass

        try:
            cdp.call(
                "Page.reload",
                {"ignoreCache": True},
                timeout=min(self.config.timeout_seconds, 10.0),
            )
        except Exception:
            pass

    def _execute_one(
        self,
        page: GoogleSearchPage,
        cdp: CdpConnection,
        keyword: str,
        domain: str,
        query: str,
    ) -> CaptureResult:
        self._checkpoint()
        if self.config.search_mode == "search-box":
            page.open_google_home()
            state = self._resolve_user_action(page, page.page_state())
            if state == PageState.NETWORK_ERROR:
                raise Stage1Error("Google 홈을 불러오지 못했습니다.")
            page.submit_search_box(query)
        else:
            page.search_direct_url(query)

        state = self._resolve_user_action(page, page.wait_for_terminal_state())
        if state == PageState.NETWORK_ERROR:
            raise Stage1Error("Google 검색 결과 페이지에 네트워크 오류가 있습니다.")
        if state not in {PageState.SEARCH_RESULTS, PageState.NO_RESULTS}:
            raise Stage1Error(f"예상하지 못한 페이지 상태: {state.value}")

        self._checkpoint()
        rect = self._capture_rect_after_user_action(page)
        if rect.selector == "result-card-fallback":
            self.callbacks.log(
                "Google 기본 본문 선택자를 찾지 못해 검색결과 카드 기준으로 캡처 영역을 계산했습니다."
            )
        self._checkpoint()
        png = capture_png(cdp, rect)
        png_width, png_height = validate_png(png)
        digest = sha256_hex(png)
        captured_at = datetime.now().astimezone()
        destination = build_output_path(
            self.config.output_root,
            captured_at=captured_at,
            domain=domain,
            keyword=keyword,
            overwrite=self.config.overwrite,
        )
        self._checkpoint()
        atomic_write_bytes(destination, png)

        metadata_path: Path | None = None
        search_url = page.current_url()
        if self.config.write_metadata:
            metadata_path = destination.with_suffix(".json")
            atomic_write_json(
                metadata_path,
                {
                    "keyword": keyword,
                    "domain": domain,
                    "query": query,
                    "page_state": state.value,
                    "captured_at": captured_at.isoformat(timespec="seconds"),
                    "search_url": search_url,
                    "screenshot_path": str(destination),
                    "capture_selector": rect.selector,
                    "capture_rect": {
                        "x": rect.x,
                        "y": rect.y,
                        "width": rect.width,
                        "height": rect.height,
                    },
                    "png_width": png_width,
                    "png_height": png_height,
                    "sha256": digest,
                },
            )

        return CaptureResult(
            keyword=keyword,
            domain=domain,
            query=query,
            state=state,
            path=destination,
            search_url=search_url,
            captured_at=captured_at.isoformat(timespec="seconds"),
            rect=rect,
            png_width=png_width,
            png_height=png_height,
            sha256=digest,
            metadata_path=metadata_path,
        )

    def _capture_rect_after_user_action(self, page: GoogleSearchPage) -> CaptureRect:
        while True:
            try:
                return page.stable_main_rect()
            except UserActionRequiredError as exc:
                try:
                    state = PageState(str(exc))
                except ValueError:
                    state = page.page_state()
                state = self._resolve_user_action(page, state)
                if state == PageState.NETWORK_ERROR:
                    raise Stage1Error("Google 검색 결과 페이지에 네트워크 오류가 있습니다.")
                if state not in {PageState.SEARCH_RESULTS, PageState.NO_RESULTS}:
                    raise Stage1Error(f"예상하지 못한 페이지 상태: {state.value}")

    def _resolve_user_action(self, page: GoogleSearchPage, state: PageState) -> PageState:
        while state in {PageState.CONSENT_REQUIRED, PageState.CAPTCHA_REQUIRED}:
            if self.config.headless:
                raise Stage1Error(f"{state.value}: headless 모드에서는 직접 처리가 필요합니다.")
            label = "Google 동의 화면" if state == PageState.CONSENT_REQUIRED else "Google CAPTCHA 화면"
            self.control.prepare_user_action()
            self._set_state(BatchState.USER_ACTION_REQUIRED, f"{label}을 Chrome에서 처리하세요.")
            self.callbacks.log(f"사용자 조치 필요: {label}")
            self.callbacks.user_action_required(state.value, label)
            self.control.wait_for_user_action()
            self._set_state(BatchState.RUNNING, "사용자 조치 후 상태를 다시 확인합니다.")
            page.wait_document_ready()
            state = page.page_state()
        return state

    def _checkpoint(self) -> None:
        if self.control.pause_requested and not self._pause_announced:
            self._state_before_pause = self._current_state
            self._pause_announced = True
            self._set_state(BatchState.PAUSED, "일시정지 요청을 처리 중입니다.")
        waited = self.control.checkpoint()
        if waited and self._pause_announced:
            resume_state = self._state_before_pause
            if resume_state in {
                BatchState.IDLE,
                BatchState.PAUSED,
                BatchState.USER_ACTION_REQUIRED,
                BatchState.STOPPING,
                BatchState.STOPPED,
                BatchState.COMPLETED,
                BatchState.FAILED,
            }:
                resume_state = BatchState.RUNNING
            self._pause_announced = False
            self._set_state(resume_state, "작업을 재개했습니다.")

    def _interruptible_sleep(self, seconds: float) -> None:
        self.control.interruptible_sleep(seconds)

    def _emit_progress(self, summary: Stage2Summary) -> None:
        self.callbacks.progress_changed(
            summary.completed,
            summary.total,
            summary.succeeded,
            summary.failed,
        )

    def _set_state(self, state: BatchState, message: str) -> None:
        self._current_state = state
        self.callbacks.state_changed(state.value, message)

        if self.repository is None or self.run_id is None:
            return

        status_map = {
            BatchState.PREPARING: RunStatus.PREPARING,
            BatchState.RUNNING: RunStatus.RUNNING,
            BatchState.PAUSED: RunStatus.PAUSED,
            BatchState.USER_ACTION_REQUIRED: RunStatus.USER_ACTION_REQUIRED,
            BatchState.STOPPING: RunStatus.STOPPING,
            BatchState.STOPPED: RunStatus.STOPPED,
            BatchState.COMPLETED: RunStatus.COMPLETED,
            BatchState.FAILED: RunStatus.FAILED,
        }
        run_status = status_map.get(state)
        if run_status is not None:
            self.repository.set_run_status(self.run_id, run_status, message)

    def _close_browser(
        self,
        session: ChromeSession | None,
        cdp: CdpConnection | None,
        *,
        force: bool = False,
    ) -> None:
        should_close = force or not self.config.keep_chrome_open
        if cdp is not None:
            if should_close and cdp.connected:
                try:
                    cdp.call("Browser.close", timeout=3.0)
                except Exception:
                    pass
            cdp.close()

        if session is None:
            return
        if should_close:
            session.stop()
        else:
            self.callbacks.log("설정에 따라 Chrome을 열어 둡니다.")
