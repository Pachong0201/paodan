# -*- coding: utf-8 -*-
"""Dashboard migration gate."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.storage.database import Database
from app.storage.migrations import SCHEMA_VERSION
from app.dashboard.server import create_app

from .db_utils import close_db, create_schema, insert_email


def test_dashboard_migration_creates_reviews_and_preserves_old_data(tmp_path):
    path = tmp_path / "x.db"
    db = create_schema(path)
    insert_email(db, "e1", "old email", processed_at="2026-01-01T10:00:00")
    close_db(db)

    # 模拟旧 V4.1.1：删除 dashboard 相关 schema，保留已有邮件数据。
    import sqlite3
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE IF EXISTS dashboard_reviews")
    conn.execute("DELETE FROM schema_version WHERE version>=4")
    conn.execute("DELETE FROM schema_migrations WHERE version>=4")
    conn.commit()
    conn.close()

    app = create_app(path)
    assert app.state.dashboard_config.host == "127.0.0.1"
    # create_app 会触发 migration
    conn = sqlite3.connect(path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "dashboard_reviews" in tables
    version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    assert version == SCHEMA_VERSION == 4
    rows = conn.execute("SELECT email_id, subject FROM emails").fetchall()
    assert len(rows) == 1 and rows[0][0] == "e1"
    conn.close()
