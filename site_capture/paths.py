"""Downloads 경로와 안전한 출력 파일명 처리."""

from __future__ import annotations

import ctypes
import hashlib
import os
import platform
import re
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path

_INVALID_WINDOWS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1F]')
_WHITESPACE = re.compile(r"\s+")
_RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_string(cls, value: str) -> "_GUID":
        item = uuid.UUID(value)
        data4 = (ctypes.c_ubyte * 8).from_buffer_copy(item.bytes[8:])
        return cls(item.time_low, item.time_mid, item.time_hi_version, data4)


def downloads_directory() -> Path:
    if platform.system().lower() == "windows":
        # FOLDERID_Downloads = {374DE290-123F-4565-9164-39C4925E467B}
        try:
            folder_id = _GUID.from_string("374DE290-123F-4565-9164-39C4925E467B")
            path_pointer = ctypes.c_wchar_p()
            result = ctypes.windll.shell32.SHGetKnownFolderPath(  # type: ignore[attr-defined]
                ctypes.byref(folder_id), 0, None, ctypes.byref(path_pointer)
            )
            if result == 0 and path_pointer.value:
                path = Path(path_pointer.value)
                ctypes.windll.ole32.CoTaskMemFree(  # type: ignore[attr-defined]
                    ctypes.cast(path_pointer, ctypes.c_void_p)
                )
                return path
        except Exception:
            pass
    return Path.home() / "Downloads"


def display_path(path: Path) -> str:
    resolved = path.expanduser().resolve()
    home = Path.home().resolve()
    try:
        relative = resolved.relative_to(home)
    except ValueError:
        return str(path)

    prefix = "%USERPROFILE%" if platform.system().lower() == "windows" else "~"
    return str(Path(prefix, *relative.parts))


def resolve_display_path(value: str) -> Path:
    return Path(os.path.expandvars(value.strip())).expanduser()


def application_data_directory() -> Path:
    if platform.system().lower() == "windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        return base / "SiteCapture"
    if platform.system().lower() == "darwin":
        return Path.home() / "Library/Application Support/SiteCapture"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "site-capture"


def normalize_keyword(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return _WHITESPACE.sub(" ", normalized).strip()


def safe_component(value: str, *, max_length: int = 100) -> str:
    original = normalize_keyword(value)
    cleaned = _INVALID_WINDOWS_CHARS.sub("_", original)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip(" .")
    if not cleaned:
        cleaned = "keyword"

    stem_upper = cleaned.split(".", 1)[0].upper()
    if stem_upper in _RESERVED_WINDOWS_NAMES:
        cleaned = f"_{cleaned}"

    if len(cleaned) > max_length:
        digest = hashlib.sha1(original.encode("utf-8")).hexdigest()[:8]
        cleaned = f"{cleaned[: max_length - 9].rstrip()}_{digest}"
    return cleaned


def build_output_path(
    output_root: Path,
    *,
    captured_at: datetime,
    domain: str,
    keyword: str,
    overwrite: bool,
) -> Path:
    date_text = captured_at.strftime("%Y-%m-%d")
    directory = output_root / date_text / safe_component(domain, max_length=80)
    base = directory / f"{date_text}_{safe_component(keyword)}.png"
    if overwrite or not base.exists():
        return base

    for index in range(2, 10000):
        candidate = base.with_name(f"{base.stem}_{index}{base.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"동일 파일명 충돌이 너무 많습니다: {base}")
