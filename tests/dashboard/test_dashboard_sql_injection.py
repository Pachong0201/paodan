# -*- coding: utf-8 -*-
"""Dashboard SQL injection / path traversal."""
from __future__ import annotations

import sqlite3
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
    for i in range(5):
        eid = f"e{i}"
        insert_email(db, eid, f"subject {eid}", processed_at=f"{today}T08:00:00")
        insert_score(db, eid, "A", 80 + i)
        insert_review_queue(db, eid, "A", 80 + i, f"summary {eid}")
    close_db(db)
    return create_app(path)


def test_sql_injection_search_safe(tmp_path):
    app = _seed(tmp_path / "d.db")
    client = TestClient(app)
    for q in ("' OR 1=1 --", "%'; DROP TABLE emails; --"):
        r = client.get("/", params={"q": q})
        assert r.status_code == 200
        # 不应出现全部 5 封
        assert "subject e4" not in r.text or "subject e0" not in r.text or "subject e3" not in r.text
    conn = sqlite3.connect(str(tmp_path / "d.db"))
    count = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    conn.close()
    assert count == 5


def test_path_traversal_email_id(tmp_path):
    app = _seed(tmp_path / "d.db")
    client = TestClient(app)
    for path in ("/emails/../../etc/passwd", "/emails/..%2F..%2Fetc%2Fpasswd"):
        assert client.get(path).status_code == 404
