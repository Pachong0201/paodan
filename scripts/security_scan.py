#!/usr/bin/env python3
"""V4.1 repository secret/PII gate.

检查 tracked files：
- API key patterns
- private email samples
- raw mailbox absolute paths
- bank account-like canaries
- real .eml under data/
- all tracked .eml must be synthetic (header or tests/fixtures/manifest.json SHA256)

测试夹具（tests/fixtures、tests/*.py）允许 synthetic canary，不参与真实仓库泄漏判定。
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

API_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")
PRIVATE_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@(?!example\.(?:com|org|net|local)\b)[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
RAW_MAILBOX_RE = re.compile(r"(?:/home/[^\s\"'<>]+|/Users/[^\s\"'<>]+|[A-Za-z]:\\Users\\[^\s\"'<>]+)")
BANK_CANARY_RE = re.compile(r"\b812345678901234567\b")
KNOWN_CANARY_RE = re.compile(r"\b(?:RAW|ATTACHMENT)_PRIVATE_CANARY_[A-Z0-9]+\b")
SYNTHETIC_HEADER_RE = re.compile(r"x-paodan-synthetic-fixture\s*:\s*true", re.IGNORECASE)
EXTERNAL_URL_RE = re.compile(r"https?://", re.IGNORECASE)
DASHBOARD_FORBIDDEN_IN_TEMPLATES = ("source_path", "cached_path", "body_text", "combined_text")
MANIFEST_FILE = ROOT / "tests" / "fixtures" / "manifest.json"


def load_synthetic_manifest() -> dict:
    try:
        data = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(data, dict) and isinstance(data.get("files"), dict):
        return {str(k): str(v) for k, v in data["files"].items()}
    if isinstance(data, dict):
        return {str(k): str(v) for k, v in data.items()}
    return {}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def is_synthetic_eml(path: Path, manifest: dict) -> tuple[bool, str]:
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:4096]
    except Exception:
        head = ""
    if SYNTHETIC_HEADER_RE.search(head):
        return True, "header"
    rel = path.relative_to(ROOT).as_posix()
    expected = manifest.get(rel)
    if expected:
        try:
            if sha256_file(path) == expected:
                return True, "manifest"
        except Exception:
            pass
    return False, ""


def tracked_files() -> list[Path]:
    try:
        out = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
        names = [n for n in out.decode("utf-8", errors="replace").split("\x00") if n]
        return [ROOT / n for n in names]
    except Exception:
        ignored_parts = {".git", ".venv", "__pycache__", ".commandcode", "logs"}
        ignored_prefixes = ("data/inbox/", "data/reports/", "data/review_queue/",
                            "data/processed/", "data/user_uploads/")
        out = []
        for p in ROOT.rglob("*"):
            if not p.is_file() or any(part in ignored_parts for part in p.parts):
                continue
            rel = p.relative_to(ROOT).as_posix()
            if rel.startswith(ignored_prefixes):
                continue
            out.append(p)
        return out


def is_test_fixture(path: Path) -> bool:
    rel = path.relative_to(ROOT).as_posix()
    return rel.startswith("tests/") or rel.startswith(".github/") or rel.startswith("scripts/")


def main() -> int:
    issues: list[str] = []
    files = tracked_files()
    # 即使文件尚未 git add，tests/fixtures 下新增的 .eml 也必须可证明 synthetic。
    try:
        fixture_dir = ROOT / "tests" / "fixtures"
        if fixture_dir.exists():
            existing = {p.resolve() for p in files if p.exists()}
            for p in fixture_dir.rglob("*.eml"):
                if p.resolve() not in existing:
                    files.append(p)
    except Exception:
        pass
    manifest = load_synthetic_manifest()

    # Dashboard templates/static 不得引用公网资源或直接渲染敏感字段。
    dashboard_dir = ROOT / "app" / "dashboard"
    for p in [dashboard_dir / "templates", dashboard_dir / "static"]:
        if not p.exists():
            continue
        for f in p.rglob("*"):
            if not f.is_file():
                continue
            rel = f.relative_to(ROOT).as_posix()
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if EXTERNAL_URL_RE.search(text):
                issues.append(f"{rel}: dashboard asset contains external URL")
            if p.name == "templates":
                for token in DASHBOARD_FORBIDDEN_IN_TEMPLATES:
                    if token in text:
                        issues.append(f"{rel}: dashboard template contains forbidden raw field token {token}")
    for path in files:
        if not path.exists():
            # index 中可能残留已移动/删除的旧路径；以 working tree 实际文件为准。
            continue
        rel = path.relative_to(ROOT).as_posix()
        # 所有 tracked .eml 都必须可证明是 synthetic。
        if rel.lower().endswith(".eml"):
            if rel.startswith("data/"):
                issues.append(f"{rel}: real .eml under data/ (must move to tests/fixtures)")
                continue
            if rel.startswith("tests/fixtures/"):
                ok, reason = is_synthetic_eml(path, manifest)
                if not ok:
                    issues.append(f"{rel}: fixture .eml lacks X-Paodan-Synthetic-Fixture: true or manifest SHA256")
                continue
            issues.append(f"{rel}: .eml outside tests/fixtures/ is not allowed")
            continue
        if is_test_fixture(path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for label, regex in (("API key", API_KEY_RE), ("private email", PRIVATE_EMAIL_RE),
                             ("raw mailbox path", RAW_MAILBOX_RE),
                             ("bank account canary", BANK_CANARY_RE),
                             ("private canary", KNOWN_CANARY_RE)):
            for m in regex.finditer(text):
                issues.append(f"{rel}: {label} pattern found")
                break
    if issues:
        print("SECURITY SCAN FAIL")
        for issue in issues:
            print("  - " + issue)
        return 1
    print(f"SECURITY SCAN PASS ({len(files)} tracked files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
