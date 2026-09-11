# -*- coding: utf-8 -*-
"""人工审核通过邮件打包导出测试。"""
from __future__ import annotations

import io
import sys
import zipfile
from email import policy
from email.message import EmailMessage
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.dashboard.server import create_app
from app.storage.database import Database


def _eml(subject: str, body: str) -> bytes:
    msg = EmailMessage(policy=policy.default)
    msg["Subject"] = subject
    msg["From"] = "source@example.com"
    msg["To"] = "tips@example.com"
    msg["Message-ID"] = f"<export-{subject}@example.com>"
    msg.set_content(body)
    return msg.as_bytes()


def _app_with_approved(tmp_path: Path, source_exists: bool = True):
    db_path = tmp_path / "export.db"
    db = Database(db_path)
    raw = _eml("approved-export", "APPROVED_RAW_BODY")
    source = tmp_path / "approved.eml"
    if source_exists:
        source.write_bytes(raw)
    db.execute(
        """INSERT INTO emails(email_id, subject, body_text, source_path, raw_sha256, processed_at)
           VALUES (?,?,?,?,?,?)""",
        ("approved1", "approved-export", "APPROVED_BODY_FALLBACK",
         str(source) if source_exists else "", __import__("hashlib").sha256(raw).hexdigest(),
         "2026-09-01T08:00:00"))
    db.execute("INSERT INTO scores(email_id, final_score, priority, primary_track) VALUES (?,?,?,?)",
               ("approved1", 91.0, "S", "MIXED"))
    db.execute(
        """INSERT INTO dashboard_reviews
           (email_id, review_status, editor_note, reviewed_at, updated_at, created_at)
           VALUES (?,?,?,?,?,?)""",
        ("approved1", "VERIFIED", "confirmed by editor", "2026-09-02T08:00:00",
         "2026-09-02T08:00:00", "2026-09-02T08:00:00"))

    # 未审核邮件不能进入导出包。
    db.execute(
        """INSERT INTO emails(email_id, subject, body_text, raw_sha256, processed_at)
           VALUES (?,?,?,?,?)""",
        ("unreviewed1", "unreviewed", "UNREVIEWED_BODY", "deadbeef", "2026-09-01T08:00:00"))
    db.execute("INSERT INTO scores(email_id, final_score, priority, primary_track) VALUES (?,?,?,?)",
               ("unreviewed1", 50.0, "C", "NONE"))
    db.close()
    return create_app(db_path, import_staging_root=tmp_path / "staging"), db_path


def _csrf(client: TestClient) -> str:
    import re
    text = client.get("/review").text
    return re.search(r'name="csrf_token" value="([^"]+)"', text).group(1)


def test_export_verified_emails_zip(tmp_path):
    app, _ = _app_with_approved(tmp_path, source_exists=True)
    client = TestClient(app)
    r = client.post("/review/export", data={
        "csrf_token": _csrf(client),
        "reviewed_status": "VERIFIED",
    })
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = zf.namelist()
        assert "manifest.json" in names
        assert "reviewed_emails.csv" in names
        assert any(name.endswith(".eml") for name in names)
        assert not any("unreviewed" in name.lower() for name in names)
        manifest = zf.read("manifest.json").decode("utf-8")
        assert "approved1" in manifest
        assert "unreviewed1" not in manifest
        eml_name = next(name for name in names if name.endswith(".eml"))
        assert zf.read(eml_name) == _eml("approved-export", "APPROVED_RAW_BODY")
        assert "source_path" not in manifest
        assert str(tmp_path) not in manifest


def test_export_falls_back_to_txt_when_source_missing(tmp_path):
    app, _ = _app_with_approved(tmp_path, source_exists=False)
    client = TestClient(app)
    r = client.post("/review/export", data={
        "csrf_token": _csrf(client),
        "reviewed_status": "VERIFIED",
    })
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = zf.namelist()
        assert any(name.endswith(".txt") for name in names)
        text_name = next(name for name in names if name.endswith(".txt"))
        assert "APPROVED_BODY_FALLBACK" in zf.read(text_name).decode("utf-8")


def test_export_without_approved_emails_returns_404(tmp_path):
    db_path = tmp_path / "empty.db"
    Database(db_path).close()
    app = create_app(db_path, import_staging_root=tmp_path / "staging")
    client = TestClient(app)
    r = client.post("/review/export", data={
        "csrf_token": _csrf(client),
        "reviewed_status": "VERIFIED",
    })
    assert r.status_code == 404
