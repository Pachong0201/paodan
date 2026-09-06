"""规则引擎：组合 keyword + pattern + negative + 实体/金额，输出 RuleResult 与粗分."""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Set

from ..models import EntityHit, RuleResult
from ..preprocessing.extractors import parse_amount
from ..preprocessing.normalization import Normalizer, to_simplified
from .config_loader import RuleConfig
from .context_window import ContextSplitter
from .keyword_engine import KeywordEngine
from .negative_engine import NegativeEngine
from .pattern_engine import PatternEngine
from .rule_entities import extract_basic_entities

logger = logging.getLogger(__name__)

TYPE_WEIGHT = {"H": 14.0, "M": 6.0, "C": 2.0, "E": 8.0, "S": 5.0, "X": -30.0}
# 每个词出现的次数收益递减：同一词在正文反复出现(导航/页脚/分享按钮)不重复计权
TYPE_MAX_TOTAL = {"H": 60.0, "M": 35.0, "C": 20.0, "E": 40.0, "S": 25.0}


def _type_weight(ktype: str, count: int) -> float:
    base = TYPE_WEIGHT.get(ktype, 2.0)
    # 出现 1 次全额；2-3 次少量加成；超过 3 次不再加(防页脚/分享按钮重复刷分)
    eff = min(count, 3)
    return base * (1.0 if eff <= 1 else (1.0 + 0.2 * (eff - 1)))


