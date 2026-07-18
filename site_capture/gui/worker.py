from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, Signal, Slot

from ..models import RunConfig
from ..paths import application_data_directory
from ..persistence import JobRepository
from ..stage2_runner import Stage2Runner
from ..execution import BatchControl, RunnerCallbacks


class BatchWorker(QObject):
    log_emitted = Signal(str)
    run_started = Signal(str)
    state_changed = Signal(str, str)
    job_changed = Signal(object)
    progress_changed = Signal(int, int, int, int)
    user_action_required = Signal(str, str)
    fatal_error = Signal(str)
    finished = Signal(object)

    def __init__(
        self,
        config: RunConfig,
        control: BatchControl,
        resume_run_id: str | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._control = control
        self._resume_run_id = resume_run_id

    @Slot()
    def run(self) -> None:
        summary: object = None
        callbacks = RunnerCallbacks(
            log=self.log_emitted.emit,
            state_changed=self.state_changed.emit,
            job_changed=self.job_changed.emit,
            progress_changed=self.progress_changed.emit,
            user_action_required=self.user_action_required.emit,
        )
        try:
            db_path = application_data_directory() / "data" / "jobs.db"
            repository = JobRepository(db_path)

            if self._resume_run_id:
                run_id = self._resume_run_id
                config = repository.load_config(run_id)
                self.log_emitted.emit(f"이전 작업 재개: {run_id}")
            else:
                config = self._config
                run_id = repository.create_run(config)
                self.log_emitted.emit(f"새 작업 생성: {run_id}")

            self.run_started.emit(run_id)

            summary = Stage2Runner(
                config,
                self._control,
                callbacks,
                repository=repository,
                run_id=run_id,
            ).run()
        except Exception as exc:
            self.fatal_error.emit(f"{exc}\n\n{traceback.format_exc()}")
        finally:
            self.finished.emit(summary)
