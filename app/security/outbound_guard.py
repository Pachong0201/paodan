"""OutboundGuard：fail-closed 的最终出口扫描。

流程：SafePayloadBuilder -> JSON serialize -> OutboundGuard final scan -> requests.post。
本模块同时递归检查 payload key 与最终 HTTP body 中的敏感内容。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional

from .models import (FORBIDDEN_FIELD_NAMES, GuardResult, PrivacyViolation,
                     SecurityBlockedError, SafeLLMPayload)
from .policy import SecurityPolicy
from .redactor import PrivacyRedactor

# key 同义/近似字段（归一化后包含即阻断）
FORBIDDEN_KEY_TOKENS = {
    "bodytext", "combinedtext", "originaltext", "normalizedtext", "htmlbody",
    "rawemail", "raweml", "rawattachment", "attachmentcontent", "attachmenttext",
    "sender", "senderemail", "recipients", "cc", "messageid", "sourcepath",
    "cachedpath", "apikey", "authorization", "token", "password", "secret",
}


def _norm_key(key: Any) -> str:
    s = re.sub(r"[^a-z0-9]", "", str(key or "").lower())
    return s


def _walk_keys(obj: Any, path: str = "") -> Iterable[str]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _walk_keys(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            yield from _walk_keys(v, f"{path}[{i}]")


def _walk_values(obj: Any) -> Iterable[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _walk_values(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_values(v)


class OutboundGuard:
    def __init__(self, policy: Optional[SecurityPolicy] = None,
                 redactor: Optional[PrivacyRedactor] = None):
        self.policy = policy or SecurityPolicy()
        self.redactor = redactor or PrivacyRedactor()
        # 系统 Prompt 是可信指令，可能含公开示例人名/“来源/当事人”等词；
        # 对其内容扫描只保留高置信 secret/PII 规则，避免误报。
        self.system_redactor = PrivacyRedactor(exclude_kinds=(
            "CN_NAME_OO", "CN_NAME_CONTEXT", "CN_NAME_HONORIFIC", "CN_NAME_VERB",
            "CN_NAME_GENERIC", "EN_NAME",
        ))

    # ------------------------------------------------------------------
    def _check_keys(self, obj: Any) -> List[str]:
        reasons: List[str] = []
        # HTTP/LLM 协议字段不是敏感 key；只放行明确协议键，避免 max_tokens 被 token 误伤。
        protocol_keys = {"model", "temperature", "maxtokens", "messages", "responseformat",
                         "type", "role", "content", "jsonobject", "system", "user"}
        for key in _walk_keys(obj):
            nk = _norm_key(key)
            if not nk or nk in protocol_keys:
                continue
            if nk in FORBIDDEN_FIELD_NAMES or nk in FORBIDDEN_KEY_TOKENS:
                reasons.append(f"FORBIDDEN_KEY:{key}")
                continue
            # 同义字段：sender_name/source_path/senderEmail 等；但协议字段已放行。
            for token in FORBIDDEN_KEY_TOKENS:
                if token and token in nk:
                    reasons.append(f"FORBIDDEN_KEY:{key}")
                    break
        return reasons

    def _check_content(self, obj: Any, redactor: Optional[PrivacyRedactor] = None
                       ) -> tuple[List[str], int, List[Dict[str, Any]]]:
        scanner = redactor or self.redactor
        reasons: List[str] = []
        count = 0
        findings_out: List[Dict[str, Any]] = []
        for value in _walk_values(obj):
            findings = scanner.scan(value)
            if findings:
                count += len(findings)
                for f in findings:
                    reasons.append(f"SENSITIVE_CONTENT:{f.kind}")
                    findings_out.append(f.to_dict())
        # 去重保留顺序
        return list(dict.fromkeys(reasons)), count, findings_out[:100]

    def check(self, payload: Any, redactor: Optional[PrivacyRedactor] = None) -> GuardResult:
        """检查业务结构/最终 HTTP body；任何异常 fail-closed。"""
        try:
            if isinstance(payload, SafeLLMPayload):
                data = payload.to_dict()
            elif isinstance(payload, str):
                try:
                    data = json.loads(payload)
                except Exception:  # noqa: BLE001
                    data = payload
            else:
                data = payload
            serialized = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str) \
                if not isinstance(data, str) else data
            reasons = self._check_keys(data)
            content_reasons, redaction_count, findings = self._check_content(data, redactor)
            reasons.extend(content_reasons)
            # 最终 HTTP body 再扫一遍（防止后续拼装/序列化泄漏）
            final_reasons, final_count, final_findings = self._check_content(serialized, redactor)
            reasons.extend(final_reasons)
            redaction_count += final_count
            findings.extend(final_findings)
            reasons = list(dict.fromkeys(reasons))
            size = len(serialized.encode("utf-8", errors="ignore"))
            if size > int(self.policy.max_payload_chars):
                reasons.append("PAYLOAD_TOO_LARGE")
            return GuardResult(allowed=not reasons, reason_codes=reasons,
                               redaction_count=redaction_count, payload_size=size,
                               findings=findings[:100])
        except Exception as exc:  # noqa: BLE001
            if self.policy.fail_closed:
                return GuardResult(allowed=False, reason_codes=[f"GUARD_ERROR:{type(exc).__name__}"])
            raise

    # aliases
    def validate(self, payload: Any) -> GuardResult:
        return self.check(payload)

    def scan(self, payload: Any) -> GuardResult:
        return self.check(payload)

    def assert_safe(self, payload: Any) -> GuardResult:
        result = self.check(payload)
        if not result.allowed:
            raise SecurityBlockedError("outbound guard blocked: " + ", ".join(result.reason_codes))
        return result

    def check_and_raise(self, payload: Any) -> GuardResult:
        return self.assert_safe(payload)

    def final_scan(self, http_body: Any) -> GuardResult:
        return self.check(http_body)
