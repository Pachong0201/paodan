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

    @staticmethod
    def _allowed_windows(rule: dict) -> list[str]:
        raw = rule.get("window")
        allow_full = bool(rule.get("allow_full_document", False))
        if raw is None:
            # 未配置 window 时默认段落/相邻3句；只有显式 allow_full_document=true 才加 full。
            windows = ["paragraph", "context3"] + (["full"] if allow_full else [])
        elif isinstance(raw, list):
            windows = [str(x) for x in raw]
        else:
            windows = [str(raw)]
        out: list[str] = []
        for w in windows:
            if w == "full" and not allow_full:
                continue
            if w in ("sentence", "paragraph", "context3", "full") and w not in out:
                out.append(w)
        return out

    @staticmethod
    def _windows(splitter: ContextSplitter, norm: str, level: str):
        if level == "sentence":
            return splitter.sentences
        if level == "paragraph":
            return splitter.paragraphs
        if level == "context3":
            return splitter.windows_ctx3
        if level == "full":
            return [(0, len(norm), norm)]
        return []

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

        windows = self._allowed_windows(rule)
        matched_level = ""
        matched_snip = ""
        for level in windows:
            wins = self._windows(splitter, norm, level)
            for ws, we, wtext in wins:
                ok = True
                for term in chosen:
                    key = to_simplified(str(term))
                    pos = splitter.find(key)
                    if not any(ws <= p0 < we for p0, _ in pos):
                        ok = False
                        break
                if ok:
                    matched_level = level
                    # 证据片段取窗口内 anchor 附近
                    anchor = to_simplified(str(chosen[0]))
                    apos = splitter.find(anchor)
                    if apos:
                        p0 = apos[0][0]
                        s = max(ws, p0 - 40)
                        e = min(we, p0 + 100)
                        matched_snip = "…" + norm[s:e].replace("\n", " ") + "…"
                    else:
                        matched_snip = "…" + wtext.replace("\n", " ")[:140] + "…"
                    break
            if matched_level:
                break
        if not matched_level:
            return None
        return GovPatternHit(pattern_id=str(rule.get("id")),
                             category=str(rule.get("category") or "GLOBAL"),
                             name=str(rule.get("name") or ""),
                             matched_terms=chosen,
                             evidence_snippets=[matched_snip] if matched_snip else [],
                             pattern_score=float(rule.get("base_score") or 80),
                             window=matched_level)
