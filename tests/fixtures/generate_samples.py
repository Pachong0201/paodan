"""生成 60 条测试样本 EML（tests/fixtures/emails/）及期望标注 tests/fixtures/expectations.jsonl.

样本措辞策略：自然嵌入规则包 canonical 词（如「未實際上班」「壓案」「翻拍」「傳送」），
确保 Pattern Engine 的验收覆盖真实反映引擎能力，而不是测试文案碰运气。

构成：
  20 显性高价值 + 10 隐性高价值 + 20 困难负样本 + 10 边界（Case1-10）
"""
from __future__ import annotations

import json
from email import policy
from email.message import EmailMessage
from pathlib import Path

FIX_DIR = Path(__file__).resolve().parent
EML_DIR = FIX_DIR / "emails"
EXP_FILE = FIX_DIR / "expectations.jsonl"

P = "林文杰"        # 民进党籍市议员（虚构）
A = "王雅婷"        # 办公室主任（虚构）
C1 = "大川建设股份有限公司"
C2 = "永泰营造股份有限公司"

EXPECTED = []


def _add(email_id, text, subject, *, expect_sa=None, expect_cats=None, patterns=None,
         min_score=None, note="", sender="whistle@example.tw"):
    msg = EmailMessage(policy=policy.default)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = "tips@newspaper.tw"
    msg["Date"] = "Tue, 01 Sep 2026 09:30:00 +0800"
    msg["Message-ID"] = f"<{email_id}@tipbox>"
    msg.set_content(text)
    (EML_DIR / f"{email_id}.eml").write_bytes(msg.as_bytes())
    rec = {"id": email_id, "expect_sa": expect_sa, "expect_cats": expect_cats or [],
           "patterns": patterns or [], "min_score": min_score}
    if note:
        rec["note"] = note
    EXPECTED.append(rec)


