"""의존성을 최소화한 동기식 CDP WebSocket 클라이언트."""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from typing import Any

import websocket
from websocket import WebSocketException, WebSocketTimeoutException

from .errors import BrowserDisconnectedError, CdpError, CdpTimeoutError


class CdpConnection:
    """페이지 Target 하나에 연결하는 동기식 CDP 클라이언트.

    1단계는 단일 작업 스레드에서 순차 실행하므로 명령 호출을 직렬화한다.
    응답을 기다리는 동안 수신한 이벤트는 작은 큐에 보관한다.
    """

    def __init__(self, websocket_url: str, *, default_timeout: float = 15.0):
        self.websocket_url = websocket_url
        self.default_timeout = default_timeout
        self._ws: websocket.WebSocket | None = None
        self._next_id = 0
        self._lock = threading.RLock()
        self._events: deque[dict[str, Any]] = deque(maxlen=1000)

    @property
    def connected(self) -> bool:
        return self._ws is not None and bool(self._ws.connected)

    def connect(self) -> None:
        if self.connected:
            return
        try:
            # Origin 헤더를 보내지 않아 localhost CDP 연결의 Origin 검사 충돌을 피한다.
            self._ws = websocket.create_connection(
                self.websocket_url,
                timeout=self.default_timeout,
                suppress_origin=True,
                enable_multithread=True,
            )
        except Exception as exc:  # websocket 라이브러리의 다양한 연결 예외를 한곳에서 변환
            raise BrowserDisconnectedError(
                f"CDP WebSocket 연결 실패: {exc}", method="connect"
            ) from exc

    def close(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if not self.connected:
            self.connect()
        assert self._ws is not None

        timeout = self.default_timeout if timeout is None else timeout
        deadline = time.monotonic() + timeout

        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            payload: dict[str, Any] = {"id": request_id, "method": method}
            if params:
                payload["params"] = params

            try:
                self._ws.send(json.dumps(payload, ensure_ascii=False))
            except (WebSocketException, OSError) as exc:
                self.close()
                raise BrowserDisconnectedError(
                    f"CDP 명령 전송 중 연결 종료: {method}: {exc}", method=method
                ) from exc
            except Exception as exc:
                raise CdpError(f"CDP 명령 전송 실패: {exc}", method=method) from exc

            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CdpTimeoutError(
                        f"CDP 응답 제한시간 초과: {method}", method=method
                    )
                self._ws.settimeout(remaining)
                try:
                    raw = self._ws.recv()
                except WebSocketTimeoutException as exc:
                    raise CdpTimeoutError(
                        f"CDP 응답 제한시간 초과: {method}", method=method
                    ) from exc
                except (WebSocketException, OSError) as exc:
                    self.close()
                    raise BrowserDisconnectedError(
                        f"CDP 응답 대기 중 연결 종료: {method}: {exc}", method=method
                    ) from exc
                except Exception as exc:
                    raise CdpError(f"CDP 수신 실패: {exc}", method=method) from exc

                if raw is None or raw == "":
                    self.close()
                    raise BrowserDisconnectedError(
                        f"CDP 응답 대기 중 빈 메시지 수신: {method}", method=method
                    )

                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                if message.get("id") != request_id:
                    if "method" in message:
                        self._events.append(message)
                    continue

                if "error" in message:
                    error = message["error"]
                    code = error.get("code")
                    text = error.get("message", "알 수 없는 CDP 오류")
                    raise CdpError(
                        f"CDP 오류 {code}: {text}",
                        method=method,
                        data=error.get("data"),
                    )
                return message.get("result", {})

    def evaluate(
        self,
        expression: str,
        *,
        timeout: float | None = None,
        await_promise: bool = False,
    ) -> Any:
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
                "userGesture": True,
            },
            timeout=timeout,
        )
        if "exceptionDetails" in result:
            details = result["exceptionDetails"]
            description = (
                details.get("exception", {}).get("description")
                or details.get("text")
                or "JavaScript 평가 오류"
            )
            raise CdpError(description, method="Runtime.evaluate", data=details)
        remote_object = result.get("result", {})
        if "value" in remote_object:
            return remote_object["value"]
        return None

    def pop_events(self, method: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if method is None:
                items = list(self._events)
                self._events.clear()
                return items
            matched: list[dict[str, Any]] = []
            kept: deque[dict[str, Any]] = deque(maxlen=self._events.maxlen)
            while self._events:
                item = self._events.popleft()
                if item.get("method") == method:
                    matched.append(item)
                else:
                    kept.append(item)
            self._events = kept
            return matched

    def __enter__(self) -> "CdpConnection":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
