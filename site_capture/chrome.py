"""Chrome/Chromium 탐색, 실행, CDP Target 조회."""

from __future__ import annotations

import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ChromeLaunchError, ChromeNotFoundError


def locate_chrome(explicit_path: Path | None = None) -> Path:
    if explicit_path is not None:
        path = explicit_path.expanduser().resolve()
        if path.is_file():
            return path
        raise ChromeNotFoundError(f"지정한 Chrome 실행 파일이 없습니다: {path}")

    system = platform.system().lower()
    candidates: list[Path] = []

    if system == "windows":
        for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(env_name)
            if not base:
                continue
            candidates.extend(
                [
                    Path(base) / "Google/Chrome/Application/chrome.exe",
                    Path(base) / "Chromium/Application/chrome.exe",
                    Path(base) / "Google/Chrome Beta/Application/chrome.exe",
                ]
            )
    elif system == "darwin":
        candidates.extend(
            [
                Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                Path.home()
                / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
            ]
        )
    else:
        for executable in (
            "google-chrome",
            "google-chrome-stable",
            "chromium",
            "chromium-browser",
        ):
            found = shutil.which(executable)
            if found:
                candidates.append(Path(found))

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    raise ChromeNotFoundError(
        "Chrome/Chromium 실행 파일을 찾지 못했습니다. --chrome-path로 직접 지정하세요."
    )


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass(slots=True)
class ChromeSession:
    executable: Path
    profile_dir: Path
    width: int
    height: int
    headless: bool = False
    process: subprocess.Popen[bytes] | None = None
    port: int | None = None

    def start(self, *, timeout: float = 20.0) -> None:
        if self.process is not None:
            return

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.port = find_free_port()

        args = [
            str(self.executable),
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-component-update",
            "--lang=ko-KR",
            f"--window-size={self.width},{self.height + 100}",
        ]
        if self.headless:
            args.append("--headless=new")
        # Linux 컨테이너/root 테스트 환경에서만 필요하다. 일반 데스크톱에서는 추가하지 않는다.
        if platform.system().lower() == "linux" and hasattr(os, "geteuid") and os.geteuid() == 0:
            args.append("--no-sandbox")
        args.append("about:blank")

        creationflags = 0
        if platform.system().lower() == "windows":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

        try:
            self.process = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
                start_new_session=platform.system().lower() != "windows",
            )
        except OSError as exc:
            self.process = None
            raise ChromeLaunchError(f"Chrome 실행 실패: {exc}") from exc

        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                code = self.process.returncode
                self.process = None
                raise ChromeLaunchError(
                    f"Chrome이 CDP 준비 전에 종료되었습니다. 종료 코드: {code}"
                )
            try:
                self.get_json("/json/version", timeout=1.0)
                return
            except Exception as exc:
                last_error = exc
                time.sleep(0.2)

        self.stop()
        raise ChromeLaunchError(f"Chrome 원격 디버깅 준비 시간 초과: {last_error}")

    @property
    def base_url(self) -> str:
        if self.port is None:
            raise ChromeLaunchError("Chrome 세션이 시작되지 않았습니다.")
        return f"http://127.0.0.1:{self.port}"

    def _opener(self) -> urllib.request.OpenerDirector:
        # 시스템 프록시가 localhost CDP 요청을 가로채지 않도록 한다.
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get_json(self, path: str, *, timeout: float = 5.0) -> Any:
        url = f"{self.base_url}{path}"
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with self._opener().open(request, timeout=timeout) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ChromeLaunchError(f"Chrome CDP HTTP 요청 실패: {url}: {exc}") from exc

    def create_page(self, url: str = "about:blank", *, timeout: float = 5.0) -> dict[str, Any]:
        encoded_url = urllib.parse.quote(url, safe=":/?=&%")
        request = urllib.request.Request(
            f"{self.base_url}/json/new?{encoded_url}",
            method="PUT",
            headers={"Accept": "application/json"},
        )
        try:
            with self._opener().open(request, timeout=timeout) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ChromeLaunchError(f"새 페이지 Target 생성 실패: {exc}") from exc

    def get_page_target(self) -> dict[str, Any]:
        targets = self.get_json("/json/list")
        pages = [
            target
            for target in targets
            if target.get("type") == "page" and target.get("webSocketDebuggerUrl")
        ]
        if pages:
            # 프로그램이 연 about:blank 탭을 우선 사용한다.
            pages.sort(key=lambda item: 0 if item.get("url") == "about:blank" else 1)
            return pages[0]
        return self.create_page()

    def stop(self) -> None:
        process, self.process = self.process, None
        if process is None:
            return
        if process.poll() is None:
            try:
                if platform.system().lower() == "windows":
                    process.terminate()
                else:
                    os.killpg(process.pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    if platform.system().lower() == "windows":
                        process.kill()
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
