# -*- coding: utf-8 -*-
"""OutboundGuard 递归 key 检查 + 最终 body 扫描。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.security.outbound_guard import OutboundGuard
from app.security.policy import load_security_policy


def test_forbidden_keys_block_recursively():
    g = OutboundGuard(load_security_policy(config_dir="config"))
    assert not g.check({"body_text": "x"}).allowed
    assert not g.check({"metadata": {"sender_email": "x"}}).allowed
    assert not g.check({"items": [{"api_key": "x"}]}).allowed
    assert g.check({"max_tokens": 100, "messages": [{"content": "safe"}]}).allowed


def test_canary_content_blocks_final_scan():
    g = OutboundGuard(load_security_policy(config_dir="config"))
    assert not g.check({"safe_snippets": ["RAW_PRIVATE_CANARY_9F72C31A"]}).allowed
    assert not g.final_scan('{"x":"ATTACHMENT_PRIVATE_CANARY_E817AC21"}').allowed


def test_pii_content_blocks():
    g = OutboundGuard(load_security_policy(config_dir="config"))
    assert not g.check({"note": "secret.source.2026@example.com"}).allowed
    assert not g.check({"note": "812345678901234567"}).allowed
