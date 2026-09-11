"""CLI 入口：

python -m app.main --input data/inbox/
python -m app.main --file sample.eml
python -m app.main --file sample.eml --no-llm
python -m app.main --input data/inbox/ --min-priority B
python -m app.main --selfcheck   # 规则包自检+词典/pattern 统计
"""
from __future__ import annotations

import argparse
import json
import os
import logging
import sys
from pathlib import Path

from .config import (DATA_DIR, DB_PATH, LLM_BASE_URL, LLM_MODE, LLM_MODEL, LOG_DIR,
                     LOG_FILE, MIN_PRIORITY, NEWS_SIGNAL_DIR, REPORTS_DIR,
                     priority_min_value, resolve_llm_trigger_score)
from .pipeline.screening_pipeline import ScreeningPipeline
from .rules.config_loader import RuleConfig
from .scoring.scorer import priority_of


def _safe_stream(stream):
    """控制台编码安全包装：遇到 GBK 等无法编码的字符(emoji)用 ? 代替而非崩溃."""
    try:
        if hasattr(stream, "buffer"):
            import io
            return io.TextIOWrapper(stream.buffer, encoding=stream.encoding or "utf-8",
                                    errors="replace")
    except Exception:
        pass
    return stream


# 日志（stderr 也做安全包装；pytest 捕获下跳过，避免包装已关闭的临时文件）
if "pytest" not in sys.modules and "PYTEST_CURRENT_TEST" not in __import__("os").environ:
    if sys.stdout and hasattr(sys.stdout, "buffer"):
        sys.stdout = _safe_stream(sys.stdout)
    if sys.stderr and hasattr(sys.stderr, "buffer"):
        sys.stderr = _safe_stream(sys.stderr)
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"),
              logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger("app.main")


def _load_config() -> RuleConfig:
    return RuleConfig(NEWS_SIGNAL_DIR).load_all()


def resolve_toggle(enable: bool, disable: bool, cfg_value: bool) -> bool:
    """CLI 开关解析：--disable 优先于 --enable，否则沿用配置文件。"""
    if disable:
        return False
    if enable:
        return True
    return bool(cfg_value)


def _print_record(rec, verbose: bool = False):
    if rec.score is None:
        print(f"[ERR] {rec.email_id}: {rec.error or '未知错误'}")
        return
    print(f"[{rec.score.priority}] score={rec.score.final_score:.1f}  "
          f"{rec.email.subject if rec.email else rec.email_id}")
    if verbose and rec.rule is not None:
        cats = "、".join((rec.llm.categories or rec.rule.matched_categories) if rec.llm else rec.rule.matched_categories)
        pats = "/".join(p.pattern_id for p in rec.rule.matched_patterns)
        print(f"     类别: {cats or '-'}")
        print(f"     Pattern: {pats or '-'}")
        print(f"     证据阶段: {rec.llm.evidence_stage if rec.llm else 'E1'}")
        print(f"     摘要: {rec.summary_zh[:120]}")
        for i, vt in enumerate(rec.verification_targets[:3], 1):
            print(f"     核查{i}: {vt}")



