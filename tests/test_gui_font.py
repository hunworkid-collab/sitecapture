from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication

from site_capture.gui.font import load_a2z_font


class ApplicationFontTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_load_a2z_font_registers_a2z_family(self) -> None:
        family = load_a2z_font()

        self.assertEqual(family, "A2Z")


if __name__ == "__main__":
    unittest.main()
