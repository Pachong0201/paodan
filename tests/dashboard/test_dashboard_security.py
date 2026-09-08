# -*- coding: utf-8 -*-
"""Dashboard security / XSS / leakage / trusted host / headers."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi.testclient import TestClient

from app.dashboard.server import create_app

from .db_utils import close_db, create_schema, insert_email, insert_review_queue, insert_score


def _seed(path, subject="安全測試", sender="secret.source@example.com", body="RAW_BODY_CANARY_8192"):
    db = create_schema(path)
    today = date.today().isoformat()
    eid = "sec1"
    insert_email(db, eid, subject, processed_at=f"{today}T08:00:00", sender=sender, body=body)
    db.execute("UPDATE emails SET message_id='<message-id@example.com>', source_path='D:\\\\Secret\\\\x.eml' WHERE email_id='sec1'")
    insert_score(db, eid, "S", 90)
    insert_review_queue(db, eid, "S", 90, "安全摘要")
    close_db(db)
    return create_app(path)


def test_sensitive_fields_not_in_home_or_detail(tmp_path):
    app = _seed(tmp_path / "d.db")
    client = TestClient(app)
    for path in ("/", "/emails/sec1"):
        html = client.get(path).text
        for bad in ("secret.source@example.com", "<message-id@example.com>",
                    "D:\\Secret\\x.eml", "RAW_BODY_CANARY_8192",
                    "0912345678", "812345678901234567"):
            assert bad not in html, (path, bad)


def test_xss_subject_and_summary(tmp_path):
    db = create_schema(tmp_path / "d.db")
    today = date.today().isoformat()
    insert_email(db, "x1", "<script>alert(1)</script>", processed_at=f"{today}T08:00:00")
    insert_score(db, "x1", "A", 85)
    insert_review_queue(db, "x1", "A", 85, "<script>alert('xss')</script>")
    close_db(db)
    client = TestClient(create_app(tmp_path / "d.db"))
    home = client.get("/", params={"q": "alert"}).text
    assert "<script>alert(1)</script>" not in home
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in home


def test_stored_xss_editor_note(tmp_path):
    app = _seed(tmp_path / "d.db")
    client = TestClient(app)
    client.post("/emails/sec1/review", data={
        "status": "PRIORITY", "editor_note": "<img src=x onerror=alert(1)>",
        "csrf_token": app.state.csrf_token})
    html = client.get("/emails/sec1").text
    assert "<img src=x onerror=alert(1)>" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_trusted_host_and_headers(tmp_path):
    app = _seed(tmp_path / "d.db")
    client = TestClient(app)
    assert client.get("/", headers={"host": "evil.example.com"}).status_code == 400
    r = client.get("/")
    for h in ("X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy", "Cache-Control",
              "Content-Security-Policy"):
        assert h in r.headers


def test_no_external_network(tmp_path):
    app = _seed(tmp_path / "d.db")
    client = TestClient(app)
    with patch("requests.get", side_effect=AssertionError("network")) as rg, \
         patch("requests.post", side_effect=AssertionError("network")) as rp, \
         patch("httpx.get", side_effect=AssertionError("network")) as hg, \
         patch("httpx.post", side_effect=AssertionError("network")) as hp:
        assert client.get("/").status_code == 200
        assert client.get("/emails/sec1").status_code == 200
        assert client.post("/emails/sec1/review", data={
            "status": "VERIFY", "editor_note": "ok",
            "csrf_token": app.state.csrf_token}).status_code == 200
        assert rg.call_count == 0 and rp.call_count == 0
        assert hg.call_count == 0 and hp.call_count == 0


def test_no_cdn_in_templates(tmp_path):
    from app.dashboard import server as srv
    for p in list((srv.TEMPLATES_DIR).rglob("*")) + list((srv.STATIC_DIR).rglob("*")):
        if p.is_file():
            text = p.read_text(encoding="utf-8", errors="ignore")
            for bad in ("https://", "http://", "cdnjs", "jsdelivr", "unpkg", "fonts.googleapis"):
                assert bad not in text, (p, bad)
