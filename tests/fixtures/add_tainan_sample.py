# -*- coding: utf-8 -*-
"""追加生成台南光电场景样本（V3 location-aware 测试用）。"""
from email import policy
from email.message import EmailMessage
from pathlib import Path

FIX = Path(__file__).resolve().parent
EML = FIX / "release_emails" / "rc_tainan_solar.eml"
if EML.exists():
    print("样本已存在，跳过")
else:
    msg = EmailMessage(policy=policy.default)
    msg["Subject"] = "台南光電案場股權爭議"
    msg["From"] = "whistle@example.tw"
    msg["To"] = "tips@newspaper.tw"
    msg["Date"] = "Tue, 01 Sep 2026 09:30:00 +0800"
    msg["Message-ID"] = "<rc_tainan_solar@release-test>"
    msg.set_content(
        "檢舉：臺南市麻豆區太陽光電案場開發，五家關係企業交叉持股、層層轉匯分潤，"
        "林文傑議員胞兄持股其中兩家，市府審查時議員施壓加速饋線核准。"
        "附件有股權結構表與匯款紀錄，涉及多位台南在地地主。"
    )
    EML.write_bytes(msg.as_bytes())
    print("生成 ->", EML)
