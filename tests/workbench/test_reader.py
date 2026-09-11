# -*- coding: utf-8 -*-
"""V5.0 Sensitive Reader gates."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi.testclient import TestClient

from app.dashboard.server import create_app
from app.storage.database import Database


def _seed(tmp_path):
    dbp = tmp_path / "r.db"
    db = Database(dbp)
    today = date.today().isoformat()
    db.execute(
        """INSERT INTO emails(email_id, subject, sender, body_text, message_id, source_path, processed_at)
           VALUES (?,?,?,?,?,?,?)""",
        ("reader1", "原始郵件標題 <script>alert(1)</script>", "secret.source@example.com",
         "RAW_MAIL_BODY_CANARY 完整郵件正文 <img src=x onerror=alert(1)> <img src=https://tracking.example.com/x>",
         "<secret-message@example.com>", "D:\\\\Secret\\\\raw.eml", f"{today}T08:00:00"))
    db.execute("INSERT INTO scores(email_id, final_score, priority, primary_track) VALUES (?,?,?,?)",
               ("reader1", 90.0, "S", "MIXED"))
    db.execute(
        """INSERT INTO attachments(email_id, filename, file_type, text, extraction_status)
           VALUES (?,?,?,?,?)""",
        ("reader1", "证据.pdf", "pdf", "ATTACHMENT_CANARY 提取文本", "success"))
    db.close()
    return create_app(dbp, import_staging_root=tmp_path / "staging")


def test_reader_shows_raw_body_and_escapes_xss(tmp_path):
    app = _seed(tmp_path)
    client = TestClient(app)
    r = client.get("/emails/reader1/reader")
    assert r.status_code == 200
    assert "RAW_MAIL_BODY_CANARY" in r.text
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in r.text
    assert "secret.source@example.com" not in r.text
    assert "<secret-message@example.com>" not in r.text
    assert "D:\\\\Secret\\\\raw.eml" not in r.text


def test_source_reveal_on_demand(tmp_path):
    app = _seed(tmp_path)
    client = TestClient(app)
    # 初始 HTML 不包含来源
    assert "secret.source@example.com" not in client.get("/emails/reader1/reader").text
    r = client.get("/emails/reader1/reader/source")
    assert r.status_code == 200
    assert r.json()["sender"] == "secret.source@example.com"
    assert "no-store" in r.headers.get("cache-control", "")


def test_attachment_text_and_path_traversal(tmp_path):
    app = _seed(tmp_path)
    client = TestClient(app)
    r = client.get("/emails/reader1/reader/attachments/1")
    assert r.status_code == 200
    assert "ATTACHMENT_CANARY" in r.text
    assert client.get("/emails/reader1/reader/attachments/999").status_code == 404
    assert client.get("/emails/../../etc/passwd/reader").status_code in (404, 400)


def test_reader_no_external_network(tmp_path):
    app = _seed(tmp_path)
    client = TestClient(app)
    with patch("requests.get", side_effect=AssertionError("network")) as rg, \
         patch("requests.post", side_effect=AssertionError("network")) as rp, \
         patch("httpx.get", side_effect=AssertionError("network")) as hg, \
         patch("httpx.post", side_effect=AssertionError("network")) as hp:
        assert client.get("/emails/reader1/reader").status_code == 200
        assert client.get("/emails/reader1/reader/source").status_code == 200
        assert client.get("/emails/reader1/reader/attachments/1").status_code == 200
        assert rg.call_count == 0 and rp.call_count == 0
        assert hg.call_count == 0 and hp.call_count == 0
