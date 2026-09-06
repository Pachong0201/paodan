# -*- coding: utf-8 -*-
"""V3 具名渠道推荐模块验收测试（Named Channel Recommender）。

覆盖：
- 配置完整性（channel_entities / named_channel_rules / historical_cases）
- 状态过滤（inactive/historical/unknown 不进 Top 推荐）
- stale 检查
- 候选仅来自实体库（LLM schema 拦截池外实体）
- fit_score / 角色分类
- location-aware（台南场景 -> 台南地检署/谢龙介型）
- 60+ 样本门禁：
  Named Top-1 Accuracy >= 80%、Named Top-3 Coverage >= 95%、
  Role Accuracy >= 95%、Sensitive Safety(PTT/Threads 0 错误 Top1)、
  Anonymous Source Protection(公开个人社媒 0 错误 Top1)、
  Top3 多样性 >= 12 个不同实体
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIX = Path(__file__).resolve().parent / "fixtures"
NAMED_EXP_FILE = FIX / "expectations_named.jsonl"

# 所有参与 V3 评估的 EML 目录
EML_DIRS = [FIX / "emails", FIX / "release_emails"]

# 安全硬约束映射（门禁用）
SENSITIVE_IDS = {  # A17/匿名文件场景禁 PTT/Threads 等
    "platform_ptt", "platform_threads", "platform_x",
    "pundit_wu_tzechia", "pundit_chiu_yi",
}
ANON_SOCIAL_IDS = {"platform_facebook", "platform_threads", "platform_x"}
ANON_ACTOR_IDS = {"actor_huang_kuochang", "actor_hsu_chiaohsin", "actor_wang_hungwei"}


@pytest.fixture(scope="session")
def named_loader():
    from app.named_channel.entity_loader import ChannelEntityLoader
    return ChannelEntityLoader()


@pytest.fixture(scope="session")
def named_expectations():
    exps = {}
    if NAMED_EXP_FILE.exists():
        for line in NAMED_EXP_FILE.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                exps[r["id"]] = r
    return exps


@pytest.fixture(scope="session")
def named_pipeline():
    """V1+V2+V3 全链路 pipeline（模板模式）。"""
    from app.config import NEWS_SIGNAL_DIR
    from app.llm.screener import LLMScreener
    from app.pipeline.screening_pipeline import ScreeningPipeline
    from app.release_advisor.llm_advisor import ReleaseAdvisor
    from app.named_channel.advisor import NamedChannelAdvisor
    from app.rules.config_loader import RuleConfig

    cfg = RuleConfig(NEWS_SIGNAL_DIR).load_all()
    screener = LLMScreener(cfg, mode="template")
    rel_advisor = ReleaseAdvisor(cfg, mode="template", allow_llm=False)
    named_advisor = NamedChannelAdvisor(mode="template", allow_llm=False)
    return ScreeningPipeline(cfg, db=None, llm_screener=screener, allow_llm=False,
                             release_advisor=rel_advisor, named_advisor=named_advisor)


@pytest.fixture(scope="session")
def named_records(named_pipeline, named_expectations):
    """端到端跑全部样本，返回有 V3 输出的记录。"""
    recs = {}
    for d in EML_DIRS:
        for eml in sorted(d.glob("*.eml")):
            rid = eml.stem
            rec = named_pipeline.process_file(eml)
            if rec is not None and rec.score is not None \
                    and rec.score.priority in ("S", "A", "B") \
                    and rec.named_channel_recommendation:
                recs[rid] = rec
    return recs


def _grp(named: dict, key: str) -> list:
    return named.get(key) or []


def _ids(named: dict, key: str) -> list:
    return [str(x.get("entity_id")) for x in _grp(named, key)]


# ---------------------------------------------------------------------------
# 一、配置完整性
# ---------------------------------------------------------------------------
def test_named_config_entities(named_loader):
    c = named_loader.counts()
    assert c["media"] >= 15, c
    assert c["political_actors"] >= 10, c
    assert c["pundits"] >= 3, c
    assert c["platforms"] >= 5, c
    assert c["formal_authorities"] >= 10, c
    assert c["local_channels"] >= 1, c
    assert c["rules"] >= 30, c
    assert c["historical_cases"] >= 30, c
    assert c["internal_newsroom"] >= 1, c


def test_named_required_entities(named_loader):
    """必须存在的最低名单。"""
    for eid in ("media_mirror", "media_tvbs", "media_udn", "media_ltn", "media_ct",
                "media_ettoday", "media_sanli", "media_ftv", "media_pts", "media_storm",
                "media_newtalk", "media_nextapple", "media_ctwant", "media_upmedia",
                "media_taipao", "media_beautimode", "media_rwnews",
                "actor_huang_kuochang", "actor_wang_hungwei", "actor_hsu_chiaohsin",
                "actor_ling_tao", "actor_hsieh_lungchieh", "actor_chen_chiaohua",
                "actor_chiu_henchih", "actor_chen_wanhui", "actor_yu_shuhui",
                "actor_hou_hanting",
                "pundit_huang_yangming", "pundit_wu_tzechia", "pundit_chiu_yi",
                "platform_facebook", "platform_threads", "platform_ptt",
                "platform_x", "platform_youtube"):
        assert named_loader.get(eid) is not None, eid


def test_named_historical_status_filtered(named_loader):
    """historical 实体（菱传媒）不得进入 active 媒体列表。"""
    media = [m.get("id") for m in named_loader.media_list()]
    assert "media_rwnews" not in media
    body = named_loader.get("media_rwnews")
    assert body.get("status") == "historical"


def test_named_stale_warning(named_loader):
    """last_verified_date 超过 180 天输出 stale 警告。"""
    stale = [w for w in named_loader.stale_warnings
             if "media_rwnews" in w or "channel profile may be stale" in w]
    assert stale


def test_named_entity_scores_present(named_loader):
    """所有实体有 0-100 评分维度与 status/last_verified_date。"""
    for eid, body in named_loader.entities.items():
        scores = body.get("scores") or {}
        for k in ("reach", "verification", "source_protection", "speed",
                  "complexity", "controversy_risk"):
            assert k in scores, (eid, k)
            v = scores[k]
            assert 0 <= v <= 100, (eid, k, v)
        assert body.get("status") in ("active", "inactive", "historical", "unknown")
        assert body.get("last_verified_date"), eid


def test_named_media_differentiated(named_loader):
    """媒体画像差异化：不得 all 类别/证据。"""
    for eid in ("media_mirror", "media_tvbs", "media_udn", "media_pts",
                "media_nextapple", "media_ctwant"):
        body = named_loader.get(eid)
        cats = set(body.get("strong_categories") or [])
        assert len(cats) < 18, eid  # 不得全类别
        assert len(body.get("strong_categories") or []) >= 3, eid
        assert body.get("source_protection") in (
            "very_high", "high", "medium", "low")
    # 公视不应以私德照片为首选画像
    pts = named_loader.get("media_pts")
    assert "A14" not in (pts.get("strong_categories") or [])
    # 壹苹/CTWANT 不强于复杂金流
    na = named_loader.get("media_nextapple")
    assert "A03" not in (na.get("strong_categories") or [])


# ---------------------------------------------------------------------------
# 二、规则与候选
# ---------------------------------------------------------------------------
def test_named_rules_reference_existing_entities(named_loader):
    """NC 规则引用的实体必须存在于实体库。"""
    from app.named_channel.candidate_engine import NamedChannelEngine
    eng = NamedChannelEngine(named_loader)
    for rid, rule in named_loader.rules.items():
        for key in ("media", "disclosure_actors", "amplifiers", "formal_channels",
                    "platforms", "local_channels", "avoid"):
            for eid in rule.get(key) or []:
                assert named_loader.get(eid) is not None, (rid, key, eid)


def test_named_no_empty_output_when_sab(named_records):
    """S/A/B 且 V2 有路线 -> V3 必有媒体或正式渠道输出。"""
    n = 0
    for rid, rec in named_records.items():
        named = rec.named_channel_recommendation
        media = _ids(named, "recommended_media")
        formal = _ids(named, "recommended_formal_channels")
        assert media or formal, rid
        n += 1
    assert n >= 60, f"可用样本不足 {n}"


def test_named_cd_no_output(named_pipeline):
    """C/D 级样本不产生 V3 具名推荐。"""
    for rid in ("rc43_lawmaker_fight", "rc45_public_figure_affair",
                "rc46_fire_association_subsidy", "rc47_ghost_voter"):
        path = FIX / "release_emails" / f"{rid}.eml"
        if not path.exists():
            continue
        rec = named_pipeline.process_file(path)
        assert rec is not None and rec.score is not None
        assert rec.score.priority in ("C", "D"), rid
        assert rec.named_channel_recommendation is None, rid


def test_named_sample_count(named_expectations):
    """V3 测试标注样本 >= 60。"""
    assert len(named_expectations) >= 60


def test_named_lists_limited(named_records):
    """每组数量上限：媒体<=3、人物<=3、放大<=2、正式<=2、平台<=2。"""
    for rid, rec in named_records.items():
        named = rec.named_channel_recommendation
        assert len(_grp(named, "recommended_media")) <= 3, rid
        assert len(_grp(named, "recommended_disclosure_actors")) <= 3, rid
        assert len(_grp(named, "recommended_amplifiers")) <= 2, rid
        assert len(_grp(named, "recommended_formal_channels")) <= 2, rid
        assert len(_grp(named, "recommended_platforms")) <= 2, rid


def test_named_fit_scores_in_range(named_records):
    for rid, rec in named_records.items():
        named = rec.named_channel_recommendation
        for key in ("recommended_media", "recommended_disclosure_actors",
                    "recommended_amplifiers", "recommended_formal_channels"):
            for it in _grp(named, key):
                s = it.get("fit_score")
                assert 0 <= s <= 100, (rid, it)
                assert it.get("reason"), (rid, it)


# ---------------------------------------------------------------------------
# 三、Schema：LLM 只能从候选池选择
# ---------------------------------------------------------------------------
def test_named_schema_blocks_out_of_pool():
    from app.named_channel.schema import validate_llm_output
    pool = {"media_mirror", "actor_huang_kuochang"}
    out = {"recommended_media": [{"id": "media_hacker_news", "name": "x",
                                  "fit_score": 99}],
           "recommended_disclosure_actors": [],
           "recommended_amplifiers": [], "recommended_formal_channels": [],
           "recommended_platforms": [], "avoid_named_channels": [],
           "recommended_sequence": []}
    ok, cleaned, errors = validate_llm_output(out, pool)
    assert not ok
    assert cleaned["recommended_media"] == []  # 池外实体被拦截
    out2 = {"recommended_media": [{"id": "media_mirror", "name": "镜周刊",
                                   "fit_score": 95}],
            "recommended_disclosure_actors": [],
            "recommended_amplifiers": [], "recommended_formal_channels": [],
            "recommended_platforms": [], "avoid_named_channels": ["platform_ptt"],
            "recommended_sequence": []}
    ok2, cleaned2, _ = validate_llm_output(out2, pool)
    assert ok2 and cleaned2["recommended_media"][0]["id"] == "media_mirror"


def test_named_role_validity(named_records):
    VALID = {"FIRST_RELEASE", "INVESTIGATIVE", "DISCLOSURE", "AMPLIFIER",
             "VERIFIER", "FORMAL_REFERRAL", "DATA_ANALYSIS", "LOCAL_NETWORK",
             "SOURCE_PROTECTION"}
    for rid, rec in named_records.items():
        named = rec.named_channel_recommendation
        for key in ("recommended_media", "recommended_disclosure_actors",
                    "recommended_amplifiers", "recommended_formal_channels"):
            for it in _grp(named, key):
                for r in it.get("roles") or []:
                    assert r in VALID, (rid, it)


# ---------------------------------------------------------------------------
# 四、门禁指标
# ---------------------------------------------------------------------------
def _top_media_ids(named):
    return _ids(named, "recommended_media")


def _top_actor_ids(named):
    return _ids(named, "recommended_disclosure_actors")


def _top_formal_ids(named):
    return _ids(named, "recommended_formal_channels")


def test_named_top1_accuracy_gate(named_records, named_expectations):
    """Named Top-1 Accuracy >= 80%：媒体 top1 命中人工预设的 media 名单之一即算对。"""
    n = ok = 0
    for rid, rec in named_records.items():
        exp = named_expectations.get(rid)
        if not exp or not exp.get("media"):
            continue
        n += 1
        top1 = _top_media_ids(rec.named_channel_recommendation)
        if top1 and top1[0] in exp["media"]:
            ok += 1
    assert n >= 40, f"样本不足 {n}"
    assert ok / n >= 0.80, f"Top-1 {ok}/{n}"


def test_named_top3_coverage_gate(named_records, named_expectations):
    """Named Top-3 Coverage >= 95%：正确渠道进入 Top3。"""
    n = ok = 0
    for rid, rec in named_records.items():
        exp = named_expectations.get(rid)
        if not exp or not exp.get("media"):
            continue
        n += 1
        top3 = _top_media_ids(rec.named_channel_recommendation)[:3]
        if set(exp["media"]) & set(top3):
            ok += 1
    assert n >= 40
    assert ok / n >= 0.95, f"Top-3 {ok}/{n}"


def test_named_actor_top3_gate(named_records, named_expectations):
    """揭弊人物 Top3 覆盖（有期望 actor 的样本）。"""
    n = ok = 0
    for rid, rec in named_records.items():
        exp = named_expectations.get(rid)
        if not exp or not exp.get("actor"):
            continue
        n += 1
        top3 = _top_actor_ids(rec.named_channel_recommendation)[:3]
        if set(exp["actor"]) & set(top3):
            ok += 1
    assert n >= 15, f"actor 样本不足 {n}"
    assert ok / n >= 0.90, f"actor Top3 {ok}/{n}"


def test_named_formal_top2_gate(named_records, named_expectations):
    """正式机关 Top2 覆盖。"""
    n = ok = 0
    for rid, rec in named_records.items():
        exp = named_expectations.get(rid)
        if not exp or not exp.get("formal"):
            continue
        n += 1
        top2 = _top_formal_ids(rec.named_channel_recommendation)[:2]
        if set(exp["formal"]) & set(top2):
            ok += 1
    assert n >= 40
    assert ok / n >= 0.95, f"formal Top2 {ok}/{n}"


def test_role_accuracy_gate(named_records, named_expectations):
    """Role Accuracy >= 95%：正式机关组只能出现 FORMAL_REFERRAL/VERIFIER 角色。"""
    bad = n = 0
    for rid, rec in named_records.items():
        named = rec.named_channel_recommendation
        # 角色准确性：媒体组无 FORMAL_REFERRAL，正式组无 AMPLIFIER
        for it in _grp(named, "recommended_media"):
            n += 1
            if set(it.get("roles") or []) & {"FORMAL_REFERRAL"}:
                bad += 1
        for it in _grp(named, "recommended_formal_channels"):
            n += 1
            if set(it.get("roles") or []) & {"AMPLIFIER", "FIRST_RELEASE"}:
                bad += 1
        for it in _grp(named, "recommended_amplifiers"):
            n += 1
            if "FORMAL_REFERRAL" in (it.get("roles") or []):
                bad += 1
    assert n > 100
    assert bad / n <= 0.05, f"Role 错误 {bad}/{n}"


def test_sensitive_safety_gate(named_records, named_expectations):
    """A17/机密/匿名文件样本：PTT/Threads 错误 Top1 = 0。"""
    bad = n = 0
    for rid, rec in named_records.items():
        exp = named_expectations.get(rid)
        if not (exp and exp.get("no_social_top")):
            continue
        n += 1
        named = rec.named_channel_recommendation
        top1_media = _top_media_ids(named)[:1]
        platforms = _ids(named, "recommended_platforms")
        if set(top1_media) & SENSITIVE_IDS or set(platforms) & SENSITIVE_IDS:
            bad += 1
    assert n >= 8, f"敏感样本不足 {n}"
    assert bad == 0, f"Sensitive Safety 失败 {bad}/{n}"


def test_anonymous_source_protection_gate(named_records, named_expectations):
    """匿名/身份保护样本：公开个人社媒错误 Top1 = 0。"""
    bad = n = 0
    for rid, rec in named_records.items():
        exp = named_expectations.get(rid)
        if not (exp and exp.get("no_social_top")):
            continue
        n += 1
        named = rec.named_channel_recommendation
        platforms = _ids(named, "recommended_platforms")
        actors = _ids(named, "recommended_disclosure_actors")
        if (set(platforms[:1]) & ANON_SOCIAL_IDS) or (set(actors[:1]) & ANON_ACTOR_IDS):
            bad += 1
    assert n >= 8
    assert bad == 0, f"匿名保护失败 {bad}/{n}"


def test_media_diversity_gate(named_records):
    """Top3 渠道多样性：至少 12 个不同媒体实体进入过 Top3。"""
    seen = set()
    for rid, rec in named_records.items():
        named = rec.named_channel_recommendation
        for eid in _top_media_ids(named)[:3]:
            seen.add(eid)
    assert len(seen) >= 12, f"多样性不足: {len(seen)} {seen}"


def test_avoid_includes_sensitive_platforms(named_records, named_expectations):
    """A17 样本 avoid 必须含 PTT 等社交平台。"""
    checked = 0
    for rid, rec in named_records.items():
        exp = named_expectations.get(rid)
        if not (exp and exp.get("no_fun_top")):
            continue
        named = rec.named_channel_recommendation
        avoid = named.get("avoid_named_channels") or []
        assert "platform_ptt" in avoid or "platform_threads" in avoid, rid
        checked += 1
    assert checked >= 5


def test_location_tainan_route(named_records, named_expectations):
    """台南光电样本：谢龙介型地方渠道与台南地检署进入候选。"""
    rec = named_records.get("rc_tainan_solar")
    assert rec is not None, "台南样本未生成 V3 输出"
    named = rec.named_channel_recommendation
    actors = _top_actor_ids(named)
    formals = _top_formal_ids(named)
    local = [str(x.get("entity_id")) for x in _grp(named, "local_channels")]
    assert "actor_hsieh_lungchieh" in actors, actors
    assert "authority_tainan_dpo" in formals or "authority_prosecutor_generic" in formals, formals
    assert local, local


def test_victim_first_for_sexharass_named(named_records, named_expectations):
    """性骚实名：平台/媒体优先，政治人物不排在当事人前。"""
    rec = named_records.get("rc02_sexharass_named")
    assert rec is not None
    named = rec.named_channel_recommendation
    platforms = _ids(named, "recommended_platforms")
    assert "platform_facebook" in platforms or "platform_threads" in platforms, platforms
    # 政治人物揭弊渠道应为空或不排第一
    actors = _top_actor_ids(named)
    assert "actor_huang_kuochang" not in actors, actors


# ---------------------------------------------------------------------------
# 五、历史案例库
# ---------------------------------------------------------------------------
def test_historical_cases_structure(named_loader):
    cases = named_loader.historical_cases
    assert len(cases) >= 30
    for c in cases:
        for k in ("case_id", "title", "date", "category", "first_release_source",
                  "first_release_type", "evidence_shape", "result"):
            assert c.get(k), (c.get("case_id"), k)


def test_historical_role_stats(named_loader):
    stats = named_loader.role_stats()
    assert stats["first_release_media"] >= 5
    assert stats["first_release_formal"] >= 5
    assert stats["first_release_actor"] >= 3
    assert stats["total"] >= 30