def main():
    EML_DIR.mkdir(parents=True, exist_ok=True)
    # ================= 20 条显性高价值 =================
    _add("hv01_headcount",
         f"檢舉：{P}議員的胞姐掛名公費助理，未實際上班，每月助理薪資入帳後全數領出，提款卡長期由辦公室主任{A}保管。附件有薪資單與提款紀錄。",
         "檢舉人頭助理", expect_sa=True, expect_cats=["A02"], patterns=["P01", "P02", "P03"], min_score=75)
    _add("hv02_bribe_building",
         f"{C1}建商透過中間人給{P}議員辦公室主任{A}八十萬元顧問費，之後辦公室人員出面協調建照、協助發照。附件有匯款紀錄與LINE對話。",
         "建商顧問費換協調建照", expect_sa=True, expect_cats=["A03", "A04"], patterns=["P05", "P19"], min_score=75)
    _add("hv03_land_rezoning",
         f"{P}議員涉嫌在市地重劃案中收受地主三百萬元現金，並在市議會護航都市計畫變更案。附件有帳冊影本與會議紀錄。",
         "重劃案金流", expect_sa=True, expect_cats=["A03", "A04"], patterns=["P11"], min_score=75)
    _add("hv04_solar",
         f"太陽光電業者租地需經{A}主任牽線，支付土地仲介費每公頃三十萬，{P}議員再向市府施壓要求加速饋線核准。附件LINE對話與匯款單。",
         "光電土地仲介費", expect_sa=True, expect_cats=["A03", "A05"], patterns=["P10"], min_score=75)
    _add("hv05_procurement_leak",
         f"市府資訊採購案招標前三週，承辦人已把需求書交給特定廠商{C2}，雙方招標前接觸頻繁，正式規格照廠商資料製作、為其量身訂做，只有一家符合。附件有需求書版本與email往來。",
         "標案需求書外流", expect_sa=True, expect_cats=["A06"], patterns=["P07", "P08", "P09"], min_score=75)
    _add("hv06_subsidy",
         f"{P}議員協助其妻擔任理事長的協會取得三百萬元政府補助，協會再以假發票不實核銷。附件有補助文件與發票。",
         "補助款不實核銷", expect_sa=True, expect_cats=["A07", "A09"], min_score=70)
    _add("hv07_donation_quidproquo",
         f"{C1}在選前捐政治獻金二百萬元給{P}，選後議員在議會護航該公司取得市府標案。附件有政治獻金收據與得標公告。",
         "政治獻金換標案", expect_sa=True, expect_cats=["A08", "A06"], patterns=["P12"], min_score=75)
    _add("hv08_kin_contract",
         f"{P}議員的兒子經營營造公司，議員本人在市府工程評選簽核時未迴避，事後核准發包給兒子公司。附件有簽呈。",
         "親屬利益本人簽核", expect_sa=True, expect_cats=["A09"], patterns=["P06"], min_score=75)
    _add("hv09_vote_buying",
         f"選前樁腳拿著名冊到服務處，說每票五百元現金已逐戶發放，{A}主任收下名單。附件有選民名冊與現金照片。",
         "每票五百元買票", expect_sa=True, expect_cats=["A10"], patterns=["P13", "P14"], min_score=75)
    _add("hv10_sexual_harassment",
         f"黨部工讀生申訴遭{A}主任權勢性騷擾，向{P}議員反映後遭壓案未啟動程序，主任事後還威脅刪除訊息。附件有申訴書與LINE對話。",
         "性騷申訴吃案", expect_sa=True, expect_cats=["A11", "A12"], patterns=["P15", "P16"], min_score=70)
    _add("hv11_doc_leak",
         f"{A}主任涉嫌將市府密件翻拍後，透過私人email提供給特定中間人，換取酬金。附件有密件翻拍截圖與對話紀錄。",
         "密件翻拍外傳", expect_sa=True, expect_cats=["A17"], patterns=["P17"], min_score=75)
    _add("hv12_flyash",
         f"爐碴清運業者每月固定交付{P}議員服務處五十萬處理費，換取議員施壓環保稽查放水。附件有帳冊與LINE對話。",
         "爐碴業者交付處理費", expect_sa=True, expect_cats=["A16", "A03"], patterns=["P18"], min_score=75)
    _add("hv13_drink_drive",
         f"{P}議員上月酒駕被攔查，酒測值超標，卻疑似找關係要求撤案吃案未移送。附件有酒測紀錄單與對話。",
         "議員酒駕疑吃案", expect_sa=True, expect_cats=["A13", "A12"], min_score=70)
    _add("hv14_gang",
         f"{P}議員與黑道角頭共同投資公司，角頭負責處理地方糾紛，議員辦公室協助角頭公司取得政府標案。附件有合約與帳冊。",
         "議員與角頭利益", expect_sa=True, expect_cats=["A15", "A06"], min_score=70)
    _add("hv15_salary_fund",
         f"{P}議員要求助理每月低薪高報，實際薪資入帳後交回辦公室作為公積金。附件有薪資表與銀行帳戶明細。",
         "助理薪資回流", expect_sa=True, expect_cats=["A02"], patterns=["P02", "P03"], min_score=70)
    _add("hv16_media_sex",
         f"{P}議員被爆與女助理發生婚外關係，利用權勢安排其升遷加薪，對話紀錄顯示脅迫性。附件LINE對話紀錄。",
         "利用權勢婚外關係", expect_sa=True, expect_cats=["A14"], min_score=60)
    _add("hv17_admin_permit",
         f"{P}議員幫特定餐廳施壓市府要求提前核准營業許可，跳過消防稽查，餐廳事後贈送乾股給議員妻子。附件有股權文件與對話。",
         "施壓許可換乾股", expect_sa=True, expect_cats=["A18", "A03"], min_score=70)
    _add("hv18_suppress_whistle",
         f"爆料人檢舉{P}議員後遭服務處人員要求撤案，並恐嚇刪除訊息、不要對外說，否則報復。附件有恐嚇訊息截圖。",
         "報復爆料人", expect_sa=True, expect_cats=["A12"], patterns=["P16"], min_score=65)
    _add("hv19_thesis",
         f"{P}議員碩士論文被檢舉抄襲，學倫會已啟動學倫調查，比對報告顯示與他人論文高度相似。附件有比對表。",
         "論文學倫調查", expect_sa=True, expect_cats=["A01"], min_score=60)
    _add("hv20_cash_at_home",
         f"{P}議員服務處遭搜索，查獲來源不明現金與人頭帳戶，檢方已列被告並偵辦中。附件有報導與相關文件。",
         "搜索列被告", expect_sa=True, expect_cats=["A08", "A03"], min_score=70)

    # ================= 10 条隐性高价值（无 贪污/收贿/违法/弊案 等词） =================
    _add("im01_coordinate_build",
         f"{C1}林總上週與{P}議員辦公室主任{A}見面。主任說：老闆很重視，資格幫忙想辦法，不要走公文，之後會回饋。附件有LINE對話原始記錄。",
         "主任對話紀錄", expect_sa=True, expect_cats=["A03", "A04"], patterns=["P04"], min_score=70)
    _add("im02_deposit_card",
         f"{P}議員的胞姐未實際上班，但助理薪資每月申報入帳後全數領出，提款卡由辦公室主任{A}保管。附件有存摺影本。",
         "胞姐薪資回流", expect_sa=True, expect_cats=["A02"], patterns=["P01", "P02"], min_score=75)
    _add("im03_spec_align",
         f"標案公告前接觸頻繁，承辦人把需求書先給{C2}，之後正式採購文件照廠商提供資料製作，報價單與規格幾乎一致。附件含兩份文件對照。",
         "需求書與廠商報價一致", expect_sa=True, expect_cats=["A06"], patterns=["P07", "P08"], min_score=70)
    _add("im04_solar_fee",
         f"太陽光電案場開發商按月支付顧問費給{P}議員服務處顧問，顧問再安排與市府官員協調饋線併網、協助發照。附件有轉帳紀錄與行程表。",
         "光電顧問費與協調", expect_sa=True, expect_cats=["A05", "A03"], patterns=["P10"], min_score=70)
    _add("im05_rezoning_fee",
         f"土地變更案申請期間，地主匯款二百萬元給{P}議員妹妹的公司，都市計畫委員會隨後通過變更。附件有匯款紀錄與會議紀錄。",
         "變更期間匯款給親屬", expect_sa=True, expect_cats=["A04", "A09"], patterns=["P11"], min_score=75)
    _add("im06_vote_list",
         f"選前兩週，樁腳拿著名冊到服務處說每戶都拜託好了，主任說名單先給我，先不要留紀錄。附件有LINE對話截圖。",
         "名單不留紀錄", expect_sa=True, expect_cats=["A10"], patterns=["P13", "P14"], min_score=70)
    _add("im07_harass_pressure",
         f"工讀生向主任反映遭主管性騷擾，主任說這件事不要擴大、不要對外說，內部處理，之後工讀生被調職。附件有申訴與回覆截圖。",
         "性騷壓案調職", expect_sa=True, expect_cats=["A11", "A12"], patterns=["P15", "P16"], min_score=70)
    _add("im08_doc_copy",
         f"{A}主任把標示密件的內部文件翻拍後，透過私人email傳送給某公司代表。附件有翻拍檔與email。",
         "密件翻拍傳送", expect_sa=True, expect_cats=["A17"], patterns=["P17"], min_score=70)
    _add("im09_flyash_fee",
         f"廢棄物清運業者每月固定付{P}議員服務處處理費，服務處再轉介稽查行程資訊，並要求施壓不要稽查。附件有帳冊與行事曆。",
         "清運處理費", expect_sa=True, expect_cats=["A16", "A12"], patterns=["P18"], min_score=70)
    _add("im10_relatives_salary",
         f"{P}議員的母親掛名服務處助理，未實際上班，每月薪資入帳後由主任代為領出。附件有薪資表與提款紀錄。",
         "母親掛名助理", expect_sa=True, expect_cats=["A02"], patterns=["P01", "P02"], min_score=70)

    # ================= 20 条困难负样本 =================
    _add("neg01_normal_procure",
         "市府依政府採購法辦理道路養護標案，公開招標、開標、決標，一切程序正常，歡迎廠商投標。",
         "道路養護標案新聞稿", expect_sa=False, min_score=60)
    _add("neg02_public_meeting",
         "市府召開跨部門例行協調會，討論下水道工程進度，各局處出席，會議紀錄公開上網。",
         "例行協調會議紀錄", expect_sa=False, min_score=60)
    _add("neg03_legal_donation",
         "某企業依法透過政治獻金專戶捐款給候選人，金額與申報均符合規定。",
         "企業依法政治獻金", expect_sa=False, min_score=60)
    _add("neg04_normal_kin",
         "議員的妹妹在學校任教，是正常公開教職，與議員職務無關。",
         "議員妹妹任教", expect_sa=False, min_score=60)
    _add("neg05_photo_together",
         "議員與建商出席同場公益活動合照，現場還有其他十多位來賓。",
         "公益活動合照", expect_sa=False, min_score=60)
    _add("neg06_normal_paper",
         "某大學教授發表關於都市治理的論文，引用文獻完備，無抄襲疑慮。",
         "學術論文發表", expect_sa=False, min_score=60)
    _add("neg07_policy_criticism",
         "某評論員批評政府住宅政策不當，認為應增加社會住宅供給。",
         "政策評論", expect_sa=False, min_score=60)
    _add("neg08_political_insult",
         "某民眾在網路上辱罵民進黨議員是垃圾、滾下台，未提供具體事證。",
         "網路辱罵言論", expect_sa=False, min_score=40)
    _add("neg09_normal_solar",
         "太陽能業者依法申請地面型光電案場許可，通過環評後施工，一切依規定辦理。",
         "光電案場依法申請", expect_sa=False, min_score=60)
    _add("neg10_normal_permit",
         "餐廳向市府申請營業許可，經消防檢查合格後核發執照，過程公開。",
         "餐廳依法取得許可", expect_sa=False, min_score=60)
    _add("neg11_routine_qa",
         "議員在議會質詢道路工程品質，要求工務局說明進度，屬例行監督。",
         "議會例行質詢", expect_sa=False, min_score=60)
    _add("neg12_spouse_job",
         "議員配偶任職私人公司人資部門，屬一般就業，與公共事務無關。",
         "配偶一般職業", expect_sa=False, min_score=60)
    _add("neg13_normal_contract",
         "某建設公司承攬公共工程，依契約施工並通過驗收，款項依約支付。",
         "公共工程正常履約", expect_sa=False, min_score=60)
    _add("neg14_gossip_no_public",
         "某政治人物傳出婚外緋聞，但無任何證據或職權關聯，僅為八卦流言。",
         "未經證實緋聞", expect_sa=False, min_score=40)
    _add("neg15_town_news",
         "某鄉公所辦理村里活動補助，依規定申請、審核、核銷，金額小且公開。",
         "村里活動補助", expect_sa=False, min_score=60)
    _add("neg16_normal_staff",
         "議員服務處徵才助理，公告薪資、工作內容，多人應徵，正常聘用。",
         "服務處徵助理", expect_sa=False, min_score=60)
    _add("neg17_university_ok",
         "某議員論文通過學倫會審查，認定無抄襲，結案。",
         "學倫會認定無抄襲", expect_sa=False, min_score=40)
    _add("neg18_photo_meal",
         "縣長與企業界餐敘，合照流傳，無金流或業務關係說明。",
         "縣長與企業餐敘合照", expect_sa=False, min_score=60)
    _add("neg19_normal_river",
         "水利署例行巡查河川，發現違規即依法裁罰並公告。",
         "河川例行巡查", expect_sa=False, min_score=60)
    _add("neg20_old_outcome_dup",
         "去年某議員涉嫌弊案已獲不起訴處分，本篇只是轉貼當時新聞，無任何新事證。",
         "舊新聞轉貼不起訴", expect_sa=False, min_score=35)

    # ================= 10 条边界（Case1-10） =================
    _add("edge01_contact_only", "某建商最近跟議員辦公室聯繫。",
         "建商聯繫議員辦公室", expect_sa=False, min_score=40, note="Case1 低分")
    _add("edge02_builder_fee",
         f"某建商支付80萬元顧問費給{P}議員辦公室人員{A}，隨後辦公室人員幫助協調建照。附件包含匯款紀錄和LINE截圖。",
         "建商顧問費與協調建照", expect_sa=True, expect_cats=["A03", "A04"],
         patterns=["P05", "P19"], min_score=75, note="Case2 期望A/S")
    _add("edge03_sister_public_assistant", "某議員的胞姐是公費助理。",
         "議員胞姐為公費助理", expect_sa=False, min_score=45, note="Case3 不得高分")
    _add("edge04_sister_no_work",
         f"{P}議員的胞姐未實際上班，每月卻申報6萬元助理薪資，薪資入帳後全數領出，提款卡長期由辦公室主任{A}保管。",
         "胞姐未上班申報薪資", expect_sa=True, expect_cats=["A02"],
         patterns=["P01", "P02", "P03"], min_score=75, note="Case4 高優先")
    _add("edge05_legal_procure", "某廠商依法取得政府採購標案，過程公開。",
         "廠商依法得標", expect_sa=False, min_score=60, note="Case5 低分")
    _add("edge06_prebid_spec",
         f"招標公告前三週，承辦人員已經把需求書交給後來得標廠商{C2}，雙方招標前接觸，正式規格與該廠商此前提供資料高度一致，採購文件照廠商資料製作。",
         "公告前三週給需求書", expect_sa=True, expect_cats=["A06"],
         patterns=["P07", "P08"], min_score=75, note="Case6 高分")
    _add("edge07_vague_accuse", "有人舉報某民進黨政治人物貪污，但沒有具體事實與證據。",
         "空泛舉報貪污", expect_sa=False, min_score=45, note="Case7 低至中分")
    _add("edge08_no_prosecute_old",
         f"去年{P}議員涉嫌工程弊案，最後不起訴處分。本郵件只是轉發當年新聞報導，無新增內容。",
         "不起訴舊聞轉發", expect_sa=False, min_score=35, note="Case8 EX+P20")
    _add("edge09_new_evidence_after_ex",
         f"去年{P}議員一案獲不起訴處分，但本次郵件提供此前未曝光的銀行流水，顯示{C1}另匯入三百萬元到其服務處帳戶。",
         "不起訴後出現新金流證據", expect_sa=True, expect_cats=["A03"],
         patterns=["P19"], min_score=65, note="Case9 EX但新證據不得過濾")
    _add("edge10_boss_request",
         f"{A}主任傳訊息給承辦人：老闆很重視，資格想辦法，不要走公文，之後會回饋，不會虧待你。附件有該專案文件、公司名和對話原始記錄。",
         "老闆交代資格想辦法", expect_sa=True, expect_cats=["A03", "A06"],
         min_score=70, note="Case10 核心能力：無直接負面詞")

    with open(EXP_FILE, "w", encoding="utf-8") as f:
        for rec in EXPECTED:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"生成 {len(EXPECTED)} 条样本 -> {EML_DIR}")


if __name__ == "__main__":
    main()
