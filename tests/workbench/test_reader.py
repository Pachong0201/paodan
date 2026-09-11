# -*- coding: utf-8 -*-
"""V5.0 Sensitive Reader gates."""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi.testclient import TestClient

from app.dashboard.server import create_app
from app.storage.database import Database


def _seed(tmp_path):
    dbp = tmp_path / "r.db"
    db = Database(dbp)
    today = date.today().isoformat()
    db.execute(
        """INSERT INTO emails(email_id, subject, sender, body_text, message_id, source_path, processed_at)
           VALUES (?,?,?,?,?,?,?)""",
        ("reader1", "原始郵件標題 <script>alert(1)</script>", "secret.source@example.com",
         "RAW_MAIL_BODY_CANARY 完整郵件正文 <img src=x onerror=alert(1)> <img src=https://tracking.example.com/x>",
         "<secret-message@example.com>", "D:\\\\Secret\\\\raw.eml", f"{today}T08:00:00"))
    db.execute("INSERT INTO scores(email_id, final_score, priority, primary_track) VALUES (?,?,?,?)",
               ("reader1", 90.0, "S", "MIXED"))
    db.execute(
        """INSERT INTO attachments(email_id, filename, file_type, text, extraction_status)
           VALUES (?,?,?,?,?)""",
        ("reader1", "证据.pdf", "pdf", "ATTACHMENT_CANARY 提取文本", "success"))
    db.close()
    return create_app(dbp, import_staging_root=tmp_path / "staging")


def test_reader_shows_raw_body_and_escapes_xss(tmp_path):
    app = _seed(tmp_path)
    client = TestClient(app)
    r = client.get("/emails/reader1/reader")
    assert r.status_code == 200
    assert "RAW_MAIL_BODY_CANARY" in r.text
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in r.text
    assert "secret.source@example.com" not in r.text
    assert "<secret-message@example.com>" not in r.text
    assert "D:\\\\Secret\\\\raw.eml" not in r.text


def test_source_reveal_on_demand(tmp_path):
    app = _seed(tmp_path)
    client = TestClient(app)
    # 初始 HTML 不包含来源
    assert "secret.source@example.com" not in client.get("/emails/reader1/reader").text
    r = client.get("/emails/reader1/reader/source")
    assert r.status_code == 200
    assert r.json()["sender"] == "secret.source@example.com"
    assert "no-store" in r.headers.get("cache-control", "")


def test_attachment_text_and_path_traversal(tmp_path):
    app = _seed(tmp_path)
    client = TestClient(app)
    r = client.get("/emails/reader1/reader/attachments/1")
    assert r.status_code == 200
    assert "ATTACHMENT_CANARY" in r.text
    assert client.get("/emails/reader1/reader/attachments/999").status_code == 404
    assert client.get("/emails/../../etc/passwd/reader").status_code in (404, 400)


def test_reader_no_external_network(tmp_path):
    app = _seed(tmp_path)
    client = TestClient(app)
    with patch("requests.get", side_effect=AssertionError("network")) as rg, \
         patch("requests.post", side_effect=AssertionError("network")) as rp, \
         patch("httpx.get", side_effect=AssertionError("network")) as hg, \
         patch("httpx.post", side_effect=AssertionError("network")) as hp:
        assert client.get("/emails/reader1/reader").status_code == 200
        assert client.get("/emails/reader1/reader/source").status_code == 200
        assert client.get("/emails/reader1/reader/attachments/1").status_code == 200
        assert rg.call_count == 0 and rp.call_count == 0
        assert hg.call_count == 0 and hp.call_count == 0


# ----------------------------------------------------------------------
# 上一封 / 下一封导航
# ----------------------------------------------------------------------
def _seed_ordered(tmp_path):
    """三封邮件，按 优先级/评分 排序应为 s(90) > a(70) > d(10)。"""
    dbp = tmp_path / "nav.db"
    db = Database(dbp)
    rows = [("m_s", "S", 90.0, "2026-09-01T08:00:00"),
            ("m_a", "A", 70.0, "2026-09-01T09:00:00"),
            ("m_d", "D", 10.0, "2026-09-01T10:00:00")]
    for eid, prio, score, ts in rows:
        db.execute("""INSERT INTO emails(email_id, subject, body_text, processed_at)
                      VALUES (?,?,?,?)""", (eid, f"subject {eid}", "body", ts))
        db.execute("""INSERT INTO scores(email_id, final_score, priority, primary_track)
                      VALUES (?,?,?,?)""", (eid, score, prio, "POLITICAL"))
    db.close()
    return create_app(dbp, import_staging_root=tmp_path / "staging")


