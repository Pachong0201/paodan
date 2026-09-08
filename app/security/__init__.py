"""V4.1.1 External LLM Privacy Gateway.

本包是 External LLM 唯一允许的出口层：
业务层只构造 :class:`SafeLLMPayload`，由 ``OutboundGuard`` 做最终 HTTP body 扫描；
任何外部 raw string 调用都会被 ``LLMClient`` 拒绝（fail closed）。
"""
from .models import (SafeLLMPayload, PrivacyLevel, SecurityError, PrivacyViolation,
                     SecurityBlockedError, GuardResult)
from .policy import SecurityPolicy, load_security_policy
from .classifier import Destination, classify_destination
from .pseudonymizer import Pseudonymizer
from .redactor import PrivacyRedactor
from .payload_builder import SafePayloadBuilder
from .outbound_guard import OutboundGuard
from .audit import SecurityAuditLogger
from .spreadsheet import spreadsheet_safe

__all__ = [
    "SafeLLMPayload", "PrivacyLevel", "SecurityError", "PrivacyViolation",
    "SecurityBlockedError", "GuardResult", "SecurityPolicy", "load_security_policy",
    "Destination", "classify_destination", "Pseudonymizer", "PrivacyRedactor",
    "SafePayloadBuilder", "OutboundGuard", "SecurityAuditLogger", "spreadsheet_safe",
]
