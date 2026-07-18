from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from ..models import CaptureResult


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
