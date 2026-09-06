"""渠道实体加载器：读取 channel_entities.yaml / named_channel_rules.yaml /
channel_historical_cases.yaml，提供实体画像、规则、历史案例访问。

- 状态过滤：inactive/historical/unknown 不进 Top 推荐；
- 陈旧检查：last_verified_date 超过 stale_days(默认180天)输出警告但不删除。
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from ..config import CONFIG_DIR

logger = logging.getLogger(__name__)


class ChannelConfigError(RuntimeError):
    pass


class ChannelEntityLoader:
    """具名渠道三配置统一加载器."""

    def __init__(self, config_dir: Optional[Path] = None,
                 stale_days: Optional[int] = None):
        self.config_dir = Path(config_dir) if config_dir else CONFIG_DIR
        self.stale_days = stale_days or 180
        self.entities: Dict[str, dict] = {}
        self.rules: Dict[str, dict] = {}
        self.historical_cases: List[dict] = []
        self.stale_warnings: List[str] = []
        self.load_all()

    # ------------------------------------------------------------------
    def load_all(self) -> None:
        ent_path = self.config_dir / "channel_entities.yaml"
        rules_path = self.config_dir / "named_channel_rules.yaml"
        cases_path = self.config_dir / "channel_historical_cases.yaml"
        for p in (ent_path, rules_path, cases_path):
            if not p.exists():
                raise ChannelConfigError(f"缺少配置文件: {p}")
        with open(ent_path, "r", encoding="utf-8") as f:
            ent_data = yaml.safe_load(f) or {}
        with open(rules_path, "r", encoding="utf-8") as f:
            rules_data = yaml.safe_load(f) or {}
        with open(cases_path, "r", encoding="utf-8") as f:
            cases_data = yaml.safe_load(f) or {}

        raw_entities = ent_data.get("entities") or {}
        for eid, body in raw_entities.items():
            if not isinstance(body, dict):
                continue
            body = dict(body)
            body["id"] = body.get("id") or eid
            self.entities[body["id"]] = body
        # 规则
        for rid, rule in (rules_data.get("rules") or {}).items():
            if isinstance(rule, dict):
                self.rules[rid] = rule
        self.limits = {
            "media_top_n": int(rules_data.get("media_top_n") or 3),
            "actor_top_n": int(rules_data.get("actor_top_n") or 3),
            "amplifier_top_n": int(rules_data.get("amplifier_top_n") or 2),
            "formal_top_n": int(rules_data.get("formal_top_n") or 2),
            "platform_top_n": int(rules_data.get("platform_top_n") or 2),
        }
        # 历史案例
        for c in (cases_data.get("cases") or []):
            if isinstance(c, dict) and c.get("case_id"):
                self.historical_cases.append(c)
        self._check_stale()

    # ------------------------------------------------------------------
    def _check_stale(self) -> None:
        today = date.today()
        for eid, body in self.entities.items():
            lvd = body.get("last_verified_date") or ""
            try:
                d = datetime.strptime(lvd, "%Y-%m-%d").date()
            except (TypeError, ValueError):
                self.stale_warnings.append(f"{eid}: 缺少有效 last_verified_date")
                continue
            if (today - d).days > self.stale_days:
                self.stale_warnings.append(
                    f"{eid} ({body.get('name')}): channel profile may be stale "
                    f"(last_verified {lvd})")
        if self.stale_warnings:
            for w in self.stale_warnings[:10]:
                logger.warning("channel profile may be stale: %s", w)

    # ------------------------------------------------------------------
    # 实体访问
    # ------------------------------------------------------------------
    def get(self, eid: str) -> Optional[dict]:
        return self.entities.get(eid)

    def active_entities(self, entity_type: Optional[str] = None) -> List[dict]:
        out = []
        for body in self.entities.values():
            if body.get("status") != "active":
                continue
            if entity_type and body.get("entity_type") != entity_type:
                continue
            out.append(body)
        return out

    def entities_of_types(self, types) -> List[dict]:
        types = set(types)
        return [b for b in self.entities.values()
                if b.get("entity_type") in types]

    def media_list(self) -> List[dict]:
        return self.active_entities("MEDIA")

    def counts(self) -> Dict[str, int]:
        from collections import Counter
        c = Counter(b.get("entity_type", "?") for b in self.entities.values())
        return {
            "total": len(self.entities),
            "media": c.get("MEDIA", 0),
            "political_actors": c.get("POLITICAL_ACTOR", 0),
            "pundits": c.get("JOURNALIST_OR_COMMENTATOR", 0),
            "platforms": c.get("SOCIAL_PLATFORM", 0),
            "formal_authorities": c.get("FORMAL_AUTHORITY", 0),
            "local_channels": c.get("LOCAL_WHISTLEBLOWER_CHANNEL", 0),
            "internal_newsroom": c.get("INTERNAL_NEWSROOM", 0),
            "rules": len(self.rules),
            "historical_cases": len(self.historical_cases),
            "stale_warnings": len(self.stale_warnings),
        }

    # 规则/历史
    def rule(self, rid: str) -> dict:
        return self.rules.get(rid, {})

    def similar_cases(self, categories, limit: int = 5) -> List[dict]:
        """按类别召回相似历史案例作参照。"""
        cats = set(categories or [])
        scored = []
        for c in self.historical_cases:
            c_cats = set(str(c.get("category") or "").split("|"))
            overlap = len(cats & c_cats)
            if overlap:
                scored.append((overlap, c))
        scored.sort(key=lambda kv: -kv[0])
        return [c for _, c in scored[:limit]]

    def role_stats(self) -> Dict[str, int]:
        """历史案例角色统计（FIRST_RELEASE 等）。"""
        from collections import Counter
        c = Counter()
        for case in self.historical_cases:
            src_type = str(case.get("first_release_type") or "")
            c[f"FIRST_RELEASE:{src_type}"] += 1
        return {
            "first_release_media": c.get("FIRST_RELEASE:MEDIA", 0),
            "first_release_actor": c.get("FIRST_RELEASE:POLITICAL_ACTOR", 0),
            "first_release_formal": c.get("FIRST_RELEASE:FORMAL_AUTHORITY", 0),
            "first_release_social": c.get("FIRST_RELEASE:SOCIAL_PLATFORM", 0),
            "first_release_other": c.get("FIRST_RELEASE:LOCAL_WHISTLEBLOWER_CHANNEL", 0)
            + c.get("FIRST_RELEASE:OTHER", 0),
            "total": len(self.historical_cases),
        }
