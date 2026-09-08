# -*- coding: utf-8 -*-
"""业务事务 rollback 测试。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import EmailDocument, FinalScore, ScreeningRecord
from app.storage.database import Database
from app.storage.repository import Repository


def test_transaction_rollback_on_midway_failure(tmp_path):
    db = Database(tmp_path / "x.db")
    repo = Repository(db)
    doc = EmailDocument(email_id="RAW:abc", raw_sha256="abc", subject="x",
                        body_text="body", body_hash="h")
    rec = ScreeningRecord(email_id="RAW:abc", email=doc,
                          score=FinalScore(final_score=80, priority="A"),
                          summary_zh="x", verification_targets=["t"])
    try:
        with db.transaction():
            repo.save_email(doc)
            repo.save_record(rec, store_full=False)
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert db.query("SELECT * FROM emails") == []
    assert db.query("SELECT * FROM scores") == []
    db.close()
