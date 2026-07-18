from __future__ import annotations

from pathlib import Path
from typing import Final

from PySide6.QtGui import QFontDatabase


_A2Z_REGULAR_FONT_PATH: Final = (
    Path(__file__).resolve().parents[1]
    / "resources"
    / "fonts"
    / "a2z"
    / "에이투지체-4Regular.ttf"
)


class A2zFontLoadError(RuntimeError):
    pass


def load_a2z_font() -> str:
    font_id = QFontDatabase.addApplicationFont(str(_A2Z_REGULAR_FONT_PATH))
    if font_id == -1:
        raise A2zFontLoadError(
            f"에이투지체 글꼴을 불러올 수 없습니다: {_A2Z_REGULAR_FONT_PATH}"
        )

    families = QFontDatabase.applicationFontFamilies(font_id)
    if not families:
        raise A2zFontLoadError("에이투지체 글꼴 패밀리 이름을 찾을 수 없습니다.")
    return families[0]
