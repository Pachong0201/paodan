"""Dashboard 展示层 Privacy Filter。

用途：把 SQLite 中已经存在的 subject / summary / verification / filename /
V2/V3 reason 等二次生成文本，在进入 Safe ViewModel 前统一脱敏。
与 Jinja autoescape（防 XSS）是两个独立层次。
"""
from __future__ import annotations

from typing import Any, Iterable, List

from ..security.redactor import PrivacyRedactor


class DashboardPrivacyFilter:
    def __init__(self) -> None:
        # 默认 PrivacyRedactor 包含 Email/Phone/ID/Bank/Path/Message-ID/API Key/
        # LINE/Address/中文姓名/英文姓名 等规则。
        self._redactor = PrivacyRedactor()

    def redact_text(self, value: Any) -> str:
        if value is None:
            return ""
        return self._redactor.redact(str(value))

    def redact_list(self, values: Iterable[Any]) -> List[str]:
        return [self.redact_text(v) for v in (values or [])]


_default_filter = DashboardPrivacyFilter()


def dashboard_safe_text(value: Any) -> str:
    return _default_filter.redact_text(value)


def dashboard_safe_list(values: Iterable[Any]) -> List[str]:
    return _default_filter.redact_list(values)