def _nav_block(html: str) -> str:
    m = re.search(r'<nav class="reader-nav">(.*?)</nav>', html, re.S)
    assert m, "reader nav block missing"
    return m.group(1)


def test_reader_navigation_follows_priority_order(tmp_path):
    app = _seed_ordered(tmp_path)
    client = TestClient(app)

    # 第一封 m_s(90)：没有上一封，下一封是 m_a
    first = _nav_block(client.get("/emails/m_s/reader").text)
    assert 'href="/emails/m_a/reader"' in first
    assert "下一封" in first
    assert 'class="disabled"' in first and "上一封" in first
    # m_s 之前没有别的邮件，所以 nav 里只应出现一个链接（指向 m_a）
    assert first.count('href="/emails/') == 1

    # 中间那封 m_a(70)：两侧都有
    mid = _nav_block(client.get("/emails/m_a/reader").text)
    assert 'href="/emails/m_s/reader"' in mid
    assert 'href="/emails/m_d/reader"' in mid
    assert 'class="disabled"' not in mid
    assert mid.count('href="/emails/') == 2

    # 最后一封 m_d(10)：没有下一封
    last = _nav_block(client.get("/emails/m_d/reader").text)
    assert 'href="/emails/m_a/reader"' in last
    assert 'class="disabled"' in last and "下一封" in last
    assert last.count('href="/emails/') == 1


def test_reader_neighbors_service(tmp_path):
    from app.workbench.reader_service import get_reader_neighbors
    app = _seed_ordered(tmp_path)
    from app.dashboard.db import connect_dashboard
    with connect_dashboard(app.state.db_path) as conn:
        assert get_reader_neighbors(conn, "m_s") == (None, "m_a")
        assert get_reader_neighbors(conn, "m_a") == ("m_s", "m_d")
        assert get_reader_neighbors(conn, "m_d") == ("m_a", None)
        assert get_reader_neighbors(conn, "missing") == (None, None)


# ----------------------------------------------------------------------
# 图片附件内联展示
# ----------------------------------------------------------------------
_PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 40)


def _seed_image(tmp_path, payload: bytes, filename: str = "photo.png",
                file_type: str = "png", email_id: str = "img1"):
    dbp = tmp_path / "img.db"
    cache = tmp_path / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    att_file = cache / f"{filename}"
    att_file.write_bytes(payload)
    db = Database(dbp)
    db.execute("""INSERT INTO emails(email_id, subject, body_text, processed_at)
                  VALUES (?,?,?,?)""", (email_id, "with image", "body", "2026-09-01T08:00:00"))
    db.execute("""INSERT INTO scores(email_id, final_score, priority, primary_track)
                  VALUES (?,?,?,?)""", (email_id, 80.0, "A", "POLITICAL"))
    db.execute("""INSERT INTO attachments(email_id, filename, file_type, text,
                                          extraction_status, cached_path)
                  VALUES (?,?,?,?,?,?)""",
               (email_id, filename, file_type, "", "success", str(att_file)))
    db.close()
    return create_app(dbp, import_staging_root=tmp_path / "staging")


def test_png_attachment_served_inline(tmp_path):
    app = _seed_image(tmp_path, _PNG)
    client = TestClient(app)
    page = client.get("/emails/img1/reader")
    assert page.status_code == 200
    assert "/emails/img1/reader/attachments/1/raw" in page.text
    r = client.get("/emails/img1/reader/attachments/1/raw")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == _PNG
    assert "no-store" in r.headers.get("cache-control", "")
    assert r.headers.get("x-content-type-options") == "nosniff"


def test_svg_attachment_is_never_served_inline(tmp_path):
    """SVG 可内嵌脚本，同源内联返回等于 XSS —— 必须拒绝。"""
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    app = _seed_image(tmp_path, svg, filename="evil.svg", file_type="svg")
    client = TestClient(app)
    assert client.get("/emails/img1/reader/attachments/1/raw").status_code == 404
    assert "/attachments/1/raw" not in client.get("/emails/img1/reader").text


