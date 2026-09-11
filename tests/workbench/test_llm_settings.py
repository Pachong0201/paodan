# -*- coding: utf-8 -*-
"""V5.0.2+ Workbench 自定义 LLM API 设置。"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.dashboard.server import create_app
from app.storage.database import Database
from app.workbench.llm_profiles import get_profile, profile_status
from app.workbench.models import JobRuntimeConfig
from app.workbench.pipeline_factory import build_llm_client
from app.workbench.runtime_settings import get_custom_llm_settings, set_custom_llm_settings


def _app(tmp_path: Path):
    db_path = tmp_path / "llm_settings.db"
    Database(db_path).close()
    app = create_app(db_path, import_staging_root=tmp_path / "staging")
    return app, db_path


def _token(client: TestClient) -> str:
    import re
    html = client.get("/settings").text
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def test_settings_page_has_custom_llm_form(tmp_path):
    app, _ = _app(tmp_path)
    client = TestClient(app)
    html = client.get("/settings").text
    assert "自定义 LLM API" in html
    assert "API Base URL" in html
    assert "API Key" in html


def test_save_custom_local_api_without_key_is_available(tmp_path):
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    r = client.post("/settings/llm", data={
        "csrf_token": _token(client),
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "local-model",
    }, follow_redirects=False)
    assert r.status_code == 303
    with Database(db_path) as db:
        saved = get_custom_llm_settings(db.conn)
    assert saved["base_url"] == "http://127.0.0.1:11434/v1"
    assert saved["model"] == "local-model"
    status = profile_status("custom", db_path=db_path)
    assert status["available"] is True
    profile = get_profile("custom", db_path=db_path)
    assert profile.resolved_base_url() == "http://127.0.0.1:11434/v1"
    assert profile.resolved_model() == "local-model"


def test_save_custom_external_api_requires_key(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    app, db_path = _app(tmp_path)
    client = TestClient(app)
    r = client.post("/settings/llm", data={
        "csrf_token": _token(client),
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-test",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert profile_status("custom", db_path=db_path)["available"] is False

    r2 = client.post("/settings/llm", data={
        "csrf_token": _token(client),
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-test",
        "api_key": "sk-test-token-12345678901234567890",
    }, follow_redirects=False)
    assert r2.status_code == 303
    assert profile_status("custom", db_path=db_path)["available"] is True
    html = client.get("/settings").text
    assert "sk-test-token-12345678901234567890" not in html


def test_build_llm_client_reads_custom_key_from_local_db(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    db_path = tmp_path / "llm.db"
    with Database(db_path) as db:
        set_custom_llm_settings(
            db.conn, "https://api.openai.com/v1", "gpt-test",
            api_key="sk-custom-local-db-token-1234567890")
        captured = {}

        def fake_client(api_key, base_url, model, timeout=60, policy=None):
            captured["api_key"] = api_key
            captured["base_url"] = base_url
            captured["model"] = model
            return object()

        import app.workbench.pipeline_factory as pf
        monkeypatch.setattr(pf, "LLMClient", fake_client)
        runtime = JobRuntimeConfig(
            llm_enabled=True, llm_mode="api", llm_profile_id="custom",
            llm_model="gpt-test", llm_base_url="https://api.openai.com/v1")
        build_llm_client(runtime, db=db)
    assert captured["api_key"] == "sk-custom-local-db-token-1234567890"
    assert captured["model"] == "gpt-test"
