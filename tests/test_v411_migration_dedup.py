# -*- coding: utf-8 -*-
"""V4.1.1 P1-1: old DB duplicate child rows are cleaned and UNIQUE INDEX is created."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.storage.database import Database
from app.storage.migrations import SCHEMA_VERSION


def _make_old_db(path: Path):
    c = sqlite3.connect(path)
    c.executescript(
        """
        CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT, description TEXT);
        CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT, description TEXT);
        INSERT INTO schema_version(version, applied_at, description) VALUES (2, 'old', 'old');
        INSERT INTO schema_migrations(version, applied_at, description) VALUES (2, 'old', 'old');

        CREATE TABLE attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, email_id TEXT, filename TEXT, file_type TEXT,
            sha256 TEXT, content_hash TEXT, text TEXT, metadata TEXT, extraction_status TEXT,
            warnings TEXT, source_sha256 TEXT, text_sha256 TEXT, parser_version TEXT,
            ocr_version TEXT, cached_path TEXT
        );
        CREATE TABLE entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT, email_id TEXT, entity_type TEXT,
            text TEXT, count INTEGER
        );
        CREATE TABLE pattern_matches (
            email_id TEXT, pattern_id TEXT, category TEXT, name TEXT, pattern_score REAL,
            matched_terms TEXT, evidence_snippets TEXT, window TEXT
        );
        CREATE TABLE named_channel_recommendations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, email_id TEXT, entity_id TEXT, entity_name TEXT,
            entity_type TEXT, channel_group TEXT, channel_role TEXT, fit_score REAL, rank INTEGER,
            reason TEXT, created_at TEXT
        );
        CREATE TABLE analysis_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, email_id TEXT, pipeline_version TEXT,
            political_rule_pack_hash TEXT, governance_rule_pack_hash TEXT, scoring_rule_hash TEXT,
            prompt_hash TEXT, llm_mode TEXT, llm_provider TEXT, llm_model TEXT,
            analysis_started_at TEXT, analysis_finished_at TEXT, result_status TEXT
        );
        """
    )
    # duplicates: same logical key, different completeness/id
    c.execute("INSERT INTO attachments(email_id,filename,source_sha256,text) VALUES('e1','f.pdf','s1','')")
    c.execute("INSERT INTO attachments(email_id,filename,source_sha256,text) VALUES('e1','f.pdf','s1','完整文本')")
    c.execute("INSERT INTO entities(email_id,entity_type,text,count) VALUES('e1','PERSON','张三',1)")
    c.execute("INSERT INTO entities(email_id,entity_type,text,count) VALUES('e1','PERSON','张三',2)")
    c.execute("INSERT INTO pattern_matches(email_id,pattern_id,window,evidence_snippets) VALUES('e1','P01','paragraph','')")
    c.execute("INSERT INTO pattern_matches(email_id,pattern_id,window,evidence_snippets) VALUES('e1','P01','paragraph','证据')")
    c.execute("INSERT INTO named_channel_recommendations(email_id,entity_id,channel_group,reason) VALUES('e1','media_mirror','media','')")
    c.execute("INSERT INTO named_channel_recommendations(email_id,entity_id,channel_group,reason) VALUES('e1','media_mirror','media','完整理由')")
    c.commit()
    c.close()


def _count(db, table):
    return int(db.query_one(f"SELECT COUNT(*) AS c FROM {table}")["c"])


def _index_names(db, table):
    return {r["name"] for r in db.query(f"PRAGMA index_list('{table}')")}


def test_old_db_dedup_and_unique_indexes(tmp_path):
    path = tmp_path / "old.db"
    _make_old_db(path)
    db = Database(path)
    assert db.schema_state()["schema_version"] == SCHEMA_VERSION
    assert _count(db, "attachments") == 1
    assert _count(db, "entities") == 1
    assert _count(db, "pattern_matches") == 1
    assert _count(db, "named_channel_recommendations") == 1
    # 保留最完整行
    att = db.query_one("SELECT text FROM attachments")
    assert att["text"] == "完整文本"
    # 唯一索引真实存在
    assert "uq_attachments_email_source" in _index_names(db, "attachments")
    assert "uq_entities_email_type_text" in _index_names(db, "entities")
    assert "uq_pattern_email_pattern_window" in _index_names(db, "pattern_matches")
    assert "uq_named_email_entity_group" in _index_names(db, "named_channel_recommendations")
    # 再次写入同逻辑键不增加重复
    db.execute("INSERT OR REPLACE INTO attachments(email_id,filename,source_sha256,text) VALUES('e1','f.pdf','s1','new')")
    db.execute("INSERT OR REPLACE INTO entities(email_id,entity_type,text,count) VALUES('e1','PERSON','张三',3)")
    db.execute("INSERT OR REPLACE INTO pattern_matches(email_id,pattern_id,window,evidence_snippets) VALUES('e1','P01','paragraph','new')")
    db.execute("INSERT OR REPLACE INTO named_channel_recommendations(email_id,entity_id,channel_group,reason) VALUES('e1','media_mirror','media','new')")
    assert _count(db, "attachments") == 1
    assert _count(db, "entities") == 1
    assert _count(db, "pattern_matches") == 1
    assert _count(db, "named_channel_recommendations") == 1
    db.close()
