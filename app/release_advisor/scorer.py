"""规则层与 LLM 建议的融合评分器。

融合原则：
- 安全硬约束优先于一切：规则层的 avoid（R1/R4 对高敏感材料、R1 对匿名来源）为不可突破项；
- 规则层有明确主渠道(primary)时作为锚点，LLM 只能在其未定或明显冲突时修正；
- LLM 未调用/失败时直接使用规则结果（template/failed 回退）。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .models import ReleaseDecisionFeatures, ReleaseRecommendation
from .schema import ROUTE_NAMES
from .rule_engine import ReleaseRouteRuleEngine

# 规则层置信权重：规则命中越多越可信
RULE_W = 0.6
LLM_W = 0.4


def _clean_routes(routes: List[str]) -> List[str]:
    seen = []
    for r in routes:
        r = str(r or "").strip().upper()
        if r in {"R1", "R2", "R3", "R4", "R5", "R6"} and r not in seen:
            seen.append(r)
    return seen


def merge_recommendations(rule_rec: ReleaseRecommendation, llm_out: dict,
                          engine: ReleaseRouteRuleEngine) -> ReleaseRecommendation:
    """把 LLM 输出与规则结果融合，返回新 ReleaseRecommendation（rule_rec 不被修改）。"""
    out = ReleaseRecommendation(
        email_id=rule_rec.email_id,
        score=rule_rec.score,
        source="rule+llm",
        llm_status="ok",
    )
    # 安全 avoid 集（规则层特征 avoid 与渠道规则 avoid 的并集为硬约束）
    hard_avoid: List[str] = _clean_routes(rule_rec.avoid_routes)
    # LLM 建议的 primary/secondary（过滤非法值）
    llm_primary = str(llm_out.get("primary_route") or "").strip().upper()
    if llm_primary not in {"R1", "R2", "R3", "R4", "R5", "R6"}:
        llm_primary = ""
    llm_secondary = _clean_routes(llm_out.get("secondary_routes") or [])
    llm_avoid = _clean_routes(llm_out.get("avoid_routes") or [])
    # 融合 avoid：硬约束 + LLM 建议 avoid
    avoid_out = list(dict.fromkeys(hard_avoid + [a for a in llm_avoid if a not in hard_avoid]))

    rule_primary = rule_rec.primary_route
    # 规则已给出主渠道：除非 LLM 主渠道落入 hard_avoid，否则沿用规则主渠道
    # （安全优先：宁可保守）
    if llm_primary and llm_primary in hard_avoid:
        llm_primary = ""
    if rule_primary and rule_primary not in hard_avoid:
        primary = rule_primary
    elif llm_primary and llm_primary not in hard_avoid:
        primary = llm_primary
    elif rule_primary:
        primary = rule_primary
    elif llm_primary:
        primary = llm_primary
    else:
        primary = ""
    # secondary：规则 secondary（非 hard_avoid）+ LLM secondary（非 hard_avoid、非 primary）
    sec_rule = [r for r in _clean_routes(rule_rec.secondary_routes) if r not in hard_avoid]
    sec_llm = [r for r in llm_secondary if r not in hard_avoid and r not in sec_rule]
    secondary = list(dict.fromkeys(sec_rule + sec_llm))
    if primary in secondary:
        secondary.remove(primary)
    secondary = secondary[:3]

    # 置信度：规则锚定 + LLM 一致时上调
    conf_rule = float(rule_rec.route_confidence or 0.4)
    conf_llm = max(0.0, min(1.0, float(llm_out.get("route_confidence") or 0.0)))
    agree = bool(primary and primary == llm_primary)
    if primary and agree:
        conf = min(0.97, RULE_W * conf_rule + LLM_W * conf_llm + 0.08)
    elif primary:
        conf = max(0.3, RULE_W * conf_rule + LLM_W * conf_llm)
    else:
        conf = max(0.05, min(0.35, conf_llm * 0.5))

    out.primary_route = primary
    out.primary_route_name = ROUTE_NAMES.get(primary, "")
    out.secondary_routes = secondary
    out.avoid_routes = avoid_out
    out.route_confidence = round(conf, 2)
    # LLM 语义字段
    out.prepublication_verification_required = bool(
        llm_out.get("prepublication_verification_required", True) or
        rule_rec.prepublication_verification_required)
    out.verification_before_release = _merge_str_lists(
        rule_rec.verification_before_release,
        [str(x) for x in (llm_out.get("verification_before_release") or [])])[:12]
    out.formal_referral_recommended = bool(
        llm_out.get("formal_referral_recommended", False) or
        rule_rec.formal_referral_recommended)
    fr = [str(x).strip().upper() for x in (llm_out.get("formal_referral_type") or [])
          if str(x).strip().upper() != "NONE"]
    rule_fr = [t for t in rule_rec.formal_referral_type if t != "NONE"]
    out.formal_referral_type = list(dict.fromkeys(rule_fr + fr))[:4] or ["NONE"]
    risks_rule = list(rule_rec.release_risks)
    risks_llm = [str(x).strip().upper() for x in (llm_out.get("release_risks") or [])]
    out.release_risks = list(dict.fromkeys(risks_rule + [r for r in risks_llm
                                                         if r not in risks_rule]))[:8]
    # reason/headline/editor_note 优先 LLM 文案
    out.reason = str(llm_out.get("reason") or rule_rec.reason or "")
    out.headline_angle = str(llm_out.get("headline_angle") or "")
    out.editor_note = str(llm_out.get("editor_note") or "")
    out.rule_hits = list(rule_rec.rule_hits)
    # 序列：优先 LLM（规范化），缺失时用规则序列
    llm_seq = llm_out.get("recommended_release_sequence") or []
    seq = [dict(x) for x in llm_seq if isinstance(x, dict)] if llm_seq else \
        list(rule_rec.recommended_release_sequence or [])
    out.recommended_release_sequence = seq[:10]
    return out


def _merge_str_lists(a: List[str], b: List[str]) -> List[str]:
    out = list(a)
    for x in b:
        if x and x not in out:
            out.append(x)
    return out
