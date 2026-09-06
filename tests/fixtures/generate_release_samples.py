# -*- coding: utf-8 -*-
"""生成首发渠道推荐测试样本（tests/fixtures/release_emails/）与期望标注。

样本骨架复用 generate_samples.py 中已验证能进入 S/A/B 的措辞，再叠加
渠道决策要素（当事人意愿/证据形态/灭证风险/机密等级等），确保测试关注
「渠道判断」而非 V1 筛选召回。

期望字段约定：
  expect_sa      是否应进入 S/A/B（未进入表示系统不应推荐公开首发）
  top1           人工预设主渠道（Top-1 Accuracy 分母）
  top2           正确渠道应落在 primary+secondary（Top-2 Coverage）
  avoid          应避免的渠道
  formal         应给出正式检举建议（bool）
  formal_types   应包含的机关类型
  no_r1r4        高敏感样本：R1/R4 不得为首推（Sensitive Material Safety）
  no_r1          匿名/身份保护样本：R1 不得为首推（Source Protection）
  count_accuracy 是否计入 Top-1 Accuracy 分母（场景足够明确时计）
"""
from __future__ import annotations

import json
from email import policy
from email.message import EmailMessage
from pathlib import Path

FIX_DIR = Path(__file__).resolve().parent
EML_DIR = FIX_DIR / "release_emails"
EXP_FILE = FIX_DIR / "expectations_release.jsonl"

P = "林文杰"      # 民进党籍市议员（虚构）
A = "王雅婷"      # 办公室主任（虚构）
C1 = "大川建设股份有限公司"
C2 = "永泰营造股份有限公司"

EXPECTED = []


def _add(rid, text, subject, *, top1=None, top2=None, avoid=None, formal=False,
         formal_types=None, no_r1r4=False, no_r1=False, count_accuracy=True,
         expect_sa=True, note=""):
    msg = EmailMessage(policy=policy.default)
    msg["Subject"] = subject
    msg["From"] = "whistle@example.tw"
    msg["To"] = "tips@newspaper.tw"
    msg["Date"] = "Tue, 01 Sep 2026 09:30:00 +0800"
    msg["Message-ID"] = f"<{rid}@release-test>"
    msg.set_content(text)
    (EML_DIR / f"{rid}.eml").write_bytes(msg.as_bytes())
    rec = {"id": rid, "expect_sa": expect_sa, "top1": top1, "top2": top2 or [],
           "avoid": avoid or [], "formal": formal, "formal_types": formal_types or [],
           "no_r1r4": no_r1r4, "no_r1": no_r1, "count_accuracy": count_accuracy}
    if note:
        rec["note"] = note
    EXPECTED.append(rec)


