# -*- coding: utf-8 -*-
"""首发渠道推荐模块验收测试（Release Route Advisor）。

覆盖：
- 配置加载（release_routes.yaml / release_route_rules.yaml）
- 特征构建（evidence_shapes / ReleaseDecisionFeatures）
- 规则引擎（RR 规则命中与多渠道/avoid 输出）
- JSON Schema 校验
- LLM 模板顾问（feature -> rule -> advisor）
- 端到端 42 条渠道样本验收门禁：
  Top-1 Accuracy >= 85%、Top-2 Coverage >= 95%、高敏感材料安全 = 0 错误首推、
  匿名来源保护 R1 错误首推 <= 5%、正式检举召回 >= 90%
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REL_EML_DIR = Path(__file__).resolve().parent / "fixtures" / "release_emails"
REL_EXP_FILE = Path(__file__).resolve().parent / "fixtures" / "expectations_release.jsonl"

REQUIRED_ROUTES = {"R1", "R2", "R3", "R4", "R5", "R6"}
REQUIRED_RULES = {"RR01", "RR02", "RR03", "RR04", "RR05", "RR06", "RR07", "RR08",
                  "RR09", "RR10", "RR11", "RR12"}
REQUIRED_SHAPES = {
    "FIRST_PERSON_TESTIMONY", "CHAT_RECORD", "AUDIO", "VIDEO", "PHOTO",
    "BANK_RECORD", "CONTRACT", "INTERNAL_DOCUMENT", "OFFICIAL_DOCUMENT",
    "PROCUREMENT_FILE", "SPREADSHEET", "DATABASE_RECORD", "ACADEMIC_DOCUMENT",
    "LOCATION_DATA", "CLASSIFIED_DOCUMENT", "ANONYMOUS_DOCUMENT",
    "MULTI_SOURCE_PACKAGE",
}
REQUIRED_RISKS = {
    "SOURCE_EXPOSURE", "PRIVACY", "DEFAMATION", "EVIDENCE_AUTHENTICITY",
    "CONTEXT_LOSS", "RETALIATION", "DESTRUCTION_OF_EVIDENCE",
    "WITNESS_COLLUSION", "CLASSIFIED_INFORMATION", "LEGAL_PROCESS_INTERFERENCE",
    "MISLEADING_OLD_NEWS", "DOCUMENT_FORGERY",
}


@pytest.fixture(scope="session")
def release_engine():
    from app.release_advisor.rule_engine import ReleaseRouteRuleEngine
    return ReleaseRouteRuleEngine()


@pytest.fixture(scope="session")
def release_expectations():
    exps = {}
    if REL_EXP_FILE.exists():
        for line in REL_EXP_FILE.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                exps[r["id"]] = r
    return exps


@pytest.fixture(scope="session")
def release_records(release_engine):
    """端到端跑完全部渠道样本（一次，供多个门禁测试复用）。"""
    from app.config import NEWS_SIGNAL_DIR
    from app.llm.screener import LLMScreener
    from app.pipeline.screening_pipeline import ScreeningPipeline
    from app.release_advisor.llm_advisor import ReleaseAdvisor
    from app.rules.config_loader import RuleConfig

    cfg = RuleConfig(NEWS_SIGNAL_DIR).load_all()
    screener = LLMScreener(cfg, mode="template")
    advisor = ReleaseAdvisor(cfg, mode="template", allow_llm=False)
    pipe = ScreeningPipeline(cfg, db=None, llm_screener=screener, allow_llm=False,
                             release_advisor=advisor)
    recs = {}
    for eml in sorted(REL_EML_DIR.glob("*.eml")):
        rec = pipe.process_file(eml)
        if rec is not None and rec.score is not None:
            recs[eml.stem] = rec
    return recs


# ---------------------------------------------------------------------------
# 一、配置完整性
# ---------------------------------------------------------------------------
def test_release_config_taxonomy(release_engine):
    assert set(release_engine.routes.keys()) == REQUIRED_ROUTES
    for rid in REQUIRED_ROUTES:
        assert release_engine.route_name(rid), rid
    assert release_engine.min_score == 60.0


def test_release_config_rules(release_engine):
    rule_ids = set(release_engine.rule_ids)
    assert REQUIRED_RULES.issubset(rule_ids), REQUIRED_RULES - rule_ids
    # RR01-RR12 每条都指向合法渠道
    for rid in REQUIRED_RULES:
        rule = release_engine._rules[rid]
        prim = rule.get("primary")
        if prim:
            assert prim in REQUIRED_ROUTES, (rid, prim)
        for r in (rule.get("secondary") or []):
            assert r in REQUIRED_ROUTES, (rid, r)
        for r in (rule.get("avoid") or []):
            assert r in REQUIRED_ROUTES, (rid, r)


def test_release_route_names_and_risks(release_engine):
    """风险矩阵与证据形态元数据齐全。"""
    # 12 类风险中文标签完整（取一代表验证加载）
    for risk in ("SOURCE_EXPOSURE", "CLASSIFIED_INFORMATION", "DESTRUCTION_OF_EVIDENCE"):
        assert release_engine.risk_label(risk), risk
    # 正式机关类型映射
    for code in ("PROSECUTOR", "ACADEMIC_ETHICS", "CONTROL_YUAN", "NONE"):
        assert release_engine.formal_label(code), code


# ---------------------------------------------------------------------------
# 二、JSON Schema
# ---------------------------------------------------------------------------
def test_release_schema_valid():
    from app.release_advisor.schema import validate_result
    ok_data = {
        "primary_route": "R3", "primary_route_name": "深度调查报道型",
        "secondary_routes": ["R5"], "avoid_routes": ["R1"],
        "route_confidence": 0.88, "prepublication_verification_required": True,
        "verification_before_release": ["核验银行流水"],
        "formal_referral_recommended": True, "formal_referral_type": ["PROSECUTOR"],
        "release_risks": ["EVIDENCE_AUTHENTICITY"], "reason": "x",
        "recommended_release_sequence": [
            {"step": 1, "action": "EDITORIAL_VERIFICATION", "route": "", "reason": "r"},
            {"step": 2, "action": "FORMAL_REFERRAL", "route": "PROSECUTOR", "reason": "r"},
            {"step": 3, "action": "PUBLIC_RELEASE", "route": "R3", "reason": "r"},
        ],
        "headline_angle": "", "editor_note": "",
    }
    ok, cleaned, errs = validate_result(ok_data)
    assert ok, errs
    assert cleaned["primary_route"] == "R3"


def test_release_schema_fixes_invalid():
    from app.release_advisor.schema import validate_result
    bad = {"primary_route": "R9", "avoid_routes": ["R3"], "route_confidence": 9,
           "formal_referral_type": ["POLICE"], "release_risks": ["HACKING"]}
    ok, cleaned, errs = validate_result(bad)
    assert not ok
    assert cleaned["primary_route"] == ""
    assert cleaned["route_confidence"] <= 1.0
    assert cleaned["formal_referral_type"] == ["NONE"]
    assert cleaned["release_risks"] == []


def test_release_schema_primary_not_in_avoid():
    from app.release_advisor.schema import validate_result
    data = {"primary_route": "R1", "avoid_routes": ["R1", "R4"],
            "secondary_routes": ["R2"], "formal_referral_type": ["NONE"]}
    ok, cleaned, errs = validate_result(data)
    assert "R1" not in cleaned["avoid_routes"]


# ---------------------------------------------------------------------------
# 三、特征构建
# ---------------------------------------------------------------------------
def test_evidence_shapes_extraction():
    """证据形态多选抽取：银行流水邮件应检出 BANK_RECORD 等形态。"""
    from app.models import EmailDocument, FinalScore, LLMResult, RuleResult
    from app.release_advisor.feature_builder import ReleaseFeatureBuilder

    b = ReleaseFeatureBuilder()
    # 构造规则结果（normalized 含证据词）
    rr = RuleResult(normalized="建商匯款八十萬元給議員，附件有銀行流水與LINE對話截圖")
    rr.money = [{"raw": "80萬", "amount": 800000, "currency": "TWD"}]
    llm = LLMResult(categories=["A03"], evidence_stage="E1")
    fs = FinalScore(final_score=92, priority="S")
    feats = b.build("x1", rr, llm, fs, EmailDocument(email_id="x1"))
    assert "BANK_RECORD" in feats.evidence_shapes
    assert "CHAT_RECORD" in feats.evidence_shapes
    assert feats.has_money_flow is True


def test_first_person_and_anonymity_detection():
    from app.models import FinalScore, LLMResult
    from app.release_advisor.feature_builder import ReleaseFeatureBuilder
    from app.models import RuleResult

    b = ReleaseFeatureBuilder()
    # 实名第一人称性骚
    rr1 = RuleResult(normalized="我是工讀生，我遭到主任性騷擾，我願意實名對外發聲")
    llm1 = LLMResult(categories=["A11", "A12"])
    f1 = b.build("x2", rr1, llm1, FinalScore(final_score=90, priority="S"))
    assert f1.has_first_person_testimony is True
    assert f1.source_requests_anonymity is False
    # 匿名第一人称
    rr2 = RuleResult(normalized="我遭性騷擾，我要求保護身分不願意具名")
    f2 = b.build("x3", rr2, llm1, FinalScore(final_score=90, priority="S"))
    assert f2.source_requests_anonymity is True


def test_classified_and_anonymous_docs():
    from app.models import FinalScore, LLMResult
    from app.release_advisor.feature_builder import ReleaseFeatureBuilder
    from app.models import RuleResult

    b = ReleaseFeatureBuilder()
    rr = RuleResult(normalized="匿名提供的極機密軍事文件，沒有metadata，無法確認來源")
    llm = LLMResult(categories=["A17"])
    f = b.build("x4", rr, llm, FinalScore(final_score=90, priority="S"))
    assert f.has_classified_material is True
    assert f.has_anonymous_documents is True


# ---------------------------------------------------------------------------
# 四、规则引擎语义（规范用例 RR01-RR12 直接构造特征验证）
# ---------------------------------------------------------------------------
def _mkf(**kw):
    from app.release_advisor.models import ReleaseDecisionFeatures
    base = dict(priority_level="A", final_score=80)
    base.update(kw)
    return ReleaseDecisionFeatures(**base)


def test_rr01_first_person_named_goes_R1(release_engine):
    eng = release_engine
    f = _mkf(categories=["A11", "A12"], evidence_shapes=["CHAT_RECORD"],
             has_first_person_testimony=True)
    rec = eng.recommend(f)
    assert rec.primary_route == "R1"
    assert "R2" in rec.secondary_routes


def test_rr02_anonymous_goes_R2_avoid_R1(release_engine):
    eng = release_engine
    f = _mkf(categories=["A11", "A12"], evidence_shapes=["CHAT_RECORD"],
             has_first_person_testimony=True, source_requests_anonymity=True)
    rec = eng.recommend(f)
    assert rec.primary_route == "R2"
    assert "R1" in rec.avoid_routes


def test_rr03_bankflow_bribery_goes_R5(release_engine):
    eng = release_engine
    f = _mkf(categories=["A03"], evidence_shapes=["BANK_RECORD"], has_money_flow=True,
             has_power_action=True)
    rec = eng.recommend(f)
    assert rec.primary_route == "R5"
    assert rec.formal_referral_recommended is True
    assert "PROSECUTOR" in rec.formal_referral_type


def test_rr04_procurement_goes_R3_secondary_R4(release_engine):
    eng = release_engine
    f = _mkf(categories=["A06"], evidence_shapes=["PROCUREMENT_FILE"])
    rec = eng.recommend(f)
    assert rec.primary_route == "R3"
    assert "R4" in rec.secondary_routes


def test_rr05_thesis_goes_R4_academic_ethics(release_engine):
    eng = release_engine
    f = _mkf(categories=["A01"], evidence_shapes=["ACADEMIC_DOCUMENT"])
    rec = eng.recommend(f)
    assert rec.primary_route == "R4"
    assert "ACADEMIC_ETHICS" in rec.formal_referral_type


def test_rr08_private_photo_goes_R2(release_engine):
    eng = release_engine
    f = _mkf(categories=["A14"], evidence_shapes=["PHOTO", "CHAT_RECORD"],
             public_interest_established=True)
    rec = eng.recommend(f)
    assert rec.primary_route == "R2"


def test_rr09_classified_goes_R6_avoid_R1_R4(release_engine):
    eng = release_engine
    f = _mkf(categories=["A17"], evidence_shapes=["CLASSIFIED_DOCUMENT"],
             has_classified_material=True)
    rec = eng.recommend(f)
    assert rec.primary_route == "R6"
    assert "R1" in rec.avoid_routes and "R4" in rec.avoid_routes


def test_rr10_anonymous_doc_goes_R6(release_engine):
    eng = release_engine
    f = _mkf(categories=["A17"], evidence_shapes=["ANONYMOUS_DOCUMENT"],
             has_anonymous_documents=True)
    rec = eng.recommend(f)
    assert rec.primary_route == "R6"
    assert "R1" in rec.avoid_routes and "R4" in rec.avoid_routes


def test_old_news_no_new_info_no_release(release_engine):
    eng = release_engine
    f = _mkf(categories=["A03"], evidence_shapes=["OFFICIAL_DOCUMENT"],
             known_old_case=True, contains_new_information=False)
    rec = eng.recommend(f)
    assert rec.primary_route == ""
    assert rec.route_confidence < 0.3


# ---------------------------------------------------------------------------
# 五、端到端 42 条样本验收门禁
# ---------------------------------------------------------------------------
def _pool(rec):
    rel = rec.release_recommendation or {}
    return {rel.get("primary_route")} | set(rel.get("secondary_routes") or [])


def test_release_testset_count(release_expectations):
    """渠道测试集 >= 40 条。"""
    assert len(release_expectations) >= 40


def test_top1_accuracy_gate(release_records, release_expectations):
    """Top-1 Accuracy >= 85%（人工预设主渠道）。"""
    n = ok = 0
    for rid, exp in release_expectations.items():
        if not exp.get("count_accuracy") or not exp.get("top1"):
            continue
        rec = release_records.get(rid)
        if rec is None:
            continue
        n += 1
        rel = rec.release_recommendation or {}
        if rel.get("primary_route") == exp["top1"]:
            ok += 1
    assert n >= 30, f"精确样本不足 {n}"
    assert ok / n >= 0.85, f"Top-1 {ok}/{n}"


def test_top2_coverage_gate(release_records, release_expectations):
    """Top-2 Coverage >= 95%（正确渠道落在 primary+secondary）。"""
    n = ok = 0
    for rid, exp in release_expectations.items():
        if not exp.get("count_accuracy") or not (exp.get("top1") or exp.get("top2")):
            continue
        rec = release_records.get(rid)
        if rec is None:
            continue
        n += 1
        target = {exp["top1"]} if exp.get("top1") else set()
        target |= set(exp.get("top2") or [])
        if target & _pool(rec):
            ok += 1
    assert n >= 35, f"覆盖样本不足 {n}"
    assert ok / n >= 0.95, f"Top-2 {ok}/{n}"


def test_sensitive_material_safety_gate(release_records, release_expectations):
    """A17/classified/anonymous 样本 R1/R4 错误首推率 = 0%。"""
    bad = n = 0
    for rid, exp in release_expectations.items():
        if not exp.get("no_r1r4"):
            continue
        rec = release_records.get(rid)
        if rec is None:
            continue
        rel = rec.release_recommendation or {}
        n += 1
        if rel.get("primary_route") in ("R1", "R4"):
            bad += 1
    assert n >= 3
    assert bad == 0, f"高敏感材料 R1/R4 错误首推 {bad}/{n}"


def test_source_protection_gate(release_records, release_expectations):
    """明确匿名的性骚/职场/内部爆料 R1 错误首推率 <= 5%。"""
    bad = n = 0
    for rid, exp in release_expectations.items():
        if not exp.get("no_r1"):
            continue
        rec = release_records.get(rid)
        if rec is None:
            continue
        rel = rec.release_recommendation or {}
        n += 1
        if rel.get("primary_route") == "R1":
            bad += 1
    assert n >= 5
    assert bad / n <= 0.05, f"匿名来源 R1 错误首推 {bad}/{n}"


def test_formal_referral_recall_gate(release_records, release_expectations):
    """涉及金流/收贿/贿选/组织犯罪/泄密/灭证样本的正式检举建议召回 >= 90%。"""
    n = ok = 0
    for rid, exp in release_expectations.items():
        if not exp.get("formal"):
            continue
        rec = release_records.get(rid)
        if rec is None:
            continue
        rel = rec.release_recommendation or {}
        n += 1
        if rel.get("formal_referral_recommended"):
            exp_types = set(exp.get("formal_types") or [])
            got_types = set(rel.get("formal_referral_type") or [])
            if exp_types & got_types:
                ok += 1
    assert n >= 10
    assert ok / n >= 0.90, f"正式检举召回 {ok}/{n}"


def test_cd_never_release_advised(release_records, release_expectations):
    """C/D 级邮件不产生渠道建议（除低置信不推荐外）。"""
    for rid, exp in release_expectations.items():
        if exp.get("expect_sa") is False:
            rec = release_records.get(rid)
            if rec is None:
                continue
            assert rec.score.priority in ("C", "D"), rid
            rel = rec.release_recommendation
            if rel:
                assert rel.get("primary_route") in ("", None), rid


def test_verification_and_sequence_output(release_records):
    """S/A/B 渠道建议必须携带核验清单与多阶段序列。"""
    from app.release_advisor.formatter import render_workbench
    checked = 0
    for rid, rec in release_records.items():
        rel = rec.release_recommendation
        if not rel:
            continue
        assert rel.get("reason"), rid
        assert rel.get("prepublication_verification_required") is not None, rid
        assert rel.get("verification_before_release"), rid
        assert rel.get("recommended_release_sequence"), rid
        block = render_workbench(rel)
        assert "【推荐首发】" in block or "【不建议】" in block
        checked += 1
    assert checked >= 10


# ---------------------------------------------------------------------------
# 六、SQLite / CSV / JSONL 集成
# ---------------------------------------------------------------------------
def test_sqlite_release_recommendation_persisted(tmp_path):
    """S/A/B 线索的渠道建议落库 release_recommendations；D 级不产生条目。"""
    from app.config import NEWS_SIGNAL_DIR
    from app.llm.screener import LLMScreener
    from app.pipeline.screening_pipeline import ScreeningPipeline
    from app.release_advisor.llm_advisor import ReleaseAdvisor
    from app.rules.config_loader import RuleConfig
    from app.storage.database import Database

    cfg = RuleConfig(NEWS_SIGNAL_DIR).load_all()
    db = Database(tmp_path / "test_rel.db")
    screener = LLMScreener(cfg, mode="template")
    advisor = ReleaseAdvisor(cfg, mode="template", allow_llm=False)
    pipe = ScreeningPipeline(cfg, db=db, llm_screener=screener, allow_llm=False,
                             release_advisor=advisor)
    # S 级：重划金流（应 R5）
    rec1 = pipe.process_file(str(REL_EML_DIR / "rc26_land_rezone_cash.eml"))
    assert rec1.score.priority == "S" and rec1.release_recommendation is not None
    # D 级：空泛举报（不应有渠道建议）
    rec2 = pipe.process_file(str(REL_EML_DIR / "rc27_vague_no_evidence.eml"))
    assert rec2.score.priority == "D"
    rows = db.query("SELECT email_id, primary_route FROM release_recommendations")
    ids = {r[0]: r[1] for r in rows}
    assert rec1.email_id in ids and ids[rec1.email_id] == "R5"
    assert rec2.email_id not in ids
    # 字段完整性
    row = db.query("SELECT secondary_routes, formal_referral_type, release_risks, "
                   "recommended_release_sequence FROM release_recommendations "
                   "WHERE email_id=?", (rec1.email_id,))[0]
    import json as _json
    assert "R3" in _json.loads(row[0])
    assert "PROSECUTOR" in _json.loads(row[1])
    assert len(_json.loads(row[3])) >= 2
    db.close()


def test_csv_release_columns(tmp_path):
    """priority_queue.csv 增加 5 个首发渠道字段。"""
    import csv as _csv
    from app.models import EmailDocument, FinalScore, RuleResult, ScreeningRecord
    from app.reports.exporter import export_csv

    rec = ScreeningRecord(
        email_id="x1",
        email=EmailDocument(email_id="x1", subject="測試", sender="a@b.tw"),
        rule=RuleResult(),
        score=FinalScore(final_score=88, priority="A"),
        release_recommendation={
            "primary_route": "R5", "secondary_routes": ["R3"],
            "formal_referral_recommended": True, "release_risks": ["EVIDENCE_AUTHENTICITY"],
            "reason": "理由", "route_confidence": 0.9,
            "avoid_routes": [], "verification_before_release": [],
            "recommended_release_sequence": [], "formal_referral_type": ["PROSECUTOR"],
        },
    )
    out = tmp_path / "q.csv"
    export_csv([rec], out, min_priority_value=0)
    with open(out, encoding="utf-8-sig", newline="") as f:
        rows = list(_csv.DictReader(f))
    assert len(rows) == 1
    row = rows[0]
    for col in ("recommended_release_route", "secondary_release_routes",
                "formal_referral_recommended", "release_risk", "release_reason"):
        assert col in row, col
    assert row["recommended_release_route"] == "R5"
    assert row["secondary_release_routes"] == "R3"
    assert row["formal_referral_recommended"] == "是"


def test_jsonl_release_field(tmp_path):
    """screening_results.jsonl 行内含 release_recommendation 对象。"""
    from app.models import EmailDocument, FinalScore, ScreeningRecord
    from app.reports.exporter import export_jsonl

    rec = ScreeningRecord(
        email_id="x2",
        email=EmailDocument(email_id="x2", subject="t", sender="a@b.tw"),
        score=FinalScore(final_score=92, priority="S"),
        release_recommendation={"primary_route": "R3", "route_confidence": 0.8,
                                "reason": "r", "secondary_routes": [], "avoid_routes": [],
                                "verification_before_release": [], "release_risks": [],
                                "recommended_release_sequence": []},
    )
    out = tmp_path / "r.jsonl"
    export_jsonl([rec], out, min_priority_value=0)
    data = json.loads(out.read_text(encoding="utf-8").strip())
    assert data["release_recommendation"]["primary_route"] == "R3"


def test_threshold_60_only_sab():
    """阈值配置：score<60 或 C/D 不进渠道判断（引擎层 min_score=60）。"""
    from app.release_advisor.rule_engine import ReleaseRouteRuleEngine
    eng = ReleaseRouteRuleEngine()
    assert eng.min_score == 60.0
