from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from site_capture.models import RunConfig
from site_capture.persistence import JobRepository


class ResumeRepositoryTests(unittest.TestCase):
    def test_decline_resume_cancels_unfinished_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = JobRepository(Path(directory) / "jobs.db")
            run_id = repository.create_run(
                RunConfig(
                    keywords=("keyword",),
                    domains=("example.com",),
                    output_root=Path(directory) / "output",
                    profile_dir=Path(directory) / "profile",
                )
            )

            repository.decline_resume(run_id)

            self.assertIsNone(repository.latest_resumable_run_id())
            self.assertEqual(
                [row[1] for row in repository.job_display_rows(run_id)],
                ["cancelled"],
            )
