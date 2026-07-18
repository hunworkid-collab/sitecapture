from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from site_capture.models import CaptureRect, CaptureResult, PageState, RunConfig
from site_capture.persistence import JobRepository
from site_capture.persistence.models import (
    StoredJobStatus,
    RunStatus,
    new_id,
    run_config_from_json,
    run_config_to_json,
)
from site_capture.query import build_search_jobs
from site_capture.persistence.schema import SCHEMA_SQL, SCHEMA_VERSION


class PersistenceSchemaTests(unittest.TestCase):
    def test_schema_creates_expected_tables_and_indexes(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.executescript(SCHEMA_SQL)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        self.assertEqual(tables, {"app_meta", "runs", "jobs"})
        self.assertIn("idx_runs_status_updated", indexes)
        self.assertIn("idx_jobs_run_status_sequence", indexes)
        self.assertNotIn("idx_jobs_retry_due", indexes)
        self.assertEqual(SCHEMA_VERSION, 2)
        connection.close()

    def test_repository_migrates_retry_wait_job_without_events_table(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "jobs.db"
            connection = sqlite3.connect(db_path)
            connection.executescript(
                "CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL); "
                "CREATE TABLE runs ("
                "id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, "
                "config_json TEXT NOT NULL, output_root TEXT NOT NULL, profile_dir TEXT NOT NULL, "
                "total_jobs INTEGER NOT NULL, completed_jobs INTEGER NOT NULL DEFAULT 0, "
                "succeeded_jobs INTEGER NOT NULL DEFAULT 0, failed_jobs INTEGER NOT NULL DEFAULT 0, "
                "cancelled_jobs INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
                "started_at TEXT, finished_at TEXT, last_message TEXT NOT NULL DEFAULT ''); "
                "CREATE TABLE jobs ("
                "id TEXT PRIMARY KEY, run_id TEXT NOT NULL, sequence INTEGER NOT NULL, keyword_index INTEGER NOT NULL, "
                "keyword_original TEXT NOT NULL, keyword_normalized TEXT NOT NULL, domain TEXT NOT NULL, query TEXT NOT NULL, "
                "status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 2, "
                "retryable INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, started_at TEXT, "
                "finished_at TEXT, next_attempt_at TEXT, captured_at TEXT, page_state TEXT NOT NULL DEFAULT '', "
                "search_url TEXT NOT NULL DEFAULT '', screenshot_path TEXT NOT NULL DEFAULT '', metadata_path TEXT NOT NULL DEFAULT '', "
                "capture_selector TEXT NOT NULL DEFAULT '', capture_x REAL, capture_y REAL, capture_width REAL, capture_height REAL, "
                "png_width INTEGER, png_height INTEGER, sha256 TEXT NOT NULL DEFAULT '', last_error_type TEXT NOT NULL DEFAULT '', "
                "last_error_message TEXT NOT NULL DEFAULT ''); "
                "CREATE TABLE events ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, job_id TEXT, "
                "level TEXT NOT NULL, event_type TEXT NOT NULL, message TEXT NOT NULL, "
                "data_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);"
            )
            connection.execute("INSERT INTO app_meta (key, value) VALUES ('schema_version', '1')")
            connection.execute(
                "INSERT INTO runs (id, status, config_json, output_root, profile_dir, total_jobs, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("run-1", "interrupted", "{}", "out", "profile", 1, "now", "now"),
            )
            connection.execute(
                "INSERT INTO jobs (id, run_id, sequence, keyword_index, keyword_original, keyword_normalized, domain, query, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("job-1", "run-1", 1, 1, "keyword", "keyword", "example.com", "site:example.com keyword", "retry_wait", "now", "now"),
            )
            connection.commit()
            connection.close()

            repository = JobRepository(db_path)

            self.assertEqual(
                [job.status for job in repository.pending_jobs("run-1")],
                [StoredJobStatus.PENDING],
            )
            migrated = sqlite3.connect(db_path)
            try:
                tables = {
                    row[0]
                    for row in migrated.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                columns = {
                    row[1]
                    for row in migrated.execute("PRAGMA table_info(jobs)")
                }
            finally:
                migrated.close()
            self.assertNotIn("events", tables)
            self.assertNotIn("retryable", columns)
            self.assertNotIn("next_attempt_at", columns)

    def test_schema_rejects_duplicate_job_sequence_per_run(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA_SQL)
        connection.execute(
            "INSERT INTO runs (id, status, config_json, output_root, profile_dir, total_jobs, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("run-1", "created", "{}", "out", "profile", 1, "now", "now"),
        )
        values = (
            "job-1",
            "run-1",
            1,
            1,
            "keyword",
            "keyword",
            "example.com",
            "site:example.com keyword",
            "pending",
            "now",
            "now",
        )
        columns = "id, run_id, sequence, keyword_index, keyword_original, keyword_normalized, domain, query, status, created_at, updated_at"
        connection.execute(f"INSERT INTO jobs ({columns}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                f"INSERT INTO jobs ({columns}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("job-2", *values[1:]),
            )
        connection.close()


class PersistenceModelTests(unittest.TestCase):
    def test_run_config_json_round_trip_preserves_paths_and_options(self) -> None:
        config = RunConfig(
            keywords=("테스트키워드", "추가 검색어"),
            domains=("example.com",),
            output_root=Path(tempfile.gettempdir()) / "output",
            profile_dir=Path(tempfile.gettempdir()) / "profile",
            chrome_path=Path("chrome.exe"),
            search_mode="direct-url",
            exact_phrase=True,
            viewport_width=1600,
            viewport_height=900,
            timeout_seconds=45.0,
            stabilization_interval_seconds=0.25,
            stabilization_required_count=4,
            delay_between_jobs_seconds=7.0,
            overwrite=True,
            keep_chrome_open=True,
            write_metadata=False,
            headless=True,
        )
        restored = run_config_from_json(run_config_to_json(config))
        self.assertEqual(restored, config)
        self.assertIn('"schema_version": 2', run_config_to_json(config))

    def test_build_search_jobs_normalizes_and_orders_keyword_domain_pairs(self) -> None:
        config = RunConfig(
            keywords=("  테스트   검색어  ", "추가 검색어"),
            domains=("example.com", "public.example.com"),
            output_root=Path("out"),
            profile_dir=Path("profile"),
        )
        jobs = build_search_jobs(config)
        self.assertEqual([job.sequence for job in jobs], [1, 2, 3, 4])
        self.assertEqual([job.keyword_index for job in jobs], [1, 1, 2, 2])
        self.assertEqual(jobs[0].keyword_normalized, "테스트 검색어")
        self.assertEqual(jobs[1].domain, "public.example.com")

    def test_new_id_has_expected_length(self) -> None:
        self.assertEqual(len(new_id()), 32)


class JobRepositoryTests(unittest.TestCase):
    def _config(self) -> RunConfig:
        return RunConfig(
            keywords=("테스트키워드", "추가 검색어"),
            domains=("example.com",),
            output_root=Path(tempfile.gettempdir()) / "output",
            profile_dir=Path(tempfile.gettempdir()) / "profile",
        )

    def test_create_run_persists_config_and_pending_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = JobRepository(Path(directory) / "jobs.db")
            run_id = repository.create_run(self._config())
            jobs = repository.pending_jobs(run_id)
            self.assertEqual(len(jobs), 2)
            self.assertTrue(all(job.status is StoredJobStatus.PENDING for job in jobs))
            self.assertTrue(all(job.max_attempts == 2 for job in jobs))
            self.assertEqual(repository.load_config(run_id), self._config())
            self.assertEqual(repository.latest_resumable_run_id(), run_id)

    def test_success_and_failure_update_pending_work_and_run_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = JobRepository(Path(directory) / "jobs.db")
            run_id = repository.create_run(self._config())
            jobs = repository.pending_jobs(run_id)
            repository.set_run_status(run_id, RunStatus.RUNNING)
            repository.mark_job_running(jobs[0].id)
            result = CaptureResult(
                keyword=jobs[0].keyword_normalized,
                domain=jobs[0].domain,
                query=jobs[0].query,
                state=PageState.SEARCH_RESULTS,
                path=Path(directory) / "capture.png",
                search_url="https://www.google.com/search?q=test",
                captured_at=datetime.now(timezone.utc).isoformat(),
                rect=CaptureRect(0, 0, 300, 100, "#search"),
                png_width=300,
                png_height=100,
                sha256="a" * 64,
            )
            repository.mark_job_success(jobs[0].id, result)
            repository.mark_job_running(jobs[1].id)
            repository.mark_job_failed(jobs[1].id, ValueError("capture failed"))
            repository.finish_run(run_id)
            connection = sqlite3.connect(Path(directory) / "jobs.db")
            try:
                row = connection.execute(
                    "SELECT status, completed_jobs, succeeded_jobs, failed_jobs FROM runs WHERE id = ?",
                    (run_id,),
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(row, ("completed", 2, 1, 1))
            self.assertIsNone(repository.latest_resumable_run_id())

    def test_recover_interrupted_runs_returns_running_jobs_to_pending(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = JobRepository(Path(directory) / "jobs.db")
            run_id = repository.create_run(self._config())
            job = repository.pending_jobs(run_id)[0]
            repository.set_run_status(run_id, RunStatus.RUNNING)
            repository.mark_job_running(job.id)
            self.assertEqual(repository.recover_interrupted_runs(), 1)
            self.assertEqual(repository.latest_resumable_run_id(), run_id)
            self.assertEqual(
                [item.status for item in repository.pending_jobs(run_id)],
                [StoredJobStatus.PENDING, StoredJobStatus.PENDING],
            )

    def test_recover_interrupted_runs_leaves_inactive_run_jobs_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = JobRepository(Path(directory) / "jobs.db")
            active_run_id = repository.create_run(self._config())
            inactive_run_id = repository.create_run(self._config())
            active_job = repository.pending_jobs(active_run_id)[0]
            inactive_job = repository.pending_jobs(inactive_run_id)[0]
            repository.set_run_status(active_run_id, RunStatus.RUNNING)
            repository.mark_job_running(active_job.id)
            repository.mark_job_running(inactive_job.id)

            repository.recover_interrupted_runs()

            self.assertEqual(
                repository.pending_jobs(active_run_id)[0].status,
                StoredJobStatus.PENDING,
            )
            self.assertEqual(
                repository.pending_jobs(inactive_run_id)[0].status,
                StoredJobStatus.RUNNING,
            )

    def test_run_counts_and_reset_job_pending(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = JobRepository(Path(directory) / "jobs.db")
            run_id = repository.create_run(self._config())
            job = repository.pending_jobs(run_id)[0]
            repository.set_run_status(run_id, RunStatus.RUNNING)
            repository.mark_job_running(job.id)

            self.assertEqual(repository.run_counts(run_id), (2, 0, 0, 0))

            repository.reset_job_pending(job.id)
            pending = repository.pending_jobs(run_id)

            self.assertEqual(repository.run_counts(run_id), (2, 0, 0, 0))
            self.assertEqual(pending[0].status, StoredJobStatus.PENDING)

    def test_retry_failed_jobs_resets_only_failed_jobs(self) -> None:
        config = RunConfig(
            keywords=("test",),
            domains=("example.com",),
            output_root=Path(tempfile.gettempdir()) / "output",
            profile_dir=Path(tempfile.gettempdir()) / "profile",
        )
        with tempfile.TemporaryDirectory() as directory:
            repository = JobRepository(Path(directory) / "jobs.db")
            run_id = repository.create_run(config)
            job = repository.pending_jobs(run_id)[0]
            repository.mark_job_running(job.id)
            repository.mark_job_failed(job.id, ValueError("capture failed"))
            repository.finish_run(run_id)

            retried = repository.retry_failed_jobs(run_id)

            self.assertEqual(retried, 1)
            self.assertEqual(repository.run_counts(run_id), (1, 0, 0, 0))
            self.assertEqual(repository.pending_jobs(run_id)[0].status, StoredJobStatus.PENDING)

    def test_job_display_rows_returns_rows_in_sequence_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = JobRepository(Path(directory) / "jobs.db")
            run_id = repository.create_run(self._config())
            jobs = repository.pending_jobs(run_id)
            repository.mark_job_running(jobs[0].id)
            repository.mark_job_failed(jobs[0].id, ValueError("capture failed"))

            self.assertEqual(
                repository.job_display_rows(run_id),
                [
                    (1, "failed", "", "capture failed"),
                    (2, "pending", "", ""),
                ],
            )

    def test_running_status_clears_previous_finished_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = JobRepository(Path(directory) / "jobs.db")
            run_id = repository.create_run(self._config())
            repository.finish_run(run_id)
            repository.set_run_status(run_id, RunStatus.RUNNING)

            connection = sqlite3.connect(Path(directory) / "jobs.db")
            try:
                row = connection.execute(
                    "SELECT status, finished_at FROM runs WHERE id = ?",
                    (run_id,),
                ).fetchone()
            finally:
                connection.close()

            self.assertEqual(row, ("running", None))


if __name__ == "__main__":
    unittest.main()
