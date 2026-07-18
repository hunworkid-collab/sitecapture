from __future__ import annotations

from typing import Final


SCHEMA_VERSION: Final = 2
JOBS_TABLE_SQL: Final = (
    "CREATE TABLE IF NOT EXISTS jobs ("
    "id TEXT PRIMARY KEY, run_id TEXT NOT NULL, sequence INTEGER NOT NULL CHECK (sequence >= 1), "
    "keyword_index INTEGER NOT NULL CHECK (keyword_index >= 1), keyword_original TEXT NOT NULL, "
    "keyword_normalized TEXT NOT NULL, domain TEXT NOT NULL, query TEXT NOT NULL, "
    "status TEXT NOT NULL CHECK (status IN ('pending','running','success','no_results_captured','failed','cancelled','skipped_existing')), "
    "attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0), "
    "max_attempts INTEGER NOT NULL DEFAULT 2 CHECK (max_attempts >= 1), "
    "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, "
    "captured_at TEXT, page_state TEXT NOT NULL DEFAULT '', search_url TEXT NOT NULL DEFAULT '', "
    "screenshot_path TEXT NOT NULL DEFAULT '', metadata_path TEXT NOT NULL DEFAULT '', capture_selector TEXT NOT NULL DEFAULT '', "
    "capture_x REAL, capture_y REAL, capture_width REAL, capture_height REAL, png_width INTEGER, png_height INTEGER, "
    "sha256 TEXT NOT NULL DEFAULT '', last_error_type TEXT NOT NULL DEFAULT '', last_error_message TEXT NOT NULL DEFAULT '', "
    "FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE, UNIQUE (run_id, sequence)); "
)
SCHEMA_SQL: Final = (
    "CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL); "
    "CREATE TABLE IF NOT EXISTS runs ("
    "id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', "
    "status TEXT NOT NULL CHECK (status IN ('created','preparing','running','paused','user_action_required','stopping','interrupted','stopped','completed','failed')), "
    "config_json TEXT NOT NULL, output_root TEXT NOT NULL, profile_dir TEXT NOT NULL, "
    "total_jobs INTEGER NOT NULL CHECK (total_jobs >= 0), "
    "completed_jobs INTEGER NOT NULL DEFAULT 0 CHECK (completed_jobs >= 0), "
    "succeeded_jobs INTEGER NOT NULL DEFAULT 0 CHECK (succeeded_jobs >= 0), "
    "failed_jobs INTEGER NOT NULL DEFAULT 0 CHECK (failed_jobs >= 0), "
    "cancelled_jobs INTEGER NOT NULL DEFAULT 0 CHECK (cancelled_jobs >= 0), "
    "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, "
    "last_message TEXT NOT NULL DEFAULT ''); "
    + JOBS_TABLE_SQL
    + "CREATE INDEX IF NOT EXISTS idx_runs_status_updated ON runs(status, updated_at DESC); "
    "CREATE INDEX IF NOT EXISTS idx_jobs_run_status_sequence ON jobs(run_id, status, sequence);"
)
