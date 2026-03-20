#!/usr/bin/env python3
"""
将 events_history.json 一次性迁移到 LanceDB。

读取旧的 JSON 格式事件历史，补充缺失字段，调用 embedding API
生成向量，通过 EventsDB 的 batch_upsert 写入 LanceDB。

用法:
    python3 scripts/migrate_json_to_lancedb.py \
        --input data/events_history.json \
        --db-path data/events_vector_db
"""

import argparse
import json
import os
import sys
import time


def load_json(path: str) -> list:
    """加载 JSON 文件，返回事件列表。"""
    if not path or not os.path.exists(path):
        print(f"错误: 文件不存在: {path}", file=sys.stderr)
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            print(f"错误: {path} 不是数组", file=sys.stderr)
            return []
        return data
    except (json.JSONDecodeError, ValueError) as e:
        print(f"错误: 解析 {path} 失败: {e}", file=sys.stderr)
        return []


def fill_missing_fields(event: dict) -> dict:
    """补充旧格式事件中缺失的字段。

    Args:
        event: 旧格式事件字典。

    Returns:
        补充完整的事件字典（新对象，不修改原始数据）。
    """
    evt = dict(event)

    # keywords 默认空数组
    if "keywords" not in evt:
        evt["keywords"] = []

    # summary 默认空字符串
    if "summary" not in evt:
        evt["summary"] = ""

    # related_events 默认空数组
    if "related_events" not in evt:
        evt["related_events"] = []

    # latest_importance 从 importance_trend 最后一个值取
    if "latest_importance" not in evt:
        trend = evt.get("importance_trend", [])
        if isinstance(trend, list) and trend:
            evt["latest_importance"] = trend[-1]
        else:
            evt["latest_importance"] = 5

    # 兼容旧的 daily_titles 格式
    if "daily_titles" in evt and "daily_entries" not in evt:
        old_titles = evt.pop("daily_titles")
        evt["daily_entries"] = {
            d: {"title": t, "url": ""} for d, t in old_titles.items()
        }

    return evt


def migrate(input_path: str, db_path: str) -> dict:
    """执行迁移，返回统计信息。

    Args:
        input_path: 旧 JSON 文件路径。
        db_path: LanceDB 数据库路径。

    Returns:
        {'total': N, 'new': N, 'updated': N, 'elapsed': float}
    """
    events = load_json(input_path)
    if not events:
        return {"total": 0, "new": 0, "updated": 0, "elapsed": 0.0}

    # 补充缺失字段
    filled_events = [fill_missing_fields(evt) for evt in events]

    # 延迟导入，方便测试 mock
    from events_db import EventsDB

    start = time.time()
    db = EventsDB(db_path=db_path)
    result = db.batch_upsert(filled_events)
    elapsed = time.time() - start

    return {
        "total": len(filled_events),
        "new": result["new"],
        "updated": result["updated"],
        "elapsed": elapsed,
    }


def print_stats(stats: dict) -> None:
    """打印迁移统计信息。"""
    print("迁移完成:")
    print(f"  总事件数:   {stats['total']}")
    print(f"  新增:       {stats['new']}")
    print(f"  更新:       {stats['updated']}")
    print(f"  耗时:       {stats['elapsed']:.2f} 秒")


def main(args: list = None) -> None:
    parser = argparse.ArgumentParser(
        description="将 events_history.json 迁移到 LanceDB"
    )
    parser.add_argument(
        "--input", required=True,
        help="旧的 events_history.json 文件路径"
    )
    parser.add_argument(
        "--db-path", type=str, default="data/events_vector_db",
        help="LanceDB 数据库路径（默认 data/events_vector_db）"
    )
    parsed = parser.parse_args(args)

    stats = migrate(parsed.input, parsed.db_path)

    if stats["total"] == 0:
        print("无事件可迁移")
        return

    print_stats(stats)


if __name__ == "__main__":
    main()
