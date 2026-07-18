from __future__ import annotations

from typing import Callable

from .gui.control import BatchControl
from .gui.events import RunnerCallbacks
from .models import RunConfig, RunSummary
from .paths import display_path
from .stage2_runner import Stage2Runner


class Stage1Runner:
    def __init__(
        self,
        config: RunConfig,
        *,
        log: Callable[[str], None] = print,
    ) -> None:
        self.config = config
        self.log = log

    def run(self) -> RunSummary:
        control = BatchControl()
        callbacks = RunnerCallbacks(
            log=self.log,
            user_action_required=lambda _state, label: self._confirm_user_action(control, label),
        )
        self.log(f"프로필: {display_path(self.config.profile_dir)}")
        self.log(f"출력 루트: {display_path(self.config.output_root)}")
        summary = Stage2Runner(self.config, control, callbacks).run()
        return RunSummary(
            total=summary.total,
            succeeded=summary.succeeded,
            failed=summary.failed,
            results=summary.results,
            errors=summary.errors,
        )

    def _confirm_user_action(self, control: BatchControl, label: str) -> None:
        self.log(f"사용자 조치 필요: {label}")
        input("Chrome 화면에서 직접 처리한 뒤 Enter를 누르세요: ")
        control.confirm_user_action()
