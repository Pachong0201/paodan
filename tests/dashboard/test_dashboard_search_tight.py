# -*- coding: utf-8 -*-
"""Search only subject/summary, not whole llm_json."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi.testclient import TestClient

from app.dashboard.server import create_app

from .db_utils import close_db, create_schema, insert_email, insert_llm, insert_review_queue, insert_score


def _seed(path):
    db = create_schema(path)
    today = date.today().isoformat()
    insert_email(db, "s1", "市府停電投訴", processed_at=f"{today}T08:00:00")
    insert_score(db, "s1", "A", 85)
    insert_review_queue(db, "s1", "A", 85, "停電摘要")
    insert_llm(db, "s1", {"reason_for_attention": "只在reason出現的詞X",
                          "one_sentence_summary": "停電摘要"})
    close_db(db)
    return create_app(path)


def _shows(html: str, email_id: str) -> bool:
    """按「邮件行链接」判断邮件是否出现，而不是裸 id 子串。

    页面内嵌随机 CSRF token（secrets.token_urlsafe(24)）。裸 "s1" 有约 0.75%
    概率恰好落在 token 里（例如 44MeCyRNd_s1O29mNL1w7tqDBMs），会造成偶发假失败；
    这正是曾把 CI 打红的原因。/emails/<id> 只可能来自真实邮件行。
    """
    return f"/emails/{email_id}" in html


def test_search_subject_summary(tmp_path):
    client = TestClient(_seed(tmp_path / "d.db"))
    assert _shows(client.get("/", params={"q": "停電"}).text, "s1")
    assert not _shows(client.get("/", params={"q": "只在reason出現"}).text, "s1")
