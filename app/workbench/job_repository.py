"""V5.0 Workbench Repository：import_batches / import_files / analysis_jobs / job_items。"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .models import AnalysisJob, JobRuntimeConfig


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row(row) -> Optional[Dict[str, Any]]:
    return dict(row) if row is not None else None


def _new_job_id() -> str:
    return f"JOB-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


class WorkbenchRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ------------------------------------------------------------------
    # import batches
    # ------------------------------------------------------------------
    def save_import_result(self, result, source_type: str = "upload") -> str:
        batch = result
        self.conn.execute(
            """INSERT OR REPLACE INTO import_batches
               (import_id, status, total_files, accepted_files, rejected_files,
                duplicate_files, total_bytes, created_at, finished_at, source_type, error_summary)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (batch.import_id, "READY", batch.discovered, batch.accepted,
             batch.rejected, batch.duplicates,
             sum(int(getattr(i, "size", 0) or 0) for i in batch.items),
             _now(), _now(), source_type, "; ".join(batch.errors[:5])))
        for item in batch.items:
            original = Path(item.archive_relative_path or item.staged_path).name
            stored = Path(item.staged_path).name if item.staged_path else original
            self.conn.execute(
                """INSERT INTO import_files
                   (import_id, original_filename, stored_filename, archive_relative_path,
                    source_sha256, size_bytes, file_type, status, error_code, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (batch.import_id, original, stored, item.archive_relative_path or original,
                 item.raw_sha256 or "", int(item.size or 0), "eml", item.status,
                 item.reason or "", _now()))
        self.conn.commit()
        return batch.import_id

    def get_import_batch(self, import_id: str) -> Optional[Dict[str, Any]]:
        return _row(self.conn.execute(
            "SELECT * FROM import_batches WHERE import_id=?", (import_id,)).fetchone())

    def list_import_batches(self, status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM import_batches WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (status, limit)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM import_batches ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def ready_import_count(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM import_batches WHERE status='READY'").fetchone()
        return int(row[0] or 0)

    def list_ready_files(self, import_id: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT * FROM import_files
               WHERE import_id=? AND status='accepted'
               ORDER BY id""", (import_id,)).fetchall()
        return [dict(r) for r in rows]

    def mark_batch_processing(self, import_id: str) -> None:
        self.conn.execute("UPDATE import_batches SET status='PROCESSING' WHERE import_id=?", (import_id,))
        self.conn.commit()

    def mark_batch_completed(self, import_id: str, status: str = "COMPLETED") -> None:
        self.conn.execute("UPDATE import_batches SET status=?, finished_at=? WHERE import_id=?",
                          (status, _now(), import_id))
        self.conn.commit()

    # ------------------------------------------------------------------
    # jobs
    # ------------------------------------------------------------------
    def create_job(self, import_id: str, runtime_config: JobRuntimeConfig,
                   total_count: int) -> str:
        job_id = _new_job_id()
        self.conn.execute(
            """INSERT INTO analysis_jobs
               (job_id, import_id, status, total_count, llm_profile_id, llm_enabled,
                llm_mode, llm_model, runtime_config_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (job_id, import_id, "PENDING", total_count, runtime_config.llm_profile_id,
             1 if runtime_config.llm_enabled else 0, runtime_config.llm_mode,
             runtime_config.llm_model, runtime_config.to_json(), _now()))
        self.conn.commit()
        return job_id

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        return _row(self.conn.execute(
            "SELECT * FROM analysis_jobs WHERE job_id=?", (job_id,)).fetchone())

    def list_jobs(self, limit: int = 50) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM analysis_jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_active_job(self) -> Optional[Dict[str, Any]]:
        return _row(self.conn.execute(
            """SELECT * FROM analysis_jobs
               WHERE status IN ('PENDING','RUNNING','CANCEL_REQUESTED')
               ORDER BY created_at DESC LIMIT 1""").fetchone())

    def set_job_status(self, job_id: str, status: str, error: str = "") -> None:
        fields = ["status=?"]
        params: List[Any] = [status]
        if status == "RUNNING":
            fields.append("started_at=?")
            params.append(_now())
        if status in ("COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"):
            fields.append("finished_at=?")
            params.append(_now())
        if error:
            fields.append("last_error=?")
            params.append(error)
        params.append(job_id)
        self.conn.execute(f"UPDATE analysis_jobs SET {', '.join(fields)} WHERE job_id=?", params)
        self.conn.commit()

    def update_job_progress(self, job_id: str, current_filename: str = "") -> None:
        self.conn.execute(
            "UPDATE analysis_jobs SET current_filename=? WHERE job_id=?",
            (current_filename, job_id))
        self.conn.commit()

    def increment_job(self, job_id: str, field: str, amount: int = 1,
                      count_processed: bool = True) -> None:
        allowed = {"processed_count", "success_count", "failed_count", "duplicate_count",
                   "s_count", "a_count", "b_count", "c_count", "d_count"}
        if field not in allowed:
            raise ValueError(f"invalid job counter: {field}")
        prefix = "processed_count=processed_count+1, " if count_processed else ""
        self.conn.execute(
            f"UPDATE analysis_jobs SET {prefix}{field}={field}+? WHERE job_id=?",
            (amount, job_id))
        self.conn.commit()

    def request_cancel(self, job_id: str) -> None:
        self.conn.execute(
            """UPDATE analysis_jobs
               SET cancel_requested=1,
                   status=CASE WHEN status='PENDING' THEN 'CANCELLED' ELSE 'CANCEL_REQUESTED' END,
                   finished_at=CASE WHEN status='PENDING' THEN ? ELSE finished_at END
               WHERE job_id=?""", (_now(), job_id))
        self.conn.commit()

    def cancel_requested(self, job_id: str) -> bool:
        row = self.conn.execute("SELECT cancel_requested FROM analysis_jobs WHERE job_id=?", (job_id,)).fetchone()
        return bool(row and row["cancel_requested"])

    def recover_interrupted_jobs(self) -> int:
        rows = self.conn.execute(
            "SELECT job_id FROM analysis_jobs WHERE status IN ('PENDING','RUNNING','CANCEL_REQUESTED')").fetchall()
        for r in rows:
            self.conn.execute(
                "UPDATE analysis_jobs SET status='INTERRUPTED', finished_at=?, last_error='WORKBENCH_RESTARTED' WHERE job_id=?",
                (_now(), r["job_id"]))
        self.conn.commit()
        return len(rows)

    # ------------------------------------------------------------------
    # job items
    # ------------------------------------------------------------------
    def add_job_items(self, job_id: str, files: Iterable[Dict[str, Any]]) -> None:
        for f in files:
            self.conn.execute(
                """INSERT INTO job_items(job_id, import_file_id, filename, status)
                   VALUES (?,?,?,?)""",
                (job_id, f.get("id"), Path(f.get("stored_filename") or "").name or f.get("original_filename") or "",
                 "PENDING"))
        self.conn.commit()

    def list_job_items(self, job_id: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM job_items WHERE job_id=? AND status=? ORDER BY id",
                (job_id, status)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM job_items WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
        return [dict(r) for r in rows]

    def update_job_item(self, item_id: int, status: str, email_id: str = "",
                        priority: str = "", final_score: Optional[float] = None,
                        error_code: str = "") -> None:
        fields = ["status=?", "email_id=?", "priority=?", "final_score=?", "error_code=?"]
        params: List[Any] = [status, email_id, priority, final_score, error_code]
        if status == "RUNNING":
            fields.append("started_at=?")
            params.append(_now())
        if status in ("COMPLETED", "FAILED", "DUPLICATE", "SKIPPED"):
            fields.append("finished_at=?")
            params.append(_now())
        params.append(item_id)
        self.conn.execute(f"UPDATE job_items SET {', '.join(fields)} WHERE id=?", params)
        self.conn.commit()

    def link_import_file_email(self, import_file_id: Optional[int], email_id: str) -> None:
        if import_file_id:
            self.conn.execute("UPDATE import_files SET email_id=? WHERE id=?", (email_id, import_file_id))
            self.conn.commit()

    # ------------------------------------------------------------------
    # settings
    # ------------------------------------------------------------------
    def get_setting(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM workbench_settings WHERE key=?", (key,)).fetchone()
        return str(row["value"] if row is not None else default)

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute(
            """INSERT INTO workbench_settings(key, value, updated_at) VALUES (?,?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (key, value, _now()))
        self.conn.commit()
