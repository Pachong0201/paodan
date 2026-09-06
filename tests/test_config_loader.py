# -*- coding: utf-8 -*-
"""规则包加载测试：taxonomy/keywords/patterns/negative/scoring 100% 加载 + 统计真实读取."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_all_rule_files_load(rule_config):
    assert all(rule_config.load_status.values()), rule_config.load_status


def test_taxonomy_18_categories(rule_config):
    cats = rule_config.category_ids
    assert len(cats) == 18
    assert cats == [f"A{i:02d}" for i in range(1, 19)]


def test_keyword_stats_positive(rule_config):
    s = rule_config.keyword_stats()
    for k in ("H", "M", "C", "E", "S", "X"):
        assert s.get(k, 0) > 0, f"关键词类型 {k} 为空"


def test_pattern_count_20(rule_config):
    pats = rule_config.pattern_list()
    ids = [p["id"] for p in pats]
    assert ids == [f"P{i:02d}" for i in range(1, 21)]


def test_negative_rules_present(rule_config):
    rules = rule_config.negative_rules()
    assert len(rules) == 8
    assert [r["id"] for r in rules] == [f"N{i:02d}" for i in range(1, 9)]
    assert len(rule_config.x_terms()) > 0


def test_scoring_rules_present(rule_config):
    sd = rule_config.scoring_data()
    dims = sd.get("dimensions", {})
    assert set(dims.keys()) == {
        "target_relevance", "behavior_severity", "evidence_quality",
        "relationship_chain", "specificity", "novelty", "public_interest"}
    assert sd.get("priority_bands")


def test_llm_prompt_loaded(rule_config):
    assert len(rule_config.llm_prompt) > 500
    assert "爆料邮箱智能筛选" in rule_config.llm_prompt or "爆料" in rule_config.llm_prompt


def test_all_categories_have_keywords(rule_config):
    """18 个类别都要有真实执行路径（词典词）。"""
    idx = rule_config.build_keyword_index()
    cats_with_words = set()
    for items in idx.values():
        for it in items:
            if it["category"] != "GLOBAL":
                cats_with_words.add(it["category"])
    assert cats_with_words == {f"A{i:02d}" for i in range(1, 19)}, \
        f"缺词典词类别: {sorted({f'A{i:02d}' for i in range(1,19)} - cats_with_words)}"
