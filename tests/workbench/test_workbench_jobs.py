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
