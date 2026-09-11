# -*- coding: utf-8 -*-
"""Dashboard list / sort / pagination."""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi.testclient import TestClient

from app.dashboard.server import create_app

from .db_utils import close_db, create_schema, insert_email, insert_review_queue, insert_score


def _basic(tmp_path, n=6):
    db = create_schema(tmp_path / "d.db")
    today = date.today().isoformat()
    for i in range(n):
        eid = f"e{i:03d}"
        insert_email(db, eid, f"subject {eid}", processed_at=f"{today}T08:00:00")
        insert_score(db, eid, "B", 60 + i)
        insert_review_queue(db, eid, "B", 60 + i, f"summary {eid}")
    close_db(db)
    return create_app(tmp_path / "d.db")


def test_priority_sort_gate(tmp_path):
    db = create_schema(tmp_path / "d.db")
    today = date.today().isoformat()
    for eid, pri, score in [("a", "A", 85), ("s", "S", 90), ("b", "B", 74), ("a2", "A", 89)]:
        insert_email(db, eid, f"subject {eid}", processed_at=f"{today}T10:00:00")
        insert_score(db, eid, pri, score)
        insert_review_queue(db, eid, pri, score, f"summary {eid}")
    close_db(db)
    client = TestClient(create_app(tmp_path / "d.db"))
    resp = client.get("/", params={"priority": "S,A,B,C,D"})
    assert resp.status_code == 200
    html = resp.text
    # order in html: subjects should appear s,a2,a,b
    pos_s = html.find('href="/emails/s/reader">subject s')
    pos_a2 = html.find('href="/emails/a2/reader">subject a2')
    pos_a = html.find('href="/emails/a/reader">subject a')
    pos_b = html.find('href="/emails/b/reader">subject b')
    assert -1 not in (pos_s, pos_a2, pos_a, pos_b)
    assert pos_s < pos_a2 < pos_a < pos_b


def test_pagination_gate(tmp_path):
    db = create_schema(tmp_path / "d.db")
    today = date.today().isoformat()
    for i in range(105):
        eid = f"p{i:03d}"
        insert_email(db, eid, f"paginate {eid}", processed_at=f"{today}T08:00:00")
        insert_score(db, eid, "D", 10 + i % 30)
    close_db(db)
    client = TestClient(create_app(tmp_path / "d.db"))
    counts = []
    for page in range(1, 5):
        resp = client.get("/", params={"priority": "D", "page": page, "page_size": 30})
        html = resp.text
        counts.append(html.count("<tr>") - 1 if html.count("<tr>") else 0)
    # <tr> appears header+rows; rows count approx counts list
    assert counts[0] == 30
    assert counts[1] == 30
    assert counts[2] == 30
    assert counts[3] == 15
