"""金额、日期、账户、电话号码等抽取（规则为主，供规则引擎与实体识别复用）."""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import List, Optional

CURRENCY_MAP = {
    "人民币": "CNY", "rmb": "CNY", "cn¥": "CNY", "人民币元": "CNY", "人民幣": "CNY",
    "新台币": "TWD", "新臺幣": "TWD", "台币": "TWD", "臺幣": "TWD", "nt$": "TWD",
    "ntd": "TWD", "美元": "USD", "美金": "USD", "us$": "USD", "港币": "HKD",
    "港幣": "HKD", "日圆": "JPY", "日元": "JPY", "韩元": "KRW", "韓元": "KRW",
    "欧元": "EUR", "歐元": "EUR", "英镑": "GBP", "英鎊": "GBP",
}
UNIT_MAP = {"万": 1e4, "萬": 1e4, "亿": 1e8, "億": 1e8, "千": 1e3, "百": 100}

# 1) 汉字数字+单位组合：八十萬 / 八十万元 / 一点五亿 —— 必须有单位词(万/亿/元等)才算金额
CN_NUM_RE = re.compile(
    r"([零〇一二三四五六七八九十百千万亿點點两兩壹贰叁肆伍陆柒捌玖拾佰仟万亿兆]+)\s*(万亿|億|万|萬|亿|億|千|百|仟|佰)?\s*(元|块|塊|圓|圆|美金|美元)"
    r"|([零〇一二三四五六七八九十百千万亿點點两兩壹贰叁肆伍陆柒捌玖拾佰仟万亿兆]+)\s*(万亿|億|万|萬|亿|億)"
)
# 简化处理：先正则找 数字+单位
AMOUNT_RE = re.compile(
    r"(?<![A-Za-z0-9年])(\d{1,3}(?:,\d{3})+|\d+\.?\d*)\s*(亿|億|万|萬|千|百)?\s*(新台币|新臺幣|台币|臺幣|人民币|人民幣|美金|美元|港币|港幣|元|塊|塊|圆|圓)?(?![A-Za-z0-9%])"
)
CURRENCY_PREFIX_RE = re.compile(
    r"(新台币|新臺幣|台币|臺幣|人民币|人民幣|美金|美元|港币|港幣|nt\$|us\$|rmb|cn¥|NT\$)", re.IGNORECASE
)

# 账户/卡号（10-20位数字）
ACCOUNT_RE = re.compile(r"(?<![A-Za-z0-9])(\d{10,20})(?![A-Za-z0-9])")
# 日期：2024/03/01、2024-03-01、2024年3月1日、3月1日
DATE_RE = re.compile(
    r"((?:19|20)\d{2}\s*[年./\-]\s*\d{1,2}\s*[月./\-]\s*\d{1,2}\s*日?|"
    r"(?:19|20)\d{2}\s*年\s*\d{1,2}\s*月|"
    r"\d{1,2}\s*月\s*\d{1,2}\s*日)"
)
PHONE_RE = re.compile(r"(?<!\d)(09\d{8}|02[- ]?\d{7,8}|0\d{1,2}[- ]?\d{6,8})(?!\d)")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
DOC_NO_RE = re.compile(r"(?<!\d)(\d{7,9})(?!\d)")  # 统一编号等


@dataclass
class MoneyHit:
    raw: str
    amount: float
    currency: str = "TWD"
    low: Optional[float] = None      # 范围下限
    high: Optional[float] = None     # 范围上限

    def to_dict(self) -> dict:
        return asdict(self)


