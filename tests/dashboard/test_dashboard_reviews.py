# -*- coding: utf-8 -*-
"""Dashboard review gates."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi.testclient import TestClient

from app.dashboard.server import create_app

from .db_utils import close_db, create_schema, insert_email, insert_review_queue, insert_score


def _seed(path):
    db = create_schema(path)
    today = date.today().isoformat()
    insert_email(db, "rev1", "review email", processed_at=f"{today}T08:00:00")
    insert_score(db, "rev1", "A", 82)
    insert_review_queue(db, "rev1", "A", 82, "summary")
    close_db(db)
    return create_app(path)


def test_review_save_and_isolate(tmp_path):
    app = _seed(tmp_path / "d.db")
    client = TestClient(app)
    token = app.state.csrf_token
    r = client.post("/emails/rev1/review", data={"status": "PRIORITY",
                                                 "editor_note": "已聯繫來源",
                                                 "csrf_token": token})
    assert r.status_code == 200
    data = r.json()
    assert data["review_status"] == "PRIORITY"
    detail = client.get("/emails/rev1").text
    assert "已聯繫來源" in detail
    # 不改变自动评分
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "d.db"))
    row = conn.execute("SELECT final_score, priority FROM scores WHERE email_id='rev1'").fetchone()
    conn.close()
    assert row == (82.0, "A")


def test_invalid_status(tmp_path):
    app = _seed(tmp_path / "d.db")
    client = TestClient(app)
    r = client.post("/emails/rev1/review", data={"status": "HACKED", "editor_note": "x",
                                                 "csrf_token": app.state.csrf_token})
    assert r.status_code == 400
