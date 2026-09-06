# -*- coding: utf-8 -*-
"""追加生成即时爆料类样本（丰富 V3 媒体多样性，临时生成器）。"""
from email import policy
from email.message import EmailMessage
from pathlib import Path

FIX = Path(__file__).resolve().parent
EML_DIR = FIX / "release_emails"


def _mk(rid, subject, text):
    msg = EmailMessage(policy=policy.default)
    msg["Subject"] = subject
    msg["From"] = "whistle@example.tw"
    msg["To"] = "tips@newspaper.tw"
    msg["Date"] = "Tue, 01 Sep 2026 09:30:00 +0800"
    msg["Message-ID"] = f"<{rid}@release-test>"
    msg.set_content(text)
    (EML_DIR / f"{rid}.eml").write_bytes(msg.as_bytes())
    print("生成", rid)


# A13 立委酒后冲突：即时新闻场景（ETtoday/三立/自由时报类）
_mk("rc43_lawmaker_fight",
    "立委酒吧衝突影片外流",
    "民進黨立委張姓委員上週在台北酒吧與民眾爆發肢體衝突，現場影片被拍下，"
    "畫面顯示委員動手推擠並辱罵對方。檢舉人附上完整影片檔案與時間地點。"
    "張委員事後否認動手。")

# A11 政党工读生第一人称实名 Threads 爆料
_mk("rc44_party_staff_threads",
    "黨工實名Threads揭性騷",
    "我是民進黨某縣黨部工讀生，我遭到主管多次性騷擾，言語與肢體都有。"
    "我願意實名發聲，也已經在個人社群寫好長文，希望媒體協助查證並報導。"
    "附件有我與主管的LINE對話截圖。")

# A14 艺人/公众人物婚外偷拍影片（壹苹/CTWANT/镜周刊场景）
_mk("rc45_public_figure_affair",
    "公眾人物婚外情影片",
    "某知名政治評論員遭偷拍與女性進出旅館，影片與照片齊全。"
    "其妻已委任律師，評論員本人否認。影片經初步檢視非合成。")

# A07 消防协会补助诈领（立委助理举发-即时媒体）
_mk("rc46_fire_association_subsidy",
    "消防協會補助詐領",
    "檢舉：某縣消防協會以人頭會員重複申請補助款，三年內詐領上百萬元，"
    "附件有申請名冊與核銷單據。協會理事長為民進黨籍前議員。")

# A10 村里长选举幽靈人口（联合报/即时）
_mk("rc47_ghost_voter",
    "村里長選舉幽靈人口",
    "年底村里長選舉，某里長候選人將十多名親友戶籍遷入選區，"
    "疑似幽靈人口影響選舉結果。附件有戶籍謄本與遷入紀錄。")

print("完成")
