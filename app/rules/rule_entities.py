"""规则实体抽取（独立模块，供 RuleEngine 复用）."""
from __future__ import annotations

import re
from typing import List

from ..models import EntityHit
from ..preprocessing.extractors import extract_accounts, extract_dates, parse_amount
from ..preprocessing.normalization import Normalizer
from .pattern_engine import extract_person_candidates

ORG_SUFFIX_RE = re.compile(
    r"(?<![的與和及、，。；])([\u4e00-\u9fffA-Za-z0-9]{1,8}?)(?:股份有限公司|有限公司|公司|基金会|基金会|集團|集团|銀行|银行|事務所|事务所|診所|诊所|營造公司|建设公司|建設公司)")

# 公司名内的句子性成分（简体域）：命中则整条丢弃，不做人名/公司。
_COMPANY_BAD_SUBSTRINGS = (
    "提供", "显示", "涉及", "协助", "处理", "中间", "人员", "往来", "合作", "范围",
    "金额", "公开", "展示", "版本", "图片", "报告", "专案", "执行", "请款", "付款",
    "服务", "资料", "究竟", "附件", "本次", "此前", "尚未", "表示", "指出", "强调",
    "认为", "确认", "查证", "调查", "披露", "曝光", "流传", "转发", "转寄", "重建",
    "测试", "样本",
)
# 通用到无法作为核查对象的公司名（简体域）。
_GENERIC_COMPANIES = {"电信公司", "网络公司", "科技公司", "有限公司", "公司"}
_GLUE_SPLIT_RE = re.compile(
    r"[的與与和及在將将從从到把被給给讓让由往同跟對对為为是,，。、；：！？「」『』（）()\"' \n\t\-—…·|/\\]+"
)


def _clean_company_name(raw_match: str) -> str | None:
    """清洗公司候选：左缘胶水字截断 + 句子成分拦截 + 通用名拦截.

    例：央與南風整合行銷股份有限公司 -> 南風整合行銷股份有限公司；
    中間人協助處理公司（含協助/處理/中間）-> None；
    份資料究竟是電信公司 -> 電信公司（通用）-> None。
    """
    from ..preprocessing.normalization import to_simplified as _t2s

    text = (raw_match or "").strip().strip("，。；：！？、（）()「」『』\"'")
    if not text:
        return None
    # 以胶水字切分取最后一段（左缘定语/介词截断）
    parts = [p for p in _GLUE_SPLIT_RE.split(text) if p]
    cand = parts[-1] if parts else text
    cand = cand.strip().strip("，。；：！？、（）()「」『』\"'")
    cand = cand.lstrip("示顯显的此并並并且還还與与和及在將将提供等请請協协調调處处現现")
    if not cand or len(cand) > 30:
        return None
    simp = _t2s(cand)
    for _bad in _COMPANY_BAD_SUBSTRINGS:
        if _bad and _bad in simp:
            return None
    if simp in _GENERIC_COMPANIES:
        return None
    # 前缀过短的通用组合（如电信公司已在上拦截，此处兜底 2 字前缀+公司）
    if simp.endswith("电信公司") and len(simp) <= 6:
        return None
    return cand

GOV_RE = re.compile(r"(行政院|立法院|監察院|內政部|經濟部|法務部|國防部|教育部|衛福部|交通部|農委會|水利署|環保署|環境部|能源署|台北市政府|新北市政府|高雄市政府|臺北市政府|桃園市政府|臺中市政府|臺南市政府|[A-Z0-9]{1,3}縣政府|[A-Z0-9]{1,3}市政府|[A-Z0-9]{1,3}縣議會|[A-Z0-9]{1,3}市議會|議會|鄉公所|鎮公所|市公所)")
PARTY_RE = re.compile(r"(民主進步黨|民進黨|中國國民黨|國民黨|台灣民眾黨|民眾黨|時代力量|基進黨)")
ROLE_RE = re.compile(r"(立法委員|立委|議員|市長|縣長|局長|處長|主任|黨部主委|中執委|中常委|發言人|辦公室主任|服務處主任|黨工|董事長|總經理|副總|祕書|秘書|檢察官)")
PROJECT_RE = re.compile(r"(太陽光電|光電案場|綠能|儲能|漁電共生|都市更新|市地重劃|自辦重劃|區段徵收|聯合開發|促參案|都更案|標案|採購案|開發案|土地變更案)")


def extract_basic_entities(raw_text: str) -> List[EntityHit]:
    out: List[EntityHit] = []
    seen = set()
    raw = raw_text or ""
    normalized = Normalizer.normalize(raw)

    # 人名启发式前，先把金额/数字/公司/角色等片段占位，
    # 避免「万元顾」「司名和」「万元到」这类组合被当作姓名
    mask_spans = []
    for m in __import__("re").finditer(r"\d[\d,，.]*\s*(?:万亿|萬億|万|萬|亿|億)?[元块塊圓圆]?", normalized):
        mask_spans.append((m.start(), m.end()))
    for m in __import__("re").finditer(r"[零〇一二三四五六七八九十百千万亿點點两兩]+(?:万|萬|亿|億|元|块|塊|圓|圆)", normalized):
        mask_spans.append((m.start(), m.end()))
    for m in __import__("re").finditer(r"(公司|集团|集團|銀行|银行|基金会|基金会|協會|协会|事务所|机关|单位|部分|部门|群组|办公室|服务处)", normalized):
        mask_spans.append((m.start(), m.end()))
    person_scan = list(normalized)
    for s, e in mask_spans:
        for i in range(s, e):
            if i < len(person_scan):
                person_scan[i] = "\u0000"
    person_text = "".join(person_scan)

    def push(text: str, etype: str, count: int = 1):
        text = (text or "").strip().strip("，。；：！？、（）()「」『』\"'")
        if not text or len(text) > 60:
            return
        # 公司名候选清洗：左缘截断 + 句子成分/通用名拦截
        if etype == "COMPANY":
            cleaned = _clean_company_name(text)
            if not cleaned:
                return
            text = cleaned
        key = (text, etype)
        if key in seen:
            return
        seen.add(key)
        out.append(EntityHit(text=text, type=etype, count=count))

    cand_pos = extract_person_candidates(person_text)
    for cand, cnt in cand_pos.items():
        push(cand, "PERSON", cnt)

    for m in ORG_SUFFIX_RE.finditer(raw):
        push(m.group(0), "COMPANY")
    for m in GOV_RE.finditer(raw):
        push(m.group(0), "GOVERNMENT_AGENCY")
    for m in PARTY_RE.finditer(raw):
        push(m.group(0), "POLITICAL_PARTY")
    for m in ROLE_RE.finditer(raw):
        push(m.group(0), "ROLE")
    for m in PROJECT_RE.finditer(raw):
        push(m.group(0), "PROJECT")
    for d in extract_dates(raw):
        push(d, "DATE")
    for m in parse_amount(raw):
        push(m.raw, "MONEY")
    for a in extract_accounts(raw)[:5]:
        push(a, "BANK_ACCOUNT")
    return out
