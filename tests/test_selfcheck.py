# -*- coding: utf-8 -*-
"""Selfcheck：合法配置 PASS；非法 Governance 配置 FAIL。"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import NEWS_SIGNAL_DIR
from app.main import selfcheck
from app.rules.config_loader import RuleConfig


def test_selfcheck_valid(capsys):
    cfg = RuleConfig(NEWS_SIGNAL_DIR).load_all()
    assert selfcheck(cfg, NEWS_SIGNAL_DIR) == 0
    out = capsys.readouterr().out
    for text in ("[OK] A categories", "[OK] P patterns", "[OK] G categories",
                 "[OK] security.yaml", "[OK] schema version", "[OK] LLM_TRIGGER_SCORE"):
        assert text in out


def test_selfcheck_invalid_governance_fails(tmp_path, capsys):
    # 构造 config/news_signal 临时目录，复制有效规则后故意引用不存在的 G99
    tmp_config = tmp_path / "config"
    news = tmp_config / "news_signal"
    shutil.copytree(NEWS_SIGNAL_DIR, news)
    pattern_file = news / "governance_pattern_rules.yaml"
    text = pattern_file.read_text(encoding="utf-8")
    text = text.replace("category: G01", "category: G99", 1)
    pattern_file.write_text(text, encoding="utf-8")
    # 复制 security.yaml，避免 selfcheck 因 security 缺失而掩盖 Governance 失败
    shutil.copy2(Path("config/security.yaml"), tmp_config / "security.yaml")
    cfg = RuleConfig(NEWS_SIGNAL_DIR).load_all()
    assert selfcheck(cfg, news) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "Governance" in out
