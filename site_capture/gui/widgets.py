from __future__ import annotations

from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget


def create_card(title: str, help_text: str = "") -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 18, 20, 20)
    layout.setSpacing(12)
    title_label = QLabel(title)
    title_label.setObjectName("cardTitle")
    layout.addWidget(title_label)
    if help_text:
        label = QLabel(help_text)
        label.setObjectName("helpText")
        label.setWordWrap(True)
        layout.addWidget(label)
    return card, layout


def tab_layout(tab: QWidget) -> QVBoxLayout:
    layout = QVBoxLayout(tab)
    layout.setContentsMargins(24, 20, 24, 24)
    layout.setSpacing(16)
    return layout
