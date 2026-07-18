from __future__ import annotations

import sqlite3

from .models import StoredJobStatus


def find_resumable_run_id(connection: sqlite3.Connection) -> str | None:
    row = connection.execute(
        "SELECT id FROM runs WHERE status IN ('created', 'interrupted', 'stopped', 'failed') "
        "AND EXISTS (SELECT 1 FROM jobs WHERE jobs.run_id = runs.id "
        "AND jobs.status IN ('pending', 'running', 'retry_wait')) "
        "ORDER BY updated_at DESC LIMIT 1",
    ).fetchone()
    return None if row is None else str(row["id"])


def cancel_unfinished_jobs(connection: sqlite3.Connection, run_id: str, now: str) -> None:
    connection.execute(
        "UPDATE jobs SET status = ?, updated_at = ?, finished_at = ?, next_attempt_at = NULL "
        "WHERE run_id = ? AND status IN ('pending', 'running', 'retry_wait')",
        (StoredJobStatus.CANCELLED.value, now, now, run_id),
    )
