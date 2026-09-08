"""SQLite schema migrations for V4.1.1.

原则：兼容已有用户数据库；启动时自动执行未应用的 migration；保留旧数据；
历史重复子行必须清理后再建立 UNIQUE INDEX，不能静默跳过。
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 4
PIPELINE_VERSION = "4.1.1"


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


def _safe_index(conn: sqlite3.Connection, sql: str, strict: bool = False) -> None:
    try:
        conn.execute(sql)
    except sqlite3.IntegrityError:
        if strict:
            raise
        logger.warning("唯一索引创建失败（旧库可能仍有重复行），稍后 migration 将清理: %s", sql)
    except sqlite3.Error as exc:
        if strict:
            raise
        logger.warning("索引创建失败: %s (%s)", sql, exc)


def _row_value(row: Dict[str, object], col: str) -> str:
    value = row.get(col)
    if value is None:
        return ""
    return str(value).strip()


def _dedupe_table(conn: sqlite3.Connection, table: str,
                  key_cols: Sequence[str],
                  fallback_key_cols: Optional[Sequence[str]] = None,
                  prefer_cols: Sequence[str] = ()) -> int:
    """按逻辑主键清理重复行，保留“最完整/最新”的一条。

    返回删除行数。旧库若缺少表/列则安全跳过。
    """
    if not _table_exists(conn, table):
        return 0
    cols = _columns(conn, table)
    if not all(c in cols for c in key_cols):
        return 0
    select_cols = ", ".join(["rowid AS __paodan_rowid", *cols])
    rows = [dict(r) for r in conn.execute(f"SELECT {select_cols} FROM {table}").fetchall()]
    groups: Dict[Tuple[str, ...], List[Dict[str, object]]] = {}
    for row in rows:
        key = tuple(_row_value(row, c) for c in key_cols)
        if fallback_key_cols and any(k == "" for k in key):
            if all(c in cols for c in fallback_key_cols):
                key = tuple(_row_value(row, c) for c in fallback_key_cols)
        # 即使逻辑键为空也归组去重，否则 UNIQUE INDEX 无法建立。
        groups.setdefault(key, []).append(row)

    deleted = 0
    for key, items in groups.items():
        if len(items) <= 1:
            continue
        def _quality(row: Dict[str, object]) -> Tuple[int, int]:
            complete = sum(1 for c in prefer_cols if _row_value(row, c) != "")
            return complete, int(row.get("__paodan_rowid") or 0)
        best = max(items, key=_quality)
        best_rowid = int(best.get("__paodan_rowid") or 0)
        for row in items:
            rid = int(row.get("__paodan_rowid") or 0)
            if rid and rid != best_rowid:
                conn.execute(f"DELETE FROM {table} WHERE rowid=?", (rid,))
                deleted += 1
    if deleted:
        logger.info("migration dedupe %s: removed %d duplicate row(s)", table, deleted)
    return deleted


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
    # 先尝试创建；若旧库已有重复，migration_003 会清理后严格重建。
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


def migration_003_v411_hardening(conn: sqlite3.Connection) -> None:
    """V4.1.1：清理历史重复子行；严格建立唯一索引；补齐 analysis hash 字段。"""
    _add_column(conn, "analysis_runs", "release_rule_hash", "TEXT")
    _add_column(conn, "analysis_runs", "named_rule_hash", "TEXT")
    _add_column(conn, "analysis_runs", "channel_entity_hash", "TEXT")

    # 逻辑主键去重；保留文本/reason 更完整、id 更新的行。
    _dedupe_table(conn, "attachments", ("email_id", "source_sha256"),
                  fallback_key_cols=("email_id", "filename", "sha256", "content_hash"),
                  prefer_cols=("text", "text_sha256", "source_sha256"))
    _dedupe_table(conn, "entities", ("email_id", "entity_type", "text"),
                  prefer_cols=("count",))
    _dedupe_table(conn, "pattern_matches", ("email_id", "pattern_id", "window"),
                  prefer_cols=("matched_terms", "evidence_snippets"))
    _dedupe_table(conn, "named_channel_recommendations",
                  ("email_id", "entity_id", "channel_group"),
                  prefer_cols=("reason", "channel_role"))

    # 去重后必须真正建立唯一索引；失败则 migration 失败，不允许继续积累脏行。
    _safe_index(conn, "CREATE UNIQUE INDEX IF NOT EXISTS uq_attachments_email_source "
                      "ON attachments(email_id, source_sha256) "
                      "WHERE source_sha256 IS NOT NULL AND source_sha256 != ''", strict=True)
    _safe_index(conn, "CREATE UNIQUE INDEX IF NOT EXISTS uq_entities_email_type_text "
                      "ON entities(email_id, entity_type, text)", strict=True)
    _safe_index(conn, "CREATE UNIQUE INDEX IF NOT EXISTS uq_pattern_email_pattern_window "
                      "ON pattern_matches(email_id, pattern_id, window)", strict=True)
    _safe_index(conn, "CREATE UNIQUE INDEX IF NOT EXISTS uq_named_email_entity_group "
                      "ON named_channel_recommendations(email_id, entity_id, channel_group)", strict=True)


def migration_004_v420_dashboard(conn: sqlite3.Connection) -> None:
    """V4.2: Local Dashboard 人工审核表与查询索引。"""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS dashboard_reviews (
            email_id TEXT PRIMARY KEY,
            review_status TEXT NOT NULL DEFAULT 'UNREVIEWED',
            editor_note TEXT NOT NULL DEFAULT '',
            reviewed_at TEXT,
            updated_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(email_id) REFERENCES emails(email_id)
        );
        """
    )
    _safe_index(conn, "CREATE INDEX IF NOT EXISTS idx_dashboard_reviews_status "
                      "ON dashboard_reviews(review_status)")
    _safe_index(conn, "CREATE INDEX IF NOT EXISTS idx_scores_priority ON scores(priority)")
    _safe_index(conn, "CREATE INDEX IF NOT EXISTS idx_scores_primary_track ON scores(primary_track)")
    _safe_index(conn, "CREATE INDEX IF NOT EXISTS idx_emails_processed_at ON emails(processed_at)")


@dataclass
class Migration:
    version: int
    description: str
    fn: Callable[[sqlite3.Connection], None]


MIGRATIONS: List[Migration] = [
    Migration(1, "V4.1 add governance/analysis/cache tables and columns", migration_001_add_v41_tables),
    Migration(2, "V4.1 finalize indexes and schema version", migration_002_v41_finalize),
    Migration(3, "V4.1.1 deduplicate child tables and add analysis hashes", migration_003_v411_hardening),
    Migration(4, "V4.2 dashboard reviews and query indexes", migration_004_v420_dashboard),
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
