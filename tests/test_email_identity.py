# -*- coding: utf-8 -*-
"""WP1 email identity：无 Message-ID 同正文不同发件人不得覆盖。"""
from __future__ import annotations

import sys
from email import policy
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.parsers import parse_eml
from app.storage.database import Database
from app.storage.repository import Repository


def _mk_no_mid(tmp_path: Path, name: str, sender: str) -> Path:
    msg = EmailMessage(policy=policy.default)
    msg["Subject"] = "same body"
    msg["From"] = sender
    msg["To"] = "tips@news.tw"
    msg["Date"] = "Mon, 01 Sep 2026 08:00:00 +0800"
    msg.set_content("完全相同的正文 RAW_IDENTITY_BODY")
    p = tmp_path / f"{name}.eml"
    p.write_bytes(msg.as_bytes())
    return p


def test_no_message_id_same_body_different_sender(tmp_path):
    a = parse_eml(_mk_no_mid(tmp_path, "a", "a@example.com"))
    b = parse_eml(_mk_no_mid(tmp_path, "b", "b@example.com"))
    assert a.email_id != b.email_id
    assert a.email_id.startswith("RAW:")
    assert a.raw_sha256 and b.raw_sha256 and a.raw_sha256 != b.raw_sha256
    db = Database(tmp_path / "x.db")
    repo = Repository(db)
    assert repo.is_duplicate(a) is False
    repo.save_email(a)
    assert repo.is_duplicate(b) is False
    repo.save_email(b)
    assert len(db.query("SELECT * FROM emails")) == 2
    db.close()


def test_message_id_stable_hashed_id(tmp_path):
    p = tmp_path / "m.eml"
    p.write_text("From: a@example.com\nTo: t@example.com\nMessage-ID: <ABC@Example.COM>\n\nbody", encoding="utf-8")
    doc = parse_eml(p)
    assert doc.email_id.startswith("MID:")
    assert "ABC@Example.COM" not in doc.email_id
    assert doc.normalized_message_id == "abc@example.com"