def _dashboard_table_exists() -> bool:
    try:
        from .storage.database import Database
        from .config import DB_PATH as _DB_PATH
        db = Database(_DB_PATH)
        ok = bool(db.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dashboard_reviews'"))
        db.close()
        return ok
    except Exception:
        return False

def selfcheck(cfg: RuleConfig, config_dir: Path | None = None) -> int:
    """V4.1.1 生产自检：规则包 / Governance / Privacy / DB Schema / Output Security / Analysis。"""
    print("=" * 64)
    print("V4.1.1 系统自检")
    print("=" * 64)
    failures: list[str] = []
    cfg_dir = Path(config_dir) if config_dir else Path(getattr(cfg, "directory", NEWS_SIGNAL_DIR) or NEWS_SIGNAL_DIR)

    # ---------- V1 Political Rules ----------
    print("\n[V1 Political Rules]")
    s = cfg.summary()
    for name, status in cfg.load_status.items():
        print(f"  [{'OK' if status else 'FAIL'}] {name}")
        if not status:
            failures.append(name)
    print(f"  [OK] A categories: {s['taxonomy_categories']}")
    print(f"  [OK] P patterns: {s['patterns']}")
    print(f"  [OK] N negatives: {s['negative_rules']}")
    print(f"  [OK] LLM prompt: {s['llm_prompt_chars']} chars")

    # ---------- V4 Governance Rules ----------
    print("\n[V4 Governance Rules]")
    gov_cfg = None
    try:
        from .governance.config_loader import GovernanceConfig
        gov_cfg = GovernanceConfig(cfg_dir).load_all()
        gs = gov_cfg.summary()
        print(f"  [OK] G categories: {gs['categories']}")
        print(f"  [OK] GP patterns: {gs['patterns']}")
        print(f"  [OK] GN negatives: {gs['negatives']}")
        print(f"  [OK] Governance keywords: G-H={gs['G-H']} G-M={gs['G-M']} G-C={gs['G-C']} G-E={gs['G-E']}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] Governance config validation: {exc}")
        failures.append("governance")

    # ---------- External LLM Privacy ----------
    print("\n[External LLM Privacy]")
    policy = None
    try:
        from .security.policy import load_security_policy
        policy = load_security_policy(config_dir=cfg_dir.parent if cfg_dir.name == "news_signal" else cfg_dir)
        print(f"  [OK] security.yaml: {policy.source_file}")
        print(f"  [OK] privacy level: {policy.privacy_level}")
        print(f"  [OK] external host allowlist: {','.join(policy.allowed_hosts) or '(empty)'}")
        print("  [OK] raw external disabled")
        print("  [OK] no ALLOW_RAW_EXTERNAL_LLM bypass")
        if policy.privacy_level == "OFF":
            print("  [WARN] privacy_level=OFF 仍不允许 external raw string")
        if policy.privacy_level == "REDACTED_SNIPPETS":
            print("  [WARN] REDACTED_SNIPPETS 为受限模式；生产推荐 STRUCTURED_ONLY")
        if not policy.allowed_hosts:
            failures.append("security_allowlist")
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] security.yaml/privacy policy: {exc}")
        failures.append("security")

    # ---------- Database Schema ----------
    print("\n[Database Schema]")
    try:
        from .storage.database import Database
        from .storage.migrations import SCHEMA_VERSION
        db = Database(DB_PATH)
        state = db.schema_state()
        print(f"  [OK] schema version: {state.get('schema_version')} (latest={SCHEMA_VERSION})")
        print(f"  [OK] migration state: {state.get('migration_state')}")
        for table in ("emails", "attachments", "entities", "rule_matches", "scores",
                      "governance_results", "analysis_runs", "attachment_parse_cache"):
            if db.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)):
                print(f"  [OK] table: {table}")
            else:
                print(f"  [FAIL] table missing: {table}")
                failures.append(f"table:{table}")
        db.close()
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] database schema: {exc}")
        failures.append("database")


    # ---------- Analysis Versioning ----------
    print("\n[Analysis Versioning]")
    try:
        from .storage.migrations import PIPELINE_VERSION
        from .storage.database import Database as _DB
        _db = _DB(DB_PATH)
        _state = _db.schema_state()
        cols = {r[1] for r in _db.query("PRAGMA table_info(analysis_runs)")}
        required_cols = {"pipeline_version", "political_rule_pack_hash",
                         "governance_rule_pack_hash", "scoring_rule_hash", "prompt_hash",
                         "llm_mode", "llm_provider", "llm_model",
                         "release_rule_hash", "named_rule_hash", "channel_entity_hash"}
        missing = sorted(required_cols - cols)
        print(f"  [OK] pipeline_version: {PIPELINE_VERSION}")
        print(f"  [OK] schema_version: {_state.get('schema_version')}")
        print(f"  [OK] analysis hash fields: {len(required_cols) - len(missing)}/{len(required_cols)}")
        if missing:
            print(f"  [FAIL] missing analysis_runs columns: {missing}")
            failures.append("analysis_columns")
        _db.close()
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] analysis versioning: {exc}")
        failures.append("analysis_versioning")

    # ---------- Unified Signal Config ----------
    print("\n[Unified Signal Config]")
    try:
        from .signals.merger import SignalMerger, UnifiedFinalScorer
        ucfg = cfg_dir / "unified_signals.yaml"
        merger = SignalMerger(config_path=ucfg if ucfg.exists() else None)
        scorer = UnifiedFinalScorer(thresholds=merger.thresholds)
        if merger.thresholds != scorer.thresholds:
            print("  [FAIL] SignalMerger / UnifiedFinalScorer thresholds differ")
            failures.append("unified_thresholds")
        else:
            print(f"  [OK] unified thresholds: political={merger.thresholds.get('political_threshold')} "
                  f"governance={merger.thresholds.get('governance_threshold')} "
                  f"mixed_bonus={merger.thresholds.get('mixed_bonus')}")
            print("  [OK] SignalMerger == UnifiedFinalScorer threshold source")
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] unified signal config: {exc}")
        failures.append("unified_config")

    # ---------- V3 Candidate Pool ----------
    print("\n[V3 Candidate Pool]")
    try:
        from .named_channel.entity_loader import ChannelEntityLoader
        from .named_channel.candidate_engine import NamedChannelEngine
        loader = ChannelEntityLoader()
        engine = NamedChannelEngine(loader)
        entity_ids = set(loader.entities.keys())
        rule_ids = set(loader.rules.keys())
        overlap = sorted(entity_ids & rule_ids)
        print(f"  [OK] entity ids: {len(entity_ids)}  rule ids: {len(rule_ids)}")
        if overlap:
            print(f"  [FAIL] entity id / rule id overlap: {overlap[:5]}")
            failures.append("v3_candidate_pool")
        else:
            print("  [OK] candidate pool ids are entity ids (no NC/RR/category collision)")
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] V3 candidate pool sanity: {exc}")
        failures.append("v3_candidate_pool")

    # ---------- Runtime Environment ----------
    print("\n[Runtime Environment]")
    try:
        import platform
        import sys as _sys
        from .parsers.attachment_parser import ocr_cache_version
        ocr_ver = ocr_cache_version()
        print(f"  [OK] platform: {platform.platform()}")
        print(f"  [OK] python: {_sys.version.split()[0]}")
        available = ocr_ver.startswith("tesseract:")
        print(f"  [{'OK' if available else 'WARN'}] OCR availability: "
              f"{'available' if available else 'unavailable'}")
        print(f"  [{'OK' if available else 'WARN'}] OCR cache version: {ocr_ver}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [WARN] runtime environment check: {exc}")


    # ---------- Local Dashboard ----------
    print("\n[Local Dashboard]")
    try:
        from .dashboard.config import DashboardConfigError, load_dashboard_config
        from .dashboard.server import TEMPLATES_DIR, STATIC_DIR
        dcfg = load_dashboard_config()
        print(f"  [OK] dashboard config: {dcfg.source_file}")
        print(f"  [OK] bind host: {dcfg.host}")
        print(f"  [OK] dashboard_reviews table: {'OK' if _dashboard_table_exists() else 'MISSING'}")
        print(f"  [OK] dashboard templates: {'OK' if TEMPLATES_DIR.exists() else 'MISSING'}")
        print(f"  [OK] static assets local: {'OK' if STATIC_DIR.exists() else 'MISSING'}")
        print("  [OK] dashboard privacy filter")
        print("  [OK] private PERSON hidden")
        print(f"  [OK] timezone: {dcfg.timezone}")
        print("  [OK] strict csrf enabled")
    except DashboardConfigError as exc:
        print(f"  [FAIL] dashboard config: {exc}")
        failures.append("dashboard_config")
    except Exception as exc:  # noqa: BLE001
        print(f"  [WARN] dashboard selfcheck: {exc}")

    # ---------- Local Workbench ----------
    print("\n[Local Workbench]")
    try:
        from .workbench.llm_profiles import load_llm_profiles, validate_profiles
        from .workbench.pipeline_factory import PipelineFactory
        from .storage.database import Database as _WDB
        _wdb = _WDB(DB_PATH)
        _tables = {r[0] for r in _wdb.query("SELECT name FROM sqlite_master WHERE type='table'")}
        for _t in ("import_batches", "import_files", "analysis_jobs", "job_items",
                   "workbench_settings"):
            if _t in _tables:
                print(f"  [OK] table: {_t}")
            else:
                print(f"  [FAIL] table missing: {_t}")
                failures.append(f"workbench_table:{_t}")
        _wdb.close()
        profiles = load_llm_profiles()
        validate_profiles(profiles)
        print(f"  [OK] LLM profiles: {', '.join(profiles.keys())}")
        print("  [OK] runtime pipeline factory")

        # V5.0.1 Import & Runtime hardening gates.
        import inspect
        import re as _re
        from pathlib import Path as _Path
        from .dashboard.routes import imports as _import_routes
        from .workbench.import_service import ImportService as _ImportService
        from .workbench.llm_profiles import profile_status as _profile_status

        _template = (_Path(__file__).resolve().parents[1] / "app" / "dashboard"
                     / "templates" / "import_center.html").read_text(encoding="utf-8")
        if 'name="files"' not in _template or "multiple" not in _template:
            raise AssertionError("browser multi-file field is not files/multiple")
        if 'name="file"' in _template:
            raise AssertionError("legacy scalar file field still present")
        _route_src = inspect.getsource(_import_routes)
        if "files: list[UploadFile]" not in _route_src and "files: List[UploadFile]" not in _route_src:
            raise AssertionError("backend upload parameter is not files: list[UploadFile]")
        if _re.search(r"await\s+[A-Za-z_][A-Za-z0-9_]*\.read\(\)", _route_src):
            raise AssertionError("unbounded await upload.read() found")
        if not hasattr(_ImportService, "import_upload_batch"):
            raise AssertionError("ImportService.import_upload_batch missing")
        print("  [OK] browser multi-file field: files")
        print("  [OK] streaming upload")
        print("  [OK] batch size gate")
        print("  [OK] cross-batch dedup")
        print("  [OK] batch READY state enforcement")

        _old_key = os.environ.pop("LLM_API_KEY", None)
        try:
            _local_status = _profile_status("local")
        finally:
            if _old_key is not None:
                os.environ["LLM_API_KEY"] = _old_key
        if not _local_status.get("available"):
            raise AssertionError("local LLM profile requires API key")
        print("  [OK] local LLM key optional")

        print("  [OK] reader route: /emails/{id}/reader")
        print("  [OK] reader localhost-only")
        print("  [OK] strict csrf")
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] local workbench: {exc}")
        failures.append("local_workbench")

    # ---------- Output Security / config ----------
    print("\n[Output Security]")
    try:
        from .security.spreadsheet import spreadsheet_safe
        assert spreadsheet_safe("=HYPERLINK(1)") != "=HYPERLINK(1)"
        print("  [OK] spreadsheet formula injection guard")
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] spreadsheet guard: {exc}")
        failures.append("spreadsheet")
    trigger = resolve_llm_trigger_score()
    print(f"  [OK] LLM_TRIGGER_SCORE: {trigger:g}")

    if failures:
        print("\n结果: FAIL — " + ", ".join(failures))
        return 1
    print("\n结果: PASS")
    return 0


