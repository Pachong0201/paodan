# -*- coding: utf-8 -*-
"""V5.0 Import Center: nested ZIP/directory EML discovery."""
from __future__ import annotations

import sys
import zipfile
from email import policy
from email.message import EmailMessage
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.workbench.import_service import (
    ImportSecurityError,
    ImportService,
    discover_eml_files,
)


def _eml(subject: str, body: str = "body") -> bytes:
    msg = EmailMessage(policy=policy.default)
    msg["Subject"] = subject
    msg["From"] = "source@example.com"
    msg["To"] = "tips@example.com"
    msg["Date"] = "Mon, 01 Sep 2026 08:00:00 +0800"
    msg.set_content(body)
    return msg.as_bytes()


def _zip(tmp_path: Path, entries: dict) -> Path:
    p = tmp_path / "batch.zip"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return p


def _service(tmp_path: Path, **kw) -> ImportService:
    return ImportService(staging_root=tmp_path / "staging", **kw)


def test_case1_one_level(tmp_path):
    z = _zip(tmp_path, {"a/1.eml": _eml("one"), "b/2.eml": _eml("two")})
    r = _service(tmp_path).import_zip(z)
    assert r.discovered == 2 and r.accepted == 2


def test_case2_multi_level(tmp_path):
    z = _zip(tmp_path, {"root/year/month/case/mail.eml": _eml("deep")})
    r = _service(tmp_path).import_zip(z)
    assert r.accepted == 1


def test_case3_same_filename_different_content(tmp_path):
    z = _zip(tmp_path, {
        "001/message.eml": _eml("A"),
        "002/message.eml": _eml("B"),
        "003/message.eml": _eml("C"),
    })
    r = _service(tmp_path).import_zip(z)
    assert r.discovered == 3 and r.accepted == 3
    staged = [i.staged_path for i in r.items if i.status == "accepted"]
    assert len(staged) == 3 and len(set(staged)) == 3


def test_case4_same_content_dedup(tmp_path):
    same = _eml("same")
    z = _zip(tmp_path, {"001/message.eml": same, "002/message.eml": same})
    r = _service(tmp_path).import_zip(z)
    assert r.discovered == 2
    assert r.accepted == 1
    assert r.duplicates == 1


def test_case5_mixed_folder_content(tmp_path):
    z = _zip(tmp_path, {
        "001/message.eml": _eml("mail"),
        "001/file.pdf": b"%PDF-1.4 fake",
        "001/image.jpg": b"fake-image",
    })
    r = _service(tmp_path).import_zip(z)
    assert r.discovered == 1 and r.accepted == 1


def test_case6_zip_slip_blocked(tmp_path):
    z = _zip(tmp_path, {"../../evil.eml": _eml("evil")})
    with pytest.raises(ImportSecurityError):
        _service(tmp_path).import_zip(z)


def test_case7_nesting_depth_limit(tmp_path):
    z = _zip(tmp_path, {"a/b/c/mail.eml": _eml("deep")})
    r = _service(tmp_path, max_nesting_depth=1).import_zip(z)
    assert r.discovered == 1
    assert r.accepted == 0
    assert r.rejected == 1
    assert r.items[0].reason == "NESTING_DEPTH_EXCEEDED"


def test_case8_chinese_nested_path(tmp_path):
    z = _zip(tmp_path, {"2026爆料邮件/台北市政府/第一批/举报邮件.eml": _eml("中文")})
    r = _service(tmp_path).import_zip(z)
    assert r.accepted == 1


def test_case9_empty_directory_ignored(tmp_path):
    z = _zip(tmp_path, {"empty/.keep": b"", "mail.eml": _eml("x")})
    r = _service(tmp_path).import_zip(z)
    assert r.accepted == 1 and r.invalid == 0


def test_discover_eml_files_recursive(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "deep.eml").write_bytes(_eml("deep"))
    (tmp_path / "a" / "top.txt").write_text("x", encoding="utf-8")
    assert len(discover_eml_files(tmp_path, recursive=True)) == 1


def test_import_directory_recursive(tmp_path):
    (tmp_path / "in" / "a" / "b").mkdir(parents=True)
    (tmp_path / "in" / "a" / "b" / "deep.eml").write_bytes(_eml("deep"))
    r = _service(tmp_path).import_directory(tmp_path / "in")
    assert r.accepted == 1


def test_import_center_page_and_nested_upload(tmp_path):
    from fastapi.testclient import TestClient
    from app.dashboard.server import create_app

    # 使用临时 DB，避免触碰生产 data/news_screening.db
    from app.storage.database import Database
    db_path = tmp_path / "dash.db"
    Database(db_path).close()
    app = create_app(db_path)
    app.state.import_staging_root = tmp_path / "staging"
    client = TestClient(app)
    assert "支持 ZIP 中多层文件夹结构" in client.get("/import").text

    z = _zip(tmp_path, {
        "一级/二级/message.eml": _eml("nested"),
        "一级/二级/other.eml": _eml("nested2"),
    })
    with open(z, "rb") as f:
        resp = client.post("/import", files={"file": ("batch.zip", f, "application/zip")})
    assert resp.status_code == 200
    assert "发现邮件" in resp.text
