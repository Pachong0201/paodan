# -*- coding: utf-8 -*-
"""SecurityPolicy / destination classification / config precedence."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.security.classifier import Destination, DestinationClassifier, classify_destination
from app.security.models import SecurityBlockedError
from app.security.policy import load_security_policy


def test_default_policy_structured_only():
    p = load_security_policy(config_dir="config")
    assert p.privacy_level == "STRUCTURED_ONLY"
    assert p.external_llm_enabled is True
    assert "api.openai.com" in p.allowed_hosts
    assert p.https_required is True


def test_localhost_is_local():
    p = load_security_policy(config_dir="config")
    assert classify_destination("http://127.0.0.1:11434/v1", p) == Destination.LOCAL
    assert classify_destination("http://localhost:11434/v1", p) == Destination.LOCAL
    assert DestinationClassifier(p).classify("http://127.0.0.1:11434/v1") == Destination.LOCAL


def test_unknown_and_http_external_blocked():
    p = load_security_policy(config_dir="config")
    for url in ("https://evil.example.com/v1", "http://api.openai.com/v1"):
        try:
            DestinationClassifier(p).classify(url)
            assert False, url
        except SecurityBlockedError:
            pass


def test_env_overrides_yaml(monkeypatch):
    monkeypatch.setenv("SECURITY_PRIVACY_LEVEL", "REDACTED_SNIPPETS")
    monkeypatch.setenv("EXTERNAL_LLM_ALLOWED_HOSTS", "api.openai.com,example.org")
    p = load_security_policy(config_dir="config")
    assert p.privacy_level == "REDACTED_SNIPPETS"
    assert "example.org" in p.allowed_hosts


def test_allow_raw_external_env_is_ignored(monkeypatch):
    monkeypatch.setenv("ALLOW_RAW_EXTERNAL_LLM", "1")
    p = load_security_policy(config_dir="config")
    assert p.privacy_level == "STRUCTURED_ONLY"
    # 不存在任何 raw bypass 字段
    assert not hasattr(p, "allow_raw_external_llm")
