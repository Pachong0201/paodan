# -*- coding: utf-8 -*-
"""解析器测试：EML/PDF/DOCX/XLSX/CSV/TXT。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.parsers import parse_attachment, parse_eml

FIX = Path(__file__).resolve().parent / "fixtures"


def _mk_eml(tmp_path, body, subject="测试主旨", att=None):
    from email import policy
    from email.message import EmailMessage
    msg = EmailMessage(policy=policy.default)
    msg["Subject"] = subject
    msg["From"] = "a@b.tw"
    msg["To"] = "tips@news.tw"
    msg["Message-ID"] = "<xyz@b.tw>"
    msg["Date"] = "Mon, 01 Sep 2026 08:00:00 +0800"
    msg.set_content(body)
    if att:
        msg.add_attachment(att, maintype="application", subtype="octet-stream",
                           filename="a.pdf")
    p = tmp_path / "t.eml"
    p.write_bytes(msg.as_bytes())
    return p


def test_eml_basic_parse(tmp_path):
    p = _mk_eml(tmp_path, "这是正文。检举建商付顾问费。")
    doc = parse_eml(p)
    assert doc.subject == "测试主旨"
    assert doc.sender.startswith("a@b.tw")
    assert doc.message_id == "xyz@b.tw"
    assert "检举" in doc.body_text
    assert doc.body_hash


def test_eml_html_to_text(tmp_path):
    html = "<html><body><h1>标题</h1><p>第一段</p><p>第二段</p></body></html>"
    p = _mk_eml(tmp_path, html, subject="HTML信")
    # 覆盖为 html-only 邮件
    from email import policy
    from email.message import EmailMessage
    msg = EmailMessage(policy=policy.default)
    msg["Subject"] = "html"
    msg["From"] = "a@b"
    msg.add_alternative(html, subtype="html")
    p2 = tmp_path / "h.eml"
    p2.write_bytes(msg.as_bytes())
    doc = parse_eml(p2)
    assert "第一段" in doc.body_text or "标题" in doc.body_text


def test_pdf_text_layer(tmp_path):
    import fitz
    pdf = tmp_path / "doc.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "汇款记录: 80万元顾问费")
    doc.save(pdf)
    doc.close()
    att = parse_attachment(pdf, ocr_enabled=False)
    assert att.extraction_status in ("success", "partial")
    assert "80" in att.text or "汇款" in att.text


def test_pdf_no_text_layer_fallback(tmp_path):
    """空白 PDF：文本层为空 -> partial（OCR 未启用）。"""
    import fitz
    pdf = tmp_path / "blank.pdf"
    d = fitz.open()
    d.new_page()
    d.save(pdf)
    d.close()
    att = parse_attachment(pdf, ocr_enabled=False)
    assert att.extraction_status in ("partial", "failed")
    assert att.warnings


def test_docx_parse(tmp_path):
    from docx import Document
    p = tmp_path / "d.docx"
    d = Document()
    d.add_heading("签呈", level=1)
    d.add_paragraph("本府委托标案需照厂商资料制作规格。")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "厂商"
    t.cell(0, 1).text = "永泰营造"
    d.save(p)
    att = parse_attachment(p)
    assert att.text
    assert "永泰营造" in att.text or any(x["value"] == "永泰营造" for x in att.tables)
    assert att.extraction_status == "success"


def test_xlsx_tables(tmp_path):
    from openpyxl import Workbook
    p = tmp_path / "x.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "流水"
    ws.append(["日期", "金额", "备注"])
    ws.append(["2026-01-05", 800000, "顾问费"])
    wb.save(p)
    att = parse_attachment(p)
    assert att.text and "顾问费" in att.text
    assert any(t["sheet"] == "流水" and t["value"] == "顾问费" for t in att.tables)


def test_csv_parse(tmp_path):
    p = tmp_path / "c.csv"
    p.write_text("公司,金额\n大川建设,800000\n", encoding="utf-8-sig")
    att = parse_attachment(p)
    assert "大川建设" in att.text
    assert att.tables


def test_txt_parse(tmp_path):
    p = tmp_path / "t.txt"
    p.write_text("对话记录：老板很重视。", encoding="utf-8")
    att = parse_attachment(p)
    assert "老板很重视" in att.text


def test_corrupt_pdf_no_crash(tmp_path):
    p = tmp_path / "bad.pdf"
    p.write_bytes(b"%PDF-1.4 broken garbage not a real pdf")
    att = parse_attachment(p, ocr_enabled=False)
    # 不崩溃，状态为 failed/partial
    assert att.extraction_status in ("failed", "partial", "success")


def test_unsupported_type(tmp_path):
    p = tmp_path / "a.zip"
    p.write_bytes(b"PK\x03\x04 fake")
    att = parse_attachment(p)
    assert att.extraction_status == "skipped"
