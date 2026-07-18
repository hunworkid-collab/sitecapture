#!/usr/bin/env python3

from __future__ import annotations

import compileall
import importlib
import os
import sys
import tempfile
import traceback
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(PROJECT_ROOT))

REQUIRED_FILES = (
    "gui_main.py",
    "site_capture/__init__.py",
    "site_capture/models.py",
    "site_capture/paths.py",
    "site_capture/query.py",
    "site_capture/cdp.py",
    "site_capture/chrome.py",
    "site_capture/google.py",
    "site_capture/capture.py",
    "site_capture/manifest.py",
    "site_capture/stage2_runner.py",
    "site_capture/gui/control.py",
    "site_capture/gui/events.py",
    "site_capture/gui/worker.py",
    "site_capture/gui/main_window.py",
    "site_capture/persistence/schema.py",
    "site_capture/persistence/models.py",
    "site_capture/persistence/repository.py",
)

MODULES = (
    "site_capture.models",
    "site_capture.paths",
    "site_capture.query",
    "site_capture.cdp",
    "site_capture.chrome",
    "site_capture.google",
    "site_capture.capture",
    "site_capture.manifest",
    "site_capture.keyword_io",
    "site_capture.persistence.schema",
    "site_capture.persistence.models",
    "site_capture.persistence.repository",
    "site_capture.gui.control",
    "site_capture.gui.events",
    "site_capture.stage2_runner",
    "site_capture.gui.worker",
    "site_capture.gui.main_window",
)


def print_result(name: str, success: bool, message: str = "") -> None:
    status = "PASS" if success else "FAIL"
    suffix = f": {message}" if message else ""
    print(f"[{status}] {name}{suffix}")


def check_required_files() -> None:
    missing = [
        filename
        for filename in REQUIRED_FILES
        if not (PROJECT_ROOT / filename).is_file()
    ]
    if missing:
        raise RuntimeError(
            "다음 파일이 없습니다:\n" + "\n".join(f"  - {filename}" for filename in missing)
        )
    print_result("필수 파일", True, f"{len(REQUIRED_FILES)}개")


def check_python_syntax() -> None:
    package_ok = compileall.compile_dir(PROJECT_ROOT / "site_capture", quiet=1, force=True)
    main_ok = compileall.compile_file(PROJECT_ROOT / "gui_main.py", quiet=1, force=True)
    if not package_ok or not main_ok:
        raise RuntimeError("Python 문법 검사에 실패했습니다.")
    print_result("Python 문법", True)


def check_imports() -> None:
    for module_name in MODULES:
        importlib.import_module(module_name)
    print_result("모듈 import", True, f"{len(MODULES)}개")


def check_database() -> None:
    from site_capture.models import RunConfig
    from site_capture.persistence import JobRepository

    with tempfile.TemporaryDirectory() as value:
        temp_root = Path(value)
        config = RunConfig(
            keywords=("테스트키워드",),
            domains=("기관도메인", "public.기관도메인"),
            output_root=temp_root / "output",
            profile_dir=temp_root / "profile",
            delay_between_jobs_seconds=0,
        )
        repository = JobRepository(temp_root / "jobs.db")
        run_id = repository.create_run(config)
        pending_jobs = repository.pending_jobs(run_id)
        total, completed, succeeded, failed = repository.run_counts(run_id)
        loaded_config = repository.load_config(run_id)

        if total != 2:
            raise RuntimeError(f"작업 개수가 잘못되었습니다: {total}")
        if len(pending_jobs) != 2:
            raise RuntimeError(f"대기 작업 개수가 잘못되었습니다: {len(pending_jobs)}")
        if completed != 0 or succeeded != 0 or failed != 0:
            raise RuntimeError("초기 진행률 값이 잘못되었습니다.")
        if loaded_config.keywords != ("테스트키워드",):
            raise RuntimeError("RunConfig 복원 결과가 잘못되었습니다.")
        if loaded_config.domains != ("기관도메인", "public.기관도메인"):
            raise RuntimeError("도메인 설정 복원 결과가 잘못되었습니다.")

    print_result("SQLite 저장", True, "실행 1개, 작업 2개")


def check_gui_creation() -> None:
    from PySide6.QtWidgets import QApplication

    from site_capture.gui.main_window import MainWindow

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    window = MainWindow()
    if window.windowTitle() == "":
        raise RuntimeError("메인 창 제목이 설정되지 않았습니다.")
    title = window.windowTitle()
    window.close()
    window.deleteLater()
    print_result("PySide6 GUI 생성", True, title)


def main() -> int:
    print()
    print("=" * 54)
    print(" Site Capture 프로젝트 점검")
    print("=" * 54)
    print()

    checks = (
        check_required_files,
        check_python_syntax,
        check_imports,
        check_database,
        check_gui_creation,
    )

    try:
        for check in checks:
            check()
    except Exception as exc:  # noqa: BLE001
        print()
        print_result("최종 결과", False, str(exc))
        print()
        traceback.print_exc()
        return 1

    print()
    print_result("최종 결과", True, "기본 점검 완료")
    print()
    print("주의: Chrome 실행과 실제 Google 검색은 검사하지 않았습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
