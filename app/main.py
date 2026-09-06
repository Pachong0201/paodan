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
import logging
import sys
from pathlib import Path

from .config import (DATA_DIR, DB_PATH, LLM_MODE, LOG_DIR, LOG_FILE, MIN_PRIORITY,
                     NEWS_SIGNAL_DIR, REPORTS_DIR, priority_min_value)
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


def selfcheck(cfg: RuleConfig) -> int:
    """规则包验收自检."""
    print("=" * 60)
    print("规则包自检")
    print("=" * 60)
    s = cfg.summary()
    ok = all(v for v in cfg.load_status.values())
    for name, status in cfg.load_status.items():
        print(f"  [{'OK' if status else 'FAIL'}] {name}")
    print(f"\n分类数量: {s['taxonomy_categories']} (A01-A18)")
    print(f"Pattern 数量: {s['patterns']} (P01-P20)")
    print(f"Negative Rules: {s['negative_rules']} (N01-N08)")
    print(f"反向词 X: {s['x_terms']}")
    counts = s["keyword_counts"]
    print("词典统计(实际从YAML读取):")
    print(f"  H(高强度行为词): {counts.get('H', 0)}")
    print(f"  M(隐性/中强度): {counts.get('M', 0)}")
    print(f"  C(场景/关系): {counts.get('C', 0)}")
    print(f"  E(原始证据): {counts.get('E', 0)}")
    print(f"  S(程序词): {counts.get('S', 0)}")
    print(f"  X(反向): {counts.get('X', 0)}")
    if not ok:
        print("\n结果: FAIL — 存在规则文件加载失败")
        return 1
    print("\n结果: PASS")
    return 0


def run(args) -> int:
    cfg = _load_config()
    if args.selfcheck:
        return selfcheck(cfg)

    # 允许 data/ 为相对根目录
    inbox = Path(args.input) if args.input else DATA_DIR / "inbox"
    from .storage.database import Database

    db = Database(DB_PATH)

    llm_mode = args.llm_mode or LLM_MODE
    from .llm.screener import LLMScreener

    screener = LLMScreener(cfg, mode=llm_mode)
    # 首发渠道推荐模块（V2：仅 S/A/B 且 final_score >= 配置阈值）
    advisor = None
    if args.enable_release_advisor:
        from .release_advisor.llm_advisor import ReleaseAdvisor
        advisor = ReleaseAdvisor(cfg, mode=llm_mode, allow_llm=not args.no_llm)
    # 具名渠道推荐模块（V3：依赖 V2 输出）
    named_advisor = None
    if args.enable_named_advisor:
        from .named_channel.advisor import NamedChannelAdvisor
        named_advisor = NamedChannelAdvisor(mode=llm_mode, allow_llm=not args.no_llm)
    pipeline = ScreeningPipeline(cfg, db=db, llm_screener=screener,
                                 llm_trigger_score=35,
                                 allow_llm=not args.no_llm,
                                 release_advisor=advisor,
                                 named_advisor=named_advisor)

    records = []
    if args.file:
        p = Path(args.file)
        if p.exists():
            rec = pipeline.process_file(p)
            if rec:
                records.append(rec)
        else:
            logger.error("文件不存在: %s", p)
            return 2
    else:
        records = pipeline.process_directory(inbox)
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
