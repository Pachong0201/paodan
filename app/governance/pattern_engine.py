# -*- coding: utf-8 -*-
"""Governance pattern GP01-GP10: 组间 AND 组内 OR，同 PatternEngine 窗口语义简化版。"""
from __future__ import annotations
from .config_loader import GovernanceConfig
from .models import GovPatternHit
from app.preprocessing.normalization import to_simplified
from app.rules.context_window import ContextSplitter


class GovPatternEngine:
    def __init__(self, config: GovernanceConfig):
        self.config = config

    def match(self, normalized_text: str) -> list[GovPatternHit]:
        if not normalized_text:
            return []
        splitter = ContextSplitter(normalized_text).build()
        out: list[GovPatternHit] = []
        for rule in self.config.pattern_list():
            pid = str(rule.get("id"))
            hit = self._match_rule(rule, splitter, normalized_text)
            if hit:
                out.append(hit)
        return out

    def _match_rule(self, rule: dict, splitter: ContextSplitter, norm: str):
        specs = rule.get("required_groups") or []
        if not specs:
            return None
        # 每组找命中词
        chosen: list[str] = []
        for group in specs:
            found = None
            for term in group:
                key = to_simplified(str(term))
                if key and splitter.find(key):
                    found = term
                    break
            if found is None:
                return None
            chosen.append(found)
        # 窗口级别判定：优先 sentence/paragraph/context3，否则 full（组数>=2 允许全文）
        level = "full"
        for lvl, wins in [("sentence", splitter.sentences), ("paragraph", splitter.paragraphs),
                          ("context3", splitter.windows_ctx3)]:
            if not wins:
                continue
            for ws, we, _ in wins:
                ok = True
                for term in chosen:
                    key = to_simplified(term)
                    pos = splitter.find(key)
                    if not any(ws <= p0 < we for p0, _ in pos):
                        ok = False
                        break
                if ok:
                    level = lvl
                    break
            if level != "full":
                break
        # GP07 群体性要求更严：若仅全文共现但文本过长则仍算（已放宽，便于召回）
        anchor = chosen[0]
        pos = splitter.find(to_simplified(anchor))
        snip = ""
        if pos:
            s = max(0, pos[0][0]-30)
            e = min(len(norm), pos[0][1]+80)
            snip = "…" + norm[s:e].replace("\n", " ") + "…"
        return GovPatternHit(pattern_id=str(rule.get("id")),
                             category=str(rule.get("category") or "GLOBAL"),
                             name=str(rule.get("name") or ""),
                             matched_terms=chosen,
                             evidence_snippets=[snip] if snip else [],
                             pattern_score=float(rule.get("base_score") or 80),
                             window=level)
