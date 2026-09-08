# -*- coding: utf-8 -*-
"""Database migration 兼容旧库测试。"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.storage.database import Database
from app.storage.migrations import SCHEMA_VERSION


def test_old_db_migrates_and_preserves_data(tmp_path):
    p = tmp_path / "old.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE emails (email_id TEXT PRIMARY KEY, message_id TEXT, subject TEXT, sender TEXT, recipients TEXT, cc TEXT, date TEXT, body_text TEXT, body_hash TEXT, source_path TEXT, processed_at TEXT, dedup_key TEXT UNIQUE)")
    c.execute("INSERT INTO emails(email_id,message_id,subject,dedup_key) VALUES('old1','m1','hello','d1')")
    c.commit()
    c.close()
    db = Database(p)
    assert db.schema_state()["schema_version"] == SCHEMA_VERSION
    assert db.query("SELECT email_id, subject FROM emails")[0]["subject"] == "hello"
    cols = {r[1] for r in db.query("PRAGMA table_info(emails)")}
    assert "raw_sha256" in cols and "normalized_message_id" in cols
    for table in ("governance_results", "analysis_runs", "attachment_parse_cache", "schema_version"):
        assert db.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
    db.close()
