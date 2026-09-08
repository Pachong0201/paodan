"""SQLite schema migrations for V4.1.

原则：兼容已有用户数据库；启动时自动执行未应用的 migration；保留旧数据。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, List, Optional

SCHEMA_VERSION = 2
PIPELINE_VERSION = "4.1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.Error:
        return set()


def _add_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    if not _table_exists(conn, table):
        return
    if column in _columns(conn, table):
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _safe_index(conn: sqlite3.Connection, sql: str) -> None:
    try:
        conn.execute(sql)
    except sqlite3.IntegrityError:
        # 旧库可能存在重复行；唯一索引无法建立时保留数据，由 Repository 做幂等 upsert。
        pass
    except sqlite3.Error:
        pass


def migration_001_add_v41_tables(conn: sqlite3.Connection) -> None:
    # 兼容旧 emails 表
    _add_column(conn, "emails", "raw_sha256", "TEXT")
    _add_column(conn, "emails", "normalized_message_id", "TEXT")
    _add_column(conn, "emails", "ingestion_identity", "TEXT")
    _add_column(conn, "emails", "last_analysis_hash", "TEXT")
    # 兼容旧 attachments 表
    _add_column(conn, "attachments", "source_sha256", "TEXT")
    _add_column(conn, "attachments", "text_sha256", "TEXT")
    _add_column(conn, "attachments", "parser_version", "TEXT")
    _add_column(conn, "attachments", "ocr_version", "TEXT")
    _add_column(conn, "attachments", "cached_path", "TEXT")
    # 兼容旧 scores 表
    _add_column(conn, "scores", "governance_score", "REAL")
    _add_column(conn, "scores", "primary_track", "TEXT")
    _add_column(conn, "scores", "unified_json", "TEXT")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS governance_results (
            email_id TEXT PRIMARY KEY,
            categories_json TEXT,
            keywords_json TEXT,
            patterns_json TEXT,
            negatives_json TEXT,
            enhance_json TEXT,
            score REAL,
            priority TEXT,
            dimensions_json TEXT,
            details_json TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS analysis_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_id TEXT NOT NULL,
            pipeline_version TEXT,
            political_rule_pack_hash TEXT,
            governance_rule_pack_hash TEXT,
            scoring_rule_hash TEXT,
            prompt_hash TEXT,
            llm_mode TEXT,
            llm_provider TEXT,
            llm_model TEXT,
            analysis_started_at TEXT,
            analysis_finished_at TEXT,
            result_status TEXT
        );
        CREATE TABLE IF NOT EXISTS attachment_parse_cache (
            source_sha256 TEXT NOT NULL,
            parser_version TEXT NOT NULL,
            ocr_version TEXT NOT NULL,
            text TEXT,
            text_sha256 TEXT,
            metadata TEXT,
            tables_json TEXT,
            extraction_status TEXT,
            updated_at TEXT,
            PRIMARY KEY (source_sha256, parser_version, ocr_version)
        );
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT,
            description TEXT
        );
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT,
            description TEXT
        );
        """
    )
    # 子表幂等唯一约束（旧库重复行时不阻断启动）
    _safe_index(conn, "CREATE UNIQUE INDEX IF NOT EXISTS uq_attachments_email_source "
                      "ON attachments(email_id, source_sha256) "
                      "WHERE source_sha256 IS NOT NULL AND source_sha256 != ''")
    _safe_index(conn, "CREATE UNIQUE INDEX IF NOT EXISTS uq_entities_email_type_text "
                      "ON entities(email_id, entity_type, text)")
    _safe_index(conn, "CREATE UNIQUE INDEX IF NOT EXISTS uq_pattern_email_pattern_window "
                      "ON pattern_matches(email_id, pattern_id, window)")
    _safe_index(conn, "CREATE UNIQUE INDEX IF NOT EXISTS uq_named_email_entity_group "
                      "ON named_channel_recommendations(email_id, entity_id, channel_group)")
    _safe_index(conn, "CREATE INDEX IF NOT EXISTS idx_analysis_email ON analysis_runs(email_id)")
    _safe_index(conn, "CREATE INDEX IF NOT EXISTS idx_governance_priority ON governance_results(priority)")


def migration_002_v41_finalize(conn: sqlite3.Connection) -> None:
    """V4.1 最终 schema：确保索引/表结构存在（幂等）。"""
    _safe_index(conn, "CREATE INDEX IF NOT EXISTS idx_scores_priority ON scores(priority)")
    _safe_index(conn, "CREATE INDEX IF NOT EXISTS idx_analysis_started ON analysis_runs(analysis_started_at)")
    _safe_index(conn, "CREATE INDEX IF NOT EXISTS idx_governance_score ON governance_results(score)")


@dataclass
class Migration:
    version: int
    description: str
    fn: Callable[[sqlite3.Connection], None]


MIGRATIONS: List[Migration] = [
    Migration(1, "V4.1 add governance/analysis/cache tables and columns", migration_001_add_v41_tables),
    Migration(2, "V4.1 finalize indexes and schema version", migration_002_v41_finalize),
]


def current_schema_version(conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, "schema_version"):
        return 0
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return int(row[0] or 0) if row else 0


def apply_migrations(conn: sqlite3.Connection) -> int:
    """应用所有未执行 migration；返回当前 schema version。"""
    if not _table_exists(conn, "schema_version"):
        conn.execute("CREATE TABLE IF NOT EXISTS schema_version "
                     "(version INTEGER PRIMARY KEY, applied_at TEXT, description TEXT)")
    if not _table_exists(conn, "schema_migrations"):
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                     "(version INTEGER PRIMARY KEY, applied_at TEXT, description TEXT)")
    current = current_schema_version(conn)
    for mig in MIGRATIONS:
        if mig.version <= current:
            continue
        mig.fn(conn)
        conn.execute("INSERT OR REPLACE INTO schema_version(version, applied_at, description) VALUES (?,?,?)",
                     (mig.version, _now(), mig.description))
        conn.execute("INSERT OR REPLACE INTO schema_migrations(version, applied_at, description) VALUES (?,?,?)",
                     (mig.version, _now(), mig.description))
        current = mig.version
    # PRAGMA user_version 便于外部工具检查
    try:
        conn.execute(f"PRAGMA user_version = {int(current)}")
    except sqlite3.Error:
        pass
    return current


def schema_state(conn: sqlite3.Connection) -> dict:
    return {
        "schema_version": current_schema_version(conn),
        "latest_migration": SCHEMA_VERSION,
        "migration_state": "up-to-date" if current_schema_version(conn) >= SCHEMA_VERSION else "stale",
    }
