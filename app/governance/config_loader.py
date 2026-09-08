# -*- coding: utf-8 -*-
"""Governance config loader: G01-G12 + patterns + negatives."""
from __future__ import annotations
from pathlib import Path
from typing import Any
import yaml


class GovernanceConfigError(RuntimeError):
    pass


class GovernanceConfig:
    def __init__(self, directory: Path | None = None):
        self.directory = Path(directory) if directory else None
        self.complaints: dict[str, Any] = {}
        self.patterns: dict[str, Any] = {}
        self.negatives: dict[str, Any] = {}

    def load_all(self) -> "GovernanceConfig":
        base = self.directory
        # 主路径 config/news_signal/，兼容 config/
        cands = []
        if base:
            cands.append(base)
            # 兼容传入 config/ 或 config/news_signal/ 两种路径
            if base.name != "news_signal":
                cands.append(base / "news_signal")
            cands.append(base.parent)
        else:
            cands.append(Path("config/news_signal"))
            cands.append(Path("config"))
        errs = []
        for name, attr in [("governance_complaints.yaml", "complaints"),
                           ("governance_pattern_rules.yaml", "patterns"),
                           ("governance_negative_rules.yaml", "negatives")]:
            data = None
            last_e = None
            for d in cands:
                p = d / name
                try:
                    if p.exists():
                        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                        break
                except Exception as e:
                    last_e = e
            if data is None:
                errs.append(f"{name}: not found ({last_e})")
            else:
                setattr(self, attr, data)
        if errs:
            raise GovernanceConfigError("governance config load failed: " + "; ".join(errs))
        self.validate()
        return self

    @property
    def categories(self) -> list[dict]:
        return list((self.complaints.get("categories") or []))

    @property
    def category_ids(self) -> list[str]:
        return [c.get("id") for c in self.categories if c.get("id")]

    def keywords_of(self, cid: str) -> dict:
        return (self.complaints.get("keywords") or {}).get(cid, {})

    def enhancement(self) -> dict:
        return (self.complaints.get("enhancement_signals") or {})

    def evidence_terms(self) -> list[str]:
        return list(self.complaints.get("evidence_terms") or [])

    def gx_terms(self) -> list[str]:
        out = list(self.complaints.get("reasonable_explanations") or [])
        # 各类 G-X 并入
        for cid in self.category_ids:
            for t in (self.keywords_of(cid).get("G-X") or []):
                if t not in out:
                    out.append(t)
        return out

    def pattern_list(self) -> list[dict]:
        return list((self.patterns.get("patterns") or []))

    def negative_rules(self) -> list[dict]:
        return list((self.negatives.get("downgrade_rules") or []))

    def validate(self) -> "GovernanceConfig":
        """严格校验 Governance 配置；失败必须让启动/selfcheck FAIL，禁止静默禁用。"""
        errors: list[str] = []

        def err(msg: str):
            errors.append(msg)

        cats = self.categories
        if not isinstance(cats, list) or not cats:
            err("categories 必须为非空 list")
        cat_ids: list[str] = []
        for i, c in enumerate(cats):
            if not isinstance(c, dict):
                err(f"categories[{i}] 必须是 mapping")
                continue
            cid = str(c.get("id") or "")
            if not cid:
                err(f"categories[{i}] 缺 id")
            if cid in cat_ids:
                err(f"G ID 重复: {cid}")
            cat_ids.append(cid)
            if "name" in c and not isinstance(c.get("name"), str):
                err(f"{cid} name 必须是 str")
        cat_set = set(cat_ids)

        keywords = self.complaints.get("keywords") or {}
        if not isinstance(keywords, dict):
            err("keywords 必须是 mapping")
            keywords = {}
        for cid, slots in keywords.items():
            if cid not in cat_set:
                err(f"unknown category reference in keywords: {cid}")
            if not isinstance(slots, dict):
                err(f"keywords.{cid} 必须是 mapping")
                continue
            for slot, terms in slots.items():
                if not isinstance(terms, list):
                    err(f"keywords.{cid}.{slot} 必须是 list")
                    continue
                seen = set()
                for term in terms:
                    if not isinstance(term, str) or not term.strip():
                        err(f"keywords.{cid}.{slot} 含非字符串/空词")
                        continue
                    if term in seen:
                        err(f"duplicate term: {cid}.{slot}.{term}")
                    seen.add(term)

        patterns = self.pattern_list()
        if not isinstance(patterns, list) or not patterns:
            err("patterns 必须为非空 list")
        pat_ids: list[str] = []
        valid_windows = {"sentence", "paragraph", "context3", "full"}
        for i, rule in enumerate(patterns):
            if not isinstance(rule, dict):
                err(f"patterns[{i}] 必须是 mapping")
                continue
            pid = str(rule.get("id") or "")
            if not pid:
                err(f"patterns[{i}] 缺 id")
            if pid in pat_ids:
                err(f"GP ID 重复: {pid}")
            pat_ids.append(pid)
            cat = str(rule.get("category") or "")
            for c in cat.split("|"):
                if c and c not in cat_set:
                    err(f"{pid} category 引用不存在: {c}")
            groups = rule.get("required_groups")
            if not isinstance(groups, list) or not groups:
                err(f"{pid} required_groups 不得为空")
                continue
            for gi, group in enumerate(groups):
                if not isinstance(group, list) or not group:
                    err(f"{pid} required_groups[{gi}] 不得为空")
                    continue
                seen = set()
                for term in group:
                    if not isinstance(term, str) or not term.strip():
                        err(f"{pid} required_groups[{gi}] 含非字符串/空词")
                    elif term in seen:
                        err(f"duplicate term: {pid}.group{gi}.{term}")
                    seen.add(term)
            try:
                if isinstance(rule.get("base_score", 0), bool):
                    raise TypeError("bool")
                base = float(rule.get("base_score", 0))
                if not (0 <= base <= 100):
                    err(f"{pid} base_score 必须在 0-100")
            except (TypeError, ValueError):
                err(f"{pid} base_score 类型错误")
            window = rule.get("window")
            if window is not None:
                wins = window if isinstance(window, list) else [window]
                for w in wins:
                    if str(w) not in valid_windows:
                        err(f"{pid} window enum 非法: {w}")
            if "allow_full_document" in rule and not isinstance(rule.get("allow_full_document"), bool):
                err(f"{pid} allow_full_document 必须是 bool")

        negatives = self.negative_rules()
        if not isinstance(negatives, list):
            err("downgrade_rules 必须是 list")
        neg_ids: list[str] = []
        for i, rule in enumerate(negatives or []):
            if not isinstance(rule, dict):
                err(f"downgrade_rules[{i}] 必须是 mapping")
                continue
            rid = str(rule.get("id") or "")
            if not rid:
                err(f"downgrade_rules[{i}] 缺 id")
            if rid in neg_ids:
                err(f"GN ID 重复: {rid}")
            neg_ids.append(rid)
            try:
                if isinstance(rule.get("score_delta"), bool):
                    raise TypeError("bool")
                float(rule.get("score_delta"))
            except (TypeError, ValueError):
                err(f"{rid} score_delta 类型错误")
            if rule.get("max_score") is not None:
                try:
                    if isinstance(rule.get("max_score"), bool):
                        raise TypeError("bool")
                    ms = float(rule.get("max_score"))
                    if not (0 <= ms <= 100):
                        err(f"{rid} max_score 必须在 0-100")
                except (TypeError, ValueError):
                    err(f"{rid} max_score 类型错误")
            trig = rule.get("trigger_terms")
            if trig is not None and not isinstance(trig, list):
                err(f"{rid} trigger_terms 必须是 list")

        if errors:
            raise GovernanceConfigError("governance config validation failed: " + "; ".join(errors))
        return self

    def summary(self) -> dict:
        gh = gm = gc = ge = gx = 0
        for cid in self.category_ids:
            kw = self.keywords_of(cid)
            gh += len(kw.get("G-H", []) or [])
            gm += len(kw.get("G-M", []) or [])
            gc += len(kw.get("G-C", []) or [])
            ge += len(kw.get("G-E", []) or [])
            gx += len(kw.get("G-X", []) or [])
        ge += len(self.evidence_terms())
        enh = self.enhancement()
        return {"categories": len(self.category_ids),
                "G-H": gh, "G-M": gm, "G-C": gc, "G-E": ge, "G-X": gx,
                "patterns": len(self.pattern_list()),
                "negatives": len(self.negative_rules()),
                "enhance": {k: len(v or []) for k, v in enh.items()}}
