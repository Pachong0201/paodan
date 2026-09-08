# -*- coding: utf-8 -*-
"""V4.2.1 Dashboard privacy leakage / entity policy."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi.testclient import TestClient

from app.dashboard.server import create_app

from .db_utils import (
    close_db, create_schema, insert_attachment, insert_email, insert_llm,
    insert_named, insert_release, insert_review_queue, insert_score,
)

CANARIES = [
    "王小明",
    "secret.source.2026@example.com",
    "0912345678",
    "A123456789",
    "812345678901234567",
    "<super-secret-message@example.com>",
    r"D:\Secret\Whistleblower\raw.eml",
    "/home/private/source/raw.eml",
    "line-source-secret-8899",
    "sk-super-secret-test-token",
    "台北市信义区松高路1号",
]


def _build(path):
    db = create_schema(path)
    today = date.today().isoformat()
    eid = "privacy-dashboard-01"
    insert_email(db, eid, "王小明0912345678爆料", processed_at=f"{today}T08:00:00",
                 sender="sender@example.com", body="RAW_BODY_CANARY_8192")
    unified = {
        "political_categories": ["A06"], "governance_categories": ["G03"],
        "political_patterns": ["P01"], "governance_patterns": ["GP01"],
        "political_score": 55, "governance_score": 84, "primary_track": "MIXED",
        "entities": [
            {"text": "王小明", "type": "PERSON"},
            {"text": "测试科技公司", "type": "COMPANY"},
        ],
    }
    insert_score(db, eid, "S", 84, primary_track="MIXED", governance_score=84, unified=unified)
    insert_review_queue(db, eid, "S", 84,
                        "爆料人王小明 secret.source.2026@example.com",
                        ["联系王小明核验812345678901234567"])
    insert_llm(db, eid, {"reason_for_attention": "王小明通过LINE line-source-secret-8899提供",
                         "one_sentence_summary": "王小明0912345678爆料"})
    insert_release(db, eid, "R3")
    db.execute(
        """UPDATE release_recommendations SET reason=?
           WHERE email_id=?""",
        ("建议联系王小明 0912345678 secret.source.2026@example.com", eid))
    insert_named(db, eid, "media", "镜周刊", reason="王小明通过LINE line-source-secret-8899提供")
    insert_attachment(db, eid, "王小明_A123456789_证据.pdf", "pdf", "success")
    db.execute("UPDATE emails SET message_id='<super-secret-message@example.com>', "
               "source_path='D:\\\\Secret\\\\Whistleblower\\\\raw.eml' WHERE email_id=?", (eid,))
    close_db(db)
    return create_app(path)


def test_privacy_pages_and_api_no_leak(tmp_path):
    app = _build(tmp_path / "d.db")
    client = TestClient(app)
    pages = ["/", "/emails/privacy-dashboard-01", "/review",
             "/api/emails?page_size=100"]
    for path in pages:
        text = client.get(path).text
        for c in CANARIES:
            assert c not in text, (path, c)


def test_public_company_and_v3_entity_visible(tmp_path):
    app = _build(tmp_path / "d.db")
    client = TestClient(app)
    html = client.get("/emails/privacy-dashboard-01").text
    assert "测试科技公司" in html
    assert "镜周刊" in html
    assert "王小明" not in html


def test_viewmodel_no_forbidden_fields():
    from app.dashboard.view_models import EmailDetail, EmailListItem
    fields = set(EmailListItem.__annotations__) | set(EmailDetail.__annotations__)
    forbidden = {"sender", "sender_email", "recipients", "cc", "message_id",
                 "source_path", "cached_path", "body_text", "combined_text",
                 "attachment_text"}
    assert not (fields & forbidden)