def test_html_disguised_as_png_is_rejected(tmp_path):
    """伪装成 .png 的 HTML 必须按 magic bytes 被拒绝，而不是按扩展名放行。"""
    html = b"<html><script>alert(document.cookie)</script></html>"
    app = _seed_image(tmp_path, html, filename="trap.png", file_type="png")
    client = TestClient(app)
    r = client.get("/emails/img1/reader/attachments/1/raw")
    assert r.status_code == 404
    # 页面也不应渲染 <img>
    assert "/attachments/1/raw" not in client.get("/emails/img1/reader").text


def test_image_route_does_not_leak_filesystem_path(tmp_path):
    app = _seed_image(tmp_path, _PNG)
    client = TestClient(app)
    html = client.get("/emails/img1/reader").text
    assert str(tmp_path) not in html
    assert "cached_path" not in html


def test_missing_cached_file_yields_404(tmp_path):
    app = _seed_image(tmp_path, _PNG)
    (tmp_path / "cache" / "photo.png").unlink()
    client = TestClient(app)
    assert client.get("/emails/img1/reader/attachments/1/raw").status_code == 404
    assert "/attachments/1/raw" not in client.get("/emails/img1/reader").text


def test_image_route_requires_owning_email(tmp_path):
    """不能用别的 email_id 去取他人附件。"""
    app = _seed_image(tmp_path, _PNG)
    client = TestClient(app)
    assert client.get("/emails/other/reader/attachments/1/raw").status_code == 404



# ----------------------------------------------------------------------
# 端到端：真实 EML 内嵌 PNG → 导入 → 跑任务 → Reader 内联展示
# ----------------------------------------------------------------------
def _png_1x1() -> bytes:
    """生成一个结构合法的 1x1 PNG（避免依赖 PIL）。"""
    import struct
    import zlib

    def chunk(typ: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00" + b"\xff\x00\x00")
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


def _eml_with_png(png: bytes) -> bytes:
    from email import policy
    from email.message import EmailMessage

    m = EmailMessage(policy=policy.default)
    m["Subject"] = "含图片附件的檢舉"
    m["From"] = "source@example.com"
    m["To"] = "tips@example.com"
    m["Date"] = "Mon, 01 Sep 2026 08:00:00 +0800"
    m.set_content("停水投诉 同一区停水反复5次 500户 1999未改善")
    m.add_attachment(png, maintype="image", subtype="png", filename="evidence.png")
    return m.as_bytes()


def test_end_to_end_png_attachment_reaches_reader(tmp_path):
    import time as _time

    dbp = tmp_path / "e2e.db"
    Database(dbp).close()
    app = create_app(dbp, import_staging_root=tmp_path / "staging")
    client = TestClient(app)
    png = _png_1x1()

    r = client.post("/import", data={"csrf_token": app.state.csrf_token},
                    files={"file": ("m.eml", _eml_with_png(png), "message/rfc822")})
    assert r.status_code == 200
    with Database(dbp) as db:
        batch = db.query("SELECT * FROM import_batches")[0]
    start = client.post("/jobs/start", data={
        "csrf_token": app.state.csrf_token,
        "import_id": batch["import_id"],
        "profile_id": "template",
    })
    assert start.status_code == 200
    job_id = start.json()["job_id"]
    status = None
    for _ in range(120):
        status = client.get(f"/jobs/{job_id}/status").json()
        if status["status"] in ("COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"):
            break
        _time.sleep(0.15)
    assert status["status"] == "COMPLETED", status

    with Database(dbp) as db:
        eid = db.query("SELECT email_id FROM emails")[0]["email_id"]
        atts = db.query("SELECT id, filename, cached_path FROM attachments WHERE email_id=?", (eid,))
    assert atts, "附件未入库"
    assert atts[0]["filename"] == "evidence.png"

    page = client.get(f"/emails/{eid}/reader")
    assert page.status_code == 200
    raw_url = f"/emails/{eid}/reader/attachments/{atts[0]['id']}/raw"
    assert raw_url in page.text, "Reader 未渲染图片附件"

    raw = client.get(raw_url)
    assert raw.status_code == 200
    assert raw.headers["content-type"] == "image/png"
    assert raw.content == png, "返回的图片字节与原始附件不一致"
