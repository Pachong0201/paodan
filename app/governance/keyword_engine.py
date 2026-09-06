# -*- coding: utf-8 -*-
"""Governance keyword engine: G-H/M/C/E/X + REPEAT/GROUP/LOSS/FAILED."""
from __future__ import annotations
from .config_loader import GovernanceConfig
from .models import GovKeywordHit
from app.preprocessing.normalization import Normalizer, to_simplified
from app.rules.context_window import ContextSplitter


class GovKeywordEngine:
    def __init__(self, config: GovernanceConfig):
        self.config = config

    def match(self, normalized_text: str):
        hits: list[GovKeywordHit] = []
        hit_by_slot: dict[str, set[str]] = {}
        splitter = ContextSplitter(normalized_text).build()
        # G01-G12
        for cid in self.config.category_ids:
            kw = self.config.keywords_of(cid)
            for slot in ("G-H", "G-M", "G-C", "G-E", "G-X"):
                for term in (kw.get(slot) or []):
                    term = str(term)
                    if len(term) < 1:
                        continue
                    key = to_simplified(term)
                    pos = splitter.find(key)
                    if not pos:
                        continue
                    seg = normalized_text[max(0, pos[0][0]-20):pos[0][1]+20]
                    h = GovKeywordHit(term=term, ktype=slot, category=cid,
                                      count=len(pos), snippet="…"+seg.strip()+"…")
                    hits.append(h)
                    hit_by_slot.setdefault(slot, set()).add(term)
        # 全局 evidence -> G-E GLOBAL
        for term in self.config.evidence_terms():
            term = str(term)
            key = to_simplified(term)
            if len(key) < 2:
                continue
            pos = splitter.find(key)
            if pos:
                # 去重：若已作为某类 G-E 命中则跳过
                if any(h.term == term and h.ktype == "G-E" for h in hits):
                    continue
                seg = normalized_text[max(0, pos[0][0]-20):pos[0][1]+20]
                hits.append(GovKeywordHit(term=term, ktype="G-E", category="GLOBAL",
                                          count=len(pos), snippet="…"+seg.strip()+"…"))
                hit_by_slot.setdefault("G-E", set()).add(term)
        # GX 全局
        for term in self.config.gx_terms():
            term = str(term)
            key = to_simplified(term)
            if len(key) < 2:
                continue
            pos = splitter.find(key)
            if pos and term not in hit_by_slot.get("G-X", set()):
                seg = normalized_text[max(0, pos[0][0]-20):pos[0][1]+20]
                # 仅当未命中时补充，避免重复
                if not any(h.term == term and h.ktype == "G-X" for h in hits):
                    hits.append(GovKeywordHit(term=term, ktype="G-X", category="GLOBAL",
                                              count=len(pos), snippet="…"+seg.strip()+"…"))
                    hit_by_slot.setdefault("G-X", set()).add(term)
        # enhancement
        enhance: dict[str, list[str]] = {}
        for slot, terms in (self.config.enhancement() or {}).items():
            found = []
            for term in (terms or []):
                term = str(term)
                key = to_simplified(term)
                if key and splitter.find(key):
                    found.append(term)
                    # 同时记为 keyword hit 便于 scorer
                    seg = normalized_text[:60]
                    hits.append(GovKeywordHit(term=term, ktype=slot, category="GLOBAL",
                                              count=1, snippet="…"+seg[:60]+"…"))
            if found:
                enhance[slot] = found
        return hits, hit_by_slot, enhance
