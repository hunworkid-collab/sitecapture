#!/usr/bin/env python3
"""Chrome CDP 기반 Google site 검색결과 본문 캡처 — 1단계 CLI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from site_capture.errors import Stage1Error
from site_capture.keyword_io import read_keyword_text_file
from site_capture.models import RunConfig
from site_capture.paths import (
    application_data_directory,
    downloads_directory,
    normalize_keyword,
)
from site_capture.query import normalize_domains
from site_capture.runner import Stage1Runner


def _collect_keywords(values: list[str] | None, file_path: Path | None) -> tuple[str, ...]:
    items: list[str] = list(values or [])
    if file_path is not None:
        items.extend(read_keyword_text_file(file_path))

    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = normalize_keyword(item)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    if not result:
        raise ValueError("--keyword 또는 --keyword-file로 키워드를 하나 이상 입력하세요.")
    return tuple(result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Chrome CDP로 Google site 검색결과 본문만 PNG로 캡처합니다."
    )
    parser.add_argument(
        "--keyword",
        action="append",
        help="검색 키워드. 여러 번 지정할 수 있습니다.",
    )
    parser.add_argument(
        "--keyword-file",
        type=Path,
        help="한 줄에 하나의 키워드를 넣은 TXT 파일",
    )
    parser.add_argument(
        "--domain",
        action="append",
        help="검색할 도메인을 한 번 이상 입력하세요.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=downloads_directory(),
        help="날짜 폴더를 만들 상위 출력 폴더. 기본값은 OS 다운로드 폴더입니다.",
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=application_data_directory() / "chrome-profile",
        help="CDP 전용 Chrome 사용자 데이터 폴더",
    )
    parser.add_argument("--chrome-path", type=Path, help="Chrome 실행 파일 경로")
    parser.add_argument(
        "--search-mode",
        choices=("search-box", "direct-url"),
        default="search-box",
        help="기본값 search-box는 Google 검색창에 직접 입력합니다.",
    )
    parser.add_argument("--exact", action="store_true", help="키워드를 큰따옴표 정확 문구로 검색")
    parser.add_argument(
        "--exclude-public-from-root",
        action="store_true",
        help="루트 도메인 검색식에 -site:public.<도메인> 추가",
    )
    parser.add_argument("--viewport-width", type=int, default=1440)
    parser.add_argument("--viewport-height", type=int, default=1000)
    parser.add_argument("--timeout", type=float, default=30.0, help="페이지/CDP 제한시간(초)")
    parser.add_argument("--delay", type=float, default=5.0, help="작업 사이 고정 대기시간(초)")
    parser.add_argument("--overwrite", action="store_true", help="동일 PNG를 덮어쓰기")
    parser.add_argument("--no-metadata", action="store_true", help="PNG 옆 JSON 메타데이터를 쓰지 않음")
    parser.add_argument("--keep-chrome-open", action="store_true")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="화면 없이 실행. CAPTCHA/동의 화면이 나오면 처리할 수 없습니다.",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        keywords = _collect_keywords(args.keyword, args.keyword_file)
        domains = normalize_domains(args.domain or [])
        if not domains:
            raise ValueError("--domain으로 검색할 도메인을 하나 이상 입력하세요.")
        if args.viewport_width < 800 or args.viewport_height < 600:
            raise ValueError("viewport는 최소 800×600 이상으로 지정하세요.")
        if args.timeout <= 0 or args.delay < 0:
            raise ValueError("timeout은 0보다 크고 delay는 0 이상이어야 합니다.")

        config = RunConfig(
            keywords=keywords,
            domains=domains,
            output_root=args.output_dir.expanduser().resolve(),
            profile_dir=args.profile_dir.expanduser().resolve(),
            chrome_path=args.chrome_path,
            search_mode=args.search_mode,
            exact_phrase=args.exact,
            exclude_public_from_root=args.exclude_public_from_root,
            viewport_width=args.viewport_width,
            viewport_height=args.viewport_height,
            timeout_seconds=args.timeout,
            delay_between_jobs_seconds=args.delay,
            overwrite=args.overwrite,
            keep_chrome_open=args.keep_chrome_open,
            write_metadata=not args.no_metadata,
            headless=args.headless,
            verbose=args.verbose,
        )
        summary = Stage1Runner(config).run()
    except (ValueError, OSError, Stage1Error) as exc:
        print(f"실행 실패: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("사용자가 작업을 중단했습니다.", file=sys.stderr)
        return 130

    print(
        f"작업 종료: 전체 {summary.total}, 성공 {summary.succeeded}, 실패 {summary.failed}"
    )
    return 0 if summary.failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
