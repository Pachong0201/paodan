# -*- coding: utf-8 -*-
"""评分/流水线/质量门槛测试（60 条样本端到端）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _sa(r):
    return r.score is not None and r.score.priority in ("S", "A")


# ---------- 质量门槛 ----------
def test_high_value_recall_gate(all_records, expectations):
    """人工定义 S/A 高价值样本召回 >= 90%。"""
    pos = [i for i, e in expectations.items() if e.get("expect_sa")]
    hits = sum(1 for i in pos if i in all_records and _sa(all_records[i]))
    assert hits / len(pos) >= 0.90, f"高价值召回 {hits}/{len(pos)}"


def test_evidence_recall_gate(all_records, expectations):
    """E 类原始证据高价值样本 S/A 召回 >= 95%。"""
    ev_ids = {"hv01", "hv02", "hv03", "hv04", "hv05", "hv06", "hv07", "hv08", "hv09",
              "hv10", "hv11", "hv12", "hv15", "hv18", "hv19", "hv20",
              "im01", "im02", "im05", "im08", "im09",
              "edge02", "edge04", "edge06", "edge09", "edge10"}
    pos = [i for i, e in expectations.items() if e.get("expect_sa")]
    ev_pos = [i for i in pos if any(i.startswith(k) for k in ev_ids) or i in ev_ids]
    hits = sum(1 for i in ev_pos if i in all_records and _sa(all_records[i]))
    assert hits / len(ev_pos) >= 0.95, f"E类证据召回 {hits}/{len(ev_pos)}"


def test_implicit_recall_gate(all_records, expectations):
    """隐性线索（无 贪污/收贿/违法/弊案 词）S/A 召回 >= 90%。"""
    imp = [i for i, e in expectations.items()
           if e.get("expect_sa") and (i.startswith("im") or i == "edge10_boss_request")]
    hits = sum(1 for i in imp if i in all_records and _sa(all_records[i]))
    assert hits / len(imp) >= 0.90, f"隐性召回 {hits}/{len(imp)}"


def test_hard_negative_false_positive(all_records, expectations):
    """困难负样本误入 S/A <= 10%。"""
    neg = [i for i, e in expectations.items() if e.get("expect_sa") is False]
    fp = sum(1 for i in neg if i in all_records and _sa(all_records[i]))
    assert fp / len(neg) <= 0.10, f"负样本误报 {fp}/{len(neg)}"


def test_ex_old_news_never_s(all_records):
    """EX 旧闻无新证据不得进 S。"""
    for i in ("edge08_no_prosecute_old", "neg20_old_outcome_dup"):
        rec = all_records.get(i)
        assert rec is not None
        assert rec.score.priority != "S"
        assert rec.score.final_score < 60


def test_ex_with_new_evidence_promoted(all_records):
    """EX + 新证据不得被过滤。"""
    rec = all_records.get("edge09_new_evidence_after_ex")
    assert rec is not None and _sa(rec)
    assert any(p.pattern_id == "P19" for p in rec.rule.matched_patterns)


# ---------- 边界 Case1-10 ----------
def test_boundary_cases(all_records):
    cases = {
        "edge01_contact_only": False,
        "edge02_builder_fee": True,
        "edge03_sister_public_assistant": False,
        "edge04_sister_no_work": True,
        "edge05_legal_procure": False,
        "edge06_prebid_spec": True,
        "edge07_vague_accuse": False,
        "edge08_no_prosecute_old": False,
        "edge09_new_evidence_after_ex": True,
        "edge10_boss_request": True,
    }
    for cid, want_sa in cases.items():
        rec = all_records.get(cid)
        assert rec is not None, f"{cid} 无结果"
        got = _sa(rec)
        assert got == want_sa, f"{cid}: 期望S/A={want_sa} 实际 {rec.score.priority} {rec.score.final_score}"


# ---------- 可解释性 ----------
def test_sab_explainability(all_records):
    """所有 S/A/B 必须包含 matched_keywords/matched_patterns/evidence_snippets/分数/摘要/核查清单。"""
    for i, rec in all_records.items():
        if rec.score.priority not in ("S", "A", "B"):
            continue
        r, l = rec.rule, rec.llm
        assert r.matched_keywords, i
        assert len(rec.summary_zh) >= 40, f"{i} 摘要过短"
        assert l.reason_for_attention or rec.summary_zh, i
        assert rec.verification_targets, f"{i} 缺核查清单"
        # evidence_snippets 由 matched_keywords 或 pattern 提供
        snippets = r.evidence_snippets or [s for p in r.matched_patterns for s in p.evidence_snippets]
        assert snippets, i


# ---------- LLM JSON schema ----------
def test_llm_schema_validation():
    from app.llm.schemas import validate_result
    full = {
        "relevant": True, "priority_level": "A", "importance_score": 80,
        "target_persons": ["某人"], "target_organizations": ["某公司"],
        "categories": ["A03"], "subcategories": [],
        "specific_behaviors": ["收顾问费"], "evidence_stage": "E1",
        "allegation_status": "待核实爆料", "related_entities": {},
        "projects_or_cases": [], "money_or_benefits": [],
        "power_actions": [], "evidence_items": [], "suspicious_phrases": [],
        "relationship_chain": [], "negative_or_exculpatory_evidence": [],
        "new_information": [], "known_old_information": [],
        "verification_targets": ["核验汇款"], "public_interest_reason": "涉及公职伦理",
        "one_sentence_summary": "x", "reason_for_attention": "y",
        "needs_human_review": True, "confidence": 0.8,
    }
    ok, cleaned, errs = validate_result(full)
    assert ok, errs
    # 非法阶段/类别会被修正并报错
    bad = dict(full)
    bad["categories"] = ["A99"]
    bad["evidence_stage"] = "E9"
    ok2, _, errs2 = validate_result(bad)
    assert not ok2


def test_template_result_structure(all_records):
    """模板兜底也要满足 schema（llm_status=template 时字段齐全）。"""
    for i, rec in list(all_records.items())[:10]:
        l = rec.llm
        assert l.llm_status in ("ok", "template", "degraded", "failed")
        assert isinstance(l.categories, list)
        assert l.evidence_stage in ("E0", "E1", "E2", "E3", "E4", "E5", "EX")


# ---------- 金额抽取 ----------
def test_money_extraction():
    from app.preprocessing.extractors import parse_amount
    for raw, expect in [("80万", 800000), ("80萬元", 800000), ("800,000", 800000),
                        ("新台币80万元", 800000), ("人民币20万元", 200000)]:
        hits = parse_amount(raw)
        assert any(abs(h.amount - expect) < 1 for h in hits), (raw, hits)


def test_money_no_single_char_false_positive():
    from app.preprocessing.extractors import parse_amount
    hits = parse_amount("某议员贪污被起诉，记者查证中")
    assert all(h.amount >= 10 for h in hits)
