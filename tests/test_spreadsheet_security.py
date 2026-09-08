# -*- coding: utf-8 -*-
"""CSV / Excel formula injection 防护测试。"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import EmailDocument, FinalScore, ScreeningRecord
from app.reports.exporter import export_csv
from app.security.spreadsheet import spreadsheet_safe


def test_spreadsheet_safe_prefixes_formula():
    assert spreadsheet_safe("=HYPERLINK(1)").startswith("'")
    assert spreadsheet_safe("+SUM(1,1)").startswith("'")
    assert spreadsheet_safe("-1+1").startswith("'")
    assert spreadsheet_safe("@cmd").startswith("'")
    assert spreadsheet_safe("  =SUM(1)").startswith("'")
    assert spreadsheet_safe("normal") == "normal"


def test_csv_formula_injection_neutralized(tmp_path):
    doc = EmailDocument(email_id="x", subject='=HYPERLINK("https://evil.test","点击")',
                        sender="+SUM(1,1)", body_text="body", source_path="@cmd")
    rec = ScreeningRecord(email_id="x", email=doc,
                          score=FinalScore(final_score=80, priority="A"),
                          summary_zh="summary")
    out = tmp_path / "q.csv"
    export_csv([rec], out, min_priority_value=0)
    rows = list(csv.DictReader(out.read_text(encoding="utf-8-sig").splitlines()))
    assert rows[0]["subject"].startswith("'")
    assert rows[0]["sender"].startswith("'")
    assert rows[0]["source_path"].startswith("'")
