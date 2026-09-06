"""Pattern 引擎：动态加载 P01-P20，组间 AND、组内 OR，四级上下文窗口共现匹配.

窗口优先级：sentence > paragraph > context3 > full。
语义组：
  target_person     - 实体识别出的人名或 C 类角色词
  amount_or_number  - 抽取的金额或阿拉伯数字
  X_result          - 反向结果词命中
  no_new_evidence   - 全文判定“无新增证据”
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from ..models import PatternHit
from ..preprocessing.normalization import to_simplified as _t2s
from .config_loader import RuleConfig
from .context_window import ContextSplitter
from .term_variants import KINSHIP_VARIANTS_SIMPLIFIED, OTHER_VARIANTS_SIMPLIFIED

# 变体词表（variant(simplified) -> canonical(simplified)），匹配时组内任一变体命中即算该词命中
_VARIANTS = {}
for _v, _c in {**KINSHIP_VARIANTS_SIMPLIFIED, **OTHER_VARIANTS_SIMPLIFIED}.items():
    _VARIANTS[_t2s(_v)] = _t2s(_c)
# canonical -> 其全部变体
_VARIANT_OF: Dict[str, List[str]] = {}
for _v, _c in _VARIANTS.items():
    _VARIANT_OF.setdefault(_c, []).append(_v)

logger = logging.getLogger(__name__)

# 姓氏表（繁简并容），供启发式人名识别
SURNAME_RE = re.compile(
    r"([王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤赖"  # 简体
    r"王李張劉陳楊黃趙吳周徐孫馬朱胡郭何高林羅鄭梁謝宋唐許韓馮鄧曹彭曾肖田董袁潘于蔣蔡余杜葉程蘇魏呂丁任沈姚盧姜崔鍾譚陸汪范金石廖賈夏韋付方白鄒孟熊秦邱江尹薛閻段雷侯龍史陶黎賀顧毛郝龔邵萬錢嚴覃武戴莫孔向湯賴"  # 繁体
    r"欧阳司马诸葛歐陽司馬諸葛][\u4e00-\u9fff]{1,2})"
)

DENY_PERSON_TERMS = {
    "立委", "議員", "议员", "市长", "市長", "县长", "縣長", "局长", "局長", "处长", "處長",
    "主任", "助理", "董事", "董事長", "董事长", "发言人", "發言人", "顾问", "顧問", "业者", "業者",
    "厂商", "廠商", "建商", "地主", "中间人", "中間人", "检察官", "檢察官", "法官",
    "里长", "里長", "鄰長", "邻长", "党工", "黨工", "幕僚", "桩脚", "樁腳", "白手套",
    "秘书", "祕書", "秘書", "科长", "科長", "组长", "組長", "事务官", "事務官",
    "政务官", "政務官", "市府", "县府", "縣府", "辦公室", "办公室", "老板", "老闆",
    "大哥", "阿姨", "姐姐", "哥哥", "某人", "主人",
    "主管", "經理", "经理", "書記", "书记", "委員", "委员", "黨部", "党部",
    "主委", "中委", "總經", "总经",
}

# 單姓集合（簡體域；輸入已繁轉簡，此集合用於位置感知的人名掃描）
_SINGLE_SURNAMES = set(
    "王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤赖"
)
_COMPOUND_SURNAMES = ("欧阳", "司马", "诸葛")

# 幾乎不可能出現在真實名字第二字的功能字（介詞/代詞/助詞/方位）。
# 向/和/與等雖也是姓氏，但在此位置多為介詞用法，寧可漏判也不誤抓。
# 另含電/分/測/結等絕少入名的字，用於攔截「方電」「于測」等二字回退誤抓。
_FUNCTION_SECOND_CHARS = set(
    "在向遭对从到把被给让由往同跟和与及或为是的之地得着过们我你他她它您这那哪"
    "上有后当主电分涉合测结规原经公曾已将要有最民"
)

# 常見非人名雙字以上片段（簡體域）：命中即非人名。
# 注意：刻意不收錄「英文/唐山/文馨」等，以免誤殺蔡英文、陳唐山、湯文馨等真名。
_NONNAME_SUBSTRINGS = (
    "原始", "历史", "测试", "样本", "重建", "报道", "邮件", "经济", "电讯", "电信",
    "任务", "程序", "方式", "范围", "金额", "成果", "公开", "合作", "分析", "资料",
    "文件", "图片", "合约", "合同", "讯息", "会议", "记录", "结果", "规则", "曾经",
    "流传", "匿名", "媒体", "选举", "操作", "格式", "用语", "编号", "问题", "来源",
    "服务器", "人证", "结论", "版本", "整理", "参考", "协助", "处理", "中间", "厂商",
    "建商", "党工", "主管", "董事", "经理", "书记", "委员", "党部", "中央", "政府",
    "部门", "单位", "机关", "专案", "报告", "提供", "显示", "涉及", "任何", "方向",
    "交付", "民进",
)

# 角色雙字：token 以此開頭，或姓氏緊貼此雙字（如「主+任許嘉」中的主任），皆非人名。
_ROLE_BIGRAMS = {
    "主任", "董事", "书记", "秘书", "助理", "议员", "立委", "委员",
    "主管", "经理", "主委", "中委", "发言", "总经",
}

NUMBER_RE = re.compile(r"\d[\d,，.]*\s*(?:万亿|萬億|万|萬|亿|億)?")


def is_role_or_verb(term: str) -> bool:
    return any(d in term for d in DENY_PERSON_TERMS)


def is_person_token(term: str, prev_char: str = "") -> bool:
    """启发式人名判断：2-4字、姓氏开头、非角色组合.

    prev_char: 姓氏前一字（用于识别「主任許嘉恬」中「任許嘉」这类
    紧贴角色词的误抓：主+任许嘉 -> 主任是角色，任许嘉非人名）。
    """
    if not (2 <= len(term) <= 4):
        return False
    if "\x00" in term:
        return False
    if re.search(r"[，。；：！？、（）\d\s]", term):
        return False
    if is_role_or_verb(term):
        return False
    # 常见非人名片段（测试样板/公文/财经常用词）
    for _bad in _NONNAME_SUBSTRINGS:
        if _bad and _bad in term:
            return False
    # 第二字为功能字（介词/代词/助词）几乎不可能是名字
    if len(term) >= 2 and term[1] in _FUNCTION_SECOND_CHARS:
        return False
    # token 以角色双字开头（如董事湯）非人名
    if len(term) >= 2 and term[:2] in _ROLE_BIGRAMS:
        return False
    # 姓氏紧贴角色/常用词双字（如主任中的任、历史中的史、交付中的付）非人名
    if prev_char:
        _prev_bigram = prev_char + term[0]
        if _prev_bigram in _ROLE_BIGRAMS or _prev_bigram in _NONNAME_SUBSTRINGS:
            return False
    if term.endswith(("公司", "基金會", "基金会", "協會", "协会", "集團", "集团", "銀行", "银行",
                      "事務所", "事务所", "診所", "诊所", "市府", "县府", "縣府")):
        return False
    if term[-1] in "费费费款额金额等":
        return False
    # 常见双字动词/名词/称谓紧邻，几乎不可能是人名
    if term[-2:] in {"保管", "处理", "协调", "人员", "主任", "助理", "帮助", "表示", "指出",
                     "强调", "声称", "否认", "承认", "负责", "主导", "主持", "说明", "透露",
                     "提供", "检举", "爆料", "检方", "法官", "书记", "干事", "党员", "干部",
                     "主管", "副手", "随从", "秘书", "秘书", "传讯", "訊息", "讯息", "告诉",
                     "联系", "联络", "陪同", "出席", "参与", "涉及", "收受", "支付", "汇入",
                     "交付", "领取", "交给", "收取", "称", "说", "担任", "当选", "服务"}:
        return False
    if term[-1] in "长员官司处所部局科会院组队队股":  # X长/X员/X官…
        return False
    return bool(SURNAME_RE.fullmatch(term))


def _is_cjk(ch: str) -> bool:
    return len(ch) == 1 and "\u4e00" <= ch <= "\u9fff"


def extract_person_candidates(text: str) -> Dict[str, int]:
    """位置感知的人名候选扫描（解决重叠吞字）.

    旧逻辑用 SURNAME_RE.finditer 非重叠匹配：在「主任許嘉恬」中，
    「任許嘉」先被吃掉，「許嘉恬」永遠掃不到；「董事湯文馨」中
    「董事湯」吃掉湯字，「湯文馨」丟失。

    新逻辑逐字扫描：每个姓氏起点优先取最长（3字>2字），通过
    is_person_token（含 prev_char 角色贴靠检查）才保留；同一位置
    只保留最长通过者，下一位置继续（允许重叠，如任位拒收后許位仍可收）。
    返回 {候选: 出现次数}。
    """
    out: Dict[str, int] = {}
    if not text:
        return out
    n = len(text)
    covered_until = -1  # 已接受长名的覆盖右界：中间字不再起新名（如陳唐山中的唐）
    for i in range(n):
        if i <= covered_until:
            continue  # 前一名中间字，跳过（防「唐山協」类尾巴误抓）
        base_len = 0
        if text.startswith(tuple(_COMPOUND_SURNAMES), i):
            # 复姓：欧阳/司马/诸葛 + 1-2 字
            base_len = 2
        elif text[i] in _SINGLE_SURNAMES:
            base_len = 1
        else:
            continue
        prev = text[i - 1] if i > 0 else ""
        # 优先长（单姓 3 字，复姓 4 字），再试短
        for tail in (2, 1):
            total = base_len + tail
            if total < 2 or total > 4:
                continue
            j = i + total
            if j > n:
                continue
            cand = text[i:j]
            if any(not _is_cjk(c) for c in cand):
                continue
            if "\x00" in cand:
                continue
            if is_person_token(cand, prev_char=prev):
                out[cand] = out.get(cand, 0) + 1
                covered_until = j - 1
                break  # 同一起点只保留最长通过者
    return out


class PatternEngine:
    """从 pattern_rules.yaml 动态加载并匹配，支持上下文窗口共现."""

    def __init__(self, config: RuleConfig, keyword_engine=None,
                 max_full_window_chars: int = 8000):
        self.config = config
        self.keyword_engine = keyword_engine
        self.rules: List[dict] = config.pattern_list()
        self.max_full_window_chars = max_full_window_chars
        # 预先把规则里的词做繁转简，供匹配；保留原词展示
        self._match_rules = []
        SEM_GROUPS = {"target_person", "amount_or_number", "X_result", "no_new_evidence"}
        for r in self.rules:
            specs = []
            for spec in r.get("required_groups") or []:
                if isinstance(spec, str):
                    specs.append(spec)
                elif isinstance(spec, list) and len(spec) == 1 and spec[0] in SEM_GROUPS:
                    specs.append(str(spec[0]))
                elif isinstance(spec, list):
                    # 组内可混合普通词与语义组(如 P10: [target_person, 地方人士])
                    sem_in_group = False
                    pairs = []
                    for t in spec:
                        if t in SEM_GROUPS:
                            pairs.append(("__SEM__", t))
                            sem_in_group = True
                            continue
                        orig = t
                        simp = _t2s(t) if not t.isascii() else t
                        pairs.append((orig, simp))
                        # 若词典词(简体)有口语变体，追加变体候选（匹配文本口语，展示仍用词典词）
                        for var in _VARIANT_OF.get(simp, []):
                            if var != simp:
                                pairs.append((orig, var))
                    specs.append(pairs)
                else:
                    specs.append(spec)
            rr = dict(r)
            rr["required_groups"] = specs
            self._match_rules.append(rr)

    @property
    def rule_count(self) -> int:
        return len(self.rules)

    # ------------------------------------------------------------------
    def match(self, normalized_text: str, orig_text: str,
              money: Optional[List] = None,
              x_terms: Optional[List[str]] = None,
              no_new_evidence: bool = False,
              hit_by_type: Optional[Dict[str, Set[str]]] = None,
              entities: Optional[List] = None,
              role_terms: Optional[List[str]] = None) -> List[PatternHit]:
        if not normalized_text:
            return []
        splitter = ContextSplitter(normalized_text).build()

        person_terms: List[str] = []
        for e in entities or []:
            t = e.get("text") if isinstance(e, dict) else getattr(e, "text", "")
            ty = e.get("type") if isinstance(e, dict) else getattr(e, "type", "")
            if ty == "PERSON" and is_person_token(t):
                person_terms.append(_t2s(t))

        role_terms = [_t2s(t) for t in (role_terms or []) if len(t) >= 2]
        hit_c = (hit_by_type or {}).get("C", set())
        for t in hit_c:
            t2 = _t2s(t)
            if len(t2) >= 2 and t2 not in role_terms:
                role_terms.append(t2)
        # 角色词过滤掉明显动词/非角色（简体域判断）
        role_terms = [t for t in role_terms if any(
            k in t for k in ("议员", "立委", "市长", "县长", "局长", "处长", "主任", "助理",
                             "董事长", "发言人", "顾问", "桩脚", "主委", "委员", "党", "长", "董",
                             "董事", "官员", "办公室"))]

        hits: List[PatternHit] = []
        for rule in self._match_rules:
            try:
                hit = self._match_rule(rule, splitter, person_terms, role_terms,
                                       money or [], x_terms or [], no_new_evidence,
                                       orig_text)
            except Exception as e:  # noqa: BLE001
                logger.warning("Pattern %s 匹配异常: %s", rule.get("id"), e)
                continue
            if hit is not None:
                hits.append(hit)
        return hits

    # ------------------------------------------------------------------
    def _match_rule(self, rule: dict, splitter: ContextSplitter,
                    person_terms: List[str], role_terms: List[str],
                    money: List, x_terms: List[str],
                    no_new_evidence: bool, orig_text: str = "") -> Optional[PatternHit]:
        pid = rule.get("id")
        specs = rule.get("required_groups") or []
        if not specs:
            return None

        # 语义组解析。target_person：优先用可靠人名；无可靠人名时允许角色词
        # （角色词本身常作为“议员/主任”等目标指代出现，能显著提高召回，噪声由降权规则处理）
        semantic_group_terms: Dict[str, List[str]] = {}
        person_terms = [t for t in person_terms if is_person_token(t)]

        candidates: List[List[tuple]] = []   # 每组 -> [(term, positions)]
        for spec in specs:
            opts: List[tuple] = []
            if isinstance(spec, str):
                sem = spec
                if sem == "target_person":
                    pool = list(dict.fromkeys(person_terms + role_terms))
                    for t in pool:
                        pos = splitter.find(t)
                        if pos:
                            opts.append((t, pos))
                elif sem == "amount_or_number":
                    for m in money:
                        raw = m.get("raw", "") if isinstance(m, dict) else getattr(m, "raw", "")
                        # raw 可能为繁/原文，简体文本需尝试转换
                        for cand in dict.fromkeys([raw, _t2s(raw)]):
                            pos = splitter.find(cand)
                            if pos:
                                opts.append((cand, pos))
                                break
                    for mm in NUMBER_RE.finditer(splitter.text):
                        raw = mm.group(0).replace(" ", "")
                        opts.append((raw, [(mm.start(), mm.end())]))
                elif sem == "X_result":
                    for t in x_terms:
                        t2 = _t2s(t)
                        pos = splitter.find(t2)
                        if pos:
                            opts.append((t, pos))
                elif sem == "no_new_evidence":
                    if no_new_evidence:
                        # 全文语义：处处命中
                        opts.append(("__NEWEV__", [(0, min(80, len(splitter.text)))]))
                else:
                    logger.warning("未知语义组 %s (pattern %s)", sem, pid)
            elif isinstance(spec, list):
                # 组内混合普通词与语义组（P10/P18: target_person+地方人士 同组 OR）
                expanded = []
                for pair in spec:
                    if isinstance(pair, tuple) and pair and pair[0] == "__SEM__":
                        sem_name = pair[1]
                        if sem_name == "target_person":
                            for t in person_terms + role_terms:
                                expanded.append((t, splitter.find(t)))
                        elif sem_name == "amount_or_number":
                            for m in money:
                                raw = m.get("raw", "") if isinstance(m, dict) else getattr(m, "raw", "")
                                for cand in dict.fromkeys([raw, _t2s(raw)]):
                                    pos = splitter.find(cand)
                                    if pos:
                                        expanded.append((cand, pos))
                                        break
                            for mm in NUMBER_RE.finditer(splitter.text):
                                raw = mm.group(0).replace(" ", "")
                                expanded.append((raw, [(mm.start(), mm.end())]))
                        elif sem_name == "X_result":
                            for t in x_terms:
                                pos = splitter.find(_t2s(t))
                                if pos:
                                    expanded.append((t, pos))
                        elif sem_name == "no_new_evidence" and no_new_evidence:
                            expanded.append(("__NEWEV__", [(0, min(80, len(splitter.text)))]))
                        continue
                    if isinstance(pair, tuple):
                        orig, simp = pair
                    else:
                        orig = simp = pair
                    pos = splitter.find(simp)
                    if pos:
                        expanded.append((orig, pos))
                # 去重（同名同位置）
                seen_opt = set()
                for term, positions in expanded:
                    if term != "__NEWEV__" and not positions:
                        continue
                    k = (term, tuple(p[0] for p in positions[:1]))
                    if k not in seen_opt:
                        seen_opt.add(k)
                        opts.append((term, positions))
            if not opts:
                return None
            candidates.append(opts)

        # 按窗口级别扫描
        matched: Optional[dict] = None
        level_order = [("sentence", splitter.sentences), ("paragraph", splitter.paragraphs),
                       ("context3", splitter.windows_ctx3)]
        for level, windows in level_order:
            if not windows:
                continue
            for (ws, we, _wt) in windows:
                chosen: List[str] = []
                ok = True
                for opts in candidates:
                    hit_term = None
                    for term, positions in opts:
                        if term == "__NEWEV__" or any(ws <= p0 < we for p0, _ in positions):
                            hit_term = term
                            break
                    if hit_term is None:
                        ok = False
                        break
                    chosen.append(hit_term)
                if ok:
                    matched = {"chosen": chosen, "level": level, "window": (ws, we)}
                    break
            if matched:
                break

        # 全文级兜底：仅短文本且组数>=3 的高结构化 pattern 允许
        if matched is None and len(splitter.text) <= self.max_full_window_chars and len(candidates) >= 3:
            chosen = []
            ok = True
            for opts in candidates:
                hit_term = None
                for term, positions in opts:
                    if term == "__NEWEV__" or positions:
                        hit_term = term
                        break
                if hit_term is None:
                    ok = False
                    break
                chosen.append(hit_term)
            if ok:
                matched = {"chosen": chosen, "level": "full", "window": (0, len(splitter.text))}

        if matched is None:
            return None

        chosen = matched["chosen"]
        base = float(rule.get("base_score") or 0)
        cap = rule.get("cap_score")
        # 证据摘录：找第一个普通词的原文片段
        snippet = ""
        anchor = ""
        for t in chosen:
            if t and t != "__NEWEV__":
                anchor = t
                break
        if anchor:
            pos = splitter.find(anchor)
            if pos:
                s = max(0, pos[0][0] - 30)
                e = min(len(splitter.text), pos[0][1] + 110)
                snippet = "…" + splitter.text[s:e].replace("\n", " ") + "…"
        if not snippet:
            snippet = (orig_text or splitter.text)[:120]

        # P20 特殊：cap_score 生效且已降权
        score = base
        if cap is not None:
            score = min(score, float(cap))

        terms = [t for t in chosen if t and t != "__NEWEV__"]
        return PatternHit(
            pattern_id=str(pid), category=str(rule.get("category") or "GLOBAL"),
            name=str(rule.get("name") or ""),
            matched_terms=terms,
            evidence_snippets=[snippet] if snippet else [],
            pattern_score=score,
            window=matched["level"],
            signal_terms=terms[:6],
        )
