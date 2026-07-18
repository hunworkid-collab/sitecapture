"""공통 데이터 모델."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal


class PageState(str, Enum):
    LOADING = "loading"
    SEARCH_RESULTS = "search_results"
    NO_RESULTS = "no_results"
    CONSENT_REQUIRED = "consent_required"
    CAPTCHA_REQUIRED = "captcha_required"
    NETWORK_ERROR = "network_error"
    UNKNOWN_LAYOUT = "unknown_layout"


@dataclass(frozen=True, slots=True)
class CaptureRect:
    x: float
    y: float
    width: float
    height: float
    selector: str

    def as_cdp_clip(self) -> dict[str, float]:
        return {
            "x": max(0.0, float(self.x)),
            "y": max(0.0, float(self.y)),
            "width": max(1.0, float(self.width)),
            "height": max(1.0, float(self.height)),
            "scale": 1.0,
        }


@dataclass(frozen=True, slots=True)
class RunConfig:
    keywords: tuple[str, ...]
    domains: tuple[str, ...]
    output_root: Path
    profile_dir: Path
    chrome_path: Path | None = None
    search_mode: Literal["search-box", "direct-url"] = "search-box"
    exact_phrase: bool = False
    viewport_width: int = 1440
    viewport_height: int = 1000
    timeout_seconds: float = 30.0
    stabilization_interval_seconds: float = 0.5
    stabilization_required_count: int = 3
    delay_between_jobs_seconds: float = 5.0
    overwrite: bool = False
    keep_chrome_open: bool = False
    write_metadata: bool = True
    headless: bool = False
    verbose: bool = False
    max_attempts: int = 2


@dataclass(frozen=True, slots=True)
class CaptureResult:
    keyword: str
    domain: str
    query: str
    state: PageState
    path: Path
    search_url: str
    captured_at: str
    rect: CaptureRect
    png_width: int
    png_height: int
    sha256: str
    metadata_path: Path | None = None


@dataclass(slots=True)
class RunSummary:
    total: int
    succeeded: int = 0
    failed: int = 0
    results: list[CaptureResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
