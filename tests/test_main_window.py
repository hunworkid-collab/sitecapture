from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QLabel, QToolButton

from site_capture.execution import BatchState, JobStatus, JobUpdate
from site_capture.gui.execution_view import ExecutionView
from site_capture.gui.main_window import MainWindow
from site_capture.models import RunConfig


class MainWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    @patch("site_capture.gui.main_window.QTimer.singleShot")
    def test_capture_estimate_updates_for_keywords_and_domains(self, _: object) -> None:
        window = MainWindow()
        self.addCleanup(window.close)

        window.execution_view.keyword_edit.setPlainText("첫째\n둘째\n셋째")
        window.execution_view.add_domain("example.com")

        self.assertEqual(
            window.execution_view.capture_estimate_label.text(),
            "키워드 3개 × 도메인 1개 = 총 3건 캡처 예정",
        )

    @patch("site_capture.gui.main_window.QTimer.singleShot")
    def test_start_shows_custom_validation_dialog_for_invalid_input(
        self,
        _: object,
    ) -> None:
        window = MainWindow()
        self.addCleanup(window.close)

        with patch(
            "site_capture.gui.main_window.show_validation_message",
        ) as show_validation_message:
            window._start(test_mode=False)

        show_validation_message.assert_called_once()
        self.assertIs(show_validation_message.call_args.args[0], window)

    @patch("site_capture.gui.main_window.QTimer.singleShot")
    def test_saved_file_cell_shows_name_and_full_path_tooltip(self, _: object) -> None:
        window = MainWindow()
        self.addCleanup(window.close)
        path = Path("C:/captures/2026-07-18/20260718_검색어.png")
        config = RunConfig(
            keywords=("검색어",),
            domains=("example.com",),
            output_root=Path("C:/captures"),
            profile_dir=Path("C:/profile"),
        )
        window._prepare_job_table(config)
        window._on_job_changed(
            JobUpdate(
                1,
                1,
                "검색어",
                "example.com",
                "site:example.com 검색어",
                JobStatus.SUCCESS,
                path=path,
            )
        )

        cell = window.results_view.job_table.cellWidget(0, 5)

        self.assertIsNotNone(cell)
        self.assertEqual(cell.toolTip(), str(path))
        self.assertEqual(cell.findChild(QLabel).text(), path.name)
        self.assertIsNotNone(cell.findChild(QToolButton))


class ExecutionViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_input_and_state_are_managed_by_execution_view(self) -> None:
        view = ExecutionView()
        self.addCleanup(view.close)

        view.set_keywords(("첫째", "둘째"))
        view.add_domain("example.com")
        view.set_run_state(BatchState.RUNNING.value, "작업을 실행합니다.", active=True)

        self.assertEqual(view.keywords(), ("첫째", "둘째"))
        self.assertEqual(view.domains(), ("example.com",))
        self.assertEqual(
            view.capture_estimate_label.text(),
            "키워드 2개 × 도메인 1개 = 총 2건 캡처 예정",
        )
        self.assertEqual(view.state_label.text(), "실행 중")
        self.assertTrue(view.pause_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
