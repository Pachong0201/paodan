# -*- coding: utf-8 -*-
"""Governance config loader: G01-G12 + patterns + negatives."""
from __future__ import annotations
from pathlib import Path
from typing import Any
import yaml


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
            # 若是 news_signal，父目录 config 也尝试
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
            raise RuntimeError("governance config load failed: " + "; ".join(errs))
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
