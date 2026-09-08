# -*- coding: utf-8 -*-
"""V4.1.1 P2: OCR Parse Cache version must reflect real runtime capability."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.parsers.attachment_parser as ap


def test_ocr_cache_version_none_and_unavailable(monkeypatch):
    assert ap.ocr_cache_version(False) == "none"
    monkeypatch.setattr(ap, "_tesseract_binary", lambda: "")
    assert ap.ocr_cache_version(True) == "ocr-unavailable"


def test_ocr_cache_version_real_capability(monkeypatch):
    monkeypatch.setattr(ap, "_tesseract_binary", lambda: "/usr/bin/tesseract")
    monkeypatch.setattr(ap, "_tesseract_version", lambda binary: "5.5.0")
    monkeypatch.setattr(ap, "_tesseract_languages", lambda binary: "chi_tra+eng")
    assert ap.ocr_cache_version(True) == "tesseract:5.5.0:chi_tra+eng"


def test_parse_attachment_uses_ocr_cache_version(tmp_path, monkeypatch):
    p = tmp_path / "a.txt"
    p.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(ap, "ocr_cache_version", lambda enabled=True: "tesseract:9.9:eng")
    att = ap.parse_attachment(p)
    assert att.ocr_version == "tesseract:9.9:eng"
