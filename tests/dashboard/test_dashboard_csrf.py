# -*- coding: utf-8 -*-
"""Strict CSRF gate."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi.testclient import TestClient

from app.dashboard.server import create_app

from .db_utils import close_db, create_schema, insert_email, insert_review_queue, insert_score


def _seed(path):
    db = create_schema(path)
    today = date.today().isoformat()
    insert_email(db, "csrf1", "csrf", processed_at=f"{today}T08:00:00")
    insert_score(db, "csrf1", "A", 85)
    insert_review_queue(db, "csrf1", "A", 85, "summary")
    close_db(db)
    return create_app(path)


def _post(client, app, token=None, origin=None):
    data = {"status": "PRIORITY", "editor_note": "note"}
    if token is not None:
        data["csrf_token"] = token
    headers = {}
    if origin:
        headers["Origin"] = origin
    return client.post("/emails/csrf1/review", data=data, headers=headers)


def test_csrf_gates(tmp_path):
    app = _seed(tmp_path / "d.db")
    client = TestClient(app)
    token = app.state.csrf_token
    assert _post(client, app, token=token).status_code == 200
    assert _post(client, app, token=None).status_code == 403
    assert _post(client, app, token="wrong").status_code == 403
    assert _post(client, app, token=token, origin="http://evil.example.com").status_code == 403
    assert _post(client, app, token=token, origin="http://localhost:8765").status_code == 200
    assert _post(client, app, token=token, origin="http://127.0.0.1:8765").status_code == 200