def main():
    EML_DIR.mkdir(parents=True, exist_ok=True)
    # ============ Test 01 学术论文：论文+完整比对表+Turnitin ============
    _add("rc01_thesis_turnitin",
         f"檢舉：{P}議員碩士論文抄襲。附件有完整比對表與Turnitin報告，顯示論文大段內容與他人已發表論文高度相似，逐段比對一覽無遺。",
         "論文完整比對表檢舉", top1="R4", top2=["R4", "R2"], formal=True,
         formal_types=["ACADEMIC_ETHICS"],
         note="Test01 论文比对 -> R4 + 学伦")
    # ============ Test 02 性骚实名：第一人称+愿实名+LINE ============
    _add("rc02_sexharass_named",
         f"我是黨部工讀生。我遭{A}主任權勢性騷擾，曾向{P}議員反映卻被壓案未啟動程序，主任還威脅我。我願意實名對外發聲，不怕被知道。附件有我與主任的LINE對話原始紀錄。",
         "工讀生實名檢舉性騷", top1="R1", top2=["R1", "R2"], no_r1=False,
         note="Test02 实名性骚 -> R1+R2")
    # ============ Test 03 性骚匿名：第一人称+要求保护身份 ============
    _add("rc03_sexharass_anon",
         f"我是黨部工讀生，我遭{A}主任權勢性騷擾，向{P}議員反映後被壓案，還被威脅刪除訊息。我要求保護身分，不願意具名，不希望報導中出現任何可辨識我的細節。附件有申訴書與LINE對話。",
         "性騷申訴要求匿名", top1="R2", top2=["R2"], avoid=["R1"], no_r1=True,
         count_accuracy=True,
         note="Test03 匿名性骚 -> R2 avoid R1")
    # ============ 职场霸凌实名 ============
    _add("rc04_bully_named",
         f"我是{P}議員服務處前助理。我遭到{A}主任長期職場霸凌與言語羞辱，有錄音為證。我願意具名接受採訪，把錄音檔公開。",
         "服務處前助理實名揭霸凌", top1="R1", top2=["R1", "R2"],
         note="第一人称职场霸凌实名 -> R1")
    # ============ Test 04 收贿金流：银行流水+职务对价 ============
    _add("rc05_bribe_bankflow",
         f"{C1}建商透過中間人給{P}議員辦公室主任{A}八十萬元顧問費，之後辦公室人員出面協調建照、協助發照。附件有銀行流水與LINE對話。",
         "建商顧問費換協調建照", top1="R5", top2=["R5", "R3"], avoid=["R1"],
         formal=True, formal_types=["PROSECUTOR"],
         note="Test04 收贿金流 -> R5 优先")
    # ============ Test 12 灭证风险 ============
    _add("rc06_destruction_risk",
         f"{C1}建商匯款八十萬元顧問費給{A}主任後協助發照。爆料人說對方發現被盯上，正在刪除LINE對話與監視器檔案，要求我們快點處理。附件有銀行流水。",
         "對方正在刪除紀錄", top1="R5", top2=["R5", "R3"], avoid=["R1"],
         formal=True, formal_types=["PROSECUTOR"], count_accuracy=True,
         note="Test12 灭证风险 -> 检举优先")
    # ============ Test 05 政府采购：招标前接触+需求书版本 ============
    _add("rc07_procurement_prebid",
         f"市府資訊採購案招標前三週，承辦人已把需求書交給特定廠商{C2}，雙方招標前接觸頻繁，正式規格照廠商資料製作、為其量身訂做。附件有需求書前後版本與email往來，可直接對照。",
         "標案需求書外流", top1="R3", top2=["R3", "R4"], count_accuracy=True,
         note="Test05 政府采购 -> R3+R4")
    # ============ Test 06 政治献金 ============
    _add("rc08_donation_quid",
         f"{C1}在選前捐政治獻金二百萬元給{P}，選後議員在議會護航該公司取得市府標案。附件有政治獻金申報資料與得標紀錄，可交叉比對。",
         "政治獻金換標案", top1="R3", top2=["R3", "R4"], formal=True,
         formal_types=["INVESTIGATION_BUREAU"],
         note="Test06 献金+标案 -> R3")
    # ============ Test 07 私德照片：公共利益成立 ============
    _add("rc09_affair_photo",
         f"{P}議員與女助理發生婚外關係，利用權勢安排其升遷加薪。附件有兩人親密照片與LINE對話，照片為真實拍攝非合成。",
         "議員權勢婚外關係照片", top1="R2", top2=["R2"], avoid=["R5"],
         count_accuracy=True, no_r1=True,
         note="Test07 私德照片+公共利益 -> R2")
    # ============ Test 08 普通绯闻：无公共利益 -> 不应推荐公开首发 ============
    _add("rc10_gossip_no_public",
         "某政治人物傳出婚外緋聞，據說是與一名女子過從甚密，但沒有照片、沒有對話紀錄，也看不出與其職務有任何關係。",
         "未經證實緋聞", expect_sa=False, count_accuracy=False,
         note="Test08 无公共利益绯闻 -> 不推荐公开")
    # ============ Test 09 国安密件：未公开军事文件 ============
    _add("rc11_military_doc",
         f"{A}主任涉嫌將國防部未公開軍事文件翻拍後，透過私人email傳送給中間人，文件註記極機密，涉及部隊部署細節，換取酬金。附件有密件翻拍截圖與對話紀錄。",
         "未公開軍事文件外流", top1="R6", top2=["R6"], avoid=["R1", "R4"],
         formal=True, formal_types=["INVESTIGATION_BUREAU", "PROSECUTOR"],
         no_r1r4=True,
         note="Test09 国安密件 -> R6 禁 R1/R4")
    # ============ Test 10 匿名假公文：无 metadata 无法确认来源 ============
    _add("rc12_anonymous_doc",
         f"收到一封匿名提供的「{P}議員涉及土地案內部機密」文件，對方不願透露來源，文件沒有metadata，也無法確認是否遭變造。文件疑似翻拍後外流，內容涉及市府土地變更內部簡報。",
         "匿名來路不明內部文件", top1="R6", top2=["R6"], avoid=["R1", "R4"],
         no_r1r4=True, count_accuracy=True,
         note="Test10 匿名文件 -> R6")
    # ============ Test 11 土地光电复杂关系 ============
    _add("rc13_land_solar_complex",
         f"五家關係企業與多名地主參與市郊太陽光電案場，公司間交叉持股、層層轉匯分潤，{P}議員胞兄持股其中兩家，市府審查時議員施壓加速饋線核准。附件有股權結構表與匯款紀錄。",
         "光電五公司交叉持股", top1="R3", top2=["R3", "R5"], formal=True,
         formal_types=["PROSECUTOR", "INVESTIGATION_BUREAU"],
         note="Test11 复杂政商关系 -> R3+R5")
    # ============ A01 正式学伦调查中 ============
    _add("rc14_thesis_hearing",
         f"{P}議員碩士論文被檢舉抄襲，學倫會已啟動調查並通知本人說明。附件有比對報告，逐段標示與另一篇論文雷同。",
         "學倫會調查中論文", top1="R4", top2=["R4", "R2"], formal=True,
         formal_types=["ACADEMIC_ETHICS"],
         note="论文+正式学伦调查 -> R4")
    # ============ 组织犯罪 ============
    _add("rc15_organized_crime",
         f"{P}議員與黑道角頭共同投資公司，角頭負責處理地方糾紛，議員辦公室協助角頭公司取得政府標案。附件有合約與帳冊。",
         "議員與角頭共同投資", top1="R5", top2=["R5", "R3"], avoid=["R1"],
         formal=True, formal_types=["PROSECUTOR"],
         note="组织犯罪 -> 检调优先")
    # ============ 贿选 ============
    _add("rc16_vote_buying",
         f"選前樁腳拿著名冊到服務處，說每票五百元現金已逐戶發放，{A}主任收下名單。附件有選民名冊與現金照片。",
         "每票五百元買票", top1="R5", top2=["R5", "R3"], avoid=["R1"],
         formal=True, formal_types=["PROSECUTOR"],
         note="贿选 -> 检调优先")
    # ============ 人头助理（薪资文件） ============
    _add("rc17_ghost_assistant",
         f"檢舉：{P}議員的胞姐掛名公費助理，未實際上班，每月助理薪資入帳後全數領出，提款卡長期由辦公室主任{A}保管。附件有薪資單與提款紀錄。",
         "檢舉人頭助理", top1=None, top2=["R3", "R5"], formal=True,
         formal_types=["PROSECUTOR"],
         note="人头助理 -> R3/R5 皆可（调查报道或告发）")
    # ============ 吃案第三人举报 ============
    _add("rc18_suppress_thirdparty",
         f"黨部工讀生向{A}主任反映遭主管性騷擾，主任說這件事不要擴大、不要對外說，內部處理，之後工讀生被調職。檢舉人提供申訴截圖與對話，要求先保護被害人。",
         "性騷吃案調職檢舉", top1="R2", top2=["R2", "R5"], avoid=["R1"],
         no_r1=True, count_accuracy=True,
         note="第三人检举性骚吃案 -> R2 保护当事人")
    # ============ 性骚匿名+恐吓 ============
    _add("rc19_sexharass_threatened",
         f"我遭{A}主任性騷擾並被威脅「不要對外說，否則別想繼續做」。我要求保護身分不願具名。附件有恐嚇訊息與對話截圖。",
         "性騷遭恐嚇要求保護", top1="R2", top2=["R2"], avoid=["R1"], no_r1=True,
         formal=True, formal_types=["LABOR_OR_EQUALITY_AUTHORITY", "INTERNAL_COMPLAINT"],
         count_accuracy=True,
         note="匿名+报复威胁 -> R2")
    # ============ 权势私德（对话记录，无照片） ============
    _add("rc20_power_affair_chat",
         f"{P}議員與女助理發生婚外關係，LINE對話顯示其以職權脅迫對方繼續關係並安排升遷。附件有LINE對話原始紀錄。",
         "議員脅迫婚外關係", top1="R2", top2=["R2"], avoid=["R5"], count_accuracy=True,
         no_r1=True,
         note="权势私德对话 -> R2")
    # ============ 补助款假发票 ============
    _add("rc21_subsidy_invoice",
         f"{P}議員協助其妻擔任理事長的協會取得三百萬元政府補助，協會再以假發票不實核銷。附件有補助文件與發票。",
         "補助款不實核銷", top1="R3", top2=["R3", "R5"], formal=True,
         formal_types=["CONTROL_YUAN"],
         note="补助异常 -> 深度调查")
    # ============ 炉碴对价（A03 并存） ============
    _add("rc22_flyash_fee",
         f"爐碴清運業者每月固定交付{P}議員服務處五十萬處理費，換取議員施壓環保稽查放水。附件有帳冊與LINE對話。",
         "爐碴業者交付處理費", top1="R5", top2=["R5", "R3"], avoid=["R1"],
         formal=True, formal_types=["PROSECUTOR", "INVESTIGATION_BUREAU"],
         note="环保对价金流 -> R5（检察官/调查机关皆宜）")
    # ============ EX旧案+新银行流水 ============
    _add("rc23_old_case_new_flow",
         f"去年{P}議員一案獲不起訴處分，但本次郵件提供此前未曝光的銀行流水，顯示{C1}另匯入三百萬元到其服務處帳戶。",
         "不起訴後新金流", top1="R5", top2=["R5", "R3"], avoid=["R1"],
         formal=True, formal_types=["PROSECUTOR"],
         note="旧案新证据金流 -> R5")
    # ============ 内部文件标密翻拍 ============
    _add("rc24_sealed_internal_doc",
         f"{A}主任涉嫌將標示密件的市府內部文件翻拍後，透過私人email提供給特定中間人，換取酬金。附件有密件翻拍截圖與對話紀錄。",
         "密件翻拍外傳", top1="R6", top2=["R6"], avoid=["R1", "R4"],
         no_r1r4=True, formal=True, formal_types=["INVESTIGATION_BUREAU", "PROSECUTOR"],
         count_accuracy=True,
         note="密件外传 -> R6")
    # ============ 性招待（A14 权势） ============
    _add("rc25_sex_party_photo",
         f"{P}議員被爆接受建商招待性招待，同行者包含市府官員。附件有現場照片與帳單，照片為真實拍攝。",
         "議員性招待照片", top1="R2", top2=["R2"], avoid=["R5"], count_accuracy=True,
         note="私德+违法 -> R2（涉违法以R5为后备？见规则）")
    # ============ 土地重划金流 ============
    _add("rc26_land_rezone_cash",
         f"{P}議員涉嫌在市地重劃案中收受地主三百萬元現金，並在市議會護航都市計畫變更案。附件有帳冊影本與會議紀錄。",
         "重劃案金流", top1="R5", top2=["R5", "R3"], avoid=["R1"], formal=True,
         formal_types=["PROSECUTOR"],
         note="土地+现金对价 -> R5")
    # ============ 记者个人自述（无证据转述）-> 低置信不入S/A？ ============
    _add("rc27_vague_no_evidence",
         "有人舉報某民進黨政治人物貪污，說他收了很多錢，但沒有具體事實與證據。",
         "空泛舉報貪污", expect_sa=False, count_accuracy=False,
         note="空泛举报 -> 不应进 S/A/B")
    # ============ 旧闻转发无新增 ============
    _add("rc28_old_news_dup",
         f"去年{P}議員涉嫌工程弊案，最後不起訴處分。本郵件只是轉發當年新聞報導，無新增內容。",
         "不起訴舊聞轉發", expect_sa=False, count_accuracy=False,
         note="旧闻转发 -> 不进 S/A/B（非首发）")
    # ============ 深喉咙内部员工匿名检举企业内幕（权势关系） ============
    _add("rc29_insider_anon_politics",
         f"任職{P}議員服務處的內部人員提供主任{A}與建商{C1}的對話紀錄，顯示協助關說建照。他要求保護身分，不願具名。",
         "服務處內部匿名檢舉", top1="R2", top2=["R2", "R5"], avoid=["R1"],
         no_r1=True, count_accuracy=True,
         note="内部匿名爆料 -> R2")
    # ============ 选举动员异常（人头党员） ============
    _add("rc30_member_padding",
         f"{P}議員競選連任時，助理收購大量人頭黨員名冊灌入黨部，名冊顯示同一地址數十人入黨，疑似人頭黨員虛灌選舉動員。附件有名冊Excel檔。",
         "人頭黨員名冊", expect_sa=False, count_accuracy=False,
         note="选举人头党员（V1 弱识别题材，不进 S/A/B 时不推荐公开）")
    # ============ 家庭暴力(民代) ============
    _add("rc31_domestic_violence",
         f"{P}議員被太太指控家暴，有驗傷單與錄音。太太希望透過媒體討公道但要求不露臉。附件有驗傷單與錄音檔。",
         "議員家暴指控", top1="R2", top2=["R2"], avoid=["R1"], no_r1=True,
         count_accuracy=True,
         note="家暴+保护当事人 -> R2")
    # ============ 酒驾吃案 ============
    _add("rc32_drunk_drive_suppress",
         f"{P}議員上月酒駕被攔查，酒測值超標，卻疑似找關係要求撤案吃案未移送。附件有酒測紀錄單與對話。",
         "議員酒駕疑吃案", top1="R5", top2=["R5", "R3"], avoid=["R1"], formal=True,
         formal_types=["PROSECUTOR"], count_accuracy=True,
         note="刑事+吃案 -> R5")
    # ============ 监察/行政不法（许可放水） ============
    _add("rc33_permit_favor",
         f"{P}議員幫特定餐廳施壓市府要求提前核准營業許可，跳過消防稽查，餐廳事後贈送乾股給議員妻子。附件有股權文件與對話。",
         "施壓許可換乾股", top1="R5", top2=["R5", "R3"], avoid=["R1"], formal=True,
         formal_types=["PROSECUTOR", "CONTROL_YUAN"], count_accuracy=True,
         note="许可对价 -> R5")
    # ============ 海外账户洗钱 ============
    _add("rc34_money_laundering",
         f"{A}主任協助{P}議員將不明款項層層轉匯至海外帳戶，透過三家境外公司繞回購買房產，涉洗錢。附件有匯款單、銀行流水與境外公司登記資料。",
         "層層轉匯海外洗錢", top1="R5", top2=["R5", "R3"], avoid=["R1"],
         formal=True, formal_types=["PROSECUTOR"], count_accuracy=True,
         note="洗钱金流 -> R5")
    # ============ 监听/行踪资料（黑客取得） ============
    _add("rc35_hacked_location",
         f"有人提供{P}議員行蹤紀錄與基地台定位資料，宣稱是從其手機「備份」取得的，來源不明。附件有定位資料檔。",
         "不明來源行蹤資料", top1="R6", top2=["R6"], avoid=["R1", "R4"],
         no_r1r4=True, count_accuracy=True,
         note="疑似黑客资料 -> R6")
    # ============ 学历造假（学位未取得声称） ============
    _add("rc36_fake_degree",
         f"檢舉：{P}議員對外宣稱畢業於美國某大學，但校方回覆查無學籍，學歷造假、學位造假，論文也比對有問題。學倫會已啟動調查並通知本人說明。附件有校方email回覆、論文比對表與其履歷表。",
         "議員學歷造假學倫調查", top1="R4", top2=["R4", "R2"], formal=True,
         formal_types=["ACADEMIC_ETHICS"], count_accuracy=True,
         note="学历造假 -> R4 公开比对")
    # ============ 器官/人伦边缘案例：排除（非范围） ============
    # （不加入：超出系统类别范围，不造无谓样本）
    # ============ 记者会型：财务申报差异 ============
    _add("rc37_declare_diff",
         f"檢舉：{P}議員財產申報不實，申報資料與其實際購入房產價格差異巨大，涉嫌短報漏報、申報不實。附件有申報表與實價登錄對照表，一目瞭然。",
         "財產申報不實比對", top1="R4", top2=["R4", "R3"], formal=True,
         formal_types=["CONTROL_YUAN"], count_accuracy=True,
         note="申报差异可视化 -> R4")
    # ============ 政府采购绑标（三家陪标） ============
    _add("rc38_rigged_bid",
         f"市府工程標案開標時，三家投標廠商之押標金皆由同一帳戶匯出，疑似陪標綁標，得標廠商為{C2}。附件有押標金匯款紀錄與標單。",
         "同帳戶押標金綁標", top1="R5", top2=["R5", "R3"], avoid=["R1"],
         formal=True, formal_types=["PROSECUTOR", "INVESTIGATION_BUREAU"],
         count_accuracy=True,
         note="绑标刑事 -> R5")
    # ============ 砂石/土方利益 ============
    _add("rc39_gravel_money",
         f"砂石業者與{P}議員服務處配合，取得河川疏濬土方標案後轉售牟利，每月回饋服務處二十萬元。附件有帳冊與標案文件。",
         "砂石業者回饋", top1="R5", top2=["R5", "R3"], avoid=["R1"],
         formal=True, formal_types=["PROSECUTOR"], count_accuracy=True,
         note="砂石对价 -> R5")
    # ============ 视频证据（围标谈判被拍） ============
    _add("rc40_video_collusion",
         f"檢舉人拍到{C2}負責人與市府承辦人在茶館討論底價、圖利特定廠商的影片，畫面清晰，疑似圍標綁標、洩漏底價。檢舉人要求保護身分不願具名，但同意影片供記者查證後使用。附件有影片檔案與標單影本。",
         "茶館談底價影片", top1=None, top2=["R2", "R3"], avoid=["R1"],
         no_r1=True, count_accuracy=True,
         note="匿名+视频+围标 -> R2/R3 皆可（媒体核验或深度调查），禁社交")
    # ============ 顶替开会/公费考察造假 ============
    _add("rc41_junket_fraud",
         f"檢舉：{P}議員公費出國考察未實際出席會議，卻虛報全程參與並不實核銷費用，浮報金額。附件有行程表、申報表與會議簽到紀錄對照。",
         "公費考察核銷不實", top1="R3", top2=["R3", "R4"], formal=True,
         formal_types=["CONTROL_YUAN"], count_accuracy=True,
         note="核销不实 -> 调查报道+记者会材料")
    # ============ 私德对话无公共利益(权势弱) ============
    _add("rc42_affair_no_power",
         f"{P}議員與一名女子婚外情，LINE對話內容為一般情侶對話，看不出與議員職務有任何關係，也無脅迫或利益往來。",
         "議員婚外情無職權關聯", expect_sa=False, count_accuracy=False,
         note="无私德公共利益 -> 不推荐公开")

    with open(EXP_FILE, "w", encoding="utf-8") as f:
        for rec in EXPECTED:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"生成 {len(EXPECTED)} 条渠道测试样本 -> {EML_DIR}")


if __name__ == "__main__":
    main()
