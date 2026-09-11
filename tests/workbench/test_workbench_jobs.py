# -*- coding: utf-8 -*-
"""V5.0 Workbench: import -> job -> ScreeningPipeline integration."""
from __future__ import annotations

import io
import sys
import time
import zipfile
from email import policy
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi.testclient import TestClient

from app.dashboard.server import create_app
from app.storage.database import Database
from app.workbench.job_repository import WorkbenchRepository


def _eml(subject: str, body: str) -> bytes:
    m = EmailMessage(policy=policy.default)
    m["Subject"] = subject
    m["From"] = "source@example.com"
    m["To"] = "tips@example.com"
    m["Date"] = "Mon, 01 Sep 2026 08:00:00 +0800"
    m.set_content(body)
    return m.as_bytes()


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("001/message.eml", _eml("采购案件", "RAW_BODY_CANARY_8192 采购收贿 80万元 银行流水 檢舉"))
        z.writestr("002/message.eml", _eml("停水投诉", "RAW_BODY_CANARY_8192 同一区停水反复5次 500户 1999未改善"))
    return buf.getvalue()


def _app(tmp_path):
    db_path = tmp_path / "w.db"
    Database(db_path).close()
    app = create_app(db_path, import_staging_root=tmp_path / "staging")
    return app, db_path


def test_import_start_job_complete_and_persist(tmp_path):
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    r = client.post("/import", data={"csrf_token": app.state.csrf_token},
                    files={"file": ("batch.zip", _zip_bytes(), "application/zip")})
    assert r.status_code == 200
    with Database(db_path) as db:
        repo = WorkbenchRepository(db.conn)
        batch = repo.list_import_batches(status="READY")[0]
        assert batch["accepted_files"] == 2
        start = client.post("/jobs/start", data={
            "csrf_token": app.state.csrf_token,
            "import_id": batch["import_id"],
            "profile_id": "template",
        })
        assert start.status_code == 200
        job_id = start.json()["job_id"]
        status = None
        for _ in range(80):
            status = client.get(f"/jobs/{job_id}/status").json()
            if status["status"] in ("COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"):
                break
            time.sleep(0.15)
        assert status["status"] == "COMPLETED", status
        assert status["processed_count"] == 2
        assert status["success_count"] == 2
        assert len(db.query("SELECT * FROM scores")) == 2
        assert db.query("SELECT * FROM emails") != []
    # API list must not leak raw body; reader must show raw body
    api = client.get("/api/emails?page_size=100").text
    assert "RAW_BODY_CANARY_8192" not in api
    with Database(db_path) as db:
        eid = db.query("SELECT email_id, body_text FROM emails")[0]["email_id"]
    reader = client.get(f"/emails/{eid}/reader")
    assert reader.status_code == 200
    assert "邮件正文" in reader.text


def test_single_worker_and_cancel(tmp_path):
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    client.post("/import", data={"csrf_token": app.state.csrf_token},
                files={"file": ("batch.zip", _zip_bytes(), "application/zip")})
    with Database(db_path) as db:
        repo = WorkbenchRepository(db.conn)
        batch = repo.list_import_batches(status="READY")[0]
    start = client.post("/jobs/start", data={
        "csrf_token": app.state.csrf_token,
        "import_id": batch["import_id"],
        "profile_id": "template",
    })
    assert start.status_code == 200
    # 第二个并发 Start 应被拒绝
    second = client.post("/jobs/start", data={
        "csrf_token": app.state.csrf_token,
        "import_id": batch["import_id"],
        "profile_id": "template",
    })
    assert second.status_code == 409


def test_restart_marks_running_interrupted(tmp_path):
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    client.post("/import", data={"csrf_token": app.state.csrf_token},
                files={"file": ("batch.zip", _zip_bytes(), "application/zip")})
    with Database(db_path) as db:
        repo = WorkbenchRepository(db.conn)
        batch = repo.list_import_batches(status="READY")[0]
        runtime = __import__("app.workbench.models", fromlist=["JobRuntimeConfig"]).JobRuntimeConfig(
            llm_enabled=False, llm_mode="template", llm_profile_id="off")
        repo.create_job(batch["import_id"], runtime, 1)
    # 新 app 启动恢复
    create_app(db_path, import_staging_root=tmp_path / "staging")
    with Database(db_path) as db:
        job = WorkbenchRepository(db.conn).list_jobs()[0]
        assert job["status"] == "INTERRUPTED"


def _zip_bytes_n(count: int) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i in range(count):
            z.writestr(f"{i:03d}/message.eml", _eml(f"邮件 {i}", "停水投诉 同一区停水反复5次 500户 1999未改善"))
    return buf.getvalue()


def test_cancel_is_checked_per_file(tmp_path, monkeypatch):
    """取消必须在**每封邮件之间**被检查，而不是只在任务开始时检查一次。

    使用慢速假 pipeline，确保取消请求落在任务执行中途。
    """
    from app.workbench import job_runner as job_runner_mod
    from app.workbench.pipeline_factory import PipelineFactory

    app, db_path = _app(tmp_path)
    client = TestClient(app)
    client.post("/import", data={"csrf_token": app.state.csrf_token},
                files={"file": ("batch.zip", _zip_bytes_n(6), "application/zip")})

    class _SlowPipeline:
        def process_file(self, path):
            time.sleep(0.35)
            return None      # 视为 duplicate，但仍计入 processed_count

    monkeypatch.setattr(PipelineFactory, "create",
                        staticmethod(lambda *a, **kw: _SlowPipeline()))

    with Database(db_path) as db:
        batch = WorkbenchRepository(db.conn).list_import_batches(status="READY")[0]
        import_id = batch["import_id"]
        assert batch["accepted_files"] == 6
    start = client.post("/jobs/start", data={
        "csrf_token": app.state.csrf_token,
        "import_id": import_id,
        "profile_id": "template",
    })
    assert start.status_code == 200
    job_id = start.json()["job_id"]

    # 等到任务真正开跑、且已经处理完至少一封，再请求取消。
    # 不使用固定 sleep：CI 机器负载不同，固定等待会让断言随负载变成 flaky。
    deadline = time.time() + 20
    started = False
    while time.time() < deadline:
        snap = client.get(f"/jobs/{job_id}/status").json()
        if snap["status"] == "RUNNING" and snap["processed_count"] >= 1:
            started = True
            break
        if snap["status"] in ("COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"):
            break
        time.sleep(0.02)
    assert started, "任务未能进入 RUNNING 并处理至少一封邮件"
    cancel = client.post(f"/jobs/{job_id}/cancel",
                         data={"csrf_token": app.state.csrf_token})
    assert cancel.status_code == 200

    status = None
    deadline = time.time() + 20
    while time.time() < deadline:
        status = client.get(f"/jobs/{job_id}/status").json()
        if status["status"] in ("COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"):
            break
        time.sleep(0.1)
    assert status["status"] == "CANCELLED", status
    # 关键断言：取消后必须真的中途停下，不能把 6 封全部跑完
    assert status["processed_count"] < 6, status
    # 且确认是「跑到一半」被取消，而不是一开始就退出
    assert status["processed_count"] >= 1, status
    assert status["cancel_requested"] == 1

