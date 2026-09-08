# -*- coding: utf-8 -*-
"""Page overflow normalization."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi.testclient import TestClient

from app.dashboard.server import create_app

from .db_utils import close_db, create_schema, insert_email, insert_score


def test_page_overflow_returns_last_page(tmp_path):
    db = create_schema(tmp_path / "d.db")
    today = date.today().isoformat()
    for i in range(105):
        eid = f"o{i:03d}"
        insert_email(db, eid, f"overflow {eid}", processed_at=f"{today}T08:00:00")
        insert_score(db, eid, "D", 10 + i % 30)
    close_db(db)
    client = TestClient(create_app(tmp_path / "d.db"))
    resp = client.get("/", params={"priority": "D", "page": 999, "page_size": 30})
    assert resp.status_code == 200
    html = resp.text
    rows = max(0, html.count("<tr>") - 1)
    assert rows == 15
