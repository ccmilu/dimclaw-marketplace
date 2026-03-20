#!/usr/bin/env python3
"""
机械预去重脚本：合并多个子 Agent 新闻 JSON 文件，做 URL 去重和标题去重。

语义级聚类由聚类去重 Agent 完成，本脚本只做确定性的机械去重。

用法:
    python3 scripts/cluster_preprocess.py \
        /tmp/news_hn_ph.json /tmp/news_github_v2ex.json \
        --history data/events_history.json \
        -o /tmp/news_pre_clustered.json
"""

import argparse
import difflib
import json
import os
import sys
from urllib.parse import urlparse, urlunparse

TITLE_SIMILARITY_THRESHOLD = 0.85


def load_files(paths):
    """加载所有输入 JSON 文件。文件不存在或 JSON 无效则警告跳过。

    Returns:
        tuple: (all_items, loaded_count, skipped_paths)
    """
    all_items = []
    loaded_count = 0
    skipped_paths = []
    for path in paths:
        if not os.path.exists(path):
            print(f"警告: 文件不存在，跳过: {path}", file=sys.stderr)
            skipped_paths.append(path)
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"警告: JSON 无效，跳过: {path} ({e})", file=sys.stderr)
            skipped_paths.append(path)
            continue
        if not isinstance(data, list):
            print(f"警告: 文件内容不是数组，跳过: {path}", file=sys.stderr)
            skipped_paths.append(path)
            continue
        all_items.extend(data)
        loaded_count += 1
    return all_items, loaded_count, skipped_paths


def normalize_url(url):
    """标准化 URL：统一 http/https、去掉尾部斜杠、小写化域名。

    比 merge_news.py 更严格的标准化。
    """
    if not url:
        return url
    url = url.strip()
    parsed = urlparse(url)
    # 统一为 https
    scheme = "https" if parsed.scheme in ("http", "https") else parsed.scheme
    netloc = parsed.netloc.lower()
    # 去掉尾部斜杠
    path = parsed.path.rstrip("/")
    normalized = urlunparse((scheme, netloc, path, parsed.params, parsed.query, ""))
    return normalized


def dedup_by_url(items):
    """URL 去重：标准化后相同 URL 保留 importance 更高的条目。

    Returns:
        tuple: (deduped_items, removed_count)
    """
    seen = {}
    removed = 0
    for item in items:
        raw_url = item.get("url", "")
        key = normalize_url(raw_url)
        if not key:
            # 无 URL 的条目保留（用 id 作 key 避免互相覆盖）
            seen[id(item)] = item
            continue
        if key in seen:
            existing = seen[key]
            if item.get("importance", 0) > existing.get("importance", 0):
                seen[key] = item
            removed += 1
        else:
            seen[key] = item
    return list(seen.values()), removed


def dedup_by_title(items, threshold=TITLE_SIMILARITY_THRESHOLD):
    """标题去重：使用 difflib.SequenceMatcher，阈值 0.85。

    按 importance 降序处理，保留更高 importance 的条目。

    Returns:
        tuple: (deduped_items, removed_count)
    """
    sorted_items = sorted(items, key=lambda x: x.get("importance", 0), reverse=True)
    accepted = []
    removed = 0
    for item in sorted_items:
        title = item.get("title", "")
        is_dup = False
        for existing in accepted:
            ratio = difflib.SequenceMatcher(
                None, title, existing.get("title", "")
            ).ratio()
            if ratio >= threshold:
                is_dup = True
                break
        if is_dup:
            removed += 1
        else:
            accepted.append(item)
    return accepted, removed


def preprocess(input_paths, history_path=None):
    """主预处理管线。

    Returns:
        tuple: (result_items, stats_dict)
    """
    all_items, loaded_count, skipped = load_files(input_paths)
    total_raw = len(all_items)

    items_after_url, url_removed = dedup_by_url(all_items)
    items_after_title, title_removed = dedup_by_title(items_after_url)

    stats = {
        "files_loaded": loaded_count,
        "files_skipped": len(skipped),
        "total_raw": total_raw,
        "after_url_dedup": len(items_after_url),
        "url_removed": url_removed,
        "after_title_dedup": len(items_after_title),
        "title_removed": title_removed,
        "history_file": history_path or "",
    }

    return items_after_title, stats


def print_stats(stats):
    """打印统计信息到 stdout。"""
    print("预处理完成:")
    print(f"  加载文件: {stats['files_loaded']} 个 (跳过 {stats['files_skipped']} 个)")
    print(f"  原始条目: {stats['total_raw']} 条")
    print(f"  URL 去重后: {stats['after_url_dedup']} 条 (-{stats['url_removed']})")
    print(f"  标题去重后: {stats['after_title_dedup']} 条 (-{stats['title_removed']})")
    if stats["history_file"]:
        print(f"  事件历史: {stats['history_file']}")


def parse_args(argv=None):
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="机械预去重：合并多个子 Agent 新闻 JSON 文件，做 URL 和标题去重"
    )
    parser.add_argument("files", nargs="+", help="输入的 JSON 文件路径（一个或多个）")
    parser.add_argument(
        "--history",
        default=None,
        help="事件历史文件路径（可选，仅传递给输出 metadata）",
    )
    parser.add_argument("-o", "--output", required=True, help="输出文件路径")
    return parser.parse_args(argv)


def main(argv=None):
    """主入口。"""
    args = parse_args(argv)
    result_items, stats = preprocess(args.files, history_path=args.history)

    out_dir = os.path.dirname(args.output)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result_items, f, ensure_ascii=False, indent=2)

    print_stats(stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
