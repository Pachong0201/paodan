# -*- coding: utf-8 -*-
"""V5.0 Import Center: multi-ZIP and mixed ZIP+EML upload regression gates.

背景（真实缺陷，本文件用于防回归）：
路由 `POST /import` 曾对多 ZIP / ZIP+EML 混合上传只处理**第一个** ZIP，
其余上传被静默丢弃 —— 无错误提示、无批次记录、临时文件随即删除，属于数据丢失。
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi.testclient import TestClient

from app.dashboard.server import create_app
from app.storage.database import Database
from app.workbench.import_service import ImportService
from app.workbench.job_repository import WorkbenchRepository
from tests.workbench.test_import_nested_zip import _eml


def _app(tmp_path):
    db_path = tmp_path / "w.db"
    Database(db_path).close()
    app = create_app(db_path, import_staging_root=tmp_path / "staging")
    return app, db_path


def _zip_bytes(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _post(client, app, files):
    return client.post("/import", data={"csrf_token": app.state.csrf_token}, files=files)


def _batches(db_path):
    with Database(db_path) as db:
        return WorkbenchRepository(db.conn).list_import_batches()


# ----------------------------------------------------------------------
# 多 ZIP
# ----------------------------------------------------------------------
def test_multi_zip_upload_single_batch_all_entries(tmp_path):
    """两个 ZIP 各含 2 封邮件 → 必须全部导入，且只有一个批次。"""
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    z1 = _zip_bytes({"a/1.eml": _eml("a1"), "a/2.eml": _eml("a2")})
    z2 = _zip_bytes({"b/1.eml": _eml("b1"), "b/2.eml": _eml("b2")})
    r = _post(client, app, [("files", ("a.zip", z1, "application/zip")),
                            ("files", ("b.zip", z2, "application/zip"))])
    assert r.status_code == 200
    batches = _batches(db_path)
    assert len(batches) == 1, "多 ZIP 上传必须合并为单一 import batch"
    assert batches[0]["accepted_files"] == 4
    assert batches[0]["total_files"] == 4
    assert batches[0]["source_type"] == "zip"


def test_multi_zip_upload_stages_every_file_on_disk(tmp_path):
    """每个 ZIP 的邮件都必须真实落盘到 staging。"""
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    z1 = _zip_bytes({"1.eml": _eml("one")})
    z2 = _zip_bytes({"2.eml": _eml("two")})
    _post(client, app, [("files", ("a.zip", z1, "application/zip")),
                        ("files", ("b.zip", z2, "application/zip"))])
    staging = tmp_path / "staging"
    staged = [p for p in staging.rglob("*.eml") if "_uploads" not in p.parts]
    assert len(staged) == 2


# ----------------------------------------------------------------------
# 混合 ZIP + EML
# ----------------------------------------------------------------------
def test_mixed_zip_and_eml_upload_imports_both(tmp_path):
    """1 个 EML + 1 个含 2 封的 ZIP → 3 封全部导入。"""
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    z = _zip_bytes({"nested/deep/z1.eml": _eml("zip-one"),
                    "nested/deep/z2.eml": _eml("zip-two")})
    r = _post(client, app, [("files", ("loose.eml", _eml("loose"), "message/rfc822")),
                            ("files", ("batch.zip", z, "application/zip"))])
    assert r.status_code == 200
    batches = _batches(db_path)
    assert len(batches) == 1
    assert batches[0]["accepted_files"] == 3
    assert batches[0]["source_type"] == "mixed"


def test_mixed_upload_dedup_across_zip_and_eml(tmp_path):
    """同一封邮件同时以 EML 与 ZIP 内条目出现 → 计一次，另一次为 duplicate。"""
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    same = _eml("identical")
    z = _zip_bytes({"same.eml": same})
    r = _post(client, app, [("files", ("same.eml", same, "message/rfc822")),
                            ("files", ("b.zip", z, "application/zip"))])
    assert r.status_code == 200
    batch = _batches(db_path)[0]
    assert batch["accepted_files"] == 1
    assert batch["duplicate_files"] == 1


def test_mixed_upload_rejects_bad_suffix_without_partial_batch(tmp_path):
    """非法扩展名必须整批拒绝，且不留下批次记录。"""
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    r = _post(client, app, [("files", ("ok.eml", _eml("ok"), "message/rfc822")),
                            ("files", ("bad.txt", b"nope", "text/plain"))])
    assert r.status_code == 400
    assert _batches(db_path) == []


# ----------------------------------------------------------------------
# 服务层：批次级预算与安全错误传播
# ----------------------------------------------------------------------
def test_import_uploads_shares_file_budget_across_zips(tmp_path):
    """max_files_per_batch 必须在批次级别共享，而不是每个 ZIP 各自计数。"""
    svc = ImportService(staging_root=tmp_path / "staging", max_files_per_batch=2)
    z1 = tmp_path / "a.zip"
    z2 = tmp_path / "b.zip"
    for path, name in ((z1, "a"), (z2, "b")):
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{name}1.eml", _eml(f"{name}1"))
            zf.writestr(f"{name}2.eml", _eml(f"{name}2"))
    result = svc.import_uploads([], [z1, z2])
    assert result.discovered == 4
    assert result.accepted == 2
    assert result.rejected == 2
    reasons = {it.reason for it in result.items if it.status == "rejected"}
    assert reasons == {"MAX_FILES_EXCEEDED"}


def test_import_uploads_propagates_zip_slip_across_batch(tmp_path):
    """任一批次内的 ZIP 触发 Zip Slip 必须整批中止，不得静默降级。"""
    from app.workbench.import_service import ImportSecurityError

    svc = ImportService(staging_root=tmp_path / "staging")
    good = tmp_path / "good.zip"
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(good, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("ok.eml", _eml("ok"))
    with zipfile.ZipFile(evil, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("../../escape.eml", _eml("evil"))
    try:
        svc.import_uploads([], [good, evil])
    except ImportSecurityError as exc:
        assert "ZIP_SLIP" in str(exc)
    else:
        raise AssertionError("Zip Slip 未被阻断")


def test_import_uploads_rejects_invalid_zip(tmp_path):
    from app.workbench.import_service import ImportSecurityError

    svc = ImportService(staging_root=tmp_path / "staging")
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"this is not a zip")
    try:
        svc.import_uploads([], [bad])
    except ImportSecurityError as exc:
        assert "INVALID_ZIP" in str(exc)
    else:
        raise AssertionError("无效 ZIP 未被拒绝")


# ----------------------------------------------------------------------
# 上传体积上限：超限必须在读取过程中中止
# ----------------------------------------------------------------------
def test_oversized_upload_rejected_before_full_read(tmp_path, monkeypatch):
    """超限上传必须边读边停，不能先整体读入内存再判断。"""
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    from app.dashboard.routes import imports as imports_route

    monkeypatch.setattr(imports_route, "MAX_UPLOAD_BYTES", 1024)
    r = _post(client, app, [("files", ("big.eml", b"x" * 5000, "message/rfc822"))])
    assert r.status_code == 400
    assert _batches(db_path) == []
