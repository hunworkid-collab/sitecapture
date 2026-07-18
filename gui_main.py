from __future__ import annotations

import sys
import traceback
from datetime import datetime
from types import TracebackType

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox

from site_capture.gui.font import load_a2z_font
from site_capture.gui.main_window import MainWindow
from site_capture.paths import application_data_directory


def install_exception_handler() -> None:
    def handle_exception(
        exception_type: type[BaseException],
        exception: BaseException,
        traceback_object: TracebackType | None,
    ) -> None:
        error_text = "".join(
            traceback.format_exception(
                exception_type,
                exception,
                traceback_object,
            )
        )

        try:
            log_directory = application_data_directory() / "logs"
            log_directory.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = log_directory / f"error_{timestamp}.log"
            log_path.write_text(error_text, encoding="utf-8")
            message = (
                "프로그램 오류가 발생했습니다.\n\n"
                f"{exception}\n\n"
                f"오류 로그:\n{log_path}"
            )
        except OSError:
            message = (
                "프로그램 오류가 발생했습니다.\n\n"
                f"{exception}\n\n"
                "오류 로그 파일을 저장하지 못했습니다."
            )

        QMessageBox.critical(None, "프로그램 오류", message)

    sys.excepthook = handle_exception


def main() -> int:
    QCoreApplication.setOrganizationName("SiteCapture")
    QCoreApplication.setApplicationName("SiteCapture")
    app = QApplication(sys.argv)
    app.setFont(QFont(load_a2z_font()))
    app.setStyle("Fusion")
    install_exception_handler()
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
