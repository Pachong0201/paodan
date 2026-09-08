# -*- coding: utf-8 -*-
"""Dashboard detail page gate."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi.testclient import TestClient

from app.dashboard.server import create_app

from .db_utils import (
    close_db, create_schema, insert_analysis, insert_attachment, insert_email,
    insert_governance, insert_llm, insert_named, insert_release,
    insert_review_queue, insert_score,
)


def test_detail_page_and_missing(tmp_path):
    db = create_schema(tmp_path / "d.db")
    today = date.today().isoformat()
    eid = "detail1"
    insert_email(db, eid, "市府停電投訴 <script>alert(1)</script>", processed_at=f"{today}T08:00:00")
    unified = {
        "political_categories": ["A06"], "governance_categories": ["G03"],
        "political_patterns": ["P01"], "governance_patterns": ["GP01"],
        "political_score": 70, "governance_score": 80, "primary_track": "MIXED",
        "evidence_stage": "E2", "evidence_shapes": ["OFFICIAL_DOCUMENT"],
        "money": [{"amount": 800000, "currency": "TWD"}],
        "entities": [{"text": "某公司", "type": "COMPANY"}],
    }
    insert_score(db, eid, "A", 80, primary_track="MIXED", governance_score=80, unified=unified)
    insert_review_queue(db, eid, "A", 80, "郵件指稱市府停電影響社區", ["核對停電紀錄"])
    insert_llm(db, eid, {"evidence_stage": "E2", "verification_targets": ["核對停電紀錄"],
                         "one_sentence_summary": "郵件指稱停電影響", "reason_for_attention": "值得查"})
    insert_governance(db, eid, ["G03"], 80)
    insert_release(db, eid, "R3")
    insert_named(db, eid, "media", "鏡周刊")
    insert_attachment(db, eid, "證據.pdf", "pdf", "success", [])
    insert_analysis(db, eid)
    close_db(db)

    client = TestClient(create_app(tmp_path / "d.db"))
    resp = client.get(f"/emails/{eid}")
    assert resp.status_code == 200
    html = resp.text
    assert "市府停電投訴" in html
    assert "G03" in html and "A06" in html
    assert "核對停電紀錄" in html
    assert "鏡周刊" in html
    assert "證據.pdf" in html
    # no raw body / attachment text / sender / path
    for sensitive in ("RAW_BODY_CANARY_8192", "ATTACHMENT_PRIVATE_CANARY_E817AC21",
                      "sender@example.com", "data/inbox/x.eml"):
        assert sensitive not in html
    missing = client.get("/emails/not-found")
    assert missing.status_code == 404
