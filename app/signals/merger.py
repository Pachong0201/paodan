"""SignalMerger：把 V1/V4 结果融合为 UnifiedSignalSet，并执行统一最终评分。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .models import Track, UnifiedSignalSet

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLDS = {
    "political_threshold": 40.0,
    "governance_threshold": 40.0,
    "strong_political_threshold": 60.0,
    "strong_governance_threshold": 60.0,
    "mixed_bonus": 5.0,
}


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _str_list(value: Any, limit: int = 100) -> List[str]:
    out: List[str] = []
    for x in _list(value):
        s = str(_get(x, "pattern_id", x) if not isinstance(x, str) else x).strip()
        if s and s not in out:
            out.append(s)
    return out[:limit]


def load_unified_thresholds(config_path: Optional[Path | str] = None) -> Dict[str, float]:
    """读取 config/news_signal/unified_signals.yaml；缺省使用安全默认值。"""
    cfg = dict(DEFAULT_THRESHOLDS)
    path = Path(config_path) if config_path else Path("config/news_signal/unified_signals.yaml")
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    if path.exists():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            section = data.get("unified_signals", data) if isinstance(data, dict) else {}
            for k in DEFAULT_THRESHOLDS:
                if isinstance(section, dict) and k in section:
                    try:
                        cfg[k] = float(section[k])
                    except (TypeError, ValueError):
                        pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("unified_signals 配置读取失败，使用默认值: %s", exc)
    return cfg


class SignalMerger:
    def __init__(self, thresholds: Optional[Dict[str, float]] = None,
                 config_path: Optional[Path | str] = None):
        self.thresholds = dict(DEFAULT_THRESHOLDS)
        if thresholds:
            self.thresholds.update({k: float(v) for k, v in thresholds.items() if v is not None})
        else:
            self.thresholds.update(load_unified_thresholds(config_path))

    def merge(self, rule: Any = None, llm: Any = None, score: Any = None,
              governance: Any = None, known_news: Any = None,
              political_categories: Optional[List[str]] = None,
              governance_categories: Optional[List[str]] = None,
              political_score: Optional[float] = None,
              governance_score: Optional[float] = None,
              evidence_shapes: Optional[List[str]] = None,
              attachments: Any = None,
              email_id: str = "", **kwargs) -> UnifiedSignalSet:
        pol_cats = self._dedupe(list(political_categories or []) +
                                _str_list(_get(rule, "matched_categories", None)) +
                                _str_list(_get(llm, "categories", None)))
        gov_cats = self._dedupe(list(governance_categories or []) +
                                _str_list(_get(governance, "categories", None)))
        pol_score = self._num(political_score, None)
        if pol_score is None:
            pol_score = self._num(_get(score, "final_score", None), None)
        if pol_score is None:
            pol_score = self._num(_get(rule, "rule_score", 0.0), 0.0)
        gov_score = self._num(governance_score, None)
        if gov_score is None:
            gov_score = self._num(_get(governance, "score", 0.0), 0.0)

        primary, secondary = self._track(pol_score, gov_score, pol_cats, gov_cats)
        evidence_stage = str(_get(llm, "evidence_stage", "") or _get(score, "evidence_stage", "") or "E1")
        money = self._money(_get(rule, "money", None) or _get(score, "money", None))
        entities = self._entities(_get(rule, "entities", None))
        risk_flags = self._risk_flags(rule, governance)
        old_news, new_info = self._novelty(llm, known_news)
        public_interest = self._public_interest(rule, governance, llm)
        shapes = self._dedupe(list(evidence_shapes or []) + self._shapes_from_attachments(attachments) +
                              self._shapes_from_keywords(rule))
        # evidence stage from governance-only is E1 by default
        us = UnifiedSignalSet(
            email_id=email_id or str(_get(score, "email_id", "") or _get(rule, "email_id", "") or ""),
            political_categories=pol_cats,
            governance_categories=gov_cats,
            political_patterns=_str_list(_get(rule, "matched_patterns", None)),
            governance_patterns=_str_list(_get(governance, "patterns", None)),
            political_score=round(float(pol_score or 0.0), 1),
            governance_score=round(float(gov_score or 0.0), 1),
            primary_track=primary.value,
            secondary_track=secondary.value if secondary else "",
            entities=entities,
            money=money,
            evidence_shapes=shapes,
            evidence_stage=evidence_stage,
            risk_flags=risk_flags,
            old_news=old_news,
            new_information=new_info,
            public_interest=public_interest,
            track_confidence=self._confidence(pol_score, gov_score, primary),
        )
        return us

    # ------------------------------------------------------------------
    def _track(self, pol_score: float, gov_score: float,
               pol_cats: List[str], gov_cats: List[str]) -> tuple[Track, Optional[Track]]:
        pt = float(self.thresholds.get("political_threshold", 40))
        gt = float(self.thresholds.get("governance_threshold", 40))
        p_strong = float(self.thresholds.get("strong_political_threshold", 60))
        g_strong = float(self.thresholds.get("strong_governance_threshold", 60))
        pol_active = bool(pol_cats) and pol_score >= pt
        gov_active = bool(gov_cats) and gov_score >= gt
        if pol_active and gov_active:
            return Track.MIXED, None
        if pol_active:
            return Track.POLITICAL, Track.GOVERNANCE if gov_cats else None
        if gov_active:
            return Track.GOVERNANCE, Track.POLITICAL if pol_cats else None
        # 无有效类别时不得仅因融合后的分数误判政治轨；返回 NONE 由最终评分兜底。
        return Track.NONE, None

    def _confidence(self, pol_score: float, gov_score: float, primary: Track) -> float:
        if primary == Track.NONE:
            return 0.0
        if primary == Track.MIXED:
            p_strong = float(pol_score or 0) >= float(
                self.thresholds.get("strong_political_threshold", 60))
            g_strong = float(gov_score or 0) >= float(
                self.thresholds.get("strong_governance_threshold", 60))
            strong_bonus = 0.05 * int(p_strong) + 0.05 * int(g_strong)
            return round(min(1.0, (float(pol_score or 0) + float(gov_score or 0)) / 200.0
                             + 0.1 + strong_bonus), 2)
        return round(min(1.0, max(float(pol_score or 0), float(gov_score or 0)) / 100.0), 2)

    @staticmethod
    def _num(value: Any, default: Any) -> Any:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _dedupe(values: List[Any]) -> List[str]:
        out: List[str] = []
        for x in values:
            s = str(x or "").strip()
            if s and s not in out:
                out.append(s)
        return out

    @staticmethod
    def _money(money: Any) -> List[Dict[str, Any]]:
        out = []
        for m in _list(money):
            if isinstance(m, dict):
                out.append({k: m.get(k) for k in ("raw", "amount", "currency") if k in m})
            else:
                out.append({"amount": getattr(m, "amount", None),
                            "currency": getattr(m, "currency", None)})
        return out[:50]

    @staticmethod
    def _entities(entities: Any) -> List[Dict[str, Any]]:
        out = []
        for e in _list(entities):
            if isinstance(e, dict):
                out.append({k: e.get(k) for k in ("text", "type", "count") if k in e})
            else:
                out.append({"text": getattr(e, "text", ""), "type": getattr(e, "type", ""),
                            "count": getattr(e, "count", 1)})
        return out[:100]

    @staticmethod
    def _risk_flags(rule: Any, governance: Any) -> List[str]:
        out: List[str] = []
        for neg in _list(_get(rule, "negative_matches", None)) + _list(_get(governance, "negatives", None)):
            rid = str(_get(neg, "rule_id", "") or "")
            if rid and rid not in out:
                out.append(rid)
        for c in _list(_get(governance, "categories", None)):
            if str(c).startswith("G") and str(c) not in out:
                out.append(str(c))
        return out

    @staticmethod
    def _novelty(llm: Any, known_news: Any) -> tuple[List[str], List[str]]:
        old = _str_list(_get(llm, "known_old_information", None)) + \
              _str_list(_get(known_news, "matched_events", None))
        new = _str_list(_get(llm, "new_information", None)) + \
              _str_list(_get(known_news, "possible_new_information", None))
        return list(dict.fromkeys(old))[:20], list(dict.fromkeys(new))[:20]

    @staticmethod
    def _public_interest(rule: Any, governance: Any, llm: Any) -> bool:
        if _get(governance, "categories", None):
            return True
        if _get(llm, "public_interest_reason", ""):
            return True
        # 治理/政治规则命中通常具备公共利益；保留 V1 已有类别
        return bool(_get(rule, "matched_categories", None))

    @staticmethod
    def _shapes_from_attachments(attachments: Any) -> List[str]:
        out = []
        mapping = {"pdf": "OFFICIAL_DOCUMENT", "docx": "INTERNAL_DOCUMENT",
                   "xlsx": "SPREADSHEET", "xls": "SPREADSHEET", "csv": "SPREADSHEET",
                   "jpg": "PHOTO", "jpeg": "PHOTO", "png": "PHOTO",
                   "eml": "INTERNAL_DOCUMENT", "txt": "OFFICIAL_DOCUMENT"}
        for a in _list(attachments):
            ft = str(_get(a, "file_type", "") or "").lower()
            if ft in mapping and mapping[ft] not in out:
                out.append(mapping[ft])
        return out

    @staticmethod
    def _shapes_from_keywords(rule: Any) -> List[str]:
        out = []
        kw = _get(rule, "matched_keywords", {}) or {}
        if isinstance(kw, dict) and kw.get("E"):
            out.append("MULTI_SOURCE_PACKAGE")
        return out


class UnifiedFinalScorer:
    """统一最终评分：max(political_final, governance_final) + Mixed 小幅协同。

    Rule A：Governance 高分不得被 V1 低分压低；
    Rule C：Mixed 案件不丢弃任一轨道（由 UnifiedSignalSet 保留两轨类别/分数）。
    """

    def __init__(self, thresholds: Optional[Dict[str, float]] = None,
                 config_path: Optional[Path | str] = None):
        if thresholds is None:
            # 与 SignalMerger 共用同一 YAML 配置源。
            self.thresholds = load_unified_thresholds(config_path)
        else:
            self.thresholds = dict(DEFAULT_THRESHOLDS)
            self.thresholds.update(thresholds)

    def fuse(self, political_final: float, governance_final: float,
             political_categories: Optional[List[str]] = None,
             governance_categories: Optional[List[str]] = None,
             political_priority: str = "", governance_priority: str = "") -> tuple[float, str, str]:
        pf = max(0.0, min(100.0, float(political_final or 0.0)))
        gf = max(0.0, min(100.0, float(governance_final or 0.0)))
        p_active = bool(political_categories) and pf >= float(self.thresholds.get("political_threshold", 40))
        g_active = bool(governance_categories) and gf >= float(self.thresholds.get("governance_threshold", 40))
        final = max(pf, gf)
        reason = "max(political,governance)"
        if p_active and g_active:
            final = min(100.0, final + float(self.thresholds.get("mixed_bonus", 5.0)))
            reason = "mixed_track_fusion"
        priority = self._priority(final)
        if not p_active and g_active and governance_priority:
            priority = governance_priority
        if not g_active and p_active and political_priority:
            priority = political_priority
        return round(final, 1), priority, reason

    @staticmethod
    def _priority(score: float) -> str:
        if score >= 90:
            return "S"
        if score >= 75:
            return "A"
        if score >= 60:
            return "B"
        if score >= 40:
            return "C"
        return "D"


# 兼容常见命名
UnifiedSignalMerger = SignalMerger


def merge_signals(**kwargs) -> UnifiedSignalSet:
    return SignalMerger().merge(**kwargs)
