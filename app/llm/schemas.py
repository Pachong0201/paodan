"""LLM 结构化输出 schema 与校验器（对应规则包 llm_email_screening_prompt.md 输出 JSON）。"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

# 顶层字段白名单（规则包提示词定义）
SCHEMA_FIELDS = {
    "relevant": bool, "priority_level": str, "importance_score": (int, float),
    "target_persons": list, "target_organizations": list,
    "categories": list, "subcategories": list,
    "specific_behaviors": list, "evidence_stage": str, "allegation_status": str,
    "related_entities": dict, "projects_or_cases": list,
    "money_or_benefits": list, "power_actions": list, "evidence_items": list,
    "suspicious_phrases": list, "relationship_chain": list,
    "negative_or_exculpatory_evidence": list, "new_information": list,
    "known_old_information": list, "verification_targets": list,
    "public_interest_reason": str, "one_sentence_summary": str,
    "reason_for_attention": str, "needs_human_review": bool, "confidence": (int, float),
}

VALID_STAGES = {"E0", "E1", "E2", "E3", "E4", "E5", "EX"}
VALID_LEVELS = {"S", "A", "B", "C", "D"}
VALID_CATEGORIES = {f"A{i:02d}" for i in range(1, 19)}

REQUIRED_STR_FIELDS = ["evidence_stage", "allegation_status", "public_interest_reason",
                       "one_sentence_summary", "reason_for_attention"]
REQUIRED_LIST_FIELDS = ["target_persons", "target_organizations", "categories",
                        "verification_targets", "specific_behaviors", "evidence_items",
                        "negative_or_exculpatory_evidence", "new_information",
                        "relationship_chain", "money_or_benefits"]


def normalize_result(data: Any) -> Optional[Dict[str, Any]]:
    """把 LLM 输出规范化为 dict；不是 dict 时返回 None."""
    if isinstance(data, dict):
        return dict(data)
    if isinstance(data, str):
        text = data.strip()
        # 去掉 markdown 代码围栏
        fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
        if fence:
            text = fence.group(1)
        # 截到第一个 { 到最后一个 }
        s, e = text.find("{"), text.rfind("}")
        if s >= 0 and e > s:
            text = text[s:e + 1]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
    return None


def validate_result(data: Any) -> tuple:
    """校验并修复；返回 (ok: bool, normalized: dict, errors: list)。"""
    obj = normalize_result(data)
    if obj is None:
        return False, {}, ["无法解析为 JSON 对象"]
    errors = []
    cleaned: Dict[str, Any] = {}
    # 过滤未知键、校正类型
    for key, expected in SCHEMA_FIELDS.items():
        if key not in obj:
            if key in REQUIRED_STR_FIELDS or key in REQUIRED_LIST_FIELDS:
                errors.append(f"缺少字段 {key}")
            cleaned[key] = "" if expected is str else ([] if expected is list else
                                                       ({} if expected is dict else None))
            continue
        val = obj[key]
        if expected is str:
            if not isinstance(val, str):
                val = str(val)
        elif expected is list:
            if not isinstance(val, list):
                val = [val] if val is not None else []
            val = [str(x) for x in val if x is not None][:50]
        elif expected is dict:
            if not isinstance(val, dict):
                val = {}
        elif expected is bool:
            val = bool(val)
        elif expected in ((int, float), (int, float)):
            try:
                val = float(val)
            except (TypeError, ValueError):
                val = 0.0
                errors.append(f"字段 {key} 非数值")
        cleaned[key] = val
    # 枚举校验
    if cleaned.get("evidence_stage") not in VALID_STAGES:
        errors.append(f"evidence_stage={cleaned.get('evidence_stage')} 非法")
        cleaned["evidence_stage"] = "E1"
    if cleaned.get("priority_level") and cleaned["priority_level"] not in VALID_LEVELS:
        errors.append(f"priority_level={cleaned['priority_level']} 非法")
        cleaned["priority_level"] = ""
    bad_cats = [c for c in cleaned.get("categories", []) if c not in VALID_CATEGORIES]
    if bad_cats:
        cleaned["categories"] = [c for c in cleaned["categories"] if c in VALID_CATEGORIES]
    if not cleaned["categories"]:
        cleaned["categories"] = []
    return (len(errors) == 0, cleaned, errors)


def result_to_llm_fields(cleaned: dict, llm_status: str = "ok") -> dict:
    """schema dict -> LLMResult 构造字段."""
    fields = dict(cleaned)
    fields["llm_status"] = llm_status
    return fields
