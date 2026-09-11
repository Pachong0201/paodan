# -*- coding: utf-8 -*-
"""V5.0.1 Import & Runtime hardening gates."""
from __future__ import annotations

import inspect
import io
import re
import sys
import time
import zipfile
from email import policy
from email.message import EmailMessage
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.dashboard.routes import imports as imports_route
from app.dashboard.server import create_app
from app.storage.database import Database
from app.workbench.import_service import ImportService, UploadSource
from app.workbench.job_repository import WorkbenchRepository


def _eml(subject: str, body: str = "body") -> bytes:
    m = EmailMessage(policy=policy.default)
    m["Subject"] = subject
    m["From"] = "source@example.com"
    m["To"] = "tips@example.com"
    m["Date"] = "Mon, 01 Sep 2026 08:00:00 +0800"
    m.set_content(body)
    return m.as_bytes()


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _app(tmp_path: Path):
    db_path = tmp_path / "v501.db"
    Database(db_path).close()
    app = create_app(db_path, import_staging_root=tmp_path / "staging")
    return app, db_path


def _batches(db_path: Path):
    with Database(db_path) as db:
        return WorkbenchRepository(db.conn).list_import_batches()


# ----------------------------------------------------------------------
# multi-file field / streaming static gates
# ----------------------------------------------------------------------
def test_browser_template_and_backend_use_files_field(tmp_path):
    app, _ = _app(tmp_path)
    client = TestClient(app)
    html = client.get("/import").text
    assert 'name="files"' in html
    assert "multiple" in html
    assert 'name="file"' not in html

    src = inspect.getsource(imports_route.import_upload)
    assert "files: list[UploadFile]" in src
    assert not re.search(r"await\s+uf\.read\(\)", src)
    assert "read(READ_CHUNK_BYTES)" in src


# ----------------------------------------------------------------------
# single / multi EML / multi ZIP / mixed
# ----------------------------------------------------------------------
def test_single_eml_one_batch_ready(tmp_path):
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    r = client.post("/import", data={"csrf_token": app.state.csrf_token},
                    files=[("files", ("one.eml", _eml("single"), "message/rfc822"))])
    assert r.status_code == 200
    batches = _batches(db_path)
    assert len(batches) == 1
    assert batches[0]["accepted_files"] == 1
    assert batches[0]["status"] == "READY"
    assert batches[0]["source_type"] == "eml"
    with Database(db_path) as db:
        assert len(WorkbenchRepository(db.conn).list_ready_files(batches[0]["import_id"])) == 1


def test_multi_eml_one_batch_accepted_three(tmp_path):
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    r = client.post("/import", data={"csrf_token": app.state.csrf_token}, files=[
        ("files", ("a.eml", _eml("a"), "message/rfc822")),
        ("files", ("b.eml", _eml("b"), "message/rfc822")),
        ("files", ("c.eml", _eml("c"), "message/rfc822")),
    ])
    assert r.status_code == 200
    batches = _batches(db_path)
    assert len(batches) == 1
    assert batches[0]["accepted_files"] == 3
    assert batches[0]["total_files"] == 3
    assert batches[0]["status"] == "READY"


def test_multi_zip_all_entries_one_batch(tmp_path):
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    z1 = _zip_bytes({"a/1.eml": _eml("a1"), "a/2.eml": _eml("a2")})
    z2 = _zip_bytes({"b/1.eml": _eml("b1"), "b/2.eml": _eml("b2"), "b/3.eml": _eml("b3")})
    r = client.post("/import", data={"csrf_token": app.state.csrf_token}, files=[
        ("files", ("a.zip", z1, "application/zip")),
        ("files", ("b.zip", z2, "application/zip")),
    ])
    assert r.status_code == 200
    batches = _batches(db_path)
    assert len(batches) == 1
    assert batches[0]["total_files"] == 5
    assert batches[0]["accepted_files"] == 5
    assert batches[0]["source_type"] == "zip"


def test_mixed_eml_zip_all_entries_one_batch(tmp_path):
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    z1 = _zip_bytes({"z1/a.eml": _eml("z1a"), "z1/b.eml": _eml("z1b")})
    z2 = _zip_bytes({"z2/a.eml": _eml("z2a"), "z2/b.eml": _eml("z2b"), "z2/c.eml": _eml("z2c")})
    r = client.post("/import", data={"csrf_token": app.state.csrf_token}, files=[
        ("files", ("a.eml", _eml("a"), "message/rfc822")),
        ("files", ("z1.zip", z1, "application/zip")),
        ("files", ("b.eml", _eml("b"), "message/rfc822")),
        ("files", ("z2.zip", z2, "application/zip")),
    ])
    assert r.status_code == 200
    batches = _batches(db_path)
    assert len(batches) == 1
    assert batches[0]["total_files"] == 7
    assert batches[0]["accepted_files"] == 7
    assert batches[0]["source_type"] == "mixed"


