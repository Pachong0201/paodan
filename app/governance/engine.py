# -*- coding: utf-8 -*-
"""GovernanceEngine: G01-G12 并行识别主入口。"""
from __future__ import annotations
from pathlib import Path
from .config_loader import GovernanceConfig
from .keyword_engine import GovKeywordEngine
from .pattern_engine import GovPatternEngine
from .negative_engine import GovNegativeEngine
from .scorer import GovernanceComplaintScorer
from .models import GovernanceResult, GovKeywordHit
from app.preprocessing.normalization import Normalizer, to_simplified
from app.rules.context_window import ContextSplitter


class GovernanceEngine:
    def __init__(self, config: GovernanceConfig | Path | None = None):
        if isinstance(config, GovernanceConfig):
            self.config = config
        else:
            # 默认从 NEWS_SIGNAL_DIR 加载
            try:
                from app.config import NEWS_SIGNAL_DIR
                base = Path(config) if config else NEWS_SIGNAL_DIR
            except Exception:
                base = Path(config) if config else Path("config/news_signal")
            self.config = GovernanceConfig(base).load_all()
        self.kw = GovKeywordEngine(self.config)
        self.pat = GovPatternEngine(self.config)
        self.neg = GovNegativeEngine(self.config)
        self.scorer = GovernanceComplaintScorer()

    def evaluate(self, email_text: str, has_attachment: bool = False) -> GovernanceResult:
        res = GovernanceResult()
        if not email_text:
            return res
        norm = Normalizer.normalize(email_text)
        res.normalized = norm
        hits, hit_by_slot, enhance = self.kw.match(norm)
        patterns = self.pat.match(norm)
        negatives = self.neg.match(norm, hits)
        # 类别收敛：Pattern 类别必含 + G-H 类别 + (M/E 仅当已由 pattern/H 命中时并入) + C 兜底
        pat_cats = set()
        for p in patterns:
            for c in str(p.category).split("|"):
                if c.startswith("G"):
                    pat_cats.add(c)
        h_cats: dict[str, int] = {}
        me_cats: dict[str, int] = {}
        c_cats: dict[str, int] = {}
        for h in hits:
            if not h.category.startswith("G"):
                continue
            if h.ktype == "G-H":
                h_cats[h.category] = h_cats.get(h.category, 0) + h.count
            elif h.ktype in ("G-M", "G-E"):
                me_cats[h.category] = me_cats.get(h.category, 0) + h.count
            elif h.ktype == "G-C":
                c_cats[h.category] = c_cats.get(h.category, 0) + h.count
        out: list[str] = []
        for c in sorted(pat_cats):
            out.append(c)
        if patterns:
            # G-H 为硬信号：pattern 命中时仍并入其类别（多类并存）
            for c in sorted(h_cats, key=lambda x: -h_cats[x]):
                if c not in out:
                    out.append(c)
            for c in me_cats:
                if c in pat_cats and c not in out:
                    out.append(c)
        else:
            for c in sorted(h_cats, key=lambda x: -h_cats[x]):
                if c not in out:
                    out.append(c)
            for c, _ in sorted(me_cats.items(), key=lambda kv: -kv[1]):
                if c not in out and len(out) < 3 and h_cats:
                    # M/E 仅当该类已有 H 时并入（pattern/H 纲）
                    if c in h_cats:
                        out.append(c)
            # G04/G05 等 C 主导但 H 明确的住房托育：H 已覆盖；纯 C 不单独成类（防泛词）
            if not out:
                # C 兜底仅当 G-H/G-M 至少其一存在且 C>=2，避免“申请”单字成类
                gh_n = sum(1 for h in hits if h.ktype == "G-H")
                gm_n = sum(1 for h in hits if h.ktype == "G-M")
                if (gh_n or gm_n) and c_cats:
                    for c, _ in sorted(c_cats.items(), key=lambda kv: -kv[1])[:1]:
                        out.append(c)
        res.categories = out
        # keywords 分组输出
        grouped: dict[str, list] = {}
        for h in hits:
            if h.ktype in ("G-H", "G-M", "G-C", "G-E", "G-X"):
                grouped.setdefault(h.ktype, []).append(h)
        res.keywords = grouped
        res.patterns = patterns
        res.negatives = negatives
        res.enhance = enhance
        score, pri, dims, details = self.scorer.score(norm, hits, patterns, negatives, enhance, has_attachment)
        # 无任何 G 信号：强制 D/0，避免误报
        if not out and not patterns and not [h for h in hits if h.ktype in ("G-H", "G-M")]:
            score, pri = 0.0, "D"
        res.score = score
        res.priority = pri
        res.dims = dims
        res.details = details
        return res
