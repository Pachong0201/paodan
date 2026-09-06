"""SQLite 数据库：建表与基础访问。表：emails/attachments/entities/rule_matches/
pattern_matches/llm_results/scores/review_queue."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS emails (
    email_id TEXT PRIMARY KEY,
    message_id TEXT,
    subject TEXT,
    sender TEXT,
    recipients TEXT,
    cc TEXT,
    date TEXT,
    body_text TEXT,
    body_hash TEXT,
    source_path TEXT,
    processed_at TEXT,
    dedup_key TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id TEXT,
    filename TEXT,
    file_type TEXT,
    sha256 TEXT,
    content_hash TEXT,
    text TEXT,
    metadata TEXT,
    extraction_status TEXT,
    warnings TEXT
);
CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id TEXT,
    entity_type TEXT,
    text TEXT,
    count INTEGER
);
CREATE TABLE IF NOT EXISTS rule_matches (
    email_id TEXT PRIMARY KEY,
    result_json TEXT,
    rule_score REAL
);
CREATE TABLE IF NOT EXISTS pattern_matches (
    email_id TEXT,
    pattern_id TEXT,
    category TEXT,
    name TEXT,
    pattern_score REAL,
    matched_terms TEXT,
    evidence_snippets TEXT,
    window TEXT
);
CREATE TABLE IF NOT EXISTS llm_results (
    email_id TEXT PRIMARY KEY,
    llm_json TEXT,
    llm_status TEXT,
    evidence_stage TEXT
);
CREATE TABLE IF NOT EXISTS scores (
    email_id TEXT PRIMARY KEY,
    final_score REAL,
    priority TEXT,
    dimensions_json TEXT,
    stage_adjustment REAL,
    negative_adjustment REAL
);
CREATE TABLE IF NOT EXISTS review_queue (
    email_id TEXT PRIMARY KEY,
    priority TEXT,
    final_score REAL,
    subject TEXT,
    sender TEXT,
    summary_zh TEXT,
    verification_targets TEXT,
    queued_at TEXT
);
CREATE TABLE IF NOT EXISTS release_recommendations (
    email_id TEXT PRIMARY KEY,
    primary_route TEXT,
    secondary_routes TEXT,
    avoid_routes TEXT,
    route_confidence REAL,
    prepublication_verification_required INTEGER,
    verification_before_release TEXT,
    formal_referral_recommended INTEGER,
    formal_referral_type TEXT,
    release_risks TEXT,
    reason TEXT,
    recommended_release_sequence TEXT,
    headline_angle TEXT,
    editor_note TEXT,
    rule_hits TEXT,
    source TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS named_channel_recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id TEXT,
    entity_id TEXT,
    entity_name TEXT,
    entity_type TEXT,
    channel_group TEXT,
    channel_role TEXT,
    fit_score REAL,
    rank INTEGER,
    reason TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_named_email ON named_channel_recommendations(email_id);
CREATE INDEX IF NOT EXISTS idx_release_route ON release_recommendations(primary_route);
CREATE INDEX IF NOT EXISTS idx_pattern_email ON pattern_matches(email_id);
CREATE INDEX IF NOT EXISTS idx_entities_email ON entities(email_id);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur

    def executemany(self, sql: str, rows) -> sqlite3.Cursor:
        cur = self.conn.executemany(sql, rows)
        self.conn.commit()
        return cur

    def query(self, sql: str, params: tuple = ()) -> list:
        return list(self.conn.execute(sql, params).fetchall())

    def exists_email(self, message_id: str, dedup_key: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM emails WHERE message_id=? OR dedup_key=? LIMIT 1",
            (message_id or "", dedup_key)).fetchone()
        return row is not None
