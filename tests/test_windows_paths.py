# -*- coding: utf-8 -*-
"""Windows/WSL 路径兼容回归。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.parsers.email_parser import _safe_filename
from app.security.spreadsheet import spreadsheet_safe


def test_windows_attachment_filename_sanitized():
    assert "\\" not in _safe_filename(r"C:\temp\evidence.pdf")
    assert "/" not in _safe_filename(r"C:\temp\evidence.pdf")
    assert _safe_filename(r"\\server\share\evidence.pdf") == "evidence.pdf"
    assert _safe_filename("CON.txt").startswith("_")


def test_spreadsheet_safe_windows_values():
    assert spreadsheet_safe("=cmd").startswith("'")
    assert spreadsheet_safe(r"C:\Users\x\file.txt") == r"C:\Users\x\file.txt"


def test_unicode_directory_attachment_cache_and_sqlite(tmp_path):
    from email import policy
    from email.message import EmailMessage
    from app.parsers import parse_eml, parse_attachment
    from app.storage.database import Database

    d = tmp_path / "中文目录"
    d.mkdir()
    msg = EmailMessage(policy=policy.default)
    msg["Subject"] = "中文路径测试"
    msg["From"] = "a@example.com"
    msg["To"] = "t@example.com"
    msg["Date"] = "Mon, 01 Sep 2026 08:00:00 +0800"
    msg.set_content("正文")
    msg.add_attachment("证据内容".encode("utf-8"), maintype="text", subtype="plain",
                       filename="证据.txt")
    eml = d / "邮件.eml"
    eml.write_bytes(msg.as_bytes())
    doc = parse_eml(eml)
    att = doc.attachments[0]
    assert att.metadata["cached_path"].endswith("证据.txt")
    parsed = parse_attachment(att.metadata["cached_path"], att.filename, ocr_enabled=False)
    assert "证据内容" in parsed.text
    db = Database(d / "数据库.db")
    assert db.schema_state()["schema_version"] >= 3
    db.close()
