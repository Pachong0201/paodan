"""Spreadsheet/CSV formula injection 防护：邮件可控字段必须经过本函数。"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

FORMULA_PREFIXES = ("=", "+", "-", "@")


def spreadsheet_safe(value: Any) -> Any:
    """如果字符串 trim-left 后以 = + - @ 开头，强制作为文本。

    使用 Excel 通用做法：在值前加单引号。CSV 文本打开时 Excel 也会将其视为文本。
    非字符串值原样返回（数字单元格本身不是公式）。
    """
    if not isinstance(value, str):
        return value
    # 去除前后空白后再判断；保留原值的前导空白，避免改变人工输入
    stripped = value.lstrip()
    if stripped.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def spreadsheet_safe_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k: spreadsheet_safe(v) for k, v in row.items()}


def spreadsheet_safe_list(values: Iterable[Any]) -> List[Any]:
    return [spreadsheet_safe(v) for v in values]


# 兼容常见命名
safe_spreadsheet_value = spreadsheet_safe
