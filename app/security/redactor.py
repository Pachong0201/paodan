"""PrivacyRedactor：识别并替换/阻断常见 PII 与 secret。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, List, Pattern, Tuple


@dataclass
class RedactionFinding:
    kind: str
    start: int
    end: int
    matched: str
    replacement: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # 审计/测试可见 kind/位置/替换，但不返回完整敏感原文
        d["matched_preview"] = _preview(self.matched)
        d.pop("matched", None)
        return d


def _preview(value: str) -> str:
    s = str(value or "")
    if len(s) <= 4:
        return "*" * len(s)
    return s[:2] + "*" * max(1, len(s) - 4) + s[-2:]


PATTERNS: List[Tuple[str, Pattern[str], str]] = [
    ("PRIVATE_CANARY", re.compile(r"(?i)\b(?:RAW|ATTACHMENT)_PRIVATE_CANARY_[A-Z0-9]+\b"),
     "[REDACTED_CANARY]"),
    ("MESSAGE_ID", re.compile(r"<[^<>\s@]+@[^<>\s]+>"), "[REDACTED_MESSAGE_ID]"),
    ("WINDOWS_PATH", re.compile(r"(?:[A-Za-z]:\\[^\s<>\"'|?*]+|\\\\[^\\/\s]+\\[^\s<>\"'|?*]+)"),
     "[REDACTED_PATH]"),
    ("UNIX_PATH", re.compile(r"(?<![A-Za-z0-9])(?:/(?:home|Users|tmp|var|etc|opt|private|root)/[^\s<>\"']+)"),
     "[REDACTED_PATH]"),
    ("API_KEY", re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"), "[REDACTED_API_KEY]"),
    ("BEARER_TOKEN", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{8,}\b"), "[REDACTED_TOKEN]"),
    ("PASSWORD_SECRET", re.compile(
        r"(?i)\b(?:password|passwd|pwd|secret|token|api[_ -]?key)\s*[:=：]\s*([^\s,;，；]+)"),
     "[REDACTED_SECRET]"),
    ("LINE_ID", re.compile(r"(?i)\bline(?:[\s_-]*id)?[\s:：-]*[A-Za-z0-9._-]{4,}\b"),
     "[REDACTED_LINE_ID]"),
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
     "[REDACTED_EMAIL]"),
    ("TAIWAN_ID", re.compile(r"(?<![A-Za-z0-9])[A-Za-z][12]\d{8}(?![A-Za-z0-9])"), "[REDACTED_TW_ID]"),
    ("BANK_ACCOUNT", re.compile(r"(?<![A-Za-z0-9])\d{10,20}(?![A-Za-z0-9])"), "[REDACTED_ACCOUNT]"),
    ("PHONE", re.compile(r"(?<!\d)(?:\+?886[- ]?)?(?:09\d{8}|0\d{1,2}[- ]?\d{6,8})(?!\d)"),
     "[REDACTED_PHONE]"),
    ("IP", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[REDACTED_IP]"),
    ("USERNAME_HANDLE", re.compile(r"(?<![\w.])@[A-Za-z0-9_.-]{3,}"), "[REDACTED_USERNAME]"),
]

# 路径/Message-ID 等长匹配优先，避免被数字规则拆碎。
ORDER = {
    "PRIVATE_CANARY": -1, "WINDOWS_PATH": 0, "UNIX_PATH": 1, "MESSAGE_ID": 2, "API_KEY": 3,
    "BEARER_TOKEN": 4, "PASSWORD_SECRET": 5, "LINE_ID": 6, "EMAIL": 7,
    "TAIWAN_ID": 8, "PHONE": 9, "BANK_ACCOUNT": 10, "IP": 11,
    "USERNAME_HANDLE": 12,
}


class PrivacyRedactor:
    def __init__(self, extra_patterns: Iterable[Tuple[str, Pattern[str], str]] | None = None):
        self.patterns = list(PATTERNS)
        if extra_patterns:
            self.patterns.extend(list(extra_patterns))

    def scan(self, text: Any) -> List[RedactionFinding]:
        s = "" if text is None else str(text)
        if not s:
            return []
        candidates: List[RedactionFinding] = []
        for kind, pattern, replacement in self.patterns:
            for m in pattern.finditer(s):
                candidates.append(RedactionFinding(kind=kind, start=m.start(), end=m.end(),
                                                  matched=m.group(0), replacement=replacement))
        # 去重叠：按 kind 优先级与起点选择，长匹配优先。
        candidates.sort(key=lambda f: (ORDER.get(f.kind, 99), f.start, -(f.end - f.start)))
        selected: List[RedactionFinding] = []
        occupied: List[Tuple[int, int]] = []
        for f in candidates:
            if any(not (f.end <= a or f.start >= b) for a, b in occupied):
                continue
            selected.append(f)
            occupied.append((f.start, f.end))
        selected.sort(key=lambda f: f.start)
        return selected

    def redact(self, text: Any) -> str:
        s = "" if text is None else str(text)
        if not s:
            return s
        out = s
        # 从后向前替换，避免偏移
        for f in sorted(self.scan(s), key=lambda x: x.start, reverse=True):
            out = out[:f.start] + f.replacement + out[f.end:]
        return out

    def contains_sensitive(self, text: Any) -> bool:
        return bool(self.scan(text))

    def redaction_count(self, text: Any) -> int:
        return len(self.scan(text))

    def redact_obj(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, list):
            return [self.redact_obj(v) for v in value]
        if isinstance(value, tuple):
            return tuple(self.redact_obj(v) for v in value)
        if isinstance(value, dict):
            return {self.redact_obj(k) if isinstance(k, str) else k: self.redact_obj(v)
                    for k, v in value.items()}
        return value


def redact_sensitive(text: Any) -> str:
    return PrivacyRedactor().redact(text)


def contains_sensitive(text: Any) -> bool:
    return PrivacyRedactor().contains_sensitive(text)
