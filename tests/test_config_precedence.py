# -*- coding: utf-8 -*-
"""配置优先级：默认 < YAML < ENV < CLI。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import (load_excel_register_config, load_review_queue_config,
                        merge_config_precedence, resolve_llm_trigger_score)
from app.security.policy import load_security_policy


def test_review_queue_env_over_yaml(monkeypatch):
    monkeypatch.setenv("REVIEW_QUEUE_MIN_SCORE", "88")
    monkeypatch.setenv("REVIEW_QUEUE_PRIORITIES", "S,A")
    cfg = load_review_queue_config(Path("config"))
    assert cfg["min_score"] == 88
    assert cfg["priorities"] == ["S", "A"]


def test_excel_env_over_yaml(monkeypatch):
    monkeypatch.setenv("EXCEL_REGISTER_MIN_SCORE", "77")
    monkeypatch.setenv("EXCEL_REGISTER_PATH", "tmp/register.xlsx")
    cfg = load_excel_register_config(Path("config"))
    assert cfg["min_score"] == 77
    assert cfg["path"] == "tmp/register.xlsx"


def test_security_env_over_yaml(monkeypatch):
    monkeypatch.setenv("SECURITY_PRIVACY_LEVEL", "REDACTED_SNIPPETS")
    p = load_security_policy(config_dir="config")
    assert p.privacy_level == "REDACTED_SNIPPETS"


def test_llm_trigger_cli_over_env(monkeypatch):
    monkeypatch.setenv("LLM_TRIGGER_SCORE", "55")
    assert resolve_llm_trigger_score() == 55
    assert resolve_llm_trigger_score(70) == 70


def test_merge_precedence_helper():
    out = merge_config_precedence({"a": 1, "b": 2}, {"b": 3, "c": 4},
                                  {"c": 5}, {"c": 6})
    assert out == {"a": 1, "b": 3, "c": 6}
