from __future__ import annotations

import threading
import time

from ..errors import RunCancelled


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
