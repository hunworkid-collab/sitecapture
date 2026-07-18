from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from ..models import RunConfig
from ..paths import normalize_keyword
from ..query import build_query
from .schema import SCHEMA_VERSION


class RunStatus(str, Enum):
    CREATED = "created"
    PREPARING = "preparing"
    RUNNING = "running"
    PAUSED = "paused"
    USER_ACTION_REQUIRED = "user_action_required"
    STOPPING = "stopping"
    INTERRUPTED = "interrupted"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"


class StoredJobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCESS = "success"
    NO_RESULTS_CAPTURED = "no_results_captured"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED_EXISTING = "skipped_existing"


@dataclass(frozen=True, slots=True)
class JobSeed:
    id: str
    sequence: int
    keyword_index: int
    keyword_original: str
    keyword_normalized: str
    domain: str
    query: str


@dataclass(frozen=True, slots=True)
class StoredRun:
    id: str
    title: str
    status: RunStatus
    total_jobs: int
    completed_jobs: int
    succeeded_jobs: int
    failed_jobs: int
    cancelled_jobs: int
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None
    last_message: str


@dataclass(frozen=True, slots=True)
class StoredJob:
    id: str
    run_id: str
    sequence: int
    keyword_index: int
    keyword_original: str
    keyword_normalized: str
    domain: str
    query: str
    status: StoredJobStatus
    attempts: int
    max_attempts: int
    next_attempt_at: str | None
    screenshot_path: str
    metadata_path: str
    page_state: str
    last_error_type: str
    last_error_message: str


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id() -> str:
    return uuid.uuid4().hex


def run_config_to_json(config: RunConfig) -> str:
    payload = asdict(config)
    payload["schema_version"] = SCHEMA_VERSION
    payload["keywords"] = list(config.keywords)
    payload["domains"] = list(config.domains)
    payload["output_root"] = str(config.output_root)
    payload["profile_dir"] = str(config.profile_dir)
    payload["chrome_path"] = str(config.chrome_path) if config.chrome_path is not None else None
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def run_config_from_json(value: str) -> RunConfig:
    raw = json.loads(value)
    if not isinstance(raw, dict):
        raise ValueError("저장된 RunConfig JSON이 객체 형식이 아닙니다.")
    known_names = {item.name for item in fields(RunConfig)}
    payload = {key: item for key, item in raw.items() if key in known_names}
    required_names = {"keywords", "domains", "output_root", "profile_dir"}
    missing = required_names - payload.keys()
    if missing:
        missing_text = ", ".join(sorted(missing))
        raise ValueError(f"저장된 실행 설정에 필수 항목이 없습니다: {missing_text}")
    payload["keywords"] = tuple(str(item) for item in payload["keywords"])
    payload["domains"] = tuple(str(item) for item in payload["domains"])
    payload["output_root"] = Path(str(payload["output_root"]))
    payload["profile_dir"] = Path(str(payload["profile_dir"]))
    chrome_path = payload.get("chrome_path")
    payload["chrome_path"] = Path(str(chrome_path)) if chrome_path else None
    return RunConfig(**payload)


def build_job_seeds(config: RunConfig) -> tuple[JobSeed, ...]:
    seeds: list[JobSeed] = []
    sequence = 0
    for keyword_index, keyword in enumerate(config.keywords, start=1):
        normalized = normalize_keyword(keyword)
        if not normalized:
            continue
        for domain in config.domains:
            sequence += 1
            seeds.append(JobSeed(
                id=new_id(),
                sequence=sequence,
                keyword_index=keyword_index,
                keyword_original=keyword,
                keyword_normalized=normalized,
                domain=domain,
                query=build_query(
                    domain,
                    normalized,
                    exact_phrase=config.exact_phrase,
                ),
            ))
    return tuple(seeds)
