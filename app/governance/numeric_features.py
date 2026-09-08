# -*- coding: utf-8 -*-
"""GovernanceNumericFeatures：治理投诉数字/时间/次数/金额表达归一化。

目标：不再依赖 "500户"/"等床8天" 等测试样本字符串硬编码，而是统一抽取
duration / frequency / affected_population / money_loss / complaint_count /
waiting_time / deadline_pressure。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from ..preprocessing.extractors import parse_amount

CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "兩": 2, "三": 3,
             "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_UNITS = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000,
            "万": 10000, "萬": 10000, "亿": 100000000, "億": 100000000}

CN_NUM_CHARS = "零〇一二三四五六七八九十百千万亿兩两壹贰叁肆伍陆柒捌玖拾佰仟萬億"
NUM_RE = r"\d[\d,]*"
CN_NUM_RE = f"[{CN_NUM_CHARS}]+"


def cn_number_to_int(text: str) -> Optional[int]:
    """中文数字转整数：五百->500，四百->400，一百二十->120，两万->20000。"""
    if not text:
        return None
    s = str(text).strip()
    if not s:
        return None
    # 纯阿拉伯数字
    try:
        return int(s.replace(",", ""))
    except ValueError:
        pass
    # 大写/异体归一
    trans = str.maketrans({"壹": "一", "贰": "二", "叁": "三", "肆": "四", "伍": "五",
                           "陆": "六", "柒": "七", "捌": "八", "玖": "九",
                           "拾": "十", "佰": "百", "仟": "千", "萬": "万", "億": "亿"})
    s = s.translate(trans)
    if any(ch.isdigit() for ch in s):
        # 混合如 3千/1万
        m = re.match(r"^(\d+)\s*([万亿千百十]?)$", s)
        if m:
            base = int(m.group(1))
            unit = CN_UNITS.get(m.group(2), 1)
            return int(base * unit)
    total = 0
    section = 0
    number = 0
    for ch in s:
        if ch in CN_DIGITS:
            number = CN_DIGITS[ch]
        elif ch in ("十", "百", "千"):
            unit = CN_UNITS[ch]
            section += (number or 1) * unit
            number = 0
        elif ch in ("万", "亿"):
            unit = CN_UNITS[ch]
            total += (section + number) * unit
            section = 0
            number = 0
        else:
            return None
    return int(total + section + number)


def _num(value: str) -> Optional[int]:
    value = str(value or "").replace(",", "").strip()
    if not value:
        return None
    if value.isdigit():
        return int(value)
    return cn_number_to_int(value)


def _match_first(text: str, pattern: str, flags: int = 0):
    return re.search(pattern, text, flags)


@dataclass
class GovernanceNumericFeatures:
    duration_days: Optional[float] = None
    approximate_duration_days: Optional[float] = None
    frequency: Optional[float] = None
    affected_population: Optional[float] = None
    population_value: Optional[float] = None
    population_lower_bound: Optional[float] = None
    population_confidence: float = 0.0
    money_loss: Optional[float] = None
    complaint_count: Optional[float] = None
    waiting_time_days: Optional[float] = None
    deadline_pressure: bool = False
    evidence_terms: List[str] = field(default_factory=list)
    raw_matches: Dict[str, str] = field(default_factory=dict)

    @property
    def waiting_time(self):
        return self.waiting_time_days

    @property
    def population(self):
        return self.population_value

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # 兼容规格命名：waiting_time / population
        d["waiting_time"] = self.waiting_time_days
        d["population"] = self.population_value
        return d

    @classmethod
    def extract(cls, text: str) -> "GovernanceNumericFeatures":
        return extract_numeric_features(text)


# ---------------------------------------------------------------------------
def _extract_population(text: str, out: GovernanceNumericFeatures) -> None:
    unit = r"(?:户|戶|个家庭|個家庭|家庭|人|名|位|家)"
    # 超过400户 / 约五百户 / 500户
    pat = re.compile(r"(?P<prefix>超过|逾|至少|约|大约|近|约莫|近|超过)?\s*"
                     r"(?P<num>" + NUM_RE + r"|" + CN_NUM_RE + r")\s*(?P<unit>" + unit + r")")
    for m in pat.finditer(text):
        num_text = m.group("num")
        prefix = m.group("prefix") or ""
        # 跳过“数百户”中误匹配到的“百户”；交给下面的 approx 分支处理。
        if (not prefix and num_text in ("十", "百", "千", "万", "亿")
                and m.start() > 0 and text[m.start() - 1] in "数數几幾上近"):
            continue
        num = _num(num_text)
        if num is None or num <= 0:
            continue
        conf = 0.95
        value = float(num)
        lower = float(num)
        if prefix in ("超过", "逾", "至少"):
            conf = 0.8
        elif prefix in ("约", "大约", "近", "约莫"):
            conf = 0.75
        if out.population_value is None or value > out.population_value:
            out.population_value = value
            out.population_lower_bound = lower
            out.population_confidence = conf
            out.raw_matches["population"] = m.group(0).strip()
    # 数百户/几百户/数十户/上千户/上万户
    approx = re.compile(r"(?P<word>数百|數百|几百|幾百|数十|數十|上千|上万|上萬)\s*(?P<unit>" + unit + r")")
    for m in approx.finditer(text):
        word = m.group("word")
        lower = {"数百": 300, "數百": 300, "几百": 300, "幾百": 300,
                 "数十": 30, "數十": 30, "上千": 1000, "上万": 10000, "上萬": 10000}.get(word, 300)
        if out.population_lower_bound is None or lower > out.population_lower_bound:
            out.population_lower_bound = float(lower)
            out.population_value = max(out.population_value or 0.0, float(lower))
            out.population_confidence = max(out.population_confidence, 0.8)
            out.raw_matches["population"] = m.group(0).strip()
    if out.population_value is None and out.population_lower_bound is not None:
        out.population_value = out.population_lower_bound
    out.affected_population = out.population_value or out.population_lower_bound


def _extract_duration(text: str, out: GovernanceNumericFeatures) -> None:
    # 特殊表达
    specials = [
        ("半年", 180, 0.9), ("六个月", 180, 0.95), ("六個月", 180, 0.95),
        ("一个月", 30, 0.95), ("一個月", 30, 0.95), ("一周", 7, 0.95),
        ("一星期", 7, 0.95), ("连续数月", 90, 0.6), ("連續數月", 90, 0.6),
        ("数月", 90, 0.6), ("數月", 90, 0.6), ("几个月", 90, 0.6), ("幾個月", 90, 0.6),
        ("多年", 1095, 0.6), ("数年", 1095, 0.6), ("數年", 1095, 0.6),
        ("超过一星期", 7, 0.7), ("超過一星期", 7, 0.7),
    ]
    for term, days, conf in specials:
        if term in text:
            if conf >= 0.8:
                out.duration_days = max(out.duration_days or 0, float(days))
            else:
                out.approximate_duration_days = max(out.approximate_duration_days or 0, float(days))
            out.raw_matches.setdefault("duration", term)
    # 阿拉伯数字 + 单位
    pat = re.compile(r"(?P<num>" + NUM_RE + r"|" + CN_NUM_RE + r")\s*"
                     r"(?P<unit>天|日|周|週|星期|个月|個月|月|年|小时|小時|钟头|鐘頭)")
    for m in pat.finditer(text):
        n = _num(m.group("num"))
        if n is None or n <= 0:
            continue
        unit = m.group("unit")
        if unit in ("天", "日"):
            days = float(n)
        elif unit in ("周", "週", "星期"):
            days = float(n) * 7
        elif unit in ("个月", "個月", "月"):
            days = float(n) * 30
        elif unit == "年":
            days = float(n) * 365
        else:
            days = float(n) / 24.0
        if out.duration_days is None or days > out.duration_days:
            out.duration_days = days
            out.raw_matches["duration"] = m.group(0).strip()


def _extract_frequency(text: str, out: GovernanceNumericFeatures) -> None:
    pat = re.compile(r"(?P<num>" + NUM_RE + r"|" + CN_NUM_RE + r")\s*次")
    for m in pat.finditer(text):
        n = _num(m.group("num"))
        if n is not None and n > 0:
            out.frequency = max(out.frequency or 0, float(n))
            out.raw_matches.setdefault("frequency", m.group(0).strip())
    for term in ("多次", "多次反映", "反复", "反覆", "连续投诉", "連續投訴", "打了七次电话",
                 "打了七次電話", "多次打", "屡次", "屢次"):
        if term in text:
            out.frequency = max(out.frequency or 0, 3.0)
            out.raw_matches.setdefault("frequency", term)
    # 陈情/投诉/1999 次数
    complaint_terms = ["1999", "市长信箱", "市長信箱", "陈情", "陳情", "投诉", "投訴",
                       "申诉", "申訴", "找议员", "找議員", "找里长", "找里長"]
    cnt = sum(1 for t in complaint_terms if t in text)
    if cnt:
        out.complaint_count = float(cnt)
    if out.frequency is None and out.complaint_count:
        out.frequency = float(out.complaint_count)


def _extract_money(text: str, out: GovernanceNumericFeatures) -> None:
    try:
        hits = parse_amount(text)
    except Exception:  # noqa: BLE001
        hits = []
    if hits:
        out.money_loss = max(float(h.amount) for h in hits)
        out.raw_matches.setdefault("money", hits[0].raw)
    else:
        # 4.8万/4.8萬/48000 等常见治理损失表达
        m = re.search(r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>万|萬|元)", text)
        if m:
            try:
                val = float(m.group("num"))
                if m.group("unit") in ("万", "萬"):
                    val *= 10000
                out.money_loss = val
                out.raw_matches.setdefault("money", m.group(0))
            except ValueError:
                pass


def _extract_waiting_and_deadline(text: str, out: GovernanceNumericFeatures) -> None:
    m = re.search(r"(?:等床|等候|等待|排队|排隊|等)\s*(?P<num>" + NUM_RE + r"|" + CN_NUM_RE + r")\s*(?:天|日|周|週|个月|個月|月)", text)
    if m:
        n = _num(m.group("num"))
        if n is not None:
            unit = "月" if "月" in m.group(0) else ("周" if "周" in m.group(0) or "週" in m.group(0) else "天")
            out.waiting_time_days = float(n) * (30 if unit == "月" else (7 if unit == "周" else 1))
            out.raw_matches.setdefault("waiting_time", m.group(0).strip())
    deadline_terms = ["截止", "期限", "到期", "即将到期", "即將到期", "最后期限", "最後期限",
                      "申请截止", "申請截止", "逾期", "补件期限", "補件期限"]
    out.deadline_pressure = any(t in text for t in deadline_terms)
    out.evidence_terms = [t for t in ("截图", "截圖", "公文", "通知", "错误码", "錯誤碼",
                                      "时间线", "時間線", "三联单", "三聯單", "附件")
                          if t in text]


def extract_numeric_features(text: str) -> GovernanceNumericFeatures:
    out = GovernanceNumericFeatures()
    if not text:
        return out
    t = str(text)
    _extract_population(t, out)
    _extract_duration(t, out)
    _extract_frequency(t, out)
    _extract_money(t, out)
    _extract_waiting_and_deadline(t, out)
    return out


def features_to_dict(text: str) -> Dict[str, Any]:
    return extract_numeric_features(text).to_dict()


# 兼容常见命名
extract_governance_numeric_features = extract_numeric_features
extract_features = extract_numeric_features
