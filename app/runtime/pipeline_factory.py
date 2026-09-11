"""CLI/Workbench 共用 Pipeline 构造入口。"""
from __future__ import annotations

from typing import Any, Optional

from ..config import LLM_BASE_URL, LLM_MODE, LLM_MODEL
from ..rules.config_loader import RuleConfig
from ..storage.database import Database
from ..workbench.models import JobRuntimeConfig
from ..workbench.pipeline_factory import PipelineFactory, PipelineFactoryError


def build_cli_pipeline(cfg: RuleConfig, db: Optional[Database], args: Any):
    """根据 CLI 参数构造 ScreeningPipeline；行为与旧 CLI 兼容。"""
    llm_mode = args.llm_mode or LLM_MODE
    llm_enabled = not bool(getattr(args, "no_llm", False))
    runtime = JobRuntimeConfig(
        llm_enabled=llm_enabled,
        llm_mode=llm_mode,
        llm_profile_id=("off" if not llm_enabled else
                        ("primary" if llm_mode == "api" else "template")),
        llm_model=LLM_MODEL,
        llm_base_url=LLM_BASE_URL,
        release_advisor_enabled=bool(getattr(args, "enable_release_advisor", False)),
        named_advisor_enabled=bool(getattr(args, "enable_named_advisor", False)),
    )
    pipeline = PipelineFactory.create(
        cfg, runtime, db=db,
        llm_trigger_score=getattr(args, "llm_trigger_score", None),
        fallback_on_llm_error=True,
    )
    pipeline.reprocess = bool(getattr(args, "reprocess", False))
    pipeline.rescore = bool(getattr(args, "rescore", False))
    pipeline.reanalyze = bool(getattr(args, "reanalyze", False))
    return pipeline
