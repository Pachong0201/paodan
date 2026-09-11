"""V5.0 JobRunner：本地单 worker 后台执行 ScreeningPipeline。"""
from __future__ import annotations

import logging
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Optional

from ..storage.database import Database
from .job_repository import WorkbenchRepository
from .models import JobRuntimeConfig
from .pipeline_factory import PipelineFactory

logger = logging.getLogger(__name__)


class JobRunnerError(RuntimeError):
    pass


class JobRunner:
    def __init__(self, db_path: str | Path, staging_root: str | Path | None = None):
        self.db_path = str(db_path)
        self.staging_root = Path(staging_root) if staging_root else (Path(__file__).resolve().parents[2]
                                                                    / "data" / "import_staging")
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="paodan-job")
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def recover_on_startup(self) -> int:
        with Database(self.db_path) as db:
            repo = WorkbenchRepository(db.conn)
            return repo.recover_interrupted_jobs()

    def start_job(self, import_id: str, runtime_config: JobRuntimeConfig) -> str:
        with self._lock:
            with Database(self.db_path) as db:
                repo = WorkbenchRepository(db.conn)
                try:
                    # 原子校验 batch.status == READY、防重复启动、写 Job + Items、
                    # READY→PROCESSING；单 worker 锁只是额外保护，不是唯一保护。
                    job_id = repo.start_job_for_batch(import_id, runtime_config)
                except ValueError as exc:
                    raise JobRunnerError(str(exc)) from exc
        self.executor.submit(self._run_job, job_id, import_id, runtime_config)
        return job_id

    def request_cancel(self, job_id: str) -> None:
        with Database(self.db_path) as db:
            WorkbenchRepository(db.conn).request_cancel(job_id)

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with Database(self.db_path) as db:
            return WorkbenchRepository(db.conn).get_job(job_id)

    # ------------------------------------------------------------------
    def _staged_path(self, import_id: str, f: Dict[str, Any]) -> Path:
        sha = str(f.get("source_sha256") or "")
        name = Path(str(f.get("stored_filename") or "message.eml")).name
        return self.staging_root / import_id / "files" / sha / name

    def _priority_counter(self, priority: str) -> str:
        return {"S": "s_count", "A": "a_count", "B": "b_count",
                "C": "c_count", "D": "d_count"}.get(str(priority or "").upper(), "d_count")

    def _run_job(self, job_id: str, import_id: str, runtime_config: JobRuntimeConfig) -> None:
        db = Database(self.db_path)
        repo = WorkbenchRepository(db.conn)
        try:
            repo.set_job_status(job_id, "RUNNING")
            config = PipelineFactory.load_rule_config()
            pipeline = PipelineFactory.create(config, runtime_config, db=db)
            files = repo.list_ready_files(import_id)
            for f in files:
                if repo.cancel_requested(job_id):
                    repo.set_job_status(job_id, "CANCELLED")
                    repo.refresh_batch_status(import_id)
                    return
                # 按 import_file_id 精确映射 job_item，不依赖两个列表“排序刚好一致”。
                job_item = repo.get_job_item_for_import_file(job_id, int(f.get("id") or 0))
                item_id = job_item.get("id") if job_item else None
                if job_item is None or int(job_item.get("import_file_id") or 0) != int(f.get("id") or 0):
                    repo.increment_job(job_id, "failed_count")
                    if item_id is not None:
                        repo.update_job_item(item_id, "FAILED", error_code="JOB_ITEM_MAPPING_MISMATCH")
                    continue
                repo.update_job_item(item_id, "RUNNING", error_code="")
                repo.update_job_progress(job_id, Path(str(f.get("stored_filename") or "")).name)
                path = self._staged_path(import_id, f)
                try:
                    rec = pipeline.process_file(path)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Job %s 单封处理失败: %s", job_id, type(exc).__name__)
                    repo.increment_job(job_id, "failed_count")
                    if item_id is not None:
                        repo.update_job_item(item_id, "FAILED", error_code=type(exc).__name__)
                    continue
                if rec is None:
                    repo.increment_job(job_id, "duplicate_count")
                    repo.mark_import_file_duplicate(f.get("id"))
                    if item_id is not None:
                        repo.update_job_item(item_id, "DUPLICATE", error_code="DUPLICATE")
                    continue
                if getattr(rec, "error", ""):
                    repo.increment_job(job_id, "failed_count")
                    if item_id is not None:
                        repo.update_job_item(item_id, "FAILED", error_code="EML_PARSE_ERROR")
                    continue
                priority = str(rec.score.priority if rec.score else "D")
                score = float(rec.score.final_score if rec.score else 0.0)
                repo.increment_job(job_id, "success_count")
                repo.increment_job(job_id, self._priority_counter(priority), count_processed=False)
                if item_id is not None:
                    repo.update_job_item(item_id, "COMPLETED", email_id=rec.email_id,
                                         priority=priority, final_score=score)
                repo.mark_import_file_completed(f.get("id"), rec.email_id)
                try:
                    db.execute("UPDATE analysis_runs SET job_id=? WHERE email_id=?",
                               (job_id, rec.email_id))
                except Exception:
                    pass
            if repo.cancel_requested(job_id):
                repo.set_job_status(job_id, "CANCELLED")
                repo.refresh_batch_status(import_id)
                return
            repo.set_job_status(job_id, "COMPLETED")
            repo.refresh_batch_status(import_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Job %s failed", job_id)
            try:
                repo.set_job_status(job_id, "FAILED", error=type(exc).__name__)
                repo.refresh_batch_status(import_id)
            except Exception:
                pass
        finally:
            db.close()
