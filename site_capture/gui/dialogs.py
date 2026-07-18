from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFrame, QLabel, QVBoxLayout, QWidget


def show_validation_message(parent: QWidget, message: str) -> None:
    dialog = QDialog(parent)
    dialog.setObjectName("validationDialog")
    dialog.setAccessibleName("입력 안내")
    dialog.setWindowTitle("입력 내용을 확인하세요")
    dialog.setModal(True)
    dialog.setMinimumWidth(400)
    dialog.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(28, 24, 28, 24)
    layout.setSpacing(16)

    title = QLabel("입력 내용을 확인하세요")
    title.setObjectName("validationDialogTitle")
    layout.addWidget(title)

    message_panel = QFrame()
    message_panel.setObjectName("validationMessagePanel")
    message_layout = QVBoxLayout(message_panel)
    message_layout.setContentsMargins(16, 14, 16, 14)

    message_label = QLabel(message)
    message_label.setObjectName("validationDialogMessage")
    message_label.setWordWrap(True)
    message_layout.addWidget(message_label)
    layout.addWidget(message_panel)

    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
    confirm_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
    confirm_button.setObjectName("primaryButton")
    confirm_button.setText("확인")
    buttons.accepted.connect(dialog.accept)
    layout.addWidget(buttons, alignment=Qt.AlignmentFlag.AlignRight)

    dialog.exec()