# ----------------------------------------------------------------------
# dedup
# ----------------------------------------------------------------------
def test_cross_zip_dedup(tmp_path):
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    same = _eml("same-cross-zip")
    z1 = _zip_bytes({"a/1.eml": same})
    z2 = _zip_bytes({"b/1.eml": same})
    r = client.post("/import", data={"csrf_token": app.state.csrf_token}, files=[
        ("files", ("a.zip", z1, "application/zip")),
        ("files", ("b.zip", z2, "application/zip")),
    ])
    assert r.status_code == 200
    batch = _batches(db_path)[0]
    assert batch["accepted_files"] == 1
    assert batch["duplicate_files"] == 1


def test_eml_vs_zip_dedup(tmp_path):
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    same = _eml("same-eml-zip")
    r = client.post("/import", data={"csrf_token": app.state.csrf_token}, files=[
        ("files", ("same.eml", same, "message/rfc822")),
        ("files", ("batch.zip", _zip_bytes({"nested/same.eml": same}), "application/zip")),
    ])
    assert r.status_code == 200
    batch = _batches(db_path)[0]
    assert batch["accepted_files"] == 1
    assert batch["duplicate_files"] == 1
    assert batch["source_type"] == "mixed"


def test_cross_batch_dedup_sets_empty(tmp_path):
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    same = _eml("same-cross-batch")
    first = client.post("/import", data={"csrf_token": app.state.csrf_token},
                        files=[("files", ("A.eml", same, "message/rfc822"))])
    second = client.post("/import", data={"csrf_token": app.state.csrf_token},
                         files=[("files", ("A2.eml", same, "message/rfc822"))])
    assert first.status_code == 200 and second.status_code == 200
    batches = _batches(db_path)
    assert len(batches) == 2
    by_accepted = {b["accepted_files"]: b for b in batches}
    assert by_accepted[1]["status"] == "READY"
    assert by_accepted[0]["status"] == "EMPTY"
    assert by_accepted[0]["duplicate_files"] == 1


def test_legacy_emails_raw_sha256_dedup(tmp_path):
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    raw = _eml("legacy-raw-sha")
    import hashlib
    sha = hashlib.sha256(raw).hexdigest()
    with Database(db_path) as db:
        db.execute(
            "INSERT INTO emails(email_id, subject, body_text, raw_sha256, processed_at) VALUES (?,?,?,?,?)",
            ("legacy1", "legacy", "body", sha, "2026-01-01T00:00:00"))
    r = client.post("/import", data={"csrf_token": app.state.csrf_token},
                    files=[("files", ("legacy.eml", raw, "message/rfc822"))])
    assert r.status_code == 200
    batch = _batches(db_path)[0]
    assert batch["accepted_files"] == 0
    assert batch["duplicate_files"] == 1
    assert batch["status"] == "EMPTY"


# ----------------------------------------------------------------------
# size / file-count gates
# ----------------------------------------------------------------------
def test_batch_upload_size_gate_413_cleanup(tmp_path, monkeypatch):
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    monkeypatch.setattr(imports_route, "MAX_UPLOAD_BYTES", 1024 * 1024)
    big = _eml("big", "x" * (700 * 1024))
    r = client.post("/import", data={"csrf_token": app.state.csrf_token}, files=[
        ("files", ("a.eml", big, "message/rfc822")),
        ("files", ("b.eml", big, "message/rfc822")),
    ])
    assert r.status_code == 413
    assert _batches(db_path) == []
    uploads_dir = tmp_path / "staging" / "_uploads"
    if uploads_dir.exists():
        assert list(uploads_dir.iterdir()) == []


