"""V3 记者工作台文本渲染：具名渠道推荐展示块。"""
from __future__ import annotations

from typing import Optional

from .models import NamedChannelRecommendation


def _fmt_items(items, n: int = 5) -> str:
    if not items:
        return "（无）"
    parts = []
    for it in items[:n]:
        name = it.get("name") if isinstance(it, dict) else it.name
        score = it.get("fit_score") if isinstance(it, dict) else it.fit_score
        parts.append(f"{name} {score:.0f}".strip())
    return "、".join(parts)


def _list_items(items) -> str:
    names = []
    for it in items or []:
        name = it.get("name") if isinstance(it, dict) else it.name
        names.append(str(name))
    return "、".join(names) if names else "（无）"


def render_named_workbench(rec: Optional[NamedChannelRecommendation]) -> str:
    """生成【具名媒体】【适配揭弊渠道】等区块文本。rec 或 dict。"""
    if rec is None:
        return ""
    if isinstance(rec, dict):
        rec = NamedChannelRecommendation(**{k: v for k, v in rec.items()
                                            if k in NamedChannelRecommendation.__dataclass_fields__})
    lines: list = []
    media = _fmt_items(rec.recommended_media)
    actors = _fmt_items(rec.recommended_disclosure_actors)
    amps = _fmt_items(rec.recommended_amplifiers)
    formals = _fmt_items(rec.recommended_formal_channels)
    platforms = _fmt_items(rec.recommended_platforms)
    local = _fmt_items(rec.local_channels)
    lines.append(f"【具名媒体】{media}")
    lines.append(f"【适配揭弊渠道】{actors}")
    if amps:
        lines.append(f"【放大渠道】{amps}")
    if platforms:
        lines.append(f"【平台】{platforms}")
    if local:
        lines.append(f"【地方渠道】{local}")
    lines.append(f"【正式渠道】{formals}")
    if rec.avoid_named_channels:
        names = []
        for eid in rec.avoid_named_channels:
            names.append(str(eid))
        lines.append("【不建议】" + "、".join(names))
    # 理由：取各组 top1 reason
    reasons = []
    for it in (rec.recommended_media or [])[:1]:
        reasons.append(str(it.get("reason") if isinstance(it, dict) else it.reason))
    for it in (rec.recommended_disclosure_actors or [])[:1]:
        reasons.append(str(it.get("reason") if isinstance(it, dict) else it.reason))
    for it in (rec.recommended_formal_channels or [])[:1]:
        reasons.append(str(it.get("reason") if isinstance(it, dict) else it.reason))
    if reasons:
        lines.append("【原因】" + "；".join(r for r in reasons if r))
    if rec.recommended_sequence:
        lines.append("【建议顺序】")
        for s in rec.recommended_sequence[:5]:
            cid = s.get("channel_id") if isinstance(s, dict) else ""
            grp = s.get("channel_group") if isinstance(s, dict) else ""
            reason = s.get("reason") if isinstance(s, dict) else ""
            lines.append(f"Step{s.get('step', '?') if isinstance(s, dict) else '?'} "
                         f"{grp}({cid})：{reason}")
    if rec.editor_note:
        lines.append(f"【编辑备注】{rec.editor_note}")
    return "\n".join(lines)
