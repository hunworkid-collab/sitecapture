from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from site_capture.gui.control import BatchControl
from site_capture.gui.events import Stage2Summary
from site_capture.gui.worker import BatchWorker
from site_capture.models import RunConfig
from site_capture.single_instance import SingleInstanceLock


@unittest.skipUnless(sys.platform == "win32", "Windows mutex required")
class SingleInstanceLockTests(unittest.TestCase):
    def test_second_instance_cannot_acquire_the_same_mutex(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = SingleInstanceLock(Path(directory))
            second = SingleInstanceLock(Path(directory))
            try:
                self.assertTrue(first.acquire())
                self.assertFalse(second.acquire())
                first.close()
                self.assertTrue(second.acquire())
            finally:
                first.close()
                second.close()


class WorkerRecoveryTests(unittest.TestCase):
    def test_worker_does_not_recover_runs_while_another_worker_may_be_active(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = RunConfig(
                keywords=("keyword",),
                domains=("example.com",),
                output_root=Path(directory) / "output",
                profile_dir=Path(directory) / "profile",
            )
            repository = MagicMock()
            repository.create_run.return_value = "run-id"
            runner = MagicMock()
            runner.run.return_value = Stage2Summary(total=1)

            with patch(
                "site_capture.gui.worker.application_data_directory",
                return_value=Path(directory),
            ), patch(
                "site_capture.gui.worker.JobRepository",
                return_value=repository,
            ), patch(
                "site_capture.gui.worker.Stage2Runner",
                return_value=runner,
            ):
                BatchWorker(config, BatchControl()).run()

            repository.recover_interrupted_runs.assert_not_called()


if __name__ == "__main__":
    unittest.main()
