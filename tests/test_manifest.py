from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from site_capture.errors import Stage1Error
from site_capture.execution import BatchControl, RunnerCallbacks
from site_capture.manifest import ResultManifest
from site_capture.models import CaptureRect, CaptureResult, PageState, RunConfig
from site_capture.stage2_runner import Stage2Runner


class ResultManifestTests(unittest.TestCase):
    def _result(self, state: PageState = PageState.SEARCH_RESULTS) -> CaptureResult:
        return CaptureResult(
            keyword="테스트키워드",
            domain="example.com",
            query="site:example.com 테스트키워드",
            state=state,
            path=Path("C:/captures/2026-07-17_테스트키워드.png"),
            search_url="https://www.google.com/search?q=테스트키워드",
            captured_at="2026-07-17T15:20:11+09:00",
            rect=CaptureRect(0, 0, 802, 1115, "#search"),
            png_width=802,
            png_height=1115,
            sha256="a" * 64,
        )

    def test_append_success_and_failure_uses_one_daily_csv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = ResultManifest(Path(directory))
            csv_path = manifest.append_success(
                sequence=1,
                keyword="테스트키워드",
                domain="example.com",
                query="site:example.com 테스트키워드",
                result=self._result(),
            )
            manifest.append_success(
                sequence=2,
                keyword="테스트키워드",
                domain="public.example.com",
                query="site:public.example.com 테스트키워드",
                result=self._result(PageState.NO_RESULTS),
            )
            with patch("site_capture.manifest.datetime") as datetime_type:
                datetime_type.now.return_value = datetime.fromisoformat(
                    "2026-07-17T15:20:11+09:00"
                )
                manifest.append_failure(
                    sequence=3,
                    keyword="테스트키워드",
                    domain="example.com",
                    query="site:example.com 테스트키워드",
                    error=ValueError("capture failed"),
                )

            self.assertEqual(csv_path, Path(directory) / "2026-07-17" / "results.csv")
            with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))

            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0]["status"], "success")
            self.assertEqual(rows[1]["status"], "no_results_captured")
            self.assertEqual(rows[2]["status"], "failed")
            self.assertEqual(rows[2]["error_type"], "ValueError")
            self.assertEqual(rows[2]["error_message"], "capture failed")
            self.assertEqual(rows[0]["capture_selector"], "#search")
            self.assertEqual(rows[0]["png_width"], "802")

    def _config(self, directory: str) -> RunConfig:
        root = Path(directory)
        return RunConfig(
            keywords=("테스트키워드",),
            domains=("example.com",),
            output_root=root / "output",
            profile_dir=root / "profile",
            search_mode="direct-url",
            delay_between_jobs_seconds=0,
        )

    def _browser_patches(self) -> tuple[MagicMock, MagicMock, MagicMock, MagicMock]:
        session = MagicMock()
        session.get_page_target.return_value = {"webSocketDebuggerUrl": "ws://test"}
        cdp = MagicMock()
        page = MagicMock()
        chrome = MagicMock(return_value=session)
        return session, cdp, page, chrome

    def test_runner_records_success_in_results_csv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session, cdp, page, chrome = self._browser_patches()
            config = self._config(directory)
            result = self._result()
            runner = Stage2Runner(config, BatchControl(), RunnerCallbacks())
            with patch("site_capture.stage2_runner.locate_chrome", return_value=Path("chrome.exe")), patch(
                "site_capture.stage2_runner.ChromeSession", chrome
            ), patch("site_capture.stage2_runner.CdpConnection", return_value=cdp), patch(
                "site_capture.stage2_runner.GoogleSearchPage", return_value=page
            ), patch.object(runner, "_execute_one_with_retry", return_value=result):
                summary = runner.run()

            csv_path = config.output_root / "2026-07-17" / "results.csv"
            self.assertEqual(summary.succeeded, 1)
            self.assertEqual(csv_path.read_text(encoding="utf-8-sig").count("success"), 1)
            session.start.assert_called_once()

    def test_runner_records_failure_in_results_csv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session, cdp, page, chrome = self._browser_patches()
            config = self._config(directory)
            runner = Stage2Runner(config, BatchControl(), RunnerCallbacks())
            with patch("site_capture.stage2_runner.locate_chrome", return_value=Path("chrome.exe")), patch(
                "site_capture.stage2_runner.ChromeSession", chrome
            ), patch("site_capture.stage2_runner.CdpConnection", return_value=cdp), patch(
                "site_capture.stage2_runner.GoogleSearchPage", return_value=page
            ), patch.object(
                runner,
                "_execute_one_with_retry",
                side_effect=Stage1Error("capture failed"),
            ):
                summary = runner.run()

            csv_paths = list(config.output_root.rglob("results.csv"))
            self.assertEqual(summary.failed, 1)
            self.assertEqual(len(csv_paths), 1)
            self.assertIn("capture failed", csv_paths[0].read_text(encoding="utf-8-sig"))


if __name__ == "__main__":
    unittest.main()
