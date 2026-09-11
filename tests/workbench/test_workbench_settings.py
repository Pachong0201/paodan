# -*- coding: utf-8 -*-
"""V5.0 settings page: API key hiding, CSRF, profile selection."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi.testclient import TestClient

from app.dashboard.server import create_app
from app.storage.database import Database


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-super-secret-test-token")
    db_path = tmp_path / "s.db"
    Database(db_path).close()
    return create_app(db_path, import_staging_root=tmp_path / "staging"), db_path


def test_settings_hides_api_key_and_requires_csrf(tmp_path, monkeypatch):
    app, db_path = _app(tmp_path, monkeypatch)
    client = TestClient(app)
    html = client.get("/settings").text
    assert "sk-super-secret-test-token" not in html
    assert "已配置" in html
    # missing/wrong CSRF -> 403
    assert client.post("/settings/profile", data={"profile_id": "off"}).status_code == 403
    assert client.post("/settings/profile", data={"profile_id": "off",
                                                  "csrf_token": "wrong"}).status_code == 403
    ok = client.post("/settings/profile", data={"profile_id": "off",
                                                "csrf_token": app.state.csrf_token})
    assert ok.status_code == 200
    assert ok.json()["selected_profile"] == "off"
