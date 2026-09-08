# -*- coding: utf-8 -*-
"""GP01-GP10 窗口/全文拼接误报测试。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import NEWS_SIGNAL_DIR
from app.governance.config_loader import GovernanceConfig
from app.governance.engine import GovernanceEngine
from app.governance.pattern_engine import GovPatternEngine


class _Cfg:
    def __init__(self, rule):
        self._rule = rule

    def pattern_list(self):
        return [self._rule]


def _rule(window, allow_full=False):
    return {
        "id": "TEST_WINDOW",
        "category": "G01",
        "name": "窗口测试",
        "required_groups": [["政府", "市府"], ["投诉", "陈情"]],
        "window": window,
        "allow_full_document": allow_full,
        "base_score": 80,
    }


def test_gp01_far_paragraphs_do_not_match():
    text = (
        "第一段：政府机关说明例行业务。\n\n"
        "填充段一。\n\n填充段二。\n\n填充段三。\n\n"
        "第五段：居民投诉服务态度。"
    )
    engine = GovernanceEngine(GovernanceConfig(NEWS_SIGNAL_DIR).load_all())
    result = engine.evaluate(text)
    assert not any(p.pattern_id == "GP01" for p in result.patterns)


def test_window_first_picks_alternative_inside_window():
    # 全局第一备选词“政府”在窗口外，但窗口内有“市府”+“投诉”，必须命中。
    text = "第一段：政府发布活动消息。\n\n第二段：市府接获民众投诉后仍未处理。"
    hit = GovPatternEngine(_Cfg(_rule(["paragraph"]))).match(text)
    assert hit and hit[0].matched_terms == ["市府", "投诉"], [h.to_dict() for h in hit]


def test_context3_adjacent_three_sentences_hit():
    text = "政府说明例行业务。市府接获民众投诉。相关单位回复。"
    hit = GovPatternEngine(_Cfg(_rule(["context3"]))).match(text)
    assert hit and hit[0].window == "context3", [h.to_dict() for h in hit]


def test_unauthorized_full_document_does_not_match():
    text = "政府发布活动消息。\n\n居民投诉服务态度。"
    hit = GovPatternEngine(_Cfg(_rule(["full"], allow_full=False))).match(text)
    assert hit == []


def test_windows_configured_not_full_by_default():
    cfg = GovernanceConfig(NEWS_SIGNAL_DIR).load_all()
    for rule in cfg.pattern_list():
        assert rule.get("allow_full_document") is False
        windows = rule.get("window") or []
        assert "full" not in windows
        assert windows, rule.get("id")
