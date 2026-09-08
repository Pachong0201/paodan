"""安全数据模型：只允许结构化、匿名化字段进入外部 LLM。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


class PrivacyLevel(str, Enum):
    OFF = "OFF"
    STRUCTURED_ONLY = "STRUCTURED_ONLY"
    REDACTED_SNIPPETS = "REDACTED_SNIPPETS"

    @classmethod
    def parse(cls, value: Any) -> "PrivacyLevel":
        raw = str(value or "").strip().upper()
        aliases = {
            "STRUCTURED": cls.STRUCTURED_ONLY,
            "STRUCTURED_ONLY": cls.STRUCTURED_ONLY,
            "REDACTED": cls.REDACTED_SNIPPETS,
            "REDACTED_SNIPPETS": cls.REDACTED_SNIPPETS,
            "OFF": cls.OFF,
        }
        return aliases.get(raw, cls.STRUCTURED_ONLY)


class SecurityError(RuntimeError):
    """安全层基础异常。"""


class PrivacyViolation(SecurityError):
    """检测到隐私/敏感内容，请求已阻断。"""


class SecurityBlockedError(PrivacyViolation):
    """OutboundGuard / destination policy 阻断。"""


@dataclass
class GuardResult:
    allowed: bool = True
    reason_codes: List[str] = field(default_factory=list)
    redaction_count: int = 0
    payload_size: int = 0
    findings: List[Dict[str, Any]] = field(default_factory=list)

    def raise_if_blocked(self) -> None:
        if not self.allowed:
            raise SecurityBlockedError(
                "external payload blocked: " + ", ".join(self.reason_codes or ["UNKNOWN"]))


# 这些字段禁止出现在 SafeLLMPayload 中，也禁止出现在最终 HTTP payload 的 key 中。
FORBIDDEN_FIELD_NAMES = {
    "body_text", "combined_text", "original_text", "normalized_text", "html_body",
    "raw_email", "raw_eml", "raw_attachment", "attachment_content", "attachment_text",
    "sender", "sender_email", "recipients", "cc", "message_id", "source_path",
    "cached_path", "api_key", "authorization", "token", "password", "secret",
}


@dataclass
class SafeLLMPayload:
    """External LLM 可见数据白名单。

    注意：本模型刻意不包含正文、原始邮件、附件全文、sender、message_id、本机路径、
    API key/token/password 等字段；新增字段前必须通过 OutboundGuard 与安全测试。
    """

    purpose: str = "screening"
    email_ref: str = ""
    privacy_level: str = PrivacyLevel.STRUCTURED_ONLY.value

    political_categories: List[str] = field(default_factory=list)
    governance_categories: List[str] = field(default_factory=list)

    primary_track: str = "NONE"
    secondary_track: str = ""

    rule_score: float = 0.0
    governance_score: float = 0.0
    final_score: float = 0.0
    priority: str = ""

    political_patterns: List[str] = field(default_factory=list)
    governance_patterns: List[str] = field(default_factory=list)

    evidence_stage: str = ""
    evidence_shapes: List[str] = field(default_factory=list)

    money_facts: List[Dict[str, Any]] = field(default_factory=list)

    target_entities: List[Dict[str, Any]] = field(default_factory=list)
    organization_entities: List[Dict[str, Any]] = field(default_factory=list)

    relationship_chain: List[str] = field(default_factory=list)

    behavior_signals: List[str] = field(default_factory=list)
    procedure_signals: List[str] = field(default_factory=list)

    risk_flags: List[str] = field(default_factory=list)
    public_interest_flags: List[str] = field(default_factory=list)
    known_news_flags: List[str] = field(default_factory=list)
    novelty_flags: List[str] = field(default_factory=list)

    safe_snippets: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # V2/V3 专用白名单字段（仍不含原文/来源身份）
    release_route: str = ""
    secondary_routes: List[str] = field(default_factory=list)
    avoid_routes: List[str] = field(default_factory=list)
    release_flags: Dict[str, Any] = field(default_factory=dict)
    verification_requirements: List[str] = field(default_factory=list)
    public_entities: List[Dict[str, Any]] = field(default_factory=list)
    public_historical_case_summaries: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        raw = asdict(self)
        out: Dict[str, Any] = {}
        for key, value in raw.items():
            if key.lower() in FORBIDDEN_FIELD_NAMES:
                # 防御性：模型字段若被误改也不外发
                continue
            out[key] = value
        return out

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, default=str)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SafeLLMPayload":
        bad = [str(k) for k in (data or {}).keys()
               if str(k).lower() in FORBIDDEN_FIELD_NAMES]
        if bad:
            raise PrivacyViolation("SafeLLMPayload dict contains forbidden fields: " +
                                   ", ".join(sorted(bad)))
        allowed = set(cls.__dataclass_fields__.keys())
        clean = {k: v for k, v in (data or {}).items() if k in allowed}
        return cls(**clean)

    def ensure_safe_fields(self) -> None:
        bad = [k for k in self.to_dict() if k.lower() in FORBIDDEN_FIELD_NAMES]
        if bad:
            raise PrivacyViolation("SafeLLMPayload contains forbidden fields: " + ", ".join(bad))


def hash_ref(value: str, prefix: str = "REF") -> str:
    """把 email_id / 来源引用变为不可逆短哈希，禁止把原文 ID 外发。"""
    if not value:
        return ""
    h = hashlib.sha256(str(value).encode("utf-8", errors="ignore")).hexdigest()[:20]
    return f"{prefix}:{h}"


# 兼容常见命名
SafePayload = SafeLLMPayload
__all__ = [
    "PrivacyLevel", "SecurityError", "PrivacyViolation", "SecurityBlockedError",
    "GuardResult", "SafeLLMPayload", "SafePayload", "FORBIDDEN_FIELD_NAMES", "hash_ref",
]
