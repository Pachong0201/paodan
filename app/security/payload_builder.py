"""SafePayloadBuilder：把业务对象转成 External LLM 唯一可见的 SafeLLMPayload。

原则：
- 不接收 doc.body_text / combined_text / original_text / attachment text；
- 只接收结构化结果、类别、分数、Pattern、匿名化实体、金额、风险 flags；
- V2/V3 只传渠道决策特征与公开候选实体，不传正文/来源身份。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from .models import PrivacyLevel, SafeLLMPayload, hash_ref
from .policy import SecurityPolicy
from .pseudonymizer import Pseudonymizer
from .redactor import PrivacyRedactor


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _str_list(value: Any, limit: int = 50) -> List[str]:
    out: List[str] = []
    for item in _as_list(value):
        s = str(item or "").strip()
        if s and s not in out:
            out.append(s[:200])
    return out[:limit]


def _clamp_score(value: Any) -> float:
    try:
        return max(0.0, min(100.0, float(value or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def _safe_dict_list(items: Iterable[Any], allowed_keys: Iterable[str],
                    limit: int = 30) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    allowed = set(allowed_keys)
    for item in items or []:
        if isinstance(item, dict):
            d = {k: item.get(k) for k in allowed if item.get(k) not in (None, "", [])}
        else:
            d = {k: getattr(item, k) for k in allowed if getattr(item, k, None) not in (None, "", [])}
        if d:
            out.append(d)
    return out[:limit]


class SafePayloadBuilder:
    """构造 external-safe payload；构造器本身不调用网络。"""

    def __init__(self, policy: Optional[SecurityPolicy] = None,
                 pseudonymizer: Optional[Pseudonymizer] = None):
        self.policy = policy or SecurityPolicy()
        self._pseudonymizer_explicit = pseudonymizer is not None
        self.pseudonymizer = pseudonymizer or Pseudonymizer()
        self.redactor = PrivacyRedactor()

    # ------------------------------------------------------------------
    def build(self, purpose: str = "screening", **kwargs) -> SafeLLMPayload:
        p = str(purpose or "screening").lower()
        if p in ("v1", "screening", "screen", "unified"):
            return self.build_v1(**kwargs)
        if p in ("v2", "release", "release_advisor"):
            return self.build_v2(**kwargs)
        if p in ("v3", "named", "named_channel"):
            return self.build_v3(**kwargs)
        # 通用：只保留 SafeLLMPayload 白名单字段，危险字段直接丢弃
        return SafeLLMPayload.from_dict(kwargs)

    # ------------------------------------------------------------------
    def build_v1(self, rule: Any = None, llm: Any = None, score: Any = None,
                 governance: Any = None, unified: Any = None,
                 known_news: Any = None, email_id: str = "", **kwargs) -> SafeLLMPayload:
        pol = self.policy
        # 请求级假名化：默认每次 build 使用全新映射，不同邮件不得复用长期稳定映射。
        if not self._pseudonymizer_explicit:
            self.pseudonymizer = Pseudonymizer()
        p = SafeLLMPayload(
            purpose="screening",
            email_ref=hash_ref(email_id or _get(unified, "email_id", "") or _get(score, "email_id", "")),
            privacy_level=pol.privacy_level,
            primary_track=str(_get(unified, "primary_track", "") or "NONE"),
            secondary_track=str(_get(unified, "secondary_track", "") or ""),
            rule_score=_clamp_score(_get(unified, "political_score", None)
                                    if _get(unified, "political_score", None) is not None
                                    else (_get(score, "final_score", 0.0) or _get(rule, "rule_score", 0.0))),
            governance_score=_clamp_score(_get(unified, "governance_score", 0.0) or
                                          _get(governance, "score", 0.0)),
            final_score=_clamp_score(_get(score, "final_score", None)
                                     if _get(score, "final_score", None) is not None
                                     else _get(rule, "rule_score", 0.0)),
            priority=str(_get(score, "priority", "") or ""),
            evidence_stage=str(_get(llm, "evidence_stage", "") or _get(unified, "evidence_stage", "") or ""),
        )
        p.political_categories = _str_list(_get(unified, "political_categories", None) or
                                           _get(rule, "matched_categories", None) or
                                           _get(llm, "categories", None))
        p.governance_categories = _str_list(_get(unified, "governance_categories", None) or
                                            _get(governance, "categories", None))
        p.political_patterns = self._pattern_ids(_get(rule, "matched_patterns", None) or
                                                 _get(unified, "political_patterns", None))
        p.governance_patterns = self._pattern_ids(_get(governance, "patterns", None) or
                                                  _get(unified, "governance_patterns", None))
        p.evidence_shapes = _str_list(_get(unified, "evidence_shapes", None) or
                                      _get(llm, "evidence_items", None), limit=30)
        p.money_facts = self._money_facts(_get(rule, "money", None) or _get(unified, "money", None))
        p.target_entities = self._entities(
            (_get(llm, "target_persons", None) or []) +
            (_get(rule, "target_persons_found", None) or []), "TARGET")
        p.organization_entities = self._entities(
            (_get(llm, "target_organizations", None) or []) +
            (_get(rule, "target_orgs_found", None) or []), "ORG")
        p.relationship_chain = _str_list(_get(llm, "relationship_chain", None), limit=20)
        p.behavior_signals = _str_list(_get(llm, "specific_behaviors", None), limit=30)
        p.procedure_signals = self._procedure_signals(rule)
        p.risk_flags = _str_list(_get(unified, "risk_flags", None) or
                                 _get(governance, "negatives", None), limit=30)
        p.public_interest_flags = self._public_interest_flags(unified, llm)
        p.known_news_flags = self._known_news_flags(known_news)
        p.novelty_flags = _str_list(_get(unified, "new_information", None) or
                                    _get(llm, "new_information", None), limit=20)
        p.safe_snippets = self._safe_snippets(_get(llm, "evidence_items", None) or
                                              _get(rule, "evidence_snippets", None))
        p.metadata = {
            "known_news_mode": str(_get(known_news, "mode", "local_stub") or "local_stub"),
            "novelty_status": str(_get(known_news, "novelty_status", "unknown") or "unknown"),
            "pipeline_version": "4.1",
            "safe_payload_schema": "SafeLLMPayload/v1",
        }
        # 过滤掉任何意外 forbidden 字段
        p.ensure_safe_fields()
        return p

    # ------------------------------------------------------------------
    def build_v2(self, features: Any = None, recommendation: Any = None,
                 unified: Any = None, **kwargs) -> SafeLLMPayload:
        pol = self.policy
        rec = recommendation or {}
        f = features or {}
        p = SafeLLMPayload(
            purpose="release_advisor",
            email_ref=hash_ref(_get(unified, "email_id", "") or _get(f, "email_id", "")),
            privacy_level=pol.privacy_level,
            primary_track=str(_get(unified, "primary_track", "") or ""),
            rule_score=_clamp_score(_get(f, "final_score", 0.0)),
            final_score=_clamp_score(_get(f, "final_score", 0.0)),
            priority=str(_get(f, "priority_level", "") or ""),
            evidence_stage=str(_get(f, "evidence_stage", "") or ""),
            release_route=str(_get(rec, "primary_route", "") or ""),
            secondary_routes=_str_list(_get(rec, "secondary_routes", None)),
            avoid_routes=_str_list(_get(rec, "avoid_routes", None)),
            verification_requirements=_str_list(_get(rec, "verification_before_release", None), limit=20),
        )
        p.political_categories = _str_list(_get(f, "political_categories", None) or
                                           _get(f, "categories", None))
        p.governance_categories = _str_list(_get(f, "governance_categories", None))
        p.evidence_shapes = _str_list(_get(f, "evidence_shapes", None))
        p.release_flags = {
            "has_original_evidence": bool(_get(f, "has_original_evidence", False)),
            "has_money_flow": bool(_get(f, "has_money_flow", False)),
            "has_power_action": bool(_get(f, "has_power_action", False)),
            "has_first_person_testimony": bool(_get(f, "has_first_person_testimony", False)),
            "has_sensitive_personal_data": bool(_get(f, "has_sensitive_personal_data", False)),
            "has_classified_material": bool(_get(f, "has_classified_material", False)),
            "has_anonymous_documents": bool(_get(f, "has_anonymous_documents", False)),
            "source_requests_anonymity": bool(_get(f, "source_requests_anonymity", False)),
            "destruction_risk": bool(_get(f, "destruction_risk", False)),
            "retaliation_risk": bool(_get(f, "retaliation_risk", False)),
            "public_interest": bool(_get(f, "public_interest_established", False)),
            "known_old_case": bool(_get(f, "known_old_case", False)),
            "contains_new_information": bool(_get(f, "contains_new_information", True)),
        }
        p.metadata = {
            "pipeline_version": "4.1",
            "safe_payload_schema": "SafeLLMPayload/v2",
            "rule_hits": _str_list(_get(rec, "rule_hits", None), limit=20),
            "route_confidence": float(_get(rec, "route_confidence", 0.0) or 0.0),
        }
        p.ensure_safe_fields()
        return p

    # ------------------------------------------------------------------
    def build_v3(self, named_recommendation: Any = None, features: Any = None,
                 release_recommendation: Any = None, unified: Any = None,
                 **kwargs) -> SafeLLMPayload:
        pol = self.policy
        named = named_recommendation or {}
        rel = release_recommendation or {}
        f = features or {}
        p = SafeLLMPayload(
            purpose="named_channel_advisor",
            email_ref=hash_ref(_get(unified, "email_id", "") or _get(named, "email_id", "")),
            privacy_level=pol.privacy_level,
            primary_track=str(_get(unified, "primary_track", "") or ""),
            priority=str(_get(named, "priority", "") or _get(f, "priority_level", "") or ""),
            final_score=_clamp_score(_get(named, "final_score", 0.0) or _get(f, "final_score", 0.0)),
            release_route=str(_get(rel, "primary_route", "") or _get(named, "primary_route", "") or ""),
            secondary_routes=_str_list(_get(rel, "secondary_routes", None)),
            avoid_routes=_str_list(_get(rel, "avoid_routes", None)),
        )
        p.political_categories = _str_list(_get(named, "categories", None) or _get(f, "political_categories", None))
        p.governance_categories = _str_list(_get(f, "governance_categories", None))
        p.evidence_shapes = _str_list(_get(f, "evidence_shapes", None))
        p.risk_flags = _str_list(_get(f, "risk_flags", None) or _get(rel, "release_risks", None))
        p.public_entities = self._public_entities(named)
        extra_entities = kwargs.get("public_entities")
        if extra_entities:
            for item in extra_entities:
                if isinstance(item, dict) and item not in p.public_entities:
                    p.public_entities.append({k: item.get(k) for k in
                                              ("entity_id", "name", "group", "fit_score")
                                              if item.get(k) not in (None, "", [])})
            p.public_entities = p.public_entities[:50]
        p.public_historical_case_summaries = _str_list(kwargs.get("historical_case_summaries"), limit=10)
        p.metadata = {
            "pipeline_version": "4.1",
            "safe_payload_schema": "SafeLLMPayload/v3",
            "candidate_only": True,
        }
        p.ensure_safe_fields()
        return p

    # ------------------------------------------------------------------
    def _pattern_ids(self, patterns: Any) -> List[str]:
        out = []
        for p in _as_list(patterns):
            pid = str(_get(p, "pattern_id", "") or _get(p, "id", "") or p or "").strip()
            if pid and pid not in out:
                out.append(pid[:40])
        return out[:30]

    def _money_facts(self, money: Any) -> List[Dict[str, Any]]:
        out = []
        for m in _as_list(money):
            try:
                amount = float(_get(m, "amount", 0) or 0)
            except (TypeError, ValueError):
                continue
            currency = str(_get(m, "currency", "") or "")
            if amount:
                out.append({"amount": amount, "currency": currency})
        return out[:20]

    def _entities(self, values: Any, kind: str) -> List[Dict[str, Any]]:
        out = []
        seen = set()
        for v in _as_list(values):
            text = str(_get(v, "text", v) or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            out.append({"pseudonym": self.pseudonymizer.pseudonymize(text, kind),
                        "kind": kind})
        return out[:30]

    def _procedure_signals(self, rule: Any) -> List[str]:
        out = []
        for h in _as_list(_get(rule, "matched_keywords", {}).get("S") if isinstance(_get(rule, "matched_keywords", {}), dict) else []):
            term = str(_get(h, "term", h) or "").strip()
            if term and term not in out:
                out.append(term[:80])
        return out[:30]

    def _public_interest_flags(self, unified: Any, llm: Any) -> List[str]:
        out = []
        pi = _get(unified, "public_interest", None)
        if pi:
            out.append("public_interest")
        if _get(llm, "public_interest_reason", ""):
            out.append("public_interest_reason_present")
        return out

    def _known_news_flags(self, known: Any) -> List[str]:
        out = []
        if not known:
            return ["known_news_mode:local_stub", "novelty_status:unknown"]
        if bool(_get(known, "known", False)):
            out.append("known_case")
        if _get(known, "matched_events", None):
            out.append("matched_known_events")
        if _get(known, "possible_new_information", None):
            out.append("possible_new_information")
        else:
            out.append("novelty_status:unknown")
        out.append("known_news_mode:local_stub")
        return out

    def _safe_snippets(self, snippets: Any) -> List[str]:
        if self.policy.privacy != PrivacyLevel.REDACTED_SNIPPETS:
            return []
        if not self.policy.allow_safe_snippets:
            return []
        out = []
        for s in _as_list(snippets):
            text = str(s or "").strip()
            if not text:
                continue
            red = self.redactor.redact(text)
            if red and red not in out:
                out.append(red[:500])
        return out[:5]

    def _public_entities(self, named: Any) -> List[Dict[str, Any]]:
        out = []
        groups = ("recommended_media", "recommended_disclosure_actors",
                  "recommended_amplifiers", "recommended_formal_channels",
                  "recommended_platforms", "local_channels")
        for group in groups:
            for item in _as_list(_get(named, group, None)):
                if not isinstance(item, dict):
                    continue
                eid = str(item.get("entity_id") or "")
                name = str(item.get("name") or "")
                if eid or name:
                    out.append({"entity_id": eid[:80], "name": name[:120],
                                "group": group, "fit_score": float(item.get("fit_score") or 0)})
        return out[:50]


def build_safe_payload(purpose: str = "screening", **kwargs) -> SafeLLMPayload:
    return SafePayloadBuilder().build(purpose=purpose, **kwargs)


def ensure_safe_payload(value: Any, policy: Optional[SecurityPolicy] = None) -> SafeLLMPayload:
    if isinstance(value, SafeLLMPayload):
        value.ensure_safe_fields()
        return value
    if isinstance(value, dict):
        forbidden = {k for k in value.keys() if str(k).lower() in
                     {x.lower() for x in __import__("app.security.models", fromlist=["FORBIDDEN_FIELD_NAMES"]).FORBIDDEN_FIELD_NAMES}}
        if forbidden:
            from .models import PrivacyViolation
            raise PrivacyViolation("safe payload dict contains forbidden fields: " +
                                   ", ".join(sorted(map(str, forbidden))))
        return SafeLLMPayload.from_dict(value)
    raise TypeError("safe payload must be SafeLLMPayload or dict")
