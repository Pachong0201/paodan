"""Dashboard DB：同一 SQLite 文件 + 每请求连接 + migration 由 Database 保证。"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..config import DB_PATH
from ..storage.database import Database


class DashboardDBError(RuntimeError):
    pass


def ensure_database(path: str | Path | None = None) -> str:
    """确认数据库存在并执行与主程序一致的 migration；返回 DB 绝对路径。"""
    db_path = Path(path) if path else Path(DB_PATH)
    if not db_path.exists():
        raise DashboardDBError("未找到 paodan 数据库")
    db = Database(db_path)
    db.close()
    # 开启 WAL（不会关闭既有 WAL），提升并发读写体验。
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
    finally:
        conn.close()
    return str(db_path.resolve())


@contextmanager
def connect_dashboard(path: str) -> Iterator[sqlite3.Connection]:
    """每请求独立连接，避免 SQLite connection 跨线程。"""
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
    finally:
        conn.close()
