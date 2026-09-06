"""V3 LLM 输出 JSON Schema：只允许引用候选池内实体。"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Set

VALID_ROLES = {
    "FIRST_RELEASE", "INVESTIGATIVE", "DISCLOSURE", "AMPLIFIER", "VERIFIER",
    "FORMAL_REFERRAL", "DATA_ANALYSIS", "LOCAL_NETWORK", "SOURCE_PROTECTION",
}
OUTPUT_KEYS = {
    "recommended_media", "recommended_disclosure_actors", "recommended_amplifiers",
    "recommended_formal_channels", "recommended_platforms",
}


def normalize_json(data: Any) -> Dict[str, Any] | None:
    if isinstance(data, dict):
        return dict(data)
    if isinstance(data, str):
        text = data.strip()
        fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
        if fence:
            text = fence.group(1)
        s, e = text.find("{"), text.rfind("}")
        if s >= 0 and e > s:
            text = text[s:e + 1]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
    return None


def _clean_items(val: Any, pool_ids: Set[str], max_n: int = 8) -> List[dict]:
    if val is None:
        return []
    items = val if isinstance(val, list) else [val]
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        eid = str(it.get("id") or "").strip()
        if not eid:
            continue
        # LLM 只能引用候选池内实体（除 avoid 可留空校验）
        roles = [str(r).upper() for r in (it.get("roles") or [])
                 if str(r).upper() in VALID_ROLES]
        try:
            score = float(it.get("fit_score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        out.append({
            "id": eid,
            "name": str(it.get("name") or ""),
            "fit_score": max(0.0, min(100.0, round(score, 1))),
            "roles": list(dict.fromkeys(roles)),
            "reason": str(it.get("reason") or "")[:400],
        })
    return out[:max_n]


def validate_llm_output(data: Any, pool_ids: Set[str]) -> tuple:
    """返回 (ok, cleaned, errors)。unknown entity -> 拦截（必须来自候选池）。"""
    obj = normalize_json(data)
    if obj is None:
        return False, {}, ["无法解析 JSON"]
    errors: List[str] = []
    cleaned: Dict[str, Any] = {}
    for key in OUTPUT_KEYS:
        items = _clean_items(obj.get(key), pool_ids, max_n=8)
        for it in items:
            if it["id"] not in pool_ids:
                errors.append(f"{key} 引用了候选池外实体 {it['id']}")
                it["id"] = ""  # 标记非法，调用方忽略
        cleaned[key] = [it for it in items if it["id"] in pool_ids]
    # avoid：允许候选池外（安全硬约束来自池内），但必须 id 合法字符串
    cleaned["avoid_named_channels"] = [
        str(x).strip() for x in (obj.get("avoid_named_channels") or [])
        if str(x).strip()][:12]
    cleaned["editor_note"] = str(obj.get("editor_note") or "")[:600]
    cleaned["unlisted_channel_suggestion"] = str(
        obj.get("unlisted_channel_suggestion") or "")[:300]
    # sequence
    seq = []
    for i, s in enumerate((obj.get("recommended_sequence") or [])[:8], 1):
        if isinstance(s, dict):
            seq.append({
                "step": int(s.get("step") or i),
                "channel_group": str(s.get("channel_group") or ""),
                "channel_id": str(s.get("channel_id") or ""),
                "reason": str(s.get("reason") or ""),
            })
    cleaned["recommended_sequence"] = seq
    return (len(errors) == 0, cleaned, errors)
