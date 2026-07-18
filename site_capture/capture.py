"""CDP PNG 캡처, 검증, 원자적 저장."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
from pathlib import Path
from typing import Any

from .cdp import CdpConnection
from .errors import CdpError, Stage1Error
from .models import CaptureRect

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def capture_png(cdp: CdpConnection, rect: CaptureRect) -> bytes:
    if rect.width > 32767 or rect.height > 32767:
        raise Stage1Error(
            f"캡처 영역이 1단계 단일 이미지 제한을 초과합니다: {rect.width}x{rect.height}"
        )

    result = cdp.call(
        "Page.captureScreenshot",
        {
            "format": "png",
            "fromSurface": True,
            "captureBeyondViewport": True,
            "clip": rect.as_cdp_clip(),
        },
        timeout=60.0,
    )
    encoded = result.get("data")
    if not isinstance(encoded, str) or not encoded:
        raise CdpError("Page.captureScreenshot 응답에 이미지 데이터가 없습니다.")
    try:
        return base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise Stage1Error(f"PNG Base64 디코딩 실패: {exc}") from exc


def validate_png(data: bytes) -> tuple[int, int]:
    if len(data) < 33 or not data.startswith(PNG_SIGNATURE):
        raise Stage1Error("저장하려는 데이터가 유효한 PNG 형식이 아닙니다.")
    if data[12:16] != b"IHDR":
        raise Stage1Error("PNG IHDR 청크를 찾지 못했습니다.")
    width, height = struct.unpack(">II", data[16:24])
    if width <= 0 or height <= 0:
        raise Stage1Error(f"PNG 크기가 올바르지 않습니다: {width}x{height}")
    return width, height


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    try:
        with temporary.open("wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    atomic_write_bytes(path, encoded)
