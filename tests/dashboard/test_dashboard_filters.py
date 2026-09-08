# -*- coding: utf-8 -*-
"""Dashboard filter/search gates."""
from __future__ import annotations

import json
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
    seed = [
        ("f_a", "A", 85, "POLITICAL", ["A06"], [], "市府採購案"),
        ("f_g", "A", 82, "GOVERNANCE", [], ["G03"], "市府停電投訴"),
        ("f_m", "B", 75, "MIXED", ["A09"], ["G01"], "混合案件"),
    ]
    for eid, pri, score, track, pol, gov, summ in seed:
        insert_email(db, eid, f"subject {eid}", processed_at=f"{today}T08:00:00")
        unified = {"political_categories": pol, "governance_categories": gov,
                   "primary_track": track, "political_score": score, "governance_score": 0}
        insert_score(db, eid, pri, score, primary_track=track,
                     governance_score=76 if gov else 0, unified=unified)
        insert_review_queue(db, eid, pri, score, summ)
    close_db(db)
    return create_app(path)


def test_priority_filter(tmp_path):
    client = TestClient(_seed(tmp_path / "d.db"))
    html = client.get("/", params={"priority": "A"}).text
    assert "f_a" in html and "f_g" in html
    assert "f_m" not in html


def test_track_filter(tmp_path):
    client = TestClient(_seed(tmp_path / "d.db"))
    html = client.get("/", params={"track": "GOVERNANCE"}).text
    assert "f_g" in html and "f_a" not in html and "f_m" not in html


def test_category_filter(tmp_path):
    client = TestClient(_seed(tmp_path / "d.db"))
    html = client.get("/", params={"category": "G03"}).text
    assert "f_g" in html and "f_a" not in html


def test_search_gate(tmp_path):
    client = TestClient(_seed(tmp_path / "d.db"))
    assert "f_a" in client.get("/", params={"q": "市府"}).text
    assert "f_a" not in client.get("/", params={"q": "不存在詞"}).text
