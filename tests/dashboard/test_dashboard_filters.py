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


def _shows(html: str, email_id: str) -> bool:
    """按「邮件行链接」判断，而不是裸 id 子串。

    页面内嵌随机 CSRF token（secrets.token_urlsafe(24)），裸 id 子串
    （如 "f_a"）理论上可能恰好命中，造成偶发假失败。
    /emails/<id> 只可能来自真实邮件行。
    """
    return f"/emails/{email_id}" in html


def test_priority_filter(tmp_path):
    client = TestClient(_seed(tmp_path / "d.db"))
    html = client.get("/", params={"priority": "A"}).text
    assert _shows(html, "f_a") and _shows(html, "f_g")
    assert not _shows(html, "f_m")


def test_track_filter(tmp_path):
    client = TestClient(_seed(tmp_path / "d.db"))
    html = client.get("/", params={"track": "GOVERNANCE"}).text
    assert _shows(html, "f_g") and not _shows(html, "f_a") and not _shows(html, "f_m")


def test_category_filter(tmp_path):
    client = TestClient(_seed(tmp_path / "d.db"))
    html = client.get("/", params={"category": "G03"}).text
    assert _shows(html, "f_g") and not _shows(html, "f_a")


def test_search_gate(tmp_path):
    client = TestClient(_seed(tmp_path / "d.db"))
    assert _shows(client.get("/", params={"q": "市府"}).text, "f_a")
    assert not _shows(client.get("/", params={"q": "不存在詞"}).text, "f_a")
