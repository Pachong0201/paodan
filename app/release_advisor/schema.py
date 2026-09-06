"""首发渠道 LLM 输出 JSON Schema 与校验器。

约束来自 prompts/release_route_advisor_prompt.md；非法枚举会被修复并记录 error，
便于离线模板模式也能稳定输出合法结构。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from .models import VALID_FORMAL_TYPES, VALID_RISKS, VALID_ROUTES

# 渠道名映射（R1-R6 -> 中文名）
ROUTE_NAMES = {
    "R1": "社交平台首发型", "R2": "媒体独家型", "R3": "深度调查报道型",
    "R4": "记者会/公开展示型", "R5": "正式检举优先型", "R6": "高敏感核验型",
}
VALID_SEQUENCE_ACTIONS = {
    "EDITORIAL_VERIFICATION", "FORMAL_REFERRAL", "PUBLIC_RELEASE", "PRIVATE_OUTREACH",
}

# 顶层字段类型
SCHEMA_FIELDS = {
    "primary_route": str,
    "primary_route_name": str,
    "secondary_routes": list,
    "avoid_routes": list,
    "route_confidence": (int, float),
    "prepublication_verification_required": bool,
    "verification_before_release": list,
    "formal_referral_recommended": bool,
    "formal_referral_type": list,
    "release_risks": list,
    "reason": str,
    "recommended_release_sequence": list,
    "headline_angle": str,
    "editor_note": str,
}
REQUIRED = [
    "primary_route", "secondary_routes", "avoid_routes", "route_confidence",
    "prepublication_verification_required", "verification_before_release",
    "formal_referral_recommended", "formal_referral_type", "release_risks",
    "reason", "recommended_release_sequence",
]

_FORMAL_LABELS = {k: (v if isinstance(v, str) else "") for k, v in
                  {"PROSECUTOR": 1, "NONE": 1}.items()}  # 占位（真实映射在 rules.yaml）


def normalize_json(data: Any) -> Dict[str, Any] | None:
    """把 LLM 返回解析成 dict；无法解析返回 None."""
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


def _clean_str_list(val: Any, limit: int = 8) -> List[str]:
    if val is None:
        return []
    if not isinstance(val, list):
        val = [val]
    return [str(x) for x in val if x is not None][:limit]


def _clean_routes(val: Any) -> List[str]:
    out = []
    for r in _clean_str_list(val):
        r = r.strip().upper()
        if r in VALID_ROUTES:
            out.append(r)
    return list(dict.fromkeys(out))


def _clean_risks(val: Any) -> List[str]:
    out = []
    for r in _clean_str_list(val):
        r = r.strip().upper()
        if r in VALID_RISKS:
            out.append(r)
    return list(dict.fromkeys(out))


def _clean_formal(val: Any) -> List[str]:
    out = []
    for r in _clean_str_list(val):
        r = r.strip().upper()
        if r in VALID_FORMAL_TYPES:
            out.append(r)
    return list(dict.fromkeys(out)) or ["NONE"]


def _clean_sequence(val: Any) -> List[dict]:
    if val is None:
        return []
    items = val if isinstance(val, list) else [val]
    out = []
    for i, it in enumerate(items, 1):
        if not isinstance(it, dict):
            continue
        action = str(it.get("action", "")).strip().upper()
        if action not in VALID_SEQUENCE_ACTIONS:
            continue
        step = it.get("step", i)
        try:
            step = int(step)
        except (TypeError, ValueError):
            step = i
        out.append({
            "step": step,
            "action": action,
            "route": str(it.get("route") or "").strip().upper(),
            "reason": str(it.get("reason") or ""),
        })
    out.sort(key=lambda x: x["step"])
    return out[:10]


def validate_result(data: Any) -> tuple:
    """校验并修复；返回 (ok: bool, cleaned: dict, errors: list)。"""
    obj = normalize_json(data)
    if obj is None:
        return False, {}, ["无法解析为 JSON 对象"]
    errors: List[str] = []
    cleaned: Dict[str, Any] = {}

    def _missing(key: str, fallback):
        if key not in obj:
            errors.append(f"缺少字段 {key}")
            return fallback
        return obj[key]

    cleaned["primary_route"] = str(_missing("primary_route", "") or "").strip().upper()
    if cleaned["primary_route"] not in VALID_ROUTES:
        if cleaned["primary_route"]:
            errors.append(f"primary_route={cleaned['primary_route']} 非法")
        cleaned["primary_route"] = ""
    cleaned["primary_route_name"] = str(_missing("primary_route_name", "") or "")
    if not cleaned["primary_route_name"] and cleaned["primary_route"]:
        cleaned["primary_route_name"] = ROUTE_NAMES.get(cleaned["primary_route"], "")
    cleaned["secondary_routes"] = _clean_routes(_missing("secondary_routes", []))
    cleaned["avoid_routes"] = _clean_routes(_missing("avoid_routes", []))
    # 自一致性：primary 不在 avoid 中
    if cleaned["primary_route"] and cleaned["primary_route"] in cleaned["avoid_routes"]:
        errors.append("primary_route 同时出现在 avoid_routes")
        cleaned["avoid_routes"] = [r for r in cleaned["avoid_routes"]
                                   if r != cleaned["primary_route"]]
    for route in cleaned["secondary_routes"]:
        if route in cleaned["avoid_routes"]:
            errors.append(f"secondary {route} 同时出现在 avoid_routes")
            cleaned["avoid_routes"] = [r for r in cleaned["avoid_routes"] if r != route]

    try:
        conf = float(_missing("route_confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        conf, errors = 0.0, [*errors, "route_confidence 非数值"]
    cleaned["route_confidence"] = max(0.0, min(1.0, conf))
    cleaned["prepublication_verification_required"] = bool(
        _missing("prepublication_verification_required", True))
    cleaned["verification_before_release"] = _clean_str_list(
        _missing("verification_before_release", []), limit=12)
    cleaned["formal_referral_recommended"] = bool(
        _missing("formal_referral_recommended", False))
    cleaned["formal_referral_type"] = _clean_formal(_missing("formal_referral_type", ["NONE"]))
    if cleaned["formal_referral_type"] == ["NONE"] and cleaned["formal_referral_recommended"]:
        errors.append("formal_referral_recommended=true 但 formal_referral_type=NONE")
    cleaned["release_risks"] = _clean_risks(_missing("release_risks", []))
    cleaned["reason"] = str(_missing("reason", "") or "")
    cleaned["recommended_release_sequence"] = _clean_sequence(
        _missing("recommended_release_sequence", []))
    cleaned["headline_angle"] = str(_missing("headline_angle", "") or "")
    cleaned["editor_note"] = str(_missing("editor_note", "") or "")
    return (len(errors) == 0, cleaned, errors)
