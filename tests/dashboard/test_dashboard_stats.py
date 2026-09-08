# -*- coding: utf-8 -*-
"""Dashboard stats gate."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.dashboard.server import create_app
from fastapi.testclient import TestClient

from .db_utils import close_db, create_schema, insert_email, insert_score, insert_review_queue


def _insert_review(db, email_id, status):
    db.execute(
        """INSERT OR REPLACE INTO dashboard_reviews
           (email_id, review_status, editor_note, updated_at, created_at)
           VALUES (?,?,?,?,?)""",
        (email_id, status, "", "2026-01-01T00:00:00", "2026-01-01T00:00:00"))


def _make_db(path):
    today = date.today().isoformat()
    db = create_schema(path)
    rows = [
        ("e_s", "S", 90, "POLITICAL", 0),
        ("e_a1", "A", 85, "GOVERNANCE", 76),
        ("e_a2", "A", 89, "MIXED", 80),
        ("e_b1", "B", 74, "GOVERNANCE", 70),
        ("e_b2", "B", 70, "POLITICAL", 0),
        ("e_b3", "B", 68, "POLITICAL", 0),
    ]
    for eid, pri, score, track, gov in rows:
        insert_email(db, eid, f"subject {eid}", processed_at=f"{today}T10:00:00")
        insert_score(db, eid, pri, score, primary_track=track, governance_score=gov)
        insert_review_queue(db, eid, pri, score, f"summary {eid}")
    # 两封人工已处理，使 pending = 4
    _insert_review(db, "e_b2", "VERIFIED")
    _insert_review(db, "e_b3", "LOW_VALUE")
    close_db(db)
    return today


def test_stats_gate(tmp_path):
    path = tmp_path / "dash.db"
    _make_db(path)
    client = TestClient(create_app(path))
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["today_total"] == 6
    assert data["today_sa"] == 3  # S + A + A
    assert data["today_governance"] == 3  # a1,a2,b1? a2 mixed has gov >0, so 3
    assert data["pending_review"] == 4
    assert data["track_counts"]["POLITICAL"] == 3
    assert data["track_counts"]["GOVERNANCE"] == 2
    assert data["track_counts"]["MIXED"] == 1
