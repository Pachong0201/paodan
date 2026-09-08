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
