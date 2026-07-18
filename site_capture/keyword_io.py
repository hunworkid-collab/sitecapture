from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .paths import normalize_keyword


def read_keyword_text_file(path: Path) -> list[str]:
    raw = path.read_bytes()
    last_error: UnicodeDecodeError | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return raw.decode(encoding).splitlines()
        except UnicodeDecodeError as exc:
            last_error = exc
    raise ValueError(f"키워드 파일 인코딩을 해석할 수 없습니다: {path}") from last_error


def normalize_keywords(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        keyword = normalize_keyword(value)
        if keyword and keyword not in seen:
            seen.add(keyword)
            result.append(keyword)
    return tuple(result)
