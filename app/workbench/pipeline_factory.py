"""V5.0 PipelineFactory：CLI 与 Workbench 共用同一套 Pipeline 构造逻辑。"""
from __future__ import annotations

import os
from typing import Any, Optional

from ..config import LLM_TIMEOUT, NEWS_SIGNAL_DIR
from ..llm.client import LLMClient, LLMError
from ..llm.screener import LLMScreener
from ..named_channel.advisor import NamedChannelAdvisor
from ..pipeline.screening_pipeline import ScreeningPipeline
from ..release_advisor.llm_advisor import ReleaseAdvisor
from ..rules.config_loader import RuleConfig
from ..security.policy import load_security_policy
from ..storage.database import Database
from .models import JobRuntimeConfig


class PipelineFactoryError(RuntimeError):
    pass


def build_llm_client(runtime_config: JobRuntimeConfig, policy=None) -> LLMClient:
    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not api_key:
        raise PipelineFactoryError("模型未配置 API Key")
    base_url = runtime_config.llm_base_url or os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    model = runtime_config.llm_model or os.getenv("LLM_MODEL", "gpt-4o-mini")
    try:
        return LLMClient(api_key, base_url, model, timeout=LLM_TIMEOUT,
                         policy=policy or load_security_policy())
    except LLMError as exc:
        raise PipelineFactoryError(str(exc)) from exc


class PipelineFactory:
    """集中构造 ScreeningPipeline；不修改全局模块变量。"""

    @staticmethod
    def create(config: RuleConfig, runtime_config: JobRuntimeConfig,
               db: Optional[Database] = None) -> ScreeningPipeline:
        policy = load_security_policy()
        mode = runtime_config.llm_mode if runtime_config.llm_enabled else "template"
        if mode not in ("template", "api"):
            mode = "template"

        screener = LLMScreener(config, mode="template")
        release_advisor = None
        named_advisor = None

        if runtime_config.llm_enabled and mode == "api":
            client = build_llm_client(runtime_config, policy)
            screener.client = client
            screener.mode = "api"
        else:
            screener.client = None
            screener.mode = "template"

        if runtime_config.release_advisor_enabled:
            release_advisor = ReleaseAdvisor(config, mode="template", allow_llm=False)
            if runtime_config.llm_enabled and mode == "api":
                release_advisor.client = screener.client
                release_advisor.mode = "api"
                release_advisor.allow_llm = True
            else:
                release_advisor.allow_llm = False

        if runtime_config.named_advisor_enabled:
            named_advisor = NamedChannelAdvisor(mode="template", allow_llm=False)
            if runtime_config.llm_enabled and mode == "api":
                named_advisor.client = screener.client
                named_advisor.mode = "api"
                named_advisor.allow_llm = True
            else:
                named_advisor.allow_llm = False

        allow_llm = bool(runtime_config.llm_enabled)
        return ScreeningPipeline(
            config, db=db, llm_screener=screener,
            allow_llm=allow_llm,
            release_advisor=release_advisor,
            named_advisor=named_advisor,
        )

    @staticmethod
    def load_rule_config() -> RuleConfig:
        return RuleConfig(NEWS_SIGNAL_DIR).load_all()
