# -*- coding: utf-8 -*-
"""V5.0 LLM Profiles / PipelineFactory gates."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.workbench.llm_profiles import (LLMProfileError, get_profile, load_llm_profiles,
                                        profile_status)
from app.workbench.models import JobRuntimeConfig
from app.workbench.pipeline_factory import PipelineFactory


def test_default_profiles_present():
    profiles = load_llm_profiles()
    assert {"off", "template", "primary", "local"} <= set(profiles)
    assert profiles["off"].type == "off"
    assert profiles["template"].type == "template"


def test_api_key_not_allowed_in_profile(tmp_path):
    p = tmp_path / "llm_profiles.yaml"
    p.write_text(yaml.safe_dump({"profiles": {"bad": {"name": "x", "type": "api",
                                                     "model": "m", "base_url": "https://api.openai.com/v1",
                                                     "api_key": "sk-secret"}}}), encoding="utf-8")
    with pytest.raises(LLMProfileError):
        load_llm_profiles(p)


def test_api_profile_requires_key(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    status = profile_status("primary")
    assert status["available"] is False
    assert "API Key" in status["message"]


def test_api_profile_with_key_and_allowlist(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-test-token")
    status = profile_status("primary")
    assert status["available"] is True


def test_pipeline_factory_off_and_template_no_network(monkeypatch):
    import app.workbench.pipeline_factory as pf
    config = PipelineFactory.load_rule_config()
    with patch("requests.post", side_effect=AssertionError("network")) as post:
        pipe = PipelineFactory.create(config, JobRuntimeConfig(
            llm_enabled=False, llm_mode="template", llm_profile_id="off"))
        assert pipe.allow_llm is False
        assert pipe.llm_screener.mode == "template"
        assert post.call_count == 0
        pipe2 = PipelineFactory.create(config, JobRuntimeConfig(
            llm_enabled=True, llm_mode="template", llm_profile_id="template"))
        assert pipe2.allow_llm is True
        assert post.call_count == 0


def test_pipeline_factory_api_mode_uses_injected_client(monkeypatch):
    import app.workbench.pipeline_factory as pf
    calls = {}

    class FakeClient:
        is_external = True
        policy = None

    def fake_client(api_key, base_url, model, timeout=60, policy=None):
        calls.update(api_key=api_key, base_url=base_url, model=model)
        return FakeClient()

    monkeypatch.setenv("LLM_API_KEY", "sk-test-token")
    monkeypatch.setattr(pf, "LLMClient", fake_client)
    config = PipelineFactory.load_rule_config()
    pipe = PipelineFactory.create(config, JobRuntimeConfig(
        llm_enabled=True, llm_mode="api", llm_profile_id="primary",
        llm_model="gpt-test", llm_base_url="https://api.openai.com/v1"))
    assert pipe.allow_llm is True
    assert pipe.llm_screener.mode == "api"
    assert getattr(pipe.llm_screener.client, "is_external", False) is True
    assert calls["model"] == "gpt-test"


def test_local_profile_no_key_available(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    status = profile_status("local")
    assert status["available"] is True
    assert "API Key" not in status.get("message", "")


def test_unknown_external_host_and_http_blocked(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-test-token")
    monkeypatch.setenv("LLM_BASE_URL", "https://evil.example.com/v1")
    assert profile_status("primary")["available"] is False
    monkeypatch.setenv("LLM_BASE_URL", "http://api.openai.com/v1")
    assert profile_status("primary")["available"] is False


def test_build_llm_client_local_optional_key_and_external_required(monkeypatch):
    from app.workbench.pipeline_factory import PipelineFactoryError, build_llm_client

    monkeypatch.delenv("LLM_API_KEY", raising=False)
    local = build_llm_client(JobRuntimeConfig(
        llm_enabled=True, llm_mode="api", llm_profile_id="local",
        llm_model="local-model", llm_base_url="http://127.0.0.1:11434/v1"))
    assert local.api_key == ""
    assert local.is_external is False

    with pytest.raises(PipelineFactoryError):
        build_llm_client(JobRuntimeConfig(
            llm_enabled=True, llm_mode="api", llm_profile_id="primary",
            llm_model="gpt-test", llm_base_url="https://api.openai.com/v1"))
