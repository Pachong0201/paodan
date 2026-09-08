"""External LLM Security Audit：只写最小必要元数据，不写 payload/正文/PII。"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .models import hash_ref
from .policy import SecurityPolicy
from .redactor import PrivacyRedactor

logger = logging.getLogger(__name__)


class SecurityAuditLogger:
    ALLOWED_FIELDS = (
        "timestamp", "event", "email_ref_hash", "purpose", "provider_host",
        "model", "privacy_level", "payload_size", "redaction_count", "result",
        "reason_codes",
    )

    def __init__(self, policy: Optional[SecurityPolicy] = None):
        self.policy = policy or SecurityPolicy()
        self.redactor = PrivacyRedactor()
        self.path = Path(self.policy.audit_log_path)
        if not self.path.is_absolute():
            self.path = Path(__file__).resolve().parents[2] / self.path

    def log(self, event: str, email_ref_hash: str = "", purpose: str = "",
            provider_host: str = "", model: str = "", privacy_level: str = "",
            payload_size: int = 0, redaction_count: int = 0, result: str = "ok",
            reason_codes: Optional[Iterable[str]] = None, **kwargs) -> Dict[str, Any]:
        if not self.policy.audit_enabled:
            return {}
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": self._clean(event),
            "email_ref_hash": self._clean_ref(email_ref_hash),
            "purpose": self._clean(purpose),
            "provider_host": self._clean(provider_host),
            "model": self._clean(model),
            "privacy_level": self._clean(privacy_level or self.policy.privacy_level),
            "payload_size": int(payload_size or 0),
            "redaction_count": int(redaction_count or 0),
            "result": self._clean(result),
            "reason_codes": [self._clean(x) for x in (reason_codes or [])][:50],
        }
        # 防御性：确保没有任何 canary/PII 明文
        for key, value in list(record.items()):
            if isinstance(value, str) and self.redactor.contains_sensitive(value):
                record[key] = self.redactor.redact(value)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError as exc:
            logger.warning("security audit 写入失败: %s", exc)
        return record

    def _clean_ref(self, value: str) -> str:
        if not value:
            return ""
        if value.startswith("REF:"):
            return value
        return hash_ref(value, "REF")

    def _clean(self, value: Any) -> str:
        s = str(value or "")
        if self.redactor.contains_sensitive(s):
            return self.redactor.redact(s)
        return s[:500]


# 兼容常见命名
SecurityAudit = SecurityAuditLogger
