# -*- coding: utf-8 -*-
"""V5.0.2 Recovery & Large Archive Hotfix gates."""
from __future__ import annotations

import io
import sys
import threading
import time
import zipfile
from email import policy
from email.message import EmailMessage
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.dashboard.server import create_app
from app.storage.database import Database
from app.workbench.job_repository import WorkbenchRepository
from app.workbench.models import JobRuntimeConfig


def _eml(subject: str, body: str = "body") -> bytes:
    m = EmailMessage(policy=policy.default)
    m["Subject"] = subject
    m["From"] = "source@example.com"
    m["To"] = "tips@example.com"
    m["Date"] = "Mon, 01 Sep 2026 08:00:00 +0800"
    m.set_content(body)
    return m.as_bytes()


def _zip_bytes(entries: dict[str, bytes], compression=zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _app(tmp_path: Path):
    db_path = tmp_path / "v502.db"
    Database(db_path).close()
    app = create_app(db_path, import_staging_root=tmp_path / "staging")
    return app, db_path


def _upload(client: TestClient, app, files):
    return client.post("/import", data={"csrf_token": app.state.csrf_token}, files=files)


def _batches(db_path: Path):
    with Database(db_path) as db:
        return WorkbenchRepository(db.conn).list_import_batches()


def _rows(db_path: Path, sql: str, params: tuple = ()):
    with Database(db_path) as db:
        return [dict(r) for r in db.query(sql, params)]


def _wait_job(client: TestClient, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    status = {}
    while time.time() < deadline:
        status = client.get(f"/jobs/{job_id}/status").json()
        if status.get("status") in ("COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"):
            return status
        time.sleep(0.05)
    return status


class _FakeScore:
    def __init__(self, priority: str = "A", final_score: float = 80.0):
        self.priority = priority
        self.final_score = final_score


class _FakeRec:
    def __init__(self, email_id: str):
        self.email_id = email_id
        self.score = _FakeScore()
        self.error = ""


# ----------------------------------------------------------------------
# P1-1: large ZIP semantics
# ----------------------------------------------------------------------
def test_v502_large_zip_not_limited_by_eml_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_IMPORT_MAX_FILE_SIZE_MB", "1")
    monkeypatch.setenv("WORKBENCH_IMPORT_MAX_BATCH_SIZE_MB", "10")
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    body = "x" * (600 * 1024)
    zip_bytes = _zip_bytes({
        "nested/a.eml": _eml("zip-a", body),
        "nested/b.eml": _eml("zip-b", body),
        "nested/c.eml": _eml("zip-c", body),
    }, compression=zipfile.ZIP_STORED)
    assert len(zip_bytes) > 1024 * 1024
    r = _upload(client, app, [("files", ("large.zip", zip_bytes, "application/zip"))])
    assert r.status_code == 200
    batch = _batches(db_path)[0]
    assert batch["accepted_files"] == 3
    assert batch["status"] == "READY"


def test_v502_direct_large_eml_still_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_IMPORT_MAX_FILE_SIZE_MB", "1")
    monkeypatch.setenv("WORKBENCH_IMPORT_MAX_BATCH_SIZE_MB", "10")
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    r = _upload(client, app, [("files", ("too-big.eml", _eml("big", "x" * (1024 * 1024 + 2048)),
                                             "message/rfc822"))])
    assert r.status_code == 413
    assert _batches(db_path) == []


def test_v502_zip_member_large_is_invalid_but_archive_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_IMPORT_MAX_FILE_SIZE_MB", "1")
    monkeypatch.setenv("WORKBENCH_IMPORT_MAX_BATCH_SIZE_MB", "10")
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    zip_bytes = _zip_bytes({
        "small.eml": _eml("small", "x" * (100 * 1024)),
        "big.eml": _eml("big", "x" * (1024 * 1024 + 2048)),
    }, compression=zipfile.ZIP_STORED)
    r = _upload(client, app, [("files", ("mixed.zip", zip_bytes, "application/zip"))])
    assert r.status_code == 200
    batch = _batches(db_path)[0]
    assert batch["accepted_files"] == 1
    assert batch["total_files"] == 2
    rows = _rows(db_path, "SELECT status, error_code FROM import_files WHERE import_id=? ORDER BY id",
                 (batch["import_id"],))
    assert sorted((r["status"], r["error_code"]) for r in rows) == [
        ("accepted", ""), ("invalid", "FILE_TOO_LARGE")
    ]


def test_v502_zip_uncompressed_total_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_IMPORT_MAX_FILE_SIZE_MB", "1")
    monkeypatch.setenv("WORKBENCH_IMPORT_MAX_BATCH_SIZE_MB", "10")
    monkeypatch.setenv("WORKBENCH_IMPORT_MAX_TOTAL_SIZE", "1000000")
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    body = "x" * (600 * 1024)
    zip_bytes = _zip_bytes({
        "a.eml": _eml("a", body),
        "b.eml": _eml("b", body),
    }, compression=zipfile.ZIP_STORED)
    r = _upload(client, app, [("files", ("total.zip", zip_bytes, "application/zip"))])
    assert r.status_code == 200
    batch = _batches(db_path)[0]
    assert batch["accepted_files"] == 1
    assert batch["rejected_files"] == 1
    rows = _rows(db_path, "SELECT status, error_code FROM import_files WHERE import_id=? ORDER BY id",
                 (batch["import_id"],))
    assert sorted((r["status"], r["error_code"]) for r in rows) == [
        ("accepted", ""), ("rejected", "TOTAL_SIZE_EXCEEDED")
    ]


# ----------------------------------------------------------------------
# P1-2: recovery model
# ----------------------------------------------------------------------
def _import_three(app, client, prefix: str = "r") -> str:
    r = _upload(client, app, [
        ("files", (f"{prefix}-a.eml", _eml(f"{prefix}-a"), "message/rfc822")),
        ("files", (f"{prefix}-b.eml", _eml(f"{prefix}-b"), "message/rfc822")),
        ("files", (f"{prefix}-c.eml", _eml(f"{prefix}-c"), "message/rfc822")),
    ])
    assert r.status_code == 200
    return _batches(app.state.db_path)[0]["import_id"]


def test_v502_interrupted_job_recovery_reopens_batch(tmp_path):
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    import_id = _import_three(app, client, "int")
    runtime = JobRuntimeConfig(llm_enabled=False, llm_mode="template", llm_profile_id="off")
    with Database(db_path) as db:
        repo = WorkbenchRepository(db.conn)
        files = repo.list_ready_files(import_id)
        assert len(files) == 3
        job_id = repo.create_job(import_id, runtime, len(files))
        repo.add_job_items(job_id, files)
        repo.mark_batch_processing(import_id)
        repo.set_job_status(job_id, "RUNNING")
        items = repo.list_job_items(job_id)
        repo.update_job_item(items[0]["id"], "COMPLETED", email_id="email-int-a")
        repo.update_job_item(items[1]["id"], "RUNNING")
        # items[2] stays PENDING
        recovered = repo.recover_interrupted_jobs()
        assert recovered >= 1
        assert repo.get_job(job_id)["status"] == "INTERRUPTED"
        batch = repo.get_import_batch(import_id)
        assert batch["status"] == "READY"
        assert batch["accepted_files"] == 2
        statuses = {r["id"]: r["status"] for r in db.query(
            "SELECT id, status FROM import_files WHERE import_id=? ORDER BY id", (import_id,))}
        ordered_ids = [f["id"] for f in files]
        assert statuses[ordered_ids[0]] == "completed"
        assert statuses[ordered_ids[1]] == "accepted"
        assert statuses[ordered_ids[2]] == "accepted"
        # 新 Job 只能包含 B/C。
        new_job_id = repo.start_job_for_batch(import_id, runtime)
        new_items = repo.list_job_items(new_job_id)
        assert [i["import_file_id"] for i in new_items] == ordered_ids[1:]


def test_v502_cancelled_job_recovery_reopens_batch(tmp_path):
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    import_id = _import_three(app, client, "can")
    runtime = JobRuntimeConfig(llm_enabled=False, llm_mode="template", llm_profile_id="off")
    with Database(db_path) as db:
        repo = WorkbenchRepository(db.conn)
        files = repo.list_ready_files(import_id)
        job_id = repo.create_job(import_id, runtime, len(files))
        repo.add_job_items(job_id, files)
        repo.mark_batch_processing(import_id)
        repo.set_job_status(job_id, "CANCELLED")
        items = repo.list_job_items(job_id)
        repo.update_job_item(items[0]["id"], "COMPLETED", email_id="email-can-a")
        # B/C remain PENDING
        repo.recover_interrupted_jobs()
        assert repo.get_job(job_id)["status"] == "CANCELLED"
        batch = repo.get_import_batch(import_id)
        assert batch["status"] == "READY"
        assert batch["accepted_files"] == 2
        statuses = {r["id"]: r["status"] for r in db.query(
            "SELECT id, status FROM import_files WHERE import_id=? ORDER BY id", (import_id,))}
        ordered_ids = [f["id"] for f in files]
        assert statuses[ordered_ids[0]] == "completed"
        assert statuses[ordered_ids[1]] == "accepted"
        assert statuses[ordered_ids[2]] == "accepted"


def test_v502_real_cancel_leaves_remaining_ready_and_retries_only_unfinished(tmp_path, monkeypatch):
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    import_id = _import_three(app, client, "realcan")
    first_done = threading.Event()
    allow_first_return = threading.Event()
    holder: dict[str, object] = {}

    class ControlledPipeline:
        def __init__(self):
            self.calls = 0

        def process_file(self, path):
            self.calls += 1
            if self.calls == 1:
                first_done.set()
                allow_first_return.wait(timeout=5)
                return _FakeRec("email-real-a")
            return _FakeRec(f"email-real-{self.calls}")

    monkeypatch.setattr("app.workbench.job_runner.PipelineFactory.load_rule_config", lambda: object())
    monkeypatch.setattr(
        "app.workbench.job_runner.PipelineFactory.create",
        lambda config, runtime, db=None: holder.setdefault("pipe", ControlledPipeline()),
    )

    start = client.post("/jobs/start", data={
        "csrf_token": app.state.csrf_token,
        "import_id": import_id,
        "profile_id": "template",
    })
    assert start.status_code == 200
    job_id = start.json()["job_id"]
    assert first_done.wait(timeout=5)
    cancel = client.post(f"/jobs/{job_id}/cancel", data={"csrf_token": app.state.csrf_token})
    assert cancel.status_code == 200
    allow_first_return.set()
    status = _wait_job(client, job_id)
    assert status["status"] == "CANCELLED", status

    with Database(db_path) as db:
        repo = WorkbenchRepository(db.conn)
        batch = repo.get_import_batch(import_id)
        assert batch["status"] == "READY"
        assert batch["accepted_files"] == 2
        files = repo.list_ready_files(import_id)
        assert len(files) == 2
        # A 已完成，不应再出现在新 Job 中。
        new_job_id = repo.start_job_for_batch(
            import_id, JobRuntimeConfig(llm_enabled=False, llm_mode="template", llm_profile_id="off"))
        new_items = repo.list_job_items(new_job_id)
        assert len(new_items) == 2
        first_ids = {f["id"] for f in files}
        assert {i["import_file_id"] for i in new_items} == first_ids
        # 清理新 Job 的 active 状态，避免测试进程退出时被 recovery 视为异常。
        repo.request_cancel(new_job_id)


def test_v502_failed_item_remains_retryable(tmp_path, monkeypatch):
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    import_id = _import_three(app, client, "fail")
    holder: dict[str, object] = {}

    class FailOncePipeline:
        def __init__(self):
            self.calls = 0

        def process_file(self, path):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("synthetic one-shot failure")
            return _FakeRec(f"email-fail-{self.calls}")

    monkeypatch.setattr("app.workbench.job_runner.PipelineFactory.load_rule_config", lambda: object())
    monkeypatch.setattr(
        "app.workbench.job_runner.PipelineFactory.create",
        lambda config, runtime, db=None: holder.setdefault("pipe", FailOncePipeline()),
    )
    start = client.post("/jobs/start", data={
        "csrf_token": app.state.csrf_token,
        "import_id": import_id,
        "profile_id": "template",
    })
    assert start.status_code == 200
    job_id = start.json()["job_id"]
    status = _wait_job(client, job_id)
    assert status["status"] == "COMPLETED"
    assert status["failed_count"] == 1

    with Database(db_path) as db:
        repo = WorkbenchRepository(db.conn)
        batch = repo.get_import_batch(import_id)
        assert batch["status"] == "READY"
        assert batch["accepted_files"] == 1
        # 再次启动只处理失败的那一封。
        new_job_id = repo.start_job_for_batch(
            import_id, JobRuntimeConfig(llm_enabled=False, llm_mode="template", llm_profile_id="off"))
        new_items = repo.list_job_items(new_job_id)
        assert len(new_items) == 1


# ----------------------------------------------------------------------
# Dedup after completion / all done batch
# ----------------------------------------------------------------------
def test_v502_completed_mail_is_deduped_on_reimport(tmp_path):
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    raw = _eml("completed-dedup")
    r1 = _upload(client, app, [("files", ("A.eml", raw, "message/rfc822"))])
    assert r1.status_code == 200
    import_id = _batches(db_path)[0]["import_id"]
    start = client.post("/jobs/start", data={
        "csrf_token": app.state.csrf_token,
        "import_id": import_id,
        "profile_id": "template",
    })
    assert start.status_code == 200
    status = _wait_job(client, start.json()["job_id"])
    assert status["status"] == "COMPLETED"
    with Database(db_path) as db:
        repo = WorkbenchRepository(db.conn)
        assert repo.get_import_batch(import_id)["status"] == "COMPLETED"
        assert repo.list_ready_files(import_id) == []
    r2 = _upload(client, app, [("files", ("A2.eml", raw, "message/rfc822"))])
    assert r2.status_code == 200
    latest = _batches(db_path)[0]
    assert latest["accepted_files"] == 0
    assert latest["duplicate_files"] == 1
    assert latest["status"] == "EMPTY"


def test_v502_legacy_interrupted_processing_batch_auto_recovered(tmp_path):
    """兼容 V5.0.1 legacy: Batch PROCESSING + Job INTERRUPTED + accepted import_files。"""
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    import_id = _import_three(app, client, "legacy")
    runtime = JobRuntimeConfig(llm_enabled=False, llm_mode="template", llm_profile_id="off")
    with Database(db_path) as db:
        repo = WorkbenchRepository(db.conn)
        files = repo.list_ready_files(import_id)
        job_id = repo.create_job(import_id, runtime, len(files))
        repo.add_job_items(job_id, files)
        repo.mark_batch_processing(import_id)
        # legacy 状态：Job 已经 INTERRUPTED，但没有 V5.0.2 recovery 逻辑。
        repo.set_job_status(job_id, "INTERRUPTED", error="WORKBENCH_RESTARTED")
        recovered = repo.recover_interrupted_jobs()
        # active_rows 可能为 0，但 legacy sweep 必须把 Batch 拉回 READY。
        batch = repo.get_import_batch(import_id)
        assert batch["status"] == "READY"
        assert batch["accepted_files"] == 3
        assert repo.count_retryable_import_files(import_id) == 3
        assert recovered >= 0
        new_job_id = repo.start_job_for_batch(import_id, runtime)
        assert len(repo.list_job_items(new_job_id)) == 3
