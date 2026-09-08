"""ReleaseDecisionFeatures 构建器：从 V1 筛选结果（RuleResult + LLMResult + FinalScore）
抽取证据形态与渠道决策特征。

设计：
- 证据形态(evidence_shapes)通过「规范化文本 + E 类词典词 + 附件类型 + LLM evidence_items」
  四路信号判定，允许一个线索多形态；
- 只做「该形态存在/可核」的召回式判定，真伪核验交给 verification_before_release；
- 敏感/匿名/灭证等特征以词表方式集中管理，代码不含新闻语义硬编码之外的散词。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set

from ..models import EmailDocument, FinalScore, LLMResult, RuleResult
from ..preprocessing.normalization import to_simplified
from ..rules.config_loader import RuleConfig
from .models import ReleaseDecisionFeatures

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 证据形态 -> 触发词（简体域比对，词典词经 to_simplified 归一）
# ---------------------------------------------------------------------------
# CHAT_RECORD：聊天/对话类原始记录
CHAT_TERMS = ["line", "微信", "messenger", "telegram", "signal", "簡訊", "简讯",
              "聊天記錄", "聊天记录", "對話紀錄", "对话记录", "群組", "群组", "截圖", "截图"]
# FIRST_PERSON_TESTIMONY：第一人称受害/亲历（在叙述中常与申述/受害词组合，LLM 侧再确认）
FIRST_PERSON_TERMS = ["我遭到", "我遭", "我被", "我受到", "他對我", "对我性騷扰", "对我性骚扰",
                      "我向", "我的主管", "我申訴", "我申诉", "我舉報", "我举报", "我檢舉", "我检举",
                      "我親眼", "我亲眼", "我本人", "我自己", "受害"]
# 冒用他人截图转发的第一人称反而不可信——只作弱信号，最后以 LLM witness_role 修正
CHAT_SHAPE_HINTS = ["line", "微信", "telegram", "signal", "messenger", "聊天", "对话", "群组"]
# AUDIO / VIDEO / PHOTO
AUDIO_TERMS = ["錄音", "录音", "語音", "语音"]
VIDEO_TERMS = ["錄影", "录像", "影片", "视频", "監視器", "监视器", "行車記錄器", "行车记录器"]
PHOTO_TERMS = ["照片", "相片", "翻拍"]
# BANK_RECORD
BANK_TERMS = ["銀行流水", "银行流水", "匯款單", "汇款单", "匯款紀錄", "汇款记录", "匯款", "汇款",
              "帳戶明細", "账户明细", "存摺", "存折", "提款紀錄", "提款记录", "支票", "帳冊", "账册",
              "收據", "收据", "發票", "发票", "轉帳", "转账"]
# CONTRACT
CONTRACT_TERMS = ["契約", "契约", "合約", "合约", "合同", "租賃契約", "租赁契约"]
# INTERNAL_DOCUMENT
INTERNAL_TERMS = ["內部公文", "内部公文", "簽呈", "签呈", "簽核", "签核", "會議紀錄", "会议记录",
                  "內部文件", "内部文件", "密件", "會計帳", "会计账"]
# OFFICIAL_DOCUMENT
OFFICIAL_TERMS = ["公文", "公告", "處分書", "处分书", "判決書", "判决书", "起訴書", "起诉书",
                  "不起訴處分", "不起诉处分", "官方", "政府", "申報資料", "申报资料", "函", "備查", "备案"]
# PROCUREMENT_FILE
PROCUREMENT_TERMS = ["標書", "标书", "標單", "标单", "報價單", "报价单", "採購文件", "采购文件",
                     "需求書", "需求书", "規格書", "规格书", "招標", "招标", "底價", "底价",
                     "得標", "得标", "決標", "决标", "開標", "开标"]
# SPREADSHEET
SPREADSHEET_TERMS = ["excel", "試算表", "试算表", "表格", "明細表", "明细表", "名冊", "名册",
                     "清冊", "清册", "報表", "报表"]
# DATABASE_RECORD
DATABASE_TERMS = ["資料庫", "数据库", "匯出資料", "导出数据", "後台紀錄", "后台记录", "系統紀錄", "系统记录"]
# ACADEMIC_DOCUMENT
ACADEMIC_TERMS = ["論文", "论文", "碩士", "硕士", "博士", "學位", "学位", "比對報告", "比对报告",
                  "turnitin", "抄襲", "抄袭", "學倫", "学伦"]
# LOCATION_DATA
LOCATION_TERMS = ["定位", "gps", "軌跡", "轨迹", "足跡", "足迹", "行蹤", "行踪", "基地台"]
# CLASSIFIED_DOCUMENT
CLASSIFIED_TERMS = ["密件", "機密", "机密", "極機密", "极机密", "軍事", "军事", "國安", "国安",
                    "外交密電", "外交密电", "國家機密", "国家机密"]
# ANONYMOUS_DOCUMENT
ANONYMOUS_TERMS = ["匿名", "無法確認來源", "无法确认来源", "來路不明", "来路不明", "不明人士提供",
                   "神秘來源", "神秘来源", "沒有metadata", "无metadata", "不敢具名", "不具名",
                   "來源不明", "来源不明"]

# ---------------------------------------------------------------------------
# 决策特征词表（简体域）
# ---------------------------------------------------------------------------
MONEY_FLOW_TERMS = BANK_TERMS
ORGANIZED_CRIME_TERMS = ["黑道", "角頭", "角头", "幫派", "帮派", "組織犯罪", "组织犯罪",
                         "犯罪集團", "犯罪集团", "詐騙集團", "诈骗集团", "洗錢", "洗钱",
                         "毒品", "販毒", "贩毒", "詐騙機房", "诈骗机房", "人頭帳戶", "人头账户"]
POWER_ACTION_TERMS = ["關說", "关说", "施壓", "施压", "協調", "协调", "護航", "护航", "放水",
                      "要求核准", "協助發照", "协助发照", "幫忙", "帮忙", "安排", "刪改", "删改",
                      "要求通過", "要求通过", "施壓稽查", "施压稽查"]
DESTRUCTION_TERMS = ["刪除訊息", "删除信息", "刪除記錄", "删除记录", "刪聊天", "删聊天",
                     "正在刪", "正在删", "毀滅證據", "毁灭证据", "滅證", "灭证", "銷毀文件",
                     "销毁文件", "刪除對話", "删除对话", "刪除資料", "删除资料"]
RETALIATION_TERMS = ["報復", "报复", "恐嚇", "恐吓", "威脅", "威胁", "串供", "串證", "串证",
                     "打壓", "打压", "封口", "調職", "调职", "解僱", "解雇", "逼退", "抹黑"]
ANONYMITY_TERMS = ["要求匿名", "希望匿名", "不願具名", "不愿具名", "保護身分", "保护身份",
                   "不要公開我的", "不要透露來源", "不要透露来源", "請保護", "请保护",
                   "不方便具名", "化名", "保護爆料人", "保护爆料人", "不願意具名",
                   "保護被害人", "保护被害人", "要求保護", "要求保护", "不想曝光",
                   "不希望報導中出現", "不露臉", "不露脸", "保護身分不願具名",
                   "不願具名但同意", "不願曝光"]
FIRST_PERSON_PROMPT_TERMS = ["我", "我們", "我们"]
SENSITIVE_PERSONAL_TERMS = ["裸照", "性侵細節", "性侵细节", "未成年", "兒少", "儿少", "身分證",
                            "身份证", "病歷", "病历", "診斷", "诊断", "健檢", "健检", "性騷細節",
                            "性骚细节"]
SEX_HARASS_TERMS = ["性騷", "性骚", "性騷擾", "性骚扰", "權勢性騷", "权势性骚", "性平", "性侵"]
BULLY_TERMS = ["霸凌", "職場霸凌", "职场霸凌", "壓榨", "剝削", "剥削"]
OLD_CASE_TERMS = ["去年", "先前", "当时", "当年", "曾报道", "曾報導", "旧闻", "舊聞", "过去",
                  "舊案", "旧案", "轉貼", "转贴", "转发", "轉發", "轉載", "转载"]
NO_NEW_INFO_TERMS = ["无新增", "沒有新證據", "没有新证据", "旧闻转发", "旧闻转贴"]
COMPLEX_RELATION_TERMS = ["關係企業", "关系企业", "交叉持股", "多公司", "多人頭", "多人头",
                          "資金網絡", "资金网络", "層層轉匯", "层层转汇", "股權結構", "股权结构",
                          "分潤", "分润", "層層轉包", "层层转包", "空殼公司", "空壳公司",
                          "五家公司", "多家公司", "多筆資金", "多笔资金", "多個地主", "多个地主",
                          "多個地塊", "多个地块", "多名人物", "多公司多人物"]
# 多地块/多公司/多项目：通过实体与金额计数判断（见 builder 逻辑）
MIN_COMPLEX_COMPANIES = 2
MIN_COMPLEX_AMOUNTS = 2
MIN_COMPLEX_PROJECTS = 2

# 附件扩展名 -> evidence shape（与 parser 的 file_type 命名一致）
ATTACH_TYPE_SHAPE = {
    "xlsx": "SPREADSHEET", "xls": "SPREADSHEET", "csv": "SPREADSHEET",
    "pdf": None,  # pdf 文本内容再判
    "docx": None, "doc": None, "txt": None, "md": None,
    "jpg": "PHOTO", "jpeg": "PHOTO", "png": "PHOTO", "gif": "PHOTO", "webp": "PHOTO",
    "eml": None, "unknown": None,
}

# 文件/截图像证据词 -> 具体 shape（E 类词命中后再细化）
SHAPE_BY_KEYWORD: Dict[str, Set[str]] = {
    "银行流水": {"BANK_RECORD"}, "匯款紀錄": {"BANK_RECORD"}, "汇款记录": {"BANK_RECORD"},
    "汇款单": {"BANK_RECORD"}, "匯款單": {"BANK_RECORD"}, "账户明细": {"BANK_RECORD"},
    "帳戶明細": {"BANK_RECORD"}, "存摺": {"BANK_RECORD"}, "存折": {"BANK_RECORD"},
    "提款紀錄": {"BANK_RECORD"}, "提款记录": {"BANK_RECORD"}, "帐册": {"BANK_RECORD", "SPREADSHEET"},
    "帳冊": {"BANK_RECORD", "SPREADSHEET"}, "收据": {"BANK_RECORD"}, "收據": {"BANK_RECORD"},
    "發票": {"BANK_RECORD"}, "发票": {"BANK_RECORD"},
    "契約": {"CONTRACT"}, "契约": {"CONTRACT"}, "合約": {"CONTRACT"}, "合约": {"CONTRACT"},
    "合同": {"CONTRACT"}, "租赁契约": {"CONTRACT"},
    "簽呈": {"INTERNAL_DOCUMENT"}, "签呈": {"INTERNAL_DOCUMENT"},
    "簽核": {"INTERNAL_DOCUMENT"}, "签核": {"INTERNAL_DOCUMENT"},
    "會議紀錄": {"INTERNAL_DOCUMENT"}, "会议记录": {"INTERNAL_DOCUMENT"},
    "內部文件": {"INTERNAL_DOCUMENT"}, "内部文件": {"INTERNAL_DOCUMENT"},
    "內部公文": {"INTERNAL_DOCUMENT"}, "内部公文": {"INTERNAL_DOCUMENT"},
    "密件": {"CLASSIFIED_DOCUMENT", "INTERNAL_DOCUMENT"},
    "標書": {"PROCUREMENT_FILE"}, "标书": {"PROCUREMENT_FILE"},
    "標單": {"PROCUREMENT_FILE"}, "标单": {"PROCUREMENT_FILE"},
    "報價單": {"PROCUREMENT_FILE"}, "报价单": {"PROCUREMENT_FILE"},
    "採購文件": {"PROCUREMENT_FILE"}, "采购文件": {"PROCUREMENT_FILE"},
    "需求書": {"PROCUREMENT_FILE"}, "需求书": {"PROCUREMENT_FILE"},
    "規格書": {"PROCUREMENT_FILE"}, "规格书": {"PROCUREMENT_FILE"},
    "比對報告": {"ACADEMIC_DOCUMENT"}, "比对报告": {"ACADEMIC_DOCUMENT"},
    "論文": {"ACADEMIC_DOCUMENT"}, "论文": {"ACADEMIC_DOCUMENT"},
    "turnitin": {"ACADEMIC_DOCUMENT"},
    "政治獻金收據": {"OFFICIAL_DOCUMENT", "BANK_RECORD"},
    "政治献金收据": {"OFFICIAL_DOCUMENT", "BANK_RECORD"},
    "申報表": {"OFFICIAL_DOCUMENT"}, "申报表": {"OFFICIAL_DOCUMENT"},
    "申報資料": {"OFFICIAL_DOCUMENT"}, "申报资料": {"OFFICIAL_DOCUMENT"},
    "得標公告": {"OFFICIAL_DOCUMENT"}, "得标公告": {"OFFICIAL_DOCUMENT"},
    "判決書": {"OFFICIAL_DOCUMENT"}, "判决书": {"OFFICIAL_DOCUMENT"},
    "起訴書": {"OFFICIAL_DOCUMENT"}, "起诉书": {"OFFICIAL_DOCUMENT"},
}


def _has_any(text: str, terms: List[str]) -> bool:
    return any(t in text for t in terms)


def _hits(text: str, terms: List[str]) -> List[str]:
    return [t for t in terms if t in text]


def _texts(doc: Optional[EmailDocument], rule: Optional[RuleResult]) -> List[str]:
    """候选文本源：规范化全文 + E 类词典词（词面本身可触发形态）。"""
    out: List[str] = []
    if rule is not None and rule.normalized:
        out.append(rule.normalized)
    if doc is not None and doc.combined_text:
        out.append(to_simplified(doc.combined_text))
    for h in (rule.matched_keywords.get("E", []) if rule else []):
        out.append(to_simplified(h.term))
    return out


def _entity_kind_counts(rule: Optional[RuleResult]) -> Dict[str, int]:
    counts = {"PERSON": 0, "COMPANY": 0, "PROJECT": 0, "LOCATION": 0, "DATE": 0}
    for e in (rule.entities if rule else []):
        if isinstance(e, dict):
            t, c = e.get("type"), int(e.get("count") or 1)
        else:
            t, c = e.type, int(getattr(e, "count", 1) or 1)
        if t in counts:
            counts[t] += c
    return counts


class ReleaseFeatureBuilder:
    """把 ScreeningRecord 的相关部分转成 ReleaseDecisionFeatures."""

    def __init__(self, rule_config: Optional[RuleConfig] = None,
                 release_rules: Optional[dict] = None):
        self.rule_config = rule_config
        self.release_rules = release_rules or {}

    # ------------------------------------------------------------------
    def build(self, email_id: str, rule: Optional[RuleResult],
              llm: Optional[LLMResult], score: Optional[FinalScore],
              doc: Optional[EmailDocument] = None,
              governance_categories: Optional[List[str]] = None,
              unified: Any = None, **kwargs) -> ReleaseDecisionFeatures:
        if governance_categories is None:
            governance_categories = kwargs.get("governance_categories")
        if unified is None:
            unified = kwargs.get("unified") or kwargs.get("unified_signals")
        if governance_categories is None and kwargs.get("governance") is not None:
            governance_categories = list(getattr(kwargs.get("governance"), "categories", []) or [])
        political_cats = []
        if llm is not None and llm.categories:
            political_cats = list(llm.categories)
        elif rule is not None:
            political_cats = list(rule.matched_categories)
        gov_cats = list(governance_categories or [])
        if not gov_cats and unified is not None:
            gov_cats = list(getattr(unified, "governance_categories", []) or [])
        cats = []
        for c in political_cats + gov_cats:
            if c and c not in cats:
                cats.append(c)
        stage = (llm.evidence_stage if llm and llm.evidence_stage else
                 (score.evidence_stage if score else "E1") or "E1")
        # LLM 自报优先级不用于决策；final_score/priority 为准
        feats = ReleaseDecisionFeatures(
            email_id=email_id,
            priority_level=(score.priority if score else "B") or "B",
            final_score=float(score.final_score if score else 0.0),
            categories=cats,
            political_categories=political_cats,
            governance_categories=gov_cats,
            evidence_stage=stage,
            known_old_case=False,
            contains_new_information=True,
        )
        texts = _texts(doc, rule)
        blob = " ".join(texts)
        shapes = self._evidence_shapes(texts, blob, rule, doc, llm)
        feats.evidence_shapes = shapes

        # 基础事实特征
        feats.has_original_evidence = bool(
            (rule and rule.matched_keywords.get("E")) or shapes or doc.attachments)
        feats.has_money_flow = bool(_has_any(blob, MONEY_FLOW_TERMS) or rule.money)
        feats.has_power_action = bool(_has_any(blob, POWER_ACTION_TERMS) or
                                      (llm and llm.power_actions))
        feats.has_first_person_testimony = self._first_person(blob, rule, llm, shapes)
        feats.has_classified_material = bool(
            _has_any(blob, CLASSIFIED_TERMS) or "CLASSIFIED_DOCUMENT" in shapes or
            any(c == "A17" for c in cats))
        feats.has_anonymous_documents = bool(
            _has_any(blob, ANONYMOUS_TERMS) or "ANONYMOUS_DOCUMENT" in shapes)
        feats.has_sensitive_personal_data = bool(
            _has_any(blob, SENSITIVE_PERSONAL_TERMS))
        feats.has_organized_crime_signal = bool(_has_any(blob, ORGANIZED_CRIME_TERMS))
        feats.destruction_risk = bool(_has_any(blob, DESTRUCTION_TERMS))
        feats.retaliation_risk = bool(_has_any(blob, RETALIATION_TERMS))
        # 吃案/压案/封口类（A12）本身即压制司法信号：对爆料人/程序构成威胁
        if any(c in cats for c in ("A12",)):
            feats.retaliation_risk = True
        feats.source_requests_anonymity = self._anonymity(blob, rule, llm)
        # 第一人称性骚/职场场景也构成身份保护需求（即使未明说匿名，安全默认）
        if any(c in cats for c in ("A11", "A12")) and (
                feats.has_first_person_testimony or "FIRST_PERSON_TESTIMONY" in shapes):
            feats.source_requests_anonymity = feats.source_requests_anonymity or \
                not self._explicitly_public(blob)
        feats.public_interest_established = bool(gov_cats) or self._public_interest(cats, blob, llm)
        feats.visually_clear = self._visually_clear(shapes, blob)
        feats.quickly_verifiable = self._quickly_verifiable(shapes, cats, rule)
        feats.requires_complex_explanation = self._complex_explanation(
            blob, rule, llm, shapes)
        feats.known_old_case, feats.contains_new_information = self._old_case(
            rule, llm, blob, cats)
        feats.sensitive_material = bool(
            feats.has_classified_material or feats.has_anonymous_documents or
            feats.has_sensitive_personal_data or "CLASSIFIED_DOCUMENT" in shapes or
            "ANONYMOUS_DOCUMENT" in shapes)
        feats.source_identification = "未表明"
        feats.witness_role = self._witness_role(rule, llm)
        feats.anonymity_capability = "full" if not feats.source_requests_anonymity else "partial"
        return feats

    # ------------------------------------------------------------------
    def _evidence_shapes(self, texts: List[str], blob: str,
                         rule: Optional[RuleResult], doc: Optional[EmailDocument],
                         llm: Optional[LLMResult]) -> List[str]:
        shapes: Set[str] = set()

        def add_by_keywords(term_list: List[str], shape: str):
            if _has_any(blob, term_list):
                shapes.add(shape)

        add_by_keywords(CHAT_TERMS, "CHAT_RECORD")
        # 第一人称叙述在 shape 层面：正文「我申訴/我遭到/受害」与性骚词共现
        add_by_keywords(FIRST_PERSON_TERMS, "FIRST_PERSON_TESTIMONY")
        add_by_keywords(AUDIO_TERMS, "AUDIO")
        add_by_keywords(VIDEO_TERMS, "VIDEO")
        add_by_keywords(PHOTO_TERMS, "PHOTO")
        add_by_keywords(BANK_TERMS, "BANK_RECORD")
        add_by_keywords(CONTRACT_TERMS, "CONTRACT")
        add_by_keywords(INTERNAL_TERMS, "INTERNAL_DOCUMENT")
        add_by_keywords(OFFICIAL_TERMS, "OFFICIAL_DOCUMENT")
        add_by_keywords(PROCUREMENT_TERMS, "PROCUREMENT_FILE")
        add_by_keywords(SPREADSHEET_TERMS, "SPREADSHEET")
        add_by_keywords(DATABASE_TERMS, "DATABASE_RECORD")
        add_by_keywords(ACADEMIC_TERMS, "ACADEMIC_DOCUMENT")
        add_by_keywords(LOCATION_TERMS, "LOCATION_DATA")
        add_by_keywords(CLASSIFIED_TERMS, "CLASSIFIED_DOCUMENT")
        add_by_keywords(ANONYMOUS_TERMS, "ANONYMOUS_DOCUMENT")

        # E 类词典词 -> 具体文件形态（细粒度映射，如 银行流水->BANK_RECORD）
        if rule is not None:
            for h in rule.matched_keywords.get("E", []):
                term = to_simplified(h.term)
                for key, target in SHAPE_BY_KEYWORD.items():
                    if key in term or term in key:
                        shapes.update(target)
        # LLM evidence_items 补充（如「匯款單」「存摺影本」已在词典覆盖，兜底泛化）
        if llm is not None:
            for item in (llm.evidence_items or [])[:20]:
                t = to_simplified(str(item))
                if any(k in t for k in ("line", "微信", "聊天", "对话", "簡訊", "简讯")):
                    shapes.add("CHAT_RECORD")
                elif any(k in t for k in ("錄音", "录音")):
                    shapes.add("AUDIO")
                elif any(k in t for k in ("錄影", "录像", "影片", "视频")):
                    shapes.add("VIDEO")
                elif any(k in t for k in ("照片", "相片", "翻拍")):
                    shapes.add("PHOTO")
                elif any(k in t for k in ("流水", "匯款", "汇款", "帳戶", "账户", "存摺", "存折",
                                          "帳冊", "账册", "收據", "收据", "發票", "发票")):
                    shapes.add("BANK_RECORD")
                elif any(k in t for k in ("標", "标", "需求書", "需求书", "報價", "报价", "採購", "采购")):
                    shapes.add("PROCUREMENT_FILE")
                elif any(k in t for k in ("論文", "论文", "比對", "比对", "turnitin")):
                    shapes.add("ACADEMIC_DOCUMENT")
                elif any(k in t for k in ("密件", "機密", "机密")):
                    shapes.add("CLASSIFIED_DOCUMENT")
                elif any(k in t for k in ("內部", "内部", "簽呈", "签呈", "公文")):
                    shapes.add("INTERNAL_DOCUMENT")
        # 附件类型兜底（jpg/png -> PHOTO；xlsx/csv -> SPREADSHEET）
        if doc is not None:
            for att in doc.attachments:
                ftype = str(getattr(att, "file_type", "") or "").lower()
                if ftype in ATTACH_TYPE_SHAPE and ATTACH_TYPE_SHAPE[ftype]:
                    shapes.add(ATTACH_TYPE_SHAPE[ftype])
                if ftype == "pdf" and (att.text or "").strip():
                    pdf_txt = to_simplified(att.text)
                    for k, targets in (("bank", BANK_TERMS), ("proc", PROCUREMENT_TERMS)):
                        if _has_any(pdf_txt, targets):
                            shapes.add("BANK_RECORD" if k == "bank" else "PROCUREMENT_FILE")
        # 多来源组合：邮件 + 附件或 >=2 形态 -> MULTI_SOURCE_PACKAGE
        if doc is not None and (doc.attachments and doc.body_text.strip()) and shapes:
            shapes.add("MULTI_SOURCE_PACKAGE")
        if len(shapes) >= 3 and "MULTI_SOURCE_PACKAGE" not in shapes:
            shapes.add("MULTI_SOURCE_PACKAGE")
        return sorted(s for s in shapes if s in {
            "FIRST_PERSON_TESTIMONY", "CHAT_RECORD", "AUDIO", "VIDEO", "PHOTO",
            "BANK_RECORD", "CONTRACT", "INTERNAL_DOCUMENT", "OFFICIAL_DOCUMENT",
            "PROCUREMENT_FILE", "SPREADSHEET", "DATABASE_RECORD", "ACADEMIC_DOCUMENT",
            "LOCATION_DATA", "CLASSIFIED_DOCUMENT", "ANONYMOUS_DOCUMENT",
            "MULTI_SOURCE_PACKAGE"})

    # ------------------------------------------------------------------
    def _first_person(self, blob: str, rule: Optional[RuleResult],
                      llm: Optional[LLMResult], shapes: List[str]) -> bool:
        """第一人称受害/亲历叙述：叙述以我+受害/性骚/申述为核心。"""
        # 明确的第一人称受害句
        direct = _has_any(blob, ["我遭", "我被", "我受到", "他對我", "对我性騷", "对我性骚",
                                 "我申訴", "我申诉", "我本人", "我親眼", "我亲眼"])
        # 第一人称 + 受害场景词（性骚/霸凌/吃案/受害）
        scene = _has_any(blob, SEX_HARASS_TERMS + BULLY_TERMS + ["吃案", "壓案", "压案", "受害"])
        # LLM witness_role 语义：有 victim/first_person 自述则增强
        llm_first = False
        if llm is not None:
            chains = " ".join(str(x) for x in (llm.relationship_chain or []))
            items = " ".join(str(x) for x in (llm.evidence_items or []))
            llm_first = bool(re.search(r"(受害|被害人|第一人稱|第一人称|本人|自述|受害者)", chains + items))
        # 聊天记录中的「我」不作为第一人称——需与受害场景词共现
        return bool(direct or (llm_first and scene))

    def _anonymity(self, blob: str, rule: Optional[RuleResult],
                   llm: Optional[LLMResult]) -> bool:
        # 反向：明确同意公开/实名（愿实名须与第一人称自述关联才足以覆盖匿名词）
        explicit_public = _has_any(blob, ["願意實名", "愿意实名", "願意具名", "愿意具名",
                                          "可以公開姓名", "同意公開", "同意公开", "可具名",
                                          "願意接受採訪", "愿意接受采访"])
        if _has_any(blob, ANONYMITY_TERMS):
            # 若文本同时出现「愿意实名/公开」且无否定词（如「不愿」），才判不匿名
            if explicit_public and not _has_any(blob, ["不願意", "不愿意", "不希望", "不敢"]):
                return False
            return True
        if explicit_public:
            return False
        return False

    def _explicitly_public(self, blob: str) -> bool:
        return _has_any(blob, ["願意實名", "愿意实名", "願意具名", "愿意具名", "可以公開姓名",
                               "同意公開", "同意公开", "可具名", "我已準備好公開", "已准备好公开"])

    def _public_interest(self, cats: List[str], blob: str,
                         llm: Optional[LLMResult]) -> bool:
        # A14 私德：公共利益成立条件（公职伦理/权势关系/违法/影响公务）
        if not any(c == "A14" for c in cats):
            return True  # 非私德类默认公共利益可论证（V1 已按类别过滤）
        power = _has_any(blob, ["權勢", "权势", "脅迫", "胁迫", "升遷", "升迁", "職權", "职权",
                                "安排職位", "给好处", "給好處"])
        illegal = _has_any(blob, ["性招待", "違法", "违法", "收賄", "收贿", "貪", "贪", "詐", "诈"])
        duty = _has_any(blob, ["公務", "公务", "職務", "职务", "市政", "選民", "选民"])
        if llm is not None:
            interest_note = (llm.public_interest_reason or "") + " " + (llm.reason_for_attention or "")
            power = power or any(k in interest_note for k in ("职权", "權勢", "权势", "伦理", "倫理"))
        return bool(power or illegal or duty)

    def _visually_clear(self, shapes: List[str], blob: str) -> bool:
        """证据简单、可对照、可视化：论文比对/文件版本对照/申报差异。"""
        comparable = _has_any(blob, ["比對表", "比对表", "比對報告", "比对报告", "對照", "对照",
                                     "版本", "差異", "差异", "turnitin", "相似度"])
        has_paper = "ACADEMIC_DOCUMENT" in shapes or "OFFICIAL_DOCUMENT" in shapes
        return bool(comparable and has_paper)

    def _quickly_verifiable(self, shapes: List[str], cats: List[str],
                            rule: Optional[RuleResult]) -> bool:
        """公开资料即可核验（论文/申报/标案/判决均公开可查）。"""
        if any(c in cats for c in ("A01", "A06", "A08")):
            return True
        if any(s in shapes for s in ("ACADEMIC_DOCUMENT", "OFFICIAL_DOCUMENT",
                                     "PROCUREMENT_FILE")):
            return True
        # 合同/会议记录等非公开原件不算「快速可核验」
        return False

    def _complex_explanation(self, blob: str, rule: Optional[RuleResult],
                             llm: Optional[LLMResult], shapes: List[str]) -> bool:
        """复杂关系链：多公司/多地块/多项目/复杂资金/股权。"""
        if _has_any(blob, COMPLEX_RELATION_TERMS):
            return True
        counts = _entity_kind_counts(rule)
        if counts["COMPANY"] >= 3:
            return True
        if counts["COMPANY"] >= 2 and counts["PROJECT"] >= 2:
            return True
        money_n = len(rule.money) if rule is not None else 0
        if counts["PROJECT"] >= MIN_COMPLEX_PROJECTS and money_n >= MIN_COMPLEX_AMOUNTS:
            return True
        if llm is not None:
            related = llm.related_entities or {}
            comps = related.get("companies") or []
            rels = (related.get("relatives") or []) + (related.get("intermediaries") or [])
            chains = len(llm.relationship_chain or [])
            if len(comps) >= 3:
                return True
            if money_n >= 2 and chains >= 2:
                return True
        return False

    def _old_case(self, rule: Optional[RuleResult], llm: Optional[LLMResult],
                  blob: str, cats: List[str]) -> tuple:
        """返回 (known_old_case, contains_new_information)."""
        # LLM known_old_information 字段明确提及旧闻（或 X 词/旧案 pattern）
        old_hint = False
        if llm is not None:
            old_hint = bool(llm.known_old_information) or \
                any("旧" in str(x) or "舊" in str(x) or "去年" in str(x)
                    for x in (llm.known_old_information or []))
        # 规则层反向词命中（EX/P20 语境）
        ex_hint = bool(rule is not None and (rule.ex_present or rule.no_new_evidence or
                                             any(p.pattern_id == "P20"
                                                 for p in rule.matched_patterns)))
        # 文本：转发/旧闻 + 无新金额无新证据
        text_old = _has_any(blob, ["去年报道", "去年報導", "旧闻", "舊聞", "旧案", "舊案",
                                   "转发", "轉發", "转贴", "轉貼", "转载", "轉載"]) and \
            not (rule is not None and (rule.money or rule.matched_keywords.get("E")))
        is_old = bool(old_hint or ex_hint or text_old)
        if not is_old:
            return False, True
        # 旧案但含新增材料（新金额/新文件/LLM 新信息）
        has_new = False
        if rule is not None:
            has_new = bool(rule.money or rule.matched_keywords.get("E") or
                           rule.new_evidence_after_ex)
        if llm is not None:
            has_new = has_new or bool(llm.new_information)
        return True, has_new

    def _witness_role(self, rule: Optional[RuleResult],
                      llm: Optional[LLMResult]) -> str:
        if llm is not None:
            chains = " ".join(str(x) for x in (llm.relationship_chain or []))
            if re.search(r"(受害者|被害人|申訴人|申诉人|工讀生|工读生|當事人|当事人)", chains):
                return "victim"
            if re.search(r"(內部|员工|職員|职员|助理|幕僚|承辦|承办)", chains):
                return "insider"
            return "third_party"
        return "insider" if (rule is not None and rule.matched_keywords.get("C")) else "third_party"
