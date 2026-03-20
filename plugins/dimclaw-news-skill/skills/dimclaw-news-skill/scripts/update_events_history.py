#!/usr/bin/env python3
"""
更新事件历史记录到 LanceDB。

从 JSON 数组文件读取更新数据，合并到 LanceDB 事件库中。
保留连续性判断、daily_entries 追加、importance_trend 追加、
event_name 更新等核心逻辑。

用法:
    # 从聚类 Agent 输出更新到 LanceDB
    python3 scripts/update_events_history.py \
        --update /tmp/events_history_update.json

    # 可选指定数据库路径
    python3 scripts/update_events_history.py \
        --update /tmp/events_history_update.json \
        --db-path data/events_vector_db
"""

import argparse
import json
import os
import sys
from datetime import datetime

MAX_GAP_DAYS = 2  # last_seen 与新 date 之差 <= 2 视为连续


def parse_date(date_str: str) -> datetime:
    """将 ISO 日期字符串解析为 datetime 对象。"""
    return datetime.strptime(date_str, "%Y-%m-%d")


def load_update_file(path: str) -> list:
    """加载更新 JSON 文件，不存在或解析失败时返回空列表。"""
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            print(f"警告: {path} 不是数组，视为空列表", file=sys.stderr)
            return []
        return data
    except (json.JSONDecodeError, ValueError) as e:
        print(f"警告: 解析 {path} 失败 ({e})，视为空数组", file=sys.stderr)
        return []


def merge_single_event(existing: dict, update: dict) -> dict:
    """将单条更新合并到已有事件记录中，返回合并后的新事件。

    Args:
        existing: 从 LanceDB 读取的已有事件记录（已反序列化）。
        update: 新的更新数据，包含 date, title, url, importance 等。

    Returns:
        合并后的事件字典。
    """
    evt = dict(existing)
    upd_date = update["date"]
    upd_title = update.get("title", "")
    upd_url = update.get("url", "")
    upd_importance = update.get("importance", 5)
    upd_name = update.get("event_name", "")

    last_seen_dt = parse_date(evt["last_seen"])
    upd_dt = parse_date(upd_date)
    gap = (upd_dt - last_seen_dt).days

    # 更新 last_seen
    if upd_dt > last_seen_dt:
        evt["last_seen"] = upd_date

    # 添加 daily_entries（同一天则覆盖，创建新 dict 避免修改原始数据）
    daily_entries = evt.get("daily_entries", {})
    if isinstance(daily_entries, str):
        try:
            daily_entries = json.loads(daily_entries)
        except (json.JSONDecodeError, TypeError):
            daily_entries = {}
    daily_entries = dict(daily_entries)
    daily_entries[upd_date] = {
        "title": upd_title,
        "url": upd_url,
        "summary": update.get("summary", ""),
        "insights": update.get("insights", []),
        "keywords": update.get("keywords", []),
    }
    evt["daily_entries"] = daily_entries

    # 追加 importance_trend（创建新列表，避免修改原始数据）
    importance_trend = evt.get("importance_trend", [])
    if isinstance(importance_trend, str):
        try:
            importance_trend = json.loads(importance_trend)
        except (json.JSONDecodeError, TypeError):
            importance_trend = []
    evt["importance_trend"] = importance_trend + [upd_importance]

    # 更新 latest_importance
    evt["latest_importance"] = upd_importance

    # 连续性判断
    if gap == 0:
        # 同一天，consecutive_days 不增加
        pass
    elif 1 <= gap <= MAX_GAP_DAYS:
        evt["consecutive_days"] = evt.get("consecutive_days", 1) + 1
    elif gap > MAX_GAP_DAYS:
        evt["consecutive_days"] = 1
    # gap < 0 意味着更新日期早于 last_seen，不改变连续性

    # 更新 event_name（取最新的名称）
    if upd_name:
        evt["event_name"] = upd_name

    # 更新 keywords（如果更新数据提供了）
    if "keywords" in update:
        evt["keywords"] = update["keywords"]

    # 更新 summary（如果更新数据提供了）
    if "summary" in update:
        evt["summary"] = update["summary"]

    # 更新 related_events（合并去重，不是覆盖）
    if "related_events" in update:
        existing_related = evt.get("related_events", [])
        if isinstance(existing_related, str):
            try:
                existing_related = json.loads(existing_related)
            except (json.JSONDecodeError, TypeError):
                existing_related = []
        new_related = update["related_events"]
        merged = list(dict.fromkeys(existing_related + new_related))
        evt["related_events"] = merged

    return evt


def build_new_event(update: dict) -> dict:
    """从更新数据构建全新的事件记录。

    Args:
        update: 更新数据。

    Returns:
        新事件字典。
    """
    upd_date = update["date"]
    upd_title = update.get("title", "")
    upd_url = update.get("url", "")
    upd_importance = update.get("importance", 5)

    return {
        "event_id": update["event_id"],
        "event_name": update.get("event_name", ""),
        "first_seen": upd_date,
        "last_seen": upd_date,
        "consecutive_days": 1,
        "daily_entries": {upd_date: {
            "title": upd_title,
            "url": upd_url,
            "summary": update.get("summary", ""),
            "insights": update.get("insights", []),
            "keywords": update.get("keywords", []),
        }},
        "category": update.get("category", "other"),
        "importance_trend": [upd_importance],
        "latest_importance": upd_importance,
        "keywords": update.get("keywords", []),
        "summary": update.get("summary", ""),
        "related_events": update.get("related_events", []),
    }


def process_updates(db, updates: list) -> dict:
    """处理所有更新，将合并后的事件写入 LanceDB。

    Args:
        db: EventsDB 实例。
        updates: 更新数据列表。

    Returns:
        统计信息 {'total': N, 'new': N, 'updated': N}
    """
    merged_events = []
    new_count = 0
    updated_count = 0

    for upd in updates:
        eid = upd["event_id"]
        existing = db.get_event(eid)

        if existing is not None:
            merged = merge_single_event(existing, upd)
            merged_events.append(merged)
            updated_count += 1
        else:
            new_evt = build_new_event(upd)
            merged_events.append(new_evt)
            new_count += 1

    if merged_events:
        db.batch_upsert(merged_events)

    return {"total": len(updates), "new": new_count, "updated": updated_count}


def print_stats(history_count: int, stats: dict) -> None:
    """打印统计信息到 stdout。"""
    print("统计:")
    print(f"  历史事件数: {history_count}")
    print(f"  更新事件数: {stats['total']}")
    print(f"  新增事件:   {stats['new']}")
    print(f"  更新事件:   {stats['updated']}")


def main(args: list = None) -> None:
    parser = argparse.ArgumentParser(
        description="更新事件历史记录到 LanceDB"
    )
    parser.add_argument(
        "--update", required=True,
        help="更新文件路径（JSON 数组）"
    )
    parser.add_argument(
        "--db-path", type=str, default="data/events_vector_db",
        help="数据库路径（默认 data/events_vector_db）"
    )
    parsed = parser.parse_args(args)

    updates = load_update_file(parsed.update)

    if not updates:
        print("无更新事件，直接退出")
        return

    # 延迟导入 EventsDB，方便测试 mock
    from events_db import EventsDB

    db = EventsDB(db_path=parsed.db_path)
    history_count = db.count()

    stats = process_updates(db, updates)
    print_stats(history_count, stats)


if __name__ == "__main__":
    main()
