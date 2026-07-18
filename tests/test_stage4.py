from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import gui_main


class ExceptionLogTests(unittest.TestCase):
    def test_unhandled_exception_is_written_to_application_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous_hook = sys.excepthook
            try:
                with patch(
                    "PySide6.QtWidgets.QMessageBox.critical"
                ) as critical, patch.dict(
                    gui_main.__dict__,
                    {"application_data_directory": lambda: Path(directory)},
                ):
                    gui_main.install_exception_handler()
                    try:
                        raise RuntimeError("stage4 failure")
                    except RuntimeError as error:
                        sys.excepthook(type(error), error, error.__traceback__)
            finally:
                sys.excepthook = previous_hook

            logs = list((Path(directory) / "logs").glob("error_*.log"))

            self.assertEqual(len(logs), 1)
            self.assertIn("RuntimeError: stage4 failure", logs[0].read_text(encoding="utf-8"))
            critical.assert_called_once()


if __name__ == "__main__":
    unittest.main()
