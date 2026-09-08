# -*- coding: utf-8 -*-
"""PrivacyRedactor 敏感信息识别/替换测试。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.security.redactor import PrivacyRedactor

CANARIES = [
    ("secret.source.2026@example.com", "EMAIL"),
    ("0912345678", "PHONE"),
    ("A123456789", "TAIWAN_ID"),
    ("812345678901234567", "BANK_ACCOUNT"),
    ("<super-secret-message@example.com>", "MESSAGE_ID"),
    (r"D:\Secret\Whistleblower\raw.eml", "WINDOWS_PATH"),
    ("/home/private/source/raw.eml", "UNIX_PATH"),
    ("sk-super-secret-test-token", "API_KEY"),
    ("line-source-secret-8899", "LINE_ID"),
    ("RAW_PRIVATE_CANARY_9F72C31A", "PRIVATE_CANARY"),
]


def test_all_canaries_redacted():
    r = PrivacyRedactor()
    for raw, kind in CANARIES:
        out = r.redact(raw)
        assert raw not in out, (raw, out)
        assert any(f.kind == kind for f in r.scan(raw)), (raw, [f.kind for f in r.scan(raw)])


def test_redact_preserves_normal_text():
    r = PrivacyRedactor()
    text = "政府机关应说明办理期限，居民已经等待超过半年。"
    assert r.redact(text) == text
    assert r.contains_sensitive(text) is False
