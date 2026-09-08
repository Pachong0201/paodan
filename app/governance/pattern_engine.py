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
        """Window First -> Group Second -> Term OR -> Group AND。

        必须在同一个允许窗口内完成所有 required_groups 的匹配，禁止先用全文
        选出第一个词再检查局部窗口，否则会产生假阴性。
        """
        specs = rule.get("required_groups") or []
        if not specs:
            return None
        # 每组归一化词表；保留原词用于 matched_terms。
        group_terms: list[list[tuple[str, str]]] = []
        for group in specs:
            pairs = []
            for term in group or []:
                key = to_simplified(str(term))
                if key:
                    pairs.append((str(term), key))
            if not pairs:
                return None
            group_terms.append(pairs)

        windows = self._allowed_windows(rule)
        matched_level = ""
        matched_snip = ""
        matched_terms: list[str] = []
        for level in windows:
            for ws, we, wtext in self._windows(splitter, norm, level):
                chosen: list[str] = []
                ok = True
                for pairs in group_terms:
                    found = None
                    for original, key in pairs:
                        pos = splitter.find(key)
                        if any(ws <= p0 < we for p0, _ in pos):
                            found = original
                            break
                    if found is None:
                        ok = False
                        break
                    chosen.append(found)
                if not ok:
                    continue
                matched_level = level
                matched_terms = chosen
                # 证据片段取第一个命中组在窗口内的位置附近。
                anchor_key = to_simplified(str(chosen[0]))
                apos = splitter.find(anchor_key)
                p0 = next((x for x, _ in apos if ws <= x < we), None)
                if p0 is not None:
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
                             matched_terms=matched_terms,
                             evidence_snippets=[matched_snip] if matched_snip else [],
                             pattern_score=float(rule.get("base_score") or 80),
                             window=matched_level)