def _cn_digit_char(c: str) -> int:
    return {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "兩": 2, "三": 3, "四": 4,
            "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}.get(c, -1)


def _parse_cn_number(s: str) -> Optional[float]:
    """中文数字串转数值（支持 八十/一百二十/一点五）。"""
    if not s:
        return None
    # 小树处理：一点五万
    if "点" in s or "點" in s:
        parts = re.split(r"[点點]", s, maxsplit=1)
        if len(parts) == 2:
            base = _cn_number_int(parts[0])
            frac = parts[1]
            if base is None or not frac:
                return None
            # 小数部分按位
            frac_val = 0.0
            digits = [_cn_digit_char(c) for c in frac]
            if any(d < 0 for d in digits):
                return None
            for i, d in enumerate(digits):
                frac_val += d * (0.1 ** (i + 1))
            return base + frac_val
    return _cn_number_int(s)


def _cn_number_int(s: str) -> Optional[float]:
    """标准中文整数（万/亿进制）。"""
    total = 0
    section = 0
    number = 0
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "兩": 2, "三": 3,
              "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    for c in s:
        if c in digits:
            number = digits[c]
        elif c in ("十", "拾"):
            section += (number or 1) * 10
            number = 0
        elif c in ("百", "佰"):
            section += (number or 1) * 100
            number = 0
        elif c in ("千", "仟"):
            section += (number or 1) * 1000
            number = 0
        elif c in ("万", "萬"):
            total += (section + number) * 10000
            section = 0
            number = 0
        elif c in ("亿", "億"):
            total += (section + number) * 100000000
            section = 0
            number = 0
        else:
            return None
    return total + section + number


def parse_amount(text: str) -> List[MoneyHit]:
    """从一段文本抽取金额，支持 80万/80萬元/800,000/NT$800000/新台币80万元/人民币20万元 等."""
    hits: List[MoneyHit] = []
    if not text:
        return hits
    seen: set = set()

    def _push(raw: str, amount: float, currency: str):
        key = (round(amount, 2), currency)
        if key in seen:
            return
        seen.add(key)
        hits.append(MoneyHit(raw=raw.strip(), amount=round(amount, 2), currency=currency))

    # 阿拉伯数字金额：必须有单位/币种/千分位之一，否则跳过
    # （避免把正文列表序号 "1、2、3"、日期 "2026"、版本号等当金额）
    for m in AMOUNT_RE.finditer(text):
        raw = m.group(0)
        num_str = m.group(1).replace(",", "")
        unit = m.group(2) or ""
        cur = m.group(3) or ""
        try:
            num = float(num_str)
        except ValueError:
            continue
        if unit:
            num *= UNIT_MAP.get(unit, 1)
        if num > 1e13 or num < 1:   # 过滤电话/年份等误命中
            continue
        currency = _resolve_currency(text, m.start(), m.end())
        # 关键过滤：纯数字(无单位无币种无千分位)不算金额——如列表序号"1、"、编号"30"、日期"2026"
        has_thousands = "," in m.group(1) or "，" in m.group(1)
        if not unit and not cur and not currency and not has_thousands:
            continue
        # 纯数字且带币种词才接受（如 "NT$800000" 由前缀币种支撑）
        currency = currency or CURRENCY_MAP.get(cur, "TWD")
        _push(raw, num, currency)

    # 中文数字金额：新台币八十万元 / 八十万 —— 分支1: 数字+万/亿+元; 分支2: 数字+万/亿
    for m in CN_NUM_RE.finditer(text):
        raw = m.group(0)
        digits_part = m.group(1) or m.group(4)
        if not digits_part or re.search(r"\d", digits_part):
            continue
        amt = _parse_cn_number(digits_part)
        if amt is None:
            continue
        unit = m.group(2) or m.group(5) or ""
        cur = m.group(3) or ""
        if unit:
            amt *= UNIT_MAP.get(unit, 1)
        # 纯中文数字无万/亿且无比单位词（如“一元”“两万”）至少要求>=10 或带单位组合
        if amt < 10 and not unit:
            continue
        if not (1 <= amt <= 1e13):
            continue
        currency = _resolve_currency(text, m.start(), m.end()) or CURRENCY_MAP.get(cur, "TWD")
        _push(raw, amt, currency)

    hits.sort(key=lambda h: h.amount)
    return hits


def _resolve_currency(text: str, start: int, end: int, radius: int = 12) -> Optional[str]:
    """向前后小窗口找币种前缀."""
    pre = text[max(0, start - radius):start]
    post = text[end:end + radius]
    for w in CURRENCY_PREFIX_RE.findall(pre + " " + post):
        return CURRENCY_MAP.get(w.lower(), "TWD") if not any(ch.isalpha() for ch in w) or w.lower() in ("nt$", "rmb") else None
    for cand in list(CURRENCY_MAP.keys()):
        if len(cand) > 2 and (cand in pre or cand in post):
            return CURRENCY_MAP[cand]
    return None


def extract_dates(text: str) -> List[str]:
    if not text:
        return []
    out = []
    seen = set()
    for m in DATE_RE.finditer(text):
        d = m.group(1).replace(" ", "")
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def extract_accounts(text: str) -> List[str]:
    """账户号（含掩码调用方处理）."""
    if not text:
        return []
    return list(dict.fromkeys(m.group(1) for m in ACCOUNT_RE.finditer(text)))


def extract_phones(text: str) -> List[str]:
    if not text:
        return []
    return list(dict.fromkeys(m.group(0).replace(" ", "") for m in PHONE_RE.finditer(text)))


def extract_emails(text: str) -> List[str]:
    if not text:
        return []
    return list(dict.fromkeys(m.group(0) for m in EMAIL_RE.finditer(text)))
