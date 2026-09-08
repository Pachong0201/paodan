#!/usr/bin/env python3
"""V4.1 repository secret/PII gate.

检查 tracked files：
- API key patterns
- private email samples
- raw mailbox absolute paths
- bank account-like canaries
- real .eml under data/

测试夹具（tests/fixtures、tests/*.py）允许 synthetic canary，不参与真实仓库泄漏判定。
"""
from __future__ import annotations

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
    for path in files:
        if not path.exists():
            # index 中可能残留已移动/删除的旧路径；以 working tree 实际文件为准。
            continue
        rel = path.relative_to(ROOT).as_posix()
        # 公开仓库禁止任何 data/ 下真实 .eml（测试夹具请放 tests/fixtures）
        if rel.startswith("data/") and rel.lower().endswith(".eml"):
            issues.append(f"{rel}: real .eml under data/ (must move to tests/fixtures)")
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
