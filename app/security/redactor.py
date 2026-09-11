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


CN_SURNAMES = (
    "王李张刘陈杨黄赵周吴徐孙朱马胡郭林何高梁郑罗宋谢唐韩曹许邓萧冯曾程蔡彭潘袁"
    "于董余苏叶吕魏蒋田杜丁沈姜范江傅钟卢汪戴崔任陆廖姚方金邱夏谭韦贾邹石熊孟秦阎"
    "薛侯雷白龙段郝孔邵史毛常万顾赖武康贺严尹钱施牛洪龚"
)

PATTERNS: List[Tuple[str, Pattern[str], str]] = [
    ("PRIVATE_CANARY", re.compile(r"(?i)\b(?:RAW|ATTACHMENT)_PRIVATE_CANARY_[A-Z0-9]+\b"),
     "[REDACTED_CANARY]"),
    ("CN_NAME_OO", re.compile(r"[\u4e00-\u9fff]{1,2}○○"), "[REDACTED_PERSON]"),
    ("CN_NAME_CONTEXT", re.compile(
        r"(?:姓名|名字|當事人|当事人|證人|证人|爆料人|來源|来源|聯絡人|联系人|署名|化名|source|witness)"
        r"[：:\s]*([\u4e00-\u9fff]{2,4})"), "[REDACTED_PERSON]"),
    ("CN_NAME_HONORIFIC", re.compile(
        r"[\u4e00-\u9fff]{1,2}(?:先生|小姐|女士|太太|主任|董事|經理|经理|里長|里长|議員|议员|"
        r"同學|同学|醫師|医师|律師|律师)"), "[REDACTED_PERSON]"),
    ("CN_NAME_VERB", re.compile(
        rf"(?:[{CN_SURNAMES}])[\u4e00-\u9fff]{{1,3}}"
        r"(?=(?:昨天|今天|日前|表示|指出|告訴|告诉|說|说|提供|通過|通过|透過|透过|聯繫|联系|核實|核实|核驗|核验|爆料|證實|证实))"),
     "[REDACTED_PERSON]"),
    ("CN_NAME_CONTACT", re.compile(
        rf"(?:联系|聯繫|建議聯繫|建议联系|請聯繫|请联系|找|跟|問|问)(?:[{CN_SURNAMES}])[\u4e00-\u9fff]{{1,2}}"),
     "[REDACTED_PERSON]"),
    ("CN_NAME_GENERIC", re.compile(
        rf"(?<![\u4e00-\u9fff])(?:[{CN_SURNAMES}])[\u4e00-\u9fff]{{1,2}}(?![\u4e00-\u9fff])"),
     "[REDACTED_PERSON]"),
    ("EN_NAME", re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2}\b"), "[REDACTED_PERSON]"),
    ("ADDRESS", re.compile(
        r"(?:地址|住址|住所|地址為|地址为|戶籍|户籍)[：:\s]*(?!\[REDACTED)([^\s，。；;\[：:]{4,})"),
     "[REDACTED_ADDRESS]"),
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
    ("PHONE", re.compile(
        r"(?<!\d)(?:\+?886[- ]?)?(?:09\d{2}[- ]?\d{3}[- ]?\d{3}|"
        r"0\d{1,2}[- ]?\d{3,4}[- ]?\d{3,4}|\(\d{2,3}\)\s?\d{3,4}[- ]?\d{3,4})(?!\d)"),
     "[REDACTED_PHONE]"),
    ("PHONE", re.compile(r"(?<!\d)(?:\+?886[- ]?)?(?:09\d{8}|0\d{1,2}[- ]?\d{6,8})(?!\d)"),
     "[REDACTED_PHONE]"),
    ("ADDRESS", re.compile(
        r"(?:台北|新北|桃园|台中|台南|高雄|基隆|新竹|苗栗|彰化|南投|云林|嘉义|屏东|宜兰|花莲|台东|澎湖|金门|连江)"
        r"[市縣]?[\u4e00-\u9fff0-9]{0,20}(?:路|街|大道|段)[\u4e00-\u9fff0-9段巷弄號号]{1,30}"),
     "[REDACTED_ADDRESS]"),
    ("IP", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[REDACTED_IP]"),
    ("USERNAME_HANDLE", re.compile(r"(?<![\w.])@[A-Za-z0-9_.-]{3,}"), "[REDACTED_USERNAME]"),
]

# 路径/Message-ID 等长匹配优先，避免被数字规则拆碎。
ORDER = {
    "PRIVATE_CANARY": -2,
    "CN_NAME_OO": -1, "CN_NAME_CONTEXT": 0, "CN_NAME_HONORIFIC": 1,
    "CN_NAME_VERB": 2, "CN_NAME_CONTACT": 3, "CN_NAME_GENERIC": 4, "EN_NAME": 5, "ADDRESS": 6,
    "WINDOWS_PATH": 7, "UNIX_PATH": 8, "MESSAGE_ID": 9, "API_KEY": 10,
    "BEARER_TOKEN": 11, "PASSWORD_SECRET": 12, "LINE_ID": 13, "EMAIL": 14,
    "TAIWAN_ID": 15, "PHONE": 16, "ADDRESS": 17, "BANK_ACCOUNT": 18, "IP": 19,
    "USERNAME_HANDLE": 21,
}


class PrivacyRedactor:
    def __init__(self, extra_patterns: Iterable[Tuple[str, Pattern[str], str]] | None = None,
                 exclude_kinds: Iterable[str] | None = None):
        excluded = {str(x).upper() for x in (exclude_kinds or [])}
        self.patterns = [p for p in PATTERNS if p[0].upper() not in excluded]
        if extra_patterns:
            self.patterns.extend([p for p in list(extra_patterns)
                                  if p[0].upper() not in excluded])

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
