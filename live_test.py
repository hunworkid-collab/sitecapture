#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from site_capture.gui.control import BatchControl
from site_capture.gui.events import JobUpdate, RunnerCallbacks
from site_capture.models import RunConfig
from site_capture.paths import application_data_directory, display_path
from site_capture.query import normalize_domains
from site_capture.stage2_runner import Stage2Runner


def downloads_directory() -> Path:
    value = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.DownloadLocation
    )

    if value:
        return Path(value)

    return Path.home() / "Downloads"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Google site 검색결과를 한 건만 실제로 캡처합니다."
    )
    parser.add_argument("--keyword", required=True, help="검색할 키워드")
    parser.add_argument(
        "--domain",
        required=True,
        help="검색할 도메인",
    )
    parser.add_argument("--output", type=Path, default=None, help="출력 루트 폴더")
    parser.add_argument("--chrome", type=Path, default=None, help="Chrome 실행파일 직접 지정")
    parser.add_argument("--exact", action="store_true", help="정확한 문구 검색")
    parser.add_argument(
        "--direct-url",
        action="store_true",
        help="Google 검색창 대신 검색 URL로 이동",
    )
    parser.add_argument("--keep-chrome", action="store_true", help="테스트 후 Chrome을 닫지 않음")
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()

    output_root = args.output if args.output is not None else downloads_directory()
    output_root = output_root.expanduser().resolve()
    profile_dir = application_data_directory() / "live-test-profile"
    output_root.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)

    config = RunConfig(
        keywords=(args.keyword.strip(),),
        domains=normalize_domains((args.domain,)),
        output_root=output_root,
        profile_dir=profile_dir,
        chrome_path=args.chrome,
        search_mode="direct-url" if args.direct_url else "search-box",
        exact_phrase=args.exact,
        delay_between_jobs_seconds=0,
        keep_chrome_open=args.keep_chrome,
        write_metadata=True,
        headless=False,
    )

    control = BatchControl()

    def log(message: str) -> None:
        print(f"[LOG] {message}")

    def state_changed(state: str, message: str) -> None:
        print(f"[STATE] {state}: {message}")

    def job_changed(update: JobUpdate) -> None:
        print(f"[JOB] {update.index}/{update.total} {update.status.value} {update.query}")
        if update.path is not None:
            print(f"[FILE] {display_path(update.path)}")
        if update.message:
            print(f"[MESSAGE] {update.message}")

    def progress_changed(completed: int, total: int, succeeded: int, failed: int) -> None:
        percent = int(completed / total * 100) if total else 0
        print(f"[PROGRESS] {completed}/{total} ({percent}%) 성공={succeeded}, 실패={failed}")

    def user_action_required(_state: str, label: str) -> None:
        print()
        print(f"[사용자 조치 필요] {label}")
        print("Chrome 창에서 해당 화면을 직접 처리하세요.")
        input("처리한 뒤 Enter를 누르세요: ")
        control.confirm_user_action()

    callbacks = RunnerCallbacks(
        log=log,
        state_changed=state_changed,
        job_changed=job_changed,
        progress_changed=progress_changed,
        user_action_required=user_action_required,
    )

    print()
    print("=" * 60)
    print("Google 검색결과 실제 캡처 테스트")
    print("=" * 60)
    print(f"키워드: {config.keywords[0]}")
    print(f"도메인: {config.domains[0]}")
    print(f"출력 폴더: {display_path(config.output_root)}")
    print()

    try:
        summary = Stage2Runner(config=config, control=control, callbacks=callbacks).run()
    except KeyboardInterrupt:
        control.request_stop()
        print()
        print("사용자가 테스트를 중단했습니다.")
        return 130
    except Exception as exc:
        print()
        print(f"테스트 실패: {exc}")
        return 1

    print()
    print("=" * 60)
    print("테스트 결과")
    print("=" * 60)
    print(f"전체: {summary.total}")
    print(f"완료: {summary.completed}")
    print(f"성공: {summary.succeeded}")
    print(f"실패: {summary.failed}")

    if summary.succeeded == 1:
        print()
        print("실제 캡처 테스트를 통과했습니다.")
        return 0

    print()
    print("캡처에 실패했습니다. 위 로그를 확인하세요.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
