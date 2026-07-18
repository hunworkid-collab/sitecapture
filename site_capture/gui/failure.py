from __future__ import annotations

from typing import Final


_MAX_REASON_LENGTH: Final = 96


def short_failure_reason(message: str) -> str:
    first_line = next(
        (line.strip() for line in message.splitlines() if line.strip()),
        "알 수 없는 오류",
    )
    if len(first_line) <= _MAX_REASON_LENGTH:
        return first_line
    return f"{first_line[: _MAX_REASON_LENGTH - 1]}…"
