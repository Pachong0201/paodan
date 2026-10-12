"""Markdown 内部稿筛选 CLI（独立入口，不影响 app.main 的 EML 流程）：

python -m app.md_main --input data/markdown_inbox/
python -m app.md_main --input 稿件目录 --recursive --split-sections --no-llm
python -m app.md_main --file 稿件.md --min-priority B

结果库默认 data/markdown/md_screening.db（MD_SCREENING_DB 可覆盖），
导出到 data/reports/markdown/，与邮件结果分开。
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import MD_DB_PATH, MD_INBOX_DIR, MD_REPORTS_DIR, priority_min_value
from .main import _load_config, _print_record
from .parsers.markdown_parser import MARKDOWN_SUFFIXES

logger = logging.getLogger("app.md_main")


def run(args) -> int:
    from .runtime.pipeline_factory import PipelineFactoryError, build_cli_pipeline
    from .storage.database import Database

    cfg = _load_config()
    db_path = Path(args.db) if args.db else MD_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(db_path)
    try:
        try:
            pipeline = build_cli_pipeline(cfg, db, args)
        except PipelineFactoryError as exc:
            logger.error("Pipeline 初始化失败: %s", exc)
            return 2
        reprocess = bool(args.reprocess)
        if args.file:
            p = Path(args.file)
            if not p.is_file():
                logger.error("文件不存在: %s", p)
                return 2
            if p.suffix.lower() not in MARKDOWN_SUFFIXES:
                logger.error("仅支持 .md / .markdown: %s", p)
                return 2
            records = pipeline.process_markdown_file(p, reprocess=reprocess,
                                                     split_sections=args.split_sections)
        else:
            inbox = Path(args.input) if args.input else MD_INBOX_DIR
            if not inbox.is_dir():
                logger.error("目录不存在: %s", inbox)
                return 2
            records = pipeline.process_markdown_directory(
                inbox, reprocess=reprocess, recursive=args.recursive,
                split_sections=args.split_sections)
    finally:
        db.close()

    if not records:
        logger.warning("没有处理到任何 Markdown 稿件（可能全部已处理过，可加 --reprocess）")
        return 0

    records.sort(key=lambda r: (r.score.final_score if r.score else -1), reverse=True)
    min_val = priority_min_value(args.min_priority)
    visible = [r for r in records if r.score is not None and r.score.final_score >= min_val]
    errors = [r for r in records if r.score is None]

    print("\n===== Markdown 稿件筛选结果 =====")
    for rec in visible + errors:
        _print_record(rec, verbose=not args.quiet)
        if rec.email is not None and not args.quiet:
            print(f"     文件: {rec.email.source_path}")
        print()

    from .reports.exporter import export_csv, export_jsonl
    out_dir = Path(args.output) if args.output else MD_REPORTS_DIR
    jsonl_path = out_dir / "md_screening_results.jsonl"
    csv_path = out_dir / "md_priority_queue.csv"
    n1 = export_jsonl(records, jsonl_path, min_priority_value=min_val)
    n2 = export_csv(records, csv_path, min_priority_value=min_val)
    print(f"\n导出: {jsonl_path} ({n1}条) / {csv_path} ({n2}条)")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.md_main",
                                     description="Markdown 内部稿高价值线索筛选（独立入口）")
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--input", help=f"批量扫描目录（*.md / *.markdown），默认 {MD_INBOX_DIR}")
    src.add_argument("--file", help="单个 Markdown 文件")
    parser.add_argument("--recursive", action="store_true", help="递归扫描子目录")
    parser.add_argument("--split-sections", action="store_true",
                        help="按二级标题(##)切段分别打分（适合多条线索的汇编稿）")
    parser.add_argument("--no-llm", action="store_true", help="规则模式，不调用 LLM")
    parser.add_argument("--llm-mode", choices=["api", "template"], default=None, help="覆盖 LLM 模式")
    parser.add_argument("--llm-trigger-score", type=float, default=None, help="覆盖 LLM_TRIGGER_SCORE")
    parser.add_argument("--min-priority", default="D", choices=["S", "A", "B", "C", "D"],
                        help="只输出该级及以上")
    parser.add_argument("--reprocess", action="store_true", help="已处理过的稿件也重新分析")
    parser.add_argument("--db", help=f"结果库路径，默认 {MD_DB_PATH}")
    parser.add_argument("--output", help=f"导出目录，默认 {MD_REPORTS_DIR}")
    parser.add_argument("--quiet", action="store_true", help="只显示一行摘要")
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
