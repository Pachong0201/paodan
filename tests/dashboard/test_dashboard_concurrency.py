# -*- coding: utf-8 -*-
"""SQLite concurrency and large dataset gate."""
from __future__ import annotations

import concurrent.futures
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi.testclient import TestClient

from app.dashboard.server import create_app

from .db_utils import close_db, create_schema, insert_email, insert_review_queue, insert_score


def test_large_dataset_pagination(tmp_path):
    db = create_schema(tmp_path / "d.db")
    today = date.today().isoformat()
    email_rows = [(f"big{i:05d}", f"big {i}", "sender@example.com",
                   f"{today}T08:00:00") for i in range(10000)]
    db.executemany(
        """INSERT OR REPLACE INTO emails
           (email_id, subject, sender, processed_at)
           VALUES (?,?,?,?)""", email_rows)
    import json
    score_rows = [(f"big{i:05d}", 10 + i % 20, "D", "NONE", json.dumps({
        "political_categories": [], "governance_categories": [],
        "primary_track": "NONE"}), 0) for i in range(10000)]
    db.executemany(
        """INSERT OR REPLACE INTO scores
           (email_id, final_score, priority, primary_track, unified_json, governance_score)
           VALUES (?,?,?,?,?,?)""", score_rows)
    close_db(db)
    client = TestClient(create_app(tmp_path / "d.db"))
    resp = client.get("/", params={"priority": "D", "page_size": 100, "page": 1})
    assert resp.status_code == 200
    html = resp.text
    # only one page, not all 10k subjects
    rows = max(0, html.count("<tr>") - 1)
    assert rows <= 100
    assert "big " in html


def test_sqlite_concurrency(tmp_path):
    db = create_schema(tmp_path / "d.db")
    today = date.today().isoformat()
    insert_email(db, "c0", "concurrency", processed_at=f"{today}T08:00:00")
    insert_score(db, "c0", "S", 95)
    insert_review_queue(db, "c0", "S", 95, "summary")
    close_db(db)
    app = create_app(tmp_path / "d.db")
    token = app.state.csrf_token

    def get_home(_):
        return TestClient(app).get("/").status_code

    def post_review(i):
        return TestClient(app).post("/emails/c0/review", data={
            "status": "PRIORITY", "editor_note": f"note {i}", "csrf_token": token}).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as ex:
        get_futs = [ex.submit(get_home, i) for i in range(10)]
        post_futs = [ex.submit(post_review, i) for i in range(5)]
        results = [f.result() for f in get_futs + post_futs]
    assert results == [200] * len(results)
