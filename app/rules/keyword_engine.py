"""关键词引擎：词典粗筛（宽召回），区分 H/M/C/E/S/X 并输出片段证据."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..models import KeywordHit
from ..preprocessing.normalization import to_simplified
from .config_loader import RuleConfig
from .context_window import ContextSplitter
from .term_variants import OTHER_VARIANTS_SIMPLIFIED, KINSHIP_VARIANTS_SIMPLIFIED

logger = logging.getLogger(__name__)

# 单字/短词单独命中不稳定，排除词表（部分短词只在组合时使用，如“亲”“摸”“抱”由 pattern 处理）
EXCLUDE_SINGLE_TERMS = {"亲", "摸", "抱"}


@dataclass
class KeywordResult:
    hits: List[KeywordHit] = field(default_factory=list)
    hit_types: Dict[str, List[str]] = field(default_factory=dict)   # 类型->词表（去重）
    counts: Dict[str, int] = field(default_factory=dict)            # 类型->总命中次数
    evidence_snippets: List[str] = field(default_factory=list)
    x_terms: List[str] = field(default_factory=list)

    def matched(self, ktype: str) -> List[str]:
        return self.hit_types.get(ktype, [])

    def matched_all_types(self) -> Dict[str, List[str]]:
        return dict(self.hit_types)


class KeywordEngine:
    """在 normalized_text 上执行词典匹配。term 长度>=2，避免短词噪音."""

    def __init__(self, config: RuleConfig):
        self.config = config
        self.index: Dict[str, List[Dict]] = {}   # term -> [{ktype, category, slot, stage}]
        self._build_index()

    def _build_index(self):
        raw = self.config.build_keyword_index()
        self.index: Dict[str, List[Dict]] = {}
        # 变体：简体口语词 -> 词典词（如 姐姐->胞姐），作为附加 key 一并匹配
        self.variant_map: Dict[str, str] = {}
        for variant, canonical in {**KINSHIP_VARIANTS_SIMPLIFIED, **OTHER_VARIANTS_SIMPLIFIED}.items():
            v = to_simplified(variant)
            c = to_simplified(canonical)
            if v and c and c in self._collect_canonical_keys(raw):
                self.variant_map[v] = c
        # 第一轮：词典词
        for ktype, items in raw.items():
            for it in items:
                term = it["term"]
                if len(term) < 2:
                    continue
                if term in EXCLUDE_SINGLE_TERMS:
                    continue
                key = to_simplified(term)
                self.index.setdefault(key, []).append(
                    {"ktype": ktype, "category": it["category"], "slot": it["slot"],
                     "stage": it.get("stage"), "term": term})
        # 第二轮：注册变体 key（指向词典原词 meta）
        for v, c in self.variant_map.items():
            if v in self.index:
                continue
            metas = self.index.get(c, [])
            if metas:
                self.index.setdefault(v, []).extend(list(metas))

    @staticmethod
    def _collect_canonical_keys(raw) -> set:
        keys = set()
        for items in raw.values():
            for it in items:
                t = it["term"]
                if len(t) >= 2:
                    keys.add(to_simplified(t))
        return keys

    @property
    def term_count(self) -> int:
        return len(self.index)

    def match(self, normalized_text: str, splitter: Optional[ContextSplitter] = None) -> KeywordResult:
        res = KeywordResult()
        if not normalized_text:
            return res
        splitter = splitter or ContextSplitter(normalized_text).build()
        for key, metas in self.index.items():
            positions = splitter.find(key)
            if not positions:
                continue
            seen_meta = set()
            for meta in metas:
                mkey = (meta["ktype"], meta["category"])
                if mkey in seen_meta:
                    continue
                seen_meta.add(mkey)
                display_term = meta.get("term") or key
                h = KeywordHit(term=display_term, ktype=meta["ktype"], category=meta["category"],
                               count=len(positions), stage=meta.get("stage") or "")
                seg = normalized_text[max(0, positions[0][0] - 25):positions[0][1] + 25]
                h.snippet = "…" + seg.strip("…") + "…"
                res.hits.append(h)
                res.hit_types.setdefault(meta["ktype"], []).append(display_term)
                res.counts[meta["ktype"]] = res.counts.get(meta["ktype"], 0) + len(positions)
                if meta["ktype"] == "X":
                    res.x_terms.append(display_term)
                snip = h.snippet if len(h.snippet) <= 120 else h.snippet[:120]
                if snip not in res.evidence_snippets:
                    res.evidence_snippets.append(snip)
        # 去重类型内词表
        for t in res.hit_types:
            res.hit_types[t] = list(dict.fromkeys(res.hit_types[t]))
        res.x_terms = list(dict.fromkeys(res.x_terms))
        # 剪裁证据片段
        res.evidence_snippets = res.evidence_snippets[:20]
        return res
