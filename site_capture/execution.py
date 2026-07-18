from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from .errors import RunCancelled
from .models import CaptureResult


class BatchState(str, Enum):
    IDLE = "idle"
    PREPARING = "preparing"
    RUNNING = "running"
    PAUSED = "paused"
    USER_ACTION_REQUIRED = "user_action_required"
    STOPPING = "stopping"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class JobUpdate:
    index: int
    total: int
    keyword: str
    domain: str
    query: str
    status: JobStatus
    message: str = ""
    path: Path | None = None
    page_state: str = ""


@dataclass(slots=True)
class Stage2Summary:
    total: int
    completed: int = 0
    succeeded: int = 0
    failed: int = 0
    cancelled: int = 0
    remaining: int = 0
    stopped: bool = False
    results: list[CaptureResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _noop(*_args: object, **_kwargs: object) -> None:
    return None


@dataclass(slots=True)
class RunnerCallbacks:
    log: Callable[[str], None] = _noop
    state_changed: Callable[[str, str], None] = _noop
    job_changed: Callable[[JobUpdate], None] = _noop
    progress_changed: Callable[[int, int, int, int], None] = _noop
    user_action_required: Callable[[str, str], None] = _noop


class BatchControl:
    def __init__(self) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._paused = False
        self._stop_requested = False
        self._user_action_event = threading.Event()

    @property
    def pause_requested(self) -> bool:
        with self._condition:
            return self._paused

    @property
    def stop_requested(self) -> bool:
        with self._condition:
            return self._stop_requested

    def request_pause(self) -> None:
        with self._condition:
            if self._stop_requested:
                return
            self._paused = True
            self._condition.notify_all()

    def request_resume(self) -> None:
        with self._condition:
            self._paused = False
            self._condition.notify_all()

    def request_stop(self) -> None:
        with self._condition:
            self._stop_requested = True
            self._paused = False
            self._condition.notify_all()
        self._user_action_event.set()

    def checkpoint(self) -> bool:
        waited = False
        with self._condition:
            while self._paused and not self._stop_requested:
                waited = True
                self._condition.wait(timeout=0.2)
            if self._stop_requested:
                raise RunCancelled("사용자가 작업 중단을 요청했습니다.")
        return waited

    def interruptible_sleep(self, seconds: float) -> None:
        if seconds <= 0:
            self.checkpoint()
            return
        deadline = time.monotonic() + seconds
        while True:
            self.checkpoint()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.2, remaining))

    def prepare_user_action(self) -> None:
        self._user_action_event.clear()
        self.checkpoint()

    def confirm_user_action(self) -> None:
        self._user_action_event.set()

    def wait_for_user_action(self) -> None:
        while not self._user_action_event.wait(timeout=0.2):
            self.checkpoint()
        self.checkpoint()
