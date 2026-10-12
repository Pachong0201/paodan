# -*- coding: utf-8 -*-
"""Workbench「Markdown 稿件筛选」页面测试。"""
from __future__ import annotations

import io
import sqlite3
import sys
import zipfile
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi.testclient import TestClient

from app.dashboard.server import create_app
from app.storage.database import Database
from tests.test_markdown_mode import DRAFT_MD


def _client(tmp_path):
    db_path = tmp_path / "w.db"
    Database(db_path).close()
    app = create_app(db_path, import_staging_root=tmp_path / "staging", md_db_path=tmp_path / "md" / "md.db")
    return app, TestClient(app)


def _zip(members: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return buf.getvalue()


def test_markdown_page_renders(tmp_path):
    app, client = _client(tmp_path)
    r = client.get("/markdown")
    assert r.status_code == 200
    assert 'name="files"' in r.text and "暂无结果" in r.text
    assert 'href="/markdown"' in client.get("/").text


def test_markdown_upload_requires_csrf(tmp_path):
    app, client = _client(tmp_path)
    r = client.post("/markdown", files=[("files", ("a.md", DRAFT_MD.encode(), "text/markdown"))])
    assert r.status_code in (400, 403)


def test_markdown_upload_rejects_other_suffix(tmp_path):
    app, client = _client(tmp_path)
    r = client.post("/markdown", data={"csrf_token": app.state.csrf_token},
                    files=[("files", ("a.eml", b"Subject: x\n\nbody", "message/rfc822"))])
    assert r.status_code == 400


def test_markdown_upload_zip_detail_export_and_dedup(tmp_path):
    app, client = _client(tmp_path)
    zdata = _zip({"稿件/b.md": "# 社區餐會\n某里長參加社區餐會。", "稿件/ignore.txt": "x",
                  "__MACOSX/._b.md": "junk"})
    r = client.post("/markdown", data={"csrf_token": app.state.csrf_token},
                    files=[("files", ("a.md", DRAFT_MD.encode(), "text/markdown")),
                           ("files", ("batch.zip", zdata, "application/zip"))])
    assert r.status_code == 200
    assert "完成 2 篇" in r.text
    assert "議員胞姐掛名助理調查" in r.text and "社區餐會" in r.text
    # 不展示稿件原文
    assert "提款卡長期由辦公室主任王雅婷保管" not in r.text

    with sqlite3.connect(tmp_path / "md" / "md.db") as conn:
        rows = conn.execute("SELECT email_id, priority, file_name FROM md_results ORDER BY final_score DESC").fetchall()
    assert len(rows) == 2 and rows[0][1] in ("S", "A") and rows[0][2] == "a.md"
    assert rows[1][2] == "batch.zip/稿件/b.md"

    d = client.get("/markdown/detail?id=" + quote(rows[0][0], safe=""))
    assert d.status_code == 200 and "证据质量" in d.text
    assert "提款卡長期由辦公室主任王雅婷保管" not in d.text
    assert client.get("/markdown/detail?id=nope").status_code == 404

    high = client.get("/markdown?priority=SAB")
    assert "議員胞姐掛名助理調查" in high.text and "社區餐會" not in high.text
    csv_resp = client.get("/markdown/export.csv")
    assert csv_resp.status_code == 200 and "a.md" in csv_resp.text

    again = client.post("/markdown", data={"csrf_token": app.state.csrf_token},
                        files=[("files", ("copy.md", DRAFT_MD.encode(), "text/markdown"))])
    assert "跳过 1 篇" in again.text

    # 邮件结果库不受影响
    with sqlite3.connect(tmp_path / "w.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0] == 0
