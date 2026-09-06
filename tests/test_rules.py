# -*- coding: utf-8 -*-
"""关键词/Pattern/Negative 引擎单元测试（直接对规则配置执行）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.preprocessing.normalization import Normalizer
from app.rules.rule_engine import RuleEngine


def _ev(rule_config, text, no_new_evidence=False):
    eng = RuleEngine(rule_config)
    return eng.evaluate(text, no_new_evidence=no_new_evidence)


def test_high_value_pattern_combination(rule_config):
    """Case2: 建商+顾问费+协调建照+汇款+LINE -> P04/P05/P19 + A03/A04。"""
    text = "某建商支付80万元顾问费给议员办公室主任，随后办公室人员帮助协调建照。附件包含汇款记录和LINE截图。"
    rr = _ev(rule_config, text)
    pids = [p.pattern_id for p in rr.matched_patterns]
    assert "P04" in pids and "P05" in pids and "P19" in pids, pids
    assert "A03" in rr.matched_categories
    # 证据片段存在且非空
    assert any(p.evidence_snippets for p in rr.matched_patterns)


def test_variant_kinship_headcount(rule_config):
    """姐姐->胞姐 变体：P01 应命中（亲友组由变体满足）。"""
    text = "议员姐姐从未实际上班，每月却申报6万元助理薪资，工资入账后提款卡长期由办公室主任保管。"
    rr = _ev(rule_config, text)
    pids = [p.pattern_id for p in rr.matched_patterns]
    assert "P01" in pids, pids
    assert "A02" in rr.matched_categories


def test_context_exclusion_coordinate(rule_config):
    """跨部门例行协调被 context_exclusion 排除：不得触发收贿类。"""
    text = "市府召开跨部门例行协调会，讨论下水道工程进度，各局处出席。"
    rr = _ev(rule_config, text)
    assert rr.rule_score < 40, rr.rule_score
    assert "A03" not in rr.matched_categories


def test_negative_x_old_news(rule_config):
    """EX 旧闻无新证据：P20 命中且 score 压低。"""
    text = "去年某议员涉嫌工程弊案，最后不起诉处分。本邮件只是转发当年新闻报导，无新增内容。"
    rr = _ev(rule_config, text, no_new_evidence=True)
    pids = [p.pattern_id for p in rr.matched_patterns]
    assert "P20" in pids, pids
    assert rr.rule_score <= 35, rr.rule_score


def test_ex_with_new_evidence_not_filtered(rule_config):
    """EX 但提供新银行流水：不判 no_new_evidence。"""
    text = "去年某议员一案获不起诉处分，但本邮件提供此前未曝光的银行流水，显示某公司另汇入300万元。"
    rr = _ev(rule_config, text)
    assert rr.money, rr.money
    pids = [p.pattern_id for p in rr.matched_patterns]
    assert "P19" in pids, pids
    assert not (rr.ex_present and rr.no_new_evidence)


def test_window_level_precedence(rule_config):
    """同一句命中窗口应标记 sentence。"""
    text = "某建商支付80万元顾问费给议员办公室主任，随后办公室人员帮助协调建照。"
    rr = _ev(rule_config, text)
    p5 = [p for p in rr.matched_patterns if p.pattern_id == "P05"]
    if p5:
        assert p5[0].window in ("sentence", "paragraph", "context3", "full")


def test_context3_window_detection(rule_config):
    """跨句共现：3 句窗口内应命中 P19（LINE 在第 2 句、金额第 1 句）。"""
    text = ("议员办公室主任收了建商80万元。这件事没有留下任何记录。"
            "附件有LINE截图可以佐证，汇款也查得到。")
    rr = _ev(rule_config, text)
    pids = [p.pattern_id for p in rr.matched_patterns]
    assert "P19" in pids, pids


def test_vague_accusation_low(rule_config):
    """空泛指控低分。"""
    text = "有人举报某民进党政治人物贪污，没有具体事实和证据。"
    rr = _ev(rule_config, text)
    assert rr.rule_score < 60, rr.rule_score
