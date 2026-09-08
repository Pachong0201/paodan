# -*- coding: utf-8 -*-
"""Track score correctness."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi.testclient import TestClient

from app.dashboard.server import create_app

from .db_utils import close_db, create_schema, insert_email, insert_review_queue, insert_score


def _seed(path, eid, pol, gov, final, track, unified_override=None):
    db = create_schema(path)
    today = date.today().isoformat()
    insert_email(db, eid, f"subject {eid}", processed_at=f"{today}T08:00:00")
    unified = unified_override or {
        "political_categories": ["A01"] if pol else [],
        "governance_categories": ["G01"] if gov else [],
        "political_score": pol, "governance_score": gov,
        "primary_track": track,
    }
    insert_score(db, eid, "A", final, primary_track=track, governance_score=gov, unified=unified)
    insert_review_queue(db, eid, "A", final, "summary")
    close_db(db)
    return create_app(path)


def _detail(path, eid):
    from app.dashboard.db import connect_dashboard
    from app.dashboard.queries import get_email_detail
    with connect_dashboard(str(path)) as conn:
        return get_email_detail(conn, eid)


def test_mixed_score(tmp_path):
    path = tmp_path / "d.db"
    _seed(path, "mx", 55, 84, 84, "MIXED")
    detail = _detail(path, "mx")
    assert detail.political_score == 55
    assert detail.governance_score == 84
    assert detail.final_score == 84


def test_political_only_score(tmp_path):
    path = tmp_path / "d.db"
    _seed(path, "po", 80, 0, 80, "POLITICAL")
    detail = _detail(path, "po")
    assert detail.political_score == 80
    assert detail.governance_score == 0


def test_governance_only_score(tmp_path):
    path = tmp_path / "d.db"
    _seed(path, "go", 20, 85, 85, "GOVERNANCE")
    detail = _detail(path, "go")
    assert detail.political_score == 20
    assert detail.governance_score == 85
