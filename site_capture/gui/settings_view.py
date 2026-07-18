from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..models import RunConfig
from ..paths import display_path, resolve_display_path
from .widgets import create_card, tab_layout


class SettingsView(QWidget):
    browse_output_requested = Signal()
    open_output_requested = Signal()

    def __init__(self, default_output: str) -> None:
        super().__init__()
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        self.settings_scroll_area = QScrollArea()
        self.settings_scroll_area.setWidgetResizable(True)
        self.settings_scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        root = tab_layout(content)
        output_card, output_layout = create_card(
            "저장 위치",
            "캡처 이미지와 작업 기록을 저장할 기본 폴더입니다.",
        )
        output_form = QFormLayout()
        output_form.setSpacing(12)
        output_row = QWidget()
        output_layout_row = QHBoxLayout(output_row)
        output_layout_row.setContentsMargins(0, 0, 0, 0)
        self.output_edit = QLineEdit(default_output)
        self.browse_output_button = QPushButton("찾기")
        self.open_output_button = QPushButton("결과 폴더 열기")
        output_layout_row.addWidget(self.output_edit)
        output_layout_row.addWidget(self.browse_output_button)
        output_layout_row.addWidget(self.open_output_button)
        output_form.addRow("저장 폴더", output_row)
        output_layout.addLayout(output_form)
        root.addWidget(output_card)
        self.advanced_settings = QGroupBox("고급 설정")
        self.advanced_settings.setObjectName("advancedSettings")
        self.advanced_settings.setCheckable(True)
        self.advanced_settings.setChecked(True)
        advanced_layout = QVBoxLayout(self.advanced_settings)
        self.advanced_content = QWidget()
        advanced_form = QFormLayout(self.advanced_content)
        advanced_form.setSpacing(12)
        self.search_mode_combo = QComboBox()
        self.search_mode_combo.setMaximumWidth(420)
        self.search_mode_combo.addItem("검색창에 입력", "search-box")
        self.search_mode_combo.addItem("바로 검색하기", "direct-url")
        advanced_form.addRow("검색 방식", self.search_mode_combo)
        self.exact_phrase_check = QCheckBox("키워드 정확히 일치")
        advanced_form.addRow("", self.exact_phrase_check)
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 3600)
        self.delay_spin.setValue(5)
        self.delay_spin.setSuffix(" 초")
        self.delay_spin.setMaximumWidth(240)
        advanced_form.addRow("작업 간 대기", self.delay_spin)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(5, 300)
        self.timeout_spin.setValue(30)
        self.timeout_spin.setSuffix(" 초")
        self.timeout_spin.setMaximumWidth(240)
        advanced_form.addRow("최대 대기 시간", self.timeout_spin)
        self.overwrite_check = QCheckBox("동일 파일 덮어쓰기")
        self.metadata_check = QCheckBox("작업 정보 파일 저장")
        self.metadata_check.setChecked(True)
        advanced_form.addRow("", self.overwrite_check)
        advanced_form.addRow("", self.metadata_check)
        viewport = QWidget()
        viewport_row = QHBoxLayout(viewport)
        viewport_row.setContentsMargins(0, 0, 0, 0)
        self.viewport_width_spin = QSpinBox()
        self.viewport_width_spin.setRange(640, 4096)
        self.viewport_width_spin.setValue(1440)
        self.viewport_width_spin.setMaximumWidth(220)
        self.viewport_height_spin = QSpinBox()
        self.viewport_height_spin.setRange(480, 4096)
        self.viewport_height_spin.setValue(1000)
        self.viewport_height_spin.setMaximumWidth(220)
        viewport_row.addWidget(QLabel("가로"))
        viewport_row.addWidget(self.viewport_width_spin)
        viewport_row.addWidget(QLabel("세로"))
        viewport_row.addWidget(self.viewport_height_spin)
        viewport_row.addStretch(1)
        advanced_form.addRow("캡처 크기", viewport)
        advanced_layout.addWidget(self.advanced_content)
        self.advanced_settings.toggled.connect(self.advanced_content.setVisible)
        root.addWidget(self.advanced_settings)
        root.addStretch(1)
        self.settings_scroll_area.setWidget(content)
        root_layout.addWidget(self.settings_scroll_area)
        self.browse_output_button.clicked.connect(self.browse_output_requested)
        self.open_output_button.clicked.connect(self.open_output_requested)

    def build_run_config(
        self,
        keywords: tuple[str, ...],
        domains: tuple[str, ...],
        profile_dir: Path,
    ) -> RunConfig:
        output_root = self.output_directory()
        if output_root.exists() and not output_root.is_dir():
            raise ValueError(f"출력 경로가 폴더가 아닙니다: {output_root}")
        output_root.mkdir(parents=True, exist_ok=True)
        profile_dir.mkdir(parents=True, exist_ok=True)
        return RunConfig(
            keywords,
            domains,
            output_root,
            profile_dir,
            search_mode=str(self.search_mode_combo.currentData()),
            exact_phrase=self.exact_phrase_check.isChecked(),
            viewport_width=self.viewport_width_spin.value(),
            viewport_height=self.viewport_height_spin.value(),
            timeout_seconds=self.timeout_spin.value(),
            delay_between_jobs_seconds=self.delay_spin.value(),
            overwrite=self.overwrite_check.isChecked(),
            keep_chrome_open=False,
            write_metadata=self.metadata_check.isChecked(),
            headless=False,
        )

    def apply_config(self, config: RunConfig) -> None:
        self.exact_phrase_check.setChecked(config.exact_phrase)
        search_mode_index = self.search_mode_combo.findData(config.search_mode)
        if search_mode_index >= 0:
            self.search_mode_combo.setCurrentIndex(search_mode_index)
        self.delay_spin.setValue(round(config.delay_between_jobs_seconds))
        self.timeout_spin.setValue(round(config.timeout_seconds))
        self.viewport_width_spin.setValue(config.viewport_width)
        self.viewport_height_spin.setValue(config.viewport_height)
        self.output_edit.setText(display_path(config.output_root))
        self.overwrite_check.setChecked(config.overwrite)
        self.metadata_check.setChecked(config.write_metadata)

    def output_directory(self) -> Path:
        return resolve_display_path(self.output_edit.text())

    def set_output_directory(self, path: Path) -> None:
        self.output_edit.setText(display_path(path))

    def set_input_enabled(self, enabled: bool) -> None:
        for widget in (
            self.search_mode_combo,
            self.exact_phrase_check,
            self.delay_spin,
            self.timeout_spin,
            self.viewport_width_spin,
            self.viewport_height_spin,
            self.output_edit,
            self.browse_output_button,
            self.overwrite_check,
            self.metadata_check,
        ):
            widget.setEnabled(enabled)
        self.open_output_button.setEnabled(True)