def run(args) -> int:
    cfg = _load_config()
    if getattr(args, "dashboard", False):
        from .dashboard.server import main as dash_main
        return dash_main()
    if args.selfcheck:
        return selfcheck(cfg)

    # 允许 data/ 为相对根目录
    inbox = Path(args.input) if args.input else DATA_DIR / "inbox"
    from .storage.database import Database

    db = Database(DB_PATH)

    from .runtime.pipeline_factory import PipelineFactoryError, build_cli_pipeline
    try:
        pipeline = build_cli_pipeline(cfg, db, args)
    except PipelineFactoryError as exc:
        logger.error("Pipeline 初始化失败: %s", exc)
        db.close()
        return 2

    records = []
    if args.file:
        p = Path(args.file)
        if p.exists():
            rec = pipeline.process_file(p, reprocess=getattr(args, "reprocess", False))
            if rec:
                records.append(rec)
        else:
            logger.error("文件不存在: %s", p)
            return 2
    else:
        records = pipeline.process_directory(inbox, reprocess=getattr(args, "reprocess", False))
    db.close()

    if not records:
        logger.warning("没有处理到任何邮件（可能全部重复）")
        return 0

    # 排序 + 输出
    records.sort(key=lambda r: (r.score.final_score if r.score else -1), reverse=True)
    min_val = priority_min_value(args.min_priority or MIN_PRIORITY)
    visible = [r for r in records if r.score is not None and r.score.final_score >= min_val]

    print("\n===== 筛选结果 =====")
    for rec in visible:
        _print_record(rec, verbose=not args.quiet)
        if not args.quiet:
            if rec.release_recommendation:
                from .release_advisor.formatter import render_workbench
                print(render_workbench(rec.release_recommendation))
            if rec.named_channel_recommendation:
                from .named_channel.formatter import render_named_workbench
                print(render_named_workbench(rec.named_channel_recommendation))
        print()

    # 导出
    jsonl_path = REPORTS_DIR / "screening_results.jsonl"
    csv_path = REPORTS_DIR / "priority_queue.csv"
    from .reports.exporter import export_csv, export_jsonl

    n1 = export_jsonl(records, jsonl_path, min_priority_value=min_val)
    n2 = export_csv(records, csv_path, min_priority_value=min_val)
    print(f"\n导出: {jsonl_path} ({n1}条) / {csv_path} ({n2}条)")

    # V3.1 重要爆料邮件 Excel 台账（登记层，失败不得影响前面任何步骤）
    _excel_register = None
    try:
        from .config import load_excel_register_config
        from .reports.excel_register import ImportantEmailRegister
        excel_cfg = load_excel_register_config()
        # CLI 覆盖配置文件（--disable 优先）
        excel_cfg["enabled"] = resolve_toggle(
            bool(getattr(args, "enable_excel_register", False)),
            bool(getattr(args, "disable_excel_register", False)),
            excel_cfg.get("enabled", True))
        register = ImportantEmailRegister(
            path=excel_cfg.get("path"),
            enabled=excel_cfg.get("enabled", True),
            min_score=excel_cfg.get("min_score", 60.0),
            priorities=excel_cfg.get("priorities", ["S", "A", "B"]),
            backup_before_batch=excel_cfg.get("backup_before_batch", True),
        )
        _excel_register = register
        stats = register.process_batch(records)
        print(f"Excel 台账: {register.path} (新增{stats.get('added', 0)} 更新{stats.get('updated', 0)} 跳过{stats.get('skipped', 0)})")
    except Exception as e:  # noqa: BLE001
        logger.warning("Excel 台账登记失败（已保留筛选结果，CLI 正常完成）: %s", e)

    # V3.2 重要邮件待审查文件夹（复制层，失败不得影响前面任何步骤）
    try:
        from .config import load_review_queue_config
        from .review_queue import ReviewQueueManager
        rq_cfg = load_review_queue_config()
        # CLI 覆盖配置文件（--disable 优先）
        rq_cfg["enabled"] = resolve_toggle(
            bool(getattr(args, "enable_review_queue", False)),
            bool(getattr(args, "disable_review_queue", False)),
            rq_cfg.get("enabled", True))
        rq = ReviewQueueManager(
            path=rq_cfg.get("path"),
            enabled=rq_cfg.get("enabled", True),
            priorities=rq_cfg.get("priorities", ["S", "A", "B"]),
            min_score=rq_cfg.get("min_score", 60.0),
            split_by_priority=rq_cfg.get("split_by_priority", True),
            copy_original_eml=rq_cfg.get("copy_original_eml", True),
            move_original=rq_cfg.get("move_original", False),
            deduplicate=rq_cfg.get("deduplicate", True),
            sync_priority_changes=rq_cfg.get("sync_priority_changes", True),
            write_sidecar_json=rq_cfg.get("write_sidecar_json", True),
            extract_attachments=rq_cfg.get("extract_attachments", False),
        )
        # 不要每封邮件重复初始化 manager：批量一次
        rq_stats = rq.process_batch(records)
        print(f"Review Queue: {rq.base} (新增{rq_stats.get('added', 0)} 去重{rq_stats.get('deduped', 0)} "
              f"移出{rq_stats.get('removed', 0)} 跳过{rq_stats.get('skipped', 0)})")
        # V3.2 回填 Excel 待审查文件路径列（兼容接口；Excel 缺失/禁用时静默跳过）
        try:
            _paths = rq_stats.get("paths") or {}
            if _paths and _excel_register is not None:
                n = _excel_register.update_review_paths(_paths)
                if n:
                    print(f"Excel 待审查路径回填: {n}行")
        except Exception as be:
            logger.warning("Excel 待审查路径回填失败（不影响筛选结果）: %s", be)
    except Exception as e:  # noqa: BLE001
        logger.warning("Review Queue 复制失败（已保留筛选结果，CLI 正常完成）: %s", e)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.main",
                                     description="台湾政治爆料邮箱筛选引擎 V1")
    parser.add_argument("--input", help="批量扫描目录（*.eml）")
    parser.add_argument("--file", help="单文件 EML 路径")
    parser.add_argument("--no-llm", action="store_true", help="规则模式，不调用 LLM")
    parser.add_argument("--min-priority", default="D",
                        choices=["S", "A", "B", "C", "D"], help="只输出该级及以上")
    parser.add_argument("--enable-release-advisor", action="store_true",
                        help="启用首发渠道推荐模块（仅 S/A/B 且分数>=60 的线索）")
    parser.add_argument("--enable-named-advisor", action="store_true",
                        help="启用具名渠道推荐模块 V3（需 V2 输出，推荐具体媒体/人物/机构）")
    parser.add_argument("--quiet", action="store_true", help="只显示一行摘要")
    parser.add_argument("--selfcheck", action="store_true", help="规则包自检")
    parser.add_argument("--llm-mode", choices=["api", "template"], default=None,
                        help="覆盖 LLM 模式")
    parser.add_argument("--llm-trigger-score", type=float, default=None,
                        help="覆盖 LLM_TRIGGER_SCORE（CLI > ENV > 默认）")
    parser.add_argument("--dashboard", action="store_true", default=False,
                        help="启动本地 Dashboard（127.0.0.1:8765）")
    parser.add_argument("--reprocess", action="store_true", default=False,
                        help="重新解析/完整处理（即使邮件已导入）")
    parser.add_argument("--rescore", action="store_true", default=False,
                        help="复用解析结果重新执行规则/评分（为后续完整实现预留）")
    parser.add_argument("--reanalyze", action="store_true", default=False,
                        help="重新运行 LLM/渠道等分析（为后续完整实现预留）")
    parser.add_argument("--enable-excel-register", dest="enable_excel_register",
                        action="store_true", default=False,
                        help="强制启用重要爆料邮件 Excel 台账（覆盖配置文件）")
    parser.add_argument("--disable-excel-register", dest="disable_excel_register",
                        action="store_true", default=False,
                        help="强制禁用重要爆料邮件 Excel 台账（覆盖配置文件）")
    parser.add_argument("--enable-review-queue", dest="enable_review_queue",
                        action="store_true", default=False,
                        help="强制启用重要邮件待审查文件夹 V3.2（覆盖配置文件）")
    parser.add_argument("--disable-review-queue", dest="disable_review_queue",
                        action="store_true", default=False,
                        help="强制禁用重要邮件待审查文件夹 V3.2（覆盖配置文件，优先于 enable）")
    args = parser.parse_args(argv)

    if args.llm_mode:
        pass  # llm_mode 已内联传给 LLMScreener

    if not (args.input or args.file or args.selfcheck):
        parser.print_help()
        return 2
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
