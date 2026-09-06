"""Negative Rule 引擎：反向结果词 + 上下文排除 + N01-N08 降权."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from ..models import NegativeMatch, PatternHit
from ..preprocessing.normalization import to_simplified as _t2s
from .config_loader import RuleConfig
from .context_window import ContextSplitter

logger = logging.getLogger(__name__)

# N02 检测“仅标签无事实”：空泛指控词（简/繁双写，简体域匹配）
VAGUE_ACCUSATION = {
    "贪污", "貪汙", "贪腐", "弊案", "黑金", "性騷", "性騷擾", "性骚扰", "圖利", "图利",
    "包庇", "官商勾結", "官商勾结", "不法",
}
# N08/N05：旧闻转发信号
OLD_NEWS_SIGNALS = ["去年", "先前", "当时", "當年", "旧闻", "舊聞", "过去", "過去",
                    "早在", "曾報導", "曾报道", "當時報導", "當時报道"]
FACTS_SIGNALS = ["姓名", "日期", "金额", "金額", "公司", "帐户", "帳戶", "账号", "帳號",
                 "银行", "銀行", "line", "LINE", "Line", "汇款", "匯款", "录音", "錄音",
                 "录影", "錄影", "文件", "照片", "合约", "合約", "契约", "契約", "标书",
                 "標書", "签呈", "簽呈", "会议纪录", "會議紀錄", "监视器", "監視器", "截图",
                 "截圖", "时间", "時間", "地点", "地點", "名单", "名單", "纪录", "紀錄",
                 "申訴", "申诉", "檢舉", "检举", "反映", "對話", "对话"]
# 当事人否认/澄清（须是明确否认指控的表述，通用否定词不算）
DENIAL_SIGNALS = ["否认", "否認", "澄清", "声明", "聲明", "声称", "聲稱", "驳斥", "駁斥",
                  "澄清说", "澄清說", "否认收", "否認收", "否认涉", "否認涉", "否认与", "否認與",
                  "并无此事", "並無此事", "纯属虚构", "純屬虛構"]

# N03 场景词：合照/同桌/握手等
PHOTO_SCENE = ["合照", "合影", "同框", "同桌", "同场", "同場", "握手", "餐叙", "餐敘",
               "站台", "剪彩", "剪綵"]
ORG_RELATION_WORDS = ["资金", "資金", "汇款", "匯款", "投资", "投資", "股份", "合约", "合約",
                      "业务往来", "業務往來", "标案", "標案", "采购", "採購", "补助", "補助",
                      "政治献金", "政治獻金", "职位", "職位", "职务", "職務", "安排", "酬庸",
                      "安插", "员工", "員工"]


def context_exclude(term: str, context: str, exclude_if: List[str]) -> bool:
    """上下文排除：命中的词若紧邻排除词，则不计数."""
    idx = context.find(term)
    if idx < 0:
        return False
    win = context[max(0, idx - 15): idx + len(term) + 30]
    for ex in exclude_if:
        if ex in win:
            return True
    return False


class NegativeEngine:
    """反向/降权判定，返回命中及建议降权."""

    def __init__(self, config: RuleConfig):
        self.config = config
        self.x_terms = [t for t in config.x_terms() if len(t) >= 2]
        self.rules = config.negative_rules()
        self.exclusions = config.context_exclusions()

    def match(self, normalized_text: str, orig_text: str = "",
              hits_by_type: Optional[Dict[str, Set[str]]] = None,
              pattern_hits: Optional[List[PatternHit]] = None,
              no_new_evidence: bool = False) -> dict:
        """返回 {x_terms, ex_present, no_new_evidence, negatives:[NegativeMatch], exclusions_applied}"""
        res = {
            "x_terms": [],
            "ex_present": False,
            "no_new_evidence": no_new_evidence,
            "negatives": [],
            "exclusions_applied": [],
            "denial_present": False,
            "is_old_news": False,
        }
        if not normalized_text:
            return res
        splitter = ContextSplitter(normalized_text).build()

        # 1) X 反向词（规则为繁体，匹配简体文本需先繁转简）
        for t in self.x_terms:
            pos = splitter.find(t)
            if not pos and not t.isascii():
                pos = splitter.find(_t2s(t))
            if pos:
                res["x_terms"].append(t)
                res["ex_present"] = True
        res["x_terms"] = list(dict.fromkeys(res["x_terms"]))

        hits_by_type = hits_by_type or {}

        # 2) N 系列降权规则
        negatives: List[NegativeMatch] = []
        nid = {r.get("id"): r for r in self.rules}
        # N01: 仅攻击/情绪
        attack_words = ["垃圾", "畜生", "王八蛋", "下台", "滾", "無恥", "可恥", "丟臉", "太離譜", "天怒人怨"]
        has_political_target = bool(hits_by_type.get("C"))
        attack_hits = [w for w in attack_words if w in normalized_text]
        n01 = nid.get("N01")
        if n01 and len(attack_hits) >= 2 and not has_political_target:
            negatives.append(NegativeMatch(rule_id="N01", condition=n01.get("condition", ""),
                                           score_delta=float(n01.get("score_delta", 0)),
                                           max_score=float(n01["max_score"]) if n01.get("max_score") is not None else None,
                                           matched_terms=attack_hits[:5], snippets=[]))
        # N02: 仅标签词、无事实
        h_terms = [t for t in hits_by_type.get("H", set()) if t in VAGUE_ACCUSATION or t in
                   ["性騷", "貪污", "弊案", "黑金"]]
        has_fact = any(s in normalized_text for s in FACTS_SIGNALS)
        if nid.get("N02") and h_terms and not has_fact:
            negatives.append(NegativeMatch(rule_id="N02", condition=nid["N02"].get("condition", ""),
                                           score_delta=float(nid["N02"].get("score_delta", 0)),
                                           max_score=float(nid["N02"]["max_score"]) if nid["N02"].get("max_score") is not None else None,
                                           matched_terms=h_terms[:5], snippets=[]))
        # N03: 合照/同场无实质关系
        scene_hits = [w for w in PHOTO_SCENE if w in normalized_text]
        org_hits = [w for w in ORG_RELATION_WORDS if w in normalized_text]
        if nid.get("N03") and scene_hits and not org_hits:
            negatives.append(NegativeMatch(rule_id="N03", condition=nid["N03"].get("condition", ""),
                                           score_delta=float(nid["N03"].get("score_delta", 0)),
                                           max_score=float(nid["N03"]["max_score"]) if nid["N03"].get("max_score") is not None else None,
                                           matched_terms=scene_hits[:5], snippets=[]))
        # N04: 一般私德
        if nid.get("N04"):
            private_hits = [w for w in ["婚外情", "外遇", "不倫", "緋聞", "偷吃", "劈腿", "曖昧"] if w in normalized_text]
            public_signal = any(s in normalized_text for s in
                                ["公職", "職權", "權勢", "部屬", "辦公室", "助理", "議員", "立委",
                                 "違法", "金錢", "收受", "強迫", "偷拍", "申訴"])
            if private_hits and not public_signal:
                negatives.append(NegativeMatch(rule_id="N04", condition=nid["N04"].get("condition", ""),
                                               score_delta=float(nid["N04"].get("score_delta", 0)),
                                               max_score=float(nid["N04"]["max_score"]) if nid["N04"].get("max_score") is not None else None,
                                               matched_terms=private_hits[:5], snippets=[]))
        # N05: EX 结果 + 无新增证据（P20 由 pattern 体系处理，此处也独立判）
        if nid.get("N05") and res["ex_present"] and no_new_evidence:
            negatives.append(NegativeMatch(rule_id="N05", condition=nid["N05"].get("condition", ""),
                                           score_delta=float(nid["N05"].get("score_delta", 0)),
                                           max_score=float(nid["N05"]["max_score"]) if nid["N05"].get("max_score") is not None else None,
                                           matched_terms=res["x_terms"][:5], snippets=[]))
        # N06: 否认但存在起诉/调查：仅记录，不降权
        denial_present = any(w in normalized_text for w in DENIAL_SIGNALS)
        res["denial_present"] = denial_present
        if nid.get("N06") and denial_present:
            formal_signal = any(s in normalized_text for s in
                                ["起訴", "起诉", "搜索", "約談", "羁押", "羈押", "列被告", "判決", "判决", "調查", "调查"])
            if formal_signal:
                negatives.append(NegativeMatch(rule_id="N06", condition=nid["N06"].get("condition", ""),
                                               score_delta=0.0, max_score=None,
                                               matched_terms=[w for w in DENIAL_SIGNALS if w in normalized_text][:5],
                                               snippets=[]))
        # N07: 敏感词但对象非目标政治人物（近似：无角色词/无政党词上下文且敏感词在私人领域）
        if nid.get("N07"):
            # normalized_text 为简体域；简体词表判定
            target_markers = ["民进党", "民进党", "民主进步党", "国民党", "立委", "议员", "市长", "县长",
                              "主委", "局长", "处长", "议长", "镇长", "乡长", "代表会", "民代",
                              "政治人物", "候选人", "党籍", "委员", "党部"]
            has_target_marker = any(m in normalized_text for m in target_markers)
            has_high = bool(hits_by_type.get("H"))
            high_h = [t for t in hits_by_type.get("H", set()) if t in VAGUE_ACCUSATION]
            c_terms = [t for t in hits_by_type.get("C", set()) if
                       t not in ("政府采购", "采购", "招标", "开标", "决标", "验收", "标案",
                                 "申请", "核准", "审查", "发照", "许可", "招标公告")]
            only_procurement_terms = bool(hits_by_type.get("C")) and not c_terms
            # 情况A：空泛指控但无任何政治目标标识且无其它C词上下文
            if high_h and not has_target_marker and not c_terms:
                negatives.append(NegativeMatch(rule_id="N07", condition=nid["N07"].get("condition", ""),
                                               score_delta=float(nid["N07"].get("score_delta", 0)),
                                               max_score=float(nid["N07"]["max_score"]) if nid["N07"].get("max_score") is not None else None,
                                               matched_terms=high_h[:5], snippets=[]))
            # 情况B：只有正常行政程序词(采购/招标/决标/验收/许可)且无H/无pattern -> 依法办理的正常流程
            if only_procurement_terms and not has_high and not pattern_hits:
                normal_words = ["依法", "依规", "依政府采购法", "公告", "公开", "正常", "完成", "决标", "程序"]
                normal_hits = [w for w in normal_words if w in normalized_text]
                if normal_hits:
                    negatives.append(NegativeMatch(rule_id="N07", condition=nid["N07"].get("condition", "") + "（正常行政程序语境）",
                                                   score_delta=float(nid["N07"].get("score_delta", 0)),
                                                   max_score=float(nid["N07"]["max_score"]) if nid["N07"].get("max_score") is not None else None,
                                                   matched_terms=normal_hits[:5], snippets=[]))
        # N08: 历史旧闻重复转发、无新证据
        old_hits = [w for w in OLD_NEWS_SIGNALS if w in normalized_text]
        has_news_meta = any(w in normalized_text for w in ["報導", "报道", "新聞", "新闻", "轉載", "转载", "記者", "新闻"])
        is_old = bool(old_hits) and no_new_evidence and has_news_meta
        res["is_old_news"] = is_old
        if nid.get("N08") and is_old:
            negatives.append(NegativeMatch(rule_id="N08", condition=nid["N08"].get("condition", ""),
                                           score_delta=float(nid["N08"].get("score_delta", 0)),
                                           max_score=float(nid["N08"]["max_score"]) if nid["N08"].get("max_score") is not None else None,
                                           matched_terms=old_hits[:5], snippets=[]))

        # N01 扩展：营销/订阅/时事通讯内容降权
        # 特征：退订/订阅/分享/转寄/电子报/Newsletter/广告促销 等邮件模板词 + 无目标政治人物
        # 这类邮件里的 LINE/Email/奖金金额只是分享按钮与促销话术，不构成爆料证据
        if nid.get("N01"):
            promo_terms = ["退订", "退訂", "unsubscribe", "订阅", "訂閱", "subscribe", "分享给朋友",
                           "轉寄給朋友", "转发给朋友", "转发邮件", "email to a friend", "share this",
                           "newsletter", "电子报", "電子報", "广告", "廣告", "促销", "促銷", "early bird",
                           "早鸟", "早鳥", "discount", "折扣", "优惠", "優惠", "invitation", "邀请",
                           "邀請", "注册", "註冊", "register", "click here", "点此", "點此",
                           "view in browser", "在浏览器中查看", "submit your website", "promote your",
                           "rss feeds", "latest posts", "you're following", "configure your",
                           "instantly", "receive this email", "不再收到", "停止接收", "manage preferences",
                           "阅读全文", "read more", "view this email", "这是广告邮件"]
            promo_hits = [w for w in promo_terms if w.lower() in normalized_text]
            target_markers = ["民进党", "国民党", "议员", "市长", "县长", "立委", "主委", "政治人物",
                              "候选人", "党部", "官员", "局长", "处长", "市府", "县府", "公职",
                              "检举", "檢舉", "爆料", "申诉", "申訴", "内线", "內線", "吹哨"]
            has_target = any(m in normalized_text for m in target_markers)
            # 真 H 词（排除"截图/提供/下载/挪用"等弱证据动作词——营销文案中也常见）
            h_set = {t for t in hits_by_type.get("H", set())}
            weak_h = {"截图", "截圖", "提供", "下載", "下载", "拍攝", "拍摄", "複製", "复制",
                      "挪用", "取得", "安排", "蒐集", "搜集", "拍摄"}
            has_real_h = bool(h_set - weak_h)
            # 营销特征 >=3 -> 无条件降权(邮件模板特征极强)
            # 营销特征 >=2 且无目标政治人物 且 无真 H 词 -> 降权
            if (len(promo_hits) >= 3) or (len(promo_hits) >= 2 and not has_target and not has_real_h):
                n01r = nid.get("N01")
                negatives.append(NegativeMatch(
                    rule_id="N01", condition=n01r.get("condition", "") + "（营销/订阅内容特征）",
                    score_delta=float(n01r.get("score_delta", 0)),
                    max_score=float(n01r["max_score"]) if n01r.get("max_score") is not None else None,
                    matched_terms=promo_hits[:6], snippets=[]))
                res["is_promo"] = True

        res["negatives"] = negatives
        # 3) 上下文排除：作用于关键词命中（E 类“摸/抱/亲”等歧义）；全表在简体域比较
        # 附加工程排除：A17 泛动作词(下载/复制/上传/提供)在软件/客服/教程语境不算泄密
        exclusions = []
        eng_exclusions = [
            {"term": "下载", "exclude_if": ["客户端", "软件", "软件", "app", "安装包", "更新", "教程", "版本"]},
            {"term": "複製", "exclude_if": ["教程", "客户端", "软件", "软件", "安装"]},
            {"term": "复制", "exclude_if": ["教程", "客户端", "软件", "软件", "安装"]},
            {"term": "上傳", "exclude_if": ["教程", "客户端", "软件", "软件", "安装"]},
            {"term": "上传", "exclude_if": ["教程", "客户端", "软件", "软件", "安装"]},
            {"term": "提供", "exclude_if": ["客服", "教程", "帮助", "服务", "客户端"]},
        ]
        for ex in list(self.exclusions) + eng_exclusions:
            term = ex.get("term", "")
            if len(term) < 2:
                continue
            term_s = _t2s(term)
            if term_s not in normalized_text:
                continue
            idx = normalized_text.find(term_s)
            win = normalized_text[max(0, idx - 25): idx + len(term_s) + 45]
            for excl in ex.get("exclude_if", []):
                if _t2s(excl) in win:
                    exclusions.append(term)
                    break
        res["exclusions_applied"] = exclusions
        return res

    def compute_negative_delta(self, result: dict, rule_score: float) -> tuple:
        """汇总 negative 降权：返回 (总delta, 生效上限 min(总score,max_score 集合))"""
        delta = 0.0
        caps = []
        for n in result.get("negatives", []):
            if isinstance(n, dict):
                sd = n.get("score_delta")
                mx = n.get("max_score")
            else:
                sd = getattr(n, "score_delta", 0)
                mx = getattr(n, "max_score", None)
            if sd:
                delta += float(sd)
            if mx is not None:
                caps.append(float(mx))
        return delta, (min(caps) if caps else None)
