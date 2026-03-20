#!/usr/bin/env python3
"""
事件向量检索 CLI 脚本。

供聚类 Agent 和事件关联 Agent 通过命令行调用，支持向量检索、
结构化检索和两阶段组合检索。结果以 JSON 输出到 stdout。

用法:
    # 向量检索：找语义相似的历史事件
    python search_events.py --query "卡塔尔遭导弹袭击" --limit 10

    # 向量检索 + category 过滤
    python search_events.py --query "卡塔尔遭导弹袭击" --category headline

    # 结构化检索：同类别近 N 天事件
    python search_events.py --category headline --days-back 7 --limit 20

    # 组合检索（两阶段）：向量 top-K + 同类别近 N 天，合并去重
    python search_events.py --query "卡塔尔遭导弹袭击" --category headline \\
        --days-back 7 --combined --limit 15
"""

import argparse
import json
import sys

from events_db import EventsDB


def search_vector(db: EventsDB, query: str, category: str = None,
                  days_back: int = None, limit: int = 10) -> list[dict]:
    """向量检索，返回结果标记 _source='vector'。"""
    results = db.search_similar(
        text=query, category=category, days_back=days_back, limit=limit
    )
    for r in results:
        r["_source"] = "vector"
    return results


def search_structural(db: EventsDB, category: str,
                      days_back: int = 7, limit: int = 20) -> list[dict]:
    """结构化检索，返回结果标记 _source='structural'。"""
    results = db.search_by_category(
        category=category, days_back=days_back, limit=limit
    )
    for r in results:
        r["_source"] = "structural"
    return results


def search_combined(db: EventsDB, query: str, category: str,
                    days_back: int = 7, limit: int = 15) -> list[dict]:
    """两阶段组合检索：向量 top-K + 结构化，合并去重。

    向量检索和结构化检索各取 limit 条，然后按 event_id 去重，
    优先保留向量检索的结果（有 _distance），最终裁剪到 limit。
    """
    vector_results = search_vector(
        db, query, category=category, days_back=days_back, limit=limit
    )
    structural_results = search_structural(
        db, category=category, days_back=days_back, limit=limit
    )

    # 合并去重：向量结果优先
    seen = set()
    merged = []
    for r in vector_results:
        eid = r["event_id"]
        if eid not in seen:
            seen.add(eid)
            merged.append(r)
    for r in structural_results:
        eid = r["event_id"]
        if eid not in seen:
            seen.add(eid)
            merged.append(r)

    return merged[:limit]


def search_batch(db: EventsDB, queries_file: str, category: str = None,
                 days_back: int = None, limit: int = 10) -> dict:
    """批量向量检索：一次性计算所有 embedding，逐条搜索。

    Args:
        db: EventsDB 实例。
        queries_file: JSON 文件路径，包含 [{"id": ..., "query": ...}, ...] 数组。
        category: 分类过滤（可选）。
        days_back: 时间窗口天数（可选）。
        limit: 每条查询的最大返回数。

    Returns:
        {id: {candidates: [候选事件列表], _has_history: bool}} 字典。
    """
    from datetime import date
    today = date.today().isoformat()

    with open(queries_file, "r", encoding="utf-8") as f:
        queries = json.load(f)

    # 批量计算 embedding
    texts = [q["query"] for q in queries]
    vectors = db.embed_batch(texts)

    # 逐条搜索
    results = {}
    for q, vec in zip(queries, vectors):
        hits = db.search_similar_by_vector(
            vector=vec, category=category, days_back=days_back, limit=limit
        )
        # 判断是否有历史事件（first_seen 早于今天的候选）
        has_history = any(
            h.get("first_seen", "") < today
            for h in hits
        )
        for h in hits:
            h["_source"] = "vector"
        results[q["id"]] = {
            "candidates": hits,
            "_has_history": has_history,
        }

    return results


def main():
    parser = argparse.ArgumentParser(
        description="事件向量检索 CLI，支持向量/结构化/组合/批量检索"
    )
    parser.add_argument(
        "--query", type=str, default=None,
        help="检索文本（向量检索必需）"
    )
    parser.add_argument(
        "--batch", type=str, default=None,
        help="批量查询文件路径（JSON 数组）"
    )
    parser.add_argument(
        "--category", type=str, default=None,
        help="分类过滤（headline/tech/finance/life/other）"
    )
    parser.add_argument(
        "--days-back", type=int, default=None,
        help="时间窗口天数"
    )
    parser.add_argument(
        "--limit", type=int, default=10,
        help="最大返回数（默认 10）"
    )
    parser.add_argument(
        "--combined", action="store_true",
        help="启用两阶段检索（向量 + 结构化合并去重）"
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="将 JSON 结果写入指定文件（不提供则输出到 stdout）"
    )
    parser.add_argument(
        "--db-path", type=str, default="data/events_vector_db",
        help="数据库路径（默认 data/events_vector_db）"
    )
    args = parser.parse_args()

    # 参数校验
    if args.batch:
        if args.query or args.combined:
            print("错误: --batch 不能与 --query 或 --combined 同时使用",
                  file=sys.stderr)
            sys.exit(1)
    else:
        if args.combined and not args.query:
            print("错误: --combined 模式需要 --query 参数", file=sys.stderr)
            sys.exit(1)
        if args.combined and not args.category:
            print("错误: --combined 模式需要 --category 参数", file=sys.stderr)
            sys.exit(1)
        if not args.query and not args.category:
            print("错误: 至少需要 --query 或 --category 参数", file=sys.stderr)
            sys.exit(1)

    db = EventsDB(db_path=args.db_path)

    if args.batch:
        results = search_batch(
            db, args.batch, category=args.category,
            days_back=args.days_back, limit=args.limit
        )
    elif args.combined:
        results = search_combined(
            db, query=args.query, category=args.category,
            days_back=args.days_back or 7, limit=args.limit
        )
    elif args.query:
        results = search_vector(
            db, query=args.query, category=args.category,
            days_back=args.days_back, limit=args.limit
        )
    else:
        results = search_structural(
            db, category=args.category,
            days_back=args.days_back or 7, limit=args.limit
        )

    json_output = json.dumps(results, ensure_ascii=False, indent=2)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(json_output)
        # 统计信息输出到 stdout
        if isinstance(results, dict):
            total = sum(len(v) for v in results.values())
            print(f"已写入 {len(results)} 组共 {total} 条结果到 {args.output}")
        else:
            print(f"已写入 {len(results)} 条结果到 {args.output}")
    else:
        print(json_output)


if __name__ == "__main__":
    main()