def test_single_file_size_gate_413(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_IMPORT_MAX_FILE_SIZE_MB", "1")
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    r = client.post("/import", data={"csrf_token": app.state.csrf_token},
                    files=[("files", ("too-big.eml", b"x" * (1024 * 1024 + 1), "message/rfc822"))])
    assert r.status_code == 413
    assert _batches(db_path) == []


def test_max_files_per_batch_shared_across_zips(tmp_path):
    svc = ImportService(staging_root=tmp_path / "staging",
                        db_path=tmp_path / "svc.db",
                        max_files_per_batch=2)
    z1 = tmp_path / "a.zip"
    z2 = tmp_path / "b.zip"
    for path, name in ((z1, "a"), (z2, "b")):
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{name}1.eml", _eml(f"{name}1"))
            zf.writestr(f"{name}2.eml", _eml(f"{name}2"))
    result = svc.import_upload_batch([UploadSource(z1, "a.zip"), UploadSource(z2, "b.zip")])
    assert result.discovered == 4
    assert result.accepted == 2
    assert result.rejected == 2
    assert {it.reason for it in result.items if it.status == "rejected"} == {"MAX_FILES_EXCEEDED"}


def test_uncompressed_size_gate(tmp_path):
    svc = ImportService(staging_root=tmp_path / "staging",
                        db_path=tmp_path / "svc.db",
                        max_total_size=200)
    data = _eml("large-ish", "x" * 500)
    # 通过真实临时文件调用，确保大小门禁发生在 staging 之前。
    a = tmp_path / "a.eml"
    b = tmp_path / "b.eml"
    a.write_bytes(data)
    b.write_bytes(data)
    result = svc.import_upload_batch([UploadSource(a, "a.eml"), UploadSource(b, "b.eml")])
    assert result.accepted == 0
    assert result.rejected == 2
    assert {it.reason for it in result.items} == {"TOTAL_SIZE_EXCEEDED"}


# ----------------------------------------------------------------------
# batch state / job start gates
# ----------------------------------------------------------------------
def test_empty_batch_not_ready_and_start_rejected(tmp_path):
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    invalid = b"this is not an eml at all"
    r = client.post("/import", data={"csrf_token": app.state.csrf_token}, files=[
        ("files", (f"bad{i}.eml", invalid, "message/rfc822")) for i in range(3)
    ])
    assert r.status_code == 200
    batch = _batches(db_path)[0]
    assert batch["accepted_files"] == 0
    assert batch["status"] == "EMPTY"
    with Database(db_path) as db:
        assert WorkbenchRepository(db.conn).list_ready_import_batches() == []
    start = client.post("/jobs/start", data={
        "csrf_token": app.state.csrf_token,
        "import_id": batch["import_id"],
        "profile_id": "template",
    })
    assert start.status_code == 409


def test_ready_start_succeeds_completed_rejected(tmp_path):
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    r = client.post("/import", data={"csrf_token": app.state.csrf_token},
                    files=[("files", ("ok.eml", _eml("ready"), "message/rfc822"))])
    assert r.status_code == 200
    batch = _batches(db_path)[0]
    assert batch["status"] == "READY"

    start = client.post("/jobs/start", data={
        "csrf_token": app.state.csrf_token,
        "import_id": batch["import_id"],
        "profile_id": "template",
    })
    assert start.status_code == 200
    job_id = start.json()["job_id"]

    # 同一个 Batch 不允许重复启动（PROCESSING 或 COMPLETED 都返回 409）。
    second = client.post("/jobs/start", data={
        "csrf_token": app.state.csrf_token,
        "import_id": batch["import_id"],
        "profile_id": "template",
    })
    assert second.status_code == 409

    status = None
    for _ in range(80):
        status = client.get(f"/jobs/{job_id}/status").json()
        if status["status"] in ("COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"):
            break
        time.sleep(0.1)
    assert status["status"] == "COMPLETED", status
    assert status["processed_count"] == status["total_count"] == 1
    third = client.post("/jobs/start", data={
        "csrf_token": app.state.csrf_token,
        "import_id": batch["import_id"],
        "profile_id": "template",
    })
    assert third.status_code == 409
    with Database(db_path) as db:
        assert WorkbenchRepository(db.conn).get_import_batch(batch["import_id"])["status"] == "COMPLETED"


def test_job_progress_counts_duplicate_and_failed_items(tmp_path, monkeypatch):
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    r = client.post("/import", data={"csrf_token": app.state.csrf_token}, files=[
        ("files", ("a.eml", _eml("job-a"), "message/rfc822")),
        ("files", ("b.eml", _eml("job-b"), "message/rfc822")),
    ])
    assert r.status_code == 200
    batch = _batches(db_path)[0]

    class FakePipeline:
        def __init__(self):
            self.calls = 0

        def process_file(self, path):
            self.calls += 1
            if self.calls == 1:
                return None
            raise RuntimeError("synthetic failure")

    monkeypatch.setattr("app.workbench.job_runner.PipelineFactory.load_rule_config", lambda: object())
    monkeypatch.setattr("app.workbench.job_runner.PipelineFactory.create",
                        lambda config, runtime, db=None: FakePipeline())

    start = client.post("/jobs/start", data={
        "csrf_token": app.state.csrf_token,
        "import_id": batch["import_id"],
        "profile_id": "template",
    })
    assert start.status_code == 200
    job_id = start.json()["job_id"]
    status = None
    for _ in range(80):
        status = client.get(f"/jobs/{job_id}/status").json()
        if status["status"] in ("COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"):
            break
        time.sleep(0.1)
    assert status["status"] == "COMPLETED", status
    assert status["processed_count"] == status["total_count"] == 2
    assert status["duplicate_count"] == 1
    assert status["failed_count"] == 1
    with Database(db_path) as db:
        items = WorkbenchRepository(db.conn).list_job_items(job_id)
    assert sorted(i["status"] for i in items) == ["DUPLICATE", "FAILED"]


def test_manifest_does_not_store_upload_temp_absolute_path(tmp_path):
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    r = client.post("/import", data={"csrf_token": app.state.csrf_token},
                    files=[("files", ("a.eml", _eml("manifest"), "message/rfc822"))])
    assert r.status_code == 200
    batch = _batches(db_path)[0]
    manifest = (tmp_path / "staging" / batch["import_id"] / "manifest.json").read_text(encoding="utf-8")
    assert "staged_path" not in manifest
    assert "_uploads" not in manifest
    assert str(tmp_path) not in manifest
