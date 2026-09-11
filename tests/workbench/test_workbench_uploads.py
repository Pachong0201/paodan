# -*- coding: utf-8 -*-
"""V5.0 multi EML upload and timezone/address/phone gates."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi.testclient import TestClient

from app.dashboard.server import create_app
from app.storage.database import Database
from app.workbench.job_repository import WorkbenchRepository
from tests.workbench.test_import_nested_zip import _eml


def _app(tmp_path):
    db_path = tmp_path / "w.db"
    Database(db_path).close()
    app = create_app(db_path, import_staging_root=tmp_path / "staging")
    return app, db_path


def test_multi_eml_upload_one_batch(tmp_path):
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    r = client.post("/import", data={"csrf_token": app.state.csrf_token},
                    files=[("files", ("a.eml", _eml("a"), "message/rfc822")),
                           ("files", ("b.eml", _eml("b"), "message/rfc822"))])
    assert r.status_code == 200
    with Database(db_path) as db:
        batch = WorkbenchRepository(db.conn).list_import_batches()[0]
    assert batch["accepted_files"] == 2


def test_timezone_boundary_counts_local_today(tmp_path):
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    tz = ZoneInfo("Asia/Shanghai")
    local_now = datetime.now(tz)
    # local 01:00 today -> UTC previous day 17:00
    local_0100 = local_now.replace(hour=1, minute=0, second=0, microsecond=0)
    utc_ts = local_0100.astimezone(ZoneInfo("UTC")).isoformat(timespec="seconds")
    with Database(db_path) as db:
        db.execute("""INSERT INTO emails(email_id, subject, body_text, processed_at)
                      VALUES (?,?,?,?)""", ("tz1", "tz", "body", utc_ts))
        db.execute("""INSERT INTO scores(email_id, final_score, priority, primary_track)
                      VALUES (?,?,?,?)""", ("tz1", 90.0, "S", "POLITICAL"))
    stats = client.get("/api/stats").json()
    assert stats["today_total"] >= 1


def test_taiwan_address_phone_redacted_in_dashboard(tmp_path):
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    with Database(db_path) as db:
        db.execute("""INSERT INTO emails(email_id, subject, body_text, processed_at)
                      VALUES (?,?,?,?)""", ("addr1", "台北市信义区松高路1号",
                                            "02-1234-5678", datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds")))
        db.execute("""INSERT INTO scores(email_id, final_score, priority, primary_track)
                      VALUES (?,?,?,?)""", ("addr1", 85.0, "A", "POLITICAL"))
        db.execute("""INSERT INTO review_queue(email_id, priority, final_score, summary_zh, queued_at)
                      VALUES (?,?,?,?,?)""", ("addr1", "A", 85.0, "台北市信义区松高路1号 0912-345-678", ""))
    html = client.get("/").text
    assert "台北市信义区松高路1号" not in html
    assert "02-1234-5678" not in html
    assert "0912-345-678" not in html
