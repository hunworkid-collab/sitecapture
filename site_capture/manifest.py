from __future__ import annotations

import csv
import threading
from datetime import datetime
from pathlib import Path

from .models import CaptureResult, PageState


class ResultManifest:
    FIELDNAMES = (
        "recorded_at",
        "sequence",
        "keyword",
        "domain",
        "query",
        "status",
        "page_state",
        "search_url",
        "screenshot_path",
        "png_width",
        "png_height",
        "capture_selector",
        "sha256",
        "error_type",
        "error_message",
    )

    def __init__(self, output_root: Path) -> None:
        self.output_root = Path(output_root)
        self._lock = threading.Lock()

    def append_success(
        self,
        *,
        sequence: int,
        keyword: str,
        domain: str,
        query: str,
        result: CaptureResult,
    ) -> Path:
        recorded_at = self._parse_datetime(result.captured_at)
        status = "no_results_captured" if result.state == PageState.NO_RESULTS else "success"
        return self._append(
            recorded_at,
            {
                "recorded_at": recorded_at.isoformat(timespec="seconds"),
                "sequence": sequence,
                "keyword": keyword,
                "domain": domain,
                "query": query,
                "status": status,
                "page_state": result.state.value,
                "search_url": result.search_url,
                "screenshot_path": str(result.path),
                "png_width": result.png_width,
                "png_height": result.png_height,
                "capture_selector": result.rect.selector,
                "sha256": result.sha256,
                "error_type": "",
                "error_message": "",
            },
        )

    def append_failure(
        self,
        *,
        sequence: int,
        keyword: str,
        domain: str,
        query: str,
        error: Exception,
    ) -> Path:
        recorded_at = datetime.now().astimezone()
        return self._append(
            recorded_at,
            {
                "recorded_at": recorded_at.isoformat(timespec="seconds"),
                "sequence": sequence,
                "keyword": keyword,
                "domain": domain,
                "query": query,
                "status": "failed",
                "page_state": "",
                "search_url": "",
                "screenshot_path": "",
                "png_width": "",
                "png_height": "",
                "capture_selector": "",
                "sha256": "",
                "error_type": type(error).__name__,
                "error_message": str(error),
            },
        )

    def _append(self, recorded_at: datetime, row: dict[str, str | int]) -> Path:
        csv_path = self.output_root / recorded_at.strftime("%Y-%m-%d") / "results.csv"
        with self._lock:
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            is_new_file = not csv_path.exists() or csv_path.stat().st_size == 0
            encoding = "utf-8-sig" if is_new_file else "utf-8"
            with csv_path.open("a", encoding=encoding, newline="") as file:
                writer = csv.DictWriter(
                    file,
                    fieldnames=self.FIELDNAMES,
                    extrasaction="ignore",
                )
                if is_new_file:
                    writer.writeheader()
                writer.writerow(row)
        return csv_path

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        try:
            result = datetime.fromisoformat(value)
        except ValueError:
            return datetime.now().astimezone()
        return result if result.tzinfo is not None else result.astimezone()
