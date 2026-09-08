# -*- coding: utf-8 -*-
"""GP01-GP10 窗口/全文拼接误报测试。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import NEWS_SIGNAL_DIR
from app.governance.config_loader import GovernanceConfig
from app.governance.engine import GovernanceEngine


def test_gp01_separate_paragraphs_do_not_match():
    text = (
        "第一段：政府机关说明例行业务。\n\n"
        "第二段：居民申请资料补件。\n\n"
        "第三段：时间已经三个月。\n\n"
        "第四段：承办表示仍未处理。"
    )
    engine = GovernanceEngine(GovernanceConfig(NEWS_SIGNAL_DIR).load_all())
    result = engine.evaluate(text)
    assert not any(p.pattern_id == "GP01" for p in result.patterns)


def test_windows_configured_not_full_by_default():
    cfg = GovernanceConfig(NEWS_SIGNAL_DIR).load_all()
    for rule in cfg.pattern_list():
        assert rule.get("allow_full_document") is False
        windows = rule.get("window") or []
        assert "full" not in windows
        assert windows, rule.get("id")
