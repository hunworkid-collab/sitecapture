from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from ..models import CaptureResult, PageState, RunConfig
from .models import (
    RunStatus,
    StoredJob,
    StoredJobStatus,
    build_job_seeds,
    new_id,
    run_config_from_json,
    run_config_to_json,
    utc_now_text,
)
from .schema import SCHEMA_SQL, SCHEMA_VERSION
from .resume import cancel_unfinished_jobs, find_resumable_run_id


class JobRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        except sqlite3.Error:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._db() as connection:
            connection.executescript(SCHEMA_SQL)
            connection.execute(
                "INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)",
                ("schema_version", str(SCHEMA_VERSION)),
            )

    def create_run(self, config: RunConfig) -> str:
        run_id = new_id()
        now = utc_now_text()
        jobs = build_job_seeds(config)
        title = datetime.now().strftime("%Y-%m-%d %H:%M")
        with self._db() as connection:
            connection.execute(
                "INSERT INTO runs (id, title, status, config_json, output_root, profile_dir, total_jobs, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, title, RunStatus.CREATED.value, run_config_to_json(config), str(config.output_root), str(config.profile_dir), len(jobs), now, now),
            )
            connection.executemany(
                "INSERT INTO jobs (id, run_id, sequence, keyword_index, keyword_original, keyword_normalized, domain, query, status, max_attempts, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (job.id, run_id, job.sequence, job.keyword_index, job.keyword_original, job.keyword_normalized, job.domain, job.query, StoredJobStatus.PENDING.value, config.max_attempts, now, now)
                    for job in jobs
                ],
            )
        return run_id

    def recover_interrupted_runs(self) -> int:
        now = utc_now_text()
        active_statuses = (RunStatus.PREPARING.value, RunStatus.RUNNING.value, RunStatus.PAUSED.value, RunStatus.USER_ACTION_REQUIRED.value, RunStatus.STOPPING.value)
        placeholders = ",".join("?" for _ in active_statuses)
        with self._db() as connection:
            connection.execute(
                f"UPDATE jobs SET status = ?, started_at = NULL, updated_at = ? "
                f"WHERE run_id IN (SELECT id FROM runs WHERE status IN ({placeholders})) "
                "AND status IN (?, ?)",
                (
                    StoredJobStatus.PENDING.value,
                    now,
                    *active_statuses,
                    StoredJobStatus.RUNNING.value,
                    StoredJobStatus.RETRY_WAIT.value,
                ),
            )
            cursor = connection.execute(
                f"UPDATE runs SET status = ?, updated_at = ?, last_message = ? WHERE status IN ({placeholders})",
                (RunStatus.INTERRUPTED.value, now, "프로그램이 정상적으로 종료되지 않았습니다.", *active_statuses),
            )
            recovered_count = cursor.rowcount
        return recovered_count

    def latest_resumable_run_id(self) -> str | None:
        with self._db() as connection:
            return find_resumable_run_id(connection)

    def decline_resume(self, run_id: str) -> None:
        with self._db() as connection:
            cancel_unfinished_jobs(connection, run_id, now := utc_now_text())
            self._refresh_counts(connection, run_id)
            connection.execute(
                "UPDATE runs SET status = ?, updated_at = ?, finished_at = ?, last_message = ? WHERE id = ?",
                (RunStatus.STOPPED.value, now, now, "사용자가 이전 작업 재개를 선택하지 않았습니다.", run_id),
            )

    def load_config(self, run_id: str) -> RunConfig:
        with self._db() as connection:
            row = connection.execute("SELECT config_json FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"실행을 찾을 수 없습니다: {run_id}")
        return run_config_from_json(str(row["config_json"]))

    def pending_jobs(self, run_id: str) -> list[StoredJob]:
        with self._db() as connection:
            rows = connection.execute(
                "SELECT id, run_id, sequence, keyword_index, keyword_original, keyword_normalized, domain, query, status, attempts, max_attempts, next_attempt_at, screenshot_path, metadata_path, page_state, last_error_type, last_error_message "
                "FROM jobs WHERE run_id = ? AND status IN ('pending', 'running', 'retry_wait') ORDER BY sequence",
                (run_id,),
            ).fetchall()
        return [
            StoredJob(
                id=str(row["id"]), run_id=str(row["run_id"]), sequence=int(row["sequence"]), keyword_index=int(row["keyword_index"]),
                keyword_original=str(row["keyword_original"]), keyword_normalized=str(row["keyword_normalized"]), domain=str(row["domain"]), query=str(row["query"]),
                status=StoredJobStatus(str(row["status"])), attempts=int(row["attempts"]), max_attempts=int(row["max_attempts"]),
                next_attempt_at=row["next_attempt_at"], screenshot_path=str(row["screenshot_path"]), metadata_path=str(row["metadata_path"]),
                page_state=str(row["page_state"]), last_error_type=str(row["last_error_type"]), last_error_message=str(row["last_error_message"]),
            )
            for row in rows
        ]

    def set_run_status(self, run_id: str, status: RunStatus, message: str = "") -> None:
        now = utc_now_text()
        with self._db() as connection:
            if status == RunStatus.RUNNING:
                connection.execute(
                    "UPDATE runs SET status = ?, started_at = COALESCE(started_at, ?), finished_at = NULL, updated_at = ?, last_message = ? WHERE id = ?",
                    (status.value, now, now, message, run_id),
                )
            else:
                connection.execute(
                    "UPDATE runs SET status = ?, updated_at = ?, last_message = ? WHERE id = ?",
                    (status.value, now, message, run_id),
                )

    def mark_job_running(self, job_id: str) -> None:
        now = utc_now_text()
        with self._db() as connection:
            connection.execute(
                "UPDATE jobs SET status = ?, attempts = attempts + 1, started_at = ?, updated_at = ?, last_error_type = '', last_error_message = '' WHERE id = ?",
                (StoredJobStatus.RUNNING.value, now, now, job_id),
            )

    def mark_job_success(self, job_id: str, result: CaptureResult) -> None:
        run_id = self._run_id_for_job(job_id)
        status = StoredJobStatus.NO_RESULTS_CAPTURED if result.state == PageState.NO_RESULTS else StoredJobStatus.SUCCESS
        now = utc_now_text()
        with self._db() as connection:
            connection.execute(
                "UPDATE jobs SET status = ?, updated_at = ?, finished_at = ?, captured_at = ?, page_state = ?, search_url = ?, screenshot_path = ?, metadata_path = ?, capture_selector = ?, capture_x = ?, capture_y = ?, capture_width = ?, capture_height = ?, png_width = ?, png_height = ?, sha256 = ? WHERE id = ?",
                (status.value, now, now, result.captured_at, result.state.value, result.search_url, str(result.path), str(result.metadata_path or ""), result.rect.selector, result.rect.x, result.rect.y, result.rect.width, result.rect.height, result.png_width, result.png_height, result.sha256, job_id),
            )
            self._refresh_counts(connection, run_id)

    def mark_job_failed(self, job_id: str, error: Exception) -> None:
        run_id = self._run_id_for_job(job_id)
        now = utc_now_text()
        with self._db() as connection:
            connection.execute(
                "UPDATE jobs SET status = ?, updated_at = ?, finished_at = ?, last_error_type = ?, last_error_message = ? WHERE id = ?",
                (StoredJobStatus.FAILED.value, now, now, type(error).__name__, str(error), job_id),
            )
            self._refresh_counts(connection, run_id)

    def finish_run(self, run_id: str, *, stopped: bool = False) -> None:
        now = utc_now_text()
        status = RunStatus.STOPPED if stopped else RunStatus.COMPLETED
        message = "사용자가 작업을 중단했습니다." if stopped else "모든 작업을 처리했습니다."
        with self._db() as connection:
            self._refresh_counts(connection, run_id)
            connection.execute(
                "UPDATE runs SET status = ?, updated_at = ?, finished_at = ?, last_message = ? WHERE id = ?",
                (status.value, now, now, message, run_id),
            )

    def _run_id_for_job(self, job_id: str) -> str:
        with self._db() as connection:
            row = connection.execute("SELECT run_id FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(f"작업을 찾을 수 없습니다: {job_id}")
        return str(row["run_id"])

    @staticmethod
    def _refresh_counts(connection: sqlite3.Connection, run_id: str) -> None:
        row = connection.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN status IN ('success', 'no_results_captured', 'failed', 'cancelled', 'skipped_existing') THEN 1 ELSE 0 END) AS completed, "
            "SUM(CASE WHEN status IN ('success', 'no_results_captured', 'skipped_existing') THEN 1 ELSE 0 END) AS succeeded, "
            "SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed, "
            "SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled "
            "FROM jobs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return
        connection.execute(
            "UPDATE runs SET total_jobs = ?, completed_jobs = ?, succeeded_jobs = ?, failed_jobs = ?, cancelled_jobs = ?, updated_at = ? WHERE id = ?",
            (int(row["total"] or 0), int(row["completed"] or 0), int(row["succeeded"] or 0), int(row["failed"] or 0), int(row["cancelled"] or 0), utc_now_text(), run_id),
        )

    def run_counts(self, run_id: str) -> tuple[int, int, int, int]:
        with self._db() as connection:
            row = connection.execute(
                """
                SELECT
                    total_jobs,
                    completed_jobs,
                    succeeded_jobs,
                    failed_jobs
                FROM runs
                WHERE id = ?
                """,
                (run_id,),
            ).fetchone()

        if row is None:
            raise KeyError(f"실행을 찾을 수 없습니다: {run_id}")

        return (
            int(row["total_jobs"]),
            int(row["completed_jobs"]),
            int(row["succeeded_jobs"]),
            int(row["failed_jobs"]),
        )

    def reset_job_pending(self, job_id: str) -> None:
        now = utc_now_text()

        with self._db() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET
                    status = ?,
                    started_at = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    StoredJobStatus.PENDING.value,
                    now,
                    job_id,
                ),
            )

    def retry_failed_jobs(self, run_id: str) -> int:
        now = utc_now_text()
        with self._db() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = ?, started_at = NULL, finished_at = NULL,
                    updated_at = ?, last_error_type = '', last_error_message = ''
                WHERE run_id = ? AND status = ?
                """,
                (
                    StoredJobStatus.PENDING.value,
                    now,
                    run_id,
                    StoredJobStatus.FAILED.value,
                ),
            )
            retried = cursor.rowcount
            self._refresh_counts(connection, run_id)
            connection.execute(
                "UPDATE runs SET status = ?, finished_at = NULL, updated_at = ?, last_message = ? WHERE id = ?",
                (
                    RunStatus.CREATED.value,
                    now,
                    "실패한 작업을 다시 실행합니다.",
                    run_id,
                ),
            )
        return retried

    def job_display_rows(self, run_id: str) -> list[tuple[int, str, str, str]]:
        with self._db() as connection:
            rows = connection.execute(
                "SELECT sequence, status, screenshot_path, last_error_message "
                "FROM jobs WHERE run_id = ? ORDER BY sequence",
                (run_id,),
            ).fetchall()

        return [
            (
                int(row["sequence"]),
                str(row["status"]),
                str(row["screenshot_path"] or ""),
                str(row["last_error_message"] or ""),
            )
            for row in rows
        ]
