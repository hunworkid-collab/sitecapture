from __future__ import annotations

import struct
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import main as cli_main
import live_test
from site_capture.capture import PNG_SIGNATURE, validate_png
from site_capture.models import CaptureRect, RunSummary
from site_capture.paths import (
    build_output_path,
    display_path,
    normalize_keyword,
    resolve_display_path,
    safe_component,
)
from site_capture.query import build_query, normalize_domains
from site_capture.runner import Stage1Runner
from site_capture.gui.events import Stage2Summary


class QueryTests(unittest.TestCase):
    def test_normal_query(self) -> None:
        self.assertEqual(
            build_query("example.com", "테스트 검색어"),
            "site:example.com 테스트 검색어",
        )

    def test_exact_query(self) -> None:
        self.assertEqual(
            build_query(
                "example.com",
                "테스트 검색어",
                exact_phrase=True,
            ),
            'site:example.com "테스트 검색어"',
        )

    def test_normalize_domains_accepts_custom_values_and_deduplicates(self) -> None:
        self.assertEqual(
            normalize_domains([" Example.COM ", "example.com", "sub.example.com"]),
            ("example.com", "sub.example.com"),
        )

    def test_normalize_domains_rejects_url_and_path_input(self) -> None:
        with self.assertRaises(ValueError):
            normalize_domains(["https://example.com/path"])

    def test_cli_rejects_removed_public_exclusion_option(self) -> None:
        with self.assertRaises(SystemExit):
            cli_main.build_parser().parse_args(
                [
                    "--keyword",
                    "keyword",
                    "--domain",
                    "example.com",
                    "--exclude-public-from-root",
                ]
            )


class PathTests(unittest.TestCase):
    def test_user_home_path_is_displayed_without_username(self) -> None:
        home = Path.home().resolve()
        displayed = display_path(home / "Downloads")

        self.assertNotIn(str(home), displayed)
        self.assertEqual(resolve_display_path(displayed), home / "Downloads")

    def test_keyword_normalization(self) -> None:
        self.assertEqual(normalize_keyword("  문화   행사  "), "문화 행사")

    def test_invalid_filename_characters(self) -> None:
        self.assertEqual(safe_component('문화/행사:2026?'), "문화_행사_2026_")

    def test_domain_subfolder_and_collision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            now = datetime(2026, 7, 17, tzinfo=timezone.utc)
            first = build_output_path(
                root,
                captured_at=now,
                domain="example.com",
                keyword="테스트키워드",
                overwrite=False,
            )
            first.parent.mkdir(parents=True)
            first.write_bytes(b"x")
            second = build_output_path(
                root,
                captured_at=now,
                domain="example.com",
                keyword="테스트키워드",
                overwrite=False,
            )
            self.assertEqual(first.name, "2026-07-17_테스트키워드.png")
            self.assertEqual(second.name, "2026-07-17_테스트키워드_2.png")


class PngTests(unittest.TestCase):
    def test_header_dimensions(self) -> None:
        # validate_png는 IHDR 구조와 크기만 확인하므로 테스트용 최소 바이트를 구성한다.
        data = (
            PNG_SIGNATURE
            + struct.pack(">I", 13)
            + b"IHDR"
            + struct.pack(">II", 640, 480)
            + bytes([8, 6, 0, 0, 0])
            + b"\x00\x00\x00\x00"
        )
        self.assertEqual(validate_png(data), (640, 480))

    def test_clip(self) -> None:
        clip = CaptureRect(10, 20, 300, 400, "#search").as_cdp_clip()
        self.assertEqual(clip["x"], 10.0)
        self.assertEqual(clip["height"], 400.0)


class CliTests(unittest.TestCase):
    def test_live_test_requires_keyword(self) -> None:
        with patch.object(sys, "argv", ["live_test.py", "--domain", "기관도메인"]):
            with self.assertRaises(SystemExit):
                live_test.parse_arguments()

    def test_main_deduplicates_repeated_domains(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with patch("main.Stage1Runner") as runner:
                runner.return_value.run.return_value = RunSummary(total=1, succeeded=1)
                exit_code = cli_main.main(
                    [
                        "--keyword",
                        "keyword",
                        "--domain",
                        "example.com",
                        "--domain",
                        "example.com",
                        "--output-dir",
                        temporary,
                        "--profile-dir",
                        temporary,
                    ]
                )

        config = runner.call_args.args[0]
        self.assertEqual(exit_code, 0)
        self.assertEqual(config.domains, ("example.com",))

    def test_main_accepts_custom_domain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with patch("main.Stage1Runner") as runner:
                runner.return_value.run.return_value = RunSummary(total=1, succeeded=1)
                exit_code = cli_main.main(
                    [
                        "--keyword",
                        "keyword",
                        "--domain",
                        "example.com",
                        "--output-dir",
                        temporary,
                        "--profile-dir",
                        temporary,
                    ]
                )

        config = runner.call_args.args[0]
        self.assertEqual(exit_code, 0)
        self.assertEqual(config.domains, ("example.com",))


class Stage1RunnerTests(unittest.TestCase):
    def test_runner_adapts_stage2_summary_for_cli(self) -> None:
        config = cli_main.RunConfig(
            keywords=("keyword",),
            domains=("example.com",),
            output_root=Path(tempfile.gettempdir()),
            profile_dir=Path(tempfile.gettempdir()) / "site-capture-profile",
        )
        with patch("site_capture.runner.Stage2Runner") as stage2_runner:
            stage2_runner.return_value.run.return_value = Stage2Summary(
                total=1,
                completed=1,
                succeeded=1,
            )
            summary = Stage1Runner(config, log=lambda _message: None).run()

        self.assertEqual(summary, RunSummary(total=1, succeeded=1))
        self.assertEqual(stage2_runner.call_args.args[0], config)


if __name__ == "__main__":
    unittest.main()
