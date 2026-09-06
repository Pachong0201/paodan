"""记者工作台文本格式化：把渠道建议渲染为记者可读摘要块。"""
from __future__ import annotations

from typing import Optional

from ..models import ScreeningRecord
from .models import ReleaseRecommendation
from .rule_engine import ReleaseRouteRuleEngine

_RISK_LABELS = {
    "SOURCE_EXPOSURE": "来源暴露", "PRIVACY": "隐私侵害", "DEFAMATION": "诽谤风险",
    "EVIDENCE_AUTHENTICITY": "证据真实性未核", "CONTEXT_LOSS": "上下文缺失",
    "RETALIATION": "报复风险", "DESTRUCTION_OF_EVIDENCE": "灭证风险",
    "WITNESS_COLLUSION": "串证风险", "CLASSIFIED_INFORMATION": "机密外泄",
    "LEGAL_PROCESS_INTERFERENCE": "干扰调查程序", "MISLEADING_OLD_NEWS": "旧闻误导",
    "DOCUMENT_FORGERY": "文件伪造",
}


def _rel_attr(rel, key, default=None):
    """兼容 ReleaseRecommendation 对象与 dict."""
    if isinstance(rel, dict):
        return rel.get(key, default)
    return getattr(rel, key, default)


def render_workbench(rel, screening: Optional[ScreeningRecord] = None,
                     engine: Optional[ReleaseRouteRuleEngine] = None) -> str:
    """生成【推荐首发】等区块文本；无建议时输出提示。rel: dict 或 ReleaseRecommendation。"""
    if rel is None:
        return ""
    lines: list = []
    if screening is not None and screening.score is not None:
        cats = "、".join((screening.llm.categories if screening.llm else []) or
                         (screening.rule.matched_categories if screening.rule else []))
        lines.append(f"【新闻线索】{cats or '未分类'}")
        lines.append(f"【评分】{screening.score.final_score:.0f} / {screening.score.priority}")
        if screening.summary_zh:
            lines.append(f"【为什么值得看】{screening.summary_zh}")
    primary = _rel_attr(rel, "primary_route") or ""
    primary_name = _rel_attr(rel, "primary_route_name") or ""
    conf = float(_rel_attr(rel, "route_confidence") or 0.0)
    if primary:
        lines.append(f"【推荐首发】{primary} {primary_name or ''}（置信 {conf:.0%}）")
    else:
        lines.append("【推荐首发】不建议立即公开首发")
    secondary = _rel_attr(rel, "secondary_routes") or []
    if secondary:
        names = "、".join(f"{r} {engine.route_name(r) if engine else ''}".strip()
                          for r in secondary)
        lines.append(f"【后续公开】{names}")
    avoid = _rel_attr(rel, "avoid_routes") or []
    if avoid:
        names = "、".join(f"{r} {engine.route_name(r) if engine else ''}".strip()
                          for r in avoid)
        lines.append(f"【不建议】{names}")
    reason = _rel_attr(rel, "reason") or ""
    if reason:
        lines.append(f"【原因】{reason}")
    verify_req = bool(_rel_attr(rel, "prepublication_verification_required", True))
    verify_list = _rel_attr(rel, "verification_before_release") or []
    if verify_req or verify_list:
        lines.append("【发布前核验】")
        items = verify_list or ["先完成基础事实核验"]
        for i, item in enumerate(items[:8], 1):
            lines.append(f"{i}. {item}")
    if _rel_attr(rel, "formal_referral_recommended"):
        ftypes = [t for t in (_rel_attr(rel, "formal_referral_type") or []) if t != "NONE"]
        if ftypes:
            labels = "、".join(engine.formal_label(t) for t in ftypes) if engine else "、".join(ftypes)
            lines.append(f"【建议先行检举】{labels}")
    risks = _rel_attr(rel, "release_risks") or []
    if risks:
        labels = "、".join(_RISK_LABELS.get(r, r) for r in risks)
        lines.append(f"【主要风险】{labels}")
    seq = _rel_attr(rel, "recommended_release_sequence") or []
    if seq:
        lines.append("【建议发布序列】")
        for s in seq[:6]:
            if not isinstance(s, dict):
                s = s.to_dict() if hasattr(s, "to_dict") else {}
            action = str(s.get("action") or "")
            route = str(s.get("route") or "")
            reason = str(s.get("reason") or "")
            lines.append(f"Step{s.get('step', '?')} {action}" +
                         (f"({route})" if route else "") +
                         (f"：{reason}" if reason else ""))
    headline = _rel_attr(rel, "headline_angle") or ""
    if headline:
        lines.append(f"【报道角度参考】{headline}")
    note = _rel_attr(rel, "editor_note") or ""
    if note:
        lines.append(f"【编辑备注】{note}")
    return "\n".join(lines)
