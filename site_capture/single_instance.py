from __future__ import annotations

import ctypes
import hashlib
from pathlib import Path
from typing import Final


_ERROR_ALREADY_EXISTS: Final = 183


class SingleInstanceLock:
    def __init__(self, application_data_path: Path) -> None:
        digest = hashlib.sha256(
            str(application_data_path.resolve()).encode("utf-8")
        ).hexdigest()[:16]
        self.name = f"Local\\SiteCapture-{digest}"
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._create_mutex = kernel32.CreateMutexW
        self._create_mutex.argtypes = (
            ctypes.c_void_p,
            ctypes.c_bool,
            ctypes.c_wchar_p,
        )
        self._create_mutex.restype = ctypes.c_void_p
        self._close_handle = kernel32.CloseHandle
        self._close_handle.argtypes = (ctypes.c_void_p,)
        self._close_handle.restype = ctypes.c_bool
        self._handle: int | None = None

    def acquire(self) -> bool:
        if self._handle is not None:
            return True

        ctypes.set_last_error(0)
        handle = self._create_mutex(None, False, self.name)
        if handle is None:
            error = ctypes.get_last_error()
            raise OSError(error, "Site Capture mutex could not be created")
        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            self._close_handle(handle)
            return False

        self._handle = handle
        return True

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            self._close_handle(handle)
