# -*- coding: utf-8 -*-
"""Governance negative GN01-GN04."""
from __future__ import annotations
from .config_loader import GovernanceConfig
from .models import GovNegativeMatch, GovKeywordHit
from app.preprocessing.normalization import to_simplified


class GovNegativeEngine:
    def __init__(self, config: GovernanceConfig):
        self.config = config

    def match(self, normalized_text: str, hits: list[GovKeywordHit]) -> list[GovNegativeMatch]:
        out: list[GovNegativeMatch] = []
        rules = {r.get("id"): r for r in self.config.negative_rules()}
        gh = [h for h in hits if h.ktype == "G-H"]
        ge = [h for h in hits if h.ktype == "G-E"]
        gx = [h for h in hits if h.ktype == "G-X"]
        # 是否有具体事实：G-H>=1 或 金额/时间/地点/证据>=2
        has_concrete = len(gh) >= 1
        fact_signals = ["月", "天", "年", "号", "户", "人", "元", "万元", "区", "里", "路", "附件", "截图", "公文", "通知", "错误码", "时间"]
        fact_n = sum(1 for s in fact_signals if s in normalized_text)
        if ge:
            fact_n += len(ge)
        has_detail = has_concrete or fact_n >= 2 or ("4.8万" in normalized_text) or ("48000" in normalized_text)

        # GN01 纯情绪：触发词>=1 且无具体
        n01 = rules.get("GN01")
        if n01:
            trig = [t for t in (n01.get("trigger_terms") or []) if to_simplified(str(t)) in normalized_text]
            # 泛情绪词补充
            extra = [w for w in ["烂政府", "垃圾政府", "政府很烂", "太扯", "傻眼", "气死", "荒谬"] if w in normalized_text]
            trig = list(dict.fromkeys(trig + extra))
            if trig and not has_detail and not gh:
                out.append(GovNegativeMatch(rule_id="GN01", condition=str(n01.get("condition")),
                                            score_delta=float(n01.get("score_delta", -40)),
                                            max_score=float(n01["max_score"]) if n01.get("max_score") is not None else None,
                                            matched_terms=trig[:5]))
        # GN02 已合理解释：医疗等待 + 检伤合理
        n02 = rules.get("GN02")
        if n02:
            med = any(k in normalized_text for k in ["急诊", "等床", "挂号", "等候", "待床"])
            jus = [t for t in (n02.get("trigger_terms") or []) if to_simplified(str(t)) in normalized_text]
            if med and jus:
                out.append(GovNegativeMatch(rule_id="GN02", condition=str(n02.get("condition")),
                                            score_delta=float(n02.get("score_delta", -30)),
                                            max_score=float(n02["max_score"]) if n02.get("max_score") is not None else None,
                                            matched_terms=jus[:5]))
        # GN03 正常行政程序
        n03 = rules.get("GN03")
        if n03:
            trig = [t for t in (n03.get("trigger_terms") or []) if to_simplified(str(t)) in normalized_text]
            if trig and not gh:
                out.append(GovNegativeMatch(rule_id="GN03", condition=str(n03.get("condition")),
                                            score_delta=float(n03.get("score_delta", -25)),
                                            max_score=float(n03["max_score"]) if n03.get("max_score") is not None else None,
                                            matched_terms=trig[:5]))
        # GN04 个案已解决且无群体
        n04 = rules.get("GN04")
        if n04:
            trig = [t for t in (n04.get("trigger_terms") or []) if to_simplified(str(t)) in normalized_text]
            group_sig = any(k in normalized_text for k in ["整区", "整里", "社区", "户", "几百", "500", "3000", "群体", "共同"])
            if trig and not group_sig:
                out.append(GovNegativeMatch(rule_id="GN04", condition=str(n04.get("condition")),
                                            score_delta=float(n04.get("score_delta", -30)),
                                            max_score=float(n04["max_score"]) if n04.get("max_score") is not None else None,
                                            matched_terms=trig[:5]))
        return out
