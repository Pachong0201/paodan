# -*- coding: utf-8 -*-
"""V1+V4 Governance 双轨进入 summary/V2/V3/persistence 的集成测试。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import NEWS_SIGNAL_DIR
from app.llm.screener import LLMScreener
from app.models import EmailDocument
from app.named_channel.advisor import NamedChannelAdvisor
from app.pipeline.screening_pipeline import ScreeningPipeline
from app.release_advisor.llm_advisor import ReleaseAdvisor
from app.rules.config_loader import RuleConfig


def _pipe(db=None):
    cfg = RuleConfig(NEWS_SIGNAL_DIR).load_all()
    return ScreeningPipeline(
        cfg, db=db, llm_screener=LLMScreener(cfg, mode="template"), allow_llm=False,
        release_advisor=ReleaseAdvisor(cfg, mode="template", allow_llm=False),
        named_advisor=NamedChannelAdvisor(mode="template", allow_llm=False))


def test_governance_only_integration():
    doc = EmailDocument(email_id="g1", subject="垃圾车投诉", sender="a@example.com",
                        body_text="连续半年同一区垃圾车时间混乱，整个社区多次打1999仍未改善，已有居民投诉截图。")
    rec = _pipe().process_document(doc)
    assert rec.unified_signals.primary_track == "GOVERNANCE"
    assert rec.unified_signals.political_categories == []
    assert rec.governance_categories
    assert rec.score.priority in ("S", "A", "B")
    assert "治理" in rec.summary_zh or "垃圾" in rec.summary_zh
    assert any("核对" in t for t in rec.verification_targets)
    assert rec.release_recommendation is not None
    assert rec.release_recommendation.get("primary_route")
    named = rec.named_channel_recommendation or {}
    assert any(named.get(k) for k in ("recommended_media", "recommended_formal_channels",
                                      "recommended_disclosure_actors", "local_channels"))


def test_mixed_track_integration():
    doc = EmailDocument(email_id="m1", subject="采购与行政延宕", sender="a@example.com",
                        body_text="政府采购标案疑似护航特定厂商，同时同一区群体申请补助案件行政长期延宕，整个社区多次陈情1999仍未处理。")
    rec = _pipe().process_document(doc)
    assert rec.unified_signals.primary_track == "MIXED"
    assert rec.rule.matched_categories
    assert rec.governance_categories
    assert rec.release_recommendation is not None


def test_governance_sqlite_persistence(tmp_path):
    from app.storage.database import Database
    import json

    db = Database(tmp_path / "gov.db")
    rec = _pipe(db).process_document(EmailDocument(
        email_id="gdb", subject="治理", sender="a@example.com",
        body_text="连续半年同一区垃圾车时间混乱，整个社区多次打1999仍未改善，已有居民投诉截图。"))
    row = db.query_one("SELECT * FROM governance_results WHERE email_id=?", (rec.email_id,))
    assert row is not None
    assert json.loads(row["categories_json"])
    assert row["score"] is not None
    assert row["priority"] in ("S", "A", "B")
    assert json.loads(row["patterns_json"])
    db.close()
