"""文本清洗与标准化：繁转简、统一引号、掩码敏感信息."""
from __future__ import annotations

import re

try:
    from opencc import OpenCC

    _CC = OpenCC("t2s")
except Exception:  # pragma: no cover - 无 opencc 时退化为原样
    _CC = None


# 敏感信息掩码（银行账号、身份证、手机号）—— 日志与摘要中禁止明文出现
BANK_ACCOUNT_RE = re.compile(r"(?<![A-Za-z0-9])(\d{10,20})(?![A-Za-z0-9])")
TW_ID_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]\d{9}(?![A-Za-z0-9])")
PHONE_RE = re.compile(r"(?<!\d)(\+?86[- ]?)?(09\d{8}|0\d{1,2}[- ]?\d{6,8})(?!\d)")


def to_simplified(text: str) -> str:
    """繁转简；失败时原样返回."""
    if not text:
        return ""
    try:
        if _CC is not None:
            return _CC.convert(text)
        return text
    except Exception:
        return text


def mask_sensitive(text: str) -> str:
    """对日志/摘要输出掩码：银行账号 -> A/C****、身份证 -> ID****、手机 -> 09**."""
    if not text:
        return text
    out = BANK_ACCOUNT_RE.sub(lambda m: m.group(1)[:3] + "*" * (len(m.group(1)) - 3), text)
    out = TW_ID_RE.sub(lambda m: m.group(0)[0] + "****", out)
    out = PHONE_RE.sub(lambda m: "09********", out)
    return out


class TextCleaner:
    """原始正文清洗：保持原文语义，仅去控制字符/引号样式/编码噪音."""

    @staticmethod
    def clean(text: str) -> str:
        if not text:
            return ""
        t = text.replace("\r\n", "\n").replace("\r", "\n")
        t = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", t)
        t = re.sub(r"\n{3,}", "\n\n", t)
        # 统一引号/空白字符
        t = t.replace("\u3000", " ").replace("\u200b", "").replace("\ufeff", "")
        t = t.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
        t = re.sub(r"[ \t]{2,}", " ", t)
        return t.strip()


class Normalizer:
    """标准化文本：繁转简 + 小写 + 半角 + 空白归一，用于匹配与关键词统一."""

    @staticmethod
    def normalize(text: str) -> str:
        if not text:
            return ""
        t = TextCleaner.clean(text)
        t = to_simplified(t)
        t = t.lower()
        t = re.sub(r"[，。；：！？、（）【】《》「」『』“”‘’—…·]", " ", t)
        # 金额内常见全角数字转半角
        t = t.translate(str.maketrans("０１２３４５６７８９ＡＢＣ", "0123456789ABC"))
        t = re.sub(r"\s+", " ", t)
        return t.strip()


def text_window(text: str, start: int, end: int, radius: int = 40) -> str:
    """截取 [start,end) 附近文本片段（用于证据摘录）."""
    s = max(0, start - radius)
    e = min(len(text), end + radius)
    return text[s:e].replace("\n", " ").strip()


def best_snippet(full: str, needle_start: int, needle_end: int, max_len: int = 120) -> str:
    seg = text_window(full, needle_start, needle_end, radius=max_len // 2)
    if len(seg) > max_len:
        seg = seg[:max_len]
    return "…" + seg.strip("…") + "…" if seg else ""
