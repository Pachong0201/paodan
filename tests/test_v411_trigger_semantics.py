# -*- coding: utf-8 -*-
"""V4.1.1 P1-6: RULE_TRIGGER_EXTRA semantics restored."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import NEWS_SIGNAL_DIR
from app.llm.screener import LLMScreener
from app.models import EmailDocument, KeywordHit, LLMResult, PatternHit, RuleResult
from app.pipeline.screening_pipeline import ScreeningPipeline
from app.rules.config_loader import RuleConfig


def _pipe():
    cfg = RuleConfig(NEWS_SIGNAL_DIR).load_all()
    pipe = ScreeningPipeline(cfg, db=None, llm_screener=LLMScreener(cfg, mode="template"),
                             allow_llm=True, llm_trigger_score=35)
    pipe.rule_trigger_extra = True
    calls = []
    pipe.llm_screener.screen = lambda *a, **k: (calls.append(1), LLMResult(llm_status="ok"))[1]
    return pipe, calls


def _doc():
    return EmailDocument(email_id="x", subject="s", sender="a@example.com",
                         body_text="body")


def test_ordinary_low_score_does_not_trigger():
    pipe, calls = _pipe()
    assert pipe._maybe_llm(_doc(), RuleResult(rule_score=30)) is None
    assert calls == []


def test_extra_high_value_pattern_triggers():
    pipe, calls = _pipe()
    rr = RuleResult(rule_score=30, matched_patterns=[
        PatternHit(pattern_id="P01", category="A03", name="x", pattern_score=80)])
    assert pipe._maybe_llm(_doc(), rr) is not None
    assert len(calls) == 1


def test_extra_e_evidence_target_money_triggers():
    pipe, calls = _pipe()
    rr = RuleResult(rule_score=30, target_persons_found=["张三"],
                    matched_keywords={"E": [KeywordHit(term="汇款单", ktype="E", category="GLOBAL")]},
                    money=[{"amount": 800000, "currency": "TWD"}])
    assert pipe._maybe_llm(_doc(), rr) is not None
    assert len(calls) == 1


def test_score_threshold_triggers():
    pipe, calls = _pipe()
    assert pipe._maybe_llm(_doc(), RuleResult(rule_score=40)) is not None
    assert len(calls) == 1


def test_extra_disabled_blocks_extra_cases():
    pipe, calls = _pipe()
    pipe.rule_trigger_extra = False
    rr = RuleResult(rule_score=30, matched_patterns=[
        PatternHit(pattern_id="P01", category="A03", name="x", pattern_score=80)])
    assert pipe._maybe_llm(_doc(), rr) is None
    assert calls == []


def test_env_extra_disabled(monkeypatch):
    monkeypatch.setenv("RULE_TRIGGER_EXTRA", "0")
    cfg = RuleConfig(NEWS_SIGNAL_DIR).load_all()
    pipe = ScreeningPipeline(cfg, db=None, llm_screener=LLMScreener(cfg, mode="template"),
                             allow_llm=True)
    assert pipe.rule_trigger_extra is False