class RuleEngine:
    """粗筛引擎。规则包为主：keyword(宽召回) + pattern(组合) + negative(降权)."""

    def __init__(self, config: RuleConfig):
        self.config = config
        self.keyword_engine = KeywordEngine(config)
        self.pattern_engine = PatternEngine(config, self.keyword_engine)
        self.negative_engine = NegativeEngine(config)

    # ------------------------------------------------------------------
    def evaluate(self, email_text: str,
                 entities: Optional[List[EntityHit]] = None,
                 no_new_evidence: bool = False) -> RuleResult:
        rr = RuleResult()
        if not email_text:
            return rr
        normalized = Normalizer.normalize(email_text)
        rr.normalized = normalized

        # 1) 上下文分句
        splitter = ContextSplitter(normalized).build()

        # 2) 关键词粗筛
        kw_res = self.keyword_engine.match(normalized, splitter)
        # hit_by_type 统一转为简体，保证与 normalized 简体域一致
        hit_by_type: Dict[str, Set[str]] = {}
        for t, terms in kw_res.hit_types.items():
            hit_by_type[t] = set(to_simplified(x) for x in terms)
        for h in kw_res.hits:
            if h.ktype == "X":
                continue
            rr.matched_keywords.setdefault(h.ktype, []).append(h)
        rr.evidence_snippets = list(kw_res.evidence_snippets)

        # 3) 金额
        money_raw = parse_amount(email_text)
        rr.money = [m.to_dict() for m in money_raw]

        # 4) 实体
        if entities is None:
            entities = extract_basic_entities(email_text)
        rr.entities = [e.to_dict() if hasattr(e, "to_dict") else e for e in entities]

        # 5) X 反向词初检（无 pattern 信息；pattern 完成后重检 N 系列）
        x_terms = self.negative_engine.x_terms
        x_hits = []
        for t in x_terms:
            t2 = to_simplified(t)
            if splitter.find(t2):
                x_hits.append(t)
        rr.x_terms = list(dict.fromkeys(x_hits))
        rr.ex_present = bool(rr.x_terms)
        rr.no_new_evidence = no_new_evidence

        # 6) Pattern（含 X_result / no_new_evidence 语义组）
        role_terms = list(hit_by_type.get("C", set()))
        ent_dicts = [e.to_dict() if hasattr(e, "to_dict") else e for e in entities]
        patterns = self.pattern_engine.match(
            normalized, email_text, money=rr.money, x_terms=rr.x_terms,
            no_new_evidence=no_new_evidence, hit_by_type=hit_by_type,
            entities=ent_dicts, role_terms=role_terms)
        rr.matched_patterns = patterns

        # 5b) Negative rules（N系列降权判定，含 pattern_hits 语境）
        neg_res = self.negative_engine.match(normalized, email_text,
                                             hits_by_type=hit_by_type,
                                             pattern_hits=patterns)
        rr.negative_matches = [n for n in neg_res["negatives"]]
        if not rr.ex_present:
            rr.ex_present = neg_res["ex_present"]
            rr.x_terms = list(dict.fromkeys(rr.x_terms + neg_res["x_terms"]))
        # 5c) 上下文排除：剔除被排除的歧义词命中（如“协调”在“跨部门例行协调”语境）
        excluded = neg_res.get("exclusions_applied", [])
        if excluded:
            excluded_simp = {to_simplified(e) for e in excluded}
            for ktype in list(rr.matched_keywords.keys()):
                kept = []
                for h in rr.matched_keywords[ktype]:
                    if h.term in excluded or to_simplified(h.term) in excluded_simp:
                        continue
                    kept.append(h)
                rr.matched_keywords[ktype] = kept
                if ktype in hit_by_type:
                    hit_by_type[ktype] = {t for t in hit_by_type[ktype]
                                          if to_simplified(t) not in excluded_simp}

        # 7) 类别收敛（pattern + H + 相关 M/E，避免泛词堆叠）——用排除后的命中
        all_hits = []
        for ktype_hits in rr.matched_keywords.values():
            all_hits.extend(ktype_hits)
        rr.matched_categories = self._categories_from_keywords(all_hits, patterns)

        # 8) 目标人物/机构
        def _e_text(e):
            return e.get("text") if isinstance(e, dict) else getattr(e, "text", "")

        def _e_type(e):
            return e.get("type") if isinstance(e, dict) else getattr(e, "type", "")
        rr.target_persons_found = [_e_text(e) for e in entities if _e_type(e) == "PERSON"][:10]
        rr.target_orgs_found = [_e_text(e) for e in entities if _e_type(e) in
                                ("ORGANIZATION", "COMPANY", "GOVERNMENT_AGENCY", "POLITICAL_PARTY")][:10]

        # 8) 粗评分
        rr.rule_score = self._rule_score(rr, hit_by_type, neg_res)
        rr.rule_pass = True
        return rr

    # ------------------------------------------------------------------
    def _categories_from_keywords(self, hits, patterns=None) -> List[str]:
        """类别收敛规则（以 Pattern 为纲）：
        - Pattern 类别(GLOBAL 除外) 必含；
        - Pattern 实际命中词在词典中携带的类别并入（如 P05 命中词“建照”属 A04 -> A03+A04）；
        - 各类 H 专属词命中并入（仅在无 Pattern 拉偏时保留主要类别）；
        - M/E 专属词并入当且仅当该类已由 pattern/H 命中；
        - 完全无 pattern/H 时用 C 命中数前 2 兜底。
        避免泛词（如“截图”同时属 A17/A11）单方面决定类别。
        """
        pat_cats: Dict[str, str] = {}   # cat -> 来源说明
        for p in patterns or []:
            for c in str(p.category).split("|"):
                if c != "GLOBAL":
                    pat_cats.setdefault(c, f"pattern {p.pattern_id}")
        # Pattern 命中词 -> 词典类别（词繁简两写均可查）；只并入 C 槽类别，
        # 避免 LINE(A11 E)/汇(款)(A17 M) 等证据/隐性词把无关主题类别带进来。
        term_cat_index: Dict[str, List[str]] = {}
        for h in hits:
            if h.category and h.category != "GLOBAL":
                term_cat_index.setdefault(h.term, []).append(h.category)
                term_cat_index.setdefault(to_simplified(h.term), []).append(h.category)
        for p in patterns or []:
            for term in p.matched_terms:
                for cand in (term, to_simplified(term)):
                    for c in term_cat_index.get(cand, []):
                        if c not in pat_cats:
                            # 只并入 C 槽类别（场景/关系主题类，如 建照->A04）
                            slot_of = {h2.category: h2 for h2 in hits if h2.term in (term, cand)}
                            src = slot_of.get(c)
                            if src is not None and src.ktype == "C" and c != "A11":
                                pat_cats[c] = f"pattern词 {term}"
        h_cats: Dict[str, int] = {}
        me_cats: Dict[str, int] = {}
        c_cats: Dict[str, int] = {}
        for h in hits:
            c = h.category
            if not c or c == "GLOBAL":
                continue
            if h.ktype == "H":
                h_cats[c] = h_cats.get(c, 0) + h.count
            elif h.ktype in ("M", "E"):
                me_cats[c] = me_cats.get(c, 0) + h.count
            elif h.ktype == "C":
                c_cats[c] = c_cats.get(c, 0) + h.count
        out = []
        for c in sorted(pat_cats.keys()):
            out.append(c)
        if patterns:
            # 有 pattern：H 类别仅并入与 pattern 类别重叠者（避免“截图”等泛 H 词引入无关类）
            for c in h_cats:
                if c in pat_cats and c not in out:
                    out.append(c)
            # M/E 类别：仅与 pattern 类别重叠才并入
            for c in me_cats:
                if c in pat_cats:
                    if c not in out:
                        out.append(c)
        else:
            # 无 pattern：H 类别排序并入（排除“截图”等 A17 泛词单引）；
            # M/E 类别按命中数并入（最多补 2 个，用于 A14 婚外关系等 M 词主导主题）
            for c in sorted(h_cats, key=lambda x: -h_cats[x]):
                if c not in out and c != "A17":
                    out.append(c)
            for c, _n in sorted(me_cats.items(), key=lambda kv: -kv[1]):
                if c not in out and len(out) < 3 and c != "A17":
                    out.append(c)
        if not out:
            for c, _n in sorted(c_cats.items(), key=lambda kv: -kv[1])[:2]:
                out.append(c)
        return out

    def _rule_score(self, rr: RuleResult, hit_by_type: Dict[str, Set[str]], neg_res: dict) -> float:
        score = 0.0
        # 每类累计封顶，防营销页脚/分享按钮重复词刷分
        for ktype in ("H", "M", "C", "E", "S"):
            total = sum(_type_weight(ktype, h.count) for h in rr.matched_keywords.get(ktype, []))
            score += min(total, TYPE_MAX_TOTAL.get(ktype, 999))

        # Pattern：同类别只计最高，异类别累加*0.6
        pat_cat: Dict[str, float] = {}
        for p in rr.matched_patterns:
            for c in str(p.category).split("|"):
                pat_cat[c] = max(pat_cat.get(c, 0.0), float(p.pattern_score))
        for c, top in pat_cat.items():
            score += top * 0.6

        # 证据/金额强化
        e_count = len(rr.matched_keywords.get("E", []))
        if e_count >= 3:
            score += 4
        if rr.money:
            score += min(8, 2 * len(rr.money))
        if len(rr.target_persons_found) >= 1 and rr.money and e_count >= 1:
            score += 5

        # 隐性表达集群加分（Case10 类核心能力）：
        # M(隐性异常表达)>=2 且 有 C 角色/目标词 且 E(原始证据词)>=1 -> 隐性线索组合。
        # 这类组合不依赖“贪污/收贿/违法”等 H 词，正是规则包 M/C/E 槽的用途。
        m_count = len(rr.matched_keywords.get("M", []))
        c_role_hits = len(rr.matched_keywords.get("C", []))
        implicit_m = [h.term for h in rr.matched_keywords.get("M", []) if h.category == "GLOBAL"] or \
                     [h.term for h in rr.matched_keywords.get("M", [])]
        if m_count >= 2 and c_role_hits >= 1 and e_count >= 1 and not rr.matched_patterns:
            score += min(30.0, 12.0 + 4.0 * (m_count - 2) + 3.0 * min(e_count, 3))
        # 权力动作/隐性表达 + 无 pattern 的高 M 集群（2+ M 词 + 人物/厂商词）
        if m_count >= 3 and c_role_hits >= 2:
            score += 8.0

        # Negative rules 降权
        delta, cap = self.negative_engine.compute_negative_delta(neg_res, score)
        score += delta
        if cap is not None:
            score = min(score, cap)
        for p in rr.matched_patterns:
            if p.pattern_id == "P20":
                score = min(score, float(p.pattern_score))
        return max(0.0, round(score, 2))
