from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from site_capture.models import CaptureRect, CaptureResult, PageState, RunConfig
from site_capture.persistence import JobRepository
from site_capture.persistence.models import (
    RESUMABLE_RUN_STATUSES,
    SUCCESS_JOB_STATUSES,
    StoredJobStatus,
    RunStatus,
    build_job_seeds,
    new_id,
    run_config_from_json,
    run_config_to_json,
)
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
        self.assertEqual(tables, {"app_meta", "runs", "jobs", "events", "sqlite_sequence"})
        self.assertIn("idx_runs_status_updated", indexes)
        self.assertIn("idx_jobs_run_status_sequence", indexes)
        self.assertEqual(SCHEMA_VERSION, 1)
        connection.close()

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
            verbose=True,
        )
        restored = run_config_from_json(run_config_to_json(config))
        self.assertEqual(restored, config)
        self.assertIn('"schema_version": 1', run_config_to_json(config))

    def test_build_job_seeds_normalizes_and_orders_keyword_domain_pairs(self) -> None:
        config = RunConfig(
            keywords=("  테스트   검색어  ", "추가 검색어"),
            domains=("example.com", "public.example.com"),
            output_root=Path("out"),
            profile_dir=Path("profile"),
        )
        seeds = build_job_seeds(config)
        self.assertEqual([seed.sequence for seed in seeds], [1, 2, 3, 4])
        self.assertEqual([seed.keyword_index for seed in seeds], [1, 1, 2, 2])
        self.assertEqual(seeds[0].keyword_normalized, "테스트 검색어")
        self.assertEqual(seeds[1].domain, "public.example.com")
        self.assertEqual(len({seed.id for seed in seeds}), 4)

    def test_status_sets_describe_resume_and_success_policy(self) -> None:
        self.assertIn(RunStatus.INTERRUPTED, RESUMABLE_RUN_STATUSES)
        self.assertIn(RunStatus.STOPPED, RESUMABLE_RUN_STATUSES)
        self.assertIn(StoredJobStatus.NO_RESULTS_CAPTURED, SUCCESS_JOB_STATUSES)
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
