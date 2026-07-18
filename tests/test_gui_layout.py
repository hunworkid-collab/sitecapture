from __future__ import annotations

import unittest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QHeaderView, QScrollArea

from site_capture.gui.events import JobStatus, JobUpdate, Stage2Summary
from site_capture.gui.main_window import MainWindow


class MainWindowLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_main_window_separates_execution_results_and_settings(self) -> None:
        window = MainWindow()

        self.assertEqual(window.workspace_tabs.count(), 4)
        self.assertEqual(
            [window.workspace_tabs.tabText(index) for index in range(4)],
            ["실행", "결과", "로그", "설정"],
        )
        self.assertEqual(window.help_button.text(), "도움말")
        self.assertTrue(window.help_dialog.isModal())
        self.assertIn("캡처 시작", window.help_actions_label.text())
        self.assertIn("Chrome 확인 완료", window.help_actions_label.text())
        self.assertEqual(window.footer_label.text(), "Copyright © HUNHUN")
        self.assertTrue(
            window.footer_label.alignment() & Qt.AlignmentFlag.AlignHCenter
        )
        self.assertEqual(window.start_button.objectName(), "primaryButton")
        self.assertTrue(window.advanced_settings.isCheckable())
        self.assertTrue(window.advanced_settings.isChecked())
        self.assertFalse(window.advanced_content.isHidden())
        self.assertLessEqual(window.delay_spin.maximumWidth(), 240)
        self.assertLessEqual(window.timeout_spin.maximumWidth(), 240)
        self.assertLessEqual(window.viewport_width_spin.maximumWidth(), 220)
        self.assertLessEqual(window.viewport_height_spin.maximumWidth(), 220)

        window.advanced_settings.setChecked(False)

        self.assertTrue(window.advanced_content.isHidden())

        window.close()

    def test_domain_list_recent_results_and_completion_card(self) -> None:
        window = MainWindow()

        window.domain_edit.setText("Example.COM")
        window._add_domain()
        window.job_table.setRowCount(1)
        window._on_job_changed(
            JobUpdate(
                1,
                1,
                "키워드",
                "example.com",
                "site:example.com 키워드",
                JobStatus.SUCCESS,
            )
        )
        window._on_worker_finished(
            Stage2Summary(total=1, completed=1, succeeded=1)
        )

        self.assertEqual(window.domain_list.count(), 1)
        self.assertEqual(window.domain_list.item(0).text(), "example.com")
        self.assertEqual(window.recent_results_table.rowCount(), 1)
        self.assertFalse(window.completion_card.isHidden())

        window.close()

    def test_text_heavy_sections_wrap_and_scroll(self) -> None:
        window = MainWindow()
        self.addCleanup(window.close)

        self.assertTrue(window.header_subtitle.wordWrap())
        self.assertTrue(window.detail_label.wordWrap())
        self.assertFalse(window.count_label.wordWrap())
        self.assertTrue(window.completion_detail_label.wordWrap())
        self.assertGreaterEqual(window.control_buttons_layout.rowCount(), 2)
        self.assertEqual(window.execution_columns_layout.count(), 2)
        self.assertEqual(window.execution_columns_layout.stretch(0), 1)
        self.assertEqual(window.execution_columns_layout.stretch(1), 1)
        self.assertEqual(
            int(window.execution_columns_layout.itemAt(0).alignment()),
            0,
        )
        self.assertEqual(
            int(window.execution_columns_layout.itemAt(1).alignment()),
            0,
        )
        self.assertEqual(window.size().width(), 1500)
        self.assertEqual(window.size().height(), 1000)
        window.show()
        self.application.processEvents()
        self.assertEqual(
            window.execution_scroll_area.verticalScrollBar().maximum(),
            0,
        )
        self.assertIsInstance(window.settings_scroll_area, QScrollArea)
        self.assertIsInstance(window.results_scroll_area, QScrollArea)

    def test_progress_summary_uses_two_intentional_lines(self) -> None:
        window = MainWindow()
        self.addCleanup(window.close)

        window._on_progress_changed(2, 5, 1, 1)

        self.assertEqual(
            window.count_label.text(),
            "완료 2 / 5 · 성공 1 · 실패 1",
        )

    def test_recent_results_table_fills_remaining_card_height(self) -> None:
        window = MainWindow()
        self.addCleanup(window.close)
        window.job_table.setRowCount(2)

        for update in (
            JobUpdate(1, 1, "고구마", "example.com", "site:example.com 고구마", JobStatus.SUCCESS),
            JobUpdate(2, 2, "감자", "example.com", "site:example.com 감자", JobStatus.SUCCESS),
        ):
            window._on_job_changed(update)

        header = window.recent_results_table.horizontalHeader()
        self.assertTrue(window.recent_results_table.verticalHeader().isHidden())
        self.assertTrue(
            all(
                header.sectionResizeMode(column)
                is QHeaderView.ResizeMode.Stretch
                for column in range(3)
            )
        )
        self.assertEqual(
            window.recent_results_table.item(0, 2).textAlignment(),
            int(Qt.AlignmentFlag.AlignCenter),
        )
        window.show()
        self.application.processEvents()
        self.assertGreater(window.recent_results_table.height(), 200)

    def test_job_results_table_hides_duplicate_row_numbers_and_stays_compact(self) -> None:
        window = MainWindow()
        self.addCleanup(window.close)
        window.job_table.setRowCount(3)
        window._resize_job_table()

        self.assertTrue(window.job_table.verticalHeader().isHidden())
        self.assertLess(window.job_table.maximumHeight(), 180)

    def test_clear_log_button_removes_displayed_messages(self) -> None:
        window = MainWindow()
        self.addCleanup(window.close)
        window._append_log("test message")

        window.clear_log_button.click()

        self.assertEqual(window.log_edit.toPlainText(), "")

    def test_clear_log_button_shares_the_log_description_row(self) -> None:
        window = MainWindow()
        self.addCleanup(window.close)

        self.assertIs(
            window.log_header_layout.itemAt(0).widget(),
            window.log_help_label,
        )
        self.assertIs(
            window.log_header_layout.itemAt(2).widget(),
            window.clear_log_button,
        )
