"""V3 具名首发渠道推荐模块数据模型。

NamedChannelRecommendation —— 一条 S/A/B 线索的具名渠道最终推荐。
只输出建议，不执行任何发布/检举动作。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Optional

# 渠道实体类型
VALID_ENTITY_TYPES = {
    "MEDIA", "POLITICAL_ACTOR", "JOURNALIST_OR_COMMENTATOR", "SOCIAL_PLATFORM",
    "FORMAL_AUTHORITY", "LOCAL_WHISTLEBLOWER_CHANNEL", "INTERNAL_NEWSROOM",
}
# 渠道角色
VALID_ROLES = {
    "FIRST_RELEASE", "INVESTIGATIVE", "DISCLOSURE", "AMPLIFIER", "VERIFIER",
    "FORMAL_REFERRAL", "DATA_ANALYSIS", "LOCAL_NETWORK", "SOURCE_PROTECTION",
}
VALID_STATUS = {"active", "inactive", "historical", "unknown"}
# 输出组（五组 + avoid）
OUTPUT_GROUPS = {
    "recommended_media": ("MEDIA", "INTERNAL_NEWSROOM"),
    "recommended_disclosure_actors": ("POLITICAL_ACTOR", "JOURNALIST_OR_COMMENTATOR"),
    "recommended_amplifiers": ("JOURNALIST_OR_COMMENTATOR", "POLITICAL_ACTOR", "SOCIAL_PLATFORM"),
    "recommended_formal_channels": ("FORMAL_AUTHORITY",),
    "recommended_platforms": ("SOCIAL_PLATFORM",),
    "local_channels": ("LOCAL_WHISTLEBLOWER_CHANNEL",),
}


@dataclass
class NamedChannelHit:
    """一条具名渠道推荐（含打分与理由）。"""
    entity_id: str = ""
    name: str = ""
    entity_type: str = ""
    channel_group: str = ""        # media/disclosure_actor/amplifier/formal/platform/local
    fit_score: float = 0.0
    roles: List[str] = field(default_factory=list)
    reason: str = ""
    status: str = "active"
    rule_ids: List[str] = field(default_factory=list)
    rank: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class NamedChannelRecommendation:
    """V3 具名渠道最终输出。"""
    email_id: str = ""
    priority: str = "B"
    final_score: float = 0.0
    categories: List[str] = field(default_factory=list)
    primary_route: str = ""
    recommended_media: List[dict] = field(default_factory=list)
    recommended_disclosure_actors: List[dict] = field(default_factory=list)
    recommended_amplifiers: List[dict] = field(default_factory=list)
    recommended_formal_channels: List[dict] = field(default_factory=list)
    recommended_platforms: List[dict] = field(default_factory=list)
    local_channels: List[dict] = field(default_factory=list)
    avoid_named_channels: List[str] = field(default_factory=list)
    recommended_sequence: List[dict] = field(default_factory=list)
    editor_note: str = ""
    unlisted_channel_suggestion: str = ""
    # 溯源
    candidate_count: int = 0
    rule_hits: List[str] = field(default_factory=list)
    source: str = "rule"          # rule / rule+llm / llm / template
    llm_status: str = ""          # ok/template/failed

    def to_dict(self) -> dict:
        return asdict(self)

    def to_jsonl_line(self) -> str:
        import json
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class NamedChannelRecord:
    """落库行结构：一条推荐拆成多行（每实体一行）。"""
    email_id: str = ""
    entity_id: str = ""
    entity_name: str = ""
    entity_type: str = ""
    channel_group: str = ""
    channel_role: List[str] = field(default_factory=list)
    fit_score: float = 0.0
    rank: int = 0
    reason: str = ""
    created_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
